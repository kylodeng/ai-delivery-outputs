"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content, required sections, tool mentions, citation format
- ASSESSOR_SYSTEM prompt string: presence, content, required sections, tool mentions, format markers
- Module-level constants: type checks, non-empty, expected substrings
- Edge cases: placeholder formatting in ASSESSOR_SYSTEM ({profile}, {conversation})
- create_agent import / usage surface (mocked)
- Boundary/negative cases: missing placeholders, unexpected content

Mocks used:
- langchain.agents.create_agent (patched to prevent real LLM/agent instantiation)

TODOs:
- TODO: test actual agent graph execution (requires LangGraph runtime + LLM credentials)
- TODO: test astream_events behaviour of teacher agent (requires async event loop + real tools)
- TODO: test ainvoke behaviour of assessor agent (requires full roleplay session fixture)
- TODO: test RAG tool integration (requires knowledge-base / vector-store setup)
- TODO: test ASSESSOR_SYSTEM with real profile/conversation substitution end-to-end
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – import the module under test with create_agent mocked so that
# importing api.agent never tries to call a real LLM.
# ---------------------------------------------------------------------------

AGENT_MODULE_PATH = "api.agent"


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with langchain.agents.create_agent patched."""
    mock_create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    # Build a minimal fake langchain.agents module
    fake_langchain = types.ModuleType("langchain")
    fake_langchain_agents = types.ModuleType("langchain.agents")
    fake_langchain_agents.create_agent = mock_create_agent
    fake_langchain.agents = fake_langchain_agents

    with patch.dict(
        sys.modules,
        {
            "langchain": fake_langchain,
            "langchain.agents": fake_langchain_agents,
        },
    ):
        # Force fresh import
        if AGENT_MODULE_PATH in sys.modules:
            del sys.modules[AGENT_MODULE_PATH]
        module = importlib.import_module(AGENT_MODULE_PATH)
        yield module


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM tests
# ---------------------------------------------------------------------------


class TestTeacherSystem:
    """Tests for the TEACHER_SYSTEM prompt constant."""

    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM not defined"

    def test_teacher_system_is_string(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_teacher_system_is_not_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_teacher_system_is_substantial(self, agent_module):
        """Prompt should be more than a one-liner."""
        assert len(agent_module.TEACHER_SYSTEM) > 200

    # --- Role description ---------------------------------------------------

    def test_teacher_system_contains_role_description(self, agent_module):
        assert "insurance sales trainer" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_system_mentions_agent(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert "agent" in prompt

    def test_teacher_system_is_encouraging(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert any(
            word in prompt for word in ["encouraging", "confidence", "engaging"]
        )

    # --- Tool enumeration ---------------------------------------------------

    REQUIRED_TOOLS = [
        "get_current_date",
        "list_products",
        "search_product",
        "search_all",
        "lookup_hospital_network",
        "compare_plans",
        "lookup_exclusions",
        "search_claim_procedure",
    ]

    @pytest.mark.parametrize("tool_name", REQUIRED_TOOLS)
    def test_teacher_system_mentions_tool(self, agent_module, tool_name):
        assert tool_name in agent_module.TEACHER_SYSTEM, (
            f"TEACHER_SYSTEM should mention tool '{tool_name}'"
        )

    def test_teacher_system_has_eight_tools(self, agent_module):
        """The docstring and prompt claim eight tools."""
        count = sum(
            1
            for tool in self.REQUIRED_TOOLS
            if tool in agent_module.TEACHER_SYSTEM
        )
        assert count == 8

    # --- Age / premium guidance ---------------------------------------------

    def test_teacher_system_mentions_age_last_birthday(self, agent_module):
        assert "Age Last Birthday" in agent_module.TEACHER_SYSTEM or \
               "ALB" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_warns_about_age_calculation(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert "age" in prompt
        assert "premium" in prompt

    def test_teacher_system_get_current_date_first(self, agent_module):
        """Prompt must instruct to call get_current_date first for date-relative questions."""
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert "get_current_date" in prompt
        # Should appear before the age-calculation section
        idx_tool = agent_module.TEACHER_SYSTEM.find("get_current_date")
        idx_alb = agent_module.TEACHER_SYSTEM.find("ALB")
        assert idx_tool < idx_alb, (
            "get_current_date should be introduced before the ALB discussion"
        )

    # --- Citation format ----------------------------------------------------

    def test_teacher_system_contains_citation_instruction(self, agent_module):
        assert "[[S" in agent_module.TEACHER_SYSTEM or "citation" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_system_citation_example_format(self, agent_module):
        """The prompt must show the [[Sn]] marker pattern."""
        assert "[[S" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_citation_example_contains_source_id(self, agent_module):
        assert "[[S1]]" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_never_guess_instruction(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert "never guess" in prompt

    # --- Behavioural guidance -----------------------------------------------

    def test_teacher_system_interactive(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert any(
            kw in prompt for kw in ["quiz", "exercise", "scenario", "interactive"]
        )

    def test_teacher_system_no_template_placeholders(self, agent_module):
        """TEACHER_SYSTEM should NOT have unresolved {placeholder} variables."""
        import re
        placeholders = re.findall(r"\{[a-zA-Z_]+\}", agent_module.TEACHER_SYSTEM)
        assert placeholders == [], (
            f"Unexpected placeholders in TEACHER_SYSTEM: {placeholders}"
        )


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM tests
# ---------------------------------------------------------------------------


class TestAssessorSystem:
    """Tests for the ASSESSOR_SYSTEM prompt constant."""

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM not defined"

    def test_assessor_system_is_string(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_assessor_system_is_not_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_assessor_system_is_substantial(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM) > 200

    # --- Role description ---------------------------------------------------

    def test_assessor_system_contains_role_description(self, agent_module):
        assert "insurance sales trainer" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_mentions_assessment(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "assessment" in prompt or "assess" in prompt

    def test_assessor_system_mentions_roleplay(self, agent_module):
        assert "roleplay" in agent_module.ASSESSOR_SYSTEM.lower()

    # --- Template placeholders ----------------------------------------------

    def test_assessor_system_has_profile_placeholder(self, agent_module):
        assert "{profile}" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_has_conversation_placeholder(self, agent_module):
        assert "{conversation}" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_placeholders_only_expected(self, agent_module):
        """Only {profile} and {conversation} should appear as format placeholders."""
        import re
        placeholders = set(re.findall(r"\{([a-zA-Z_]+)\}", agent_module.ASSESSOR_SYSTEM))
        allowed = {"profile", "conversation"}
        unexpected = placeholders - allowed
        assert unexpected == set(), (
            f"Unexpected placeholders found: {unexpected}"
        )

    def test_assessor_system_format_substitution(self, agent_module):
        """format() with profile + conversation should succeed without KeyError."""
        sample_profile = "Female, 35, non-smoker, looking for health cover"
        sample_conversation = "Agent: Good morning! Customer: Hi, I need insurance."
        rendered = agent_module.ASSESSOR_SYSTEM.format(
            profile=sample_profile,
            conversation=sample_conversation,
        )
        assert sample_profile in rendered
        assert sample_conversation in rendered

    def test_assessor_system_format_substitution_missing_profile_raises(self, agent_module):
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(conversation="some chat")

    def test_assessor_system_format_substitution_missing_conversation_raises(self, agent_module):
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(profile="some profile")

    # --- Tool enumeration ---------------------------------------------------

    REQUIRED_TOOLS = [
        "get_current_date",
        "list_products",
        "search_product",
        "search_all",
        "lookup_hospital_network",
        "compare_plans",
        "lookup_exclusions",
        "search_claim_procedure",
    ]

    @pytest.mark.parametrize("tool_name", REQUIRED_TOOLS)
    def test_assessor_system_mentions_tool(self, agent_module, tool_name):
        assert tool_name in agent_module.ASSESSOR_SYSTEM, (
            f"ASSESSOR_SYSTEM should mention tool '{tool_name}'"
        )

    def test_assessor_system_has_eight_tools(self, agent_module):
        count = sum(
            1
            for tool in self.REQUIRED_TOOLS
            if tool in agent_module.ASSESSOR_SYSTEM
        )
        assert count == 8

    # --- Five assessment dimensions -----------------------------------------

    REQUIRED_DIMENSIONS = [
        "First Impression",
        "Needs Discovery",
        "Product Knowledge",
        "Objection Handling",
        "Closing",
    ]

    @pytest.mark.parametrize("dimension", REQUIRED_DIMENSIONS)
    def test_assessor_system_contains_dimension(self, agent_module, dimension):
        assert dimension in agent_module.ASSESSOR_SYSTEM, (
            f"ASSESSOR_SYSTEM should mention assessment dimension '{dimension}'"
        )

    def test_assessor_system_has_five_numbered_sections(self, agent_module):
        import re
        sections = re.findall(r"###\s+\d\.", agent_module.ASSESSOR_SYSTEM)
        assert len(sections) == 5, (
            f"Expected 5 numbered assessment sections, found {len(sections)}"
        )

    # --- Score format -------------------------------------------------------

    def test_assessor_system_contains_overall_score_marker(self, agent_module):
        assert "Overall Score" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_score_format(self, agent_module):
        """The overall score marker should follow the '## Overall Score: X/10' pattern."""
        assert "X/10" in agent_module.ASSESSOR_SYSTEM or "/10" in agent_module.ASSESSOR_SYSTEM

    # --- Workflow steps -----------------------------------------------------

    def test_assessor_system_has_workflow(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "workflow" in prompt or "step" in prompt

    def test_assessor_system_verify_with_tools(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "verify" in prompt or "verif" in prompt

    def test_assessor_system_instructs_not_to_rely_on_memory(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "memory" in prompt or "do not rely" in prompt or "not rely" in prompt

    # --- Accuracy markers ---------------------------------------------------

    def test_assessor_system_correct_marker(self, agent_module):
        assert "✓ Correct" in agent_module.ASSESSOR_SYSTEM or "Correct" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_incorrect_marker(self, agent_module):
        assert "✗ Incorrect" in agent_module.ASSESSOR_SYSTEM or "Incorrect" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_partially_correct_marker(self, agent_module):
        assert "Partially correct" in agent_module.ASSESSOR_SYSTEM or "⚠" in agent_module.ASSESSOR_SYSTEM

    # --- Age / premium guidance ---------------------------------------------

    def test_assessor_system_mentions_age_last_birthday(self, agent_module):
        assert "Age Last Birthday" in agent_module.ASSESSOR_SYSTEM or \
               "ALB" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_get_current_date_first(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "get_current_date" in prompt

    # --- Output sections ----------------------------------------------------

    def test_assessor_system_key_strengths_section(self, agent_module):
        assert "Key Strengths" in agent_module.ASSESSOR_SYSTEM or \
               "Strengths" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_areas_to_improve_section(self, agent_module):
        assert "Areas to Improve" in agent_module.ASSESSOR_SYSTEM or \
               "Improve" in agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Cross-prompt consistency tests
# ---------------------------------------------------------------------------


class TestPromptConsistency:
    """Tests that ensure TEACHER_SYSTEM and ASSESSOR_SYSTEM are consistent."""

    REQUIRED_TOOLS = [
        "get_current_date",
        "list_products",
        "search_product",
        "search_all",
        "lookup_hospital_network",
        "compare_plans",
        "lookup_exclusions",
        "search_claim_procedure",
    ]

    @pytest.mark.parametrize("tool_name", REQUIRED_TOOLS)
    def test_both_prompts_mention_tool(self, agent_module, tool_name):
        assert tool_name in agent_module.TEACHER_SYSTEM, (
            f"TEACHER_SYSTEM missing tool: {