"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content markers, citation format instructions,
  tool list completeness, age/ALB calculation reminder, non-empty content.
- ASSESSOR_SYSTEM prompt template: presence, key content markers, {profile} and {conversation}
  placeholders, tool list completeness, age/ALB calculation reminder, five assessment
  dimensions, scoring format, workflow steps.
- Both system prompts share the same eight RAG tools listed by name.
- String formatting of ASSESSOR_SYSTEM with synthetic profile/conversation data.
- Module-level docstring content.
- create_agent import side-effects (mocked).

Mocks used:
- unittest.mock.patch / MagicMock for `langchain.agents.create_agent` to avoid any
  real LangChain / LLM calls.

TODOs:
- TODO: Integration tests for the actual teacher/assessor agent graph execution
  require a live or stubbed LangGraph environment and LLM backend.
- TODO: Tests for astream_events streaming behaviour need an async LangGraph harness.
- TODO: Tests for ainvoke one-shot assessment need a mocked LangGraph runnable.
- TODO: Verify that all eight tools are actually registered on the compiled graph
  once the full agent-creation code is visible.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – import the module under test with LangChain stubbed out so that
# no real network / LLM calls are made at import time.
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


def _import_agent_module():
    """Import api.agent with langchain stubs injected."""
    # Stub out langchain.agents so create_agent doesn't blow up
    langchain_agents_stub = types.ModuleType("langchain.agents")
    langchain_agents_stub.create_agent = MagicMock(return_value=MagicMock())

    langchain_stub = types.ModuleType("langchain")
    langchain_stub.agents = langchain_agents_stub

    # Ensure the stubs are visible before import
    with patch.dict(
        sys.modules,
        {
            "langchain": langchain_stub,
            "langchain.agents": langchain_agents_stub,
        },
    ):
        # Force re-import in case it was already cached
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "api" in sys.modules:
            # Don't remove the package itself, just the submodule
            pass

        import api.agent as agent_mod  # noqa: PLC0415

    return agent_mod


@pytest.fixture(scope="module")
def agent_module():
    return _import_agent_module()


