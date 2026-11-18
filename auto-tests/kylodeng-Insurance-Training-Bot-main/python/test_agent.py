"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, format, key content sections, citation format instructions
- ASSESSOR_SYSTEM prompt string: presence, format, key content sections, placeholder variables
- Module-level constants and structure
- Tool list consistency between TEACHER_SYSTEM and ASSESSOR_SYSTEM
- System prompt formatting requirements (citation markers, section headers)
- Edge cases: placeholder substitution in ASSESSOR_SYSTEM, tool name completeness

Mocks used:
- unittest.mock.patch for `langchain.agents.create_agent` (imported at module level)
- No real LangChain / LLM calls are made

TODOs:
- TODO: Test actual agent invocation (teacher_agent, assessor_agent) once agent factory
        functions are exposed as public callables
- TODO: Test astream_events streaming behaviour for teacher agent (requires async fixtures
        and a live or mock LangGraph runtime)
- TODO: Test ainvoke one-shot behaviour for assessor agent (requires async fixtures)
- TODO: Test RAG tool wiring — verify each of the 8 tools is correctly bound to both agents
- TODO: Test ASSESSOR_SYSTEM with real profile/conversation substitution end-to-end
- TODO: Verify citation marker format [[Sn]] is honoured in actual LLM responses (integration)
"""

import re
import string
import pytest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Patch the external dependency before importing the module under test so that
# `create_agent` is never called for real during the import phase.
# ---------------------------------------------------------------------------
with patch("langchain.agents.create_agent", return_value=MagicMock()):
    import api.agent as agent_module
    from api.agent import TEACHER_SYSTEM, ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Helpers
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

ASSESSOR_PLACEHOLDERS = ["profile", "conversation"]


# ===========================================================================
# TEACHER_SYSTEM tests
# ===========================================================================


class TestTeacherSystemPrompt:
    """Tests for the TEACHER_SYSTEM constant."""

    def test_teacher_system_is_string(self):
        assert isinstance(TEACHER_SYSTEM, str)

    def test_teacher_system_non_empty(self):
        assert len(TEACHER_SYSTEM.strip()) > 0

    def test_teacher_system_contains_role_declaration(self):
        assert "expert insurance sales trainer" in TEACHER_SYSTEM

    def test_teacher_system_contains_all_eight_tools(self):
        for tool in EXPECTED_TOOLS:
            assert tool in TEACHER_SYSTEM, (
                f"Tool '{tool}' not mentioned in TEACHER_SYSTEM"
            )

    def test_teacher_system_mentions_age_last_birthday(self):
        assert "Age Last Birthday" in TEACHER_SYSTEM or "ALB" in TEACHER_SYSTEM

    def test_teacher_system_contains_citation_instructions(self):
        # Must instruct the model to use [[Sn]] citation markers
        assert "[[S" in TEACHER_SYSTEM, (
            "TEACHER_SYSTEM must contain citation marker format [[Sn]]"
        )

    def test_teacher_system_citation_format_example(self):
        # The prompt should demonstrate an example citation
        assert "[[S1]]" in TEACHER_SYSTEM

    def test_teacher_system_contains_get_current_date_guidance(self):
        # Agent must be told to call get_current_date first for date-relative questions
        assert "get_current_date" in TEACHER_SYSTEM

    def test_teacher_system_no_unresolved_placeholders(self):
        # TEACHER_SYSTEM should NOT contain {profile} or {conversation} style placeholders
        # since it is a static prompt (unlike ASSESSOR_SYSTEM)
        formatter = string.Formatter()
        fields = [
            field_name
            for _, field_name, _, _ in formatter.parse(TEACHER_SYSTEM)
            if field_name is not None
        ]
        assert fields == [], (
            f"TEACHER_SYSTEM contains unexpected format fields: {fields}"
        )

    def test_teacher_system_encourages_interactivity(self):
        lower = TEACHER_SYSTEM.lower()
        interactive_keywords = ["quiz", "exercise", "scenario", "question"]
        assert any(kw in lower for kw in interactive_keywords), (
            "TEACHER_SYSTEM should encourage interactive/exercise-based learning"
        )

    def test_teacher_system_warns_against_guessing(self):
        lower = TEACHER_SYSTEM.lower()
        assert "never guess" in lower or "do not guess" in lower or "never guess" in lower

    def test_teacher_system_contains_alb_calculation_guidance(self):
        # Must mention that premiums depend on age at policy inception
        assert "policy inception" in TEACHER_SYSTEM

    @pytest.mark.parametrize("tool", EXPECTED_TOOLS)
    def test_teacher_system_tool_present_parametrized(self, tool):
        assert tool in TEACHER_SYSTEM

    def test_teacher_system_has_citations_section_header(self):
        assert "CITATIONS" in TEACHER_SYSTEM

    def test_teacher_system_minimum_length(self):
        # Sanity check: a meaningful system prompt should be at least 500 characters
        assert len(TEACHER_SYSTEM) >= 500

    def test_teacher_system_does_not_start_with_whitespace(self):
        assert TEACHER_SYSTEM == TEACHER_SYSTEM.lstrip("\n")


# ===========================================================================
# ASSESSOR_SYSTEM tests
# ===========================================================================


class TestAssessorSystemPrompt:
    """Tests for the ASSESSOR_SYSTEM constant."""

    def test_assessor_system_is_string(self):
        assert isinstance(ASSESSOR_SYSTEM, str)

    def test_assessor_system_non_empty(self):
        assert len(ASSESSOR_SYSTEM.strip()) > 0

    def test_assessor_system_contains_role_declaration(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "assessment" in lower or "assessor" in lower or "assess" in lower

    def test_assessor_system_contains_all_eight_tools(self):
        for tool in EXPECTED_TOOLS:
            assert tool in ASSESSOR_SYSTEM, (
                f"Tool '{tool}' not mentioned in ASSESSOR_SYSTEM"
            )

    @pytest.mark.parametrize("tool", EXPECTED_TOOLS)
    def test_assessor_system_tool_present_parametrized(self, tool):
        assert tool in ASSESSOR_SYSTEM

    def test_assessor_system_has_profile_placeholder(self):
        assert "{profile}" in ASSESSOR_SYSTEM

    def test_assessor_system_has_conversation_placeholder(self):
        assert "{conversation}" in ASSESSOR_SYSTEM

    def test_assessor_system_placeholders_are_only_expected_ones(self):
        formatter = string.Formatter()
        fields = [
            field_name
            for _, field_name, _, _ in formatter.parse(ASSESSOR_SYSTEM)
            if field_name is not None
        ]
        for field in fields:
            assert field in ASSESSOR_PLACEHOLDERS, (
                f"Unexpected placeholder '{{{field}}}' found in ASSESSOR_SYSTEM"
            )

    def test_assessor_system_placeholder_substitution_works(self):
        """Verify Python string formatting with the expected placeholders succeeds."""
        rendered = ASSESSOR_SYSTEM.format(
            profile="Test customer profile",
            conversation="Agent: Hello\nCustomer: Hi",
        )
        assert "Test customer profile" in rendered
        assert "Agent: Hello" in rendered

    def test_assessor_system_contains_five_dimensions(self):
        # The five assessment dimensions must all be present
        dimensions = [
            "First Impression",
            "Needs Discovery",
            "Product Knowledge",
            "Objection Handling",
            "Closing",
        ]
        for dim in dimensions:
            assert dim in ASSESSOR_SYSTEM, (
                f"Assessment dimension '{dim}' not found in ASSESSOR_SYSTEM"
            )

    def test_assessor_system_contains_overall_score_format(self):
        assert "Overall Score" in ASSESSOR_SYSTEM

    def test_assessor_system_score_format_x_out_of_10(self):
        # e.g. "X/10" pattern should appear for scoring
        assert re.search(r"X/10", ASSESSOR_SYSTEM), (
            "ASSESSOR_SYSTEM should contain 'X/10' score template"
        )

    def test_assessor_system_contains_verification_instruction(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "verify" in lower or "verification" in lower

    def test_assessor_system_contains_age_last_birthday_guidance(self):
        assert "Age Last Birthday" in ASSESSOR_SYSTEM or "ALB" in ASSESSOR_SYSTEM

    def test_assessor_system_contains_policy_inception_guidance(self):
        assert "policy inception" in ASSESSOR_SYSTEM

    def test_assessor_system_contains_strengths_section(self):
        assert "Strengths" in ASSESSOR_SYSTEM or "strengths" in ASSESSOR_SYSTEM.lower()

    def test_assessor_system_contains_areas_to_improve_section(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "improve" in lower

    def test_assessor_system_contains_workflow_section(self):
        assert "Workflow" in ASSESSOR_SYSTEM or "workflow" in ASSESSOR_SYSTEM.lower()

    def test_assessor_system_minimum_length(self):
        assert len(ASSESSOR_SYSTEM) >= 500

    def test_assessor_system_contains_do_not_rely_on_memory(self):
        lower = ASSESSOR_SYSTEM.lower()
        assert "do not rely on memory" in lower or "not rely on memory" in lower

    def test_assessor_system_product_knowledge_dimension_check_format(self):
        # Must include correct / incorrect / partial markers
        assert "✓ Correct" in ASSESSOR_SYSTEM or "Correct" in ASSESSOR_SYSTEM
        assert "✗ Incorrect" in ASSESSOR_SYSTEM or "Incorrect" in ASSESSOR_SYSTEM


# ===========================================================================
# Tool consistency tests (cross-prompt)
# ===========================================================================


class TestToolConsistency:
    """Verify that the same 8 tools appear in both system prompts."""

    @pytest.mark.parametrize("tool", EXPECTED_TOOLS)
    def test_tool_in_both_prompts(self, tool):
        assert tool in TEACHER_SYSTEM, (
            f"'{tool}' missing from TEACHER_SYSTEM"
        )
        assert tool in ASSESSOR_SYSTEM, (
            f"'{tool}' missing from ASSESSOR_SYSTEM"
        )

    def test_total_tool_count_teacher(self):
        count = sum(1 for t in EXPECTED_TOOLS if t in TEACHER_SYSTEM)
        assert count == len(EXPECTED_TOOLS), (
            f"Expected {len(EXPECTED_TOOLS)} tools in TEACHER_SYSTEM, found {count}"
        )

    def test_total_tool_count_assessor(self):
        count = sum(1 for t in EXPECTED_TOOLS if t in ASSESSOR_SYSTEM)
        assert count == len(EXPECTED_TOOLS), (
            f"Expected {len(EXPECTED_TOOLS)} tools in ASSESSOR_SYSTEM, found {count}"
        )


# ===========================================================================
# Module-level structure tests
# ===========================================================================


class TestModuleStructure:
    """Tests for module-level attributes and imports."""

    def test_teacher_system_attribute_exists(self):
        assert hasattr(agent_module, "TEACHER_SYSTEM")

    def test_assessor_system_attribute_exists(self):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM")

    def test_create_agent_imported(self):
        # create_agent should have been imported (even if mocked)
        assert hasattr(agent_module, "create_agent")

    def test_module_has_docstring(self):
        assert agent_module.__doc__ is not None
        assert len(agent_module.__doc__.strip()) > 0

    def test_module_docstring_mentions_teacher(self):
        assert "teacher" in agent_module.__doc__.lower()

    def test_module_docstring_mentions_assessor(self):
        assert "assess" in agent_module.__doc__.lower()

    def test_module_docstring_mentions_rag_tools(self):
        assert "rag" in agent_module.__doc__.lower() or "tools" in agent_module.__doc__.lower()


# ===========================================================================
# Synthetic data integration tests (prompt rendering with realistic inputs)
# ===========================================================================


SYNTHETIC_PROFILE_GENERATIONS_II = (
    "Customer is interested in Generations II whole life insurance plan "
    "from Sun Life. They are looking for lifelong protection and estate planning."
)

SYNTHETIC_PROFILE_HOSPITAL_NETWORK = (
    "Customer enquires about designated hospitals in Mainland China and "
    "whether cashless arrangement is available at a Shanghai hospital."
)

SYNTHETIC_CONVERSATIONS = [
    (
        "Agent: The annual deductible for this plan is HKD 3,000.\n"
        "Customer: Can I go to any hospital in Shanghai?\n"
        "Agent: Yes, all Class 3 hospitals in mainland China are covered."
    ),
    (
        "Agent: Generations II offers double bonuses and mental incapacity benefit.\n"
        "Customer: What is the premium for a 50-year-old?\n"
        "Agent: I need to check today's date to calculate the Age Last Birthday first."
    ),
    (
        # Edge case: agent makes no product-specific claims
        "Agent: Hello, how can I help you today?\n"
        "Customer: Just browsing, thanks."
    ),
]


class TestAssessorSystemPromptRendering:
    """Test that ASSESSOR_SYSTEM renders correctly with synthetic data inputs."""

    @pytest.mark.parametrize(
        "profile,conversation",
        [
            (SYNTHETIC_PROFILE_GENERATIONS_II, SYNTHETIC_CONVERSATIONS[0]),
            (SYNTHETIC_PROFILE_HOSPITAL_NETWORK, SYNTHETIC_CONVERSATIONS[1]),
            (SYNTHETIC_PROFILE_GENERATIONS_II, SYNTHETIC_CONVERSATIONS[2]),
        ],
    )
    def test_assessor_renders_with_synthetic_data(self, profile, conversation):
        rendered = ASSESSOR_SYSTEM.format(profile=profile, conversation=conversation)
        assert profile in rendered
        assert conversation in rendered

    def test_assessor_rendered_contains_overall_score_header(self):
        rendered = ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE_GENERATIONS_II,
            conversation=SYNTHETIC_CONVERSATIONS[0],
        )
        assert "Overall Score" in rendered

    def test_assessor_rendered_contains_all_five_dimension_headers(self):
        rendered = ASSESSOR_SYSTEM.format(
            profile=SYNTHETIC_PROFILE_GENERATIONS_II,
            conversation=SYNTHETIC_CONVERSATIONS[1],
        )
        dimensions = [
            "First Impression",
            "Needs Discovery",
            "Product Knowledge",
            "Objection Handling",
            "Closing",
        ]
        for dim in dimensions:
            assert dim in rendered

    def test_assessor_rendered_tools_still_present(self):
        rendered = ASSESSOR_SYSTEM.format(
            profile="profile text",
            conversation="conversation text",
        )
        for tool in EXPECTED_TOOLS:
            assert tool in rendered

    def test_assessor_empty_profile_still_renders(self):
        """Edge case: empty profile string should not raise."""
        rendered = ASSESSOR_SYSTEM.format(profile="", conversation="some text")
        assert "some text