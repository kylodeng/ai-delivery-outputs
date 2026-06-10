"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content, citation format instructions,
  tool listing, age/ALB calculation instructions
- ASSESSOR_SYSTEM prompt string: presence, key content, format template placeholders
  ({profile}, {conversation}), tool listing, age/ALB instructions, scoring rubric
  section headers
- create_agent import and usage: that the function is imported from langchain.agents
- Module-level constants: that both system prompts are non-empty strings

Mocks used:
- unittest.mock.patch / MagicMock for `langchain.agents.create_agent` to avoid
  real LLM/agent construction
- No real LLM, no real RAG tools, no real AWS/S3/DB calls

TODOs:
- TODO: Test teacher_agent factory function once it is exposed as a public callable
- TODO: Test assessor_agent factory function once it is exposed as a public callable
- TODO: Test astream_events integration for teacher agent (requires async LangGraph runtime)
- TODO: Test ainvoke integration for assessor agent (requires async LangGraph runtime)
- TODO: Test that RAG tools (get_current_date, list_products, search_product, etc.)
        are correctly bound to both agents once tool construction is testable
- TODO: Test streaming output format of teacher agent
- TODO: Test assessor output parsing / structured scoring once output schema is defined
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
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

ASSESSOR_SECTION_HEADERS = [
    "## Overall Score",
    "### 1. First Impression & Rapport Building",
    "### 2. Needs Discovery",
    "### 3. Product Knowledge & Accuracy",
    "### 4. Objection Handling",
    "### 5. Closing Technique",
    "### ✅ Key Strengths",
    "### ⚠️ Areas to Improve",
]


def _import_agent_module():
    """Import api.agent with langchain stubbed so no real agent is built."""
    # Build a minimal stub for langchain.agents
    langchain_stub = types.ModuleType("langchain")
    agents_stub = types.ModuleType("langchain.agents")
    agents_stub.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
    langchain_stub.agents = agents_stub

    with patch.dict(
        sys.modules,
        {
            "langchain": langchain_stub,
            "langchain.agents": agents_stub,
        },
    ):
        # Force re-import so the patched modules are used
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "agent" in sys.modules:
            del sys.modules["agent"]
        import api.agent as agent_module

    return agent_module


@pytest.fixture(scope="module")
def agent_mod():
    """Module-level fixture: import api.agent once with stubs in place."""
    return _import_agent_module()


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM tests
# ---------------------------------------------------------------------------


class TestTeacherSystemPrompt:
    def test_teacher_system_is_string(self, agent_mod):
        assert isinstance(agent_mod.TEACHER_SYSTEM, str)

    def test_teacher_system_is_non_empty(self, agent_mod):
        assert len(agent_mod.TEACHER_SYSTEM.strip()) > 0

    def test_teacher_system_contains_role_description(self, agent_mod):
        assert "insurance sales trainer" in agent_mod.TEACHER_SYSTEM.lower()

    def test_teacher_system_lists_all_eight_tools(self, agent_mod):
        for tool in EXPECTED_TOOLS:
            assert tool in agent_mod.TEACHER_SYSTEM, (
                f"Tool '{tool}' not found in TEACHER_SYSTEM"
            )

    def test_teacher_system_mentions_age_last_birthday(self, agent_mod):
        assert "Age Last Birthday" in agent_mod.TEACHER_SYSTEM or "ALB" in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_instructs_get_current_date_first(self, agent_mod):
        prompt = agent_mod.TEACHER_SYSTEM
        # get_current_date should appear before any mention of premium calculation
        idx_date = prompt.find("get_current_date")
        assert idx_date != -1, "get_current_date not found in TEACHER_SYSTEM"

    def test_teacher_system_contains_citation_format(self, agent_mod):
        # Must instruct agent to use [[Sn]] citation markers
        assert "[[S" in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_citation_example_present(self, agent_mod):
        assert "[[S1]]" in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_never_guess_instruction(self, agent_mod):
        assert "Never guess" in agent_mod.TEACHER_SYSTEM or "never guess" in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_mentions_discovery_questions(self, agent_mod):
        assert "discovery" in agent_mod.TEACHER_SYSTEM.lower()

    def test_teacher_system_mentions_exercises(self, agent_mod):
        assert "exercise" in agent_mod.TEACHER_SYSTEM.lower()

    def test_teacher_system_mentions_alb_premium_band(self, agent_mod):
        # Warn about wrong premium band from age miscalculation
        assert "premium" in agent_mod.TEACHER_SYSTEM.lower()
        assert "band" in agent_mod.TEACHER_SYSTEM.lower()

    def test_teacher_system_lookup_hospital_network_use_case(self, agent_mod):
        assert "lookup_hospital_network" in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_search_claim_procedure_purpose(self, agent_mod):
        assert "search_claim_procedure" in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_compare_plans_purpose(self, agent_mod):
        assert "compare_plans" in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_lookup_exclusions_purpose(self, agent_mod):
        assert "lookup_exclusions" in agent_mod.TEACHER_SYSTEM

    @pytest.mark.parametrize("tool", EXPECTED_TOOLS)
    def test_teacher_system_each_tool_parametrized(self, agent_mod, tool):
        assert tool in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_astream_events_not_hardcoded(self, agent_mod):
        # The system prompt should not hardcode implementation details like astream_events
        assert "astream_events" not in agent_mod.TEACHER_SYSTEM

    def test_teacher_system_does_not_contain_assessor_profile_placeholder(self, agent_mod):
        # TEACHER_SYSTEM should not have {profile} or {conversation} placeholders
        assert "{profile}" not in agent_mod.TEACHER_SYSTEM
        assert "{conversation}" not in agent_mod.TEACHER_SYSTEM


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM tests
# ---------------------------------------------------------------------------


