"""
Test module for api/agent.py

What is tested:
  - TEACHER_SYSTEM prompt string: content, structure, tool mentions, citation format, key instructions
  - ASSESSOR_SYSTEM prompt string: content, structure, tool mentions, placeholders, assessment format
  - Module-level constants existence and types
  - Prompt template rendering (placeholder substitution in ASSESSOR_SYSTEM)
  - Coverage of all eight required tools in both prompts
  - Key behavioural instructions present in both prompts
  - create_agent import availability

Mocks used:
  - unittest.mock.patch used to mock `langchain.agents.create_agent` where needed
  - No external services are called; all tests operate on module-level string constants

TODOs:
  - TODO: Test actual agent graph construction once LangGraph internals and tool
    implementations are available (requires real tool callables and LLM stubs)
  - TODO: Test astream_events streaming behaviour for teacher agent (requires async
    LangGraph harness and mocked LLM)
  - TODO: Test ainvoke one-shot behaviour for assessor agent (requires async harness)
  - TODO: Test that ASSESSOR_SYSTEM correctly formats with real profile/conversation
    payloads end-to-end through the agent pipeline
  - TODO: Test that get_current_date is called before age/premium calculations at
    runtime (requires integration test with mocked tool executor)
"""

import importlib
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – load the module under test while mocking heavy dependencies
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
    """Import api.agent with langchain stubbed out so no real LLM calls occur."""
    # Build a minimal langchain stub so the import doesn't fail if langchain is absent
    langchain_stub = types.ModuleType("langchain")
    agents_stub = types.ModuleType("langchain.agents")
    agents_stub.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
    langchain_stub.agents = agents_stub
    sys.modules.setdefault("langchain", langchain_stub)
    sys.modules.setdefault("langchain.agents", agents_stub)

    # Force a clean reimport
    sys.modules.pop("api.agent", None)
    sys.modules.pop("agent", None)

    try:
        import api.agent as agent_mod
    except ModuleNotFoundError:
        # Fallback: try bare module name
        import importlib.util, os

        spec = importlib.util.spec_from_file_location(
            "agent", os.path.join(os.path.dirname(__file__), "api", "agent.py")
        )
        if spec is None:
            # Last resort: relative path from repo root
            spec = importlib.util.spec_from_file_location(
                "agent", "api/agent.py"
            )
        agent_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent_mod)

    return agent_mod


@pytest.fixture(scope="module")
def agent_mod():
    return _import_agent_module()


@pytest.fixture(scope="module")
def teacher_prompt(agent_mod):
    return agent_mod.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_prompt(agent_mod):
    return agent_mod.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Synthetic data fixtures (from provided samples)
# ---------------------------------------------------------------------------

SAMPLE_PROFILE = {
    "name": "Chan Tai Man",
    "age": 45,
    "occupation": "Engineer",
    "health_status": "Non-smoker, no pre-existing conditions",
    "budget": "HKD 5,000/month",
    "products_of_interest": ["Generations II", "health_products"],
}

SAMPLE_CONVERSATION = (
    "Agent: Good morning! I'd like to introduce you to our Generations II whole life plan.\n"
    "Customer: What is the annual premium?\n"
    "Agent: Based on your age of 45, the annual premium is approximately HKD 30,000.\n"
    "Customer: Does it cover terminal illness?\n"
    "Agent: Yes, it includes an accelerated benefit for terminal illness and accidental coma.\n"
    "Customer: What about mental incapacity?\n"
    "Agent: Generations II also provides a mental incapacity benefit.\n"
    "Customer: Can I go to a hospital in Shanghai?\n"
    "Agent: Yes, Shanghai hospitals are included in our network.\n"
)

SAMPLE_PROFILE_STR = str(SAMPLE_PROFILE)

ASSESSOR_RENDERED_SAMPLE = {
    "profile": SAMPLE_PROFILE_STR,
    "conversation": SAMPLE_CONVERSATION,
}


