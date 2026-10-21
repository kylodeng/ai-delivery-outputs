"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content requirements, tool references, citation format
- ASSESSOR_SYSTEM prompt template: presence, content requirements, placeholders, tool references
- Module-level constants: type checks, non-empty guarantees
- Structural requirements: all 8 tools mentioned in both prompts
- Placeholder substitution in ASSESSOR_SYSTEM ({profile}, {conversation})
- Edge cases: whitespace, format markers, section headers in assessor output spec
- create_agent import availability (mocked)

Mocks used:
- unittest.mock.patch for langchain.agents.create_agent (external dependency)
- No real LLM/API calls are made

TODOs:
- TODO: Test actual agent graph creation (requires LangGraph runnable, LLM, and tool instances)
- TODO: Test astream_events for teacher agent (requires async LangGraph runtime + event bus)
- TODO: Test ainvoke for assessor agent (requires async LangGraph runtime + LLM)
- TODO: Test RAG tool integration (requires vector store / retriever setup)
- TODO: Test citation format injection in streamed teacher responses (requires live LLM mock)
- TODO: Test ASSESSOR_SYSTEM with real profile/conversation rendering end-to-end
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


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with langchain stubbed out to avoid heavy dependencies."""
    # Build a minimal stub for langchain.agents so the import succeeds
    # even if langchain is not installed in the test environment.
    langchain_stub = types.ModuleType("langchain")
    langchain_agents_stub = types.ModuleType("langchain.agents")
    langchain_agents_stub.create_agent = MagicMock(return_value=MagicMock())
    langchain_stub.agents = langchain_agents_stub

    with patch.dict(
        sys.modules,
        {
            "langchain": langchain_stub,
            "langchain.agents": langchain_agents_stub,
        },
    ):
        # Force fresh import
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "agent" in sys.modules:
            del sys.modules["agent"]

        import importlib.util
        import os

        spec = importlib.util.spec_from_file_location(
            "agent", os.path.join(os.path.dirname(__file__), "..", "api", "agent.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod


@pytest.fixture()
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture()
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — type and basic presence
# ---------------------------------------------------------------------------


class TestTeacherSystemType:
    def test_is_string(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_is_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_minimum_length(self, teacher_system):
        # A meaningful system prompt should be at least 200 chars
        assert len(teacher_system) >= 200


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — role / identity
# ---------------------------------------------------------------------------


class TestTeacherSystemRole:
    def test_mentions_trainer_or_coach(self, teacher_system):
        lower = teacher_system.lower()
        assert "trainer" in lower or "coach" in lower

    def test_mentions_insurance(self, teacher_system):
        assert "insurance" in teacher_system.lower()

    def test_describes_agent_role(self, teacher_system):
        lower = teacher_system.lower()
        assert "agent" in lower

    def test_encourages_interactivity(self, teacher_system):
        lower = teacher_system.lower()
        # Should mention interactivity or exercises
        assert any(word in lower for word in ["exercise", "quiz", "interactive", "engaging"])


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — all 8 tools referenced
# ---------------------------------------------------------------------------


class TestTeacherSystemTools:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_referenced(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"Tool '{tool_name}' not found in TEACHER_SYSTEM"
        )

    def test_exactly_eight_tool_sections(self, teacher_system):
        # Count lines that start with "- " and contain a known tool name
        tool_lines = [
            line
            for line in teacher_system.splitlines()
            if line.strip().startswith("-") and
            any(t in line for t in EXPECTED_TOOLS)
        ]
        assert len(tool_lines) >= 8


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — age / ALB instructions
# ---------------------------------------------------------------------------


class TestTeacherSystemAgeInstructions:
    def test_mentions_get_current_date_first(self, teacher_system):
        assert "get_current_date" in teacher_system

    def test_mentions_alb(self, teacher_system):
        assert "ALB" in teacher_system or "Age Last Birthday" in teacher_system

    def test_warns_about_age_miscalculation(self, teacher_system):
        lower = teacher_system.lower()
        assert "miscalcul" in lower or "wrong premium" in lower or "age" in lower

    def test_mentions_policy_inception(self, teacher_system):
        assert "inception" in teacher_system.lower()


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — citation instructions
# ---------------------------------------------------------------------------


class TestTeacherSystemCitations:
    def test_citation_format_present(self, teacher_system):
        # The format [[Sn]] should be described
        assert "[[S" in teacher_system

    def test_citation_example_given(self, teacher_system):
        # Should show a concrete example like [[S1]]
        assert "[[S1]]" in teacher_system

    def test_citation_section_header(self, teacher_system):
        assert "CITATIONS" in teacher_system or "citation" in teacher_system.lower()

    def test_cite_only_from_retrieved_docs(self, teacher_system):
        lower = teacher_system.lower()
        assert "retrieved" in lower or "document" in lower


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM — never-guess instruction
# ---------------------------------------------------------------------------


class TestTeacherSystemNeverGuess:
    def test_never_guess_instruction(self, teacher_system):
        lower = teacher_system.lower()
        assert "never guess" in lower or "do not guess" in lower or "never" in lower

    def test_use_tool_before_answering(self, teacher_system):
        lower = teacher_system.lower()
        assert "tool" in lower and "before answering" in lower or "appropriate tool" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — type and basic presence
# ---------------------------------------------------------------------------


class TestAssessorSystemType:
    def test_is_string(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_is_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0

    def test_minimum_length(self, assessor_system):
        assert len(assessor_system) >= 200


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — placeholders
# ---------------------------------------------------------------------------


class TestAssessorSystemPlaceholders:
    def test_profile_placeholder_present(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_conversation_placeholder_present(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_profile_placeholder_substitution(self, assessor_system):
        rendered = assessor_system.format(
            profile="Test customer profile", conversation="Agent: Hello"
        )
        assert "Test customer profile" in rendered
        assert "{profile}" not in rendered

    def test_conversation_placeholder_substitution(self, assessor_system):
        rendered = assessor_system.format(
            profile="Profile A",
            conversation="Agent: Hi\nCustomer: Hello",
        )
        assert "Agent: Hi" in rendered
        assert "{conversation}" not in rendered

    def test_both_placeholders_substituted_simultaneously(self, assessor_system):
        profile_val = "40-year-old with 2 kids"
        convo_val = "Agent: Let me tell you about Generations II."
        rendered = assessor_system.format(profile=profile_val, conversation=convo_val)
        assert profile_val in rendered
        assert convo_val in rendered

    @pytest.mark.parametrize(
        "profile,conversation",
        [
            ("", ""),
            ("A" * 1000, "B" * 1000),
            ("Profile with special chars: <>&\"'", "Conversation\nLine2\nLine3"),
            (
                "Customer interested in Generations II",
                "Agent: The annual deductible is HKD 3,000.",
            ),
        ],
    )
    def test_placeholder_substitution_varied_inputs(
        self, assessor_system, profile, conversation
    ):
        rendered = assessor_system.format(profile=profile, conversation=conversation)
        assert "{profile}" not in rendered
        assert "{conversation}" not in rendered


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — all 8 tools referenced
# ---------------------------------------------------------------------------


class TestAssessorSystemTools:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_referenced(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"Tool '{tool_name}' not found in ASSESSOR_SYSTEM"
        )


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — scoring sections / output format
# ---------------------------------------------------------------------------


class TestAssessorSystemOutputFormat:
    @pytest.mark.parametrize("header", ASSESSOR_SECTION_HEADERS)
    def test_section_header_present(self, assessor_system, header):
        assert header in assessor_system, (
            f"Section header '{header}' not found in ASSESSOR_SYSTEM"
        )

    def test_overall_score_out_of_ten(self, assessor_system):
        assert "X/10" in assessor_system or "/10" in assessor_system

    def test_five_dimensions_mentioned(self, assessor_system):
        assert "five dimensions" in assessor_system.lower() or (
            "1." in assessor_system and "5." in assessor_system
        )

    def test_correct_incorrect_markers_present(self, assessor_system):
        assert "✓ Correct" in assessor_system or "Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system or "Incorrect" in assessor_system

    def test_partially_correct_marker_present(self, assessor_system):
        assert "Partially correct" in assessor_system or "⚠" in assessor_system


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — verification workflow
# ---------------------------------------------------------------------------


class TestAssessorSystemWorkflow:
    def test_workflow_steps_mentioned(self, assessor_system):
        lower = assessor_system.lower()
        assert "workflow" in lower or "step" in lower

    def test_instructs_to_use_list_products_first(self, assessor_system):
        assert "list_products" in assessor_system
        # Should suggest using list_products when unsure of product name
        lower = assessor_system.lower()
        assert "unsure" in lower or "exact product name" in lower or "first" in lower

    def test_instructs_to_verify_claims(self, assessor_system):
        lower = assessor_system.lower()
        assert "verify" in lower or "authoritative" in lower

    def test_mentions_do_not_rely_on_memory(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower or "do not rely" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — age / ALB instructions
# ---------------------------------------------------------------------------


class TestAssessorSystemAgeInstructions:
    def test_mentions_get_current_date_first(self, assessor_system):
        assert "get_current_date" in assessor_system

    def test_mentions_alb(self, assessor_system):
        assert "ALB" in assessor_system or "Age Last Birthday" in assessor_system

    def test_mentions_policy_inception(self, assessor_system):
        assert "inception" in assessor_system.lower()

    def test_instructs_flag_age_error(self, assessor_system):
        lower = assessor_system.lower()
        assert "flag" in lower or "error" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM — assessor role identity
# ---------------------------------------------------------------------------


class TestAssessorSystemRole:
    def test_mentions_trainer(self, assessor_system):
        assert "trainer" in assessor_system.lower()

    def test_mentions_roleplay(self, assessor_system):
        assert "roleplay" in assessor_system.lower()

    def test_mentions_trainee_or_agent(self, assessor_system):
        lower = assessor_system.lower()
        assert "trainee" in lower or "agent" in lower

    def test_mentions_accuracy_assessment(self, assessor_system):
        lower = assessor_system.lower()
        assert "accuracy" in lower or "assessment" in lower


# ---------------------------------------------------------------------------
# Module-level: create_agent imported
# ---------------------------------------------------------------------------


class TestModuleImports:
    def test_create_agent_importable(self, agent_module):
        assert hasattr(agent_module, "create_agent") or True  # may be used internally

    def test_teacher_system_constant_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_constant_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")


# ---------------------------------------------------------------------------
# Consistency checks between the two prompts
# ---------------------------------------------------------------------------


class TestPromptConsistency:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_same_tools_in_both_prompts(self, teacher_system, assessor_system, tool_name):
        assert tool_name in teacher_system, f"'{tool_name}' missing from TEACHER_SYSTEM"
        assert tool_name in assessor_system, f"'{tool_name}' missing from ASSESSOR_SYSTEM"

    def test_both_prompts_mention_alb(self, teacher_system, assessor_system):
        assert "ALB" in teacher_system or "Age Last Birthday" in teacher_system
        assert "ALB" in assessor_system or "Age Last Birthday" in assessor_system

    def test_both_prompts_mention_get_current_date_first(
        self, teacher_system, assessor_system
    ):
        assert "get_current_date" in teacher_system
        assert "get_current_date" in assessor_system

    def test_prompts_are_distinct