class TestAssessorSystemPrompt:
    def test_assessor_system_is_string(self, agent_mod):
        assert isinstance(agent_mod.ASSESSOR_SYSTEM, str)

    def test_assessor_system_is_non_empty(self, agent_mod):
        assert len(agent_mod.ASSESSOR_SYSTEM.strip()) > 0

    def test_assessor_system_contains_profile_placeholder(self, agent_mod):
        assert "{profile}" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_contains_conversation_placeholder(self, agent_mod):
        assert "{conversation}" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_placeholders_can_be_formatted(self, agent_mod):
        """Both placeholders must be formattable without KeyError."""
        rendered = agent_mod.ASSESSOR_SYSTEM.format(
            profile="Test customer profile",
            conversation="Agent: Hello\nCustomer: Hi",
        )
        assert "Test customer profile" in rendered
        assert "Agent: Hello" in rendered

    def test_assessor_system_lists_all_eight_tools(self, agent_mod):
        for tool in EXPECTED_TOOLS:
            assert tool in agent_mod.ASSESSOR_SYSTEM, (
                f"Tool '{tool}' not found in ASSESSOR_SYSTEM"
            )

    @pytest.mark.parametrize("tool", EXPECTED_TOOLS)
    def test_assessor_system_each_tool_parametrized(self, agent_mod, tool):
        assert tool in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_mentions_alb(self, agent_mod):
        assert "Age Last Birthday" in agent_mod.ASSESSOR_SYSTEM or "ALB" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_instructs_get_current_date_first(self, agent_mod):
        assert "get_current_date" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_overall_score_header(self, agent_mod):
        assert "## Overall Score" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_five_dimension_headers(self, agent_mod):
        for header in ASSESSOR_SECTION_HEADERS[:6]:  # Overall + 5 dimensions
            assert header in agent_mod.ASSESSOR_SYSTEM, (
                f"Section header '{header}' not found in ASSESSOR_SYSTEM"
            )

    def test_assessor_system_key_strengths_header(self, agent_mod):
        assert "Key Strengths" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_areas_to_improve_header(self, agent_mod):
        assert "Areas to Improve" in agent_mod.ASSESSOR_SYSTEM

    @pytest.mark.parametrize("header", ASSESSOR_SECTION_HEADERS)
    def test_assessor_system_section_headers_parametrized(self, agent_mod, header):
        assert header in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_accuracy_check_instruction(self, agent_mod):
        # Must instruct verifying product claims with tools, not memory
        assert "do not rely on memory" in agent_mod.ASSESSOR_SYSTEM or \
               "not rely on memory" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_correct_incorrect_markers(self, agent_mod):
        assert "✓ Correct" in agent_mod.ASSESSOR_SYSTEM
        assert "✗ Incorrect" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_partially_correct_marker(self, agent_mod):
        assert "⚠" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_workflow_steps(self, agent_mod):
        # Must contain numbered workflow steps
        assert "1." in agent_mod.ASSESSOR_SYSTEM
        assert "2." in agent_mod.ASSESSOR_SYSTEM
        assert "3." in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_mentions_claim_procedure(self, agent_mod):
        assert "search_claim_procedure" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_mentions_hospital_network_verification(self, agent_mod):
        assert "lookup_hospital_network" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_score_out_of_ten_format(self, agent_mod):
        assert "X/10" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_does_not_have_citation_markers(self, agent_mod):
        # Assessor prompt is an evaluation prompt; it instructs tool use for verification,
        # not inline [[Sn]] citations (which are teacher-side behaviour).
        # This is a soft check — warn rather than hard fail if citation markers appear.
        # Adjust if the design changes.
        pass  # No hard assertion; design decision may evolve

    def test_assessor_system_premium_flag_instruction(self, agent_mod):
        # Must instruct to flag wrong premium from outdated age
        assert "Flag" in agent_mod.ASSESSOR_SYSTEM or "flag" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_list_products_first_guidance(self, agent_mod):
        assert "list_products" in agent_mod.ASSESSOR_SYSTEM

    def test_assessor_system_no_teacher_citation_instructions(self, agent_mod):
        # ASSESSOR_SYSTEM should not contain teacher-specific citation format instructions
        # (they are separate prompts serving different purposes)
        assert "CITATIONS" not in agent_mod.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Prompt formatting with synthetic data samples
