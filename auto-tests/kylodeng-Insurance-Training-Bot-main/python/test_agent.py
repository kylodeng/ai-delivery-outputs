"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content sections, formatting requirements
- ASSESSOR_SYSTEM prompt string: presence, key content sections, placeholder variables,
  formatting requirements
- Module-level constants: existence, types, non-emptiness
- Tool references: all eight tools are mentioned in both system prompts
- Citation format instructions in TEACHER_SYSTEM
- Assessment format instructions in ASSESSOR_SYSTEM
- Placeholder correctness in ASSESSOR_SYSTEM ({profile}, {conversation})
- create_agent import availability

Mocks used:
- unittest.mock.patch used to mock `langchain.agents.create_agent` to avoid
  real LangChain/LLM dependencies

TODOs:
- TODO: Integration tests for teacher_agent streaming via astream_events require
  a live or mocked LangGraph runtime with actual tool bindings
- TODO: Integration tests for assessor_agent ainvoke require a live or mocked
  LangGraph runtime with actual tool bindings
- TODO: Tests for agent tool execution (get_current_date, list_products, etc.)
  require the tool implementations to be accessible/importable
- TODO: Tests for RAG tool responses with the synthetic insurance data samples
  require a running vector store / retrieval backend
- TODO: End-to-end streaming tests for TEACHER_SYSTEM with astream_events
  require async LangGraph test harness
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
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

ASSESSOR_REQUIRED_SECTIONS = [
    "## Overall Score",
    "### 1. First Impression & Rapport Building",
    "### 2. Needs Discovery",
    "### 3. Product Knowledge & Accuracy",
    "### 4. Objection Handling",
    "### 5. Closing Technique",
    "### ✅ Key Strengths",
    "### ⚠️ Areas to Improve",
]


