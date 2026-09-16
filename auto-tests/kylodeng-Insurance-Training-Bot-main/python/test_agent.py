"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content sections, citation format, tool listing
- ASSESSOR_SYSTEM prompt string: presence, key content sections, placeholder variables, tool listing
- Shared tool coverage in both system prompts
- Boundary/edge cases for prompt template rendering (profile/conversation placeholders)
- Module-level constants existence and types
- create_agent import is available (stubbed – no real LLM/tool calls)

Mocks used:
- unittest.mock.patch / MagicMock for `langchain.agents.create_agent`
  (no real LangGraph/LLM calls are made)

TODOs:
- TODO: Integration tests for teacher_agent streaming (astream_events) require a live
        LangGraph runtime and LLM credentials – stub tests are marked skip.
- TODO: Integration tests for assessor_agent ainvoke require a live LangGraph runtime
        and LLM credentials – stub tests are marked skip.
- TODO: Tests for actual agent graph construction need the full tool list wired up –
        stub tests are marked skip.
- TODO: Verify that all eight RAG tools are callable end-to-end against a real
        vector store – out of scope here, marked skip.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for langchain so the module can be imported without the real
# package installed in test environments.
# ---------------------------------------------------------------------------


def _install_langchain_stub():
    """Insert a minimal langchain stub into sys.modules if not already present."""
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
        sys.modules["langchain.agents"] = agents_stub
        sys.modules["langchain"].agents = agents_stub


_install_langchain_stub()

# Now import the module under test
import api.agent as agent_module  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def teacher_prompt() -> str:
    return agent_module.TEACHER_SYSTEM


@pytest.fixture()
def assessor_prompt() -> str:
    return agent_module.ASSESSOR_SYSTEM


@pytest.fixture()
def rendered_assessor_prompt() -> str:
    """Return ASSESSOR_SYSTEM with both placeholders filled in."""
    return agent_module.ASSESSOR_SYSTEM.format(
        profile="42-year-old male, non-smoker, looking for whole-life coverage",
        conversation="Agent: Good morning! Customer: Hi, I want to know about Generations II.",
    )


# ---------------------------------------------------------------------------
# Synthetic data samples (used as parameterised inputs where relevant)
# ---------------------------------------------------------------------------

SYNTHETIC_PROFILES = [
    "42-year-old male, non-smoker, looking for whole-life coverage",
    "35-year-old female, smoker, interested in health insurance in mainland China",
    "60-year-old male, retired, enquiring about hospital network in Shanghai",
]

SYNTHETIC_CONVERSATIONS = [
    "Agent: Good morning! Customer: Hi, I want to know about Generations II.",
    (
        "Agent: The annual deductible is HKD 3,000. "
        "Customer: Is Beijing Hospital covered? Agent: Yes, it is."
    ),
    "Agent: Let me check the claim procedure for you. Customer: Please do.",
]

EIGHT_TOOLS = [
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
]


# ===========================================================================
# 1. Module-level constant existence & type checks
# ===========================================================================


class TestModuleConstants:
    def test_teacher_system_exists(self):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_str(self, teacher_prompt):
        assert isinstance(teacher_prompt, str)

    def test_assessor_system_is_str(self, assessor_prompt):
        assert isinstance(assessor_prompt, str)

    def test_teacher_system_non_empty(self, teacher_prompt):
        assert len(teacher_prompt.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_prompt):
        assert len(assessor_prompt.strip()) > 0

    def test_create_agent_importable(self):
        """create_agent should be importable from langchain.agents."""
        from langchain.agents import create_agent  # noqa: F401

        assert callable(create_agent)


# ===========================================================================
# 2. TEACHER_SYSTEM prompt content
# ===========================================================================


