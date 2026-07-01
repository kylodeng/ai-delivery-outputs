"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content keywords, citation format instructions,
  tool list completeness, age/ALB calculation reminder, non-empty invariants
- ASSESSOR_SYSTEM prompt string: presence, content keywords, five assessment dimensions,
  tool list completeness, age/ALB calculation reminder, placeholder tokens,
  scoring format, non-empty invariants
- Module-level constants and structure
- create_agent import is available (mocked to avoid real LangChain calls)
- Boundary / negative cases: prompt lengths, required substrings, forbidden patterns

Mocks used:
- unittest.mock.patch for `langchain.agents.create_agent` (never called at import time,
  but patched to guarantee no real network/LLM calls if invoked indirectly)

TODOs:
- TODO: Test actual agent graph execution (requires LangGraph runtime + LLM credentials)
- TODO: Test streaming via astream_events (requires async LangGraph setup)
- TODO: Test ainvoke for assessor agent (requires async LangGraph setup + full session)
- TODO: Test each RAG tool integration (requires vector store / retriever fixtures)
- TODO: Test ASSESSOR_SYSTEM profile/conversation template interpolation at runtime
"""

import importlib
import re
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

AGENT_MODULE_PATH = "api.agent"

# Eight tools that must appear in BOTH system prompts
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

# Five assessment dimensions required in ASSESSOR_SYSTEM
EXPECTED_DIMENSIONS = [
    "First Impression",
    "Needs Discovery",
    "Product Knowledge",
    "Objection Handling",
    "Closing Technique",
]


@pytest.fixture(scope="module")
def agent_module() -> ModuleType:
    """
    Import api.agent with create_agent patched so no real LangChain calls happen.
    Returns the loaded module.
    """
    mock_create_agent = MagicMock(return_value=MagicMock())
    with patch.dict("sys.modules", {"langchain.agents": MagicMock(create_agent=mock_create_agent)}):
        # Force a fresh import
        if AGENT_MODULE_PATH in sys.modules:
            del sys.modules[AGENT_MODULE_PATH]
        mod = importlib.import_module(AGENT_MODULE_PATH)
    return mod


@pytest.fixture(scope="module")
def teacher_prompt(agent_module: ModuleType) -> str:
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_prompt(agent_module: ModuleType) -> str:
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Module-level smoke tests
# ---------------------------------------------------------------------------


class TestModuleImport:
    """Ensure the module loads and exposes the expected public names."""

    def test_module_loads_without_error(self, agent_module: ModuleType) -> None:
        assert agent_module is not None

    def test_teacher_system_exists(self, agent_module: ModuleType) -> None:
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM constant missing"

    def test_assessor_system_exists(self, agent_module: ModuleType) -> None:
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM constant missing"

    def test_teacher_system_is_string(self, agent_module: ModuleType) -> None:
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_string(self, agent_module: ModuleType) -> None:
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_create_agent_import_present(self, agent_module: ModuleType) -> None:
        """create_agent must be importable from the module's namespace."""
        assert hasattr(agent_module, "create_agent"), (
            "create_agent should be imported at module level"
        )


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM prompt tests
# ---------------------------------------------------------------------------


