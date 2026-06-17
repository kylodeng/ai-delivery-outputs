"""
Test suite for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, content requirements, tool mentions, citation format
- ASSESSOR_SYSTEM prompt string: presence, content requirements, tool mentions, format sections
- Module-level constants: type checks, non-empty, expected structural patterns
- create_agent import / call behaviour (mocked)
- Prompt template variable interpolation ({profile}, {conversation} in ASSESSOR_SYSTEM)
- Tool name completeness and consistency between the two system prompts
- Citation marker format documentation in TEACHER_SYSTEM
- ASSESSOR_SYSTEM scoring section structure
- Edge/boundary cases: empty substitution, whitespace, encoding

Mocks used:
- langchain.agents.create_agent (patched via unittest.mock.patch to avoid real LLM calls)

TODOs:
- TODO: Integration tests for actual LangGraph agent execution (requires LLM credentials & graph wiring)
- TODO: Tests for astream_events streaming behaviour (requires async LangGraph runtime)
- TODO: Tests for ainvoke one-shot assessor behaviour (requires async LangGraph runtime)
- TODO: Tests for RAG tool integration (get_current_date, list_products, search_product, etc.)
- TODO: Tests for the eight tool implementations referenced in the prompts (not defined in this file)
"""

import re
import types
import importlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test so that patches apply cleanly
# ---------------------------------------------------------------------------

MODULE_PATH = "api.agent"


def _import_agent():
    """Import (or re-import) api.agent with create_agent mocked."""
    mock_create_agent = MagicMock(return_value=MagicMock(name="mock_agent"))
    with patch("langchain.agents.create_agent", mock_create_agent):
        import importlib
        import sys

        # Force reload so the mock is active during module-level execution
        if MODULE_PATH in sys.modules:
            mod = importlib.reload(sys.modules[MODULE_PATH])
        else:
            mod = importlib.import_module(MODULE_PATH)
    return mod


@pytest.fixture(scope="module")
def agent_module():
    """Module-scoped fixture: import api.agent once for the whole test session."""
    return _import_agent()


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Expected tool names that must appear in BOTH prompts
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
# 1. Module import & constant existence
# ---------------------------------------------------------------------------


