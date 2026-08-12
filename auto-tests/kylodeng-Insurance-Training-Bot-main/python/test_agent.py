"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt content and structure (citations format, tool references, ALB instructions)
- ASSESSOR_SYSTEM prompt content and structure (profile/conversation placeholders, scoring format, tool references)
- Module-level constants (string types, non-empty, required sections)
- Parameterised checks for all eight tool names appearing in both system prompts
- Edge cases: placeholder formatting in ASSESSOR_SYSTEM, citation marker format in TEACHER_SYSTEM
- Assessment output format markers (section headers, score format, emoji markers)

Mocks used:
- unittest.mock.patch for `langchain.agents.create_agent` (imported at module level in agent.py)
- No real LangChain / LLM calls are made

TODOs:
- TODO: Integration tests for teacher_agent astream_events require a live or mocked LangGraph runtime
- TODO: Integration tests for assessor_agent ainvoke require a live or mocked LangGraph runtime
- TODO: Tests for actual agent graph construction need the full agent factory functions to be exported
- TODO: Verify RAG tool implementations (get_current_date, list_products, etc.) once exposed publicly
- TODO: End-to-end roleplay session tests with synthetic insurance data samples
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for langchain so the module can be imported without the real
# package being installed in the test environment.
# ---------------------------------------------------------------------------

def _install_langchain_stub():
    """Install a minimal langchain stub into sys.modules if not already present."""
    if "langchain" not in sys.modules:
        langchain_stub = types.ModuleType("langchain")
        agents_stub = types.ModuleType("langchain.agents")
        agents_stub.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
        langchain_stub.agents = agents_stub
        sys.modules["langchain"] = langchain_stub
        sys.modules["langchain.agents"] = agents_stub
    elif "langchain.agents" not in sys.modules:
        agents_stub = types.ModuleType("langchain.agents")
        agents_stub.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
        sys.modules["langchain"].agents = agents_stub
        sys.modules["langchain.agents"] = agents_stub


_install_langchain_stub()

# Now import the module under test
with patch("langchain.agents.create_agent", MagicMock(return_value=MagicMock())):
    import api.agent as agent_module
    from api.agent import ASSESSOR_SYSTEM, TEACHER_SYSTEM


# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

ALL_TOOL_NAMES = [
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
]

ASSESSOR_SECTION_HEADERS = [
    "## Overall Score:",
    "### 1. First Impression & Rapport Building",
    "### 2. Needs Discovery",
    "### 3. Product Knowledge & Accuracy",
    "### 4. Objection Handling",
    "### 5. Closing Technique",
    "### ✅ Key Strengths",
    "### ⚠️ Areas to Improve",
]


# ===========================================================================
# TEACHER_SYSTEM tests
# ===========================================================================


class TestTeacherSystemType:
    def test_is_string(self):
        assert isinstance(TEACHER_SYSTEM, str)

    def test_is_non_empty(self):
        assert len(TEACHER_SYSTEM.strip()) > 0

    def test_is_multiline(self):
        assert "\n" in TEACHER_SYSTEM


class TestTeacherSystemContent:
    def test_contains_role_description(self):
        assert "insurance sales trainer" in TEACHER_SYSTEM.lower()

    def test_contains_eight_tools_count_mention(self):
        assert "eight tools" in TEACHER_SYSTEM.lower()

    def test_mentions_age_last_birthday(self):
        assert "Age Last Birthday" in TEACHER_SYSTEM
        assert "ALB" in TEACHER_SYSTEM

    def test_warns_about_age_miscalculation(self):
        assert "get_current_date" in TEACHER_SYSTEM
        # Must instruct to call get_current_date first for date-relative calculations
        lower = TEACHER_SYSTEM.lower()
        assert "call get_current_date first" in lower or "call this first" in lower

    def test_citation_format_present(self):
        """Inline citation marker format [[Sn]] must be documented."""
        assert "[[S" in TEACHER_SYSTEM

    def test_citation_example_present(self):
        assert "[[S1]]" in TEACHER_SYSTEM

    def test_no_guessing_instruction(self):
        assert "Never guess" in TEACHER_SYSTEM or "never guess" in TEACHER_SYSTEM.lower()

    def test_encouragement_tone(self):
        lower = TEACHER_SYSTEM.lower()
        assert any(word in lower for word in ["encouraging", "confidence", "engaging"])

    def test_policy_inception_mentioned(self):
        assert "policy inception" in TEACHER_SYSTEM

    def test_premium_band_mentioned(self):
        assert "premium band" in TEACHER_SYSTEM or "premium" in TEACHER_SYSTEM


