"""
Test module for api/agent.py

What is tested:
  - TEACHER_SYSTEM prompt string: presence, key content sections, citation format instructions
  - ASSESSOR_SYSTEM prompt string: presence, key content sections, placeholder variables,
    scoring format, workflow instructions, tool descriptions
  - Module-level constants: both system prompts are non-empty strings
  - Structural checks: tool names listed in both prompts, required format markers,
    placeholder substitution compatibility

Mocks used:
  - langchain.agents.create_agent is patched at import time to avoid real LLM/agent
    construction (the module calls it at import in the real implementation)
  - No real LLM, vector store, or external service calls are made

TODOs:
  - TODO: Test teacher_agent streaming via astream_events — needs a running LangGraph
    runtime and mocked LLM; add integration test once test fixtures are available.
  - TODO: Test assessor_agent ainvoke — needs a mocked LLM and tool registry.
  - TODO: Test that each RAG tool (get_current_date, list_products, search_product,
    search_all, lookup_hospital_network, compare_plans, lookup_exclusions,
    search_claim_procedure) is registered on both agents — requires agent object exposure.
  - TODO: Test age-last-birthday (ALB) calculation logic if extracted to a helper function.
  - TODO: Parameterised tests for ASSESSOR_SYSTEM profile/conversation substitution with
    real synthetic customer profiles once the profile schema is finalised.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers — patch langchain before importing the module under test so that
# `create_agent(...)` at module level does not blow up.
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


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with langchain.agents.create_agent stubbed out."""
    mock_create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    # Build a minimal fake langchain.agents module so the import succeeds
    fake_langchain_agents = types.ModuleType("langchain.agents")
    fake_langchain_agents.create_agent = mock_create_agent

    fake_langchain = types.ModuleType("langchain")
    fake_langchain.agents = fake_langchain_agents

    with patch.dict(
        sys.modules,
        {
            "langchain": fake_langchain,
            "langchain.agents": fake_langchain_agents,
        },
    ):
        # Force a fresh import even if the module was already loaded
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "api" not in sys.modules:
            sys.modules["api"] = types.ModuleType("api")

        import api.agent as mod

        yield mod


