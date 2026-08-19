"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content, structure, and key instructions
- ASSESSOR_SYSTEM prompt string: presence, content, structure, placeholders, and key instructions
- Module-level constants and their types
- Tool references within prompt strings (all 8 tools mentioned)
- Citation format instructions in TEACHER_SYSTEM
- Assessment format/dimensions in ASSESSOR_SYSTEM
- Edge cases around placeholder formatting in ASSESSOR_SYSTEM

Mocks used:
- langchain.agents.create_agent is patched to avoid real LangChain instantiation
  (the source imports it but doesn't call it at module level with side effects
   beyond the import itself; we patch to prevent any import-time execution issues)

TODOs:
- TODO: Integration tests for teacher_agent streaming (astream_events) require
        a running LangGraph runtime and real or mocked LLM — stub below
- TODO: Integration tests for assessor_agent one-shot (ainvoke) require
        a running LangGraph runtime and real or mocked LLM — stub below
- TODO: Tests for the actual agent graph construction (nodes, edges, tool binding)
        require the full agent factory functions which are not visible in the
        truncated source — stub below
- TODO: Verify that create_agent is called with correct arguments once agent
        factory functions are available in source
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / module loading
# ---------------------------------------------------------------------------

TOOLS_EXPECTED = [
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


def _load_agent_module():
    """
    Load api.agent with langchain stubbed out so no real network/LLM calls occur.
    Returns the loaded module.
    """
    # Build a minimal stub for langchain.agents
    langchain_stub = types.ModuleType("langchain")
    langchain_agents_stub = types.ModuleType("langchain.agents")
    langchain_agents_stub.create_agent = MagicMock(return_value=MagicMock())
    langchain_stub.agents = langchain_agents_stub

    sys.modules.setdefault("langchain", langchain_stub)
    sys.modules.setdefault("langchain.agents", langchain_agents_stub)

    # Force fresh import each time this helper is called (within a test session
    # the module is cached after the first call, which is fine).
    if "api.agent" in sys.modules:
        return sys.modules["api.agent"]

    with patch.dict(
        sys.modules,
        {
            "langchain": langchain_stub,
            "langchain.agents": langchain_agents_stub,
        },
    ):
        import api.agent as agent_module  # noqa: PLC0415

    sys.modules["api.agent"] = agent_module
    return agent_module


@pytest.fixture(scope="module")
def agent_mod():
    """Module-scoped fixture: import api.agent once with stubs in place."""
    return _load_agent_module()


@pytest.fixture(scope="module")
def teacher_system(agent_mod):
    return agent_mod.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_mod):
    return agent_mod.ASSESSOR_SYSTEM


# ===========================================================================
# 1. Module-level sanity
# ===========================================================================