class TestTeacherSystemPrompt:
    """Validate the TEACHER_SYSTEM constant."""

    # -- Non-empty / length sanity ----------------------------------------

    def test_not_empty(self, teacher_prompt: str) -> None:
        assert teacher_prompt.strip() != ""

    def test_minimum_length(self, teacher_prompt: str) -> None:
        # A meaningful system prompt should be at least 200 characters
        assert len(teacher_prompt) >= 200, "TEACHER_SYSTEM suspiciously short"

    # -- Role / persona ---------------------------------------------------

    def test_contains_trainer_persona(self, teacher_prompt: str) -> None:
        assert "insurance sales trainer" in teacher_prompt.lower() or \
               "expert insurance" in teacher_prompt.lower()

    def test_contains_agent_reference(self, teacher_prompt: str) -> None:
        assert "agent" in teacher_prompt.lower()

    # -- Tool list completeness -------------------------------------------

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_contains_tool_name(self, teacher_prompt: str, tool_name: str) -> None:
        assert tool_name in teacher_prompt, (
            f"Tool '{tool_name}' not mentioned in TEACHER_SYSTEM"
        )

    def test_eight_tools_section_present(self, teacher_prompt: str) -> None:
        assert "eight tools" in teacher_prompt.lower() or \
               "8 tools" in teacher_prompt.lower(), (
            "TEACHER_SYSTEM should reference the total number of tools (eight)"
        )

    # -- Age / ALB instruction --------------------------------------------

    def test_alb_calculation_instruction(self, teacher_prompt: str) -> None:
        assert "ALB" in teacher_prompt or "Age Last Birthday" in teacher_prompt

    def test_get_current_date_called_first_instruction(self, teacher_prompt: str) -> None:
        assert "get_current_date" in teacher_prompt

    def test_premium_calculation_warning(self, teacher_prompt: str) -> None:
        assert "premium" in teacher_prompt.lower()

    # -- Citation format --------------------------------------------------

    def test_citation_marker_format_documented(self, teacher_prompt: str) -> None:
        """The prompt must document the [[Sn]] citation format."""
        assert "[[S" in teacher_prompt, (
            "TEACHER_SYSTEM must document the [[Sn]] inline citation marker format"
        )

    def test_citation_example_present(self, teacher_prompt: str) -> None:
        # e.g. [[S1]] should appear as an example
        citation_pattern = re.compile(r"\[\[S\d+\]\]")
        assert citation_pattern.search(teacher_prompt), (
            "TEACHER_SYSTEM should contain at least one example citation like [[S1]]"
        )

    def test_never_guess_instruction(self, teacher_prompt: str) -> None:
        assert "never guess" in teacher_prompt.lower() or \
               "do not guess" in teacher_prompt.lower()

    # -- Engagement / pedagogy --------------------------------------------

    def test_interactive_teaching_mentioned(self, teacher_prompt: str) -> None:
        keywords = ["interactive", "engaging", "exercise", "quiz", "scenario"]
        assert any(kw in teacher_prompt.lower() for kw in keywords), (
            "TEACHER_SYSTEM should encourage interactive teaching"
        )

    def test_encouragement_mentioned(self, teacher_prompt: str) -> None:
        assert "encouraging" in teacher_prompt.lower() or "confidence" in teacher_prompt.lower()

    # -- Forbidden patterns -----------------------------------------------

    def test_no_placeholder_tokens_left(self, teacher_prompt: str) -> None:
        """TEACHER_SYSTEM should not contain unfilled {placeholder} tokens."""
        placeholders = re.findall(r"\{[a-zA-Z_]+\}", teacher_prompt)
        assert placeholders == [], (
            f"TEACHER_SYSTEM has unfilled placeholders: {placeholders}"
        )

    def test_no_triple_backtick_leakage(self, teacher_prompt: str) -> None:
        assert "```" not in teacher_prompt, (
            "Markdown code fences should not appear inside the system prompt"
        )


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM prompt tests
# ---------------------------------------------------------------------------