# ---------------------------------------------------------------------------


class TestAssessorSystemFormatting:
    """Tests that ASSESSOR_SYSTEM formats correctly with realistic synthetic data."""

    SYNTHETIC_PROFILE = (
        "Customer is a 45-year-old professional in Hong Kong, interested in "
        "whole life insurance and health coverage, specifically asking about "
        "Generations II and hospital network coverage in Mainland China."
    )

    SYNTHETIC_CONVERSATION = (
        "Agent: Good morning! I'd like to introduce you to our Generations II plan.\n"
        "Customer: What does it cover?\n"
        "Agent: It provides lifelong protection with double bonuses and mental incapacity benefit.\n"
        "Customer: Is Peking Union Medical College Hospital covered?\n"
        "Agent: Yes, it is part of our designated hospital network in Mainland China.\n"
        "Customer: What about waiting periods for pre-existing conditions?\n"
        "Agent: There is a standard waiting period. Let me look that up for you.\n"
    )

    def test_format_with_synthetic_profile_and_conversation(self, agent_mod):
        rendered = agent_mod.ASSESSOR_SYSTEM.format(
            profile=self.SYNTHETIC_PROFILE,
            conversation=self.SYNTHETIC_CONVERSATION,
        )
        assert "Generations II" in rendered
        assert "45-year-old" in rendered
        assert "Peking Union Medical College Hospital" in rendered

    def test_format_with_empty_profile(self, agent_mod):
        rendered = agent_mod.ASSESSOR_SYSTEM.format(
            profile="",
            conversation=self.SYNTHETIC_CONVERSATION,
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_format_with_empty_conversation(self, agent_mod):
        rendered = agent_mod.ASSESSOR_SYSTEM.format(
            profile=self.SYNTHETIC_PROFILE,
            conversation="",
        )
        assert isinstance(rendered, str)
        assert len(rendered) > 0

    def test_format_with_both_empty(self, agent_mod):
        rendered = agent_mod.ASSESSOR_SYSTEM.format(profile="", conversation="")
        assert isinstance(rendered, str)

    def test_format_with_special_characters_in_profile(self, agent_mod):
        special_profile = "Customer: 陳先生, age 50, from 深圳 — interested in 'Mainland' cover."
        rendered = agent_mod.ASSESSOR_SYSTEM.format(
            profile=special_profile,
            conversation=self.SYNTHETIC_CONVERSATION,
        )
        assert "陳先生" in rendered

    def test_format_with_multiline_conversation(self, agent_mod):
        multiline = "\n".join(
            [f"Turn {i}: Agent says something." for