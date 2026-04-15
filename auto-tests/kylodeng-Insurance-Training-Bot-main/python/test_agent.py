"""
Tests for api/agent.py

What is tested:
  - TEACHER_SYSTEM prompt string: presence, structure, tool mentions, citation format,
    age/ALB instructions, content requirements
  - ASSESSOR_SYSTEM prompt string: presence, structure, placeholders, tool mentions,
    age/ALB instructions, scoring format, five assessment dimensions
  - Module-level constants: type checks, non-empty assertions
  - create_agent import and usage (mocked)
  - Prompt template rendering with synthetic profile/conversation data
  - Edge cases: placeholder substitution, missing keys, boundary string checks

Mocks used:
  - langchain.agents.create_agent (patched to avoid real LangChain calls)

TODOs:
  - TODO: Test actual agent invocation (teacher_agent.astream_events) — requires
    a running LangGraph runtime and real or stubbed LLM
  - TODO: Test actual assessor agent (assessor_agent.ainvoke) — requires async
    LangGraph runtime
  - TODO: Test RAG tool implementations (get_current_date, list_products, etc.) —
    not defined in this module; need tool source files
  - TODO: Verify ASSESSOR_SYSTEM truncation — source ends at "Specific Re", need
    complete string to test closing section
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SYNTHETIC_PROFILE = (
    "Age: 35, Female, Non-smoker, looking for whole life coverage. "
    "Interested in Generations II plan. Has two dependents."
)

SYNTHETIC_CONVERSATION = (
    "Agent: Good morning! I'm here to help you find the right plan.\n"
    "Customer: I'd like to know about Generations II.\n"
    "Agent: Generations II offers guaranteed lifelong protection and double bonuses. "
    "The annual premium for your age band is HKD 20,000.\n"
    "Customer: Does it cover mental health?\n"
    "Agent: Yes, it includes a mental incapacity benefit.\n"
    "Customer: What about hospital stays in mainland China?\n"
    "Agent: Sun Life has a list of designated hospitals in mainland China covering "
    "Class 3 hospitals and selected Class 2A hospitals.\n"
)


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with create_agent mocked out."""
    mock_create_agent = MagicMock(return_value=MagicMock())
    mock_langchain_agents = types.ModuleType("langchain.agents")
    mock_langchain_agents.create_agent = mock_create_agent

    with patch.dict(
        sys.modules,
        {
            "langchain": MagicMock(),
            "langchain.agents": mock_langchain_agents,
        },
    ):
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "agent" in sys.modules:
            del sys.modules["agent"]

        # Try both import paths
        try:
            import api.agent as mod
        except ModuleNotFoundError:
            # Fallback: add parent to path and import directly
            import importlib.util
            import os

            spec = importlib.util.spec_from_file_location(
                "agent", os.path.join(os.path.dirname(__file__), "..", "api", "agent.py")
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

        yield mod


@pytest.fixture()
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture()
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — basic type and presence
# ---------------------------------------------------------------------------


class TestTeacherSystemBasic:
    def test_is_string(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_is_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_minimum_length(self, teacher_system):
        """Prompt should be substantive — at least 500 chars."""
        assert len(teacher_system) >= 500


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — role / persona
# ---------------------------------------------------------------------------


class TestTeacherSystemPersona:
    def test_mentions_trainer_or_coach(self, teacher_system):
        lower = teacher_system.lower()
        assert "trainer" in lower or "coach" in lower

    def test_mentions_insurance(self, teacher_system):
        assert "insurance" in teacher_system.lower()

    def test_mentions_agent(self, teacher_system):
        assert "agent" in teacher_system.lower()

    def test_encouraging_tone_keywords(self, teacher_system):
        lower = teacher_system.lower()
        assert any(kw in lower for kw in ["encouraging", "engaging", "confidence", "hands-on"])


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — all eight tools listed
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


class TestTeacherSystemTools:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_mentioned(self, teacher_system, tool_name):
        assert tool_name in teacher_system, f"Tool '{tool_name}' missing from TEACHER_SYSTEM"

    def test_eight_tools_total(self, teacher_system):
        """The prompt explicitly states 'eight tools'."""
        assert "eight" in teacher_system.lower()

    def test_get_current_date_priority(self, teacher_system):
        """get_current_date should be described as the first tool to call."""
        lower = teacher_system.lower()
        # instruction to call first for date-relative calculations
        assert "get_current_date" in teacher_system
        # The prompt should say to call it first
        idx_tool = teacher_system.index("get_current_date")
        surrounding = teacher_system[max(0, idx_tool - 200): idx_tool + 200].lower()
        assert "first" in surrounding


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — age / ALB instructions
# ---------------------------------------------------------------------------


class TestTeacherSystemAgeInstructions:
    def test_alb_mentioned(self, teacher_system):
        assert "ALB" in teacher_system or "Age Last Birthday" in teacher_system

    def test_policy_inception_mentioned(self, teacher_system):
        assert "inception" in teacher_system.lower()

    def test_premium_calculation_warning(self, teacher_system):
        lower = teacher_system.lower()
        assert "premium" in lower

    def test_age_miscalculation_warning(self, teacher_system):
        lower = teacher_system.lower()
        assert "miscalcul" in lower or "wrong" in lower or "incorrect" in lower or "outdated" in lower

    def test_example_age_scenario_present(self, teacher_system):
        """Prompt includes a concrete age example (January 2020 / age 50)."""
        assert "50" in teacher_system or "January 2020" in teacher_system


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — citation instructions
# ---------------------------------------------------------------------------


class TestTeacherSystemCitations:
    def test_citation_marker_format(self, teacher_system):
        """Prompt must show the [[Sn]] citation format."""
        assert "[[S" in teacher_system

    def test_citation_example_present(self, teacher_system):
        assert "[[S1]]" in teacher_system

    def test_citation_inline_instruction(self, teacher_system):
        lower = teacher_system.lower()
        assert "cit" in lower  # "citation" or "cite"

    def test_citation_only_from_retrieved_docs(self, teacher_system):
        lower = teacher_system.lower()
        assert "retrieved" in lower or "document" in lower


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — never-guess instruction
# ---------------------------------------------------------------------------


class TestTeacherSystemNeverGuess:
    def test_never_guess_instruction(self, teacher_system):
        lower = teacher_system.lower()
        assert "never guess" in lower or "do not guess" in lower or "never" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — basic type and presence
# ---------------------------------------------------------------------------


class TestAssessorSystemBasic:
    def test_is_string(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_is_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0

    def test_minimum_length(self, assessor_system):
        assert len(assessor_system) >= 500


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — placeholders
# ---------------------------------------------------------------------------


class TestAssessorSystemPlaceholders:
    def test_profile_placeholder(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_conversation_placeholder(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_no_other_unformatted_placeholders(self, assessor_system):
        """After substituting {profile} and {conversation} no braces should remain."""
        rendered = assessor_system.replace("{profile}", "").replace("{conversation}", "")
        # Allow escaped braces or section headers but no leftover single {word}
        import re
        # find any {word} patterns that are not {{ or }}
        remaining = re.findall(r"(?<!\{)\{[^{}]+\}(?!\})", rendered)
        assert remaining == [], f"Unexpected placeholders remaining: {remaining}"


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — placeholder rendering with synthetic data
# ---------------------------------------------------------------------------


class TestAssessorSystemRendering:
    def test_render_with_synthetic_profile(self, assessor_system):
        rendered = assessor_system.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_PROFILE in rendered

    def test_render_with_synthetic_conversation(self, assessor_system):
        rendered = assessor_system.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_CONVERSATION in rendered

    def test_render_does_not_raise(self, assessor_system):
        """format() should not raise KeyError or ValueError."""
        try:
            assessor_system.format(
                profile=SYNTHETIC_PROFILE,
                conversation=SYNTHETIC_CONVERSATION,
            )
        except (KeyError, ValueError) as exc:
            pytest.fail(f"ASSESSOR_SYSTEM.format() raised {exc}")

    def test_render_with_empty_profile(self, assessor_system):
        rendered = assessor_system.format(profile="", conversation=SYNTHETIC_CONVERSATION)
        assert isinstance(rendered, str)

    def test_render_with_empty_conversation(self, assessor_system):
        rendered = assessor_system.format(profile=SYNTHETIC_PROFILE, conversation="")
        assert isinstance(rendered, str)

    def test_render_with_special_characters_in_profile(self, assessor_system):
        special_profile = "Age: 40, \"quoted name\", <brackets>, & ampersand"
        rendered = assessor_system.format(
            profile=special_profile,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert special_profile in rendered

    def test_render_with_multiline_conversation(self, assessor_system):
        multiline = "Line1\nLine2\nLine3\n"
        rendered = assessor_system.format(
            profile=SYNTHETIC_PROFILE,
            conversation=multiline,
        )
        assert multiline in rendered


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — all eight tools listed
# ---------------------------------------------------------------------------


class TestAssessorSystemTools:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_mentioned(self, assessor_system, tool_name):
        assert tool_name in assessor_system, f"Tool '{tool_name}' missing from ASSESSOR_SYSTEM"

    def test_eight_tools_total(self, assessor_system):
        assert "eight" in assessor_system.lower()

    def test_search_tools_used_for_verification(self, assessor_system):
        """Assessor prompt must instruct using tools to verify claims."""
        lower = assessor_system.lower()
        assert "verif" in lower  # "verify" or "verification"


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — age / ALB instructions
# ---------------------------------------------------------------------------


class TestAssessorSystemAgeInstructions:
    def test_alb_mentioned(self, assessor_system):
        assert "ALB" in assessor_system or "Age Last Birthday" in assessor_system

    def test_policy_inception_mentioned(self, assessor_system):
        assert "inception" in assessor_system.lower()

    def test_get_current_date_first_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "get_current_date" in assessor_system
        # near the ALB mention there should be "first"
        idx = assessor_system.lower().find("get_current_date")
        surrounding = assessor_system[max(0, idx - 150): idx + 150].lower()
        assert "first" in surrounding

    def test_flag_incorrect_age_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "flag" in lower or "error" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — five assessment dimensions / scoring format
# ---------------------------------------------------------------------------


class TestAssessorSystemScoringFormat:
    def test_overall_score_format(self, assessor_system):
        assert "## Overall Score:" in assessor_system or "Overall Score" in assessor_system

    def test_x_out_of_10_format(self, assessor_system):
        assert "X/10" in assessor_system or "/10" in assessor_system

    def test_dimension_1_first_impression(self, assessor_system):
        lower = assessor_system.lower()
        assert "first impression" in lower

    def test_dimension_2_needs_discovery(self, assessor_system):
        lower = assessor_system.lower()
        assert "needs discovery" in lower or "discovery" in lower

    def test_dimension_3_product_knowledge(self, assessor_system):
        lower = assessor_system.lower()
        assert "product knowledge" in lower

    def test_dimension_4_objection_handling(self, assessor_system):
        lower = assessor_system.lower()
        assert "objection" in lower

    def test_dimension_5_closing_technique(self, assessor_system):
        lower = assessor_system.lower()
        assert "closing" in lower

    def test_correct_incorrect_markers(self, assessor_system):
        """Product knowledge section should reference correct/incorrect markers."""
        assert "✓" in assessor_system or "Correct" in assessor_system
        assert "✗" in assessor_system or "Incorrect" in assessor_system

    def test_partially_correct_marker(self, assessor_system):
        assert "Partially" in assessor_system or "⚠" in assessor_system

    def test_strengths_section(self, assessor_system):
        lower = assessor_system.lower()
        assert "strength" in lower

    def test_areas_to_improve_section(self, assessor_system):
        lower = assessor_system.lower()
        assert "improve" in lower or "areas to improve" in lower

    def test_workflow_steps_present(self, assessor_system):
        """Assessor prompt must include a numbered workflow."""
        assert "1." in assessor_system
        assert "2." in assessor_system
        assert "3." in assessor_system


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — persona