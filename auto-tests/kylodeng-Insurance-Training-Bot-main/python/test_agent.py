"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content, formatting requirements
- ASSESSOR_SYSTEM prompt string: presence, content, formatting, placeholder variables
- Module-level constants and docstring
- Integration points: create_agent import, system prompt structure for both agents
- Parameterised checks on required tool names in both system prompts
- Parameterised checks on required sections in ASSESSOR_SYSTEM output format
- Edge cases: placeholder substitution, citation format, tool count accuracy

Mocks used:
- unittest.mock.patch for `langchain.agents.create_agent` (external dependency)
- No real LLM or RAG calls are made

TODOs:
- TODO: Test actual agent invocation (requires LLM + tool fixtures)
- TODO: Test astream_events streaming behaviour (requires async LLM mock)
- TODO: Test ainvoke one-shot behaviour for assessor (requires async LLM mock)
- TODO: Test RAG tool integration (requires vector store / retriever mock)
- TODO: Test ASSESSOR_SYSTEM with real profile/conversation substitution end-to-end
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while mocking heavy dependencies
# ---------------------------------------------------------------------------

LANGCHAIN_STUB = types.ModuleType("langchain")
LANGCHAIN_AGENTS_STUB = types.ModuleType("langchain.agents")
LANGCHAIN_AGENTS_STUB.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
LANGCHAIN_STUB.agents = LANGCHAIN_AGENTS_STUB


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with langchain stubbed out."""
    sys.modules.setdefault("langchain", LANGCHAIN_STUB)
    sys.modules.setdefault("langchain.agents", LANGCHAIN_AGENTS_STUB)

    # Force a fresh import
    if "api.agent" in sys.modules:
        del sys.modules["api.agent"]
    if "agent" in sys.modules:
        del sys.modules["agent"]

    with patch.dict(
        sys.modules,
        {
            "langchain": LANGCHAIN_STUB,
            "langchain.agents": LANGCHAIN_AGENTS_STUB,
        },
    ):
        import importlib.util, pathlib, os

        spec = importlib.util.spec_from_file_location(
            "agent", pathlib.Path(__file__).parent.parent / "api" / "agent.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures derived from the agent module
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Required tool names (both prompts must mention all eight)
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


# ===========================================================================
# MODULE-LEVEL SMOKE TESTS
# ===========================================================================


class TestModuleImport:
    def test_module_loads_without_error(self, agent_module):
        assert agent_module is not None

    def test_module_has_teacher_system(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_module_has_assessor_system(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_string(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_string(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_create_agent_imported(self, agent_module):
        """create_agent should be importable from the module's namespace."""
        assert hasattr(agent_module, "create_agent")


# ===========================================================================
# TEACHER_SYSTEM TESTS
# ===========================================================================