# ---------------------------------------------------------------------------
# Fixtures: expose the two constants for convenience
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ===========================================================================
# 1. Basic type / existence checks
# ===========================================================================


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_str(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_assessor_system_is_str(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_teacher_system_not_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_assessor_system_not_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0


# ===========================================================================
# 2. TEACHER_SYSTEM content checks
# ===========================================================================


class TestTeacherSystemContent:
    def test_contains_role_description(self, teacher_system):
        """The prompt must identify the assistant as an insurance trainer/coach."""
        lower = teacher_system.lower()
        assert "insurance" in lower
        assert "trainer" in lower or "coach" in lower

    def test_mentions_agent_audience(self, teacher_system):
        """Prompt targets a new insurance agent."""
        lower = teacher_system.lower()
        assert "agent" in lower

    def test_citation_format_present(self, teacher_system):
        """Inline citation format [[Sn]] must be documented."""
        assert "[[S" in teacher_system

    def test_citation_example_present(self, teacher_system):
        """A concrete citation example should appear."""
        assert "[[S1]]" in teacher_system

    def test_alb_instruction_present(self, teacher_system):
        """Age Last Birthday instruction is critical for premium accuracy."""
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_get_current_date_instruction(self, teacher_system):
        """Prompt must instruct the agent to call get_current_date first."""
        assert "get_current_date" in teacher_system

    @pytest.mark.parametrize("tool", TOOL_NAMES)
    def test_all_tools_listed(self, teacher_system, tool):
        """Every tool must be listed in the TEACHER_SYSTEM prompt."""
        assert tool in teacher_system, f"Tool '{tool}' not found in TEACHER_SYSTEM"

    def test_never_guess_instruction(self, teacher_system):
        """Prompt must explicitly prohibit guessing product details."""
        assert "Never guess" in teacher_system or "never guess" in teacher_system

    def test_interactive_teaching_mentioned(self, teacher_system):
        """Prompt should encourage interactive / hands-on approach."""
        lower = teacher_system.lower()
        assert "interactive" in lower or "exercise" in lower or "hands-on" in lower

    def test_discovery_questions_mentioned(self, teacher_system):
        """Discovery questions are a key teaching topic."""
        lower = teacher_system.lower()
        assert "discover" in lower or "question" in lower

    def test_prompt_does_not_contain_unresolved_placeholders(self, teacher_system):
        """TEACHER_SYSTEM should have no {placeholder} variables."""
        import re

        placeholders = re.findall(r"\{[^}]+\}", teacher_system)
        assert placeholders == [], f"Unexpected placeholders found: {placeholders}"

    def test_eight_tools_mentioned(self, teacher_system):
        """Prompt should reference eight tools."""
        assert "eight" in teacher_system.lower() or "8" in teacher_system

    def test_lookup_hospital_network_use_case(self, teacher_system):
        """Hospital lookup tool should explain its use case."""
        assert "hospital" in teacher_system.lower()

    def test_compare_plans_attributes_mentioned(self, teacher_system):
        """compare_plans tool description should mention at least one attribute."""
        lower = teacher_system.lower()
        assert (
            "deductible" in lower
            or "annual limit" in lower
            or "room" in lower
        )

    def test_search_claim_procedure_mentioned(self, teacher_system):
        """Claim procedure tool reference must exist."""
        assert "claim" in teacher_system.lower()


# ===========================================================================
# 3. ASSESSOR_SYSTEM content checks
# ===========================================================================


class TestAssessorSystemContent:
    def test_contains_role_description(self, assessor_system):
        lower = assessor_system.lower()
        assert "assessment" in lower or "assess" in lower
        assert "trainer" in lower or "training" in lower or "trainee" in lower

    def test_profile_placeholder_present(self, assessor_system):
        """{profile} must be present for runtime substitution."""
        assert "{profile}" in assessor_system

    def test_conversation_placeholder_present(self, assessor_system):
        """{conversation} must be present for runtime substitution."""
        assert "{conversation}" in assessor_system

    def test_overall_score_format_present(self, assessor_system):
        """Assessor output must include an Overall Score marker."""
        assert "## Overall Score" in assessor_system

    def test_five_dimensions_present(self, assessor_system):
        """All five assessment dimensions must be listed."""
        assert "First Impression" in assessor_system
        assert "Needs Discovery" in assessor_system
        assert "Product Knowledge" in assessor_system
        assert "Objection Handling" in assessor_system
        assert "Closing Technique" in assessor_system

    def test_correct_incorrect_markers_present(self, assessor_system):
        """Scoring markers for factual claims must be present."""
        assert "✓ Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system
        assert "⚠ Partially correct" in assessor_system or "Partially correct" in assessor_system

    def test_strengths_section_present(self, assessor_system):
        assert "Key Strengths" in assessor_system or "Strengths" in assessor_system

    def test_areas_to_improve_section_present(self, assessor_system):
        assert "Areas to Improve" in assessor_system or "Improve" in assessor_system

    def test_workflow_section_present(self, assessor_system):
        lower = assessor_system.lower()
        assert "workflow" in lower

    def test_alb_instruction_present(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_get_current_date_instruction(self, assessor_system):
        assert "get_current_date" in assessor_system

    @pytest.mark.parametrize("tool", TOOL_NAMES)
    def test_all_tools_listed(self, assessor_system, tool):
        assert tool in assessor_system, f"Tool '{tool}' not found in ASSESSOR_SYSTEM"

    def test_verify_claims_instruction(self, assessor_system):
        """Assessor must be told to verify factual claims via tools, not memory."""
        lower = assessor_system.lower()
        assert "verif" in lower  # verify / verification / verified

    def test_do_not_rely_on_memory(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower or "do not rely" in lower

    def test_list_products_first_instruction(self, assessor_system):
        """Assessor should be told to call list_products if unsure of product name."""
        assert "list_products" in assessor_system

    def test_only_two_placeholders(self, assessor_system):
        """Exactly two format placeholders: {profile} and {conversation}."""
        import re

        placeholders = set(re.findall(r"\{([^}]+)\}", assessor_system))
        assert placeholders == {"profile", "conversation"}, (
            f"Unexpected placeholders: {placeholders}"
        )

    def test_dimension_score_format(self, assessor_system):
        """Each dimension heading should have a (X/10) score placeholder."""
        import re

        # e.g. "### 1. First Impression & Rapport Building (X/10)"
        matches = re.findall(r"\(X/10\)", assessor_system)
        # There should be at least 5 dimension scores + 1 overall
        assert len(matches) >= 5, f"Expected at least 5 (X/10) markers, found {len(matches)}"

    def test_numbered_workflow_steps(self, assessor_system):
        """Workflow should contain numbered steps."""
        assert "1." in assessor_system
        assert "2." in assessor_system
        assert "3." in assessor_system


# ===========================================================================
# 4. Placeholder substitution (template behaviour)
# ===========================================================================


class TestAssessorSystemTemplating:
    SYNTHETIC_PROFILE = (
        "Customer: Female, age 35, two young children, interested in whole life "
        "insurance, budget HKD 5,000/year, concerned about critical illness cover."
    )

    SYNTHETIC_CONVERSATION = (
        "Agent: Good morning! My name is Alex. How can I help you today?\n"
        "Customer: I heard about Generations II and wanted to know more.\n"
        "Agent: Generations II is a whole life plan with guaranteed lifelong protection "
        "and double bonuses. The annual deductible is HKD 3,000.\n"
        "Customer: Does it cover mental illness?\n"
        "Agent: Yes, it includes a mental incapacity benefit.\n"
    )

    def test_format_substitution_succeeds(self, assessor_system):
        """ASSESSOR_SYSTEM.format() with profile and conversation should not raise."""
        result = assessor_system.format(
            profile=self.SYNTHETIC_PROFILE,
            conversation=self.SYNTHETIC_CONVERSATION,
        )
        assert isinstance(result, str)

    def test_profile_appears_in_formatted_string(self, assessor_system):
        result = assessor_system.format(
            profile=self.SYNTHETIC_PROFILE,
            conversation=self.SYNTHETIC_CONVERSATION,
        )
        assert "Female, age 35" in result

    def test_conversation_appears_in_formatted_string(self, assessor_system):
        result = assessor_system.format(
            profile=self.SYNTHETIC_PROFILE,
            conversation=self.SYNTHETIC_CONVERSATION,
        )
        assert "Generations II" in result

    def test_format_with_empty_profile(self, assessor_system):
        """Empty profile string should still produce a valid formatted prompt."""
        result = assessor_system.format(profile="", conversation=self.SYNTHETIC_CONVERSATION)
        assert "{profile}" not in result

    def test_format_with_empty_conversation(self, assessor_system):
        result = assessor_system.format(profile=self.SYNTHETIC_PROFILE, conversation="")
        assert "{conversation}" not in result

    def test_format_with_special_characters_in_profile(self, assessor_system):
        """Profile data may contain special chars — substitution must not crash."""
        profile_with_specials = "Customer: O'Brien & Sons — HKD 10,000/yr; age: 42"
        result = assessor_system.format(
            profile=profile_with_specials,
            conversation=self.SYNTHETIC_CONVERSATION,
        )
        assert "O'Brien" in result

    def test_format_missing_placeholder_raises_key_error(self, assessor_system):
        """Calling format() without required keys should raise KeyError."""
        with pytest.raises(KeyError):
            assessor_system.format(profile=self.SYNTHETIC_PROFILE)  # missing conversation

    def test_format_extra_kwargs_not_allowed_by_default(self, assessor_system):
        """str.format() with extra unused keys is silently ignored — assert no crash."""
        # Python str.format ignores extra keyword arguments
        result = assessor_system.format(
            profile=self.SYNTHETIC_PROFILE,
            conversation=self.SYNTHETIC_CONVERSATION,
            extra_key="should_be_ignored",
        )
        assert isinstance(result, str)


# ===========================================================================
# 5. create_agent mock interaction
# ===========================================================================


class TestCreateAgentMockCalled:
    """Verify that the module imports cleanly with create_agent mocked."""

    def test_module_imports_without_error(self, agent_module):
        assert agent_module is not None

    def test_teacher_system_accessible_after_import(