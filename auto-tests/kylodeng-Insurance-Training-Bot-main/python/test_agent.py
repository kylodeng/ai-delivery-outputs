```python
"""
Test module for api/agent.py

What is tested:
  - TEACHER_SYSTEM prompt string: presence, key sections, citation format instructions,
    tool list completeness, age/ALB calculation instructions
  - ASSESSOR_SYSTEM prompt string: presence, key sections, tool list completeness,
    five assessment dimensions, scoring format, age/ALB instructions, profile/conversation
    placeholder variables
  - Module-level imports and constants exist
  - String formatting behaviour of ASSESSOR_SYSTEM with profile/conversation placeholders
  - Edge cases: empty format inputs, special characters in format inputs, very long inputs

Mocks used:
  - langchain.agents.create_agent is NOT called at module level with side-effects that need
    mocking; the import itself is tested for presence via the module's namespace.
  - No external service calls are made by the constants under test.

TODOs:
  - TODO: Integration tests for teacher_agent and assessor_agent once the agent
    construction helpers (build_teacher_agent, build_assessor_agent or equivalent)
    are exported from api/agent.py — stub tests are included below.
  - TODO: Tests for astream_events streaming behaviour require a live or mocked
    LangGraph runtime — stub tests included.
  - TODO: Tests for ainvoke one-shot assessor behaviour require a live or mocked
    LangGraph runtime — stub tests included.
  - TODO: RAG tool implementations (get_current_date, list_products, etc.) are not
    defined in the provided source; add tests once those callables are exported.
"""

import importlib
import types

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def agent_module():
    """Import api.agent once for the whole test session."""
    import api.agent as mod
    return mod


# ---------------------------------------------------------------------------
# 1. Module-level sanity
# ---------------------------------------------------------------------------

class TestModuleAttributes:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), (
            "TEACHER_SYSTEM constant must be defined at module level"
        )

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), (
            "ASSESSOR_SYSTEM constant must be defined at module level"
        )

    def test_teacher_system_is_str(self, agent_module):
        assert isinstance(agent_module.TEACHER_SYSTEM, str)

    def test_assessor_system_is_str(self, agent_module):
        assert isinstance(agent_module.ASSESSOR_SYSTEM, str)

    def test_teacher_system_non_empty(self, agent_module):
        assert len(agent_module.TEACHER_SYSTEM.strip()) > 0

    def test_assessor_system_non_empty(self, agent_module):
        assert len(agent_module.ASSESSOR_SYSTEM.strip()) > 0

    def test_create_agent_imported(self, agent_module):
        """langchain.agents.create_agent should be importable via the module namespace."""
        assert hasattr(agent_module, "create_agent"), (
            "create_agent must be imported into api.agent"
        )


# ---------------------------------------------------------------------------
# 2. TEACHER_SYSTEM content tests
# ---------------------------------------------------------------------------

EXPECTED_TEACHER_TOOLS = [
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
]

EXPECTED_TEACHER_TOOL_COUNT = 8


class TestTeacherSystemPrompt:
    @pytest.fixture(autouse=True)
    def prompt(self, agent_module):
        self.prompt = agent_module.TEACHER_SYSTEM

    # --- Role definition ---
    def test_mentions_trainer_or_coach(self):
        lower = self.prompt.lower()
        assert "trainer" in lower or "coach" in lower, (
            "TEACHER_SYSTEM should mention the trainer/coach role"
        )

    def test_mentions_insurance(self):
        assert "insurance" in self.prompt.lower()

    # --- Tool list ---
    @pytest.mark.parametrize("tool_name", EXPECTED_TEACHER_TOOLS)
    def test_tool_mentioned(self, tool_name):
        assert tool_name in self.prompt, (
            f"TEACHER_SYSTEM must mention tool '{tool_name}'"
        )

    def test_tool_count_label(self):
        """The prompt should state 'eight tools' (or the number 8)."""
        assert "eight" in self.prompt.lower() or "8" in self.prompt, (
            "TEACHER_SYSTEM should indicate there are eight tools"
        )

    # --- Age / ALB instructions ---
    def test_mentions_alb(self):
        assert "ALB" in self.prompt or "Age Last Birthday" in self.prompt, (
            "TEACHER_SYSTEM must mention Age Last Birthday / ALB"
        )

    def test_get_current_date_called_first_instruction(self):
        lower = self.prompt.lower()
        assert "get_current_date first" in lower or "call get_current_date first" in lower, (
            "TEACHER_SYSTEM must instruct to call get_current_date first for date calculations"
        )

    def test_never_guess_instruction(self):
        assert "never guess" in self.prompt.lower() or "never guess" in self.prompt.lower()

    # --- Citation instructions ---
    def test_citation_format_present(self):
        """The exact citation marker format [[Sn]] should appear."""
        assert "[[S" in self.prompt, (
            "TEACHER_SYSTEM must contain inline citation marker format [[Sn]]"
        )

    def test_citation_example_present(self):
        assert "[[S1]]" in self.prompt, (
            "TEACHER_SYSTEM must include an example citation [[S1]]"
        )

    def test_citation_only_from_retrieved_docs_instruction(self):
        lower = self.prompt.lower()
        assert "retrieved document" in lower or "drawing directly from" in lower

    # --- Behavioural instructions ---
    def test_mentions_exercises_or_scenarios(self):
        lower = self.prompt.lower()
        assert "exercise" in lower or "scenario" in lower or "quiz" in lower

    def test_mentions_discovery_questions(self):
        lower = self.prompt.lower()
        assert "discovery" in lower or "discovery questions" in lower

    def test_encouragement_instruction(self):
        lower = self.prompt.lower()
        assert "encouraging" in lower or "confidence" in lower

    # --- Premium band warning ---
    def test_premium_band_warning(self):
        lower = self.prompt.lower()
        assert "premium band" in lower or "premium" in lower

    def test_example_age_calculation_mentioned(self):
        """Prompt should contain a concrete example of age calculation."""
        assert "January 2020" in self.prompt or "2020" in self.prompt, (
            "TEACHER_SYSTEM should include the age calculation example (e.g. January 2020)"
        )

    # --- No format placeholders that would break rendering ---
    def test_no_unresolved_format_placeholders(self):
        """
        TEACHER_SYSTEM is a static string; it should not contain {profile}
        or {conversation} placeholders that belong to ASSESSOR_SYSTEM.
        """
        assert "{profile}" not in self.prompt
        assert "{conversation}" not in self.prompt


# ---------------------------------------------------------------------------
# 3. ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------

EXPECTED_ASSESSOR_TOOLS = [
    "get_current_date",
    "list_products",
    "search_product",
    "search_all",
    "lookup_hospital_network",
    "compare_plans",
    "lookup_exclusions",
    "search_claim_procedure",
]

FIVE_DIMENSIONS = [
    "First Impression",
    "Needs Discovery",
    "Product Knowledge",
    "Objection Handling",
    "Closing Technique",
]


class TestAssessorSystemPrompt:
    @pytest.fixture(autouse=True)
    def prompt(self, agent_module):
        self.prompt = agent_module.ASSESSOR_SYSTEM

    # --- Placeholders ---
    def test_profile_placeholder_exists(self):
        assert "{profile}" in self.prompt, (
            "ASSESSOR_SYSTEM must contain {profile} placeholder"
        )

    def test_conversation_placeholder_exists(self):
        assert "{conversation}" in self.prompt, (
            "ASSESSOR_SYSTEM must contain {conversation} placeholder"
        )

    # --- Role ---
    def test_mentions_assessor_role(self):
        lower = self.prompt.lower()
        assert "assessment" in lower or "assess" in lower

    def test_mentions_roleplay(self):
        assert "roleplay" in self.prompt.lower() or "role-play" in self.prompt.lower()

    # --- Tools ---
    @pytest.mark.parametrize("tool_name", EXPECTED_ASSESSOR_TOOLS)
    def test_tool_mentioned(self, tool_name):
        assert tool_name in self.prompt, (
            f"ASSESSOR_SYSTEM must mention tool '{tool_name}'"
        )

    def test_tool_count_label(self):
        assert "eight" in self.prompt.lower() or "8" in self.prompt

    # --- Five dimensions ---
    @pytest.mark.parametrize("dimension", FIVE_DIMENSIONS)
    def test_dimension_present(self, dimension):
        assert dimension in self.prompt, (
            f"ASSESSOR_SYSTEM must mention assessment dimension '{dimension}'"
        )

    def test_five_dimensions_count_label(self):
        assert "five" in self.prompt.lower() or "5" in self.prompt

    # --- Scoring format ---
    def test_overall_score_heading(self):
        assert "## Overall Score" in self.prompt or "Overall Score" in self.prompt

    def test_score_out_of_ten_format(self):
        assert "X/10" in self.prompt or "/10" in self.prompt

    def test_correct_incorrect_markers(self):
        assert "✓ Correct" in self.prompt or "Correct" in self.prompt
        assert "✗ Incorrect" in self.prompt or "Incorrect" in self.prompt

    def test_partially_correct_marker(self):
        assert "Partially correct" in self.prompt or "⚠ Partially" in self.prompt

    def test_key_strengths_section(self):
        assert "Key Strengths" in self.prompt or "strengths" in self.prompt.lower()

    def test_areas_to_improve_section(self):
        assert "Areas to Improve" in self.prompt or "improve" in self.prompt.lower()

    # --- Workflow instructions ---
    def test_workflow_step_1_mentioned(self):
        lower = self.prompt.lower()
        assert "identify" in lower or "read the conversation" in lower

    def test_workflow_step_2_tool_call(self):
        lower = self.prompt.lower()
        assert "retrieve" in lower or "call the most appropriate tool" in lower

    def test_workflow_step_3_write_assessment(self):
        lower = self.prompt.lower()
        assert "write" in lower or "full assessment" in lower

    # --- Age / ALB instructions ---
    def test_mentions_alb(self):
        assert "ALB" in self.prompt or "Age Last Birthday" in self.prompt

    def test_get_current_date_first_instruction(self):
        lower = self.prompt.lower()
        assert "get_current_date first" in lower or "call get_current_date" in lower

    def test_outdated_age_flag(self):
        lower = self.prompt.lower()
        assert "outdated age" in lower or "flag it as an error" in lower

    # --- list_products guidance ---
    def test_list_products_guidance_for_unknown_product(self):
        lower = self.prompt.lower()
        assert "list_products first" in lower or "list products first" in lower

    # --- No spurious unresolved placeholders beyond expected ones ---
    def test_only_expected_placeholders(self):
        """
        After removing the two expected placeholders, no further
        single-brace format tokens should remain that would raise
        KeyError on .format().
        """
        cleaned = self.prompt.replace("{profile}", "").replace("{conversation}", "")
        # A naive check: look for remaining { that are not escaped
        import re
        remaining = re.findall(r"\{[^{}]+\}", cleaned)
        assert remaining == [], (
            f"Unexpected format placeholders found: {remaining}"
        )


# ---------------------------------------------------------------------------
# 4. ASSESSOR_SYSTEM string formatting with synthetic data
# ---------------------------------------------------------------------------

SYNTHETIC_PROFILES = [
    # Happy path — realistic profile
    (
        "Name: John Chan, Age: 35, Occupation: Engineer, "
        "Needs: whole life + health coverage",
        "Agent: Good morning, I'd like to tell you about Generations II...\n"
        "Customer: What is covered?\nAgent: It covers lifelong protection with double bonuses.",
    ),
    # Edge case — empty profile
    (
        "",
        "Agent: Hello!\nCustomer: Hi.",
    ),
    # Edge case — empty conversation
    (
        "Name: Jane Doe",
        "",
    ),
    # Edge case — both empty
    ("", ""),
    # Boundary — very long profile
    (
        "Name: " + "A" * 500,
        "Agent: " + "B" * 500,
    ),
    # Special characters in inputs
    (
        'Profile with "quotes" and {braces} and 100% special chars <>&',
        'Conversation with "quotes" and newlines\n\ttabs\n',
    ),
]


class TestAssessorSystemFormatting:
    @pytest.fixture(autouse=True)
    def template(self, agent_module):
        self.template = agent_module.ASSESSOR_SYSTEM

    @pytest.mark.parametrize("profile,conversation", SYNTHETIC_PROFILES)
    def test_format_does_not_raise(self, profile, conversation):
        """Formatting the template with profile and conversation must not raise."""
        try:
            result = self.template.format(profile=profile, conversation=conversation)
        except KeyError as exc:
            pytest.fail(
                f"ASSESSOR_SYSTEM.format() raised KeyError for unexpected placeholder: {exc}"
            )

    @pytest.mark.parametrize("profile,conversation", SYNTHETIC_PROFILES)
    def test_format_inserts_profile(self, profile, conversation):
        result = self.template.format(profile=profile, conversation=conversation)
        if profile:
            assert profile in result, "Formatted string must contain the supplied profile"

    @pytest.mark.parametrize("profile,conversation", SYNTHETIC_PROFILES)
    def test_format_inserts_conversation(self, profile, conversation):
        result = self.template.format(profile=profile, conversation=conversation)
        if conversation:
            assert conversation in result, (
                "Formatted string must contain the supplied conversation"
            )

    def test_format_result_is_string(self):
        result = self.template.format(
            profile="test profile", conversation="test conversation"
        )
        assert isinstance(result, str)

    def test_format_result_non_empty(self):
        result = self.template.format(
            profile="test profile", conversation="test conversation"
        )
        assert len(result.strip()) > 0

    def test_format_profile_placeholder_replaced(self):
        result = self.template.format(profile="MY_PROFILE", conversation="MY_CONV")
        assert "{profile}" not in result

    def test_format_conversation_placeholder_replaced(self):
        result = self.template.format(profile="MY_PROFILE", conversation="MY_CONV")
        assert "{conversation}" not in result

    def test_format_with_special_curly_braces_in_profile(self):
        """
        If the profile itself contains literal curly braces, they must not
        interf