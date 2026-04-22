"""
Test module for api/agent.py

What is tested:
- TEACHER_SYSTEM prompt string: presence, format, key content sections
- ASSESSOR_SYSTEM prompt string: presence, format, key content sections, placeholder variables
- Module-level constants: existence, types, non-emptiness
- Tool references: all 8 tools mentioned in both system prompts
- Citation format instructions in TEACHER_SYSTEM
- Placeholder substitution in ASSESSOR_SYSTEM ({profile}, {conversation})
- Edge cases: prompt encoding, whitespace, required keywords
- create_agent import availability

Mocks used:
- langchain.agents.create_agent is patched where invocation behavior is tested
- No real LLM/LangGraph/external service calls are made

TODOs:
- TODO: test actual agent graph construction (requires LangGraph StateGraph + LLM config)
- TODO: test teacher_agent astream_events behavior (requires async LLM mock)
- TODO: test assessor_agent ainvoke behavior (requires async LLM mock)
- TODO: test RAG tool integration (requires vector store / retriever mocks)
- TODO: test ASSESSOR_SYSTEM with real profile/conversation substitution end-to-end
"""

import importlib
import re
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – import the module under test with create_agent stubbed out so we
# don't need a real LangChain installation wired up.
# ---------------------------------------------------------------------------

_AGENT_MODULE_PATH = "api.agent"


def _import_agent_module():
    """Import api.agent, stubbing heavy dependencies if necessary."""
    # Ensure langchain stub exists before import so the module loads cleanly
    if "langchain.agents" not in sys.modules:
        stub = MagicMock()
        sys.modules.setdefault("langchain", MagicMock())
        sys.modules.setdefault("langchain.agents", stub)

    if _AGENT_MODULE_PATH in sys.modules:
        return sys.modules[_AGENT_MODULE_PATH]

    with patch.dict(
        sys.modules,
        {
            "langchain": sys.modules.get("langchain", MagicMock()),
            "langchain.agents": sys.modules.get("langchain.agents", MagicMock()),
        },
    ):
        spec = importlib.util.find_spec(_AGENT_MODULE_PATH)
        if spec is None:
            pytest.skip(f"Module {_AGENT_MODULE_PATH} not found on sys.path")
        module = importlib.import_module(_AGENT_MODULE_PATH)
    return module


@pytest.fixture(scope="module")
def agent_module():
    return _import_agent_module()


@pytest.fixture(scope="module")
def teacher_system(agent_module):
    return agent_module.TEACHER_SYSTEM


@pytest.fixture(scope="module")
def assessor_system(agent_module):
    return agent_module.ASSESSOR_SYSTEM


# ---------------------------------------------------------------------------
# Constants existence & type
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_teacher_system_exists(self, agent_module):
        assert hasattr(agent_module, "TEACHER_SYSTEM"), "TEACHER_SYSTEM constant missing"

    def test_assessor_system_exists(self, agent_module):
        assert hasattr(agent_module, "ASSESSOR_SYSTEM"), "ASSESSOR_SYSTEM constant missing"

    def test_teacher_system_is_string(self, teacher_system):
        assert isinstance(teacher_system, str)

    def test_assessor_system_is_string(self, assessor_system):
        assert isinstance(assessor_system, str)

    def test_teacher_system_non_empty(self, teacher_system):
        assert len(teacher_system.strip()) > 0

    def test_assessor_system_non_empty(self, assessor_system):
        assert len(assessor_system.strip()) > 0

    def test_create_agent_imported(self, agent_module):
        assert hasattr(agent_module, "create_agent"), "create_agent not imported into module"


# ---------------------------------------------------------------------------
# TEACHER_SYSTEM content tests
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


