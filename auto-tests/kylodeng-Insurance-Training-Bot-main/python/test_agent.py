"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, formatting, required sections, tool references,
  citation instructions, age/premium calculation reminder
- ASSESSOR_SYSTEM prompt string: presence, formatting, required sections, tool references,
  placeholder variables ({profile}, {conversation}), assessment dimensions, age/premium reminder
- Module-level constants: existence and correct types
- create_agent import and usage (mocked)
- Prompt content completeness and correctness

Mocks used:
- langchain.agents.create_agent (patched at api.agent module level to avoid real LLM/tool calls)

TODOs:
- TODO: Test actual agent invocation (teacher_agent, assessor_agent) once agent factory
  functions are exposed as public callables in api/agent.py
- TODO: Test astream_events integration for teacher agent once streaming interface is exposed
- TODO: Test ainvoke integration for assessor agent once invocation interface is exposed
- TODO: Test RAG tool wiring (get_current_date, list_products, search_product, etc.)
  once tool objects are importable from api/agent.py
- TODO: Test that ASSESSOR_SYSTEM.format(profile=..., conversation=...) renders correctly
  with realistic synthetic data payloads
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

ASSESSOR_DIMENSIONS = [
    "First Impression",
    "Needs Discovery",
    "Product Knowledge",
    "Objection Handling",
    "Closing Technique",
]

SYNTHETIC_PROFILES = [
    {
        "profile": "Client aged 50, diagnosed with hypertension, looking for whole-life cover.",
        "conversation": "Agent: Good morning! I'd like to introduce Generations II ...",
    },
    {
        "profile": "Young family, two children, seeking health insurance with mainland China cover.",
        "conversation": "Agent: Our Global Network Hospital List covers cashless arrangements ...",
    },
    {
        "profile": "Retiree enquiring about designated hospitals in mainland China.",
        "conversation": "Agent: Sun Life's designated hospital list includes all Class 3 hospitals.",
    },
]


@pytest.fixture(scope="module")
def agent_module():
    """
    Import api.agent with create_agent mocked so no real LangChain/LLM calls happen.
    Returns the imported module object.
    """
    mock_create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    # Provide a minimal stub for the langchain.agents namespace
    langchain_agents_stub = types.ModuleType("langchain.agents")
    langchain_agents_stub.create_agent = mock_create_agent

    langchain_stub = types.ModuleType("langchain")
    langchain_stub.agents = langchain_agents_stub

    with patch.dict(
        sys.modules,
        {
            "langchain": langchain_stub,
            "langchain.agents": langchain_agents_stub,
        },
    ):
        # Force fresh import each module-scoped fixture run
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "api" not in sys.modules:
            sys.modules["api"] = types.ModuleType("api")

        import api.agent as _module  # noqa: PLC0415

        yield _module

        # Cleanup
        sys.modules.pop("api.agent", None)


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM tests
# ---------------------------------------------------------------------------