class TestTeacherSystemPrompt:
    def test_not_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_mentions_teacher_role(self, teacher_system):
        assert "trainer" in teacher_system.lower() or "coach" in teacher_system.lower()

    def test_mentions_insurance(self, teacher_system):
        assert "insurance" in teacher_system.lower()

    def test_mentions_eight_tools_header(self, teacher_system):
        assert "eight tools" in teacher_system.lower()

    def test_citation_format_documented(self, teacher_system):
        """The [[Sn]] citation marker format must be explained."""
        assert "[[S" in teacher_system

    def test_citation_example_present(self, teacher_system):
        assert "[[S1]]" in teacher_system

    def test_never_guess_instruction(self, teacher_system):
        assert "never guess" in teacher_system.lower()

    def test_age_last_birthday_mentioned(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_get_current_date_priority(self, teacher_system):
        """Prompt must instruct calling get_current_date first for date questions."""
        lower = teacher_system.lower()
        assert "get_current_date" in lower
        # Instruction to call it first
        assert "first" in lower

    def test_encouragement_tone(self, teacher_system):
        lower = teacher_system.lower()
        assert any(
            word in lower for word in ["encouraging", "confidence", "engaging"]
        )

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_tool_mentioned(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"Tool '{tool_name}' not found in TEACHER_SYSTEM"
        )

    def test_tool_descriptions_present(self, teacher_system):
        """Each tool should have at least a brief description after the dash."""
        assert "— " in teacher_system or "- " in teacher_system

    def test_hospital_network_context(self, teacher_system):
        assert "hospital" in teacher_system.lower()

    def test_compare_plans_context(self, teacher_system):
        lower = teacher_system.lower()
        assert "deductible" in lower or "compare" in lower

    def test_lookup_exclusions_context(self, teacher_system):
        lower = teacher_system.lower()
        assert "exclusion" in lower or "not covered" in lower

    def test_search_claim_procedure_context(self, teacher_system):
        lower = teacher_system.lower()
        assert "claim" in lower

    def test_does_not_contain_placeholder_braces(self, teacher_system):
        """Teacher prompt is a static string — no {profile} or {conversation}."""
        assert "{profile}" not in teacher_system
        assert "{conversation}" not in teacher_system

    def test_premium_calculation_warning(self, teacher_system):
        lower = teacher_system.lower()
        assert "premium" in lower

    def test_policy_inception_mentioned(self, teacher_system):
        assert "inception" in teacher_system.lower()

    def test_alb_example_present(self, teacher_system):
        """The January 2020 / age 50 example must appear."""
        assert "50" in teacher_system and "2020" in teacher_system


# ===========================================================================
# ASSESSOR_SYSTEM TESTS
# ===========================================================================


class TestAssessorSystemPrompt:
    def test_not_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0

    def test_has_profile_placeholder(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_has_conversation_placeholder(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_placeholder_substitution_profile(self, assessor_system):
        result = assessor_system.format(
            profile="Test customer profile", conversation="Agent: Hello."
        )
        assert "Test customer profile" in result

    def test_placeholder_substitution_conversation(self, assessor_system):
        result = assessor_system.format(
            profile="Test profile", conversation="Agent: Hello. Customer: Hi."
        )
        assert "Agent: Hello. Customer: Hi." in result

    def test_placeholder_substitution_no_leftover_braces(self, assessor_system):
        result = assessor_system.format(
            profile="Profile A", conversation="Conv B"
        )
        # After substitution no unformatted placeholders should remain
        assert "{profile}" not in result
        assert "{conversation}" not in result

    def test_mentions_assessor_role(self, assessor_system):
        lower = assessor_system.lower()
        assert "assessment" in lower or "assessor" in lower

    def test_mentions_five_dimensions(self, assessor_system):
        lower = assessor_system.lower()
        assert "five dimensions" in lower or "five" in lower

    def test_overall_score_format(self, assessor_system):
        assert "## Overall Score:" in assessor_system or "Overall Score" in assessor_system

    def test_first_impression_section(self, assessor_system):
        assert "First Impression" in assessor_system

    def test_needs_discovery_section(self, assessor_system):
        assert "Needs Discovery" in assessor_system

    def test_product_knowledge_section(self, assessor_system):
        assert "Product Knowledge" in assessor_system

    def test_objection_handling_section(self, assessor_system):
        assert "Objection Handling" in assessor_system

    def test_closing_technique_section(self, assessor_system):
        assert "Closing Technique" in assessor_system

    def test_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system or "✅" in assessor_system

    def test_areas_to_improve_section(self, assessor_system):
        assert "Areas to Improve" in assessor_system or "⚠️" in assessor_system

    def test_accuracy_markers_present(self, assessor_system):
        """The ✓ Correct / ✗ Incorrect / ⚠ Partially correct markers must appear."""
        assert "✓" in assessor_system or "Correct" in assessor_system
        assert "✗" in assessor_system or "Incorrect" in assessor_system
        assert "Partially correct" in assessor_system or "⚠" in assessor_system

    def test_must_use_search_tools(self, assessor_system):
        lower = assessor_system.lower()
        assert "search" in lower or "tool" in lower

    def test_do_not_rely_on_memory(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower or "do not rely" in lower

    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_tool_mentioned(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"Tool '{tool_name}' not found in ASSESSOR_SYSTEM"
        )

    def test_age_last_birthday_mentioned(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_alb_policy_inception_context(self, assessor_system):
        assert "inception" in assessor_system.lower()

    def test_premium_verification_mentioned(self, assessor_system):
        lower = assessor_system.lower()
        assert "premium" in lower

    def test_get_current_date_first_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "get_current_date" in lower

    def test_workflow_steps_present(self, assessor_system):
        lower = assessor_system.lower()
        assert "workflow" in lower or "step" in lower

    def test_list_products_first_guidance(self, assessor_system):
        lower = assessor_system.lower()
        assert "list_products" in lower

    def test_x_out_of_ten_score_format(self, assessor_system):
        assert "X/10" in assessor_system

    def test_mentions_trainee(self, assessor_system):
        lower = assessor_system.lower()
        assert "trainee" in lower or "agent" in lower

    def test_mentions_roleplay(self, assessor_system):
        lower = assessor_system.lower()
        assert "roleplay" in lower

    def test_mentions_hospital_network_verification(self, assessor_system):
        lower = assessor_system.lower()
        assert "hospital" in lower

    def test_mentions_exclusion_verification(self, assessor_system):
        lower = assessor_system.lower()
        assert "exclusion" in lower or "restriction" in lower

    def test_mentions_claim_procedure_verification(self, assessor_system):
        lower = assessor_system.lower()
        assert "claim" in lower


# ===========================================================================
# PARAMETRISED CROSS-PROMPT TESTS
# ===========================================================================


@pytest.mark.parametrize(
    "prompt_fixture, prompt_label",
    [
        ("teacher_system", "TEACHER_SYSTEM"),
        ("assessor_system", "ASSESSOR_SYSTEM"),
    ],
)
class TestBothPrompts:
    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_all_tools_present(self, request, prompt_fixture, prompt_label, tool_name):
        prompt = request.getfixturevalue(prompt_fixture)
        assert tool_name in prompt, (
            f"Tool '{tool_name}' missing from {prompt_label}"
        )

    def test_minimum_length(self, request, prompt_fixture, prompt_label):
        prompt = request.getfixturevalue(prompt_fixture)
        assert len(prompt) >= 200, (
            f"{prompt_label} is suspiciously short ({len(prompt)} chars)"
        )

    def test_no_trailing_null_bytes(self, request, prompt_fixture, prompt_label):
        prompt = request.getfixturevalue(prompt_fixture)
        assert "\x00" not in prompt

    def test_age_last_birthday_in_both(self, request, prompt_fixture, prompt_label):
        prompt = request.getfixturevalue(prompt_fixture)
        assert "Age Last Birthday" in prompt or "ALB" in prompt, (
            f"ALB guidance missing from {prompt_label}"
        )

    def test_get_current_date_in_both(self, request, prompt_fixture, prompt_label):
        prompt = request.getfixturevalue(prompt_fixture)
        assert "get_current_date" in prompt, (
            f"get_current_date missing from {prompt_label}"
        )

    def test_insurance_mentioned(self, request, prompt_fixture, prompt_label):
        prompt = request.getfixturevalue(prompt_fixture)
        assert "insurance" in prompt.lower(), (
            f"'insurance' not found in {prompt_label}"
        )


# ===========================================================================
# SYNTHETIC DATA INTEGRATION TESTS
# ===========================================================================


class TestSyntheticDataIntegration:
    """Verify ASSESSOR_SYSTEM handles realistic profile/conversation substitution."""

    SAMPLE_PROFILES = [
        "Customer: Alice, 35 years old, looking for whole life cover.",