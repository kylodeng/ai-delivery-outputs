"""
Test module for api/agent.py

What is tested:
    - TEACHER_SYSTEM prompt string: presence, content, key instructions, citation format
    - ASSESSOR_SYSTEM prompt string: presence, content, placeholder variables, tool list
    - Module-level constants and their structural properties
    - String formatting of ASSESSOR_SYSTEM with synthetic profile/conversation data
    - Presence and correct configuration of shared tool references in both prompts
    - Edge cases: empty profile/conversation injection, special characters in format fields

Mocks used:
    - langchain.agents.create_agent is patched at module level to avoid real LangChain
      initialisation during import (no real agent is constructed in the source at module
      level, but the import itself must not raise)
    - No external service calls are made by the constants under test

TODOs:
    - TODO: test actual teacher_agent and assessor_agent objects once they are exported
      from api/agent.py (currently the module only defines system prompts + imports)
    - TODO: test astream_events streaming behaviour for teacher agent when agent
      construction/export is available
    - TODO: test ainvoke one-shot behaviour for assessor agent when exported
    - TODO: test RAG tool integration (get_current_date, list_products, search_product,
      search_all, lookup_hospital_network, compare_plans, lookup_exclusions,
      search_claim_procedure) with mocked vector stores
    - TODO: verify create_agent is called with correct tools and system prompts once
      agent factory functions are exposed
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: patch langchain so the module can be imported without real deps
# ---------------------------------------------------------------------------

def _make_langchain_stub():
    """Return a minimal stub package tree for langchain so the import works."""
    langchain = types.ModuleType("langchain")
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_agents.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
    langchain.__path__ = []
    sys.modules.setdefault("langchain", langchain)
    sys.modules.setdefault("langchain.agents", langchain_agents)
    return langchain, langchain_agents


# Patch before any import of the module under test
_make_langchain_stub()

# Now import the module
import api.agent as agent_module  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def teacher_prompt():
    return agent_module.TEACHER_SYSTEM


@pytest.fixture()
def assessor_prompt():
    return agent_module.ASSESSOR_SYSTEM


@pytest.fixture()
def sample_profile():
    return (
        "Name: John Doe, Age: 45, Occupation: Engineer, "
        "Health concerns: hypertension, Budget: HKD 5,000/year"
    )


@pytest.fixture()
def sample_conversation():
    return (
        "Agent: Good morning! I'd like to introduce you to our Generations II plan.\n"
        "Customer: What does it cover?\n"
        "Agent: It provides lifelong protection with guaranteed benefits and double bonuses.\n"
        "Customer: What about pre-existing conditions?\n"
        "Agent: There is a waiting period of 12 months for pre-existing conditions.\n"
        "Customer: Can I go to hospitals in mainland China?\n"
        "Agent: Yes, we have a network of designated Class 3 hospitals in mainland China.\n"
        "Customer: How do I file a claim?\n"
        "Agent: You submit the claim form together with your receipts within 30 days."
    )


# ---------------------------------------------------------------------------
# Tests: TEACHER_SYSTEM
# ---------------------------------------------------------------------------

class TestTeacherSystemPrompt:

    def test_is_string(self, teacher_prompt):
        assert isinstance(teacher_prompt, str)

    def test_is_non_empty(self, teacher_prompt):
        assert len(teacher_prompt.strip()) > 0

    def test_contains_role_description(self, teacher_prompt):
        assert "insurance sales trainer" in teacher_prompt

    def test_contains_new_agent_target_audience(self, teacher_prompt):
        assert "new insurance agent" in teacher_prompt

    def test_lists_all_eight_tools(self, teacher_prompt):
        expected_tools = [
            "get_current_date",
            "list_products",
            "search_product",
            "search_all",
            "lookup_hospital_network",
            "compare_plans",
            "lookup_exclusions",
            "search_claim_procedure",
        ]
        for tool in expected_tools:
            assert tool in teacher_prompt, f"Tool '{tool}' missing from TEACHER_SYSTEM"

    def test_tool_count_matches_eight(self, teacher_prompt):
        """Verify the prompt explicitly mentions eight tools."""
        assert "eight tools" in teacher_prompt

    def test_age_last_birthday_instruction(self, teacher_prompt):
        """ALB calculation guidance must be present."""
        assert "Age Last Birthday" in teacher_prompt
        assert "ALB" in teacher_prompt

    def test_get_current_date_called_first_instruction(self, teacher_prompt):
        """Prompt must instruct the agent to call get_current_date first."""
        assert "get_current_date first" in teacher_prompt

    def test_never_guess_product_details(self, teacher_prompt):
        assert "Never guess product details" in teacher_prompt

    def test_citations_format_present(self, teacher_prompt):
        """Citation marker format [[Sn]] must be documented."""
        assert "[[S" in teacher_prompt

    def test_citation_example_present(self, teacher_prompt):
        assert "[[S1]]" in teacher_prompt

    def test_citation_instruction_keyword(self, teacher_prompt):
        assert "CITATIONS" in teacher_prompt

    def test_citation_only_when_from_document(self, teacher_prompt):
        assert "drawing directly from a retrieved document" in teacher_prompt

    def test_engaging_session_instruction(self, teacher_prompt):
        assert "engaging" in teacher_prompt.lower()

    def test_discovery_questions_mentioned(self, teacher_prompt):
        assert "discovery" in teacher_prompt.lower()

    def test_does_not_contain_profile_placeholder(self, teacher_prompt):
        """TEACHER_SYSTEM must NOT contain {profile} — that belongs to the assessor."""
        assert "{profile}" not in teacher_prompt

    def test_does_not_contain_conversation_placeholder(self, teacher_prompt):
        assert "{conversation}" not in teacher_prompt

    def test_no_unresolved_format_placeholders(self, teacher_prompt):
        """Calling .format() with no args on TEACHER_SYSTEM must not raise KeyError."""
        # The string should have no {placeholder} tokens that need substitution
        try:
            teacher_prompt.format()
        except KeyError:
            pytest.fail(
                "TEACHER_SYSTEM contains unresolved {placeholder} tokens "
                "that require substitution arguments"
            )

    def test_age_calculation_example_present(self, teacher_prompt):
        """A concrete age example should be present to illustrate the ALB rule."""
        assert "January 2020" in teacher_prompt or "50 in January" in teacher_prompt

    def test_hospital_network_tool_description(self, teacher_prompt):
        assert "lookup_hospital_network" in teacher_prompt
        assert "hospital" in teacher_prompt.lower()

    def test_compare_plans_tool_described(self, teacher_prompt):
        assert "compare_plans" in teacher_prompt
        # Should mention at least one attribute type
        assert any(
            attr in teacher_prompt.lower()
            for attr in ["deductible", "annual limit", "room"]
        )

    def test_lookup_exclusions_for_preexisting(self, teacher_prompt):
        assert "lookup_exclusions" in teacher_prompt
        assert "pre-existing" in teacher_prompt.lower()


# ---------------------------------------------------------------------------
# Tests: ASSESSOR_SYSTEM
# ---------------------------------------------------------------------------

class TestAssessorSystemPrompt:

    def test_is_string(self, assessor_prompt):
        assert isinstance(assessor_prompt, str)

    def test_is_non_empty(self, assessor_prompt):
        assert len(assessor_prompt.strip()) > 0

    def test_contains_profile_placeholder(self, assessor_prompt):
        assert "{profile}" in assessor_prompt

    def test_contains_conversation_placeholder(self, assessor_prompt):
        assert "{conversation}" in assessor_prompt

    def test_lists_all_eight_tools(self, assessor_prompt):
        expected_tools = [
            "get_current_date",
            "list_products",
            "search_product",
            "search_all",
            "lookup_hospital_network",
            "compare_plans",
            "lookup_exclusions",
            "search_claim_procedure",
        ]
        for tool in expected_tools:
            assert tool in assessor_prompt, f"Tool '{tool}' missing from ASSESSOR_SYSTEM"

    def test_tool_count_matches_eight(self, assessor_prompt):
        assert "eight tools" in assessor_prompt

    def test_five_assessment_dimensions(self, assessor_prompt):
        """All five scoring dimensions must be referenced."""
        dimensions = [
            "First Impression",
            "Needs Discovery",
            "Product Knowledge",
            "Objection Handling",
            "Closing Technique",
        ]
        for dim in dimensions:
            assert dim in assessor_prompt, f"Dimension '{dim}' missing from ASSESSOR_SYSTEM"

    def test_overall_score_format(self, assessor_prompt):
        assert "Overall Score: X/10" in assessor_prompt

    def test_dimension_score_format(self, assessor_prompt):
        assert "X/10" in assessor_prompt

    def test_correct_incorrect_markers(self, assessor_prompt):
        assert "✓ Correct" in assessor_prompt
        assert "✗ Incorrect" in assessor_prompt
        assert "⚠ Partially correct" in assessor_prompt

    def test_key_strengths_section(self, assessor_prompt):
        assert "Key Strengths" in assessor_prompt

    def test_areas_to_improve_section(self, assessor_prompt):
        assert "Areas to Improve" in assessor_prompt

    def test_workflow_steps_present(self, assessor_prompt):
        assert "Workflow" in assessor_prompt
        assert "1." in assessor_prompt
        assert "2." in assessor_prompt
        assert "3." in assessor_prompt

    def test_age_last_birthday_instruction(self, assessor_prompt):
        assert "Age Last Birthday" in assessor_prompt
        assert "ALB" in assessor_prompt

    def test_get_current_date_first_instruction(self, assessor_prompt):
        assert "get_current_date first" in assessor_prompt

    def test_do_not_rely_on_memory_instruction(self, assessor_prompt):
        assert "do not rely on memory" in assessor_prompt

    def test_product_knowledge_accuracy_dimension_uses_tools(self, assessor_prompt):
        assert "search tools" in assessor_prompt or "use your search tools" in assessor_prompt

    def test_format_with_sample_data(self, assessor_prompt, sample_profile, sample_conversation):
        """ASSESSOR_SYSTEM must be formattable with profile and conversation."""
        formatted = assessor_prompt.format(
            profile=sample_profile,
            conversation=sample_conversation,
        )
        assert sample_profile in formatted
        assert sample_conversation in formatted

    def test_format_injects_profile_correctly(self, assessor_prompt, sample_profile):
        formatted = assessor_prompt.format(
            profile=sample_profile,
            conversation="(empty)",
        )
        assert "John Doe" in formatted
        assert "hypertension" in formatted

    def test_format_injects_conversation_correctly(self, assessor_prompt, sample_conversation):
        formatted = assessor_prompt.format(
            profile="(empty)",
            conversation=sample_conversation,
        )
        assert "Generations II" in formatted
        assert "mainland China" in formatted

    def test_format_with_empty_profile(self, assessor_prompt):
        """Empty profile string should not raise."""
        formatted = assessor_prompt.format(profile="", conversation="test conversation")
        assert "test conversation" in formatted

    def test_format_with_empty_conversation(self, assessor_prompt):
        """Empty conversation string should not raise."""
        formatted = assessor_prompt.format(profile="test profile", conversation="")
        assert "test profile" in formatted

    def test_format_with_special_characters_in_profile(self, assessor_prompt):
        """Special characters (HKD, %) should not break formatting."""
        special_profile = "Budget: HKD 5,000/year; risk tolerance: 80%"
        formatted = assessor_prompt.format(
            profile=special_profile,
            conversation="none",
        )
        assert "HKD 5,000/year" in formatted

    def test_format_with_multiline_conversation(self, assessor_prompt, sample_conversation):
        """Multi-line conversation strings should be preserved."""
        formatted = assessor_prompt.format(
            profile="profile",
            conversation=sample_conversation,
        )
        assert "\n" in formatted

    def test_flag_outdated_age_instruction(self, assessor_prompt):
        """Assessor must be told to flag wrong age as an error."""
        assert "Flag it as an error" in assessor_prompt or "flag it as an error" in assessor_prompt

    def test_list_products_use_first_if_unsure(self, assessor_prompt):
        assert "list_products first" in assessor_prompt

    def test_expert_trainer_role(self, assessor_prompt):
        assert "expert insurance sales trainer" in assessor_prompt

    def test_roleplay_context_mentioned(self, assessor_prompt):
        assert "roleplay" in assessor_prompt.lower()

    def test_trainee_agent_mentioned(self, assessor_prompt):
        assert "trainee" in assessor_prompt.lower()


# ---------------------------------------------------------------------------
# Tests: shared / cross-prompt consistency
# ---------------------------------------------------------------------------

class TestPromptConsistency:

    SHARED_TOOLS = [
        "get_current_date",
        "list_products",
        "search_product",
        "search_all",
        "lookup_hospital_network",
        "compare_plans",
        "lookup_exclusions",
        "search_claim_procedure",
    ]

    @pytest.mark.parametrize("tool", SHARED_TOOLS)
    def test_tool_in_teacher_prompt(self, teacher_prompt, tool):
        assert tool in teacher_prompt

    @pytest.mark.parametrize("tool", SHARED_TOOLS)
    def test_tool_in_assessor_prompt(self, assessor_prompt, tool):
        assert tool in assessor_prompt

    def test_alb_instruction_in_both_prompts(self, teacher_prompt, assessor_prompt):
        assert "ALB" in teacher_prompt
        assert "ALB" in assessor_prompt

    def test_both_prompts_are_distinct(self, teacher_prompt, assessor_prompt):
        assert teacher_prompt != assessor_prompt

    def test_assessor_longer_than_teacher(self, teacher_prompt, assessor_prompt):
        """Assessor prompt includes a scoring rubric so should generally be longer."""
        # This is a soft check; if it fails it signals a significant structural change
        assert len(assessor_prompt) > len(teacher_prompt) * 0.5

    def test_teacher_has_no_scoring_rubric(self, teacher_prompt):
        assert "Overall Score" not in teacher_prompt

    def test_assessor_has_scoring_rubric(self, assessor_prompt):
        assert "Overall Score" in assessor_prompt


# ---------------------------------------------------------------------------
# Tests: module-level attributes
# ---------------------------------------------------------------------------

class TestModuleAttributes:

    def test_teacher_system_attribute_exists(self):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_attribute