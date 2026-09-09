"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field behaviour
- build_skills_agent() factory function and the inner node callables it produces:
  - agent() node: happy-path JSON parsing, tool_call action, done action,
    function_call alias normalisation, missing-JSON fallback, malformed JSON,
    history injection into prompt
  - execute_tool() node: successful tool invocation, tool returning error payload,
    tool raising an exception, unknown tool name
  - router() node: routing to execute_tool when pending_call present,
    routing to END when final_answer present, routing to agent when neither

Mocks used:
- unittest.mock.MagicMock / AsyncMock for LLMS, tagged_llm, tool functions
- patch for backend.agent.agent_with_skills.LLMS
- patch for backend.agent.agent_with_skills.TOOLS
- patch for backend.agent.agent_with_skills._SKILLS_DIR (Path glob)
- patch for backend.agent.agent_with_skills._profile_tool
- patch for backend.agent.agent_with_skills._lookalike_tool
- patch for backend.agent.agent_with_skills._run_underwriting_assessment

TODOs:
- TODO: Integration test for full graph execution (requires LangGraph test harness)
- TODO: Test streaming behaviour via on_tool_start/on_tool_end callbacks (requires callback context)
- TODO: Test actual skill .md file loading against real _SKILLS_DIR content
- TODO: Test build_skills_agent with different model_name / temperature combos against real LLMS registry
"""

import json
import operator
import re
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal AgentState dict
# ---------------------------------------------------------------------------

def make_state(
    question: str = "Tell me about customer CUST00000001",
    history: list[str] | None = None,
    logs: list[dict] | None = None,
    pending_call: dict | None = None,
    final_answer: str = "",
) -> dict:
    return {
        "question": question,
        "history": history or [],
        "logs": logs or [],
        "pending_call": pending_call or {},
        "final_answer": final_answer,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_skills_dir(tmp_path):
    """Create a temporary skills directory with two .md files."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "01_get_customer_info.md").write_text("# get_customer_info skill docs")
    (skills / "02_customer_lookalike.md").write_text("# customer_lookalike skill docs")
    (skills / "index.md").write_text("# index - should be skipped")
    return skills


@pytest.fixture()
def mock_llm():
    """Return a mock LLM chain (tagged_llm)."""
    llm = MagicMock()
    tagged = MagicMock()
    llm.with_config.return_value = tagged
    return llm, tagged


@pytest.fixture()
def mock_tools():
    """Return a dict of mock async tool functions."""
    tools = {}
    for name in ("get_customer_info", "customer_lookalike", "run_risk_assessment"):
        t = MagicMock()
        t.ainvoke = AsyncMock(return_value=f"result_from_{name}")
        tools[name] = t
    return tools


@pytest.fixture()
def agent_nodes(mock_skills_dir, mock_llm, mock_tools):
    """
    Patch all external dependencies, build the agent, and return the inner
    node callables (agent, execute_tool, router) extracted via introspection.
    """
    llm_instance, tagged_llm = mock_llm

    with (
        patch("backend.agent.agent_with_skills.LLMS") as MockLLMS,
        patch("backend.agent.agent_with_skills._SKILLS_DIR", mock_skills_dir),
        patch("backend.agent.agent_with_skills.TOOLS", mock_tools),
    ):
        MockLLMS.return_value.get_model.return_value = llm_instance

        # Import after patching to pick up mocks
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        # Re-apply patches on the reloaded module
        mod.LLMS = MockLLMS
        mod._SKILLS_DIR = mock_skills_dir
        mod.TOOLS = mock_tools

        # build_skills_agent uses closures; call it and capture the returned graph
        # We need to intercept the inner functions before they are wired into StateGraph.
        # Strategy: capture via side-effect on StateGraph.add_node
        captured = {}

        original_build = mod.build_skills_agent

        def capturing_build(model_name="anthropic-fast", temperature=0):
            # Temporarily monkey-patch StateGraph to capture nodes
            real_sg = mod.__dict__.get("StateGraph") or __import__(
                "langgraph.graph", fromlist=["StateGraph"]
            ).StateGraph

            class CapturingStateGraph(real_sg):
                def add_node(self_inner, name, fn):
                    captured[name] = fn
                    return super().add_node(name, fn)

            with patch("backend.agent.agent_with_skills.StateGraph", CapturingStateGraph):
                graph = original_build(model_name=model_name, temperature=temperature)
            return graph

        graph = capturing_build()
        yield captured, tagged_llm, mock_tools, graph


