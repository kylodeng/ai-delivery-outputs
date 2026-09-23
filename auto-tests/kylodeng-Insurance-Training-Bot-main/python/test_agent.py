"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content, format, citations instructions
- ASSESSOR_SYSTEM prompt string: presence, content, format, placeholders
- Module-level constants: existence and type
- Prompt content correctness: tool names, key instructions, formatting rules
- Edge cases: placeholder formatting for ASSESSOR_SYSTEM, tool list completeness
- String boundary/content checks for both system prompts

Mocks used:
- unittest.mock.patch for `langchain.agents.create_agent` to avoid real LangChain calls
- No external services are called at import time; all tests operate on string constants

TODOs:
- TODO: Test actual agent graph execution (teacher_agent, assessor_agent) once
  the full LangGraph wiring (nodes, edges, StateGraph) is available in the module.
- TODO: Test astream_events streaming behaviour for teacher agent — requires
  a running LangGraph runtime and mocked LLM/tool responses.
- TODO: Test ainvoke for assessor agent — requires mocked LLM + tool layer.
- TODO: Test RAG tool integration (get_current_date, list_products, search_product,
  search_all, lookup_hospital_network, compare_plans, lookup_exclusions,
  search_claim_procedure) — requires tool definitions to be exported from the module.
- TODO: Test that create_agent is called with the correct system prompts and tools
  once the agent factory functions are exported.
- TODO: Verify citation format [[Sn]] is injected correctly into streamed responses
  — requires LLM mock returning synthetic tool results.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / module loading
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


def _load_agent_module():
    """Import api.agent with langchain stubbed out to avoid real network calls."""
    # Stub the langchain.agents module so create_agent doesn't fail
    langchain_stub = types.ModuleType("langchain")
    langchain_agents_stub = types.ModuleType("langchain.agents")
    langchain_agents_stub.create_agent = MagicMock(return_value=MagicMock())
    langchain_stub.agents = langchain_agents_stub

    sys.modules.setdefault("langchain", langchain_stub)
    sys.modules.setdefault("langchain.agents", langchain_agents_stub)

    # Force a fresh import each time (safe for parametrize scenarios)
    if "api.agent" in sys.modules:
        del sys.modules["api.agent"]
    if "agent" in sys.modules:
        del sys.modules["agent"]

    spec_names = ["api.agent", "agent"]
    mod = None
    for name in spec_names:
        try:
            mod = importlib.import_module(name)
            break
        except ModuleNotFoundError:
            continue

    if mod is None:
        pytest.skip("Cannot import api.agent — check PYTHONPATH")

    return mod


@pytest.fixture(scope="module")
def agent_mod():
    return _load_agent_module()


