"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content, key instructions, citation format
- ASSESSOR_SYSTEM prompt string: presence, content, placeholders, workflow instructions
- Module-level constants: correct types, non-empty
- String formatting of ASSESSOR_SYSTEM with synthetic profile/conversation data
- Boundary and edge cases for prompt formatting
- Tool name consistency between TEACHER_SYSTEM and ASSESSOR_SYSTEM
- Citation format instructions in TEACHER_SYSTEM
- Age/ALB calculation instructions present in both prompts
- create_agent import availability

Mocks used:
- unittest.mock.patch used to mock `langchain.agents.create_agent` to avoid
  real LangChain/LLM calls during import-time side-effect checks
- No external service calls are made by the module itself (constants only)

TODOs:
- TODO: Integration tests for teacher_agent astream_events — requires a real or
  mocked LangGraph runtime and LLM
- TODO: Integration tests for assessor_agent ainvoke — requires a real or mocked
  LangGraph runtime and LLM
- TODO: Test that RAG tools (get_current_date, list_products, etc.) are correctly
  bound to agents — requires tool fixture setup
- TODO: Test streaming behaviour of teacher agent — requires async test harness
  and mocked LLM responses
- TODO: Test assessor scoring format parsing — requires full agent invocation mock
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: import the module under test with create_agent patched so we never
# make real network/LLM calls at import time.
# ---------------------------------------------------------------------------

def _import_agent_module():
    """Import api.agent with create_agent stubbed out."""
    mock_create_agent = MagicMock(return_value=MagicMock())
    # Build a minimal fake langchain.agents module if not already present
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_lc_agents.create_agent = mock_create_agent  # type: ignore[attr-defined]

    fake_lc = types.ModuleType("langchain")
    fake_lc.agents = fake_lc_agents  # type: ignore[attr-defined]

    with patch.dict(
        sys.modules,
        {
            "langchain": fake_lc,
            "langchain.agents": fake_lc_agents,
        },
    ):
        # Force a fresh import each time this helper is called
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "agent" in sys.modules:
            del sys.modules["agent"]
        spec = importlib.util.spec_from_file_location("api.agent", "api/agent.py")
        module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


# Attempt to import once for the whole test session; fall back gracefully
try:
    _agent_mod = _import_agent_module()
    TEACHER_SYSTEM: str = _agent_mod.TEACHER_SYSTEM
    ASSESSOR_SYSTEM: str = _agent_mod.ASSESSOR_SYSTEM
    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    _agent_mod = None  # type: ignore[assignment]
    TEACHER_SYSTEM = ""
    ASSESSOR_SYSTEM = ""
    _IMPORT_ERROR = exc

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TOOL_NAMES = [
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
]

SYNTHETIC_PROFILE = (
    "Customer: Jane Doe, Age 45, looking for whole life insurance. "
    "Interested in Generations II from Sun Life."
)

SYNTHETIC_CONVERSATION = (
    "Agent: Good morning! I'd like to introduce you to our Generations II plan. "
    "It offers guaranteed lifelong protection and double bonuses. "
    "The annual premium for your age band is approximately HKD 20,000. "
    "Pre-existing conditions have a 2-year waiting period. "
    "You can visit any Class 3 hospital in Mainland China under the designated list."
)


# ---------------------------------------------------------------------------
# Guard: skip all tests if the module could not be imported
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    _agent_mod is None,
    reason=f"api/agent.py could not be imported: {_IMPORT_ERROR}",
)


# ===========================================================================
# 1. Module-level constant existence & type tests
# ===========================================================================

