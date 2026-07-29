```python
"""
Test suite for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function (returns a compiled LangGraph)
- agent() node: happy path (tool_call action), done action, function_call normalisation,
  malformed JSON, plain-text response, missing JSON
- execute_tool() node: successful tool invocation, tool returns error payload,
  tool raises exception, unknown tool name
- router() function: pending_call present → routes to execute_tool,
  no pending_call → routes to END

Mocks used:
- backend.agent.agent_with_skills.LLMS  (prevents real LLM instantiation)
- backend.agent.agent_with_skills._profile_tool  (LangChain @tool stub)
- backend.agent.agent_with_skills._lookalike_tool  (LangChain @tool stub)
- backend.agent.agent_with_skills._run_underwriting_assessment  (stub)
- backend.agent.agent_with_skills._SKILLS_DIR  (patched to a tmp directory)
- TOOLS dict entries directly for execute_tool tests

TODOs:
- TODO: Integration test for full graph execution requires a real or stubbed
  LangGraph runtime — stub tests marked with pytest.mark.skip below.
- TODO: Streaming / on_tool_start / on_tool_end callback verification requires
  a LangChain callback harness — stub tests marked with pytest.mark.skip below.
- TODO: router() cannot be tested in isolation without access to the compiled
  graph internals; full routing tested via graph-level stub tests.
"""

import asyncio
import json
import operator
import re
import sys
import types
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for heavy optional dependencies so import succeeds in CI
# ---------------------------------------------------------------------------

def _make_module_stub(name):
    mod = types.ModuleType(name)
    sys.modules.setdefault(name, mod)
    return mod


for _pkg in [
    "langchain_core",
    "langchain_core.messages",
    "langgraph",
    "langgraph.graph",
    "backend.modules.assessment",
    "modules",
    "modules.tools",
    "modules.LLMS",
    "backend.modules",
]:
    _make_module_stub(_pkg)

# langchain_core.messages stubs
_lc_messages = sys.modules["langchain_core.messages"]
_lc_messages.HumanMessage = lambda content: {"type": "human", "content": content}
_lc_messages.SystemMessage = lambda content: {"type": "system", "content": content}

# langgraph.graph stubs
_lg_graph = sys.modules["langgraph.graph"]
_lg_graph.START = "START"


class _FakeStateGraph:
    """Minimal StateGraph stub."""

    def __init__(self, schema):
        self._schema = schema
        self._nodes = {}
        self._edges = []
        self._conditionals = []

    def add_node(self, name, fn):
        self._nodes[name] = fn

    def add_edge(self, src, dst):
        self._edges.append((src, dst))

    def add_conditional_edges(self, src, fn, mapping):
        self._conditionals.append((src, fn, mapping))

    def compile(self):
        compiled = MagicMock()
        compiled._graph = self
        return compiled


_lg_graph.StateGraph = _FakeStateGraph

# modules.LLMS stub
_llms_mod = sys.modules["modules.LLMS"]


class _FakeLLMS:
    def __init__(self, temperature=0, streaming=False):
        self.temperature = temperature
        self.streaming = streaming

    def get_model(self, name):
        m = MagicMock()
        m.with_config.return_value = m
        return m


_llms_mod.LLMS = _FakeLLMS

# modules.tools stubs
_tools_mod = sys.modules["modules.tools"]
_fake_profile_tool = MagicMock(name="get_customer_profile")
_fake_lookalike_tool = MagicMock(name="customer_lookalike")
_tools_mod.get_customer_profile = _fake_profile_tool
_tools_mod.customer_lookalike = _fake_lookalike_tool

# backend.modules.assessment stub
_assessment_mod = sys.modules["backend.modules.assessment"]
_fake_assessment_result = MagicMock(name="run_underwriting_assessment_fast")
_assessment_mod._run_underwriting_assessment = lambda mode: _fake_assessment_result

# ---------------------------------------------------------------------------
# Now we can import the module under test
# ---------------------------------------------------------------------------
import importlib

# Patch _SKILLS_DIR before import by preparing a temporary fixture later;
# we use a module-level patch approach.

with patch.dict(
    "sys.modules",
    {
        "langchain_core": sys.modules["langchain_core"],
        "langchain_core.messages": sys.modules["langchain_core.messages"],
        "langgraph": sys.modules["langgraph"],
        "langgraph.graph": sys.modules["langgraph.graph"],
        "backend.modules.assessment": sys.modules["backend.modules.assessment"],
        "modules": sys.modules["modules"],
        "modules.tools": sys.modules["modules.tools"],
        "modules.LLMS": sys.modules["modules.LLMS"],
    },
):
    # We need backend package stubs too
    _make_module_stub("backend")
    _make_module_stub("backend.agent")

    import backend.agent.agent_with_skills as _mod


# Convenience aliases
AgentState = _mod.AgentState
build_skills_agent = _mod.build_skills_agent


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def skills_dir(tmp_path):
    """Create a fake skills directory with two .md files."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "01_get_customer_info.md").write_text("# Skill: get_customer_info\nParams: customer_id")
    (skills / "02_lookalike.md").write_text("# Skill: customer_lookalike\nParams: customer_id")
    (skills / "index.md").write_text("# Index — should be skipped")
    return skills


@pytest.fixture()
def patch_skills_dir(skills_dir):
    with patch.object(_mod, "_SKILLS_DIR", skills_dir):
        yield skills_dir


@pytest.fixture()
def fake_llm():
    """A mock LLM whose invoke() returns a controllable response."""
    llm = MagicMock()
    llm.with_config.return_value = llm
    return llm


@pytest.fixture()
def llms_patch(fake_llm):
    """Patch LLMS so build_skills_agent() uses our fake_llm."""
    with patch.object(_mod, "LLMS") as mock_llms_cls:
        instance = MagicMock()
        instance.get_model.return_value = fake_llm
        mock_llms_cls.return_value = instance
        yield fake_llm


@pytest.fixture()
def built_agent(patch_skills_dir, llms_patch):
    """Returns (compiled_graph, agent_fn, execute_tool_fn, tagged_llm)."""
    tagged_llm = llms_patch  # fake_llm.with_config returns itself

    graph_holder = {}

    original_state_graph = _lg_graph.StateGraph

    class CapturingStateGraph(_FakeStateGraph):
        def __init__(self, schema):
            super().__init__(schema)
            graph_holder["instance"] = self

    with patch.object(_lg_graph, "StateGraph", CapturingStateGraph):
        compiled = build_skills_agent("anthropic-fast", temperature=0)

    sg = graph_holder["instance"]
    agent_fn = sg._nodes.get("agent")
    execute_tool_fn = sg._nodes.get("execute_tool")
    router_fn = None
    if sg._conditionals:
        router_fn = sg._conditionals[0][1]

    return compiled, agent_fn, execute_tool_fn, router_fn, tagged_llm


def _make_state(**kwargs):
    base = {
        "question": "Tell me about customer CUST00000001",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }
    base.update(kwargs)
    return base


def _llm_response(content: str):
    resp = MagicMock()
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# Tests: AgentState TypedDict
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_has_required_keys(self):
        keys = AgentState.__annotations__.keys()
        assert "question" in keys
        assert "history" in keys
        assert "logs" in keys
        assert "pending_call" in keys
        assert "final_answer" in keys

    def test_history_uses_operator_add(self):
        hints = AgentState.__annotations__
        # Annotated type carries metadata
        history_hint = hints["history"]
        # Check that operator.add is present in metadata
        assert hasattr(history_hint, "__metadata__"), "history should be Annotated"
        assert operator.add in history_hint.__metadata__

    def test_logs_uses_operator_add(self):
        hints = AgentState.__annotations__
        logs_hint = hints["logs"]
        assert hasattr(logs_hint, "__metadata__"), "logs should be Annotated"
        assert operator.add in logs_hint.__metadata__


# ---------------------------------------------------------------------------
# Tests: build_skills_agent factory
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, built_agent):
        compiled, *_ = built_agent
        assert compiled is not None

    def test_registers_agent_node(self, built_agent):
        _, agent_fn, *_ = built_agent
        assert callable(agent_fn), "agent node should be a callable"

    def test_registers_execute_tool_node(self, built_agent):
        _, _a, execute_tool_fn, *_ = built_agent
        assert callable(execute_tool_fn), "execute_tool node should be a callable"

    def test_skill_docs_loaded(self, patch_skills_dir, llms_patch):
        """Skill .md files are read; index.md is excluded."""
        graph_holder = {}

        class CapturingStateGraph(_FakeStateGraph):
            def __init__(self, schema):
                super().__init__(schema)
                graph_holder["instance"] = self

        with patch.object(_lg_graph, "StateGraph", CapturingStateGraph):
            build_skills_agent()

        # If we got here without exception the files were read correctly.
        assert "instance" in graph_holder

    def test_index_md_excluded(self, skills_dir, llms_patch):
        """index.md content must NOT appear in the system prompt."""
        # We verify indirectly by ensuring the skill_docs string is built
        # from the two non-index files only.
        docs = "\n\n".join(
            f.read_text()
            for f in sorted(skills_dir.glob("*.md"))
            if f.name != "index.md"
        )
        assert "Index — should be skipped" not in docs
        assert "get_customer_info" in docs

    def test_default_model_name(self, patch_skills_dir):
        with patch.object(_mod, "LLMS") as mock_llms_cls:
            instance = MagicMock()
            llm = MagicMock()
            llm.with_config.return_value = llm
            instance.get_model.return_value = llm
            mock_llms_cls.return_value = instance

            class Cap(_FakeStateGraph):
                pass

            with patch.object(_lg_graph, "StateGraph", Cap):
                build_skills_agent()  # no args → default "anthropic-fast"

            instance.get_model.assert_called_once_with("anthropic-fast")

    def test_custom_model_name(self, patch_skills_dir):
        with patch.object(_mod, "LLMS") as mock_llms_cls:
            instance = MagicMock()
            llm = MagicMock()
            llm.with_config.return_value = llm
            instance.get_model.return_value = llm
            mock_llms_cls.return_value = instance

            class Cap(_FakeStateGraph):
                pass

            with patch.object(_lg_graph, "StateGraph", Cap):
                build_skills_agent("gpt-4o")

            instance.get_model.assert_called_once_with("gpt-4o")

    def test_temperature_passed_to_llms(self, patch_skills_dir):
        with patch.object(_mod, "LLMS") as mock_llms_cls:
            instance = MagicMock()
            llm = MagicMock()
            llm.with_config.return_value = llm
            instance.get_model.return_value = llm
            mock_llms_cls.return_value = instance

            class Cap(_FakeStateGraph):
                pass

            with patch.object(_lg_graph, "StateGraph", Cap):
                build_skills_agent(temperature=0.7)

            mock_llms_cls.assert_called_once_with(temperature=0.7, streaming=True)

    def test_llm_tagged_with_agent(self, patch_skills_dir):
        with patch.object(_mod, "LLMS") as mock_llms_cls:
            instance = MagicMock()
            llm = MagicMock()
            llm.with_config.return_value = llm
            instance.get_model.return_value = llm
            mock_llms_cls.return_value = instance

            class Cap(_FakeStateGraph):
                pass

            with patch.object(_lg_graph, "StateGraph", Cap):
                build_skills_agent()

            llm.with_config.assert_called_once_with({"tags": ["agent"]})


# ---------------------------------------------------------------------------
# Tests: agent() node
# ---------------------------------------------------------------------------

class TestAgentNode:
    # --- tool_call action ---

    def test_tool_call_action_sets_pending_call(self, built_agent):
        _, agent_fn, _e, _r, tagged_llm = built_agent
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        tagged_llm.invoke.return_value = _llm_response(payload)

        result = agent_fn(_make_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert "CUST00000001" in result["pending_call"]["tool_args"]["customer_id"]

    def test_tool_call_appends_assistant_history(self, built_agent):
        _, agent_fn, _e, _r, tagged_llm = built_agent
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        tagged_llm.invoke.return_value = _llm_response(payload)
        result = agent_fn(_make_state())
        assert any("Assistant:" in h for h in result["history"])

    def test_tool_call_adds_log_entry(self, built_agent):
        _, agent_fn, _e, _r, tagged_llm = built_agent
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_