@pytest.fixture(scope="module")
def teacher_system(agent_mod):
    return agent_mod.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_mod):
    return agent_mod.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Basic constant existence tests
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_mod):
        assert hasattr(agent_mod, "TEACHER_SYSTEM"), "TEACHER_SYSTEM must be defined"

    def test_assessor_system_exists(self, agent_mod):
        assert hasattr(agent_mod, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM must be defined"

    def test_teacher_system_is_str(self, agent_mod):
        assert isinstance(agent_mod.TEACHER_SYSTEM, str)

    def test_assessor_system_is_str(self, agent_mod):
        assert isinstance(agent_mod.ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self, agent_mod):
        assert len(agent_mod.TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_non_empty(self, agent_mod):
        assert len(agent_mod.ASSESSOR_SYSTEM.strip()) > 0


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestTeacherSystemContent:
    def test_contains_role_description(self, teacher_system):
        assert "insurance sales trainer" in teacher_system.lower()

    def test_contains_eight_tools_header(self, teacher_system):
        assert "eight tools" in teacher_system.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_contains_each_tool_name(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"Tool '{tool_name}' not found in TEACHER_SYSTEM"
        )

    def test_age_last_birthday_instruction(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_get_current_date_called_first_instruction(self, teacher_system):
        lower = teacher_system.lower()
        assert "get_current_date" in lower
        # Instruction to call it first for date-relative calculations
        assert "first" in lower

    def test_citation_format_instruction(self, teacher_system):
        """Citation marker [[Sn]] must be documented."""
        assert "[[S" in teacher_system, (
            "TEACHER_SYSTEM must describe the [[Sn]] citation format"
        )

    def test_citation_example_present(self, teacher_system):
        assert "[[S1]]" in teacher_system, (
            "TEACHER_SYSTEM should include a concrete citation example like [[S1]]"
        )

    def test_never_guess_instruction(self, teacher_system):
        assert "Never guess" in teacher_system or "never guess" in teacher_system

    def test_encouraging_tone_keywords(self, teacher_system):
        lower = teacher_system.lower()
        assert any(
            word in lower for word in ["encouraging", "confidence", "engaging"]
        ), "TEACHER_SYSTEM should encourage an engaging teaching style"

    def test_premium_calculation_warning(self, teacher_system):
        lower = teacher_system.lower()
        assert "premium" in lower

    def test_alb_at_policy_inception(self, teacher_system):
        assert "policy inception" in teacher_system

    def test_lookup_hospital_network_use_case(self, teacher_system):
        assert "lookup_hospital_network" in teacher_system
        # Should mention hospital-related use case
        lower = teacher_system.lower()
        assert "hospital" in lower

    def test_compare_plans_mention(self, teacher_system):
        assert "compare_plans" in teacher_system

    def test_lookup_exclusions_mention(self, teacher_system):
        assert "lookup_exclusions" in teacher_system
        lower = teacher_system.lower()
        # Should mention pre-existing conditions or exclusions
        assert any(
            phrase in lower
            for phrase in ["exclusion", "pre-existing", "waiting period"]
        )

    def test_search_claim_procedure_mention(self, teacher_system):
        assert "search_claim_procedure" in teacher_system
        lower = teacher_system.lower()
        assert "claim" in lower

    def test_search_all_mention(self, teacher_system):
        assert "search_all" in teacher_system

    def test_search_product_mention(self, teacher_system):
        assert "search_product" in teacher_system

    def test_list_products_mention(self, teacher_system):
        assert "list_products" in teacher_system

    def test_does_not_contain_raw_placeholder(self, teacher_system):
        """Teacher system should NOT have unfilled {profile} or {conversation} placeholders."""
        assert "{profile}" not in teacher_system
        assert "{conversation}" not in teacher_system

    def test_minimum_length(self, teacher_system):
        """Sanity check: a meaningful prompt should exceed 500 chars."""
        assert len(teacher_system) > 500

    def test_knowledge_base_reference(self, teacher_system):
        lower = teacher_system.lower()
        assert "knowledge base" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestAssessorSystemContent:
    def test_contains_role_description(self, assessor_system):
        lower = assessor_system.lower()
        assert "insurance sales trainer" in lower

    def test_contains_assessment_task(self, assessor_system):
        lower = assessor_system.lower()
        assert "assess" in lower

    def test_contains_five_dimensions(self, assessor_system):
        lower = assessor_system.lower()
        assert "five dimensions" in lower or "5" in lower

    def test_contains_profile_placeholder(self, assessor_system):
        assert "{profile}" in assessor_system, (
            "ASSESSOR_SYSTEM must contain {profile} placeholder"
        )

    def test_contains_conversation_placeholder(self, assessor_system):
        assert "{conversation}" in assessor_system, (
            "ASSESSOR_SYSTEM must contain {conversation} placeholder"
        )

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_contains_each_tool_name(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"Tool '{tool_name}' not found in ASSESSOR_SYSTEM"
        )

    def test_contains_eight_tools_header(self, assessor_system):
        assert "eight tools" in assessor_system.lower()

    def test_age_last_birthday_instruction(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_alb_at_policy_inception(self, assessor_system):
        assert "policy inception" in assessor_system

    def test_get_current_date_first_instruction(self, assessor_system):
        assert "get_current_date" in assessor_system
        lower = assessor_system.lower()
        assert "first" in lower

    def test_workflow_section_present(self, assessor_system):
        lower = assessor_system.lower()
        assert "workflow" in lower

    def test_output_format_overall_score(self, assessor_system):
        assert "## Overall Score" in assessor_system

    def test_output_format_first_impression(self, assessor_system):
        assert "First Impression" in assessor_system

    def test_output_format_needs_discovery(self, assessor_system):
        assert "Needs Discovery" in assessor_system

    def test_output_format_product_knowledge(self, assessor_system):
        assert "Product Knowledge" in assessor_system

    def test_output_format_objection_handling(self, assessor_system):
        assert "Objection Handling" in assessor_system

    def test_output_format_closing_technique(self, assessor_system):
        assert "Closing Technique" in assessor_system

    def test_correctness_markers_present(self, assessor_system):
        """Assessment format should include ✓ Correct / ✗ Incorrect markers."""
        assert "✓ Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system
        assert "⚠" in assessor_system  # Partially correct

    def test_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system

    def test_areas_to_improve_section(self, assessor_system):
        assert "Areas to Improve" in assessor_system

    def test_do_not_rely_on_memory_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower or "do not rely" in lower

    def test_verify_factual_claims_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "verify" in lower or "factual" in lower

    def test_minimum_length(self, assessor_system):
        assert len(assessor_system) > 500

    def test_lookup_hospital_network_verify_use(self, assessor_system):
        assert "lookup_hospital_network" in assessor_system
        lower = assessor_system.lower()
        assert "hospital" in lower

    def test_compare_plans_verify_use(self, assessor_system):
        assert "compare_plans" in assessor_system
        lower = assessor_system.lower()
        assert "comparison" in lower or "compare" in lower

    def test_lookup_exclusions_verify_use(self, assessor_system):
        assert "lookup_exclusions" in assessor_system

    def test_search_claim_procedure_verify_use(self, assessor_system):
        assert "search_claim_procedure" in assessor_system


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM placeholder formatting tests
# ---------------------------------------------------------------------------


class TestAssessorSystemPlaceholderFormatting:
    def test_format_with_profile_and_conversation(self, assessor_system):
        """str.format() should succeed with both required keys."""
        result = assessor_system.format(
            profile="Test customer profile text",
            conversation="Agent: Hello\nCustomer: Hi",
        )
        assert "Test customer profile text" in result
        assert "Agent: Hello" in result

    def test_format_substitutes_profile(self, assessor_system):
        result = assessor_system.format(
            profile="Age 45, looking for critical illness cover",
            conversation="...",
        )
        assert "Age 45, looking for critical illness cover" in result
        assert "{profile}" not in result

    def test_format_substitutes_conversation(self, assessor_system):
        result = assessor_system.format(
            profile="...",
            conversation="Agent discussed Generations II plan.",
        )
        assert "Agent discussed Generations II plan." in result
        assert "{conversation}" not in result

    def test_format_missing_profile_raises_key_error(self, assessor_system):
        with pytest.raises(KeyError):
            assessor_system.format(conversation="some conversation")

    def test_format_missing_conversation_raises_key_error(self, assessor_system):
        with pytest.raises(KeyError):
            assessor_system.format(profile="some profile")

    def test_format_with_empty_strings(self, assessor_system):
        result = assessor_system.format(profile="", conversation="")
        assert "{profile}" not in result
        assert "{conversation}" not in result

    def test_format_with_special_characters(self, assessor_system):
        profile = "Customer: aged 50, earns HKD 80,000/mo, smoker"
        conversation = 'Agent said: "deductible is HKD 3,000 [[S1]]"'
        result = assessor_system.format(profile=profile, conversation=conversation)
        assert profile in result
        assert conversation in result

    @pytest.mark.parametrize(
        "profile,conversation",
        [
            ("Female, 35, two children", "Agent greeted customer warmly."),
            (
                "Male, 50, pre-existing hypertension",
                "Agent mentioned Generations II whole life plan.",
            ),
            (
                "Couple, both 40s, seeking health rider",
                "Agent compared plans A and B.",
            ),
        ],
    )
    def test_format_parameterised_profiles(self, assessor_system, profile, conversation):
        result = assessor_system.format(profile=profile, conversation=conversation)