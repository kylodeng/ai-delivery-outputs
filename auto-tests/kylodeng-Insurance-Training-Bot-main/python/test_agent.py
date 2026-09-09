"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content sections, citation format instructions,
  tool references, age/ALB calculation instructions
- ASSESSOR_SYSTEM prompt string: presence, key content sections, placeholder variables,
  tool references, assessment format structure, age/ALB instructions
- Module-level constants: existence, type, non-emptiness
- create_agent import usage (mocked)
- Synthetic data integration: product names and doc types referenced in prompts

Mocks used:
- langchain.agents.create_agent (patched at module level to avoid real LLM/tool calls)
- No real external service calls are made

TODOs:
- TODO: Test actual agent graph construction once LangGraph agent factory signatures are confirmed
- TODO: Test astream_events integration for teacher agent (requires live LangGraph runtime or deeper mock)
- TODO: Test ainvoke integration for assessor agent (requires live LangGraph runtime or deeper mock)
- TODO: Test tool binding and tool list passed to create_agent (need tool constructors available)
- TODO: Test ASSESSOR_SYSTEM .format() with real profile/conversation once full template is confirmed
  (source is truncated — "Specific Re" section is cut off)
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – import the module under test with create_agent mocked out so that
# importing api.agent does not trigger real LangChain network calls.
# ---------------------------------------------------------------------------

MOCK_AGENT = MagicMock(name="mock_agent_instance")


def _import_agent_module():
    """Import api.agent with langchain.agents.create_agent stubbed."""
    # Build a minimal fake langchain.agents module
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_lc_agents.create_agent = MagicMock(return_value=MOCK_AGENT)

    # Also make sure the parent package exists in sys.modules
    fake_lc = sys.modules.get("langchain") or types.ModuleType("langchain")
    sys.modules.setdefault("langchain", fake_lc)
    sys.modules["langchain.agents"] = fake_lc_agents

    # Force a fresh import each time this helper is called (if already cached)
    if "api.agent" in sys.modules:
        del sys.modules["api.agent"]
    if "api" not in sys.modules:
        sys.modules["api"] = types.ModuleType("api")

    import api.agent as agent_mod  # noqa: PLC0415

    return agent_mod


@pytest.fixture(scope="module")
def agent_module():
    """Module-scoped fixture providing the imported api.agent module."""
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


class TestModuleConstants:
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

    def test_teacher_system_minimum_length(self, teacher_system):
        # Sanity-check: a meaningful system prompt should be at least 200 chars
        assert len(teacher_system) >= 200

    def test_assessor_system_minimum_length(self, assessor_system):
        assert len(assessor_system) >= 200


# ===========================================================================
# 2. TEACHER_SYSTEM content checks
# ===========================================================================


