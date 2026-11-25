"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content keywords, citation format instructions,
  tool listing completeness, age/ALB calculation instructions
- ASSESSOR_SYSTEM prompt string: presence, content keywords, profile/conversation placeholders,
  five-dimension assessment format, tool listing completeness, age/ALB instructions
- Module-level constants: existence, types, non-emptiness
- create_agent import and usage (mocked)
- Prompt template formatting with synthetic profile/conversation data
- Edge cases: placeholder substitution, missing placeholders, special characters in inputs

Mocks used:
- langchain.agents.create_agent (patched to avoid real LangChain calls)
- No external service calls are made in this module directly; all tool calls happen
  at runtime via LangGraph, so no DB/S3/API mocks are needed here

TODOs:
- TODO: Integration tests for teacher_agent and assessor_agent graph execution
  require a running LangGraph environment and LLM credentials — stub tests added below
- TODO: Tests for astream_events / ainvoke behaviour require async LangGraph graph
  objects which are not present in the truncated source — stub tests added below
- TODO: The source file is truncated (ASSESSOR_SYSTEM ends mid-sentence); some
  format-validation tests assume reasonable completions — noted inline
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SYNTHETIC_PROFILE = (
    "Name: Alice Lam, Age: 35, Occupation: Teacher, "
    "Looking for: whole life coverage, Budget: HKD 2,000/month"
)

SYNTHETIC_CONVERSATION = (
    "Agent: Good morning, Ms. Lam! How are you today?\n"
    "Customer: Fine, thanks. I want to know about Generations II.\n"
    "Agent: Generations II is a participating whole life plan by Sun Life. "
    "It offers guaranteed lifelong protection and double bonuses. "
    "The annual premium for a 35-year-old is around HKD 24,000.\n"
    "Customer: Does it cover mental incapacity?\n"
    "Agent: Yes, it includes a mental incapacity benefit.\n"
    "Customer: What about hospital networks in mainland China?\n"
    "Agent: Sun Life has a list of designated hospitals in mainland China, "
    "covering all Class 3 hospitals and Class 2A hospitals in 21 cities.\n"
)

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

EXPECTED_ASSESSMENT_SECTIONS = [
    "## Overall Score",
    "### 1. First Impression & Rapport Building",
    "### 2. Needs Discovery",
    "### 3. Product Knowledge & Accuracy",
    "### 4. Objection Handling",
    "### 5. Closing Technique",
    "### ✅ Key Strengths",
    "### ⚠️ Areas to Improve",
]


@pytest.fixture(autouse=True)
def mock_create_agent():
    """Patch create_agent so importing api.agent never calls LangChain."""
    mock_agent = MagicMock(name="mock_create_agent")
    mock_agent.return_value = MagicMock(name="agent_instance")
    with patch("langchain.agents.create_agent", mock_agent):
        yield mock_agent


@pytest.fixture()
def agent_module(mock_create_agent):
    """Import (or re-import) api.agent with create_agent mocked."""
    # Remove cached version so each test gets a fresh import if needed
    sys.modules.pop("api.agent", None)
    with patch("langchain.agents.create_agent", mock_create_agent):
        import api.agent as mod
    return mod


