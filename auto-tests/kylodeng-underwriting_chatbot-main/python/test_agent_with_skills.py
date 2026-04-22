"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure
- build_skills_agent() factory function (happy path, edge cases)
- agent() inner node: JSON parsing, tool_call routing, done action, fallback plain text
- execute_tool() inner node: successful tool invocation, tool error payload, unknown tool, exception handling
- router() function (stub — source was truncated before completion)
- JSON normalisation: "type": "function_call" → "action": "tool_call"
- Boundary values: empty history, multi-entry history, malformed JSON, no JSON in content

Mocks used:
- backend.agent.agent_with_skills.LLMS          (LLM factory)
- backend.agent.agent_with_skills._profile_tool  (get_customer_info tool)
- backend.agent.agent_with_skills._lookalike_tool (customer_lookalike tool)
- backend.agent.agent_with_skills._run_underwriting_assessment (risk assessment)
- backend.agent.agent_with_skills._SKILLS_DIR    (patched to a tmp_path fixture)
- pathlib.Path.glob / read_text via the directory mock

TODOs:
- router() function body was truncated in source — full routing logic cannot be tested
- Full LangGraph StateGraph wiring tests require the complete source
- Integration test: build_skills_agent returns a compiled graph and stream works end-to-end
"""

import json
import operator
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with all heavy deps mocked out
# ---------------------------------------------------------------------------

# We patch at import time so we never touch real LLM / tool / assessment code.
_PATCH_TARGETS = {
    "backend.modules.assessment._run_underwriting_assessment": MagicMock(
        return_value=MagicMock()
    ),
    "modules.tools.get_customer_profile": MagicMock(),
    "modules.tools.customer_lookalike": MagicMock(),
    "backend.modules.assessment": MagicMock(),
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def skills_dir(tmp_path):
    """Create a temporary skills directory with two fake skill markdown files."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "skill_a.md").write_text("# Skill A\nDoes A things.")
    (skills / "skill_b.md").write_text("# Skill B\nDoes B things.")
    # index.md should be excluded
    (skills / "index.md").write_text("# Index — should be ignored")
    return skills


@pytest.fixture()
def mock_llm():
    """Return a fake LLM whose .invoke() returns a controllable response object."""
    llm = MagicMock()
    tagged = MagicMock()
    llm.with_config.return_value = tagged
    return llm, tagged


@pytest.fixture()
def mock_llms_cls(mock_llm):
    """Patch LLMS so it returns our mock_llm."""
    llm_instance, tagged = mock_llm
    with patch("backend.agent.agent_with_skills.LLMS") as mock_cls:
        instance = MagicMock()
        instance.get_model.return_value = llm_instance
        mock_cls.return_value = instance
        yield mock_cls, llm_instance, tagged


@pytest.fixture()
def mock_tools():
    """Patch the three tool objects used inside TOOLS."""
    profile = AsyncMock()
    lookalike = AsyncMock()
    risk = AsyncMock()
    with (
        patch("backend.agent.agent_with_skills._profile_tool", profile),
        patch("backend.agent.agent_with_skills._lookalike_tool", lookalike),
        patch("backend.agent.agent_with_skills._run_underwriting_assessment", return_value=risk),
    ):
        yield {"get_customer_info": profile, "customer_lookalike": lookalike, "run_risk_assessment": risk}


@pytest.fixture()
def agent_factory(mock_llms_cls, skills_dir):
    """
    Build and return the (agent_fn, execute_tool_fn) closures from build_skills_agent().

    We patch _SKILLS_DIR to point to our tmp skills_dir so no real filesystem access
    occurs.
    """
    _, llm_instance, tagged = mock_llms_cls

    with patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir):
        # Import here so patches are already in place
        from backend.agent.agent_with_skills import build_skills_agent  # noqa: PLC0415

        # We cannot call build_skills_agent() directly and get the inner
        # functions without also building the graph — so we monkeypatch
        # StateGraph to capture the add_node calls.
        captured_nodes = {}

        original_add_node = MagicMock(side_effect=lambda name, fn: captured_nodes.update({name: fn}))
        original_add_edge = MagicMock()
        original_add_conditional_edges = MagicMock()
        original_compile = MagicMock(return_value=MagicMock())

        with patch("backend.agent.agent_with_skills.StateGraph") as mock_sg:
            graph_instance = MagicMock()
            graph_instance.add_node = original_add_node
            graph_instance.add_edge = original_add_edge
            graph_instance.add_conditional_edges = original_add_conditional_edges
            graph_instance.compile = original_compile
            mock_sg.return_value = graph_instance

            build_skills_agent("anthropic-fast", temperature=0)

        return captured_nodes, tagged


