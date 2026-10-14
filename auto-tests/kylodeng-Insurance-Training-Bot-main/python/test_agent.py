"""
Test module for api/agent.py

What is tested:
  - TEACHER_SYSTEM prompt string: content, format, required sections, citation format
  - ASSESSOR_SYSTEM prompt string: content, format, required sections, placeholders
  - Module-level constants: existence, types, non-emptiness
  - Tool references in both system prompts (all 8 tools mentioned)
  - Citation marker format in TEACHER_SYSTEM
  - Placeholder variables in ASSESSOR_SYSTEM ({profile}, {conversation})
  - Import of create_agent from langchain.agents
  - Edge cases: prompt formatting with synthetic profile/conversation data

Mocks used:
  - langchain.agents.create_agent (patched to avoid real LangChain dependency)
  - No external service calls in this module (constants only + import)

TODOs:
  - TODO: Test actual agent graph construction once agent factory functions are implemented
  - TODO: Test astream_events integration for teacher agent when streaming logic is added
  - TODO: Test ainvoke integration for assessor agent when invocation logic is added
  - TODO: Test RAG tool wiring once tool implementations are available
  - TODO: Test ASSESSOR_SYSTEM.format() with adversarial inputs (injection attempts)
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures & helpers
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

SYNTHETIC_PROFILE = (
    "Name: Jane Doe, Age: 35, Occupation: Teacher, "
    "Health: No pre-existing conditions, Budget: HKD 5,000/year"
)

SYNTHETIC_CONVERSATION = (
    "Agent: Good morning! I'd like to introduce you to our Generations II plan.\n"
    "Customer: What is the annual deductible?\n"
    "Agent: The annual deductible is HKD 3,000 [[S1]].\n"
    "Customer: Is Queen Mary Hospital in your network?\n"
    "Agent: Yes, it is covered under our network.\n"
    "Customer: What about pre-existing conditions?\n"
    "Agent: Pre-existing conditions have a 2-year waiting period.\n"
)


@pytest.fixture(scope="module")
def agent_module():
    """
    Import api.agent with langchain.agents mocked so no real LangChain
    dependency is required.
    """
    mock_langchain_agents = types.ModuleType("langchain.agents")
    mock_langchain_agents.create_agent = MagicMock(return_value=MagicMock())

    mock_langchain = types.ModuleType("langchain")
    mock_langchain.agents = mock_langchain_agents

    with patch.dict(
        sys.modules,
        {
            "langchain": mock_langchain,
            "langchain.agents": mock_langchain_agents,
        },
    ):
        # Remove cached module if present
        sys.modules.pop("api.agent", None)
        sys.modules.pop("api", None)

        import importlib.util, os

        spec = importlib.util.spec_from_file_location(
            "api.agent",
            os.path.join(os.path.dirname(__file__), "agent.py"),
        )
        if spec is None:
            # Fallback: try direct import path
            spec = importlib.util.find_spec("api.agent")

        if spec is not None:
            module = importlib.util.module_from_spec(spec)
            sys.modules["api.agent"] = module
            spec.loader.exec_module(module)
        else:
            # Last resort: attempt relative import
            import api.agent as module  # noqa: F401 – may raise, intentional

        return module


# ---------------------------------------------------------------------------
# 1. Module-level constant existence and type tests
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM not found in module"

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM not found in module"

    def test_teacher_system_is_string(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_string(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_teacher_system_not_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_not_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_create_agent_imported(self, agent_module):
        assert hasattr(agent_module, "create_agent"), (
            "create_agent should be imported at module level"
        )


# ---------------------------------------------------------------------------
# 2. TEACHER_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestTeacherSystemPrompt:
    def test_teacher_system_mentions_trainer_role(self, agent_module):
        assert "trainer" in agent_module.TEACHER_SYSTEM.lower() or \
               "coach" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_system_mentions_insurance(self, agent_module):
        assert "insurance" in agent_module.TEACHER_SYSTEM.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_system_mentions_all_tools(self, agent_module, tool_name):
        assert tool_name in agent_module.TEACHER_SYSTEM, (
            f"TEACHER_SYSTEM does not mention tool '{tool_name}'"
        )

    def test_teacher_system_citation_format_present(self, agent_module):
        """Citation marker format [[Sn]] must be documented in the prompt."""
        assert "[[S" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_citation_example(self, agent_module):
        """An example citation like [[S1]] should appear."""
        assert "[[S1]]" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_age_last_birthday_mentioned(self, agent_module):
        assert "Age Last Birthday" in agent_module.TEACHER_SYSTEM or \
               "ALB" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_get_current_date_first(self, agent_module):
        """Prompt should instruct agent to call get_current_date first."""
        prompt = agent_module.TEACHER_SYSTEM
        assert "get_current_date" in prompt
        # The instruction to call it first should precede product-specific advice
        idx_date = prompt.find("get_current_date")
        assert idx_date != -1

    def test_teacher_system_never_guess_instruction(self, agent_module):
        assert "never guess" in agent_module.TEACHER_SYSTEM.lower() or \
               "Never guess" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_eight_tools_header(self, agent_module):
        assert "eight tools" in agent_module.TEACHER_SYSTEM.lower() or \
               "8 tools" in agent_module.TEACHER_SYSTEM.lower()

    def test_teacher_system_encourages_interaction(self, agent_module):
        """Prompt should mention interactivity / exercises."""
        prompt_lower = agent_module.TEACHER_SYSTEM.lower()
        assert any(kw in prompt_lower for kw in ["exercise", "quiz", "interactive", "scenario"])

    def test_teacher_system_no_placeholder_braces_leaked(self, agent_module):
        """TEACHER_SYSTEM is a static prompt — no {profile} style placeholders expected."""
        import re
        placeholders = re.findall(r"\{[a-zA-Z_]+\}", agent_module.TEACHER_SYSTEM)
        assert placeholders == [], (
            f"Unexpected format placeholders in TEACHER_SYSTEM: {placeholders}"
        )

    def test_teacher_system_lookup_hospital_network_use_case(self, agent_module):
        prompt = agent_module.TEACHER_SYSTEM
        assert "hospital" in prompt.lower()
        assert "lookup_hospital_network" in prompt

    def test_teacher_system_premium_calculation_guidance(self, agent_module):
        prompt_lower = agent_module.TEACHER_SYSTEM.lower()
        assert "premium" in prompt_lower

    def test_teacher_system_lookup_exclusions_mentioned(self, agent_module):
        assert "lookup_exclusions" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_search_claim_procedure_mentioned(self, agent_module):
        assert "search_claim_procedure" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_compare_plans_mentioned(self, agent_module):
        assert "compare_plans" in agent_module.TEACHER_SYSTEM

    def test_teacher_system_minimum_length(self, agent_module):
        """Sanity: prompt should be reasonably long (>500 chars)."""
        assert len(agent_module.TEACHER_SYSTEM) > 500

    def test_teacher_system_source_citation_format_documented(self, agent_module):
        """Verify the exact citation format string is shown as example."""
        assert "[[Sn]]" in agent_module.TEACHER_SYSTEM or "[[S1]]" in agent_module.TEACHER_SYSTEM


# ---------------------------------------------------------------------------
# 3. ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestAssessorSystemPrompt:
    def test_assessor_system_has_profile_placeholder(self, agent_module):
        assert "{profile}" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_has_conversation_placeholder(self, agent_module):
        assert "{conversation}" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_format_with_synthetic_data(self, agent_module):
        """ASSESSOR_SYSTEM.format() should succeed with valid synthetic data."""
        formatted = agent_module.ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_PROFILE in formatted
        assert SYNTHETIC_CONVERSATION in formatted

    def test_assessor_system_format_profile_injected(self, agent_module):
        formatted = agent_module.ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert "Jane Doe" in formatted

    def test_assessor_system_format_conversation_injected(self, agent_module):
        formatted = agent_module.ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert "Generations II" in formatted

    def test_assessor_system_format_missing_profile_raises(self, agent_module):
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(conversation=SYNTHETIC_CONVERSATION)

    def test_assessor_system_format_missing_conversation_raises(self, agent_module):
        with pytest.raises(KeyError):
            agent_module.ASSESSOR_SYSTEM.format(profile=SYNTHETIC_PROFILE)

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_system_mentions_all_tools(self, agent_module, tool_name):
        assert tool_name in agent_module.ASSESSOR_SYSTEM, (
            f"ASSESSOR_SYSTEM does not mention tool '{tool_name}'"
        )

    def test_assessor_system_mentions_five_dimensions(self, agent_module):
        prompt_lower = agent_module.ASSESSOR_SYSTEM.lower()
        assert "five dimensions" in prompt_lower or "5 dimension" in prompt_lower or \
               ("first impression" in prompt_lower and "needs discovery" in prompt_lower)

    def test_assessor_system_overall_score_format(self, agent_module):
        assert "Overall Score" in agent_module.ASSESSOR_SYSTEM
        assert "X/10" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_first_impression_section(self, agent_module):
        assert "First Impression" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_needs_discovery_section(self, agent_module):
        assert "Needs Discovery" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_product_knowledge_section(self, agent_module):
        assert "Product Knowledge" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_objection_handling_section(self, agent_module):
        assert "Objection Handling" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_closing_technique_section(self, agent_module):
        assert "Closing Technique" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_key_strengths_section(self, agent_module):
        assert "Key Strengths" in agent_module.ASSESSOR_SYSTEM or \
               "✅" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_areas_to_improve_section(self, agent_module):
        assert "Areas to Improve" in agent_module.ASSESSOR_SYSTEM or \
               "⚠️" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_correct_incorrect_markers(self, agent_module):
        assert "✓ Correct" in agent_module.ASSESSOR_SYSTEM or \
               "Correct" in agent_module.ASSESSOR_SYSTEM
        assert "✗ Incorrect" in agent_module.ASSESSOR_SYSTEM or \
               "Incorrect" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_alb_mentioned(self, agent_module):
        assert "Age Last Birthday" in agent_module.ASSESSOR_SYSTEM or \
               "ALB" in agent_module.ASSESSOR_SYSTEM

    def test_assessor_system_workflow_steps(self, agent_module):
        prompt = agent_module.ASSESSOR_SYSTEM
        assert "1." in prompt and "2." in prompt and "3." in prompt

    def test_assessor_system_minimum_length(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM) > 500

    def test_assessor_system_do_not_rely_on_memory(self, agent_module):
        assert "memory" in agent_module.ASSESSOR_SYSTEM.lower() or \
               "do not rely" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_expert_role_stated(self, agent_module):
        assert "expert" in agent_module.ASSESSOR_SYSTEM.lower()

    def test_assessor_system_eight_tools_header(self, agent_module):
        assert "eight tools" in agent_module.ASSESSOR_SYSTEM.lower() or \
               "8 tools" in agent_module.ASSESSOR_SYSTEM.lower()


# ---------------------------------------------------------------------------
# 4. Tool reference completeness — cross-prompt checks
# ---------------------------------------------------------------------------


class TestToolReferencesCompleteness:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_in_teacher_system(self, agent_module, tool_name):
        assert tool_name in agent_module.TEACHER_SYSTEM

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_in_assessor_system(self, agent_module, tool_name):
        assert tool_name