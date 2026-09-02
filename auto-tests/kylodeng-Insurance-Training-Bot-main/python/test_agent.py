"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, key content sections, citation format, tool listings
- ASSESSOR_SYSTEM prompt string: presence, key content sections, tool listings, format markers
- Module-level constants: existence, types, non-emptiness
- create_agent import and usage (mocked)
- Prompt template variable placeholders ({profile}, {conversation}) in ASSESSOR_SYSTEM
- Tool enumeration consistency between TEACHER_SYSTEM and ASSESSOR_SYSTEM
- Citation format instructions in TEACHER_SYSTEM
- Age/ALB calculation instructions in both prompts
- Edge cases: unexpected whitespace, truncation, encoding

Mocks used:
- langchain.agents.create_agent (patched to avoid real LLM/agent construction)

TODOs:
- TODO: Test actual agent graph construction once LangGraph wiring is exposed publicly
- TODO: Test astream_events streaming behaviour for teacher agent (requires async fixtures + LLM mock)
- TODO: Test ainvoke behaviour for assessor agent (requires async fixtures + LLM mock)
- TODO: Test each RAG tool function independently once tool implementations are importable
- TODO: Test that {profile} and {conversation} are correctly interpolated at runtime
"""

import importlib
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with create_agent mocked so we
# never touch a real LLM / LangGraph dependency during tests.
# ---------------------------------------------------------------------------

AGENT_MODULE_PATH = "api.agent"


def _import_agent_module():
    """Import api.agent with langchain.agents.create_agent stubbed out."""
    mock_create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))

    # Build a minimal fake langchain.agents module if not already present
    if "langchain" not in sys.modules:
        langchain_mod = types.ModuleType("langchain")
        sys.modules["langchain"] = langchain_mod

    if "langchain.agents" not in sys.modules:
        agents_mod = types.ModuleType("langchain.agents")
        sys.modules["langchain.agents"] = agents_mod

    sys.modules["langchain.agents"].create_agent = mock_create_agent

    # Force re-import so the patch is applied
    if AGENT_MODULE_PATH in sys.modules:
        del sys.modules[AGENT_MODULE_PATH]

    with patch.dict(
        "sys.modules",
        {"langchain": sys.modules["langchain"], "langchain.agents": sys.modules["langchain.agents"]},
    ):
        module = importlib.import_module(AGENT_MODULE_PATH)

    return module


@pytest.fixture(scope="module")
def agent_module():
    """Module-scoped fixture: import api.agent once for the entire test session."""
    return _import_agent_module()


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Expected tools – single source of truth for parametrised checks
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

# ---------------------------------------------------------------------------
# 1. Module-level constant existence and type checks
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM constant missing"

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM constant missing"

    def test_teacher_system_is_string(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_string(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_non_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_teacher_system_substantial_length(self, agent_module):
        """Sanity-check: prompt should be at least 500 characters."""
        assert len(agent_module.TEACHER_SYSTEM) >= 500

    def test_assessor_system_substantial_length(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM) >= 500


# ---------------------------------------------------------------------------
# 2. TEACHER_SYSTEM content checks
# ---------------------------------------------------------------------------


class TestTeacherSystemContent:
    def test_teacher_role_description_present(self, teacher_system):
        assert "insurance sales trainer" in teacher_system.lower() or "trainer" in teacher_system.lower()

    def test_teacher_mentions_eight_tools(self, teacher_system):
        assert "eight tools" in teacher_system.lower() or "8 tools" in teacher_system.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_lists_all_tools(self, teacher_system, tool_name):
        assert tool_name in teacher_system, f"Tool '{tool_name}' missing from TEACHER_SYSTEM"

    def test_teacher_citation_format_present(self, teacher_system):
        """Prompt must describe the [[Sn]] inline citation format."""
        assert "[[S" in teacher_system, "Citation marker format [[Sn]] missing from TEACHER_SYSTEM"

    def test_teacher_citation_example_present(self, teacher_system):
        """At least one concrete citation example should appear."""
        assert re.search(r"\[\[S\d+\]\]", teacher_system), (
            "No concrete [[Sn]] citation example found in TEACHER_SYSTEM"
        )

    def test_teacher_alb_instruction_present(self, teacher_system):
        """Age Last Birthday (ALB) instruction must be present."""
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_teacher_age_calculation_order(self, teacher_system):
        """get_current_date must be mentioned before ALB calculation."""
        date_idx = teacher_system.find("get_current_date")
        alb_idx = teacher_system.find("ALB") if "ALB" in teacher_system else teacher_system.find("Age Last Birthday")
        assert date_idx != -1, "get_current_date not found in TEACHER_SYSTEM"
        assert alb_idx != -1, "ALB/Age Last Birthday not found in TEACHER_SYSTEM"
        assert date_idx < alb_idx, "get_current_date should be mentioned before ALB calculation"

    def test_teacher_never_guess_instruction(self, teacher_system):
        assert "never guess" in teacher_system.lower() or "Never guess" in teacher_system

    def test_teacher_engagement_instruction(self, teacher_system):
        keywords = ["engaging", "exercises", "quiz", "interactive", "confidence"]
        assert any(kw in teacher_system.lower() for kw in keywords), (
            "Teacher prompt should encourage engagement/exercises"
        )

    def test_teacher_no_leading_trailing_excessive_whitespace(self, teacher_system):
        """Prompt should not start or end with excessive blank lines."""
        assert not teacher_system.startswith("\n\n\n")
        assert not teacher_system.endswith("\n\n\n")

    def test_teacher_discovery_questions_mentioned(self, teacher_system):
        assert "discovery" in teacher_system.lower()

    def test_teacher_hospital_network_tool_description(self, teacher_system):
        """lookup_hospital_network should mention hospital name/area usage."""
        assert "lookup_hospital_network" in teacher_system
        # The description should reference hospital checks
        idx = teacher_system.find("lookup_hospital_network")
        surrounding = teacher_system[idx: idx + 200]
        assert "hospital" in surrounding.lower()

    def test_teacher_search_product_described(self, teacher_system):
        idx = teacher_system.find("search_product")
        assert idx != -1
        surrounding = teacher_system[idx: idx + 150]
        assert "product" in surrounding.lower()

    def test_teacher_compare_plans_described(self, teacher_system):
        assert "compare_plans" in teacher_system

    def test_teacher_lookup_exclusions_described(self, teacher_system):
        assert "lookup_exclusions" in teacher_system

    def test_teacher_claim_procedure_described(self, teacher_system):
        assert "search_claim_procedure" in teacher_system

    def test_teacher_premium_band_mentioned(self, teacher_system):
        assert "premium" in teacher_system.lower()

    def test_teacher_utf8_encodable(self, teacher_system):
        """Ensure the string contains no characters that break UTF-8 encoding."""
        encoded = teacher_system.encode("utf-8")
        assert len(encoded) > 0


# ---------------------------------------------------------------------------
# 3. ASSESSOR_SYSTEM content checks
# ---------------------------------------------------------------------------


class TestAssessorSystemContent:
    def test_assessor_role_description_present(self, assessor_system):
        assert "assessor" in assessor_system.lower() or "assessment" in assessor_system.lower()

    def test_assessor_profile_placeholder(self, assessor_system):
        """{profile} placeholder must be present for runtime formatting."""
        assert "{profile}" in assessor_system

    def test_assessor_conversation_placeholder(self, assessor_system):
        """{conversation} placeholder must be present for runtime formatting."""
        assert "{conversation}" in assessor_system

    def test_assessor_mentions_eight_tools(self, assessor_system):
        assert "eight tools" in assessor_system.lower() or "8 tools" in assessor_system.lower()

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_lists_all_tools(self, assessor_system, tool_name):
        assert tool_name in assessor_system, f"Tool '{tool_name}' missing from ASSESSOR_SYSTEM"

    def test_assessor_five_dimensions_mentioned(self, assessor_system):
        assert "five dimensions" in assessor_system.lower() or "5 dimensions" in assessor_system.lower() or (
            assessor_system.count("X/10") >= 5
        )

    def test_assessor_overall_score_format(self, assessor_system):
        assert "## Overall Score" in assessor_system

    def test_assessor_first_impression_dimension(self, assessor_system):
        assert "First Impression" in assessor_system

    def test_assessor_needs_discovery_dimension(self, assessor_system):
        assert "Needs Discovery" in assessor_system

    def test_assessor_product_knowledge_dimension(self, assessor_system):
        assert "Product Knowledge" in assessor_system

    def test_assessor_objection_handling_dimension(self, assessor_system):
        assert "Objection Handling" in assessor_system

    def test_assessor_closing_technique_dimension(self, assessor_system):
        assert "Closing Technique" in assessor_system

    def test_assessor_correct_incorrect_markers(self, assessor_system):
        """Assessment output format must include ✓ Correct and ✗ Incorrect markers."""
        assert "✓ Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system

    def test_assessor_partially_correct_marker(self, assessor_system):
        assert "⚠" in assessor_system

    def test_assessor_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system or "✅" in assessor_system

    def test_assessor_areas_to_improve_section(self, assessor_system):
        assert "Areas to Improve" in assessor_system or "⚠️" in assessor_system

    def test_assessor_alb_instruction_present(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_assessor_age_verification_order(self, assessor_system):
        """get_current_date should appear before ALB instruction in assessor prompt."""
        date_idx = assessor_system.find("get_current_date")
        alb_idx = (
            assessor_system.find("ALB")
            if "ALB" in assessor_system
            else assessor_system.find("Age Last Birthday")
        )
        assert date_idx != -1
        assert alb_idx != -1
        assert date_idx < alb_idx

    def test_assessor_workflow_step_1(self, assessor_system):
        """Workflow should instruct reading conversation and identifying claims."""
        assert "1." in assessor_system
        assert "claim" in assessor_system.lower()

    def test_assessor_workflow_step_2(self, assessor_system):
        assert "2." in assessor_system

    def test_assessor_workflow_step_3(self, assessor_system):
        assert "3." in assessor_system

    def test_assessor_do_not_rely_on_memory(self, assessor_system):
        assert "memory" in assessor_system.lower() or "do not rely" in assessor_system.lower()

    def test_assessor_list_products_first_guidance(self, assessor_system):
        """Assessor should be told to use list_products when product name is unsure."""
        assert "list_products" in assessor_system

    def test_assessor_utf8_encodable(self, assessor_system):
        encoded = assessor_system.encode("utf-8")
        assert len(encoded) > 0

    def test_assessor_placeholder_format_string_interpolation(self, assessor_system):
        """Verify {profile} and {conversation} can be successfully interpolated."""
        sample_profile = "35-year-old married professional, 2 children, looking for health cover"
        sample_conversation = (
            "Agent: Good morning! I'd like to tell you about our Generations II plan.\n"
            "Customer: What does it cover?\n"
            "Agent: It provides lifelong protection and grows your family legacy."
        )
        rendered = assessor_system.format(
            profile=sample_profile,
            conversation=sample_conversation,
        )
        assert sample_profile in rendered
        assert sample_conversation in rendered

    def test_assessor_no_unresolved_placeholders_after_format(self, assessor_system):
        """After formatting, no {placeholder} patterns should remain (except escaped ones)."""
        rendered = assessor_system.format(
            profile="test profile",
            conversation="test conversation",
        )
        # Only {profile} and {conversation} should have been in the template
        remaining = re.findall(r"\{[a-zA-Z_]+\}", rendered)
        assert remaining == [], f"Unresolved placeholders after format: {remaining}"


# ---------------------------------------------------------------------------
# 4. Cross-prompt consistency checks
# ---------------------------------------------------------------------------


class TestPromptConsistency:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_both_prompts_list_same_tools(self, teacher_system, assessor_system, tool_name):
        """Both prompts must list exactly the same eight tools."""
        assert tool_name in teacher_system, f"'{tool_name}' missing from TEACHER_SYSTEM"
        assert tool_name in assessor_system, f"'{tool_name}' missing from ASSESSOR_SYSTEM"

    def test_tool_count_teacher(self, teacher_system):