class TestTeacherSystemContent:
    def test_teacher_system_mentions_trainer_role(self, teacher_system):
        assert "insurance sales trainer" in teacher_system.lower() or "trainer" in teacher_system

    def test_teacher_system_mentions_agent(self, teacher_system):
        lower = teacher_system.lower()
        assert "agent" in lower or "trainee" in lower

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_teacher_system_mentions_each_tool(self, teacher_system, tool_name):
        assert tool_name in teacher_system, f"Tool '{tool_name}' not referenced in TEACHER_SYSTEM"

    def test_teacher_system_tool_count_is_eight(self, teacher_system):
        """All 8 tools should appear at least once."""
        found = [t for t in EXPECTED_TOOLS if t in teacher_system]
        assert len(found) == 8, f"Expected 8 tools, found {len(found)}: {found}"

    def test_teacher_system_citation_format_present(self, teacher_system):
        """Inline citation marker format [[Sn]] must be documented."""
        assert "[[S" in teacher_system, "Citation marker format [[Sn]] missing from TEACHER_SYSTEM"

    def test_teacher_system_citation_example(self, teacher_system):
        """Concrete citation example should be present."""
        assert "[[S1]]" in teacher_system

    def test_teacher_system_mentions_age_last_birthday(self, teacher_system):
        assert "Age Last Birthday" in teacher_system or "ALB" in teacher_system

    def test_teacher_system_warns_about_date_calculation(self, teacher_system):
        lower = teacher_system.lower()
        assert "get_current_date" in teacher_system
        assert "age" in lower

    def test_teacher_system_mentions_never_guess(self, teacher_system):
        lower = teacher_system.lower()
        assert "never guess" in lower or "never" in lower

    def test_teacher_system_mentions_discovery_questions(self, teacher_system):
        lower = teacher_system.lower()
        assert "discovery" in lower or "question" in lower

    def test_teacher_system_mentions_exercises_or_scenarios(self, teacher_system):
        lower = teacher_system.lower()
        assert any(kw in lower for kw in ["exercise", "scenario", "quiz", "roleplay", "role"])

    def test_teacher_system_get_current_date_instruction(self, teacher_system):
        """get_current_date should be called *first* for date-related questions."""
        lower = teacher_system.lower()
        idx_tool = lower.find("get_current_date")
        assert idx_tool != -1
        # The word "first" should appear near the tool instruction
        snippet = lower[max(0, idx_tool - 100): idx_tool + 200]
        assert "first" in snippet

    def test_teacher_system_has_eight_tools_section(self, teacher_system):
        """Prompt should explicitly state the number of tools available."""
        assert "eight" in teacher_system.lower() or "8" in teacher_system

    def test_teacher_system_citations_section_present(self, teacher_system):
        assert "CITATIONS" in teacher_system or "citation" in teacher_system.lower()

    def test_teacher_system_does_not_contain_unresolved_placeholders(self, teacher_system):
        """Teacher prompt should have no {profile} or {conversation} style placeholders."""
        placeholders = re.findall(r"\{[a-zA-Z_]+\}", teacher_system)
        assert placeholders == [], f"Unexpected placeholders in TEACHER_SYSTEM: {placeholders}"

    def test_teacher_system_source_id_format_documented(self, teacher_system):
        """Source ID bracket format [S1: ...] should be illustrated."""
        assert "[S1:" in teacher_system or "[S1]" in teacher_system

    def test_teacher_system_encourages_interaction(self, teacher_system):
        lower = teacher_system.lower()
        assert any(kw in lower for kw in ["engaging", "ask", "interactive", "confidence"])

    def test_teacher_system_mentions_alb_premium_band(self, teacher_system):
        lower = teacher_system.lower()
        assert "premium" in lower

    def test_teacher_system_minimum_length(self, teacher_system):
        """Sanity check: the prompt should be reasonably detailed."""
        assert len(teacher_system) >= 500


# ---------------------------------------------------------------------------
# ASSESSOR_SYSTEM content tests
# ---------------------------------------------------------------------------