# ---------------------------------------------------------------------------
# Convenience: build agent nodes without the full fixture machinery
# ---------------------------------------------------------------------------

def _build_nodes(mock_skills_dir, mock_tools, llm_response_content: str):
    """
    Lower-level helper that returns (agent_fn, execute_tool_fn, router_fn)
    by patching LLMS, _SKILLS_DIR and TOOLS then calling build_skills_agent.
    """
    llm_instance = MagicMock()
    tagged_llm = MagicMock()
    llm_instance.with_config.return_value = tagged_llm

    response_mock = MagicMock()
    response_mock.content = llm_response_content
    tagged_llm.invoke.return_value = response_mock

    captured = {}

    with (
        patch("backend.agent.agent_with_skills.LLMS") as MockLLMS,
        patch("backend.agent.agent_with_skills._SKILLS_DIR", mock_skills_dir),
        patch("backend.agent.agent_with_skills.TOOLS", mock_tools),
    ):
        MockLLMS.return_value.get_model.return_value = llm_instance

        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)
        mod.LLMS = MockLLMS
        mod._SKILLS_DIR = mock_skills_dir
        mod.TOOLS = mock_tools

        from langgraph.graph import StateGraph as RealSG

        class CapturingSG(RealSG):
            def add_node(self_inner, name, fn):
                captured[name] = fn
                return super().add_node(name, fn)

        with patch("backend.agent.agent_with_skills.StateGraph", CapturingSG):
            mod.build_skills_agent()

    return captured, tagged_llm


# ===========================================================================
# Tests: AgentState structure
# ===========================================================================

class TestAgentState:
    def test_state_has_required_keys(self):
        state = make_state()
        assert "question" in state
        assert "history" in state
        assert "logs" in state
        assert "pending_call" in state
        assert "final_answer" in state

    def test_history_is_list(self):
        state = make_state(history=["msg1", "msg2"])
        assert isinstance(state["history"], list)
        assert len(state["history"]) == 2

    def test_logs_is_list_of_dicts(self):
        state = make_state(logs=[{"event": "test"}])
        assert isinstance(state["logs"], list)
        assert state["logs"][0]["event"] == "test"

    def test_pending_call_defaults_empty(self):
        state = make_state()
        assert state["pending_call"] == {}

    def test_final_answer_defaults_empty_string(self):
        state = make_state()
        assert state["final_answer"] == ""

    def test_operator_add_semantics_for_history(self):
        """Annotated[list[str], operator.add] means lists are concatenated."""
        a = ["msg1"]
        b = ["msg2"]
        assert operator.add(a, b) == ["msg1", "msg2"]


# ===========================================================================
# Tests: skill file loading in build_skills_agent
# ===========================================================================