class TestTeacherSystemPrompt:
    """Tests for the TEACHER_SYSTEM constant."""

    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM must be defined"

    def test_teacher_system_is_string(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_teacher_system_non_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_teacher_system_minimum_length(self, agent_module):
        # A meaningful system prompt should be at least 200 characters
        assert len(agent_module.TEACHER_SYSTEM) >= 200

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_system_references_each_tool(self, agent_module, tool_name):
        assert tool_name in agent_module.TEACHER_SYSTEM, (
            f"TEACHER_SYSTEM must reference tool '{tool_name}'"
        )

    def test_teacher_system_eight_tools_mention(self, agent_module):
        assert "eight" in agent_module.TEACHER_SYSTEM.lower() or "8" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_citation_format(self, agent_module):
        """Prompt must instruct the agent to use [[Sn]] citation markers."""
        assert "[[S" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_citation_example(self, agent_module):
        assert "[[S1]]" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_age_calculation_reminder(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM
        assert "get_current_date" in prompt
        assert "Age Last Birthday" in prompt or "ALB" in prompt

    def test_teacher_system_alb_abbreviation(self, agent_module):
        assert "ALB" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_no_placeholder_variables(self, agent_module):
        """Teacher prompt is static — it should not contain unformatted {placeholders}."""
        import re

        # Allow intentional placeholders only if the prompt is a template
        # TEACHER_SYSTEM is documented as a static prompt, so no {} expected
        placeholders = re.findall(r"\{[a-zA-Z_]+\}", agent_module.TEACHER_SYSTEM)
        assert placeholders == [], (
            f"TEACHER_SYSTEM contains unexpected placeholders: {placeholders}"
        )

    def test_teacher_system_never_guess_instruction(self, agent_module):
        assert "Never guess" in agent_module.TEACHER_SYSTEM or "never guess" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_encouragement_language(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        encouragement_words = ["encouraging", "confidence", "engaging"]
        assert any(w in prompt for w in encouragement_words)

    def test_teacher_system_insurance_trainer_role(self, agent_module):
        assert "insurance" in agent_module.TEACHER_SYSTEM.lower()
        assert "trainer" in agent_module.TEACHER_SYSTEM.lower() or "coach" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_system_discovery_questions_mention(self, agent_module):
        assert "discovery" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_system_premium_calculation_warning(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM.lower()
        assert "premium" in prompt

    def test_teacher_system_policy_inception_mention(self, agent_module):
        assert "policy inception" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_system_get_current_date_first_instruction(self, agent_module):
        """Prompt must say to call get_current_date *first* for date-relative questions."""
        prompt = agent_module.TEACHER_SYSTEM
        idx_tool = prompt.find("get_current_date")
        idx_first = prompt.find("first", idx_tool)
        assert idx_first != -1 and (idx_first - idx_tool) < 200, (
            "get_current_date should be described as called 'first' near its mention"
        )


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM tests
# ---------------------------------------------------------------------------


class TestAssessorSystemPrompt:
    """Tests for the ASSESSOR_SYSTEM constant."""

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM must be defined"

    def test_assessor_system_is_string(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_assessor_system_non_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_assessor_system_minimum_length(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM) >= 200

    def test_assessor_system_has_profile_placeholder(self, agent_module):
        assert "{profile}" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_has_conversation_placeholder(self, agent_module):
        assert "{conversation}" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_format_with_profile_and_conversation(self, agent_module):
        """Template must render without KeyError when both placeholders are supplied."""
        rendered = agent_module.ASSESSOR_SYSTEM.format(
            profile="Test profile", conversation="Test conversation"
        )
        assert "Test profile" in rendered
        assert "Test conversation" in rendered

    @pytest.mark.parametrize("sample", SYNTHETIC_PROFILES)
    def test_assessor_system_format_with_synthetic_data(self, agent_module, sample):
        rendered = agent_module.ASSESSOR_SYSTEM.format(**sample)
        assert sample["profile"] in rendered
        assert sample["conversation"] in rendered

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_system_references_each_tool(self, agent_module, tool_name):
        assert tool_name in agent_module.ASSESSOR_SYSTEM, (
            f"ASSESSOR_SYSTEM must reference tool '{tool_name}'"
        )

    def test_assessor_system_eight_tools_mention(self, agent_module):
        assert "eight" in agent_module.ASSESSOR_SYSTEM.lower() or "8" in agent_module.ASSESSOR_SYSTEM

    @pytest.mark.parametrize("dimension", ASSESSOR_DIMENSIONS)
    def test_assessor_system_contains_assessment_dimension(self, agent_module, dimension):
        assert dimension in agent_module.ASSESSOR_SYSTEM, (
            f"ASSESSOR_SYSTEM must contain assessment dimension: '{dimension}'"
        )

    def test_assessor_system_overall_score_format(self, agent_module):
        assert "Overall Score" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_score_out_of_ten(self, agent_module):
        assert "/10" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_verification_instruction(self, agent_module):
        """Assessor must be told to verify facts using tools, not memory."""
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "verify" in prompt or "verif" in prompt

    def test_assessor_system_do_not_rely_on_memory(self, agent_module):
        assert "memory" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_correct_incorrect_markers(self, agent_module):
        assert "Correct" in agent_module.ASSESSOR_SYSTEM
        assert "Incorrect" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_partially_correct_marker(self, agent_module):
        assert "Partially" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_alb_reminder(self, agent_module):
        assert "ALB" in agent_module.ASSESSOR_SYSTEM or "Age Last Birthday" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_get_current_date_first(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "get_current_date" in prompt
        # Should mention calling it first for age/time verification
        idx = prompt.find("get_current_date")
        surrounding = prompt[max(0, idx - 50): idx + 200]
        assert "first" in surrounding.lower() or "always" in surrounding.lower()

    def test_assessor_system_workflow_steps(self, agent_module):
        """Assessor must have a numbered workflow section."""
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "1." in prompt or "1)" in prompt
        assert "2." in prompt or "2)" in prompt
        assert "3." in prompt or "3)" in prompt

    def test_assessor_system_strengths_section(self, agent_module):
        assert "Strengths" in agent_module.ASSESSOR_SYSTEM or "strength" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_areas_to_improve_section(self, agent_module):
        assert "Areas to Improve" in agent_module.ASSESSOR_SYSTEM or "improve" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_expert_trainer_role(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM.lower()
        assert "expert" in prompt
        assert "trainer" in prompt

    def test_assessor_system_roleplay_context(self, agent_module):
        assert "roleplay" in agent_module.ASSESSOR_SYSTEM.lower() or "role-play" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_policy_inception_mention(self, agent_module):
        assert "policy inception" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_flag_error_instruction(self, agent_module):
        """Assessor should flag incorrect age/premium claims as errors."""
        assert "Flag" in agent_module.ASSESSOR_SYSTEM or "flag" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_format_missing_profile_raises(self, agent_module):
        """Formatting with only one placeholder should raise KeyError."""
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(profile="only profile")

    def test_assessor_system_format_missing_conversation_raises(self, agent_module):
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(conversation="only conversation")

    def test_assessor_system_format_empty_strings(self, agent_module):
        """Template should tolerate empty strings for both placeholders."""
        rendered = agent_module.ASSESSOR_SYSTEM.format(profile="", conversation="")
        assert isinstance(rendered, str)
        assert len(rendered) > 0


# ---------------------------------------------------------------------------
# Module-level constant tests
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Ensure module-level names are present and well-typed."""

    def test_teacher_system_is_module_attribute(self, agent_module):
        assert "TEACHER_SYSTEM" in dir(agent_module)

    def test_assessor_system_is_module_attribute(self, agent_module):
        assert "ASSESSOR_SYSTEM" in dir(agent_module)

    def test_teacher_system_not_none(self, agent_module):
        assert agent_module.TEACHER_SYSTEM is not None

    def test_assessor_system_not_none(self, agent_module):
        assert agent_module.ASSESSOR_SYSTEM is not None

    def test_teacher_and_assessor_are_different(self,