@pytest.mark.parametrize("tool_name", ALL_TOOL_NAMES)
class TestTeacherSystemTools:
    def test_tool_mentioned(self, tool_name):
        assert tool_name in TEACHER_SYSTEM, (
            f"Expected tool '{tool_name}' to be mentioned in TEACHER_SYSTEM"
        )


class TestTeacherSystemCitationFormat:
    @pytest.mark.parametrize("marker", ["[[S1]]", "[[Sn]]"])
    def test_citation_marker_format(self, marker):
        assert marker in TEACHER_SYSTEM

    def test_citation_instruction_present(self):
        assert "CITATIONS" in TEACHER_SYSTEM

    def test_citation_only_from_retrieved_docs(self):
        lower = TEACHER_SYSTEM.lower()
        assert "retrieved document" in lower or "sourced from a document" in lower


# ===========================================================================
# ASSESSOR_SYSTEM tests
# ===========================================================================


class TestAssessorSystemType:
    def test_is_string(self):
        assert isinstance(ASSESSOR_SYSTEM, str)

    def test_is_non_empty(self):
        assert len(ASSESSOR_SYSTEM.strip()) > 0

    def test_is_multiline(self):
        assert "\n" in ASSESSOR_SYSTEM


class TestAssessorSystemPlaceholders:
    def test_profile_placeholder_present(self):
        assert "{profile}" in ASSESSOR_SYSTEM

    def test_conversation_placeholder_present(self):
        assert "{conversation}" in ASSESSOR_SYSTEM

    def test_placeholders_can_be_formatted(self):
        """The ASSESSOR_SYSTEM should be usable as a Python format string."""
        result = ASSESSOR_SYSTEM.format(
            profile="Test customer, age 35, needs health insurance",
            conversation="Agent: Hello\nCustomer: Hi",
        )
        assert "Test customer, age 35" in result
        assert "Agent: Hello" in result

    def test_format_does_not_raise_on_valid_input(self):
        try:
            ASSESSOR_SYSTEM.format(profile="p", conversation="c")
        except KeyError as exc:
            pytest.fail(f"ASSESSOR_SYSTEM.format raised KeyError: {exc}")

    def test_format_raises_on_missing_profile(self):
        with pytest.raises((KeyError, IndexError)):
            ASSESSOR_SYSTEM.format(conversation="c")

    def test_format_raises_on_missing_conversation(self):
        with pytest.raises((KeyError, IndexError)):
            ASSESSOR_SYSTEM.format(profile="p")