class TestAssessorSystemContent:
    def test_assessor_system_mentions_assessor_role(self, assessor_system):
        lower = assessor_system.lower()
        assert "assessment" in lower or "assessor" in lower or "accuracy" in lower

    def test_assessor_system_has_profile_placeholder(self, assessor_system):
        assert "{profile}" in assessor_system

    def test_assessor_system_has_conversation_placeholder(self, assessor_system):
        assert "{conversation}" in assessor_system

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_assessor_system_mentions_each_tool(self, assessor_system, tool_name):
        assert tool_name in assessor_system, f"Tool '{tool_name}' not referenced in ASSESSOR_SYSTEM"

    def test_assessor_system_tool_count_is_eight(self, assessor_system):
        found = [t for t in EXPECTED_TOOLS if t in assessor_system]
        assert len(found) == 8, f"Expected 8 tools, found {len(found)}: {found}"

    def test_assessor_system_has_five_dimensions(self, assessor_system):
        """Assessment should mention five scoring dimensions."""
        lower = assessor_system.lower()
        # Count numbered dimension headers
        dimension_matches = re.findall(r"###\s+\d\.", assessor_system)
        assert len(dimension_matches) >= 5, (
            f"Expected at least 5 numbered dimensions, found {len(dimension_matches)}"
        )

    def test_assessor_system_first_impression_dimension(self, assessor_system):
        assert "First Impression" in assessor_system

    def test_assessor_system_needs_discovery_dimension(self, assessor_system):
        assert "Needs Discovery" in assessor_system

    def test_assessor_system_product_knowledge_dimension(self, assessor_system):
        assert "Product Knowledge" in assessor_system

    def test_assessor_system_objection_handling_dimension(self, assessor_system):
        assert "Objection Handling" in assessor_system

    def test_assessor_system_closing_technique_dimension(self, assessor_system):
        assert "Closing Technique" in assessor_system

    def test_assessor_system_overall_score_format(self, assessor_system):
        """Must document the ## Overall Score: X/10 heading."""
        assert "Overall Score" in assessor_system
        assert "/10" in assessor_system

    def test_assessor_system_correct_incorrect_markers(self, assessor_system):
        """Fact-check markers should be documented."""
        assert "✓ Correct" in assessor_system or "Correct" in assessor_system
        assert "✗ Incorrect" in assessor_system or "Incorrect" in assessor_system

    def test_assessor_system_partially_correct_marker(self, assessor_system):
        assert "Partially correct" in assessor_system or "⚠" in assessor_system

    def test_assessor_system_key_strengths_section(self, assessor_system):
        assert "Key Strengths" in assessor_system or "Strengths" in assessor_system

    def test_assessor_system_areas_to_improve_section(self, assessor_system):
        lower = assessor_system.lower()
        assert "areas to improve" in lower or "improve" in lower

    def test_assessor_system_mentions_age_last_birthday(self, assessor_system):
        assert "Age Last Birthday" in assessor_system or "ALB" in assessor_system

    def test_assessor_system_get_current_date_first(self, assessor_system):
        lower = assessor_system.lower()
        idx_tool = lower.find("get_current_date")
        assert idx_tool != -1
        snippet = lower[max(0, idx_tool - 100): idx_tool + 200]
        assert "first" in snippet

    def test_assessor_system_workflow_section(self, assessor_system):
        assert "Workflow" in assessor_system or "workflow" in assessor_system.lower()

    def test_assessor_system_workflow_steps_numbered(self, assessor_system):
        """Workflow should have at least 3 numbered steps."""
        steps = re.findall(r"^\s*\d\.", assessor_system, re.MULTILINE)
        assert len(steps) >= 3, f"Expected ≥3 workflow steps, found {len(steps)}"

    def test_assessor_system_do_not_rely_on_memory(self, assessor_system):
        lower = assessor_system.lower()
        assert "memory" in lower or "do not rely" in lower

    def test_assessor_system_verify_claims_instruction(self, assessor_system):
        lower = assessor_system.lower()
        assert "verify" in lower

    def test_assessor_system_list_products_first_hint(self, assessor_system):
        """list_products should be suggested when product name is uncertain."""
        assert "list_products" in assessor_system
        lower = assessor_system.lower()
        assert "first" in lower

    def test_assessor_system_minimum_length(self, assessor_system):
        assert len(assessor_system) >= 500

    def test_assessor_system_only_expected_placeholders(self, assessor_system):
        """Only {profile} and {conversation} should appear as placeholders."""
        placeholders = set(re.findall(r"\{([a-zA-Z_]+)\}", assessor_system))
        allowed = {"profile", "conversation"}
        unexpected = placeholders - allowed
        assert unexpected == set(), f"Unexpected placeholders: {unexpected}"


# ---------------------------------------------------------------------------
# Placeholder substitution behaviour
# ---------------------------------------------------------------------------

class TestAssessorSystemSubstitution:
    def test_substitution_profile_and_conversation(self, assessor_system):
        rendered = assessor_system.format(
            profile="John Doe, 45, self-employed",
            conversation="Agent: Hello!\nCustomer: Hi!",
        )
        assert "John Doe, 45, self-employed" in rendered
        assert "Agent: Hello!" in rendered

    def test_substitution_empty_profile(self, assessor_system):
        rendered = assessor_system.format(profile="", conversation="some chat")
        assert "{profile}" not in rendered

    def test_substitution_empty_conversation(self, assessor_system):
        rendered = assessor_system.format(profile="Jane", conversation="")
        assert "{conversation}" not in rendered

    def test_substitution_special_characters_in_profile(self, assessor_system):
        profile = "Client: María López, 30 yrs, HK$ 50,000/yr income"
        rendered = assessor_system.format(profile=profile, conversation="—")
        assert "María López" in rendered

    def test_substitution_multiline_conversation(self, assessor_system):
        convo = "Agent: Good morning.\nCustomer: I need life cover.\nAgent: Understood."
        rendered = assessor_system.format(