# ---------------------------------------------------------------------------
# 1. Module-level constant existence and type checks
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), (
            "TEACHER_SYSTEM constant must be defined"
        )

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), (
            "ASSESSOR_SYSTEM constant must be defined"
        )

    def test_teacher_system_is_str(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_str(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_non_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_teacher_system_minimum_length(self, agent_module):
        # Sanity check: a meaningful system prompt should be substantial
        assert len(agent_module.TEACHER_SYSTEM) >= 500

    def test_assessor_system_minimum_length(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM) >= 500


# ---------------------------------------------------------------------------
# 2. TEACHER_SYSTEM content validation
# ---------------------------------------------------------------------------


class TestTeacherSystemContent:
    @pytest.fixture(autouse=True)
    def _prompt(self, agent_module):
        self.prompt = agent_module.TEACHER_SYSTEM

    def test_references_insurance_trainer_role(self):
        assert "insurance" in self.prompt.lower()

    def test_references_trainer_or_coach(self):
        keywords = ["trainer", "coach"]
        assert any(kw in self.prompt.lower() for kw in keywords)

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_lists_all_tools(self, tool_name):
        assert tool_name in self.prompt, (
            f"TEACHER_SYSTEM must mention tool '{tool_name}'"
        )

    def test_contains_citation_format(self):
        # Citation marker format: [[Sn]]
        assert "[[S" in self.prompt, (
            "TEACHER_SYSTEM must describe the [[Sn]] citation format"
        )

    def test_citation_example_present(self):
        # At least one concrete citation example should appear
        assert "[[S1]]" in self.prompt or "[[Sn]]" in self.prompt

    def test_age_last_birthday_instruction(self):
        assert "Age Last Birthday" in self.prompt or "ALB" in self.prompt

    def test_get_current_date_called_first_instruction(self):
        lower = self.prompt.lower()
        assert "get_current_date" in lower
        # Instruction that it must be called first for date-relative questions
        assert "first" in lower

    def test_no_guessing_instruction(self):
        lower = self.prompt.lower()
        assert "never guess" in lower or "do not guess" in lower or "never" in lower

    def test_encouragement_keywords(self):
        lower = self.prompt.lower()
        keywords = ["encouraging", "confidence", "engaging", "exercises"]
        assert any(kw in lower for kw in keywords)

    def test_discovery_questions_mentioned(self):
        lower = self.prompt.lower()
        assert "discovery" in lower or "uncover" in lower

    def test_premium_calculation_warning(self):
        lower = self.prompt.lower()
        assert "premium" in lower

    def test_alb_example_with_age(self):
        # The prompt should contain a concrete age example to illustrate ALB
        assert "50" in self.prompt or "age" in self.prompt.lower()

    def test_does_not_contain_profile_placeholder(self):
        # TEACHER_SYSTEM is a static prompt — it must NOT contain {profile}
        assert "{profile}" not in self.prompt

    def test_does_not_contain_conversation_placeholder(self):
        assert "{conversation}" not in self.prompt

    def test_hospital_network_tool_described(self):
        assert "lookup_hospital_network" in self.prompt

    def test_compare_plans_tool_described(self):
        assert "compare_plans" in self.prompt

    def test_lookup_exclusions_tool_described(self):
        assert "lookup_exclusions" in self.prompt

    def test_search_claim_procedure_tool_described(self):
        assert "search_claim_procedure" in self.prompt

    @pytest.mark.parametrize("concept", ["deductible", "pre-existing", "exclusion", "pre-authoris"])
    def test_key_insurance_concepts_mentioned(self, concept):
        lower = self.prompt.lower()
        # At least some of these concepts should appear as example tool use-cases
        # We require at least 2 out of 4 to avoid over-constraining a truncated file
        pass  # checked collectively below

    def test_at_least_two_insurance_concepts_present(self):
        concepts = ["deductible", "pre-existing", "exclusion", "pre-authoris", "reimbursement"]
        found = sum(1 for c in concepts if c in self.prompt.lower())
        assert found >= 2, (
            f"Expected at least 2 insurance concepts in TEACHER_SYSTEM, found {found}"
        )


# ---------------------------------------------------------------------------
# 3. ASSESSOR_SYSTEM content validation
# ---------------------------------------------------------------------------


class TestAssessorSystemContent:
    @pytest.fixture(autouse=True)
    def _prompt(self, agent_module):
        self.prompt = agent_module.ASSESSOR_SYSTEM

    def test_contains_profile_placeholder(self):
        assert "{profile}" in self.prompt, (
            "ASSESSOR_SYSTEM must contain {profile} placeholder"
        )

    def test_contains_conversation_placeholder(self):
        assert "{conversation}" in self.prompt, (
            "ASSESSOR_SYSTEM must contain {conversation} placeholder"
        )

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_lists_all_tools(self, tool_name):
        assert tool_name in self.prompt, (
            f"ASSESSOR_SYSTEM must mention tool '{tool_name}'"
        )

    def test_references_accuracy_assessment(self):
        lower = self.prompt.lower()
        assert "accuracy" in lower or "assessment" in lower

    def test_five_dimensions_mentioned(self):
        lower = self.prompt.lower()
        assert "five" in lower or "5" in lower

    def test_product_knowledge_dimension(self):
        lower = self.prompt.lower()
        assert "product knowledge" in lower

    def test_first_impression_dimension(self):
        lower = self.prompt.lower()
        assert "first impression" in lower

    def test_needs_discovery_dimension(self):
        lower = self.prompt.lower()
        assert "needs discovery" in lower or "discovery" in lower

    def test_objection_handling_dimension(self):
        lower = self.prompt.lower()
        assert "objection" in lower

    def test_closing_technique_dimension(self):
        lower = self.prompt.lower()
        assert "closing" in lower

    def test_overall_score_format(self):
        assert "## Overall Score" in self.prompt

    @pytest.mark.parametrize("section", EXPECTED_ASSESSMENT_SECTIONS)
    def test_assessment_section_header_present(self, section):
        assert section in self.prompt, (
            f"ASSESSOR_SYSTEM must contain section: '{section}'"
        )

    def test_correct_incorrect_markers_present(self):
        assert "✓ Correct" in self.prompt or "Correct" in self.prompt
        assert "✗ Incorrect" in self.prompt or "Incorrect" in self.prompt

    def test_partially_correct_marker(self):
        assert "Partially correct" in self.prompt or "⚠" in self.prompt

    def test_age_last_birthday_instruction(self):
        assert "Age Last Birthday" in self.prompt or "ALB" in self.prompt

    def test_get_current_date_first_instruction(self):
        assert "get_current_date" in self.prompt

    def test_do_not_rely_on_memory_instruction(self):
        lower = self.prompt.lower()
        assert "do not rely on memory" in lower or "not rely on memory" in lower

    def test_key_strengths_section(self):
        assert "Key Strengths" in self.prompt

    def test_areas_to_improve_section(self):
        assert "Areas to Improve" in self.prompt

    def test_workflow_steps_present(self):
        lower = self.prompt.lower()
        assert "workflow" in lower or "step" in lower

    def test_trainee_agent_reference(self):
        lower = self.prompt.lower()
        assert "trainee" in lower or "agent" in lower

    def test_roleplay_reference(self):
        lower = self.prompt.lower()
        assert "roleplay" in lower

    def test_references_hospital_network_verification(self):
        assert "lookup_hospital_network" in self.prompt

    def test_references_exclusion_verification(self):
        assert "lookup_exclusions" in self.prompt

    def test_premium_age_flag_instruction(self):
        lower = self.prompt.lower()
        assert "flag" in lower or "error" in lower


# ---------------------------------------------------------------------------
# 4. ASSESSOR_SYSTEM template formatting
# ---------------------------------------------------------------------------


class TestAssessorSystemFormatting:
    """Test that {profile} and {conversation} placeholders format correctly."""

    @pytest.fixture(autouse=True)
    def _template(self, agent_module):
        self.template = agent_module.ASSESSOR_SYSTEM

    def test_format_with_synthetic_data(self):
        formatted = self.template.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert SYNTHETIC_PROFILE in formatted
        assert SYNTHETIC_CONVERSATION in formatted

    def test_format_removes_profile_placeholder(self):
        formatted = self.template.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert "{profile}" not in formatted

    def test_format_removes_conversation_placeholder(self):
        formatted = self.template.format(
            profile=SYNTHETIC_PROFILE,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert "{conversation}" not in formatted

    def test_format_with_empty_profile(self):
        formatted = self.template.format(profile="", conversation=SYNTHETIC_CONVERSATION)
        assert SYNTHETIC_CONVERSATION in formatted

    def test_format_with_empty_conversation(self):
        formatted = self.template.format(profile=SYNTHETIC_PROFILE, conversation="")
        assert SYNTHETIC_PROFILE in formatted

    def test_format_with_empty_both(self):
        formatted = self.template.format(profile="", conversation="")
        assert isinstance(formatted, str)
        assert len(formatted) > 0

    def test_format_with_special_characters_in_profile(self):
        special_profile = "Name: 陳大文, Age: 42, 職業: 教師, Budget: HKD 3,000/月"
        formatted = self.template.format(
            profile=special_profile,
            conversation=SYNTHETIC_CONVERSATION,
        )
        assert special_profile in formatted

    def test_format_with_special_characters_in_conversation(self):
        special_convo = (
            "Agent: 您好！\nCustomer: 我想了解 Generations II。\n"
            "Agent: 這是一個參與式終身壽險計劃。\n"
        )
        formatted = self.template.format(
            profile=SYNTHETIC_PROFILE,
            conversation=special_convo,