class TestConstantsExist:
    def test_teacher_system_exists(self):
        assert hasattr(_agent_mod, "TEACHER_SYSTEM"), "TEACHER_SYSTEM not found in module"

    def test_assessor_system_exists(self):
        assert hasattr(_agent_mod, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM not found in module"

    def test_teacher_system_is_string(self):
        assert isinstance(TEACHER_SYSTEM, str)

    def test_assessor_system_is_string(self):
        assert isinstance(ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self):
        assert len(TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_non_empty(self):
        assert len(ASSESSOR_SYSTEM.strip()) > 0


# ===========================================================================
# 2. TEACHER_SYSTEM content tests
# ===========================================================================

class TestTeacherSystemContent:
    def test_role_description_present(self):
        assert "insurance sales trainer" in TEACHER_SYSTEM.lower()

    def test_eight_tools_mentioned(self):
        assert "eight tools" in TEACHER_SYSTEM.lower()

    def test_citation_format_present(self):
        """Citation marker [[Sn]] format must be documented."""
        assert "[[S" in TEACHER_SYSTEM, "Citation marker format [[Sn]] not found"

    def test_citation_example_present(self):
        assert "[[S1]]" in TEACHER_SYSTEM

    def test_alb_instruction_present(self):
        """Age Last Birthday instruction must be present."""
        assert "Age Last Birthday" in TEACHER_SYSTEM

    def test_alb_abbreviation_present(self):
        assert "ALB" in TEACHER_SYSTEM

    def test_get_current_date_instruction(self):
        """Must instruct agent to call get_current_date first for date-relative questions."""
        assert "get_current_date" in TEACHER_SYSTEM
        lower = TEACHER_SYSTEM.lower()
        assert "first" in lower, "Teacher prompt should instruct calling get_current_date first"

    def test_never_guess_instruction(self):
        assert "Never guess" in TEACHER_SYSTEM or "never guess" in TEACHER_SYSTEM.lower()

    def test_engaging_sessions_instruction(self):
        lower = TEACHER_SYSTEM.lower()
        assert "engaging" in lower

    def test_citations_only_from_documents(self):
        """Prompt should restrict citations to retrieved documents only."""
        assert "retrieved document" in TEACHER_SYSTEM.lower()

    @pytest.mark.parametrize("tool_name", TOOL_NAMES)
    def test_tool_listed_in_teacher(self, tool_name):
        assert tool_name in TEACHER_SYSTEM, (
            f"Tool '{tool_name}' not mentioned in TEACHER_SYSTEM"
        )

    def test_hospital_network_use_case(self):
        """Prompt should document when to use lookup_hospital_network."""
        assert "lookup_hospital_network" in TEACHER_SYSTEM
        assert "Hospital" in TEACHER_SYSTEM

    def test_compare_plans_examples_present(self):
        lower = TEACHER_SYSTEM.lower()
        assert "deductible" in lower or "annual limit" in lower or "room" in lower

    def test_lookup_exclusions_use_case(self):
        lower = TEACHER_SYSTEM.lower()
        assert "pre-existing" in lower or "exclusion" in lower

    def test_search_claim_procedure_use_case(self):
        lower = TEACHER_SYSTEM.lower()
        assert "claim" in lower

    def test_premium_band_warning(self):
        lower = TEACHER_SYSTEM.lower()
        assert "premium band" in lower or "premium" in lower

    def test_age_miscalculation_warning(self):
        lower = TEACHER_SYSTEM.lower()
        assert "age miscalculation" in lower or "miscalculation" in lower


# ===========================================================================
# 3. ASSESSOR_SYSTEM content tests
# ===========================================================================

class TestAssessorSystemContent:
    def test_role_description_present(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "insurance sales trainer" in lower

    def test_assessment_task_present(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "assess" in lower

    def test_five_dimensions_mentioned(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "five" in lower or "5" in lower

    def test_profile_placeholder_present(self):
        assert "{profile}" in ASSESSOR_SYSTEM

    def test_conversation_placeholder_present(self):
        assert "{conversation}" in ASSESSOR_SYSTEM

    def test_overall_score_format_present(self):
        assert "## Overall Score: X/10" in ASSESSOR_SYSTEM

    def test_dimension_1_first_impression(self):
        assert "First Impression" in ASSESSOR_SYSTEM

    def test_dimension_2_needs_discovery(self):
        assert "Needs Discovery" in ASSESSOR_SYSTEM

    def test_dimension_3_product_knowledge(self):
        assert "Product Knowledge" in ASSESSOR_SYSTEM

    def test_dimension_4_objection_handling(self):
        assert "Objection Handling" in ASSESSOR_SYSTEM

    def test_dimension_5_closing_technique(self):
        assert "Closing Technique" in ASSESSOR_SYSTEM

    def test_key_strengths_section(self):
        assert "Key Strengths" in ASSESSOR_SYSTEM

    def test_areas_to_improve_section(self):
        assert "Areas to Improve" in ASSESSOR_SYSTEM

    def test_correct_incorrect_markers(self):
        assert "✓ Correct" in ASSESSOR_SYSTEM
        assert "✗ Incorrect" in ASSESSOR_SYSTEM

    def test_partially_correct_marker(self):
        assert "⚠ Partially correct" in ASSESSOR_SYSTEM or "⚠️" in ASSESSOR_SYSTEM

    def test_alb_instruction_present(self):
        assert "Age Last Birthday" in ASSESSOR_SYSTEM
        assert "ALB" in ASSESSOR_SYSTEM

    def test_get_current_date_first_instruction(self):
        assert "get_current_date" in ASSESSOR_SYSTEM

    def test_do_not_rely_on_memory(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "memory" in lower or "do not rely" in lower

    def test_workflow_steps_numbered(self):
        """Assessor prompt should have a numbered workflow."""
        assert "1." in ASSESSOR_SYSTEM
        assert "2." in ASSESSOR_SYSTEM
        assert "3." in ASSESSOR_SYSTEM

    def test_list_products_first_guidance(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "list_products" in lower

    @pytest.mark.parametrize("tool_name", TOOL_NAMES)
    def test_tool_listed_in_assessor(self, tool_name):
        assert tool_name in ASSESSOR_SYSTEM, (
            f"Tool '{tool_name}' not mentioned in ASSESSOR_SYSTEM"
        )

    def test_eight_tools_mentioned(self):
        assert "eight tools" in ASSESSOR_SYSTEM.lower()

    def test_premium_age_accuracy_section(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "premium" in lower and "age" in lower

    def test_flag_error_outdated_age(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "flag" in lower or "error" in lower


# ===========================================================================
# 4. ASSESSOR_SYSTEM string formatting tests
# ===========================================================================

class TestAssessorSystemFormatting:
    def test_format_with_synthetic_data(self):
        """ASSESSOR_SYSTEM.format() must succeed with profile and conversation."""
        formatted = ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert isinstance(formatted, str)
        assert len(formatted) > len(ASSESSOR_SYSTEM) - 100  # placeholders replaced

    def test_profile_injected_correctly(self):
        formatted = ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_PROFILE in formatted

    def test_conversation_injected_correctly(self):
        formatted = ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_CONVERSATION in formatted

    def test_format_with_empty_profile(self):
        """Empty profile should still format without error."""
        formatted = ASSESSOR_SYSTEM.format(profile="", conversation=SYNTHETIC_CONVERSATION)
        assert isinstance(formatted, str)
        assert "{profile}" not in formatted

    def test_format_with_empty_conversation(self):
        """Empty conversation should still format without error."""
        formatted = ASSESSOR_SYSTEM.format(profile=SYNTHETIC_PROFILE, conversation="")
        assert isinstance(formatted, str)
        assert "{conversation}" not in formatted

    def test_format_with_special_characters(self):
        """Profile/conversation with special characters must not break formatting."""
        special = "Agent said: 'HKD 20,000 {not a placeholder}'"
        formatted = ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=special,
        )
        assert isinstance(formatted, str)

    def test_format_with_unicode(self):
        """Unicode characters (e.g. Chinese) should be handled."""
        unicode_profile = "客户：李明，45岁，对生命保险感兴趣。"
        formatted = ASSESSOR_SYSTEM.format(
            profile=unicode_profile,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert unicode_profile in formatted

    def test_format_raises_on_missing_key(self):
        """Formatting with only one placeholder should raise KeyError."""
        with pytest.raises(KeyError):
            ASSESSOR_SYSTEM.format(profile=SYNTHETIC_PROFILE)

    def test_format_raises_on_no_keys(self):
        """Formatting with no placeholders should raise KeyError."""
        with pytest.raises(KeyError):
            ASSESSOR_SYSTEM.format()

    @pytest.mark.parametrize("profile,conversation", [
        (SYNTHETIC_PROFILE, SYNTHETIC_CONVERSATION),
        ("", ""),
        ("A" * 5000, "B" * 5000),  # large inputs
        ("Profile\nwith\nnewlines", "Conversation\nwith\nnewlines"),
    ])
    def test_format_parametrized(self, profile, conversation):
        formatted = ASSESSOR_SYSTEM.format(profile=profile, conversation=conversation)
        assert isinstance(formatted, str)
        assert "{profile}" not in formatted
        assert "{conversation}" not in formatted


# ===========================================================================
# 5. Tool name consistency tests (same 8 tools in both prompts)
# ===========================================================================

class TestToolConsistency:
    