class TestModuleImport:
    def test_module_imports_without_error(self):
        mod = _import_agent()
        assert mod is not None

    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_teacher_system_is_string(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_assessor_system_is_string(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_teacher_system_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0


# ---------------------------------------------------------------------------
# 2. TEACHER_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestTeacherSystem:
    def test_contains_role_description(self, teacher_system):
        assert "insurance sales trainer" in teacher_system.lower() or \
               "insurance sales trainer" in teacher_system

    def test_mentions_eight_tools(self, teacher_system):
        assert "eight tools" in teacher_system or "8 tools" in teacher_system

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_mentioned(self, teacher_system, tool_name):
        assert tool_name in teacher_system, (
            f"Tool '{tool_name}' not found in TEACHER_SYSTEM"
        )

    def test_citation_marker_format_documented(self, teacher_system):
        """The prompt must document the [[Sn]] citation format."""
        assert "[[S" in teacher_system

    def test_citation_example_present(self, teacher_system):
        """A concrete citation example like [[S1]] should appear."""
        pattern = re.compile(r"\[\[S\d+\]\]")
        assert pattern.search(teacher_system), (
            "No [[Sn]] citation example found in TEACHER_SYSTEM"
        )

    def test_age_last_birthday_mentioned(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_get_current_date_priority_instruction(self, teacher_system):
        """The prompt should instruct to call get_current_date first for date calculations."""
        lower = teacher_system.lower()
        assert "get_current_date" in lower or "current_date" in lower

    def test_never_guess_product_details(self, teacher_system):
        assert "Never guess" in teacher_system or "never guess" in teacher_system.lower()

    def test_premium_calculation_warning(self, teacher_system):
        lower = teacher_system.lower()
        assert "premium" in lower

    def test_does_not_contain_placeholder_braces(self, teacher_system):
        """TEACHER_SYSTEM must not contain unfilled {variable} placeholders."""
        # Curly braces used for format strings would be a bug in the teacher prompt
        pattern = re.compile(r"\{(?!S\d)[a-zA-Z_][a-zA-Z0-9_]*\}")
        matches = pattern.findall(teacher_system)
        assert matches == [], (
            f"Unexpected template placeholders found in TEACHER_SYSTEM: {matches}"
        )

    def test_encouragement_language(self, teacher_system):
        lower = teacher_system.lower()
        # The prompt should be encouraging/interactive
        keywords = ["engaging", "encouraging", "confidence", "interactive"]
        found = [kw for kw in keywords if kw in lower]
        assert len(found) >= 1, (
            f"Expected at least one engagement keyword in TEACHER_SYSTEM, found none among {keywords}"
        )

    def test_hospital_network_tool_described(self, teacher_system):
        assert "lookup_hospital_network" in teacher_system

    def test_no_trailing_null_bytes(self, teacher_system):
        assert "\x00" not in teacher_system

    def test_utf8_encodable(self, teacher_system):
        encoded = teacher_system.encode("utf-8")
        assert len(encoded) > 0

    def test_minimum_length(self, teacher_system):
        """A meaningful system prompt should be at least 500 characters."""
        assert len(teacher_system) >= 500


# ---------------------------------------------------------------------------
# 3. ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------


class TestAssessorSystem:
    def test_contains_role_description(self, assessor_system):
        lower = assessor_system.lower()
        assert "assessment" in lower or "assessor" in lower

    def test_mentions_eight_tools(self, assessor_system):
        assert "eight tools" in assessor_system or "8 tools" in assessor_system

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_mentioned(self, assessor_system, tool_name):
        assert tool_name in assessor_system, (
            f"Tool '{tool_name}' not found in ASSESSOR_SYSTEM"
        )

    def test_profile_placeholder_present(self, assessor_system):
        """ASSESSOR_SYSTEM must contain {profile} for dynamic injection."""
        assert "{profile}" in assessor_system

    def test_conversation_placeholder_present(self, assessor_system):
        """ASSESSOR_SYSTEM must contain {conversation} for dynamic injection."""
        assert "{conversation}" in assessor_system

    def test_overall_score_section(self, assessor_system):
        assert "Overall Score" in assessor_system

    def test_five_dimensions_mentioned(self, assessor_system):
        assert "five dimensions" in assessor_system or "5 dimensions" in assessor_system.lower()

    def test_first_impression_section(self, assessor_system):
        assert "First Impression" in assessor_system

    def test_needs_discovery_section(self, assessor_system):
        assert "Needs Discovery" in assessor_system

    def test_product_knowledge_section(self, assessor_system):
        assert "Product Knowledge" in assessor_system

    def test_objection_handling_section(self, assessor_system):
        assert "Objection Handling" in assessor_system

    def test_closing_technique_section(self, assessor_system):
        assert "Closing Technique" in assessor_system

    def test_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system

    def test_areas_to_improve_section(self, assessor_system):
        assert "Areas to Improve" in assessor_system

    def test_scoring_format_x_out_of_10(self, assessor_system):
        """Each scored dimension should follow the X/10 pattern."""
        pattern = re.compile(r"X/10")
        matches = pattern.findall(assessor_system)
        # Expect at least 6: one overall + five dimensions
        assert len(matches) >= 6, (
            f"Expected at least 6 'X/10' placeholders, found {len(matches)}"
        )

    def test_age_last_birthday_or_alb_mentioned(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_get_current_date_priority_instruction(self, assessor_system):
        assert "get_current_date" in assessor_system

    def test_workflow_steps_numbered(self, assessor_system):
        """The workflow should have at least steps 1, 2, 3."""
        assert "1." in assessor_system
        assert "2." in assessor_system
        assert "3." in assessor_system

    def test_correct_incorrect_markers(self, assessor_system):
        """Assessment rubric should include ✓ Correct and ✗ Incorrect markers."""
        assert "✓ Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system

    def test_partially_correct_marker(self, assessor_system):
        assert "Partially correct" in assessor_system or "⚠" in assessor_system

    def test_no_trailing_null_bytes(self, assessor_system):
        assert "\x00" not in assessor_system

    def test_utf8_encodable(self, assessor_system):
        encoded = assessor_system.encode("utf-8")
        assert len(encoded) > 0

    def test_minimum_length(self, assessor_system):
        """A meaningful assessor prompt should be at least 500 characters."""
        assert len(assessor_system) >= 500

    def test_only_known_placeholders(self, assessor_system):
        """Only {profile} and {conversation} are valid placeholders."""
        pattern = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
        found = set(pattern.findall(assessor_system))
        allowed = {"profile", "conversation"}
        unexpected = found - allowed
        assert unexpected == set(), (
            f"Unexpected placeholders in ASSESSOR_SYSTEM: {unexpected}"
        )


# ---------------------------------------------------------------------------
# 4. Prompt interpolation / template rendering tests
# ---------------------------------------------------------------------------


class TestAssessorSystemInterpolation:
    SAMPLE_PROFILE = (
        "Customer: Jane Doe, 35 years old, interested in whole life insurance, "
        "budget HKD 5,000/year, has pre-existing hypertension."
    )
    SAMPLE_CONVERSATION = (
        "Agent: Good morning! I'd like to tell you about Generations II.\n"
        "Customer: What is covered?\n"
        "Agent: It covers lifelong protection and includes a double bonus feature."
    )

    def test_basic_interpolation(self, assessor_system):
        rendered = assessor_system.format(
            profile=self.SAMPLE_PROFILE,
            conversation=self.SAMPLE_CONVERSATION,
        )
        assert self.SAMPLE_PROFILE in rendered
        assert self.SAMPLE_CONVERSATION in rendered

    def test_interpolation_removes_placeholders(self, assessor_system):
        rendered = assessor_system.format(
            profile=self.SAMPLE_PROFILE,
            conversation=self.SAMPLE_CONVERSATION,
        )
        assert "{profile}" not in rendered
        assert "{conversation}" not in rendered

    def test_interpolation_with_empty_profile(self, assessor_system):
        rendered = assessor_system.format(profile="", conversation=self.SAMPLE_CONVERSATION)
        assert self.SAMPLE_CONVERSATION in rendered

    def test_interpolation_with_empty_conversation(self, assessor_system):
        rendered = assessor_system.format(profile=self.SAMPLE_PROFILE, conversation="")
        assert self.SAMPLE_PROFILE in rendered

    def test_interpolation_with_both_empty(self, assessor_system):
        rendered = assessor_system.format(profile="", conversation="")
        # Should not raise; rest of the prompt is still intact
        assert "Overall Score" in rendered

    def test_interpolation_with_multiline_conversation(self, assessor_system):
        long_conv = "\n".join(
            [f"Agent: Statement {i}\nCustomer: Response {i}" for i in range(20)]
        )
        rendered = assessor_system.format(profile=self.SAMPLE_PROFILE, conversation=long_conv)
        assert long_conv in rendered

    def test_interpolation_with_special_characters(self, assessor_system):
        special_profile = "Customer: O'Brien & Sons; age=40; budget <HKD 10,000>"
        rendered = assessor_system.format(
            profile=special_profile,
            conversation=self.SAMPLE_CONVERSATION,
        )
        assert special_profile in rendered

    def test_interpolation_with_unicode(self, assessor_system):
        unicode_profile = "客户：李小明，40岁，香港居民"
        rendered = assessor_system.format(
            profile=unicode_profile,
            conversation=self.SAMPLE_CONVERSATION,
        )
        assert unicode_profile in rendered

    @pytest.mark.parametrize(
        "profile,conversation",
        [
            ("", ""),
            ("Profile A", "Conv A"),
            ("Profile with\nnewlines", "Conv with\nnewlines"),
            ("Single char", "X"),
        ],
    )
    def test_interpolation_parametrized(self, assessor_system, profile, conversation):
        rendered = assessor_system.format(profile=profile, conversation=conversation)
        assert isinstance(rendered, str)
        assert len(rendered) > 0


# ---------------------------------------------------------------------------
# 5. Tool-list consistency between the two prompts
# ---------------------------------------------------------------------------


class TestToolConsistency:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_in_teacher_system(self, teacher_system, tool_name):
        assert tool_name in teacher_system

    @pytest.