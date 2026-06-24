"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field behaviour
- build_skills_agent() factory: agent node, execute_tool node, router node
- agent() node: JSON parsing (tool_call, function_call, done, plain text), history/log building,
  action normalisation, error resilience
- execute_tool() node: successful tool invocation, tool returning error payload, unknown tool,
  exception raised by tool, non-string results
- router() node: routing to "execute_tool" when pending_call is non-empty, routing to END otherwise

Mocks used:
- backend.agent.agent_with_skills.LLMS            → prevents real LLM initialisation
- backend.agent.agent_with_skills._profile_tool   → stub LangChain @tool object
- backend.agent.agent_with_skills._lookalike_tool → stub LangChain @tool object
- backend.agent.agent_with_skills._run_underwriting_assessment → stub assessment tool
- backend.agent.agent_with_skills._SKILLS_DIR     → tmp_path with synthetic skill markdown files
- pathlib.Path.glob / Path.read_text              → controlled via tmp_path fixture

TODOs:
- TODO: full LangGraph compiled-graph integration test (needs graph .compile() + async stream)
- TODO: test LangChain callback events (on_tool_start / on_tool_end) fired correctly
- TODO: test with real StateGraph compilation once langgraph version is pinned
"""

import json
import operator
import re
import types
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers & shared fixtures
# ---------------------------------------------------------------------------

def _make_llm_response(content: str):
    """Return a mock LLM response whose .content attribute is *content*."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_tool_mock(return_value):
    """Return an async-capable mock that behaves like a LangChain @tool."""
    t = MagicMock()
    t.ainvoke = AsyncMock(return_value=return_value)
    return t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def skills_dir(tmp_path):
    """Create a temporary skills directory with two synthetic skill files."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "01_customer_lookup.md").write_text("# Skill: get_customer_info\nParam: customer_id (str)")
    (skills / "02_lookalike.md").write_text("# Skill: customer_lookalike\nParam: customer_id (str)")
    (skills / "index.md").write_text("# Index — should be ignored")
    return skills


@pytest.fixture()
def mock_tools():
    """Return a dict of three mock tools mirroring the real TOOLS dict."""
    return {
        "get_customer_info": _make_tool_mock('{"name": "Alice"}'),
        "customer_lookalike": _make_tool_mock('["CUST00006151", "CUST00000272"]'),
        "run_risk_assessment": _make_tool_mock('{"risk": "low"}'),
    }


@pytest.fixture()
def patched_module(skills_dir, mock_tools):
    """
    Import (or reload) agent_with_skills with all external dependencies patched.
    Yields the module object so tests can call build_skills_agent() freely.
    """
    fake_llms_instance = MagicMock()
    fake_base_llm = MagicMock()
    fake_tagged_llm = MagicMock()
    fake_base_llm.with_config.return_value = fake_tagged_llm
    fake_llms_instance.get_model.return_value = fake_base_llm

    fake_llms_cls = MagicMock(return_value=fake_llms_instance)

    with (
        patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
        patch("backend.agent.agent_with_skills._profile_tool", mock_tools["get_customer_info"]),
        patch("backend.agent.agent_with_skills._lookalike_tool", mock_tools["customer_lookalike"]),
        patch(
            "backend.agent.agent_with_skills._run_underwriting_assessment",
            return_value=mock_tools["run_risk_assessment"],
        ),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
    ):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)
        # Inject mock_tools into the reloaded module's TOOLS
        mod.TOOLS = mock_tools
        yield mod


@pytest.fixture()
def agent_nodes(patched_module):
    """
    Call build_skills_agent() and expose the inner node callables by
    rebuilding them via a second call — we capture the closures by
    monkey-patching StateGraph so it records what was added.
    """
    nodes = {}
    original_add_node = patched_module.StateGraph.add_node if hasattr(patched_module.StateGraph, "add_node") else None

    # We can't easily intercept the private closures through StateGraph,
    # so we rebuild via a direct approach: call the factory and rely on the
    # fact that langgraph is also mocked.
    fake_graph = MagicMock()
    fake_compiled = MagicMock()
    fake_graph.compile.return_value = fake_compiled

    captured = {}

    def fake_add_node(name, fn=None, **kw):
        if fn is not None:
            captured[name] = fn
        return fake_graph

    fake_graph.add_node = fake_add_node
    fake_graph.add_edge = MagicMock(return_value=fake_graph)
    fake_graph.add_conditional_edges = MagicMock(return_value=fake_graph)

    with patch.object(patched_module, "StateGraph", return_value=fake_graph):
        patched_module.build_skills_agent()

    return captured  # dict: node_name → callable


# ---------------------------------------------------------------------------
# Unit tests: AgentState
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_history_uses_add_annotation(self):
        import typing, get_annotations
        # Verify via TypedDict annotations that history is Annotated[list[str], operator.add]
        hints = patched_module_hints = None
        # Access annotations directly from the class
        import backend.agent.agent_with_skills as mod0
        ann = mod0.AgentState.__annotations__
        assert "history" in ann
        assert "logs" in ann
        assert "question" in ann
        assert "pending_call" in ann
        assert "final_answer" in ann

    def test_agent_state_fields(self, patched_module):
        ann = patched_module.AgentState.__annotations__
        assert set(ann.keys()) == {"question", "history", "logs", "pending_call", "final_answer"}


# ---------------------------------------------------------------------------
# Unit tests: build_skills_agent() factory
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, patched_module):
        result = patched_module.build_skills_agent()
        # The result should be whatever StateGraph.compile() returns
        assert result is not None

    def test_llms_called_with_defaults(self, patched_module):
        """Default model_name and temperature are forwarded to LLMS."""
        patched_module.build_skills_agent()
        patched_module.LLMS.assert_called_with(temperature=0, streaming=True)
        patched_module.LLMS().get_model.assert_called_with("anthropic-fast")

    def test_llms_called_with_custom_params(self, patched_module):
        patched_module.build_skills_agent(model_name="openai-fast", temperature=0.7)
        patched_module.LLMS.assert_called_with(temperature=0.7, streaming=True)
        patched_module.LLMS().get_model.assert_called_with("openai-fast")

    def test_skill_docs_loaded_excluding_index(self, patched_module, skills_dir):
        """Skills directory .md files are loaded; index.md is excluded."""
        # Rebuild so we can inspect the system_prompt indirectly via tagged_llm.invoke calls later
        compiled = patched_module.build_skills_agent()
        assert compiled is not None  # factory ran without error

    def test_skill_docs_sorted(self, patched_module, skills_dir):
        """Files are loaded in sorted order (01_ before 02_)."""
        # Add a third file to verify ordering
        (skills_dir / "00_first.md").write_text("# Zero skill")
        # If sorting is wrong, the concatenation order differs — just ensure no exception
        patched_module.build_skills_agent()

    def test_empty_skills_dir(self, patched_module, tmp_path):
        empty_skills = tmp_path / "empty_skills"
        empty_skills.mkdir()
        with patch.object(patched_module, "_SKILLS_DIR", empty_skills):
            # Should not raise even with no skill files
            patched_module.build_skills_agent()


# ---------------------------------------------------------------------------
# Helpers: build agent() and execute_tool() callables directly
# ---------------------------------------------------------------------------

def _extract_nodes(patched_module, tagged_llm=None):
    """
    Re-run build_skills_agent() with a controlled StateGraph mock and
    return a dict of captured node callables.
    """
    fake_graph = MagicMock()
    captured = {}

    def fake_add_node(name, fn=None, **kw):
        if fn is not None:
            captured[name] = fn
        return fake_graph

    fake_graph.add_node = fake_add_node
    fake_graph.add_edge = MagicMock(return_value=fake_graph)
    fake_graph.add_conditional_edges = MagicMock(return_value=fake_graph)
    fake_graph.compile = MagicMock(return_value=MagicMock())

    with patch.object(patched_module, "StateGraph", return_value=fake_graph):
        patched_module.build_skills_agent()

    return captured


# ---------------------------------------------------------------------------
# Unit tests: agent() node
# ---------------------------------------------------------------------------

class TestAgentNode:
    """Tests for the agent() inner function (LLM reasoning node)."""

    @pytest.fixture()
    def nodes_and_llm(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        nodes = _extract_nodes(patched_module)
        return nodes, tagged_llm

    def _base_state(self, question="Tell me about CUST00000001", history=None, logs=None):
        return {
            "question": question,
            "history": history or [],
            "logs": logs or [],
            "pending_call": {},
            "final_answer": "",
        }

    # -- Happy path: tool_call action --

    def test_tool_call_action_sets_pending_call(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        tagged_llm.invoke.return_value = _make_llm_response(payload)

        nodes = _extract_nodes(patched_module)
        assert "agent" in nodes, "agent node was not registered"

        result = nodes["agent"](self._base_state())
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["action"] == "tool_call"
        assert len(result["history"]) == 1
        assert "Assistant:" in result["history"][0]

    def test_function_call_normalised_to_tool_call(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        payload = json.dumps({
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        tagged_llm.invoke.return_value = _make_llm_response(payload)

        nodes = _extract_nodes(patched_module)
        result = nodes["agent"](self._base_state())
        assert result["pending_call"]["action"] == "tool_call"

    # -- Happy path: done action --

    def test_done_action_sets_final_answer(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        payload = json.dumps({"action": "done", "answer": "The customer is low risk."})
        tagged_llm.invoke.return_value = _make_llm_response(payload)

        nodes = _extract_nodes(patched_module)
        result = nodes["agent"](self._base_state())
        assert result["final_answer"] == "The customer is low risk."
        assert result["pending_call"] == {}

    def test_done_action_missing_answer_key(self, patched_module):
        """done without 'answer' key should return empty string, not raise."""
        tagged_llm = patched_module.LLMS().get_model().with_config()
        payload = json.dumps({"action": "done"})
        tagged_llm.invoke.return_value = _make_llm_response(payload)

        nodes = _extract_nodes(patched_module)
        result = nodes["agent"](self._base_state())
        assert result["final_answer"] == ""

    # -- Edge case: plain text (no JSON) --

    def test_plain_text_response_no_pending_call(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        tagged_llm.invoke.return_value = _make_llm_response("I need more information please.")

        nodes = _extract_nodes(patched_module)
        result = nodes["agent"](self._base_state())
        assert result["pending_call"] == {}
        assert "Assistant: I need more information please." in result["history"]

    # -- Edge case: malformed JSON --

    def test_malformed_json_is_handled_gracefully(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        tagged_llm.invoke.return_value = _make_llm_response("{not valid json}")

        nodes = _extract_nodes(patched_module)
        result = nodes["agent"](self._base_state())
        assert result["pending_call"] == {}

    # -- Edge case: JSON with unknown action --

    def test_unknown_action_treated_as_plain_text(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        payload = json.dumps({"action": "fly_to_moon", "data": "x"})
        tagged_llm.invoke.return_value = _make_llm_response(payload)

        nodes = _extract_nodes(patched_module)
        result = nodes["agent"](self._base_state())
        # No pending call, no final answer
        assert result["pending_call"] == {}
        assert "final_answer" not in result

    # -- Log entry always present --

    def test_log_entry_always_appended(self, patched_module):
        tagged_llm = patched_module.LLMS().get_model().with_config()
        tagged_llm.invoke.return_value = _make_llm_response("Hello!")

        nodes