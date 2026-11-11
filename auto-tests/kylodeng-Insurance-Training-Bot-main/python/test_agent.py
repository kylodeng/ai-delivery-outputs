"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content, structure, citations format
- ASSESSOR_SYSTEM prompt string: presence, content, structure, format markers
- Module-level constants: existence, types
- create_agent import and invocation (mocked)
- Prompt template variable substitution ({profile}, {conversation})
- Edge cases: empty substitution, special characters in substitution
- Boundary values: very long profile/conversation strings

Mocks used:
- langchain.agents.create_agent (patched to avoid real LLM/tool calls)
- No real LangGraph, LLM, or external API calls are made

TODOs:
- TODO: test teacher_agent and assessor_agent objects once agent factory functions
        are exposed as public callables (currently only create_agent import is visible)
- TODO: test astream_events integration for teacher agent (requires async LangGraph harness)
- TODO: test ainvoke integration for assessor agent (requires async LangGraph harness)
- TODO: test RAG tool wiring once tool definitions are exported from the module
- TODO: test that all eight tools listed in prompts are actually registered on each agent
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

EIGHT_TOOLS = [
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
]

TEACHER_STRUCTURAL_KEYWORDS = [
    "insurance sales trainer",
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
    "Age Last Birthday",
    "ALB",
    "[[S",          # citation marker pattern
]