class TestAssessorSystemPrompt:
    """Validate the ASSESSOR_SYSTEM constant."""

    # -- Non-empty / length sanity ----------------------------------------

    def test_not_empty(self, assessor_prompt: str) -> None:
        assert assessor_prompt.strip() != ""

    def test_minimum_length(self, assessor_prompt: str) -> None:
        assert len(assessor_prompt) >= 200, "ASSESSOR_SYSTEM suspiciously short"

    # -- Role / persona ---------------------------------------------------

    def test_contains_assessor_persona(self, assessor_prompt: str) -> None:
        assert "assessment" in assessor_prompt.lower() or "assessor" in assessor_prompt.lower()

    def test_references_roleplay(self, assessor_prompt: str) -> None:
        assert "roleplay" in assessor_prompt.lower() or "role-play" in assessor_prompt.lower()

    # -- Placeholder tokens -----------------------------------------------

    def test_profile_placeholder_present(self, assessor_prompt: str) -> None:
        """ASSESSOR_SYSTEM must contain the {profile} template variable."""
        assert "{profile}" in assessor_prompt

    def test_conversation_placeholder_present(self, assessor_prompt: str) -> None:
        """ASSESSOR_SYSTEM must contain the {conversation} template variable."""
        assert "{conversation}" in assessor_prompt

    def test_no_other_unexpected_placeholders(self, assessor_prompt: str) -> None:
        """Only {profile} and {conversation} should be present as placeholders."""
        placeholders = set(re.findall(r"\{[a-zA-Z_]+\}", assessor_prompt))
        allowed = {"{profile}", "{conversation}"}
        unexpected = placeholders - allowed
        assert unexpected == set(), (
            f"Unexpected placeholders in ASSESSOR_SYSTEM: {unexpected}"
        )

    # -- Tool list completeness -------------------------------------------

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_contains_tool_name(self, assessor_prompt: str, tool_name: str) -> None:
        assert tool_name in assessor_prompt, (
            f"Tool '{tool_name}' not mentioned in ASSESSOR_SYSTEM"
        )

    def test_eight_tools_section_present(self, assessor_prompt: str) -> None:
        assert "eight tools" in assessor_prompt.lower() or \
               "8 tools" in assessor_prompt.lower()

    # -- Age / ALB instruction --------------------------------------------

    def test_alb_calculation_instruction(self, assessor_prompt: str) -> None:
        assert "ALB" in assessor_prompt or "Age Last Birthday" in assessor_prompt

    def test_get_current_date_first_instruction(self, assessor_prompt: str) -> None:
        assert "get_current_date" in assessor_prompt

    # -- Five assessment dimensions ---------------------------------------

    @pytest.mark.parametrize("dimension", EXPECTED_DIMENSIONS)
    def test_assessment_dimension_present(self, assessor_prompt: str, dimension: str) -> None:
        assert dimension in assessor_prompt, (
            f"Assessment dimension '{dimension}' missing from ASSESSOR_SYSTEM"
        )

    def test_overall_score_format(self, assessor_prompt: str) -> None:
        """The prompt must specify the 'Overall Score: X/10' heading format."""
        assert "Overall Score" in assessor_prompt
        assert "10" in assessor_prompt  # scores are out of 10

    def test_dimension_scoring_out_of_ten(self, assessor_prompt: str) -> None:
        """Each dimension score placeholder (X/10) must appear."""
        assert "X/10" in assessor_prompt

    # -- Structured output sections ---------------------------------------

    def test_strengths_section_present(self, assessor_prompt: str) -> None:
        assert "Strengths" in assessor_prompt or "strengths" in assessor_prompt.lower()

    def test_areas_to_improve_section_present(self, assessor_prompt: str) -> None:
        assert "Areas to Improve" in assessor_prompt or "improve" in assessor_prompt.lower()

    def test_workflow_steps_present(self, assessor_prompt: str) -> None:
        """The assessor prompt must describe a numbered workflow."""
        assert "1." in assessor_prompt and "2." in assessor_prompt and "3." in assessor_prompt

    def test_verification_instruction(self, assessor_prompt: str) -> None:
        """Assessor must be told to verify factual claims using tools."""
        assert "verify" in assessor_prompt.lower() or "verif" in assessor_prompt.lower()

    def test_do_not_rely_on_memory(self, assessor_prompt: str) -> None:
        assert "memory" in assessor_prompt.lower() or "do not rely" in assessor_prompt.lower()

    def test_correct_incorrect_markers_present(self, assessor_prompt: str) -> None:
        """The scoring rubric should include ✓ Correct and ✗ Incorrect markers."""
        assert "Correct" in assessor_prompt
        assert "Incorrect" in assessor_prompt

    def test_partially_correct_marker_present(self, assessor_prompt: str) -> None:
        assert "Partially correct" in assessor_prompt or "partially correct" in assessor_prompt.lower()

    # -- Markdown heading format ------------------------------------------

    def test_uses_markdown_h2_for_overall_score(self, assessor_prompt: str) -> None:
        assert "## Overall Score" in assessor_prompt

    def test_uses_markdown_h3_for_dimensions(self, assessor_prompt: str) -> None:
        assert "### 1." in assessor_prompt


# ---------------------------------------------------------------------------
# Prompt consistency tests (teacher vs assessor)
# ---------------------------------------------------------------------------


class TestPromptConsistency:
    """Cross-prompt consistency checks."""

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_in_both_prompts(
        self, teacher_prompt: str, assessor_prompt: str, tool_name: str
    ) -> None:
        assert tool_name in teacher_prompt, f"{tool_name} missing from TEACHER_SYSTEM"
        assert tool_name in assessor_prompt, f"{tool_name} missing from ASSESSOR_SYSTEM"

    def test_both_prompts_distinct(self, teacher_prompt: str, assessor_prompt: str) -> None:
        """The two system prompts must not be identical."""
        assert teacher_prompt != assessor_prompt

    def test_alb_reminder_in_both_prompts(
        self, teacher_prompt: str, assessor_prompt: str
    ) -> None:
        for prompt, name in [(teacher_prompt, "TEACHER_SYSTEM"), (assessor_prompt, "ASSESSOR_SYSTEM")]:
            assert "ALB" in prompt or "Age Last Birthday" in prompt, (
                f"ALB reminder missing from {name}"
            )

    def test_get_current_date_in_both_prompts(
        self, teacher_prompt: str, assessor_prompt: str
    ) -> None:
        assert "get_current_date" in teacher_prompt
        assert "get_current_date" in assessor_prompt


# ---------------------------------------------------------------------------
# Synthetic data / parameterised content tests
# ---------------------------------------------------------------------------


class TestSyntheticDataRelevance:
    """
    Verify the prompts are consistent with the synthetic insurance product data samples
    (Generations II, hospital network documents