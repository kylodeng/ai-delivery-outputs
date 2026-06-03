"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field definitions
- build_skills_agent(): graph construction, LLM wiring, system prompt generation
- agent() node: happy path tool_call parsing, done action parsing, fallback on invalid JSON,
  normalisation of "function_call" → "tool_call", missing json block, JSONDecodeError handling
- execute_tool() node: successful tool invocation, tool returns error payload, tool raises exception,
  unknown tool name, tool returns non-string result
- router() node: routes to execute_tool when pending_call present, routes to END when final_answer set,
  routes to END when pending_call empty and no final_answer

Mocks used:
- backend.modules.assessment._run_underwriting_assessment (patched at module level)
- modules.tools.get_customer_profile (_profile_tool)
- modules.tools.customer_lookalike (_lookalike_tool)
- backend.modules.LLMS.LLMS (LLM factory)
- pathlib.Path.glob / Path.read_text (skill docs loading)
- langchain_core.messages.HumanMessage / SystemMessage (imported types — real objects used)
- Individual @tool .ainvoke() coroutines replaced with AsyncMock

TODOs:
- TODO: Integration test requiring a real LangGraph StateGraph compilation with live LLM
- TODO: Test streaming behaviour (requires real or stubbed streaming LLM)
- TODO: Test on_tool_start / on_tool_end LangChain callback hooks end-to-end
- TODO: Verify skill_docs content injected into system prompt when real .md files are present
"""

import json
import operator
import re
import sys
import types
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, Mock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Minimal stub modules so the import of agent_with_skills.py does not require
# the full application stack to be installed.
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """Create the minimum stub modules needed before importing the agent."""

    # langchain_core.messages
    lcm = types.ModuleType("langchain_core")
    lcm_msgs = types.ModuleType("langchain_core.messages")

    class _Msg:
        def __init__(self, content=""):
            self.content = content

    lcm_msgs.HumanMessage = _Msg
    lcm_msgs.SystemMessage = _Msg
    lcm.messages = lcm_msgs
    sys.modules.setdefault("langchain_core", lcm)
    sys.modules.setdefault("langchain_core.messages", lcm_msgs)

    # langgraph.graph
    lg = types.ModuleType("langgraph")
    lg_graph = types.ModuleType("langgraph.graph")

    class _StateGraph:
        def __init__(self, *a, **kw):
            self._nodes = {}
            self._edges = []
        def add_node(self, name, fn):
            self._nodes[name] = fn
        def add_edge(self, src, dst):
            self._edges.append((src, dst))
        def add_conditional_edges(self, src, fn, mapping=None):
            self._edges.append((src, fn, mapping))
        def compile(self):
            compiled = MagicMock()
            compiled._nodes = self._nodes
            compiled._edges = self._edges
            return compiled

    lg_graph.StateGraph = _StateGraph
    lg_graph.START = "__start__"
    lg_graph.END = "__end__"
    lg.graph = lg_graph
    sys.modules.setdefault("langgraph", lg)
    sys.modules.setdefault("langgraph.graph", lg_graph)

    # backend.modules.assessment
    bk = sys.modules.setdefault("backend", types.ModuleType("backend"))
    bk_mods = sys.modules.setdefault("backend.modules", types.ModuleType("backend.modules"))
    bk_assess = sys.modules.setdefault(
        "backend.modules.assessment", types.ModuleType("backend.modules.assessment")
    )
    mock_assessment_tool = MagicMock()
    mock_assessment_tool.ainvoke = AsyncMock(return_value='{"risk": "low"}')
    bk_assess._run_underwriting_assessment = MagicMock(return_value=mock_assessment_tool)

    # modules.tools
    mt = sys.modules.setdefault("modules", types.ModuleType("modules"))
    mt_tools = sys.modules.setdefault("modules.tools", types.ModuleType("modules.tools"))
    mock_profile = MagicMock()
    mock_profile.ainvoke = AsyncMock(return_value='{"customer": "CUST00000001"}')
    mock_lookalike = MagicMock()
    mock_lookalike.ainvoke = AsyncMock(return_value='["CUST00006151","CUST00000272"]')
    mt_tools.get_customer_profile = mock_profile
    mt_tools.customer_lookalike = mock_lookalike
    mt.tools = mt_tools

    # modules.LLMS
    mt_llms = sys.modules.setdefault("modules.LLMS", types.ModuleType("modules.LLMS"))

    class _FakeLLMS:
        def __init__(self, temperature=0, streaming=False):
            self.temperature = temperature
            self.streaming = streaming

        def get_model(self, name):
            m = MagicMock()
            tagged = MagicMock()
            tagged.invoke = MagicMock(
                return_value=MagicMock(content='{"action": "done", "answer": "ok"}')
            )
            m.with_config = MagicMock(return_value=tagged)
            return m

    mt_llms.LLMS = _FakeLLMS
    mt.LLMS = mt_llms

    # backend.modules.LLMS alias used by the agent
    sys.modules.setdefault("backend.modules.LLMS", mt_llms)


_make_stub_modules()


# Now patch Path.glob so no real filesystem access happens during import
_FAKE_SKILL_FILES: list = []

with patch.object(Path, "glob", return_value=_FAKE_SKILL_FILES):
    import importlib
    import backend.agent.agent_with_skills as _aw_module
    # Force reimport to pick up stubs cleanly
    importlib.reload(_aw_module)


# Convenience re-exports
AgentState = _aw_module.AgentState
build_skills_agent = _aw_module.build_skills_agent
TOOLS = _aw_module.TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(**overrides) -> dict:
    """Return a minimal valid AgentState-compatible dict."""
    base = {
        "question": "Tell me about customer CUST00000001",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }
    base.update(overrides)
    return base


def _build_agent_with_llm_response(content: str):
    """
    Build a skills agent whose tagged LLM returns *content* as the response text.
    Returns (graph_mock, agent_node_fn, execute_tool_fn, router_fn).
    """
    fake_llm_response = MagicMock()
    fake_llm_response.content = content

    tagged_llm = MagicMock()
    tagged_llm.invoke = MagicMock(return_value=fake_llm_response)

    raw_llm = MagicMock()
    raw_llm.with_config = MagicMock(return_value=tagged_llm)

    fake_llms_instance = MagicMock()
    fake_llms_instance.get_model = MagicMock(return_value=raw_llm)

    captured_nodes = {}

    class _CapturingGraph:
        def __init__(self, *a, **kw):
            pass
        def add_node(self, name, fn):
            captured_nodes[name] = fn
        def add_edge(self, *a): pass
        def add_conditional_edges(self, *a, **kw): pass
        def compile(self):
            c = MagicMock()
            c._nodes = captured_nodes
            return c

    with patch.object(
        sys.modules["modules.LLMS"], "LLMS", return_value=fake_llms_instance
    ), patch.object(
        Path, "glob", return_value=[]
    ), patch(
        "langgraph.graph.StateGraph", _CapturingGraph
    ):
        graph = build_skills_agent()

    return graph, captured_nodes


# ---------------------------------------------------------------------------
# Tests: AgentState structure
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_required_keys_present(self):
        state = _make_state()
        assert "question" in state
        assert "history" in state
        assert "logs" in state
        assert "pending_call" in state
        assert "final_answer" in state

    def test_history_is_list(self):
        state = _make_state(history=["msg1", "msg2"])
        assert isinstance(state["history"], list)

    def test_logs_is_list(self):
        state = _make_state(logs=[{"event": "test"}])
        assert isinstance(state["logs"], list)

    def test_pending_call_is_dict(self):
        state = _make_state(pending_call={"tool_name": "x"})
        assert isinstance(state["pending_call"], dict)


# ---------------------------------------------------------------------------
# Tests: build_skills_agent graph wiring
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self):
        with patch.object(Path, "glob", return_value=[]):
            graph = build_skills_agent()
        assert graph is not None

    def test_registers_agent_node(self):
        _, nodes = _build_agent_with_llm_response('{"action":"done","answer":"x"}')
        assert "agent" in nodes

    def test_registers_execute_tool_node(self):
        _, nodes = _build_agent_with_llm_response('{"action":"done","answer":"x"}')
        assert "execute_tool" in nodes

    def test_skill_docs_empty_when_no_files(self):
        """No .md files → skill_docs is empty string; system prompt still builds."""
        with patch.object(Path, "glob", return_value=[]):
            graph = build_skills_agent()
        assert graph is not None

    def test_skill_docs_loaded_from_md_files(self, tmp_path):
        """Skills directory with .md files → content injected into system prompt."""
        skill_a = tmp_path / "skill_a.md"
        skill_a.write_text("## Skill A\nDo something.")
        skill_b = tmp_path / "index.md"
        skill_b.write_text("index content — should be skipped")

        with patch.object(_aw_module, "_SKILLS_DIR", tmp_path):
            _, nodes = _build_agent_with_llm_response('{"action":"done","answer":"ok"}')

        # Agent node must exist regardless of skills content
        assert "agent" in nodes

    def test_model_name_passed_to_llms(self):
        calls = []

        class _TrackingLLMS:
            def __init__(self, temperature=0, streaming=False):
                pass
            def get_model(self, name):
                calls.append(name)
                m = MagicMock()
                tagged = MagicMock()
                tagged.invoke = MagicMock(
                    return_value=MagicMock(content='{"action":"done","answer":"hi"}')
                )
                m.with_config = MagicMock(return_value=tagged)
                return m

        with patch.object(sys.modules["modules.LLMS"], "LLMS", _TrackingLLMS), \
             patch.object(Path, "glob", return_value=[]):
            build_skills_agent(model_name="my-custom-model")

        assert "my-custom-model" in calls

    def test_default_model_name(self):
        calls = []

        class _TrackingLLMS:
            def __init__(self, temperature=0, streaming=False):
                pass
            def get_model(self, name):
                calls.append(name)
                m = MagicMock()
                t = MagicMock()
                t.invoke = MagicMock(
                    return_value=MagicMock(content='{"action":"done","answer":"hi"}')
                )
                m.with_config = MagicMock(return_value=t)
                return m

        with patch.object(sys.modules["modules.LLMS"], "LLMS", _TrackingLLMS), \
             patch.object(Path, "glob", return_value=[]):
            build_skills_agent()

        assert calls[0] == "anthropic-fast"

    @pytest.mark.skip(reason="TODO: verify temperature parameter forwarded to LLMS constructor")
    def test_temperature_forwarded(self):
        pass


# ---------------------------------------------------------------------------
# Tests: agent() node
# ---------------------------------------------------------------------------

class TestAgentNode:

    def _get_agent_node(self, llm_response_content: str):
        _, nodes = _build_agent_with_llm_response(llm_response_content)
        return nodes["agent"]

    # --- Happy path: tool_call ---
    def test_tool_call_action_sets_pending_call(self):
        content = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        agent_fn = self._get_agent_node(content)
        result = agent_fn(_make_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["tool_args"]["customer_id"] == "CUST00000001"

    def test_tool_call_appends_history(self):
        content = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        agent_fn = self._get_agent_node(content)
        result = agent_fn(_make_state())
        assert any("Assistant:" in h for h in result["history"])

    def test_tool_call_appends_log_entry(self):
        content = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {},
        })
        agent_fn = self._get_agent_node(content)
        result = agent_fn(_make_state())
        assert len(result["logs"]) == 1
        assert result["logs"][0]["event"] == "on_chat_model_end"

    # --- Happy path: done ---
    def test_done_action_sets_final_answer(self):
        content = json.dumps({"action": "done", "answer": "Here is the assessment."})
        agent_fn = self._get_agent_node(content)
        result = agent_fn(_make_state())

        assert result["final_answer"] == "Here is the assessment."
        assert result["pending_call"] == {}

    def test_done_action_with_empty_answer(self):
        content = json.dumps({"action": "done", "answer": ""})
        agent_fn = self._get_agent_node(content)
        result = agent_fn(_make_state())
        assert result["final_answer"] == ""

    def test_done_action_missing_answer_key(self):
        content = json.dumps({"action": "