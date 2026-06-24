"""
Tests for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content markers, citation format instructions,
  tool list completeness, age/ALB calculation instructions
- ASSESSOR_SYSTEM prompt string: presence, key content markers, profile/conversation
  placeholders, five assessment dimensions, tool list completeness, scoring format,
  age/ALB calculation instructions
- Module-level constants: existence, types, non-emptiness
- create_agent import usage (mocked)
- Structural integrity of both system prompts (tool names, section headers, format strings)

Mocks used:
- langchain.agents.create_agent (patched at api.agent to prevent real LLM/agent init)

TODOs:
- TODO: Test actual agent execution (requires LLM credentials and tool implementations)
- TODO: Test astream_events integration for teacher agent (requires async LangGraph setup)
- TODO: Test ainvoke integration for assessor agent (requires async LangGraph setup)
- TODO: Test tool binding / tool list passed to create_agent (requires full tool registry)
- TODO: Test ASSESSOR_SYSTEM.format() with real profile/conversation data end-to-end
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = [
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
]

ASSESSOR_DIMENSIONS = [
    "First Impression & Rapport Building",
    "Needs Discovery",
    "Product Knowledge & Accuracy",
    "Objection Handling",
    "Closing Technique",
]

SYNTHETIC_PROFILES = [
    "Client: John Doe, Age 50, seeking whole life cover",
    "Client: Jane Smith, Age 35, interested in health insurance",
    "",  # edge case: empty profile
    "A" * 2000,  # edge case: very long profile
]

SYNTHETIC_CONVERSATIONS = [
    "Agent: Good morning! I'd like to tell you about Generations II.\nCustomer: What is it?",
    "Agent: The annual deductible is HKD 3,000.\nCustomer: Is that correct?",
    "",  # edge case: empty conversation
    "\n".join([f"Turn {i}: some text" for i in range(100)]),  # long conversation
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_create_agent():
    """Prevent real agent creation during import and tests."""
    with patch("langchain.agents.create_agent", return_value=MagicMock()) as mock:
        yield mock


@pytest.fixture
def agent_module():
    """Import (or re-import) the agent module under test."""
    # Remove cached module so patches take effect cleanly
    sys.modules.pop("api.agent", None)
    with patch("langchain.agents.create_agent", return_value=MagicMock()):
        import api.agent as mod
        yield mod


# ---------------------------------------------------------------------------
# Module-level constant existence and type checks
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_string(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_string(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_non_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_teacher_system_minimum_length(self, agent_module):
        """Sanity-check that the prompt has meaningful content."""
        assert len(agent_module.TEACHER_SYSTEM) > 200

    def test_assessor_system_minimum_length(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM) > 200


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestTeacherSystemContent:
    def test_teacher_role_description(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM
        assert "insurance sales trainer" in prompt.lower() or "insurance" in prompt

    def test_teacher_mentions_coach(self, agent_module):
        assert "coach" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_mentions_agent(self, agent_module):
        assert "agent" in agent_module.TEACHER_SYSTEM.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_lists_all_tools(self, agent_module, tool_name):
        assert tool_name in agent_module.TEACHER_SYSTEM

    def test_teacher_has_citation_format(self, agent_module):
        """Prompt must describe the [[Sn]] citation marker format."""
        assert "[[S" in agent_module.TEACHER_SYSTEM

    def test_teacher_citation_example_present(self, agent_module):
        assert "[[S1]]" in agent_module.TEACHER_SYSTEM

    def test_teacher_citation_instructions(self, agent_module):
        assert "CITATIONS" in agent_module.TEACHER_SYSTEM

    def test_teacher_age_alb_instruction(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM
        assert "Age Last Birthday" in prompt or "ALB" in prompt

    def test_teacher_get_current_date_call_first(self, agent_module):
        """Prompt must instruct to call get_current_date first for date calcs."""
        prompt = agent_module.TEACHER_SYSTEM
        assert "get_current_date" in prompt

    def test_teacher_premium_calculation_warning(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM
        assert "premium" in prompt.lower()

    def test_teacher_no_guessing_instruction(self, agent_module):
        assert "Never guess" in agent_module.TEACHER_SYSTEM or "never guess" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_discovery_questions_mentioned(self, agent_module):
        assert "discovery" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_engaging_tone(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        # At least one of these engagement indicators should be present
        assert any(word in prompt for word in ["engaging", "interactive", "exercise", "quiz", "confidence"])

    def test_teacher_tool_get_current_date_description(self, agent_module):
        assert "today's date" in agent_module.TEACHER_SYSTEM.lower() or "current date" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_tool_list_products_description(self, agent_module):
        assert "list_products" in agent_module.TEACHER_SYSTEM

    def test_teacher_tool_compare_plans_attributes(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert any(attr in prompt for attr in ["deductible", "annual limit", "room"])

    def test_teacher_tool_lookup_exclusions_description(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM
        assert "lookup_exclusions" in prompt
        assert "NOT covered" in prompt or "excluded" in prompt.lower()

    def test_teacher_tool_lookup_hospital_network_description(self, agent_module):
        assert "lookup_hospital_network" in agent_module.TEACHER_SYSTEM

    def test_teacher_tool_search_claim_procedure_description(self, agent_module):
        assert "search_claim_procedure" in agent_module.TEACHER_SYSTEM

    def test_teacher_age_calculation_example(self, agent_module):
        """Prompt should include a concrete age calculation example."""
        assert "January 2020" in agent_module.TEACHER_SYSTEM or "50" in agent_module.TEACHER_SYSTEM

    def test_teacher_eight_tools_mentioned(self, agent_module):
        assert "eight tools" in agent_module.TEACHER_SYSTEM.lower() or "eight" in agent_module.TEACHER_SYSTEM.lower()


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestAssessorSystemContent:
    def test_assessor_role_description(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "assessment" in prompt.lower()

    def test_assessor_mentions_trainer(self, agent_module):
        assert "trainer" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_has_profile_placeholder(self, agent_module):
        assert "{profile}" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_has_conversation_placeholder(self, agent_module):
        assert "{conversation}" in agent_module.ASSESSOR_SYSTEM

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_lists_all_tools(self, agent_module, tool_name):
        assert tool_name in agent_module.ASSESSOR_SYSTEM

    @pytest.mark.parametrize("dimension", ASSESSOR_DIMENSIONS)
    def test_assessor_has_all_five_dimensions(self, agent_module, dimension):
        assert dimension in agent_module.ASSESSOR_SYSTEM

    def test_assessor_overall_score_format(self, agent_module):
        assert "## Overall Score:" in agent_module.ASSESSOR_SYSTEM or "Overall Score" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_score_format_x_of_10(self, agent_module):
        assert "X/10" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_age_alb_instruction(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "Age Last Birthday" in prompt or "ALB" in prompt

    def test_assessor_get_current_date_instruction(self, agent_module):
        assert "get_current_date" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_workflow_steps(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "1." in prompt and "2." in prompt and "3." in prompt

    def test_assessor_verify_claims_instruction(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "verify" in prompt

    def test_assessor_do_not_rely_on_memory(self, agent_module):
        assert "memory" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_correct_incorrect_markers(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "✓ Correct" in prompt or "Correct" in prompt
        assert "✗ Incorrect" in prompt or "Incorrect" in prompt

    def test_assessor_partially_correct_marker(self, agent_module):
        assert "Partially correct" in agent_module.ASSESSOR_SYSTEM or "partially" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_key_strengths_section(self, agent_module):
        assert "Key Strengths" in agent_module.ASSESSOR_SYSTEM or "strengths" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_areas_to_improve_section(self, agent_module):
        assert "Areas to Improve" in agent_module.ASSESSOR_SYSTEM or "improve" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_eight_tools_mentioned(self, agent_module):
        assert "eight tools" in agent_module.ASSESSOR_SYSTEM.lower() or "eight" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_premium_accuracy_instruction(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "premium" in prompt

    def test_assessor_flag_error_instruction(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "flag" in prompt or "error" in prompt

    def test_assessor_product_knowledge_dimension(self, agent_module):
        assert "Product Knowledge" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_numbered_dimensions(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        for i in range(1, 6):
            assert f"### {i}." in prompt or f"{i}." in prompt

    def test_assessor_list_products_use_first(self, agent_module):
        """Assessor should be instructed to use list_products when unsure of product name."""
        assert "list_products" in agent_module.ASSESSOR_SYSTEM
        assert "unsure" in agent_module.ASSESSOR_SYSTEM.lower() or "exact product name" in agent_module.ASSESSOR_SYSTEM.lower()


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM format() behaviour
# ---------------------------------------------------------------------------


class TestAssessorSystemFormatting:
    @pytest.mark.parametrize("profile,conversation", [
        ("Client: John Doe, Age 50", "Agent: Hello\nCustomer: Hi"),
        ("", ""),
        (SYNTHETIC_PROFILES[0], SYNTHETIC_CONVERSATIONS[0]),
        (SYNTHETIC_PROFILES[1], SYNTHETIC_CONVERSATIONS[1]),
        (SYNTHETIC_PROFILES[3], SYNTHETIC_CONVERSATIONS[3]),
    ])
    def test_format_with_profile_and_conversation(self, agent_module, profile, conversation):
        """ASSESSOR_SYSTEM must be a valid Python format string for {profile}/{conversation}."""
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile=profile, conversation=conversation
        )
        assert isinstance(result, str)
        assert profile in result
        assert conversation in result

    def test_format_missing_profile_raises(self, agent_module):
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(conversation="some conv")

    def test_format_missing_conversation_raises(self, agent_module):
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(profile="some profile")

    def test_format_result_contains_all_dimensions(self, agent_module):
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile="Test profile", conversation="Test conversation"
        )
        for dim in ASSESSOR_DIMENSIONS:
            assert dim in result

    def test_format_result_contains_tools(self, agent_module):
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile="p", conversation="c"
        )
        for tool in EXPECTED_TOOLS:
            assert tool in result

    def test_format_with_special_characters_in_profile(self, agent_module):
        profile = "Client: 李明, Age 45, 香港居民"
        conversation = "代理人: 您好！"
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile=profile, conversation=conversation
        )
        assert profile in result

    def test_format_with_braces_in_conversation(self, agent_module):
        """Curly braces in the conversation text should not break formatting if escaped."""
        # This tests a known edge case — unescaped braces in user input would raise
        #