class TestSkillLoading:
    def test_index_md_excluded(self, mock_skills_dir, mock_tools):
        """index.md should never appear in the system prompt."""
        captured, tagged_llm = _build_nodes(
            mock_skills_dir, mock_tools,
            '{"action": "done", "answer": "ok"}'
        )
        agent_fn = captured.get("agent")
        assert agent_fn is not None

        # invoke agent to trigger the llm call that embeds the system prompt
        response_mock = MagicMock()
        response_mock.content = '{"action": "done", "answer": "ok"}'
        tagged_llm.invoke.return_value = response_mock

        agent_fn(make_state())

        call_args = tagged_llm.invoke.call_args
        messages = call_args[0][0]
        system_content = messages[0].content
        assert "index - should be skipped" not in system_content

    def test_skill_docs_present_in_system_prompt(self, mock_skills_dir, mock_tools):
        """Skill file content should be embedded in the system prompt."""
        captured, tagged_llm = _build_nodes(
            mock_skills_dir, mock_tools,
            '{"action": "done", "answer": "ok"}'
        )
        agent_fn = captured["agent"]

        response_mock = MagicMock()
        response_mock.content = '{"action": "done", "answer": "ok"}'
        tagged_llm.invoke.return_value = response_mock

        agent_fn(make_state())

        messages = tagged_llm.invoke.call_args[0][0]
        system_content = messages[0].content
        assert "get_customer_info skill docs" in system_content
        assert "customer_lookalike skill docs" in system_content

    def test_empty_skills_dir(self, tmp_path, mock_tools):
        """Agent should build fine even with no skill files."""
        empty_skills = tmp_path / "skills"
        empty_skills.mkdir()
        captured, tagged_llm = _build_nodes(
            empty_skills, mock_tools,
            '{"action": "done", "answer": "ok"}'
        )
        assert "agent" in captured

    def test_skills_sorted_alphabetically(self, tmp_path, mock_tools):
        """Skill files are loaded via sorted(), so order is deterministic."""
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "b_skill.md").write_text("B content")
        (skills / "a_skill.md").write_text("A content")

        captured, tagged_llm = _build_nodes(
            skills, mock_tools,
            '{"action": "done", "answer": "ok"}'
        )
        agent_fn = captured["agent"]

        response_mock = MagicMock()
        response_mock.content = '{"action": "done", "answer": "ok"}'
        tagged_llm.invoke.return_value = response_mock

        agent_fn(make_state())
        messages = tagged_llm.invoke.call_args[0][0]
        system_content = messages[0].content
        a_pos = system_content.index("A content")
        b_pos = system_content.index("B content")
        assert a_pos < b_pos


# ===========================================================================
# Tests: agent() node
# ===========================================================================

class TestAgentNode:

    def _make_agent(self, mock_skills_dir, mock_tools, llm_content: str):
        captured, tagged_llm = _build_nodes(mock_skills_dir, mock_tools, llm_content)
        response_mock = MagicMock()
        response_mock.content = llm_content
        tagged_llm.invoke.return_value = response_mock
        return captured["agent"], tagged_llm

    # --- Happy path: tool_call ---

    def test_agent_returns_pending_call_on_tool_call(self, mock_skills_dir, mock_tools):
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        agent_fn, _ = self._make_agent(mock_skills_dir, mock_tools, payload)
        result = agent_fn(make_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["tool_args"] == {"customer_id": "CUST00000001"}

    def test_agent_appends_to_history_on_tool_call(self, mock_skills_dir, mock_tools):
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        agent_fn, _ = self._make_agent(mock_skills_dir, mock_tools, payload)
        result = agent_fn(make_state())

        assert len(result["history"]) == 1
        assert result["history"][0].startswith("Assistant:")

    def test_agent_adds_log_entry_on_tool_call(self, mock_skills_dir, mock_tools):
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {},
        })
        agent_fn, _ = self._make_agent(mock_skills_dir, mock_tools, payload)
        result = agent_fn(make_state())

        assert len(result["logs"]) == 1
        assert result["logs"][0]["event"] == "on_chat_model_end"
        assert result["logs"][0]["name"] == "agent"

    # --- function_call alias normalisation ---

    def test_agent_normalises_function_call_to_tool_call(self, mock_skills_dir, mock_tools):
        payload = json.dumps({
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00006151"},
        })
        agent_fn, _ = self._make_agent(mock_skills_dir, mock_tools, payload)
        result = agent_fn(make_state())

        assert result["pending_call"]["action"] == "tool_call"

    # --- Happy path: done ---

    def test_agent_returns_final_answer_on_done(self, mock_skills_dir, mock_tools):
        payload = json.dumps({
            "action": "done",
            "answer": "The customer risk is low.",
        })
        agent_fn, _ = self._make_agent(mock_skills_dir, mock_tools, payload)
        result = agent_fn(make_state())

        assert result["final_answer"] == "The customer risk is low."
        assert result["pending_call"] == {}

    def test_agent_done_with_empty_answer(self, mock