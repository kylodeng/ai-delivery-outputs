"""
Test module for api/agent.py

What is tested:
  - TEACHER_SYSTEM prompt string: presence, key substrings, formatting rules
  - ASSESSOR_SYSTEM prompt string: presence, key substrings, formatting rules
  - Module-level constants: non-empty, correct types
  - Tool references: all eight expected tools are mentioned in both prompts
  - Citation format instructions in TEACHER_SYSTEM
  - Assessment format/dimensions in ASSESSOR_SYSTEM
  - Placeholder variables in ASSESSOR_SYSTEM ({profile}, {conversation})
  - create_agent import (mocked to avoid real LangChain calls)
  - Agent factory behaviour (teacher agent, assessor agent) — stubbed pending
    full implementation details

Mocks used:
  - langchain.agents.create_agent  → unittest.mock.patch / MagicMock
  - Any LLM / tool objects passed to create_agent → MagicMock

TODOs:
  - TODO: Obtain the real agent-factory functions (e.g. build_teacher_agent,
    build_assessor_agent) once they are exposed as public callables in agent.py.
  - TODO: Integration test for astream_events (teacher) and ainvoke (assessor)
    once async wiring is available; needs a mock LLM that supports streaming.
  - TODO: Verify tool-binding logic once ToolNode / tool-list construction is
    visible in the source.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – import the module under test with create_agent stubbed out so we
# never hit the real LangChain dependency during unit tests.
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

ASSESSMENT_DIMENSIONS = [
    "First Impression",
    "Needs Discovery",
    "Product Knowledge",
    "Objection Handling",
    "Closing Technique",
]


def _import_agent_module():
    """Import api.agent with create_agent replaced by a MagicMock."""
    # Build a fake langchain.agents module so the import does not fail even if
    # langchain is not installed in the test environment.
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_lc_agents.create_agent = MagicMock(return_value=MagicMock(name="agent"))

    fake_lc = types.ModuleType("langchain")
    fake_lc.agents = fake_lc_agents

    with patch.dict(
        sys.modules,
        {
            "langchain": fake_lc,
            "langchain.agents": fake_lc_agents,
        },
    ):
        # Force re-import in case a previous import is cached.
        sys.modules.pop("api.agent", None)
        sys.modules.pop("agent", None)
        try:
            module = importlib.import_module("api.agent")
        except ModuleNotFoundError:
            # Fallback: try plain 'agent' if package resolution differs.
            module = importlib.import_module("agent")
    return module


@pytest.fixture(scope="module")
def agent_module():
    return _import_agent_module()


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ===========================================================================
# 1. Module-level constant existence and type checks
# ===========================================================================


class TestConstantExistence:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_str(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_assessor_system_is_str(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_teacher_system_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0


# ===========================================================================
# 2. TEACHER_SYSTEM content checks
# ===========================================================================


class TestTeacherSystemContent:
    def test_teacher_role_description_present(self, teacher_system):
        assert "insurance sales trainer" in teacher_system.lower()

    def test_teacher_mentions_agent_audience(self, teacher_system):
        assert "agent" in teacher_system.lower()

    def test_teacher_has_eight_tools_header(self, teacher_system):
        assert "eight tools" in teacher_system.lower() or "8 tools" in teacher_system.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_references_all_tools(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"Expected tool '{tool_name}' to be referenced in TEACHER_SYSTEM"
        )

    def test_teacher_citation_format_inline_marker(self, teacher_system):
        """The exact citation marker format [[Sn]] must be explained."""
        assert "[[S" in teacher_system

    def test_teacher_citation_example_present(self, teacher_system):
        assert "[[S1]]" in teacher_system

    def test_teacher_citation_instruction_present(self, teacher_system):
        assert "citation" in teacher_system.lower() or "cite" in teacher_system.lower()

    def test_teacher_age_last_birthday_instruction(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_teacher_get_current_date_priority(self, teacher_system):
        """Prompt must instruct to call get_current_date first for date calculations."""
        lower = teacher_system.lower()
        assert "get_current_date" in lower
        assert "first" in lower

    def test_teacher_never_guess_instruction(self, teacher_system):
        assert "never guess" in teacher_system.lower() or "never guess" in teacher_system.lower()

    def test_teacher_encourages_engagement(self, teacher_system):
        lower = teacher_system.lower()
        assert any(word in lower for word in ["engaging", "interactive", "quiz", "exercises"])

    def test_teacher_no_placeholder_braces(self, teacher_system):
        """TEACHER_SYSTEM should not contain unfilled {placeholder} variables."""
        import re
        placeholders = re.findall(r"\{[a-zA-Z_]+\}", teacher_system)
        assert placeholders == [], f"Unexpected placeholders found: {placeholders}"

    def test_teacher_system_starts_with_role_statement(self, teacher_system):
        # The prompt should open with a "You are …" persona statement.
        assert teacher_system.strip().startswith("You are")

    def test_teacher_alb_calculation_example(self, teacher_system):
        """An example of ALB miscalculation consequence should be present."""
        assert "wrong premium" in teacher_system.lower() or "wrong premium band" in teacher_system.lower() or "age miscalculation" in teacher_system.lower()


# ===========================================================================
# 3. ASSESSOR_SYSTEM content checks
# ===========================================================================


class TestAssessorSystemContent:
    def test_assessor_role_description_present(self, assessor_system):
        assert "insurance sales trainer" in assessor_system.lower()

    def test_assessor_mentions_accuracy_assessment(self, assessor_system):
        assert "accuracy assessment" in assessor_system.lower() or "assessment" in assessor_system.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_references_all_tools(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"Expected tool '{tool_name}' to be referenced in ASSESSOR_SYSTEM"
        )

    @pytest.mark.parametrize("dimension", ASSESSMENT_DIMENSIONS)
    def test_assessor_contains_all_five_dimensions(self, assessor_system, dimension):
        assert dimension in assessor_system, (
            f"Expected assessment dimension '{dimension}' in ASSESSOR_SYSTEM"
        )

    def test_assessor_profile_placeholder(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_assessor_conversation_placeholder(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_assessor_overall_score_format(self, assessor_system):
        assert "Overall Score" in assessor_system

    def test_assessor_score_out_of_ten(self, assessor_system):
        assert "X/10" in assessor_system or "/10" in assessor_system

    def test_assessor_correct_incorrect_markers(self, assessor_system):
        assert "✓ Correct" in assessor_system or "Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system or "Incorrect" in assessor_system

    def test_assessor_partially_correct_marker(self, assessor_system):
        assert "Partially correct" in assessor_system or "partially correct" in assessor_system.lower()

    def test_assessor_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system

    def test_assessor_areas_to_improve_section(self, assessor_system):
        assert "Areas to Improve" in assessor_system

    def test_assessor_workflow_steps_numbered(self, assessor_system):
        assert "1." in assessor_system and "2." in assessor_system and "3." in assessor_system

    def test_assessor_alb_instruction_present(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_assessor_get_current_date_first(self, assessor_system):
        assert "get_current_date" in assessor_system

    def test_assessor_do_not_rely_on_memory(self, assessor_system):
        assert "memory" in assessor_system.lower()

    def test_assessor_eight_tools_header(self, assessor_system):
        assert "eight tools" in assessor_system.lower() or "8 tools" in assessor_system.lower()

    def test_assessor_starts_with_role_statement(self, assessor_system):
        assert assessor_system.strip().startswith("You are")

    def test_assessor_list_products_first_hint(self, assessor_system):
        """Prompt should suggest calling list_products first when product name unclear."""
        assert "list_products" in assessor_system
        lower = assessor_system.lower()
        assert "first" in lower


# ===========================================================================
# 4. Shared tool consistency between the two prompts
# ===========================================================================


class TestToolConsistency:
    """Both prompts must reference exactly the same eight tools."""

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_present_in_both_prompts(self, teacher_system, assessor_system, tool_name):
        assert tool_name in teacher_system, f"'{tool_name}' missing from TEACHER_SYSTEM"
        assert tool_name in assessor_system, f"'{tool_name}' missing from ASSESSOR_SYSTEM"

    def test_tool_count_teacher(self, teacher_system):
        found = [t for t in EXPECTED_TOOLS if t in teacher_system]
        assert len(found) == len(EXPECTED_TOOLS)

    def test_tool_count_assessor(self, assessor_system):
        found = [t for t in EXPECTED_TOOLS if t in assessor_system]
        assert len(found) == len(EXPECTED_TOOLS)


# ===========================================================================
# 5. create_agent import mock verification
# ===========================================================================


class TestCreateAgentImport:
    def test_create_agent_is_imported(self, agent_module):
        assert hasattr(agent_module, "create_agent")

    def test_create_agent_callable(self, agent_module):
        assert callable(agent_module.create_agent)


# ===========================================================================
# 6. ASSESSOR_SYSTEM format-string behaviour with synthetic data
# ===========================================================================


SYNTHETIC_PROFILE = (
    "Customer is a 45-year-old professional seeking whole life coverage. "
    "Interested in Generations II from Sun Life."
)

SYNTHETIC_CONVERSATION = (
    "Agent: Good morning! I'd like to tell you about Generations II, a participating "
    "whole life plan with guaranteed lifelong protection and double bonuses.\n"
    "Customer: What about mental incapacity coverage?\n"
    "Agent: Great question — it includes a mental incapacity benefit as well as an "
    "accelerated benefit for terminal illness and accidental coma."
)


class TestAssessorSystemFormatting:
    def test_format_with_synthetic_data_no_error(self, assessor_system):
        """The template should accept .format() calls with profile and conversation."""
        rendered = assessor_system.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert isinstance(rendered, str)
        assert len(rendered) > len(assessor_system) - 50  # placeholders replaced

    def test_format_profile_injected(self, assessor_system):
        rendered = assessor_system.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_PROFILE in rendered

    def test_format_conversation_injected(self, assessor_system):
        rendered = assessor_system.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_CONVERSATION in rendered

    def test_format_placeholders_removed(self, assessor_system):
        rendered = assessor_system.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert "{profile}" not in rendered
        assert "{conversation}" not in rendered

    def test_format_empty_strings_accepted(self, assessor_system):
        """Edge case: empty profile/conversation should not raise."""
        rendered = assessor_system.format(profile="", conversation="")
        assert isinstance(rendered, str)

    def test_format_multiline_conversation_accepted(self, assessor_system):
        """Multiline conversations (common real-world input) should be fine."""
        multi = "\n".join(
            [f"Turn {i}: agent says something about product {i}" for i in range(20)]
        )
        rendered = assessor_system.format(profile=SYNTHETIC_PROFILE, conversation=multi)
        assert multi in rendered

    def test_format_special_characters_in_profile(self, assessor_system):
        """Profile with special characters should not cause format errors."""
        special_profile = "Client: 李小明, age 50, DOB: 01/01/1974, budget ~HKD 3,000/yr"
        rendered = assessor_system.format(
            profile=special_profile, conversation=SYNTHETIC_CONVERSATION
        )
        assert special_profile in rendered


# ===========================================================================
# 7. Boundary / negative tests on the prompt strings
# ===========================================================================


class TestPromptBoundaryConditions:
    def test_teacher_system_min_length(self, teacher_system):
        """Sanity check: the prompt must be substantive (> 500 chars)."""
        assert len(teacher_system) > 500

    def test_assessor_