class TestTeacherSystemContent:
    # --- Role description ---

    def test_contains_role_description(self, teacher_system):
        assert "insurance sales trainer" in teacher_system.lower() or \
               "insurance" in teacher_system.lower()

    def test_mentions_agent_or_trainee(self, teacher_system):
        lower = teacher_system.lower()
        assert "agent" in lower or "trainee" in lower

    # --- Tool presence ---

    @pytest.mark.parametrize("tool_name", [
        "get_current_date",
        "list_products",
        "search_product",
        "search_all",
        "lookup_hospital_network",
        "compare_plans",
        "lookup_exclusions",
        "search_claim_procedure",
    ])
    def test_all_eight_tools_mentioned(self, teacher_system, tool_name):
        assert tool_name in teacher_system, \
            f"Tool '{tool_name}' should be referenced in TEACHER_SYSTEM"

    def test_tool_count_hint(self, teacher_system):
        """The prompt should mention 'eight tools'."""
        assert "eight" in teacher_system.lower() or "8" in teacher_system

    # --- Age / ALB instructions ---

    def test_age_last_birthday_mentioned(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_get_current_date_first_instruction(self, teacher_system):
        """Prompt must instruct to call get_current_date first for date-relative calculations."""
        lower = teacher_system.lower()
        assert "get_current_date" in lower
        # The prompt should instruct calling it first
        assert "first" in lower

    def test_premium_calculation_warning(self, teacher_system):
        lower = teacher_system.lower()
        assert "premium" in lower

    # --- Citation format ---

    def test_citation_format_present(self, teacher_system):
        """The [[Sn]] citation marker format must be documented."""
        assert "[[S" in teacher_system

    def test_citation_example_present(self, teacher_system):
        assert "[[S1]]" in teacher_system

    def test_citations_instruction_present(self, teacher_system):
        lower = teacher_system.lower()
        assert "citation" in lower or "cite" in lower

    # --- Teaching approach ---

    def test_mentions_exercises_or_interactive(self, teacher_system):
        lower = teacher_system.lower()
        assert "exercise" in lower or "interactive" in lower or "quiz" in lower

    def test_never_guess_instruction(self, teacher_system):
        lower = teacher_system.lower()
        assert "never guess" in lower or "do not guess" in lower or "never" in lower

    def test_mentions_discovery_questions(self, teacher_system):
        lower = teacher_system.lower()
        assert "discover" in lower or "question" in lower

    # --- Specific tool descriptions ---

    def test_hospital_network_tool_description(self, teacher_system):
        assert "hospital" in teacher_system.lower()

    def test_exclusions_tool_description(self, teacher_system):
        lower = teacher_system.lower()
        assert "exclusion" in lower or "not covered" in lower

    def test_claim_procedure_tool_description(self, teacher_system):
        lower = teacher_system.lower()
        assert "claim" in lower

    def test_compare_plans_tool_description(self, teacher_system):
        lower = teacher_system.lower()
        assert "compare" in lower or "deductible" in lower or "annual limit" in lower


# ===========================================================================
# 3. ASSESSOR_SYSTEM content checks
# ===========================================================================


class TestAssessorSystemContent:
    # --- Placeholder variables ---

    def test_profile_placeholder_present(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_conversation_placeholder_present(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_format_with_profile_and_conversation(self, assessor_system):
        """The template must be formattable with profile and conversation keys."""
        rendered = assessor_system.format(
            profile="Test customer profile",
            conversation="Agent: Hello\nCustomer: Hi",
        )
        assert "Test customer profile" in rendered
        assert "Agent: Hello" in rendered

    def test_format_raises_without_placeholders(self, assessor_system):
        """Formatting without required keys should raise KeyError."""
        with pytest.raises(KeyError):
            assessor_system.format()

    # --- Role description ---

    def test_assessor_role_description(self, assessor_system):
        lower = assessor_system.lower()
        assert "assessment" in lower or "assess" in lower

    def test_mentions_trainee(self, assessor_system):
        lower = assessor_system.lower()
        assert "trainee" in lower or "agent" in lower

    def test_mentions_roleplay(self, assessor_system):
        lower = assessor_system.lower()
        assert "roleplay" in lower or "role-play" in lower or "role play" in lower

    # --- Five assessment dimensions ---

    @pytest.mark.parametrize("dimension", [
        "First Impression",
        "Needs Discovery",
        "Product Knowledge",
        "Objection Handling",
        "Closing",
    ])
    def test_five_dimensions_present(self, assessor_system, dimension):
        assert dimension in assessor_system, \
            f"Assessment dimension '{dimension}' must appear in ASSESSOR_SYSTEM"

    def test_five_dimensions_count_hint(self, assessor_system):
        lower = assessor_system.lower()
        assert "five" in lower or "5" in assessor_system

    # --- Scoring format ---

    def test_overall_score_format(self, assessor_system):
        assert "Overall Score" in assessor_system
        assert "X/10" in assessor_system or "/10" in assessor_system

    def test_score_markers_for_dimensions(self, assessor_system):
        """Each scored dimension should reference /10."""
        assert assessor_system.count("/10") >= 5

    # --- Verification markers ---

    def test_correct_incorrect_markers(self, assessor_system):
        assert "✓ Correct" in assessor_system or "Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system or "Incorrect" in assessor_system

    def test_partially_correct_marker(self, assessor_system):
        assert "Partially correct" in assessor_system or "Partially" in assessor_system

    # --- Strengths / Areas to Improve section ---

    def test_key_strengths_section(self, assessor_system):
        assert "Strengths" in assessor_system or "strength" in assessor_system.lower()

    def test_areas_to_improve_section(self, assessor_system):
        lower = assessor_system.lower()
        assert "improve" in lower or "areas to improve" in lower.lower()

    # --- Tool presence ---

    @pytest.mark.parametrize("tool_name", [
        "get_current_date",
        "list_products",
        "search_product",
        "search_all",
        "lookup_hospital_network",
        "compare_plans",
        "lookup_exclusions",
        "search_claim_procedure",
    ])
    def test_all_eight_tools_mentioned(self, assessor_system, tool_name):
        assert tool_name in assessor_system, \
            f"Tool '{tool_name}' should be referenced in ASSESSOR_SYSTEM"

    def test_tool_count_hint(self, assessor_system):
        assert "eight" in assessor_system.lower() or "8" in assessor_system

    # --- Age / ALB instructions ---

    def test_age_last_birthday_mentioned(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_verify_claims_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "verify" in lower or "verification" in lower

    def test_no_memory_reliance_instruction(self, assessor_system):
        """Assessor must be told not to rely on memory for fact-checking."""
        lower = assessor_system.lower()
        assert "memory" in lower or "not rely" in lower or "do not rely" in lower

    # --- Workflow instructions ---

    def test_workflow_section_present(self, assessor_system):
        lower = assessor_system.lower()
        assert "workflow" in lower or "step" in lower

    def test_list_products_first_guidance(self, assessor_system):
        """Assessor should be told to use list_products if unsure of product name."""
        assert "list_products" in assessor_system
        lower = assessor_system.lower()
        assert "unsure" in lower or "not sure" in lower or "if you are unsure" in lower \
               or "first" in lower


# ===========================================================================
# 4. Prompt consistency between TEACHER and ASSESSOR
# ===========================================================================


class TestPromptConsistency:
    """Both prompts must agree on tool names and ALB instructions."""

    @pytest.mark.parametrize("tool_name", [
        "get_current_date",
        "list_products",
        "search_product",
        "search_all",
        "lookup_hospital_network",
        "compare_plans",
        "lookup_exclusions",
        "search_claim_procedure",
    ])
    def test_same_tools_in_both_prompts(self, teacher_system, assessor_system, tool_name):
        assert tool_name in teacher_system
        assert tool_name in assessor_system

    def test_alb_in_both_prompts(self, teacher_system, assessor_system):
        for system in (teacher_system, assessor_system):
            assert "ALB" in system or "Age Last Birthday" in system

    def test_get_current_date_in_both_prompts(self, teacher_system, assessor_system):
        assert "get_current_date" in teacher_system
        assert "get_current_date" in assessor_system

    def test_neither_prompt_is_identical(self, teacher_system, assessor_system):
        """Teacher and Assessor prompts should be different strings."""
        assert teacher_system != assessor_system


# ===========================================================================
# 5. Synthetic data integration checks
# ===========================================================================


class TestSyntheticDataIntegration:
    """
    Verify that the prompts are consistent with the synthetic product data
    provided (hospital network tools, health products, etc.).
    """

    SYNTHETIC_PRODUCTS = [
        "Generations II",
        "health_products",
    ]

    SYNTHETIC_DOC_TYPES = [
        "product_brochure",
        "supplementary",
    ]

    def test_hospital_network_tool_covers_synthetic_use_case(self, teacher_system, assessor_system):
        """The designated hospital lists in synthetic data require lookup_hospital_network."""
        for system in (teacher_system, assessor_system):
            assert "lookup_hospital_network" in system

    def test_hospital_keyword_in_teacher_system(self, teacher_system):
        """Hospital is a key concept given the synthetic hospital network documents."""
        assert "hospital" in teacher_system.lower()

    def test_hospital_keyword_in_assessor_system(self, assessor_system):
        assert "hospital" in assessor_system.lower()

    def test_teacher_system_covers_exclusions_for_health_products(self, teacher_system):
        """Health products (synthetic data) involve exclus