# ---------------------------------------------------------------------------
# AgentState tests
# ---------------------------------------------------------------------------


class TestAgentState:
    def test_typeddict_fields_exist(self):
        from backend.agent.agent_with_skills import AgentState  # noqa: PLC0415

        keys = set(AgentState.__annotations__.keys())
        assert keys == {"question", "history", "logs", "pending_call", "final_answer"}

    def test_history_uses_operator_add(self):
        from backend.agent.agent_with_skills import AgentState  # noqa: PLC0415
        import typing  # noqa: PLC0415

        hints = typing.get_type_hints(AgentState, include_extras=True)
        history_hint = hints["history"]
        # Annotated[list[str], operator.add]
        metadata = getattr(history_hint, "__metadata__", ())
        assert operator.add in metadata

    def test_logs_uses_operator_add(self):
        from backend.agent.agent_with_skills import AgentState  # noqa: PLC0415
        import typing  # noqa: PLC0415

        hints = typing.get_type_hints(AgentState, include_extras=True)
        logs_hint = hints["logs"]
        metadata = getattr(logs_hint, "__metadata__", ())
        assert operator.add in metadata


# ---------------------------------------------------------------------------
# build_skills_agent — construction tests
# ---------------------------------------------------------------------------


class TestBuildSkillsAgent:
    def test_llms_instantiated_with_correct_params(self, mock_llms_cls, skills_dir):
        mock_cls, llm_instance, _ = mock_llms_cls
        with patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir):
            from backend.agent.agent_with_skills import build_skills_agent  # noqa: PLC0415

            with patch("backend.agent.agent_with_skills.StateGraph") as mock_sg:
                graph_instance = MagicMock()
                mock_sg.return_value = graph_instance
                build_skills_agent("anthropic-fast", temperature=0.5)

        mock_cls.assert_called_with(temperature=0.5, streaming=True)
        llm_instance.get_model.assert_called_with("anthropic-fast")

    def test_default_model_name(self, mock_llms_cls, skills_dir):
        mock_cls, llm_instance, _ = mock_llms_cls
        with patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir):
            from backend.agent.agent_with_skills import build_skills_agent  # noqa: PLC0415

            with patch("backend.agent.agent_with_skills.StateGraph") as mock_sg:
                graph_instance = MagicMock()
                mock_sg.return_value = graph_instance
                build_skills_agent()

        llm_instance.get_model.assert_called_with("anthropic-fast")

    def test_skill_docs_loaded_excluding_index(self, mock_llms_cls, skills_dir):
        """index.md must be skipped; skill_a.md and skill_b.md must appear in system prompt."""
        _, _, tagged = mock_llms_cls

        # We capture what the agent closure was built with by inspecting invoke calls later
        response = MagicMock()
        response.content = '{"action": "done", "answer": "ok"}'
        tagged.invoke.return_value = response

        with patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir):
            from backend.agent.agent_with_skills import build_skills_agent  # noqa: PLC0415

            captured_nodes = {}
            with patch("backend.agent.agent_with_skills.StateGraph") as mock_sg:
                graph_instance = MagicMock()
                graph_instance.add_node = MagicMock(
                    side_effect=lambda name, fn: captured_nodes.update({name: fn})
                )
                mock_sg.return_value = graph_instance
                build_skills_agent()

        agent_fn = captured_nodes.get("agent")
        assert agent_fn is not None, "agent node must be registered"

        state = {
            "question": "hello",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        agent_fn(state)

        call_args = tagged.invoke.call_args
        system_msg = call_args[0][0][0]  # first positional arg, first message
        assert "Skill A" in system_msg.content
        assert "Skill B" in system_msg.content
        assert "Index" not in system_msg.content

    def test_skills_dir_empty_no_crash(self, mock_llms_cls, tmp_path):
        """Agent should build fine if no skill files exist."""
        empty_skills = tmp_path / "empty_skills"
        empty_skills.mkdir()
        _, _, tagged = mock_llms_cls
        response = MagicMock()
        response.content = '{"action": "done", "answer": "empty"}'
        tagged.invoke.return_value = response

        with patch("backend.agent.agent_with_skills._SKILLS_DIR", empty_skills):
            from backend.agent.agent_with_skills import build_skills_agent  # noqa: PLC0415

            with patch("backend.agent.agent_with_skills.StateGraph") as mock_sg:
                graph_instance = MagicMock()
                mock_sg.return_value = graph_instance
                build_skills_agent()  # must not raise


# ---------------------------------------------------------------------------
# agent() node tests
# ---------------------------------------------------------------------------


class TestAgentNode:
    """Tests for the inner `agent` closure returned by build_skills_agent."""

    def _make_response(self, content: str):
        r = MagicMock()
        r.content = content
        return r

    def _build_agent_fn(self, tagged, skills_dir):
        captured_nodes = {}
        with patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir):
            from backend.agent.agent_with_skills import build_skills_agent  # noqa: PLC0415

            with patch("backend.agent.agent_with_skills.StateGraph") as mock_sg:
                graph_instance = MagicMock()
                graph_instance.add_node = MagicMock(
                    side_effect=lambda name, fn: captured_nodes.update({name: fn})
                )
                mock_sg.return_value = graph_instance
                build_skills_agent()
        return captured_nodes["agent"]

    def _base_state(self, **overrides):
        state = {
            "question": "Tell me about customer CUST00000001",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        state.update(overrides)
        return state

    # ---- happy path: tool_call action ----

    def test_tool_call_action_returns_pending_call(self, mock_llms_cls, skills_dir):
        _, _, tagged = mock_llms_cls
        payload = {"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}}
        tagged.invoke.return_value = self._make_response(json.dumps(payload))

        agent_fn = self._build_agent_fn(tagged, skills_dir)
        result = agent_fn(self._base_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["final_answer"] == "" or "final_answer" not in result

    def test_tool_call_action_adds_to_history(self, mock_llms_cls, skills_dir):
        _, _, tagged = mock_llms_cls
        payload = {"action": "tool_call", "tool_name": "customer_lookalike", "tool_args": {"customer_id": "CUST00000001"}}
        tagged.invoke.return_value = self._make_response(json.dumps(payload))

        agent_fn = self._build_agent_fn(tagged, skills_dir)
        result = agent_fn(self._base_state())

        assert len(result["history"]) == 1
        assert result["history"][0].startswith("Assistant:")

    def test_tool_call_action_adds_log_entry(self, mock_llms_cls, skills_dir):
        _, _, tagged = mock_llms_cls
        payload = {"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {}}
        tagged.invoke.return_value = self._make_response(json.dumps(payload))

        agent_fn = self._build_agent_fn(tagged, skills_dir)
        result = agent_fn(self._base_state())

        assert len(result["logs"]) == 1
        assert result["logs"][0]["event"] == "on_chat_model_end"
        assert result["logs"][0]["name"] == "agent"

    # ---- normalisation: "type": "function_call" → "action": "tool_call" ----

    def test_function_call_type_normalised_to_tool_call(self, mock_llms_cls, skills_dir):
        _, _, tagged = mock_llms_cls
        payload = {"type": "function_call", "tool_name": "get_customer_info", "tool_args": {}}
        tagged.invoke.return_value = self._make_response(json.dumps(payload))

        agent_fn = self._build_agent_fn(tagged, skills_dir)
        result = agent_fn(self._base_state())

        assert result["pending_call"]["action"] == "tool_call"

    # ---- happy path: done action ----

    def test_done_action_sets_final_answer(self, mock_llms_cls, skills_dir):
        _, _, tagged = mock_llms_cls
        payload = {"action": "done", "answer": "Here is the risk summary."}
        tagged.invoke.return_value = self._make_response(json.dumps(payload))

        agent_fn = self._build_agent_fn(tagged, skills_dir)
        result = agent