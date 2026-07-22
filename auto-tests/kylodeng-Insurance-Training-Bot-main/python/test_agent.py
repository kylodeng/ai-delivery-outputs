"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, structure, key sections, tool references, citation format
- ASSESSOR_SYSTEM prompt string: presence, structure, key sections, tool references, placeholders
- Module-level constants: non-empty, correct types
- create_agent import and call behaviour (mocked)
- Both system prompts reference all eight expected tools
- Prompt constraints (age/premium guidance, citation format, never-guess rule, etc.)
- Edge cases: placeholder formatting in ASSESSOR_SYSTEM, template rendering

Mocks used:
- langchain.agents.create_agent (patched to avoid real LLM/tool wiring)

TODOs:
- TODO: Integration tests for teacher_agent and assessor_agent graph execution
  require a live or stubbed LangGraph runtime and a vector store — add once those
  fixtures are available.
- TODO: Tests for astream_events / ainvoke behaviour of actual agent objects
  require async LangGraph fixtures.
- TODO: Tests for each RAG tool (get_current_date, list_products, etc.) — those
  live in a separate tools module; add once the module path is confirmed.
"""

import importlib
import sys
import types
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


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with create_agent mocked out so no LLM is initialised."""
    mock_lc_agents = types.ModuleType("langchain.agents")
    mock_lc_agents.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    mock_langchain = types.ModuleType("langchain")
    mock_langchain.agents = mock_lc_agents

    with patch.dict(
        sys.modules,
        {
            "langchain": mock_langchain,
            "langchain.agents": mock_lc_agents,
        },
    ):
        # Force fresh import so the mock is in place
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "agent" in sys.modules:
            del sys.modules["agent"]

        spec = importlib.util.spec_from_file_location("agent", "api/agent.py")
        mod = importlib.util.module_from_spec(spec)
        # Inject mocked langchain before exec
        mod.__builtins__ = __builtins__
        sys.modules["agent"] = mod
        spec.loader.exec_module(mod)
        yield mod
        # cleanup
        del sys.modules["agent"]


