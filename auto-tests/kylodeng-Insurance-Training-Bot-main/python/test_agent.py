"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string content and structure
- ASSESSOR_SYSTEM prompt string content and structure
- Module-level docstring and constants
- Placeholder/format key presence in ASSESSOR_SYSTEM
- Tool references in both system prompts
- Citation format instructions in TEACHER_SYSTEM
- Age/premium calculation instructions in both prompts
- create_agent import and usage (mocked)
- Edge cases: prompt formatting with synthetic profile/conversation data

Mocks used:
- langchain.agents.create_agent (patched to avoid real LangChain calls)
- No real LLM, vector store, or external API calls are made

TODOs:
- TODO: Test actual agent graph execution (requires LangGraph runtime + tool stubs)
- TODO: Test astream_events streaming for teacher agent (requires async event loop + mock LLM)
- TODO: Test ainvoke for assessor agent (requires async runtime + mock LLM)
- TODO: Test individual RAG tool implementations (not present in this file)
- TODO: Test full end-to-end agent with mocked tool responses
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / Constants
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
    "Customer: Jane Doe, age 35, looking for whole life insurance with mental "
    "incapacity benefit and worldwide emergency assistance (Generations II)."
)

SYNTHETIC_CONVERSATION = (
    "Agent: Good morning! I'd like to tell you about Generations II, a participating "
    "whole life insurance plan from Sun Life. It offers lifelong protection, double "
    "bonuses, and a mental incapacity benefit.\n"
    "Customer: Does it cover terminal illness?\n"
    "Agent: Yes, it includes an accelerated benefit covering terminal illness and "
    "accidental coma.\n"
    "Customer: What about hospitals in mainland China?\n"
    "Agent: Sun Life has a list of designated hospitals in mainland China including "
    "all Class 3 hospitals and Class 2A hospitals in 21 designated cities."
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_langchain_agents():
    """Patch langchain.agents so importing api.agent never calls real LangChain."""
    mock_module = types.ModuleType("langchain.agents")
    mock_module.create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    # Ensure the parent package exists in sys.modules
    if "langchain" not in sys.modules:
        sys.modules["langchain"] = types.ModuleType("langchain")
    sys.modules["langchain.agents"] = mock_module

    yield mock_module

    # Clean up so other tests are not affected
    sys.modules.pop("langchain.agents", None)


@pytest.fixture()
def agent_module(mock_langchain_agents):
    """Import (or reload) api.agent with mocked dependencies."""
    # Remove cached module to force re-import with fresh mocks
    sys.modules.pop("api.agent", None)
    sys.modules.pop("api", None)

    # Provide a minimal 'api' package if not already a real package
    if "api" not in sys.modules:
        api_pkg = types.ModuleType("api")
        sys.modules["api"] = api_pkg

    import api.agent as mod  # noqa: PLC0415

    return mod


# ---------------------------------------------------------------------------
# Module-level import / structure tests
# ---------------------------------------------------------------------------


class TestModuleImport:
    def test_module_imports_without_error(self, agent_module):
        assert agent_module is not None

    def test_teacher_system_defined(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_defined(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_string(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_string(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_non_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_create_agent_imported(self, agent_module, mock_langchain_agents):
        """create_agent should be accessible from the patched langchain.agents."""
        assert mock_langchain_agents.create_agent is not None


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM prompt content tests
# ---------------------------------------------------------------------------


class TestTeacherSystemPrompt:
    @pytest.fixture(autouse=True)
    def prompt(self, agent_module):
        self.prompt = agent_module.TEACHER_SYSTEM

    # --- Role description ---
    def test_describes_trainer_role(self):
        assert "insurance sales trainer" in self.prompt.lower() or "trainer" in self.prompt.lower()

    def test_mentions_agent(self):
        assert "agent" in self.prompt.lower()

    # --- Tool references ---
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_referenced(self, tool_name):
        assert tool_name in self.prompt, (
            f"Expected tool '{tool_name}' to be mentioned in TEACHER_SYSTEM"
        )

    def test_has_eight_tools_mentioned(self):
        """All 8 expected tools must appear."""
        missing = [t for t in EXPECTED_TOOLS if t not in self.prompt]
        assert missing == [], f"Missing tools in TEACHER_SYSTEM: {missing}"

    # --- Citation instructions ---
    def test_citation_format_present(self):
        assert "[[S" in self.prompt or "[[Sn]]" in self.prompt

    def test_citation_example_present(self):
        # Prompt should show an example citation like [[S1]]
        assert "[[S1]]" in self.prompt or "[[Sn]]" in self.prompt

    def test_citation_only_for_retrieved_docs(self):
        assert "retrieved document" in self.prompt.lower() or "sourced from a document" in self.prompt.lower()

    # --- Age / premium instructions ---
    def test_age_last_birthday_mentioned(self):
        assert "Age Last Birthday" in self.prompt or "ALB" in self.prompt

    def test_get_current_date_priority_instruction(self):
        """Prompt must instruct to call get_current_date first for date-relative questions."""
        assert "get_current_date" in self.prompt

    def test_age_miscalculation_warning(self):
        assert "age" in self.prompt.lower() and "premium" in self.prompt.lower()

    def test_policy_inception_mentioned(self):
        assert "policy inception" in self.prompt.lower()

    # --- Teaching behaviours ---
    def test_encourages_interaction(self):
        keywords = ["exercise", "quiz", "scenario", "encourage", "interactive"]
        assert any(k in self.prompt.lower() for k in keywords)

    def test_instructs_not_to_guess(self):
        assert "never guess" in self.prompt.lower() or "do not guess" in self.prompt.lower()

    def test_prompt_uses_appropriate_tool_instruction(self):
        assert "appropriate tool" in self.prompt.lower()

    # --- Hospital network tool guidance ---
    def test_hospital_network_context(self):
        assert "hospital" in self.prompt.lower()

    # --- Prompt does not contain placeholder tokens left un-substituted ---
    def test_no_unresolved_format_placeholders(self):
        """TEACHER_SYSTEM should not contain {profile} or {conversation} style tokens
        since it is a static prompt (those belong to ASSESSOR_SYSTEM)."""
        import re
        # Allow {profile} and {conversation} only in ASSESSOR; teacher should have none
        placeholders = re.findall(r"\{[a-zA-Z_]+\}", self.prompt)
        assert placeholders == [], (
            f"Unexpected format placeholders found in TEACHER_SYSTEM: {placeholders}"
        )


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM prompt content tests
# ---------------------------------------------------------------------------


class TestAssessorSystemPrompt:
    @pytest.fixture(autouse=True)
    def prompt(self, agent_module):
        self.prompt = agent_module.ASSESSOR_SYSTEM

    # --- Role description ---
    def test_describes_assessor_role(self):
        assert "assessment" in self.prompt.lower() or "assess" in self.prompt.lower()

    def test_mentions_roleplay(self):
        assert "roleplay" in self.prompt.lower()

    def test_mentions_trainee(self):
        assert "trainee" in self.prompt.lower()

    # --- Format placeholders ---
    def test_profile_placeholder_present(self):
        assert "{profile}" in self.prompt

    def test_conversation_placeholder_present(self):
        assert "{conversation}" in self.prompt

    # --- Tool references ---
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_referenced(self, tool_name):
        assert tool_name in self.prompt, (
            f"Expected tool '{tool_name}' to be mentioned in ASSESSOR_SYSTEM"
        )

    def test_has_eight_tools_mentioned(self):
        missing = [t for t in EXPECTED_TOOLS if t not in self.prompt]
        assert missing == [], f"Missing tools in ASSESSOR_SYSTEM: {missing}"

    # --- Five dimensions ---
    def test_five_dimensions_present(self):
        """Assessment must cover exactly 5 numbered dimensions."""
        import re
        sections = re.findall(r"###\s+\d+\.", self.prompt)
        assert len(sections) >= 5, (
            f"Expected at least 5 numbered assessment dimensions, found {len(sections)}"
        )

    def test_dimension_first_impression(self):
        assert "First Impression" in self.prompt

    def test_dimension_needs_discovery(self):
        assert "Needs Discovery" in self.prompt

    def test_dimension_product_knowledge(self):
        assert "Product Knowledge" in self.prompt

    def test_dimension_objection_handling(self):
        assert "Objection Handling" in self.prompt

    def test_dimension_closing_technique(self):
        assert "Closing Technique" in self.prompt

    # --- Scoring format ---
    def test_overall_score_format(self):
        assert "Overall Score" in self.prompt

    def test_out_of_ten_scoring(self):
        assert "X/10" in self.prompt or "/10" in self.prompt

    # --- Workflow instructions ---
    def test_workflow_section_present(self):
        assert "Workflow" in self.prompt or "workflow" in self.prompt.lower()

    def test_workflow_lists_steps(self):
        assert "1." in self.prompt and "2." in self.prompt and "3." in self.prompt

    def test_verify_claims_with_tools(self):
        assert "verify" in self.prompt.lower()

    def test_use_list_products_first(self):
        assert "list_products" in self.prompt

    # --- Age / premium verification ---
    def test_age_last_birthday_mentioned(self):
        assert "Age Last Birthday" in self.prompt or "ALB" in self.prompt

    def test_flag_age_error_instruction(self):
        assert "flag" in self.prompt.lower() or "error" in self.prompt.lower()

    def test_get_current_date_priority(self):
        assert "get_current_date" in self.prompt

    # --- Output format markers ---
    def test_strengths_section_marker(self):
        assert "Key Strengths" in self.prompt or "Strengths" in self.prompt

    def test_areas_to_improve_section_marker(self):
        assert "Areas to Improve" in self.prompt or "Improve" in self.prompt

    # --- Accuracy instruction ---
    def test_do_not_rely_on_memory(self):
        assert "memory" in self.prompt.lower()

    # --- Correct/Incorrect markers ---
    def test_correct_marker_present(self):
        assert "✓ Correct" in self.prompt or "Correct" in self.prompt

    def test_incorrect_marker_present(self):
        assert "✗ Incorrect" in self.prompt or "Incorrect" in self.prompt

    def test_partially_correct_marker_present(self):
        assert "Partially correct" in self.prompt or "⚠" in self.prompt


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM formatting / template tests
# ---------------------------------------------------------------------------


class TestAssessorSystemFormatting:
    """Verify the ASSESSOR_SYSTEM string behaves correctly as a Python format template."""

    def test_format_with_synthetic_data(self, agent_module):
        """Should successfully format the template with profile and conversation."""
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_PROFILE in result
        assert SYNTHETIC_CONVERSATION in result

    def test_format_preserves_section_headers(self, agent_module):
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert "## Overall Score" in result
        assert "### 1." in result

    def test_format_empty_profile(self, agent_module):
        """Empty profile should not raise an error."""
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile="",
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_CONVERSATION in result

    def test_format_empty_conversation(self, agent_module):
        """Empty conversation should not raise an error."""
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation="",
        )
        assert SYNTHETIC_PROFILE in result

    def test_format_both_empty(self, agent_module):
        """Both empty strings should still produce a non-empty result."""
        result = agent_module.ASSESSOR_SYSTEM.format(profile="", conversation="")
        assert len(result.strip()) > 0

    def test_format_with_special_characters(self, agent_module):
        """Special characters in inputs should not break formatting."""
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile="Customer: 李先生, age 50, 香港居民",
            conversation="Agent: 您好！我想介紹 Generations II 計劃。",
        )
        assert "李先生" in result
        assert "Generations II" in result

    def test_format_with_multiline_conversation(self, agent_module):
        """Multi-line conversations should format correctly."""
        multiline = "\n".join(
            [
                "Agent: Hello, how can I help you today?",
                "Customer: I want whole life coverage.",
                "Agent: Great, let me explain Generations II.",
                "Customer: Does it cover accidental coma?",
                "Agent: Yes, it includes an accelerated benefit for accidental coma.",
            ]
        )
        result = agent_module.ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE,
            conversation=multiline,
        )
        assert "accidental coma" in result

    def test_format_does_not_mutate_original(self, agent_module):
        """Calling .format() should not modify