@pytest.fixture(scope="module")
def agent_module():
    """
    Import api.agent with langchain.agents.create_agent stubbed out so we
    never touch a real LLM or network.
    """
    # Build a minimal fake langchain.agents module if not already present
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_lc_agents.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    fake_lc = types.ModuleType("langchain")
    fake_lc.agents = fake_lc_agents

    with patch.dict(
        sys.modules,
        {
            "langchain": fake_lc,
            "langchain.agents": fake_lc_agents,
        },
    ):
        # Force re-import in case already cached
        sys.modules.pop("api.agent", None)
        sys.modules.pop("agent", None)
        try:
            import api.agent as mod
        except ModuleNotFoundError:
            # Fallback: try importing as a top-level module
            import importlib.util
            import os

            spec = importlib.util.spec_from_file_location(
                "agent", os.path.join(os.path.dirname(__file__), "..", "api", "agent.py")
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

        yield mod


@pytest.fixture()
def teacher_sys(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture()
def assessor_sys(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Module-level sanity
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_string(self, teacher_sys):
        assert isinstance(teacher_sys, str)

    def test_assessor_system_is_string(self, assessor_sys):
        assert isinstance(assessor_sys, str)

    def test_teacher_system_non_empty(self, teacher_sys):
        assert len(teacher_sys.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_sys):
        assert len(assessor_sys.strip()) > 0

    def test_create_agent_importable(self, agent_module):
        """create_agent must be importable from langchain.agents (mocked here)."""
        from langchain.agents import create_agent  # noqa: F401 — import check only

    def test_module_has_docstring(self, agent_module):
        assert agent_module.__doc__ is not None
        assert len(agent_module.__doc__.strip()) > 0


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM prompt content tests
# ---------------------------------------------------------------------------


class TestTeacherSystemContent:
    def test_teacher_system_identity(self, teacher_sys):
        """The teacher prompt must establish the trainer identity."""
        assert "insurance sales trainer" in teacher_sys.lower() or "trainer" in teacher_sys.lower()

    def test_teacher_system_mentions_coach_or_trainer(self, teacher_sys):
        assert "coach" in teacher_sys or "trainer" in teacher_sys

    def test_teacher_system_mentions_agent(self, teacher_sys):
        assert "agent" in teacher_sys

    def test_teacher_system_mentions_discovery_questions(self, teacher_sys):
        assert "discovery" in teacher_sys.lower()

    def test_teacher_system_mentions_exercises_or_scenarios(self, teacher_sys):
        lower = teacher_sys.lower()
        assert "exercise" in lower or "scenario" in lower or "quiz" in lower

    def test_teacher_system_has_eight_tools_header(self, teacher_sys):
        """Prompt must declare eight tools."""
        assert "eight tools" in teacher_sys.lower() or "8 tools" in teacher_sys.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_system_mentions_tool(self, teacher_sys, tool_name):
        assert tool_name in teacher_sys, (
            f"TEACHER_SYSTEM must reference tool '{tool_name}'"
        )

    def test_teacher_system_age_last_birthday(self, teacher_sys):
        """ALB calculation instruction must be present."""
        assert "Age Last Birthday" in teacher_sys or "ALB" in teacher_sys

    def test_teacher_system_get_current_date_called_first(self, teacher_sys):
        """Prompt should instruct to call get_current_date first for date calculations."""
        lower = teacher_sys.lower()
        assert "get_current_date" in lower
        # The instruction to call it first must be somewhere in the prompt
        assert "first" in lower

    def test_teacher_system_citation_format(self, teacher_sys):
        """Citation format [[Sn]] must be described."""
        assert "[[S" in teacher_sys or "[[Sn]]" in teacher_sys

    def test_teacher_system_citation_example_annual_deductible(self, teacher_sys):
        """The example citation sentence must appear."""
        assert "[[S1]]" in teacher_sys

    def test_teacher_system_never_guess(self, teacher_sys):
        """Prompt must tell the agent not to guess product details."""
        lower = teacher_sys.lower()
        assert "never guess" in lower or "do not guess" in lower or "never guess" in lower

    def test_teacher_system_premium_band(self, teacher_sys):
        """Premium band concept must be mentioned."""
        assert "premium" in teacher_sys.lower()

    def test_teacher_system_engaging_sessions(self, teacher_sys):
        lower = teacher_sys.lower()
        assert "engaging" in lower or "confidence" in lower or "encouraging" in lower

    def test_teacher_system_streamed_description_in_module_docstring(self, agent_module):
        """Module docstring should mention astream_events for teacher agent."""
        assert "astream_events" in agent_module.__doc__

    def test_teacher_system_no_bare_placeholder_braces(self, teacher_sys):
        """Teacher prompt should NOT have unfilled {profile} or {conversation} placeholders."""
        assert "{profile}" not in teacher_sys
        assert "{conversation}" not in teacher_sys

    def test_teacher_system_lookup_hospital_network_description(self, teacher_sys):
        """Hospital network tool must include usage guidance."""
        assert "lookup_hospital_network" in teacher_sys
        # Guidance: mentions hospital by name and/or area
        lower = teacher_sys.lower()
        assert "hospital" in lower

    def test_teacher_system_compare_plans_description(self, teacher_sys):
        lower = teacher_sys.lower()
        assert "deductible" in lower or "annual limit" in lower or "room" in lower

    def test_teacher_system_lookup_exclusions_description(self, teacher_sys):
        lower = teacher_sys.lower()
        assert "pre-existing" in lower or "waiting period" in lower or "exclusion" in lower

    def test_teacher_system_search_claim_procedure_description(self, teacher_sys):
        lower = teacher_sys.lower()
        assert "claim" in lower

    def test_teacher_system_reasonably_long(self, teacher_sys):
        """A prompt this detailed should be at least 500 characters."""
        assert len(teacher_sys) >= 500


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM prompt content tests
# ---------------------------------------------------------------------------


class TestAssessorSystemContent:
    def test_assessor_system_identity(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "assessment" in lower or "assessor" in lower or "trainer" in lower

    def test_assessor_system_has_profile_placeholder(self, assessor_sys):
        assert "{profile}" in assessor_sys

    def test_assessor_system_has_conversation_placeholder(self, assessor_sys):
        assert "{conversation}" in assessor_sys

    def test_assessor_system_placeholders_are_valid_format_keys(self, assessor_sys):
        """The two placeholders must survive str.format with dummy values."""
        filled = assessor_sys.format(profile="PROFILE_DATA", conversation="CONVERSATION_DATA")
        assert "PROFILE_DATA" in filled
        assert "CONVERSATION_DATA" in filled

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_system_mentions_tool(self, assessor_sys, tool_name):
        assert tool_name in assessor_sys, (
            f"ASSESSOR_SYSTEM must reference tool '{tool_name}'"
        )

    def test_assessor_system_has_eight_tools_header(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "eight tools" in lower or "8 tools" in lower

    @pytest.mark.parametrize("section_header", ASSESSOR_REQUIRED_SECTIONS)
    def test_assessor_system_has_required_section(self, assessor_sys, section_header):
        assert section_header in assessor_sys, (
            f"ASSESSOR_SYSTEM must contain section '{section_header}'"
        )

    def test_assessor_system_overall_score_format(self, assessor_sys):
        """Score format X/10 must be present."""
        assert "X/10" in assessor_sys or "/10" in assessor_sys

    def test_assessor_system_product_knowledge_accuracy_dimension(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "product knowledge" in lower
        assert "accuracy" in lower

    def test_assessor_system_correct_incorrect_markers(self, assessor_sys):
        """Assessment output format must include ✓ Correct / ✗ Incorrect markers."""
        assert "✓" in assessor_sys or "Correct" in assessor_sys
        assert "✗" in assessor_sys or "Incorrect" in assessor_sys

    def test_assessor_system_partially_correct_marker(self, assessor_sys):
        assert "⚠" in assessor_sys or "Partially" in assessor_sys

    def test_assessor_system_age_last_birthday(self, assessor_sys):
        assert "Age Last Birthday" in assessor_sys or "ALB" in assessor_sys

    def test_assessor_system_get_current_date_first(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "get_current_date" in lower
        assert "first" in lower

    def test_assessor_system_workflow_steps(self, assessor_sys):
        """Workflow must list numbered steps."""
        assert "1." in assessor_sys
        assert "2." in assessor_sys
        assert "3." in assessor_sys

    def test_assessor_system_five_dimensions(self, assessor_sys):
        """Five assessment dimensions must appear."""
        lower = assessor_sys.lower()
        assert "five dimensions" in lower or "5" in assessor_sys

    def test_assessor_system_first_impression(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "first impression" in lower

    def test_assessor_system_needs_discovery(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "needs discovery" in lower or "discovery" in lower

    def test_assessor_system_objection_handling(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "objection" in lower

    def test_assessor_system_closing_technique(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "closing" in lower

    def test_assessor_system_do_not_rely_on_memory(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "memory" in lower or "do not rely" in lower

    def test_assessor_system_roleplay_mention(self, assessor_sys):
        lower = assessor_sys.lower()
        assert "roleplay" in lower

    def test_assessor_system_reasonably_long(self, assessor_sys):
        """A detailed assessor prompt should be at least 800 characters."""
        assert len(assessor_sys) >= 800

    def test_assessor_system_list_products_first_guidance(self, assessor_sys):
        """Prompt should instruct using list_products when unsure of product name."""
        lower = assessor_sys.lower()
        assert "list_products" in lower
        # Should mention using it when unsure
        assert "unsure" in lower or "exact product name" in lower

    def test_assessor_system_no_extra_unexpected_placeholders(self, assessor_sys):
        """Only {profile} and {conversation} should be present as placeholders."""
        import string

        formatter = string.Formatter()
        keys = {field_name for _, field_name, _, _ in formatter.parse(assessor_sys) if field_name}
        assert keys == {"profile", "conversation"}, (
            f"Unexpected placeholders found: {keys - {'profile', 'conversation'}}"
        )


# ---------------------------------------------------------------------------
# Cross-prompt consistency tests
# ---------------------------------------------------------------------------


class TestCrossPromptConsistency:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_both_prompts_mention_tool(self, teacher_sys, assessor_sys, tool_name):
        assert tool_name in teacher_sys, f"TEACHER_SYSTEM missing tool '{tool_name}'"
        assert tool_name in assessor_sys, f"