@pytest.fixture(scope="module")
def teacher(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — basic sanity
# ---------------------------------------------------------------------------


class TestTeacherSystemBasic:
    def test_is_string(self, teacher):
        assert isinstance(teacher, str)

    def test_is_non_empty(self, teacher):
        assert len(teacher.strip()) > 0

    def test_starts_with_you_are(self, teacher):
        assert teacher.strip().startswith("You are")

    def test_contains_role_description(self, teacher):
        lower = teacher.lower()
        assert "insurance sales trainer" in lower or "trainer" in lower

    def test_minimum_length(self, teacher):
        # A meaningful system prompt should be at least 500 chars
        assert len(teacher) >= 500


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — tool references
# ---------------------------------------------------------------------------


class TestTeacherSystemTools:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_references_tool(self, teacher, tool_name):
        assert tool_name in teacher, (
            f"TEACHER_SYSTEM does not reference tool '{tool_name}'"
        )

    def test_tool_count_at_least_eight(self, teacher):
        found = [t for t in EXPECTED_TOOLS if t in teacher]
        assert len(found) == 8

    def test_eight_tools_label(self, teacher):
        """Prompt should tell the agent it has eight tools."""
        assert "eight tools" in teacher.lower() or "8 tools" in teacher.lower()


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — age / premium guidance
# ---------------------------------------------------------------------------


class TestTeacherSystemAgeGuidance:
    def test_mentions_age_last_birthday(self, teacher):
        assert "Age Last Birthday" in teacher or "ALB" in teacher

    def test_mentions_get_current_date_for_age(self, teacher):
        lower = teacher.lower()
        assert "get_current_date" in lower

    def test_warns_against_guessing(self, teacher):
        lower = teacher.lower()
        assert "never guess" in lower or "do not guess" in lower or "never" in lower

    def test_mentions_policy_inception(self, teacher):
        assert "policy inception" in teacher.lower()

    def test_premium_band_warning(self, teacher):
        lower = teacher.lower()
        assert "premium" in lower

    def test_alb_example_present(self, teacher):
        # The prompt contains a concrete example of age miscalculation
        assert "50" in teacher or "January 2020" in teacher


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — citation format
# ---------------------------------------------------------------------------


class TestTeacherSystemCitations:
    def test_citation_marker_format_present(self, teacher):
        # Must describe [[Sn]] format
        assert "[[S" in teacher

    def test_citation_example_present(self, teacher):
        assert "[[S1]]" in teacher or "[[Sn]]" in teacher

    def test_citation_instructions_present(self, teacher):
        lower = teacher.lower()
        assert "citation" in lower or "cite" in lower

    def test_only_cite_from_retrieved_docs(self, teacher):
        lower = teacher.lower()
        assert "retrieved document" in lower or "tool result" in lower

    def test_source_id_format_explained(self, teacher):
        # e.g. [S1: doc p.4]
        assert "[S1:" in teacher or "source ID" in teacher.lower()


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — engagement instructions
# ---------------------------------------------------------------------------


class TestTeacherSystemEngagement:
    def test_mentions_exercises(self, teacher):
        lower = teacher.lower()
        assert "exercise" in lower

    def test_mentions_quiz(self, teacher):
        lower = teacher.lower()
        assert "quiz" in lower

    def test_encourages_questions(self, teacher):
        lower = teacher.lower()
        assert "ask" in lower

    def test_mentions_scenarios(self, teacher):
        lower = teacher.lower()
        assert "scenario" in lower or "mini-scenario" in lower

    def test_mentions_confidence(self, teacher):
        lower = teacher.lower()
        assert "confidence" in lower or "encouraging" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — basic sanity
# ---------------------------------------------------------------------------


class TestAssessorSystemBasic:
    def test_is_string(self, assessor):
        assert isinstance(assessor, str)

    def test_is_non_empty(self, assessor):
        assert len(assessor.strip()) > 0

    def test_starts_with_you_are(self, assessor):
        assert assessor.strip().startswith("You are")

    def test_contains_role_description(self, assessor):
        lower = assessor.lower()
        assert "assessment" in lower or "assessor" in lower or "accuracy" in lower

    def test_minimum_length(self, assessor):
        assert len(assessor) >= 500


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — placeholder / template variables
# ---------------------------------------------------------------------------


class TestAssessorSystemPlaceholders:
    def test_has_profile_placeholder(self, assessor):
        assert "{profile}" in assessor

    def test_has_conversation_placeholder(self, assessor):
        assert "{conversation}" in assessor

    def test_only_two_placeholders(self, assessor):
        import re

        placeholders = re.findall(r"\{[^}]+\}", assessor)
        assert set(placeholders) == {"{profile}", "{conversation}"}

    def test_profile_placeholder_renders(self, assessor):
        rendered = assessor.format(
            profile="Female, 35, non-smoker", conversation="Agent: Hello"
        )
        assert "{profile}" not in rendered
        assert "Female, 35, non-smoker" in rendered

    def test_conversation_placeholder_renders(self, assessor):
        rendered = assessor.format(
            profile="Male, 40", conversation="Agent: Good morning\nCustomer: Hi"
        )
        assert "{conversation}" not in rendered
        assert "Agent: Good morning" in rendered

    def test_placeholders_render_with_synthetic_data(self, assessor):
        """Use synthetic data samples as realistic inputs."""
        profile = (
            "Customer: Hong Kong resident, age 45, looking for whole life coverage. "
            "Interested in Generations II."
        )
        conversation = (
            "Agent: Good morning! I'd like to tell you about Generations II by Sun Life.\n"
            "Customer: What is the annual premium?\n"
            "Agent: Based on your age last birthday of 45, the premium falls in band B.\n"
            "Customer: Does it cover mental incapacity?\n"
            "Agent: Yes, Generations II includes a mental incapacity benefit."
        )
        rendered = assessor.format(profile=profile, conversation=conversation)
        assert "Generations II" in rendered
        assert "mental incapacity" in rendered


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — tool references
# ---------------------------------------------------------------------------


class TestAssessorSystemTools:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_references_tool(self, assessor, tool_name):
        assert tool_name in assessor, (
            f"ASSESSOR_SYSTEM does not reference tool '{tool_name}'"
        )

    def test_tool_count_at_least_eight(self, assessor):
        found = [t for t in EXPECTED_TOOLS if t in assessor]
        assert len(found) == 8

    def test_eight_tools_label(self, assessor):
        assert "eight tools" in assessor.lower() or "8 tools" in assessor.lower()


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — age / premium guidance
# ---------------------------------------------------------------------------


class TestAssessorSystemAgeGuidance:
    def test_mentions_age_last_birthday(self, assessor):
        assert "Age Last Birthday" in assessor or "ALB" in assessor

    def test_mentions_get_current_date(self, assessor):
        assert "get_current_date" in assessor

    def test_mentions_policy_inception(self, assessor):
        assert "policy inception" in assessor.lower()

    def test_flags_incorrect_age_as_error(self, assessor):
        lower = assessor.lower()
        assert "flag" in lower or "error" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — five assessment dimensions
# ---------------------------------------------------------------------------


class TestAssessorSystemDimensions:
    DIMENSIONS = [
        "First Impression",
        "Needs Discovery",
        "Product Knowledge",
        "Objection Handling",
        "Closing Technique",
    ]

    @pytest.mark.parametrize("dimension", DIMENSIONS)
    def test_dimension_present(self, assessor, dimension):
        assert dimension in assessor, (
            f"ASSESSOR_SYSTEM missing assessment dimension '{dimension}'"
        )

    def test_overall_score_format(self, assessor):
        assert "Overall Score" in assessor
        assert "X/10" in assessor or "/10" in assessor

    def test_five_numbered_sections(self, assessor):
        import re

        # Look for "### 1." through "### 5." style headings
        matches = re.findall(r"###\s+\d\.", assessor)
        assert len(matches) >= 5

    def test_strengths_section(self, assessor):
        assert "Strengths" in assessor or "Key Strengths" in assessor

    def test_areas_to_improve_section(self, assessor):
        assert "Areas to Improve" in assessor or "Improve" in assessor

    def test_correct_incorrect_markers(self, assessor):
        """Assessment format should define ✓ Correct / ✗ Incorrect markers."""
        assert "✓ Correct" in assessor or "Correct" in assessor
        assert "✗ Incorrect" in assessor or "Incorrect" in assessor

    def test_partially_correct_marker(self, assessor):
        assert "Partially correct" in assessor or "⚠" in assessor


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — workflow instructions
# ---------------------------------------------------------------------------


class TestAssessorSystemWorkflow:
    def test_workflow_section_present(self, assessor):
        assert "Workflow" in assessor or "workflow" in assessor.lower()

    def test_verify_claims_instruction(self, assessor):
        lower = assessor.lower()
        assert "verify" in lower or "verification" in lower

    def test_use_search_tools_instruction(self, assessor):
        lower = assessor.lower()
        assert "search" in lower

    def test_do_not_rely_on_memory(self, assessor):
        lower = assessor.lower()
        assert "do not rely on memory" in lower or "not rely on memory" in lower

    def test_list_products_fallback_instruction(self, assessor):
        lower = assessor.lower()
        assert "list_products" in lower

    def test_numbered_workflow_steps(self, assessor):
        import re

        steps = re.findall(r"^\s*\d+\.", assessor, re.MULTILINE)
        assert len(steps) >= 3, "Workflow should have at least 3 numbered steps"


# ---------------------------------------------------------------------------
# Module-level constant tests
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_teacher_system_is_module_attribute(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_is_module_attribute(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_type(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_type(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_teacher_and_assessor_are_different(self, agent_module):
        assert agent_module.TEACHER_SYSTEM != agent_module.ASSESSOR_SYSTEM

    def test_teacher_system_not_none(self, agent_module):
        assert agent_module.TEACHER_SYSTEM is not None

    def test_assessor_system_not_none(self, agent_module):
        assert agent_module.ASSESSOR_SYSTEM is not None

    def test_module_docstring_present(self, agent_module):
        assert agent_module.__doc__ is not None
        assert len(agent_module.__doc__.strip()) > 0

    def test_module_docstring_mentions_teacher(self, agent_module):
        assert "teacher" in agent_module.__doc__.lower()

    def test_module_docstring_mentions_assessor(self, agent_module):
        assert "assess" in agent_module.__doc__.lower()


# ---------------------------------------------------------------------------
# create_agent import / usage
# ---------------------------------------------------------------------------


class TestCreateAgentImport:
    def test_create_agent_importable(self):
        """create_agent