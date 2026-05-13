"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content sections, citation format instructions,
  tool list completeness, age/ALB calculation instructions
- ASSESSOR_SYSTEM prompt string: presence, key content sections, tool list completeness,
  five assessment dimensions, scoring format, age/ALB instructions, profile/conversation
  placeholders
- Module-level constants: existence, type correctness
- create_agent import: verified as imported (mocked to avoid real LangChain calls)
- Structural integrity: both prompts contain all eight required tools

Mocks used:
- langchain.agents.create_agent is patched to prevent any real agent construction
- No real LLM, vector store, or external API calls are made

TODOs:
- TODO: Test actual agent graph construction once LangGraph wiring is added to agent.py
- TODO: Test astream_events for teacher agent with a mock LLM/graph
- TODO: Test ainvoke for assessor agent with a mock LLM/graph
- TODO: Test tool binding (all 8 tools attached) once tool objects are exported
- TODO: Test ASSESSOR_SYSTEM.format() with real conversation/profile data end-to-end
"""

import importlib
import sys
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

FIVE_DIMENSIONS = [
    "First Impression",
    "Needs Discovery",
    "Product Knowledge",
    "Objection Handling",
    "Closing Technique",
]

ASSESSMENT_SCORE_MARKERS = [
    "## Overall Score",
    "### 1.",
    "### 2.",
    "### 3.",
    "### 4.",
    "### 5.",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with create_agent mocked out so no real LangChain
    objects are constructed at import time."""
    mock_create_agent = MagicMock(return_value=MagicMock())
    with patch.dict("sys.modules", {"langchain.agents": MagicMock(create_agent=mock_create_agent)}):
        # Remove cached module if already imported
        sys.modules.pop("api.agent", None)
        sys.modules.pop("agent", None)
        try:
            import api.agent as mod
        except ModuleNotFoundError:
            # Fallback: try flat import (depends on PYTHONPATH)
            import importlib as _il
            mod = _il.import_module("agent")
        yield mod


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Synthetic data fixtures (from provided samples)
# ---------------------------------------------------------------------------

SAMPLE_PROFILE = {
    "name": "Alice Wong",
    "age": 35,
    "occupation": "Teacher",
    "needs": "whole life protection and legacy planning",
}

SAMPLE_CONVERSATION = (
    "Agent: Good morning, Ms Wong! I'd love to tell you about Generations II, "
    "a whole life plan from Sun Life.\n"
    "Customer: What does it cover?\n"
    "Agent: It provides lifelong protection, double bonuses, and a mental incapacity benefit. "
    "The annual deductible is HKD 3,000 [[S1]].\n"
    "Customer: Can I use hospitals in mainland China?\n"
    "Agent: Yes, Sun Life has a list of designated hospitals in mainland China, "
    "including all Class 3 hospitals and Class 2A hospitals in 21 designated cities [[S2]].\n"
)

SAMPLE_PROFILE_STR = (
    f"Name: {SAMPLE_PROFILE['name']}, Age: {SAMPLE_PROFILE['age']}, "
    f"Occupation: {SAMPLE_PROFILE['occupation']}, Needs: {SAMPLE_PROFILE['needs']}"
)


# ---------------------------------------------------------------------------
# Tests: module imports and constant types
# ---------------------------------------------------------------------------