@pytest.fixture(scope="module")
def teacher_prompt(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_prompt(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ===========================================================================
# MODULE-LEVEL SANITY
# ===========================================================================


class TestModuleImport:
    def test_module_loads_without_error(self, agent_module):
        assert agent_module is not None

    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_create_agent_imported(self, agent_module):
        assert hasattr(agent_module, "create_agent")

    def test_module_docstring_present(self, agent_module):
        assert agent_module.__doc__ is not None
        assert len(agent_module.__doc__.strip()) > 0

    def test_module_docstring_mentions_teacher(self, agent_module):
        assert "teacher" in agent_module.__doc__.lower()

    def test_module_docstring_mentions_assessor(self, agent_module):
        assert "assess" in agent_module.__doc__.lower()

    def test_module_docstring_mentions_rag_tools(self, agent_module):
        assert "rag" in agent_module.__doc__.lower() or "tool" in agent_module.__doc__.lower()


# ===========================================================================
# TEACHER_SYSTEM PROMPT
# ===========================================================================


class TestTeacherSystemPrompt:

    # --- Basic sanity ---

    def test_is_non_empty_string(self, teacher_prompt):
        assert isinstance(teacher_prompt, str)
        assert len(teacher_prompt.strip()) > 0

    def test_is_multiline(self, teacher_prompt):
        assert "\n" in teacher_prompt

    # --- Role identity ---

    def test_identifies_as_trainer_or_coach(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "trainer" in lower or "coach" in lower

    def test_mentions_insurance(self, teacher_prompt):
        assert "insurance" in teacher_prompt.lower()

    def test_mentions_agent(self, teacher_prompt):
        assert "agent" in teacher_prompt.lower()

    # --- Tool list completeness ---

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_contains_tool_name(self, teacher_prompt, tool_name):
        assert tool_name in teacher_prompt, (
            f"Tool '{tool_name}' not found in TEACHER_SYSTEM"
        )

    def test_exactly_eight_tools_section(self, teacher_prompt):
        """Prompt claims it has 'eight tools'."""
        assert "eight" in teacher_prompt.lower() or "8" in teacher_prompt

    # --- Age / ALB calculation instructions ---

    def test_mentions_age_last_birthday(self, teacher_prompt):
        assert "Age Last Birthday" in teacher_prompt or "ALB" in teacher_prompt

    def test_mentions_get_current_date_for_age(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "get_current_date" in lower

    def test_mentions_policy_inception(self, teacher_prompt):
        assert "inception" in teacher_prompt.lower()

    def test_age_calculation_warning_present(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "miscalculation" in lower or "wrong premium" in lower or "premium band" in lower

    # --- Citation instructions ---

    def test_citation_format_marker_present(self, teacher_prompt):
        assert "[[S" in teacher_prompt or "[[Sn]]" in teacher_prompt

    def test_citation_instructions_mention_source_id(self, teacher_prompt):
        assert "source" in teacher_prompt.lower() or "citation" in teacher_prompt.lower()

    def test_citation_example_present(self, teacher_prompt):
        # Prompt should include an example like [[S1]]
        assert "[[S1]]" in teacher_prompt

    def test_cite_only_from_retrieved_docs(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "retrieved" in lower or "document" in lower

    # --- Behavioural instructions ---

    def test_instructs_not_to_guess(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "never guess" in lower or "do not guess" in lower or "never" in lower

    def test_instructs_to_use_tools_before_answering(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "always use" in lower or "use the appropriate tool" in lower

    def test_encourages_engagement(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "engaging" in lower or "interactive" in lower or "encouraging" in lower

    def test_mentions_exercises_or_scenarios(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "exercise" in lower or "scenario" in lower or "quiz" in lower

    # --- Specific tool descriptions ---

    def test_get_current_date_described(self, teacher_prompt):
        assert "today" in teacher_prompt.lower() or "current date" in teacher_prompt.lower()

    def test_list_products_described(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "knowledge base" in lower or "product" in lower

    def test_lookup_hospital_network_purpose_described(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "hospital" in lower

    def test_lookup_exclusions_described(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "exclusion" in lower or "not covered" in lower

    def test_search_claim_procedure_described(self, teacher_prompt):
        lower = teacher_prompt.lower()
        assert "claim" in lower


# ===========================================================================
# ASSESSOR_SYSTEM PROMPT
# ===========================================================================


class TestAssessorSystemPrompt:

    # --- Basic sanity ---

    def test_is_non_empty_string(self, assessor_prompt):
        assert isinstance(assessor_prompt, str)
        assert len(assessor_prompt.strip()) > 0

    def test_is_multiline(self, assessor_prompt):
        assert "\n" in assessor_prompt

    # --- Template placeholders ---

    def test_contains_profile_placeholder(self, assessor_prompt):
        assert "{profile}" in assessor_prompt

    def test_contains_conversation_placeholder(self, assessor_prompt):
        assert "{conversation}" in assessor_prompt

    def test_format_with_synthetic_profile_and_conversation(self, assessor_prompt):
        """ASSESSOR_SYSTEM must be a valid Python format string."""
        profile = (
            "Name: Jane Doe, Age: 35, Looking for health insurance, "
            "has two children, non-smoker."
        )
        conversation = (
            "Agent: Good morning! I'd like to help you find the right plan.\n"
            "Customer: I'm interested in the Generations II plan.\n"
            "Agent: The Generations II plan offers lifelong protection and double bonuses [[S1]].\n"
        )
        rendered = assessor_prompt.format(profile=profile, conversation=conversation)
        assert profile in rendered
        assert conversation in rendered

    def test_format_with_empty_strings(self, assessor_prompt):
        rendered = assessor_prompt.format(profile="", conversation="")
        assert isinstance(rendered, str)

    def test_format_with_special_characters(self, assessor_prompt):
        rendered = assessor_prompt.format(
            profile="Client: O'Brien & Ó Séaghdha — age 42",
            conversation="Agent said: "No waiting period!" (incorrect?)",
        )
        assert isinstance(rendered, str)

    # --- Role identity ---

    def test_identifies_as_assessor_or_trainer(self, assessor_prompt):
        lower = assessor_prompt.lower()
        assert "assess" in lower or "trainer" in lower

    def test_mentions_roleplay(self, assessor_prompt):
        assert "roleplay" in assessor_prompt.lower()

    def test_mentions_trainee(self, assessor_prompt):
        assert "trainee" in assessor_prompt.lower()

    # --- Tool list completeness ---

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_contains_tool_name(self, assessor_prompt, tool_name):
        assert tool_name in assessor_prompt, (
            f"Tool '{tool_name}' not found in ASSESSOR_SYSTEM"
        )

    # --- Five assessment dimensions ---

    @pytest.mark.parametrize(
        "dimension",
        [
            "First Impression",
            "Needs Discovery",
            "Product Knowledge",
            "Objection Handling",
            "Closing",
        ],
    )
    def test_assessment_dimension_present(self, assessor_prompt, dimension):
        assert dimension in assessor_prompt, (
            f"Assessment dimension '{dimension}' not found in ASSESSOR_SYSTEM"
        )

    def test_five_numbered_dimensions(self, assessor_prompt):
        for i in range(1, 6):
            assert f"{i}." in assessor_prompt

    # --- Scoring format ---

    def test_overall_score_header_present(self, assessor_prompt):
        assert "Overall Score" in assessor_prompt

    def test_score_out_of_ten_format(self, assessor_prompt):
        assert "X/10" in assessor_prompt or "/10" in assessor_prompt

    # --- Workflow instructions ---

    def test_workflow_section_present(self, assessor_prompt):
        assert "Workflow" in assessor_prompt or "workflow" in assessor_prompt.lower()

    def test_workflow_step_1_read_conversation(self, assessor_prompt):
        lower = assessor_prompt.lower()
        assert "read" in lower or "identify" in lower

    def test_workflow_step_verify_claims_with_tools(self, assessor_prompt):
        lower = assessor_prompt.lower()
        assert "tool" in lower and ("verif" in lower or "retriev" in lower)

    def test_workflow_instructs_list_products_first(self, assessor_prompt):
        lower = assessor_prompt.lower()
        assert "list_products" in lower

    # --- Age / ALB calculation instructions ---

    def test_mentions_age_last_birthday(self, assessor_prompt):
        assert "Age Last Birthday" in assessor_prompt or "ALB" in assessor_prompt

    def test_mentions_get_current_date_for_verification(self, assessor_prompt):
        assert "get_current_date" in assessor_prompt

    def test_mentions_inception_for_premium(self, assessor_prompt):
        assert "inception" in assessor_prompt.lower()

    def test_instructs_flag_wrong_age_premium(self, assessor_prompt):
        lower = assessor_prompt.lower()
        assert "flag" in lower or "error" in lower

    # --- Verification markers ---

    def test_correct_marker_present(self, assessor_prompt):
        assert "✓ Correct" in assessor_prompt or "Correct" in assessor_prompt

    def test_incorrect_marker_present(self, assessor_prompt):
        assert "✗ Incorrect" in assessor_prompt or "Incorrect" in assessor_prompt

    def test_partially_correct_marker_present(self, assessor_prompt):
        assert "Partially" in assessor_prompt

    # --- Output sections ---

    def test_key_strengths_section_present(self, assessor_prompt):
        assert "Strengths" in assessor_prompt or "strengths" in assessor_prompt.lower()

    def test_areas_to_improve_section_present(self, assessor_prompt):
        lower = assessor_prompt.lower()
        assert "improve" in lower or "areas" in lower

    def test_do_not_rely_on_memory(self, assessor_prompt):
        lower = assessor_prompt.lower()
        assert "memory" in lower or "do not rely" in lower


# ===========================================================================
# SHARED PROPERTIES ACROSS BOTH PROMPTS
# ===========================================================================


class TestSharedPromptProperties:

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_both_prompts_contain_tool(self, teacher_prompt, assessor_prompt, tool_name):
        assert tool_name in teacher_prompt
        assert tool_name in assessor_prompt

    def test_both_prompts_mention_alb(self, teacher_prompt, assessor_prompt):
        alb_phrase = "Age Last Birthday"
        assert alb_phrase in teacher_prompt
        assert alb_phrase in assessor_prompt

    def test_both_prompts_mention_get_current_date(self, teacher_prompt, assessor_prompt):
        assert "