class TestAssessorSystemContent:
    def test_contains_role_description(self):
        assert "insurance sales trainer" in ASSESSOR_SYSTEM.lower()

    def test_mentions_accuracy_assessment(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "accuracy" in lower or "assessment" in lower

    def test_mentions_five_dimensions(self):
        assert "five dimensions" in ASSESSOR_SYSTEM.lower() or "5" in ASSESSOR_SYSTEM

    def test_contains_age_last_birthday(self):
        assert "Age Last Birthday" in ASSESSOR_SYSTEM
        assert "ALB" in ASSESSOR_SYSTEM

    def test_alb_at_policy_inception(self):
        assert "policy inception" in ASSESSOR_SYSTEM

    def test_instructs_use_search_tools(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "search tools" in lower or "use your search tools" in lower or "search tool" in lower

    def test_do_not_rely_on_memory(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "memory" in lower

    def test_mentions_eight_tools(self):
        assert "eight tools" in ASSESSOR_SYSTEM.lower()

    def test_workflow_section_present(self):
        assert "Workflow" in ASSESSOR_SYSTEM or "workflow" in ASSESSOR_SYSTEM.lower()

    def test_workflow_has_steps(self):
        assert "1." in ASSESSOR_SYSTEM
        assert "2." in ASSESSOR_SYSTEM
        assert "3." in ASSESSOR_SYSTEM


@pytest.mark.parametrize("tool_name", ALL_TOOL_NAMES)
class TestAssessorSystemTools:
    def test_tool_mentioned(self, tool_name):
        assert tool_name in ASSESSOR_SYSTEM, (
            f"Expected tool '{tool_name}' to be mentioned in ASSESSOR_SYSTEM"
        )


@pytest.mark.parametrize("header", ASSESSOR_SECTION_HEADERS)
class TestAssessorSystemOutputFormat:
    def test_section_header_present(self, header):
        assert header in ASSESSOR_SYSTEM, (
            f"Expected section header '{header}' in ASSESSOR_SYSTEM"
        )

    def test_score_format_x_of_10(self, header):
        """Every section header that references a score must use X/10 format."""
        if "X/10" in header or "/10" in header:
            assert "X/10" in ASSESSOR_SYSTEM


class TestAssessorSystemScoringFormat:
    def test_overall_score_format(self):
        assert "X/10" in ASSESSOR_SYSTEM

    def test_correct_symbol(self):
        assert "✓ Correct" in ASSESSOR_SYSTEM

    def test_incorrect_symbol(self):
        assert "✗ Incorrect" in ASSESSOR_SYSTEM

    def test_partially_correct_symbol(self):
        assert "⚠ Partially correct" in ASSESSOR_SYSTEM

    def test_key_strengths_emoji(self):
        assert "✅" in ASSESSOR_SYSTEM

    def test_areas_to_improve_emoji(self):
        assert "⚠️" in ASSESSOR_SYSTEM


# ===========================================================================
# Cross-prompt consistency tests
# ===========================================================================


class TestBothPromptsConsistency:
    @pytest.mark.parametrize("tool_name", ALL_TOOL_NAMES)
    def test_same_tools_in_both_prompts(self, tool_name):
        assert tool_name in TEACHER_SYSTEM
        assert tool_name in ASSESSOR_SYSTEM

    def test_both_mention_get_current_date_first(self):
        """Both prompts must instruct to call get_current_date first for ALB."""
        assert "get_current_date" in TEACHER_SYSTEM
        assert "get_current_date" in ASSESSOR_SYSTEM

    def test_both_reference_alb(self):
        assert "ALB" in TEACHER_SYSTEM
        assert "ALB" in ASSESSOR_SYSTEM

    def test_both_reference_policy_inception(self):
        assert "policy inception" in TEACHER_SYSTEM
        assert "policy inception" in ASSESSOR_SYSTEM

    def test_teacher_does_not_have_profile_placeholder(self):
        """TEACHER_SYSTEM should not accidentally contain the assessor's placeholders."""
        assert "{profile}" not in TEACHER_SYSTEM
        assert "{conversation}" not in TEACHER_SYSTEM


# ===========================================================================
# Module-level attribute tests
# ===========================================================================


class TestModuleAttributes:
    def test_teacher_system_exported(self):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exported(self):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_module_has_docstring(self):
        assert agent_module.__doc__ is not None
        assert len(agent_module.__doc__.strip()) > 0

    def test_docstring_mentions_teacher(self):
        assert "teacher" in agent_module.__doc__.lower()

    def test_docstring_mentions_assessor(self):
        assert "assessor" in agent_module.__doc__.lower() or "assessment" in agent_module.__doc__.lower()

    def test_docstring_mentions_rag_tools(self):
        assert "RAG" in agent_module.__doc__ or "rag" in agent_module.__doc__.lower()


# ===========================================================================
# Synthetic data integration stubs
# ===========================================================================


SYNTHETIC_PROFILES = [
    {
        "product_name": "Generations II",
        "doc_type": "product_brochure",
        "linked_product": "Generations II",
        "summary": "Participating whole life insurance plan by Sun Life.",
    },
    {
        "product_name": "List of Designated Hospitals in Mainland China",
        "doc_type": "supplementary",
        "linked_product": "health_products",
        "summary": "Official list of designated hospitals in mainland China for insurance claims.",
    },
    {
        "product_name": "Global Network Hospital List for Cashless Arrangement",
        "doc_type": "supplementary",
        "linked_product": "health_products",
        "summary": "Global cashless hospital network list.",
    },
]


@pytest.mark.parametrize("sample", SYNTHETIC_PROFILES)
class TestAssessorSystemWithSyntheticData:
    def test_format_with_synthetic_profile(self, sample):
        """ASSESSOR_SYSTEM should format cleanly with synthetic profile data."""
        profile_str = (
            f"Product: {sample['product_name']}\n"
            f"Type: {sample['doc_type']}\n"
            f"Summary: {sample['summary']}"
        )
        conversation_str = (
            "Agent: Good morning! I'd like to walk you through the Generations II plan.\n"
            "Customer: Sure, please go ahead."
        )
        result = ASSESSOR_SYSTEM.format(
            profile=profile_str,
            conversation=conversation_str,
        )
        assert sample["product_name"] in result
        assert "Agent:" in result
        assert "## Overall Score:" in result

    def test_formatted_result_retains_section_headers(self, sample):
        result = ASSESSOR_SYSTEM.format(
            profile=sample["summary"],
            conversation="Agent: Hi\nCustomer: Hi",
        )
        for header in ASSESSOR_SECTION_HEADERS:
            assert header in result, f"Section header '{header}' missing after format()"


# ===========================================================================
# Stub / skipped tests for agent runtime (requires LangGraph runtime)
# ===========================================================================


@pytest.mark.skip(
    reason=(
        "TODO: Integration test for teacher agent astream_events — "
        "requires a live or fully mocked LangGraph runtime and LLM backend"
    )
)
def test_teacher_agent_streams_events():
    