# ===========================================================================
# 1. Module-level constant existence and types
# ===========================================================================


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_mod):
        assert hasattr(agent_mod, "TEACHER_SYSTEM"), "TEACHER_SYSTEM not found in module"

    def test_assessor_system_exists(self, agent_mod):
        assert hasattr(agent_mod, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM not found in module"

    def test_teacher_system_is_string(self, teacher_prompt):
        assert isinstance(teacher_prompt, str)

    def test_assessor_system_is_string(self, assessor_prompt):
        assert isinstance(assessor_prompt, str)

    def test_teacher_system_not_empty(self, teacher_prompt):
        assert len(teacher_prompt.strip()) > 0

    def test_assessor_system_not_empty(self, assessor_prompt):
        assert len(assessor_prompt.strip()) > 0

    def test_create_agent_importable(self, agent_mod):
        """create_agent should be importable from langchain.agents."""
        from langchain.agents import create_agent  # noqa: F401 – just checking import
        assert callable(create_agent)


# ===========================================================================
# 2. TEACHER_SYSTEM content tests
# ===========================================================================


class TestTeacherSystemContent:

    def test_teacher_role_description_present(self, teacher_prompt):
        assert "insurance sales trainer" in teacher_prompt.lower() or \
               "insurance sales trainer" in teacher_prompt

    def test_teacher_mentions_agent(self, teacher_prompt):
        assert "agent" in teacher_prompt.lower()

    def test_teacher_encourages_interaction(self, teacher_prompt):
        keywords = ["engaging", "encouraging", "interactive", "exercises"]
        assert any(kw in teacher_prompt.lower() for kw in keywords)

    def test_teacher_no_guessing_instruction(self, teacher_prompt):
        assert "never guess" in teacher_prompt.lower()

    def test_teacher_age_last_birthday_instruction(self, teacher_prompt):
        assert "Age Last Birthday" in teacher_prompt or "ALB" in teacher_prompt

    def test_teacher_get_current_date_first_instruction(self, teacher_prompt):
        """Prompt must instruct agent to call get_current_date first for age calculations."""
        assert "get_current_date" in teacher_prompt

    def test_teacher_call_date_first(self, teacher_prompt):
        # Should say to call get_current_date first
        assert re.search(r"call\s+(this\s+first|get_current_date\s+first)", teacher_prompt, re.IGNORECASE)

    def test_teacher_citation_format_present(self, teacher_prompt):
        """Citation marker format [[Sn]] must be documented in teacher prompt."""
        assert "[[S" in teacher_prompt

    def test_teacher_citation_example_present(self, teacher_prompt):
        assert "[[S1]]" in teacher_prompt

    def test_teacher_citation_inline_instruction(self, teacher_prompt):
        assert "inline citation" in teacher_prompt.lower() or "citation" in teacher_prompt.lower()

    def test_teacher_only_cite_from_retrieved_docs(self, teacher_prompt):
        assert "retrieved document" in teacher_prompt.lower()

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_teacher_mentions_each_tool(self, teacher_prompt, tool_name):
        assert tool_name in teacher_prompt, (
            f"Tool '{tool_name}' not mentioned in TEACHER_SYSTEM"
        )

    def test_teacher_eight_tools_header_present(self, teacher_prompt):
        assert "eight tools" in teacher_prompt.lower()

    def test_teacher_list_products_guidance(self, teacher_prompt):
        assert "list_products" in teacher_prompt
        assert "first" in teacher_prompt  # instruct to call list_products first

    def test_teacher_hospital_network_use_case(self, teacher_prompt):
        assert "lookup_hospital_network" in teacher_prompt
        # Should mention hospital-related question
        assert "hospital" in teacher_prompt.lower()

    def test_teacher_compare_plans_attributes(self, teacher_prompt):
        """compare_plans description should mention plan attributes."""
        assert "compare_plans" in teacher_prompt
        attributes_keywords = ["deductible", "annual limit", "room"]
        assert any(kw in teacher_prompt.lower() for kw in attributes_keywords)

    def test_teacher_lookup_exclusions_use_case(self, teacher_prompt):
        assert "lookup_exclusions" in teacher_prompt
        assert "pre-existing" in teacher_prompt.lower() or "exclusion" in teacher_prompt.lower()

    def test_teacher_search_claim_procedure_use_case(self, teacher_prompt):
        assert "search_claim_procedure" in teacher_prompt
        assert "claim" in teacher_prompt.lower()

    def test_teacher_search_all_description(self, teacher_prompt):
        assert "search_all" in teacher_prompt
        assert "general" in teacher_prompt.lower() or "unfiltered" in teacher_prompt.lower() \
               or "across all" in teacher_prompt.lower()

    def test_teacher_premium_band_warning(self, teacher_prompt):
        assert "premium band" in teacher_prompt.lower() or "wrong premium" in teacher_prompt.lower()

    def test_teacher_policy_inception_mentioned(self, teacher_prompt):
        assert "policy inception" in teacher_prompt.lower()

    def test_teacher_age_miscalculation_warning(self, teacher_prompt):
        """Prompt must warn about age miscalculation consequences."""
        assert "miscalculation" in teacher_prompt.lower() or \
               "age" in teacher_prompt.lower()


# ===========================================================================
# 3. ASSESSOR_SYSTEM content tests
# ===========================================================================


class TestAssessorSystemContent:

    def test_assessor_role_description_present(self, assessor_prompt):
        assert "accuracy assessment" in assessor_prompt.lower() or \
               "assessment" in assessor_prompt.lower()

    def test_assessor_roleplay_context(self, assessor_prompt):
        assert "roleplay" in assessor_prompt.lower()

    def test_assessor_profile_placeholder(self, assessor_prompt):
        assert "{profile}" in assessor_prompt

    def test_assessor_conversation_placeholder(self, assessor_prompt):
        assert "{conversation}" in assessor_prompt

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_assessor_mentions_each_tool(self, assessor_prompt, tool_name):
        assert tool_name in assessor_prompt, (
            f"Tool '{tool_name}' not mentioned in ASSESSOR_SYSTEM"
        )

    def test_assessor_eight_tools_mentioned(self, assessor_prompt):
        assert "eight tools" in assessor_prompt.lower()

    def test_assessor_product_knowledge_dimension(self, assessor_prompt):
        assert "Product Knowledge" in assessor_prompt or "product knowledge" in assessor_prompt.lower()

    def test_assessor_must_use_search_tools(self, assessor_prompt):
        assert "search tools" in assessor_prompt.lower() or "use your search" in assessor_prompt.lower()

    def test_assessor_do_not_rely_on_memory(self, assessor_prompt):
        assert "do not rely on memory" in assessor_prompt.lower() or \
               "not rely on memory" in assessor_prompt.lower()

    def test_assessor_five_dimensions(self, assessor_prompt):
        """Assessment must cover five dimensions."""
        assert "five dimensions" in assessor_prompt.lower()

    def test_assessor_first_impression_dimension(self, assessor_prompt):
        assert "First Impression" in assessor_prompt

    def test_assessor_needs_discovery_dimension(self, assessor_prompt):
        assert "Needs Discovery" in assessor_prompt

    def test_assessor_objection_handling_dimension(self, assessor_prompt):
        assert "Objection Handling" in assessor_prompt

    def test_assessor_closing_technique_dimension(self, assessor_prompt):
        assert "Closing Technique" in assessor_prompt

    def test_assessor_overall_score_format(self, assessor_prompt):
        """Format must include Overall Score heading."""
        assert "## Overall Score:" in assessor_prompt or "Overall Score" in assessor_prompt

    def test_assessor_correct_mark(self, assessor_prompt):
        assert "✓ Correct" in assessor_prompt or "Correct" in assessor_prompt

    def test_assessor_incorrect_mark(self, assessor_prompt):
        assert "✗ Incorrect" in assessor_prompt or "Incorrect" in assessor_prompt

    def test_assessor_partially_correct_mark(self, assessor_prompt):
        assert "Partially correct" in assessor_prompt or "⚠" in assessor_prompt

    def test_assessor_key_strengths_section(self, assessor_prompt):
        assert "Key Strengths" in assessor_prompt or "strengths" in assessor_prompt.lower()

    def test_assessor_areas_to_improve_section(self, assessor_prompt):
        assert "Areas to Improve" in assessor_prompt or "improve" in assessor_prompt.lower()

    def test_assessor_age_last_birthday_instruction(self, assessor_prompt):
        assert "Age Last Birthday" in assessor_prompt or "ALB" in assessor_prompt

    def test_assessor_get_current_date_first_instruction(self, assessor_prompt):
        assert "get_current_date" in assessor_prompt
        assert re.search(r"call\s+get_current_date\s+first", assessor_prompt, re.IGNORECASE)

    def test_assessor_flag_outdated_age_error(self, assessor_prompt):
        """Prompt must instruct to flag outdated age as an error."""
        assert "flag" in assessor_prompt.lower()
        assert "error" in assessor_prompt.lower()

    def test_assessor_workflow_numbered_steps(self, assessor_prompt):
        """Assessor workflow must have numbered steps."""
        assert re.search(r"1\.", assessor_prompt)
        assert re.search(r"2\.", assessor_prompt)
        assert re.search(r"3\.", assessor_prompt)

    def test_assessor_list_products_first_guidance(self, assessor_prompt):
        """Should instruct to use list_products first if product name uncertain."""
        assert "list_products" in assessor_prompt
        assert "first" in assessor_prompt.lower()

    def test_assessor_identify_factual_claims(self, assessor_prompt):
        assert "factual claim" in assessor_prompt.lower() or "claim" in assessor_prompt.lower()

    def test_assessor_coverage_amounts_mentioned(self, assessor_