class TestModuleImports:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM must be defined"

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM must be defined"

    def test_teacher_system_is_string(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_assessor_system_is_string(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_teacher_system_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0

    def test_create_agent_imported(self, agent_module):
        # create_agent should be importable from langchain.agents (mocked)
        from langchain.agents import create_agent  # noqa: F401 — just verifying importability

    @pytest.mark.skip(reason="TODO: verify LangGraph graph object exported once wiring is added")
    def test_teacher_agent_graph_exported(self, agent_module):
        assert hasattr(agent_module, "teacher_graph")

    @pytest.mark.skip(reason="TODO: verify LangGraph graph object exported once wiring is added")
    def test_assessor_agent_graph_exported(self, agent_module):
        assert hasattr(agent_module, "assessor_graph")


# ---------------------------------------------------------------------------
# Tests: TEACHER_SYSTEM content
# ---------------------------------------------------------------------------


class TestTeacherSystemContent:

    def test_contains_role_description(self, teacher_system):
        assert "insurance sales trainer" in teacher_system.lower() or \
               "insurance sales trainer" in teacher_system, \
            "Teacher prompt must describe the trainer role"

    def test_mentions_agent_audience(self, teacher_system):
        assert "agent" in teacher_system.lower(), \
            "Teacher prompt should address a new insurance agent"

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_contains_all_eight_tools(self, teacher_system, tool_name):
        assert tool_name in teacher_system, \
            f"TEACHER_SYSTEM must mention tool '{tool_name}'"

    def test_contains_alb_instruction(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system, \
            "Teacher prompt must mention Age Last Birthday (ALB)"

    def test_contains_get_current_date_first_instruction(self, teacher_system):
        assert "get_current_date" in teacher_system
        # The instruction to call it first should appear
        lower = teacher_system.lower()
        assert "first" in lower, "Prompt should instruct to call get_current_date first"

    def test_citation_format_defined(self, teacher_system):
        assert "[[S" in teacher_system, \
            "Teacher prompt must define citation marker format [[Sn]]"

    def test_citation_example_present(self, teacher_system):
        # e.g. [[S1]] should appear as an example
        assert "[[S1]]" in teacher_system

    def test_never_guess_instruction(self, teacher_system):
        assert "Never guess" in teacher_system or "never guess" in teacher_system.lower(), \
            "Prompt must tell the agent never to guess product details"

    def test_encouragement_tone_hint(self, teacher_system):
        lower = teacher_system.lower()
        assert any(word in lower for word in ["encouraging", "engaging", "confidence"]), \
            "Teacher prompt should include tone guidance (encouraging/engaging/confidence)"

    def test_age_miscalculation_warning(self, teacher_system):
        assert "miscalculation" in teacher_system.lower() or "wrong premium" in teacher_system.lower(), \
            "Prompt should warn about the impact of age miscalculation on premium bands"

    def test_eight_tools_header_present(self, teacher_system):
        assert "eight tools" in teacher_system.lower() or "8 tools" in teacher_system.lower(), \
            "Prompt should state that eight tools are available"

    def test_no_unresolved_format_placeholders(self, teacher_system):
        # TEACHER_SYSTEM should not have {profile} or {conversation} placeholders
        assert "{profile}" not in teacher_system
        assert "{conversation}" not in teacher_system

    def test_lookups_for_product_questions(self, teacher_system):
        assert "product-specific" in teacher_system.lower() or \
               "product specific" in teacher_system.lower() or \
               "product" in teacher_system.lower()

    def test_tool_get_current_date_description(self, teacher_system):
        # The prompt should describe what get_current_date does
        assert "today" in teacher_system.lower() or "current date" in teacher_system.lower()

    def test_tool_list_products_description(self, teacher_system):
        assert "knowledge base" in teacher_system.lower()

    def test_tool_lookup_hospital_network_description(self, teacher_system):
        assert "hospital" in teacher_system.lower()

    def test_tool_compare_plans_description(self, teacher_system):
        assert "compare" in teacher_system.lower()

    def test_tool_lookup_exclusions_description(self, teacher_system):
        assert "exclusion" in teacher_system.lower() or "excluded" in teacher_system.lower()

    def test_tool_search_claim_procedure_description(self, teacher_system):
        assert "claim" in teacher_system.lower()

    @pytest.mark.parametrize("exercise_word", ["exercises", "quiz", "scenarios", "scenario"])
    def test_practical_exercises_mentioned(self, teacher_system, exercise_word):
        # At least one of these pedagogical words should appear
        if exercise_word in teacher_system.lower():
            assert True
            return
    # If none found, fail with a clear message
    # (done via parametrize; this is a soft check — at least one must match)

    def test_at_least_one_exercise_word_present(self, teacher_system):
        exercise_words = ["exercises", "quiz", "scenarios", "scenario", "simulate"]
        assert any(w in teacher_system.lower() for w in exercise_words), \
            "Teacher prompt should mention exercises, quizzes, or scenarios"


# ---------------------------------------------------------------------------
# Tests: ASSESSOR_SYSTEM content
# ---------------------------------------------------------------------------


class TestAssessorSystemContent:

    def test_contains_role_description(self, assessor_system):
        assert "assessment" in assessor_system.lower()

    def test_contains_roleplay_context(self, assessor_system):
        assert "roleplay" in assessor_system.lower()

    def test_profile_placeholder_present(self, assessor_system):
        assert "{profile}" in assessor_system, \
            "ASSESSOR_SYSTEM must have a {profile} placeholder"

    def test_conversation_placeholder_present(self, assessor_system):
        assert "{conversation}" in assessor_system, \
            "ASSESSOR_SYSTEM must have a {conversation} placeholder"

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_contains_all_eight_tools(self, assessor_system, tool_name):
        assert tool_name in assessor_system, \
            f"ASSESSOR_SYSTEM must mention tool '{tool_name}'"

    @pytest.mark.parametrize("dimension", FIVE_DIMENSIONS)
    def test_contains_five_dimensions(self, assessor_system, dimension):
        assert dimension in assessor_system, \
            f"ASSESSOR_SYSTEM must include assessment dimension '{dimension}'"

    @pytest.mark.parametrize("marker", ASSESSMENT_SCORE_MARKERS)
    def test_score_format_markers_present(self, assessor_system, marker):
        assert marker in assessor_system, \
            f"ASSESSOR_SYSTEM must contain score marker '{marker}'"

    def test_overall_score_format(self, assessor_system):
        assert "## Overall Score: X/10" in assessor_system, \
            "Prompt must include '## Overall Score: X/10' as a format template"

    def test_alb_instruction_present(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_get_current_date_first_instruction(self, assessor_system):
        assert "get_current_date" in assessor_system

    def test_verify_claims_instruction(self, assessor_system):
        assert "verify" in assessor_system.lower(), \
            "Assessor prompt must instruct to verify factual claims"

    def test_do_not_rely_on_memory(self, assessor_system):
        assert "memory" in assessor_system.lower(), \
            "Assessor prompt should warn not to rely on memory"

    def test_correct_incorrect_markers(self, assessor_system):
        assert "Correct" in assessor_system and "Incorrect" in assessor_system, \
            "Assessor prompt must include ✓ Correct / ✗ Incorrect markers"

    def test_partially_correct_marker(self, assessor_system):
        assert "Partially correct" in assessor_system or "Partially Correct" in assessor_system

    def test_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system or "Strengths" in assessor_system

    def test_areas_to_improve_section(self, assessor_system):
        assert "Areas to Improve" in assessor_system or "Improve" in assessor_system

    def test_workflow_numbered_steps(self, assessor_system):
        # Workflow should have at least steps 1, 2, 3
        assert "1." in assessor_system
        assert "2." in assessor_system
        assert "3." in assessor_system

    def test_product_knowledge_dimension_uses_tools(self, assessor_system):
        # The Product Knowledge dimension should explicitly require tool use
        pk_idx = assessor_system.find("Product Knowledge")
        assert pk_idx != -1
        segment = assessor_system[pk_idx: pk_idx + 300]
        assert "tool" in segment.lower() or "search" in segment.lower(), \
            "Product Knowledge section must reference tool usage for verification"

    def test_eight_tools_header_present(self, assessor_system):
        assert "eight tools" in assessor_system.lower() or "8 tools" in assessor_system.lower()

    def test_premium_age_flag_instruction(self, assessor_system):
        assert "Flag" in assessor_system or "flag" in assessor_system, \
            "Assessor should flag incorrect age/premium usage"

    def test_no_double_curly_brace_escaping_issues(self, assessor_system):
        # Ensure only {profile} and {conversation} are placeholders,
        # not broken format strings like {{ or }}
        import re
        single_braces = re.findall(r'(?<!\{)\{(?!\{)(\w+)(?<!\})\}(?!\})', assessor_system)
        allowed = {"profile", "conversation"}
        unexpected = set(single_braces) - allowed
        assert not unexpected, f"Unexpected format placeholders found: {unexpected}"


# ---------------------------------------------------------------------------
# Tests: ASSESSOR_SYSTEM.format() with synthetic data
# ---------------------------------------------------------------------------


class TestAssessorSystemFormatting:

    def test_format_with_sample_profile_and_conversation