class TestTeacherSystemPrompt:
    # --- Role description ---
    def test_teacher_mentions_trainer_role(self, teacher_prompt):
        assert "trainer" in teacher_prompt.lower() or "coach" in teacher_prompt.lower()

    def test_teacher_mentions_insurance(self, teacher_prompt):
        assert "insurance" in teacher_prompt.lower()

    def test_teacher_mentions_agent(self, teacher_prompt):
        assert "agent" in teacher_prompt.lower()

    # --- Tool listing ---
    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_teacher_lists_all_eight_tools(self, teacher_prompt, tool_name):
        assert tool_name in teacher_prompt, (
            f"Expected tool '{tool_name}' to be mentioned in TEACHER_SYSTEM"
        )

    def test_teacher_mentions_eight_tools_count(self, teacher_prompt):
        """Prompt should explicitly reference 'eight tools'."""
        assert "eight" in teacher_prompt.lower()

    # --- Age / premium calculation guidance ---
    def test_teacher_mentions_age_last_birthday(self, teacher_prompt):
        assert "Age Last Birthday" in teacher_prompt or "ALB" in teacher_prompt

    def test_teacher_mentions_get_current_date_first(self, teacher_prompt):
        """Prompt should instruct calling get_current_date first for date calculations."""
        assert "get_current_date" in teacher_prompt

    def test_teacher_warns_against_guessing(self, teacher_prompt):
        assert "Never guess" in teacher_prompt or "never guess" in teacher_prompt

    # --- Citation format ---
    def test_teacher_citation_format_present(self, teacher_prompt):
        """Inline citation format [[Sn]] must be documented."""
        assert "[[S" in teacher_prompt

    def test_teacher_citation_example_present(self, teacher_prompt):
        """A worked example citation should appear in the prompt."""
        assert "[[S1]]" in teacher_prompt

    def test_teacher_citation_only_from_retrieved_docs(self, teacher_prompt):
        assert "retrieved document" in teacher_prompt or "drawing directly" in teacher_prompt

    # --- Behavioural instructions ---
    def test_teacher_encourages_interactivity(self, teacher_prompt):
        keywords = ["interactive", "exercises", "quiz", "scenarios"]
        assert any(kw in teacher_prompt.lower() for kw in keywords)

    def test_teacher_not_purely_theoretical(self, teacher_prompt):
        assert "theoretical" in teacher_prompt.lower()

    def test_teacher_mentions_first_impression(self, teacher_prompt):
        assert "first" in teacher_prompt.lower()

    def test_teacher_mentions_discovery_questions(self, teacher_prompt):
        assert "discovery" in teacher_prompt.lower()

    def test_teacher_mentions_knowledge_base(self, teacher_prompt):
        assert "knowledge base" in teacher_prompt.lower()

    def test_teacher_mentions_premium(self, teacher_prompt):
        assert "premium" in teacher_prompt.lower()


# ===========================================================================
# 3. ASSESSOR_SYSTEM prompt content
# ===========================================================================


class TestAssessorSystemPrompt:
    # --- Role description ---
    def test_assessor_mentions_assessment_role(self, assessor_prompt):
        assert "assessment" in assessor_prompt.lower() or "assess" in assessor_prompt.lower()

    def test_assessor_mentions_roleplay(self, assessor_prompt):
        assert "roleplay" in assessor_prompt.lower()

    def test_assessor_mentions_trainee(self, assessor_prompt):
        assert "trainee" in assessor_prompt.lower()

    # --- Template placeholders ---
    def test_assessor_has_profile_placeholder(self, assessor_prompt):
        assert "{profile}" in assessor_prompt

    def test_assessor_has_conversation_placeholder(self, assessor_prompt):
        assert "{conversation}" in assessor_prompt

    # --- Tool listing ---
    @pytest.mark.parametrize("tool_name", EIGHT_TOOLS)
    def test_assessor_lists_all_eight_tools(self, assessor_prompt, tool_name):
        assert tool_name in assessor_prompt, (
            f"Expected tool '{tool_name}' to be mentioned in ASSESSOR_SYSTEM"
        )

    def test_assessor_mentions_eight_tools_count(self, assessor_prompt):
        assert "eight" in assessor_prompt.lower()

    # --- Age / premium accuracy guidance ---
    def test_assessor_mentions_alb(self, assessor_prompt):
        assert "Age Last Birthday" in assessor_prompt or "ALB" in assessor_prompt

    def test_assessor_mentions_get_current_date(self, assessor_prompt):
        assert "get_current_date" in assessor_prompt

    def test_assessor_flags_premium_error(self, assessor_prompt):
        assert "Flag" in assessor_prompt or "flag" in assessor_prompt

    # --- Five-dimension assessment structure ---
    def test_assessor_mentions_five_dimensions(self, assessor_prompt):
        assert "five" in assessor_prompt.lower()

    def test_assessor_mentions_first_impression_dimension(self, assessor_prompt):
        assert "First Impression" in assessor_prompt

    def test_assessor_mentions_needs_discovery_dimension(self, assessor_prompt):
        assert "Needs Discovery" in assessor_prompt

    def test_assessor_mentions_product_knowledge_dimension(self, assessor_prompt):
        assert "Product Knowledge" in assessor_prompt

    def test_assessor_mentions_objection_handling_dimension(self, assessor_prompt):
        assert "Objection Handling" in assessor_prompt

    def test_assessor_mentions_closing_technique_dimension(self, assessor_prompt):
        assert "Closing Technique" in assessor_prompt

    # --- Output format ---
    def test_assessor_output_format_overall_score(self, assessor_prompt):
        assert "## Overall Score" in assessor_prompt

    def test_assessor_correct_marker(self, assessor_prompt):
        assert "✓ Correct" in assessor_prompt

    def test_assessor_incorrect_marker(self, assessor_prompt):
        assert "✗ Incorrect" in assessor_prompt

    def test_assessor_partial_marker(self, assessor_prompt):
        assert "⚠" in assessor_prompt

    def test_assessor_strengths_section(self, assessor_prompt):
        assert "Key Strengths" in assessor_prompt or "Strengths" in assessor_prompt

    def test_assessor_areas_to_improve_section(self, assessor_prompt):
        assert "Areas to Improve" in assessor_prompt or "Improve" in assessor_prompt

    # --- Workflow instructions ---
    def test_assessor_workflow_step1_read_conversation(self, assessor_prompt):
        assert "Read the conversation" in assessor_prompt or "Read" in assessor_prompt

    def test_assessor_workflow_step2_use_tools(self, assessor_prompt):
        """Assessor must instruct tool use for claim verification."""
        assert "tool" in assessor_prompt.lower()

    def test_assessor_workflow_step3_write_assessment(self, assessor_prompt):
        assert "write" in assessor_prompt.lower() or "full assessment" in assessor_prompt.lower()

    def test_assessor_verify_not_rely_on_memory(self, assessor_prompt):
        assert "memory" in assessor_prompt.lower()

    def test_assessor_list_products_first_suggestion(self, assessor_prompt):
        assert "list_products" in assessor_prompt

    # --- Score format ---
    def test_assessor_score_format_x_over_10(self, assessor_prompt):
        assert "X/10" in assessor_prompt