class TestModuleAttributes:
    def test_teacher_system_exists(self, agent_mod):
        assert hasattr(agent_mod, "TEACHER_SYSTEM"), "TEACHER_SYSTEM must be defined"

    def test_assessor_system_exists(self, agent_mod):
        assert hasattr(agent_mod, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM must be defined"

    def test_teacher_system_is_str(self, agent_mod):
        assert isinstance(agent_mod.TEACHER_SYSTEM, str)

    def test_assessor_system_is_str(self, agent_mod):
        assert isinstance(agent_mod.ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0


# ===========================================================================
# 2. TEACHER_SYSTEM content tests
# ===========================================================================


class TestTeacherSystemContent:
    def test_teacher_identity_phrase(self, teacher_system):
        """The prompt must establish the teacher persona."""
        assert "insurance sales trainer" in teacher_system.lower()

    def test_teacher_mentions_coaching(self, teacher_system):
        assert "coach" in teacher_system.lower()

    def test_teacher_mentions_eight_tools(self, teacher_system):
        assert "eight tools" in teacher_system.lower()

    @pytest.mark.parametrize("tool_name", TOOLS_EXPECTED)
    def test_teacher_lists_all_tools(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"Tool '{tool_name}' is missing from TEACHER_SYSTEM"
        )

    def test_teacher_age_last_birthday_instruction(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_teacher_get_current_date_first_instruction(self, teacher_system):
        """The prompt must instruct the agent to call get_current_date first."""
        lower = teacher_system.lower()
        assert "get_current_date" in lower
        # Check that the instruction implies calling it first / before calculations
        assert "first" in lower

    def test_teacher_citation_format_present(self, teacher_system):
        """Inline citation marker format [[Sn]] must be documented."""
        assert "[[S" in teacher_system, (
            "Citation marker format '[[Sn]]' not found in TEACHER_SYSTEM"
        )

    def test_teacher_citation_example_present(self, teacher_system):
        assert "[[S1]]" in teacher_system

    def test_teacher_never_guess_instruction(self, teacher_system):
        assert "Never guess" in teacher_system or "never guess" in teacher_system

    def test_teacher_discovery_questions(self, teacher_system):
        lower = teacher_system.lower()
        assert "discovery" in lower or "discover" in lower

    def test_teacher_encouraging_tone_mentioned(self, teacher_system):
        lower = teacher_system.lower()
        assert "encouraging" in lower or "confidence" in lower

    def test_teacher_first_impression_mentioned(self, teacher_system):
        lower = teacher_system.lower()
        assert "first" in lower and ("impression" in lower or "call" in lower)

    def test_teacher_premium_calculation_warning(self, teacher_system):
        lower = teacher_system.lower()
        assert "premium" in lower

    def test_teacher_list_products_guidance(self, teacher_system):
        """Should advise calling list_products for 'what products' type questions."""
        assert "list_products" in teacher_system

    def test_teacher_lookup_hospital_guidance(self, teacher_system):
        assert "lookup_hospital_network" in teacher_system

    def test_teacher_compare_plans_guidance(self, teacher_system):
        assert "compare_plans" in teacher_system

    def test_teacher_lookup_exclusions_guidance(self, teacher_system):
        assert "lookup_exclusions" in teacher_system

    def test_teacher_search_claim_procedure_guidance(self, teacher_system):
        assert "search_claim_procedure" in teacher_system

    def test_teacher_tool_before_answering(self, teacher_system):
        """Prompt must instruct to use tools before answering."""
        lower = teacher_system.lower()
        assert "appropriate tool" in lower or "use the appropriate tool" in lower

    def test_teacher_alb_age_miscalculation_warning(self, teacher_system):
        """Explicit warning about age miscalculation leading to wrong premium band."""
        lower = teacher_system.lower()
        assert "miscalcul" in lower or "wrong premium" in lower

    def test_teacher_sessions_engaging(self, teacher_system):
        lower = teacher_system.lower()
        assert "engaging" in lower or "interactive" in lower

    def test_teacher_system_starts_with_role(self, teacher_system):
        """First non-whitespace content should establish the role."""
        stripped = teacher_system.strip()
        assert stripped.startswith("You are"), (
            "TEACHER_SYSTEM should begin with 'You are ...'"
        )

    def test_teacher_no_placeholder_tokens(self, teacher_system):
        """TEACHER_SYSTEM should not contain unfilled {placeholder} tokens."""
        import re

        placeholders = re.findall(r"\{[a-zA-Z_]+\}", teacher_system)
        assert placeholders == [], (
            f"Unexpected placeholders in TEACHER_SYSTEM: {placeholders}"
        )

    def test_teacher_waiting_period_mentioned(self, teacher_system):
        lower = teacher_system.lower()
        assert "waiting period" in lower or "pre-existing" in lower


# ===========================================================================
# 3. ASSESSOR_SYSTEM content tests
# ===========================================================================


class TestAssessorSystemContent:
    def test_assessor_identity_phrase(self, assessor_system):
        assert "insurance sales trainer" in assessor_system.lower()

    def test_assessor_conducting_assessment(self, assessor_system):
        lower = assessor_system.lower()
        assert "assessment" in lower

    def test_assessor_mentions_eight_tools(self, assessor_system):
        assert "eight tools" in assessor_system.lower()

    @pytest.mark.parametrize("tool_name", TOOLS_EXPECTED)
    def test_assessor_lists_all_tools(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"Tool '{tool_name}' is missing from ASSESSOR_SYSTEM"
        )

    @pytest.mark.parametrize("dimension", ASSESSOR_DIMENSIONS)
    def test_assessor_includes_all_dimensions(self, assessor_system, dimension):
        assert dimension in assessor_system, (
            f"Assessment dimension '{dimension}' not found in ASSESSOR_SYSTEM"
        )

    def test_assessor_profile_placeholder(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_assessor_conversation_placeholder(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_assessor_only_expected_placeholders(self, assessor_system):
        """Only {profile} and {conversation} should be template placeholders."""
        import re

        placeholders = set(re.findall(r"\{([a-zA-Z_]+)\}", assessor_system))
        allowed = {"profile", "conversation"}
        unexpected = placeholders - allowed
        assert unexpected == set(), (
            f"Unexpected placeholders in ASSESSOR_SYSTEM: {unexpected}"
        )

    def test_assessor_overall_score_format(self, assessor_system):
        assert "## Overall Score" in assessor_system

    def test_assessor_score_out_of_10(self, assessor_system):
        assert "X/10" in assessor_system

    def test_assessor_workflow_steps(self, assessor_system):
        """Workflow instructions (numbered steps) must be present."""
        assert "1." in assessor_system and "2." in assessor_system and "3." in assessor_system

    def test_assessor_verify_claims_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "verify" in lower

    def test_assessor_do_not_rely_on_memory(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower

    def test_assessor_age_last_birthday_instruction(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_assessor_get_current_date_first(self, assessor_system):
        lower = assessor_system.lower()
        assert "get_current_date" in lower

    def test_assessor_correct_incorrect_markers(self, assessor_system):
        """The rubric must include correct/incorrect judgment markers."""
        assert "✓ Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system

    def test_assessor_partial_correct_marker(self, assessor_system):
        assert "⚠ Partially correct" in assessor_system or "Partially correct" in assessor_system

    def test_assessor_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system or "✅ Key Strengths" in assessor_system

    def test_assessor_areas_to_improve_section(self, assessor_system):
        assert "Areas to Improve" in assessor_system or "⚠️ Areas to Improve" in assessor_system

    def test_assessor_five_dimensions_count(self, assessor_system):
        assert len(ASSESSOR_DIMENSIONS) == 5

    def test_assessor_product_knowledge_accuracy_requires_tools(self, assessor_system):
        lower = assessor_system.lower()
        assert "product knowledge" in lower
        assert "search tools" in lower or "tool" in lower

    def test_assessor_roleplay_mention(self, assessor_system):
        lower = assessor_system.lower()
        assert "roleplay" in lower

    def test_assessor_starts_with_role(self, assessor_system):
        stripped = assessor_system.strip()
        assert stripped.startswith("You are"), (
            "ASSESSOR_SYSTEM should begin with 'You are ...'"
        )

    def test_assessor_trainee_agent_reference(self, assessor_system):
        lower = assessor_system.lower()
        assert "trainee" in lower

    def test_assessor_list_products_fallback_instruction(self, assessor_system):
        """Should instruct to use list_products when product name is uncertain."""
        assert "list_products" in assessor_system
        lower = assessor_system.lower()
        assert "unsure" in lower or "not sure" in lower or "exact product name" in lower

    def test_assessor_premium_flag_outdated_age(self, assessor_system):
        lower = assessor_system.lower()
        assert "flag" in lower or "error" in lower


# ===========================================================================
# 4. ASSESSOR_SYSTEM placeholder formatting
# ===========================================================================


class TestAssessorPlaceholderFormatting:
    SAMPLE_PROFILE = (
        "Client: Jane Doe, age 35, looking for health insurance "
        "covering hospital stays in mainland China."
    )

    SAMPLE_CONVERSATION = (
        "Agent: Hi Jane, I'd like to recommend our EliteCare plan...\n"
        "Customer: What hospitals are covered in Shenzhen?\n"
        "Agent: All Class 3 hospitals in the Greater Bay Area are covered."
    )

    def test_format_with_valid_inputs(self, assessor_system):
        result = assessor_system.format(
            profile=self.SAMPLE_PROFILE,
            conversation=self.SAMPLE_CONVERSATION,
        )
        assert self.SAMPLE_PROFILE in result
        assert self.SAMPLE_CONVERSATION in result

    def test_format_profile_replaces_placeholder(self, assessor_system):
        result = assessor_system.format(
            profile=self.SAMPLE_PROFILE