"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, structure, key sections, citation format instructions
- ASSESSOR_SYSTEM prompt string: presence, structure, five assessment dimensions, tool list,
  workflow instructions, placeholder variables ({profile}, {conversation})
- Module-level constants: non-empty, correct types
- create_agent import and usage (mocked)
- Prompt template variable substitution for ASSESSOR_SYSTEM
- Edge cases: format strings, citation marker format, tool names consistency

Mocks used:
- langchain.agents.create_agent (patched to avoid real LangChain calls)
- No external services are called directly from agent.py constants

TODOs:
- TODO: Test actual agent graph construction once LangGraph wiring is added to the module
- TODO: Test astream_events / ainvoke integration with a mock LangGraph graph
- TODO: Test RAG tool bindings once tool objects are defined in the module
- TODO: Test TEACHER_SYSTEM and ASSESSOR_SYSTEM with a real LLM stub for token limits
- TODO: Test streaming behaviour of the teacher agent end-to-end
- TODO: Test assessor agent one-shot ainvoke with mock conversation/profile payloads
"""

import importlib
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
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

ASSESSOR_DIMENSIONS = [
    "First Impression",
    "Needs Discovery",
    "Product Knowledge",
    "Objection Handling",
    "Closing Technique",
]


@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent with create_agent mocked so no real LangChain calls occur."""
    mock_create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    # Provide a fake langchain.agents module
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
        # Force fresh import
        if "api.agent" in sys.modules:
            del sys.modules["api.agent"]
        if "agent" in sys.modules:
            del sys.modules["agent"]

        import importlib.util, pathlib, os

        # Support running as 'api.agent' or standalone 'agent'
        try:
            spec = importlib.util.spec_from_file_location(
                "api.agent",
                pathlib.Path(__file__).parent.parent / "api" / "agent.py",
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules["api.agent"] = mod
            spec.loader.exec_module(mod)
        except (FileNotFoundError, AttributeError):
            import api.agent as mod  # fallback to normal import

        yield mod


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM tests
# ---------------------------------------------------------------------------


class TestTeacherSystemPresence:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM constant missing"

    def test_teacher_system_is_string(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_teacher_system_is_not_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_teacher_system_min_length(self, teacher_system):
        # A meaningful system prompt should be at least 200 characters
        assert len(teacher_system) >= 200


class TestTeacherSystemContent:
    def test_teacher_system_mentions_trainer_role(self, teacher_system):
        assert "trainer" in teacher_system.lower() or "coach" in teacher_system.lower()

    def test_teacher_system_mentions_insurance(self, teacher_system):
        assert "insurance" in teacher_system.lower()

    def test_teacher_system_lists_all_eight_tools(self, teacher_system):
        for tool in TOOL_NAMES:
            assert tool in teacher_system, f"Tool '{tool}' missing from TEACHER_SYSTEM"

    def test_teacher_system_mentions_age_last_birthday(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_teacher_system_mentions_get_current_date_first(self, teacher_system):
        """get_current_date should be called first for date-relative calculations."""
        assert "get_current_date" in teacher_system

    def test_teacher_system_citation_format_present(self, teacher_system):
        """Inline citation marker format [[Sn]] must be described."""
        assert "[[S" in teacher_system, "Citation marker format [[Sn]] not found"

    def test_teacher_system_citation_format_regex(self, teacher_system):
        """The prompt should contain at least one example of [[Sx]] marker."""
        pattern = r"\[\[S\d+\]\]"
        assert re.search(pattern, teacher_system), (
            "No citation marker example matching [[Sn]] found in TEACHER_SYSTEM"
        )

    def test_teacher_system_no_guess_instruction(self, teacher_system):
        """Should instruct agent never to guess product details."""
        assert "never guess" in teacher_system.lower() or "do not guess" in teacher_system.lower()

    def test_teacher_system_mentions_discovery_questions(self, teacher_system):
        lower = teacher_system.lower()
        assert "discovery" in lower or "discover" in lower

    def test_teacher_system_mentions_premium(self, teacher_system):
        assert "premium" in teacher_system.lower()

    def test_teacher_system_mentions_exercises_or_scenarios(self, teacher_system):
        lower = teacher_system.lower()
        assert "exercise" in lower or "scenario" in lower or "quiz" in lower

    def test_teacher_system_encourages_interaction(self, teacher_system):
        lower = teacher_system.lower()
        assert "ask" in lower or "interactive" in lower or "engaging" in lower


class TestTeacherSystemToolDescriptions:
    @pytest.mark.parametrize("tool", TOOL_NAMES)
    def test_tool_appears_in_teacher_prompt(self, teacher_system, tool):
        assert tool in teacher_system

    def test_compare_plans_description_mentions_attribute(self, teacher_system):
        """compare_plans should mention comparing attributes."""
        idx = teacher_system.index("compare_plans")
        excerpt = teacher_system[idx : idx + 200]
        lower = excerpt.lower()
        assert "deductible" in lower or "attribute" in lower or "compare" in lower

    def test_lookup_exclusions_description_mentions_exclusion(self, teacher_system):
        idx = teacher_system.index("lookup_exclusions")
        excerpt = teacher_system[idx : idx + 200]
        lower = excerpt.lower()
        assert "exclud" in lower or "not covered" in lower or "restriction" in lower

    def test_search_claim_procedure_description_mentions_claim(self, teacher_system):
        idx = teacher_system.index("search_claim_procedure")
        excerpt = teacher_system[idx : idx + 200]
        lower = excerpt.lower()
        assert "claim" in lower or "reimburse" in lower or "document" in lower

    def test_lookup_hospital_network_description_mentions_hospital(self, teacher_system):
        idx = teacher_system.index("lookup_hospital_network")
        excerpt = teacher_system[idx : idx + 200]
        lower = excerpt.lower()
        assert "hospital" in lower or "network" in lower


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM tests
# ---------------------------------------------------------------------------


class TestAssessorSystemPresence:
    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM constant missing"

    def test_assessor_system_is_string(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_assessor_system_is_not_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0

    def test_assessor_system_min_length(self, assessor_system):
        assert len(assessor_system) >= 200


class TestAssessorSystemTemplatePlaceholders:
    def test_profile_placeholder_present(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_conversation_placeholder_present(self, assessor_system):
        assert "{conversation}" in assessor_system

    def test_format_with_profile_and_conversation(self, assessor_system):
        """Template substitution should not raise."""
        rendered = assessor_system.format(
            profile="Test profile: 35-year-old with two dependents.",
            conversation="Agent: Hello!\nCustomer: Hi!",
        )
        assert "Test profile" in rendered
        assert "Agent: Hello!" in rendered

    def test_format_with_empty_profile(self, assessor_system):
        rendered = assessor_system.format(profile="", conversation="some conversation")
        assert isinstance(rendered, str)

    def test_format_with_empty_conversation(self, assessor_system):
        rendered = assessor_system.format(profile="some profile", conversation="")
        assert isinstance(rendered, str)

    def test_format_with_multiline_conversation(self, assessor_system):
        conversation = (
            "Agent: Good morning!\n"
            "Customer: Good morning.\n"
            "Agent: I'd like to tell you about our Generations II plan.\n"
            "Customer: Sure, go ahead."
        )
        rendered = assessor_system.format(profile="35-year-old non-smoker", conversation=conversation)
        assert "Generations II" in rendered

    def test_format_with_synthetic_profile(self, assessor_system):
        """Use synthetic data: Sun Life Generations II customer scenario."""
        profile = (
            "Customer is 45 years old, looking for whole life protection, "
            "interested in Sun Life Generations II plan."
        )
        conversation = (
            "Agent: The Generations II plan offers guaranteed lifelong protection.\n"
            "Customer: What is the annual premium?\n"
            "Agent: It depends on your age and coverage amount."
        )
        rendered = assessor_system.format(profile=profile, conversation=conversation)
        assert "Generations II" in rendered
        assert "45 years old" in rendered


class TestAssessorSystemContent:
    def test_assessor_mentions_accuracy_assessment(self, assessor_system):
        lower = assessor_system.lower()
        assert "accuracy" in lower or "assess" in lower

    def test_assessor_lists_all_eight_tools(self, assessor_system):
        for tool in TOOL_NAMES:
            assert tool in assessor_system, f"Tool '{tool}' missing from ASSESSOR_SYSTEM"

    def test_assessor_mentions_five_dimensions(self, assessor_system):
        for dimension in ASSESSOR_DIMENSIONS:
            assert dimension in assessor_system, (
                f"Assessment dimension '{dimension}' missing from ASSESSOR_SYSTEM"
            )

    def test_assessor_overall_score_format(self, assessor_system):
        """Must describe an Overall Score: X/10 format."""
        assert "Overall Score" in assessor_system
        assert "/10" in assessor_system

    def test_assessor_mentions_workflow(self, assessor_system):
        assert "Workflow" in assessor_system or "workflow" in assessor_system.lower()

    def test_assessor_mentions_age_last_birthday(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_assessor_mentions_get_current_date(self, assessor_system):
        assert "get_current_date" in assessor_system

    def test_assessor_mentions_correct_incorrect_markers(self, assessor_system):
        """Should include ✓ Correct / ✗ Incorrect markers."""
        assert "Correct" in assessor_system
        assert "Incorrect" in assessor_system

    def test_assessor_mentions_partially_correct(self, assessor_system):
        assert "Partially correct" in assessor_system or "Partially Correct" in assessor_system

    def test_assessor_mentions_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system or "Strengths" in assessor_system

    def test_assessor_mentions_areas_to_improve(self, assessor_system):
        lower = assessor_system.lower()
        assert "areas to improve" in lower or "improve" in lower

    def test_assessor_mentions_do_not_rely_on_memory(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower or "do not rely" in lower

    def test_assessor_mentions_verify(self, assessor_system):
        lower = assessor_system.lower()
        assert "verify" in lower

    def test_assessor_mentions_trainee(self, assessor_system):
        lower = assessor_system.lower()
        assert "trainee" in lower

    def test_assessor_mentions_roleplay(self, assessor_system):
        lower = assessor_system.lower()
        assert "roleplay" in lower

    def test_assessor_section_headers_format(self, assessor_system):
        """Headers should use markdown ### format for each dimension."""
        assert "###" in assessor_system

    def test_assessor_product_knowledge_dimension_has_fact_check_instruction(self, assessor_system):
        """Product Knowledge section should mention verifying claims."""
        idx = assessor_system.index("Product Knowledge")
        excerpt = assessor_system[idx : idx + 300]
        lower = excerpt.lower()
        assert "claim" in lower or "verif" in lower or "factual" in lower


class TestAssessorSystemWorkflow:
    def test_workflow_step_1_read_conversation(self, assessor_system):
        """Step 1 should mention reading the conversation."""
        lower = assessor_system.lower()
        assert "read" in lower or "identify" in lower

    def test_workflow_step_2_call_tools(self, assessor_system):
        """Step 2 should mention calling tools."""
        lower = assessor_system.lower()
        assert "call" in lower or "retrieve" in lower or "tool" in lower

    def test_workflow_step_3_write_assessment(self, assessor_system):
        """Step 3 should mention writing the assessment."""
        lower = assessor_system.lower()
        assert "write" in lower or "assessment" in lower

    def test_workflow_list_products_first_hint(self, assessor_system):
        """Should advise using list_products first when unsure of product name."""
        assert "list_products" in assessor_system
        lower = assessor_system.lower()
        assert "unsure" in lower or "exact product name" in lower or "product name" in lower


# ---------------------------------------------------------------------------
# Consistency tests between TEACHER_SYSTEM and ASSESSOR_SYSTEM
# ---------------------------------------------------------------------------


class TestPromptConsistency:
    @pytest.mark.paramet