# ===========================================================================
# 4. Prompt template rendering (parameterised with synthetic data)
# ===========================================================================


class TestAssessorPromptRendering:
    @pytest.mark.parametrize("profile", SYNTHETIC_PROFILES)
    def test_profile_placeholder_renders(self, assessor_prompt, profile):
        rendered = assessor_prompt.format(
            profile=profile,
            conversation="Agent: Hello. Customer: Hi.",
        )
        assert profile in rendered
        assert "{profile}" not in rendered

    @pytest.mark.parametrize("conversation", SYNTHETIC_CONVERSATIONS)
    def test_conversation_placeholder_renders(self, assessor_prompt, conversation):
        rendered = assessor_prompt.format(
            profile="Test profile",
            conversation=conversation,
        )
        assert conversation in rendered
        assert "{conversation}" not in rendered

    @pytest.mark.parametrize("profile,conversation", zip(SYNTHETIC_PROFILES, SYNTHETIC_CONVERSATIONS))
    def test_both_placeholders_render_together(self, assessor_prompt, profile, conversation):
        rendered = assessor_prompt.format(profile=profile, conversation=conversation)
        assert profile in rendered
        assert conversation in rendered

    def test_missing_profile_raises_key_error(self, assessor_prompt):
        with pytest.raises(KeyError):
            assessor_prompt.format(conversation="only conversation provided")

    def test_missing_conversation_raises_key_error(self, assessor_prompt):
        with pytest.raises(KeyError):
            assessor_prompt.format(profile="only profile provided")

    def test_empty_profile_renders(self, assessor_prompt):
        rendered = assessor_prompt.format(profile="", conversation="some conversation")
        assert "{profile}" not in rendered

    def test_empty_conversation_renders(self, assessor_prompt):
        rendered = assessor_prompt.format(profile="some profile", conversation="")
        assert "{conversation}" not in rendered

    def test_special_characters_in_profile(self, assessor_prompt):
        profile = "Client: 50y/o, HKD$10,000/month; non-smoker (verified)"
        rendered = assessor_prompt.format(profile=profile, conversation="N/A")
        assert profile in rendered

    def test_multiline_conversation_renders(self, assessor_prompt):
        conversation = "Agent: Hello.\nCustomer: Hi.\nAgent: How can I help?\nCustomer: Tell me about Generations II."
        rendered = assessor_prompt.format(profile="Test", conversation=conversation)
        assert conversation in rendered

    def test_unicode_in_conversation_renders(self, assessor_prompt):
        conversation = "客戶: 你好。Agent: 您好！"
        rendered = assessor_prompt.format(profile="Test", conversation=conversation)
        assert conversation in rendered


# ===========================================================================
# 5. Shared content between both prompts
# ===========================================================================


class Test