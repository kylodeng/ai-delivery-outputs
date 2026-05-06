"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content, structure, key sections
- ASSESSOR_SYSTEM prompt string: presence, content, structure, key sections
- Tool references in both system prompts (all 8 tools mentioned)
- Citation format instructions in TEACHER_SYSTEM
- Assessment format/rubric structure in ASSESSOR_SYSTEM
- Placeholder variables in ASSESSOR_SYSTEM ({profile}, {conversation})
- Module-level constants are strings and non-empty
- create_agent import from langchain.agents (mocked)
- Edge cases: encoding, whitespace, expected keywords

Mocks used:
- langchain.agents.create_agent (patched to avoid real LangChain dependency)
- No external service calls are made directly in agent.py constants/strings

TODOs:
- TODO: Test actual agent creation logic once create_agent usage/wiring is implemented
- TODO: Test astream_events integration for teacher agent once agent object is instantiated
- TODO: Test ainvoke integration for assessor agent once agent object is instantiated
- TODO: Test that all 8 RAG tools are correctly bound to agents (needs tool factory context)
- TODO: Test ASSESSOR_SYSTEM.format() with real profile/conversation data from integration layer
"""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
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

ASSESSOR_RUBRIC_HEADINGS = [
    "First Impression & Rapport Building",
    "Needs Discovery",
    "Product Knowledge & Accuracy",
    "Objection Handling",
    "Closing Technique",
]

ASSESSOR_SUMMARY_SECTIONS = [
    "Key Strengths",
    "Areas to Improve",
]


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with langchain stubbed out so no network calls occur."""
    mock_langchain_agents = MagicMock()
    mock_langchain_agents.create_agent = MagicMock(return_value=MagicMock())

    with patch.dict(
        sys.modules,
        {
            "langchain": MagicMock(),
            "langchain.agents": mock_langchain_agents,
        },
    ):
        # Force fresh import
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "api" in sys.modules:
            # Don't delete the whole api package, just ensure agent is fresh
            pass
        module = importlib.import_module("api.agent")
    return module


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Module-level constant sanity checks
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
        """Prompt should be substantial — at least 200 characters."""
        assert len(teacher_system) >= 200

    def test_assessor_system_minimum_length(self, assessor_system):
        """Assessor prompt should be substantial — at least 200 characters."""
        assert len(assessor_system) >= 200

    def test_teacher_system_is_not_assessor_system(self, teacher_system, assessor_system):
        assert teacher_system != assessor_system


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestTeacherSystemContent:
    def test_teacher_identity(self, teacher_system):
        """Prompt must establish the teacher/trainer persona."""
        lower = teacher_system.lower()
        assert "trainer" in lower or "coach" in lower or "teacher" in lower

    def test_teacher_mentions_insurance(self, teacher_system):
        assert "insurance" in teacher_system.lower()

    def test_teacher_mentions_agent(self, teacher_system):
        lower = teacher_system.lower()
        assert "agent" in lower

    def test_teacher_mentions_discovery(self, teacher_system):
        """Needs discovery is a key teaching concept."""
        lower = teacher_system.lower()
        assert "discovery" in lower or "discover" in lower

    def test_teacher_mentions_exercises_or_scenarios(self, teacher_system):
        lower = teacher_system.lower()
        assert "exercise" in lower or "scenario" in lower or "quiz" in lower

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_lists_all_eight_tools(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"Tool '{tool_name}' is missing from TEACHER_SYSTEM"
        )

    def test_teacher_has_eight_tool_count_mention(self, teacher_system):
        """Prompt explicitly says 'eight tools'."""
        assert "eight" in teacher_system.lower() or "8" in teacher_system

    def test_teacher_age_calculation_guidance(self, teacher_system):
        """ALB / age calculation instructions must be present."""
        lower = teacher_system.lower()
        assert "age last birthday" in lower or "alb" in lower

    def test_teacher_instructs_get_current_date_first(self, teacher_system):
        lower = teacher_system.lower()
        assert "get_current_date" in lower
        # Verify it says to call it first for date-relative questions
        assert "first" in lower

    def test_teacher_citation_format_present(self, teacher_system):
        """Citation marker format [[Sn]] must be documented."""
        assert "[[S" in teacher_system or "[[Sn]]" in teacher_system

    def test_teacher_citation_example_present(self, teacher_system):
        """A concrete citation example should appear."""
        assert "[[S1]]" in teacher_system or "[[S2]]" in teacher_system

    def test_teacher_never_guess_instruction(self, teacher_system):
        lower = teacher_system.lower()
        assert "never guess" in lower or "do not guess" in lower or "never" in lower

    def test_teacher_tool_call_order_guidance(self, teacher_system):
        """Prompt must instruct to call get_current_date before premium calculations."""
        assert "get_current_date" in teacher_system
        idx_gcd = teacher_system.index("get_current_date")
        # At least one reference to "first" near the instruction block
        assert "first" in teacher_system[max(0, idx_gcd - 200): idx_gcd + 400].lower()

    def test_teacher_hkd_currency_reference(self, teacher_system):
        """Example citation uses HKD as currency — confirms HK market context."""
        assert "HKD" in teacher_system

    def test_teacher_mentions_list_products_for_enumeration(self, teacher_system):
        assert "list_products" in teacher_system

    def test_teacher_encouragement_tone(self, teacher_system):
        lower = teacher_system.lower()
        assert (
            "encouraging" in lower
            or "confidence" in lower
            or "engaging" in lower
        )

    def test_teacher_no_trailing_null_bytes(self, teacher_system):
        assert "\x00" not in teacher_system

    def test_teacher_valid_utf8(self, teacher_system):
        """String should round-trip through UTF-8 without error."""
        encoded = teacher_system.encode("utf-8")
        assert encoded.decode("utf-8") == teacher_system


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestAssessorSystemContent:
    def test_assessor_identity(self, assessor_system):
        lower = assessor_system.lower()
        assert "assessor" in lower or "assessment" in lower or "trainer" in lower

    def test_assessor_mentions_roleplay(self, assessor_system):
        lower = assessor_system.lower()
        assert "roleplay" in lower or "role-play" in lower or "role play" in lower

    def test_assessor_has_profile_placeholder(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_assessor_has_conversation_placeholder(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_assessor_format_with_sample_data(self, assessor_system):
        """str.format() must succeed with the two required placeholders."""
        formatted = assessor_system.format(
            profile="Test customer: 35-year-old, non-smoker.",
            conversation="Agent: Hello. Customer: Hi.",
        )
        assert "Test customer" in formatted
        assert "Agent: Hello" in formatted

    def test_assessor_format_does_not_raise_key_error(self, assessor_system):
        try:
            assessor_system.format(profile="p", conversation="c")
        except KeyError as exc:
            pytest.fail(f"Unexpected placeholder in ASSESSOR_SYSTEM: {exc}")

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_lists_all_eight_tools(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"Tool '{tool_name}' is missing from ASSESSOR_SYSTEM"
        )

    def test_assessor_has_five_rubric_dimensions(self, assessor_system):
        """All five scoring dimensions must appear."""
        count = sum(1 for h in ASSESSOR_RUBRIC_HEADINGS if h in assessor_system)
        assert count == len(ASSESSOR_RUBRIC_HEADINGS), (
            f"Missing rubric headings. Found {count}/{len(ASSESSOR_RUBRIC_HEADINGS)}"
        )

    @pytest.mark.parametrize("heading", ASSESSOR_RUBRIC_HEADINGS)
    def test_assessor_rubric_heading_present(self, assessor_system, heading):
        assert heading in assessor_system, f"Rubric heading '{heading}' not found"

    def test_assessor_overall_score_format(self, assessor_system):
        assert "Overall Score" in assessor_system

    def test_assessor_score_out_of_ten_format(self, assessor_system):
        """Pattern X/10 must appear for scoring."""
        assert "/10" in assessor_system or "X/10" in assessor_system

    @pytest.mark.parametrize("section", ASSESSOR_SUMMARY_SECTIONS)
    def test_assessor_summary_sections_present(self, assessor_system, section):
        assert section in assessor_system, f"Summary section '{section}' not found"

    def test_assessor_correct_incorrect_markers(self, assessor_system):
        """Verification markers must appear in the rubric instructions."""
        assert "Correct" in assessor_system
        assert "Incorrect" in assessor_system

    def test_assessor_partially_correct_marker(self, assessor_system):
        assert "Partially correct" in assessor_system or "Partially Correct" in assessor_system

    def test_assessor_workflow_numbered_steps(self, assessor_system):
        """Workflow must include numbered steps 1, 2, 3."""
        assert "1." in assessor_system
        assert "2." in assessor_system
        assert "3." in assessor_system

    def test_assessor_age_alb_guidance(self, assessor_system):
        lower = assessor_system.lower()
        assert "age last birthday" in lower or "alb" in lower

    def test_assessor_instructs_get_current_date_first(self, assessor_system):
        assert "get_current_date" in assessor_system

    def test_assessor_verify_claims_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "verify" in lower or "verif" in lower

    def test_assessor_do_not_rely_on_memory(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower or "do not rely" in lower

    def test_assessor_no_trailing_null_bytes(self, assessor_system):
        assert "\x00" not in assessor_system

    def test_assessor_valid_utf8(self, assessor_system):
        encoded = assessor_system.encode("utf-8")
        assert encoded.decode("utf-8") == assessor_system

    def test_assessor_markdown_headers_present(self, assessor_system):
        """The output format uses ## and ### markdown headers."""
        assert "##" in assessor_system

    def test_assessor_mentions_five_dimensions(self, assessor_system):
        lower = assessor_system.lower()
        assert "five" in lower or "5" in assessor_system


# ---------------------------------------------------------------------------
# Shared tool coverage across both prompts
# ---------------------------------------------------------------------------


class TestBothPromptsToolCoverage:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_in_teacher_system(self, teacher_system, tool_name):
        assert tool_name in teacher_system

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_in_assessor_system(self, assessor_system, tool_name):
        assert tool_name in assessor_system

    def test_lookup_hospital_network_context_teacher(self, teacher_system):
        """Hospital network tool must be contextualised (e.g., 'can I go to')."""
        lower = teacher_system.lower()
        assert "hospital" in lower

    def test_lookup_hospital_network_context_assessor(self, assessor_system):
        lower = assessor_system.lower()
        assert "hospital" in lower

    def test_compare_plans_context(self, teacher_system):
        lower = teacher_system.lower()
        assert "compare" in lower or "plan" in lower

    def test_lookup_exclusions_context_teacher(self, teacher_system):
        lower = teacher_system.lower()
        assert "exclusion" in lower or "not covered" in lower or "waiting period" in lower

    def test_lookup_exclusions_context_assessor(self, assessor_system):
        lower = assessor_system.lower()
        assert "exclusion" in lower or "restriction" in lower


# ---------------------------------------------------------------------------
# Synthetic data / integration-style tests (string formatting)
# ---------------------------------------------------------------------------


class TestAssessorWithSyntheticData:
    """Use the synthetic data samples from the task as test inputs."""

    SAMPLE_PROFILES = [
        "35-year-old non-smoker, interested in Generations II whole life plan.",
        "50-year-old, pre-existing hypertension, wants hospital coverage in mainland China.",
        "28-year-old, looking for cashless hospital arrangement abroad.",
    ]

    SAMPLE_CONVERSATIONS = [
        (
            "Agent: Good morning! I'd like to introduce Generations II, a whole life plan "
            "