ASSESSOR_STRUCTURAL_KEYWORDS = [
    "accuracy assessment",
    "roleplay",
    "{profile}",
    "{conversation}",
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
    "Overall Score",
    "First Impression",
    "Needs Discovery",
    "Product Knowledge",
    "Objection Handling",
    "Closing Technique",
    "Age Last Birthday",
    "ALB",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def agent_module():
    """
    Import api.agent with create_agent mocked so no real LLM calls happen.
    Returns the imported module.
    """
    mock_create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
    with patch.dict("sys.modules", {"langchain.agents": MagicMock(create_agent=mock_create_agent)}):
        # Remove cached module if present
        sys.modules.pop("api.agent", None)
        sys.modules.pop("agent", None)
        import api.agent as mod
        yield mod


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Synthetic data samples (from provided data)
# ---------------------------------------------------------------------------

SAMPLE_PROFILES = [
    # minimal profile
    "Name: John Doe, Age: 35, Occupation: Engineer",
    # detailed profile with product interest
    (
        "Name: Jane Smith, Age: 50, DOB: January 1974, "
        "Interested in: Generations II whole life plan, "
        "Health concern: pre-existing hypertension, "
        "Hospital preference: designated hospitals in Mainland China"
    ),
    # profile with special characters
    "Name: Chén Wěi (陳偉), Age: 42, City: Shànghai",
    # empty profile edge case
    "",
]

SAMPLE_CONVERSATIONS = [
    # minimal conversation
    "Agent: Hi! Customer: Hello.",
    # realistic conversation referencing Generations II
    (
        "Agent: Good morning, I'd like to tell you about Generations II, "
        "a participating whole life plan by Sun Life.\n"
        "Customer: What's the annual deductible?\n"
        "Agent: There is no deductible for the base plan, the annual limit is HKD 500,000.\n"
        "Customer: Can I use hospitals in mainland China?\n"
        "Agent: Yes, all Class 3 hospitals and selected Class 2A hospitals are covered."
    ),
    # conversation with hospital network claim
    (
        "Agent: The Global Network Hospital List covers cashless arrangements worldwide.\n"
        "Customer: Is Peking Union Medical College Hospital included?\n"
        "Agent: Yes, it's on the VIP Medical Navigation network."
    ),
    # empty conversation edge case
    "",
    # very long conversation (boundary value)
    "Agent: " + ("This is a repeated sentence about insurance products. " * 500),
]


# ---------------------------------------------------------------------------
# 1. Module-level constant existence and type tests
# ---------------------------------------------------------------------------

class TestModuleConstants:

    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_string(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_assessor_system_is_string(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_teacher_system_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0

    def test_teacher_system_minimum_length(self, teacher_system):
        """Prompt should be substantial (more than 200 chars)."""
        assert len(teacher_system) > 200

    def test_assessor_system_minimum_length(self, assessor_system):
        """Prompt should be substantial (more than 200 chars)."""
        assert len(assessor_system) > 200


# ---------------------------------------------------------------------------
# 2. TEACHER_SYSTEM content tests
# ---------------------------------------------------------------------------

class TestTeacherSystemContent:

    @pytest.mark.parametrize("keyword", TEACHER_STRUCTURAL_KEYWORDS)
    def test_contains_keyword(self, teacher_system, keyword):
        assert keyword in teacher_system, (
            f"TEACHER_SYSTEM missing expected keyword: {keyword!r}"
        )

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_all_eight_tools_mentioned(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"TEACHER_SYSTEM does not mention tool: {tool_name!r}"
        )

    def test_teacher_role_description(self, teacher_system):
        assert "trainer" in teacher_system.lower() or "coach" in teacher_system.lower()

    def test_citation_format_documented(self, teacher_system):
        """Citation format [[Sn]] must be described in the teacher prompt."""
        assert "[[S" in teacher_system

    def test_citation_example_present(self, teacher_system):
        """An example citation like [[S1]] should appear."""
        assert "[[S1]]" in teacher_system

    def test_never_guess_instruction(self, teacher_system):
        assert "Never guess" in teacher_system or "never guess" in teacher_system

    def test_age_last_birthday_instruction(self, teacher_system):
        assert "Age Last Birthday" in teacher_system

    def test_alb_abbreviation(self, teacher_system):
        assert "ALB" in teacher_system

    def test_get_current_date_called_first_instruction(self, teacher_system):
        """Prompt must instruct to call get_current_date first for date-relative questions."""
        assert "get_current_date" in teacher_system
        # Check the instruction to call it first appears
        lower = teacher_system.lower()
        assert "first" in lower

    def test_no_profile_placeholder(self, teacher_system):
        """Teacher prompt should NOT have unfilled {profile} template variable."""
        assert "{profile}" not in teacher_system

    def test_no_conversation_placeholder(self, teacher_system):
        """Teacher prompt should NOT have unfilled {conversation} template variable."""
        assert "{conversation}" not in teacher_system

    def test_does_not_contain_assessor_specific_text(self, teacher_system):
        """Teacher prompt should not contain assessor-only scoring rubric."""
        assert "Overall Score" not in teacher_system

    def test_eight_tools_count_in_prompt(self, teacher_system):
        """Prompt states it has eight tools."""
        assert "eight" in teacher_system.lower() or "8" in teacher_system


# ---------------------------------------------------------------------------
# 3. ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------

class TestAssessorSystemContent:

    @pytest.mark.parametrize("keyword", ASSESSOR_STRUCTURAL_KEYWORDS)
    def test_contains_keyword(self, assessor_system, keyword):
        assert keyword in assessor_system, (
            f"ASSESSOR_SYSTEM missing expected keyword: {keyword!r}"
        )

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_all_eight_tools_mentioned(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"ASSESSOR_SYSTEM does not mention tool: {tool_name!r}"
        )

    def test_profile_placeholder_present(self, assessor_system):
        """{profile} must be a template variable in the assessor prompt."""
        assert "{profile}" in assessor_system

    def test_conversation_placeholder_present(self, assessor_system):
        """{conversation} must be a template variable in the assessor prompt."""
        assert "{conversation}" in assessor_system

    def test_overall_score_format(self, assessor_system):
        assert "## Overall Score" in assessor_system or "Overall Score:" in assessor_system

    def test_five_dimensions_present(self, assessor_system):
        dimensions = [
            "First Impression",
            "Needs Discovery",
            "Product Knowledge",
            "Objection Handling",
            "Closing Technique",
        ]
        for dim in dimensions:
            assert dim in assessor_system, f"Missing dimension: {dim!r}"

    def test_dimension_count_is_five(self, assessor_system):
        """Workflow step 3 mentions five dimensions."""
        assert "five" in assessor_system.lower() or "5" in assessor_system

    def test_verification_instruction(self, assessor_system):
        """Assessor must be instructed to verify claims with tools, not memory."""
        lower = assessor_system.lower()
        assert "verify" in lower or "search tools" in lower

    def test_no_reliance_on_memory_instruction(self, assessor_system):
        assert "do not rely on memory" in assessor_system or "not rely on memory" in assessor_system

    def test_age_last_birthday_instruction(self, assessor_system):
        assert "Age Last Birthday" in assessor_system

    def test_alb_abbreviation(self, assessor_system):
        assert "ALB" in assessor_system

    def test_workflow_steps_numbered(self, assessor_system):
        """Assessor prompt should include numbered workflow steps."""
        assert "1." in assessor_system
        assert "2." in assessor_system
        assert "3." in assessor_system

    def test_strengths_section_marker(self, assessor_system):
        assert "Key Strengths" in assessor_system or "Strengths" in assessor_system

    def test_areas_to_improve_section_marker(self, assessor_system):
        assert "Areas to Improve" in assessor_system or "Improve" in assessor_system

    def test_eight_tools_count_in_prompt(self, assessor_system):
        assert "eight" in assessor_system.lower() or "8" in assessor_system

    def test_does_not_contain_teacher_specific_text(self, assessor_system):
        """Assessor prompt should not contain teacher-only training instructions."""
        assert "quiz the agent" not in assessor_system
        assert "Build confidence" not in assessor_system


# ---------------------------------------------------------------------------
# 4. ASSESSOR_SYSTEM template substitution tests
# ---------------------------------------------------------------------------

class TestAssessorSystemTemplateSubstitution:

    @pytest.mark.parametrize("profile", SAMPLE_PROFILES)
    def test_profile_substitution(self, assessor_system, profile):
        """Substituting {profile} should produce a valid string."""
        result = assessor_system.replace("{profile}", profile).replace("{conversation}", "Agent: Hi.")
        assert "{profile}" not in result
        assert isinstance(result, str)

    @pytest.mark.parametrize("conversation", SAMPLE_CONVERSATIONS)
    def test_conversation_substitution(self, assessor_system, conversation):
        """Substituting {conversation} should produce a valid string."""
        result = assessor_system.replace("{profile}", "Name: Test User").replace(
            "{conversation}", conversation
        )
        assert "{conversation}" not in result
        assert isinstance(result, str)

    def test_both_placeholders_substituted(self, assessor_system):
        profile = "Name: John, Age: 40"
        conversation = "Agent: Hello. Customer: Hi."
        result = assessor_system.format(profile=profile, conversation=conversation)
        assert profile in result
        assert conversation in result
        assert "{profile}" not in result
        assert "{conversation}" not in result

    def test_substitution_with_special_characters(self, assessor_system):
        profile = "Name: Chén Wěi (陳偉), Age: 42"
        conversation = "Agent: 您好！ Customer: 你好。 Claim: HKD $10,000 & deductible: 0%"
        result = assessor_system.format(profile=profile, conversation=conversation)
        assert profile in result
        assert conversation in result

    def test_substitution_with_empty_profile(self, assessor_system):
        result = assessor_system.format(profile="", conversation="Agent: Hello.")
        assert isinstance(result, str)
        assert "{profile}" not in result

    def test_substitution_with_empty_conversation(self, assessor_system):
        result = assessor_system.format(profile="Name: Test", conversation="")
        assert isinstance(result, str)
        assert "{conversation}" not in result

    def test_substitution_with_very_long_conversation(self, assessor_system):
        long_conv = "Agent: " + ("Repeated insurance sentence. " * 500)
        result = assessor_system.format(profile="Name: Stress Test", conversation=long_conv)
        assert long_conv in result

    def test_format_raises_on_missing_profile_key(self, assessor_system):
        """Calling format() without 'profile' should raise KeyError."""
        with pytest.raises(KeyError):
            assessor_system.format(conversation="Agent: Hi.")

    def test_format_raises_on_missing_conversation_key(self, assessor_system):
        """Calling format() without 'conversation' should raise KeyError."""
        with pytest.raises(KeyError):
            assessor_system.format(profile="Name: Test")


# ---------------------------------------------------------------------------
# 5. TEACHER_SYSTEM has no unintended template placeholders
# ---------------------------------------------------------------------------

class TestTeacherSystemNoUnintendedPlaceholders:

    def test_no_braced_profile(self