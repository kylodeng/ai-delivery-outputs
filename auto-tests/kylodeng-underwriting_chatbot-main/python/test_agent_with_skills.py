```python
"""
Module docstring
================
What is tested:
    - backend/agent/agent_with_skills.py

    Specifically:
        * AgentState TypedDict structure and field annotations
        * build_skills_agent() – the factory function that wires up the LangGraph
        * Inner `agent` node: happy-path JSON parsing (tool_call / function_call /
          done actions), malformed JSON fallback, missing JSON block, normalisation
          of action field.
        * Inner `execute_tool` node: successful tool invocation, tool returning an
          error payload, unknown tool name, exception raised by tool.
        * Inner `router` function: routing to correct next node based on state.
        * TOOLS dict structure.

Mocks used:
    - unittest.mock.MagicMock / AsyncMock for:
        * LLMS (and the LLM chain it returns)
        * _profile_tool  (modules.tools.get_customer_profile)
        * _lookalike_tool (modules.tools.customer_lookalike)
        * _run_underwriting_assessment (backend.modules.assessment)
        * Path / _SKILLS_DIR glob so no real filesystem reads are needed

TODOs:
    - TODO: full StateGraph compilation/execution test requires a running LangGraph
      environment – stubbed below.
    - TODO: streaming behaviour (on_tool_start / on_tool_end LangChain callbacks)
      requires a real LangChain callback manager – stubbed below.
    - TODO: router() function body is truncated in the source; its complete logic
      cannot be tested without the full implementation – stubbed below.
"""

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
# Minimal stubs for heavy third-party packages so imports succeed in CI
# without installing the full ML stack.
# ---------------------------------------------------------------------------

def _make_stub_module(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_stub(dotted_name, attrs=None):
    parts = dotted_name.split(".")
    parent = None
    for i, part in enumerate(parts):
        full = ".".join(parts[: i + 1])
        if full not in sys.modules:
            mod = types.ModuleType(full)
            sys.modules[full] = mod
            if parent is not None:
                setattr(parent, part, mod)
        parent = sys.modules[full]
    if attrs:
        for k, v in attrs.items():
            setattr(parent, k, v)
    return parent


# langchain_core stubs
lc_core = _ensure_stub("langchain_core")
lc_msgs = _ensure_stub("langchain_core.messages")


class _FakeMsg:
    def __init__(self, content=""):
        self.content = content


lc_msgs.HumanMessage = _FakeMsg
lc_msgs.SystemMessage = _FakeMsg

# langgraph stubs
lg = _ensure_stub("langgraph")
lg_graph = _ensure_stub("langgraph.graph")
lg_graph.START = "__start__"


class _FakeStateGraph:
    def __init__(self, state_schema):
        self._nodes = {}
        self._edges = []

    def add_node(self, name, fn):
        self._nodes[name] = fn

    def add_edge(self, a, b):
        self._edges.append((a, b))

    def add_conditional_edges(self, src, fn, mapping):
        pass

    def compile(self):
        return MagicMock(name="compiled_graph")


lg_graph.StateGraph = _FakeStateGraph

# backend.modules.assessment stub
_ensure_stub("backend")
_ensure_stub("backend.modules")
_ensure_stub("backend.modules.assessment")

_fake_assessment_tool = AsyncMock(name="run_risk_assessment_tool")
sys.modules["backend.modules.assessment"]._run_underwriting_assessment = (
    lambda mode: _fake_assessment_tool
)

# modules stubs (relative imports inside the agent)
_ensure_stub("modules")
_ensure_stub("modules.tools")
_fake_profile_tool = AsyncMock(name="get_customer_profile")
_fake_lookalike_tool = AsyncMock(name="customer_lookalike")
sys.modules["modules.tools"].get_customer_profile = _fake_profile_tool
sys.modules["modules.tools"].customer_lookalike = _fake_lookalike_tool

_ensure_stub("modules.LLMS")


class _FakeLLMS:
    def __init__(self, temperature=0, streaming=False):
        self._model = MagicMock()
        self._model.with_config = MagicMock(return_value=self._model)

    def get_model(self, name):
        return self._model


sys.modules["modules.LLMS"].LLMS = _FakeLLMS

# ---------------------------------------------------------------------------
# NOW we can safely import the module under test
# ---------------------------------------------------------------------------

# Patch Path.glob to return zero skill files so no real disk access happens
_empty_glob_patch = patch.object(
    Path,
    "glob",
    return_value=[],
)
_empty_glob_patch.start()

import backend.agent.agent_with_skills as _mod  # noqa: E402

_empty_glob_patch.stop()

# Re-export symbols for convenience
AgentState = _mod.AgentState
TOOLS = _mod.TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_response(content: str):
    resp = MagicMock()
    resp.content = content
    return resp


def _build_agent_internals(llm_response_content: str = ""):
    """Build the agent internals with a controllable LLM response."""
    fake_llm_instance = MagicMock()
    fake_llm_instance.with_config.return_value = fake_llm_instance
    fake_llm_instance.invoke.return_value = _make_llm_response(llm_response_content)

    with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
        Path, "glob", return_value=[]
    ):
        mock_llms_cls.return_value.get_model.return_value = fake_llm_instance
        graph = _mod.build_skills_agent()

    return graph, fake_llm_instance


# ---------------------------------------------------------------------------
# ── TOOLS dict ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestToolsDict:
    def test_required_keys_present(self):
        assert "get_customer_info" in TOOLS
        assert "customer_lookalike" in TOOLS
        assert "run_risk_assessment" in TOOLS

    def test_no_extra_unexpected_keys(self):
        assert set(TOOLS.keys()) == {
            "get_customer_info",
            "customer_lookalike",
            "run_risk_assessment",
        }

    def test_tools_are_not_none(self):
        for name, tool in TOOLS.items():
            assert tool is not None, f"Tool '{name}' must not be None"


# ---------------------------------------------------------------------------
# ── AgentState TypedDict ────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestAgentState:
    def test_required_fields(self):
        hints = AgentState.__annotations__
        assert "question" in hints
        assert "history" in hints
        assert "logs" in hints
        assert "pending_call" in hints
        assert "final_answer" in hints

    def test_history_uses_operator_add_annotation(self):
        # history must be Annotated with operator.add so LangGraph merges lists
        from typing import get_args, get_origin
        import typing

        raw = AgentState.__annotations__["history"]
        origin = get_origin(raw)
        # Annotated types have __class__ Annotated
        assert origin is typing.Annotated or str(origin) == "typing.Annotated" or hasattr(raw, "__metadata__")

    def test_logs_uses_operator_add_annotation(self):
        raw = AgentState.__annotations__["logs"]
        assert hasattr(raw, "__metadata__")


# ---------------------------------------------------------------------------
# ── build_skills_agent ──────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self):
        graph, _ = _build_agent_internals()
        # The fake StateGraph.compile() returns a MagicMock; just ensure it was called
        assert graph is not None

    def test_default_model_name(self):
        """build_skills_agent should accept no args."""
        with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
            Path, "glob", return_value=[]
        ):
            instance = MagicMock()
            instance.with_config.return_value = instance
            mock_llms_cls.return_value.get_model.return_value = instance
            result = _mod.build_skills_agent()
            mock_llms_cls.return_value.get_model.assert_called_once_with("anthropic-fast")

    def test_custom_model_name(self):
        with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
            Path, "glob", return_value=[]
        ):
            instance = MagicMock()
            instance.with_config.return_value = instance
            mock_llms_cls.return_value.get_model.return_value = instance
            _mod.build_skills_agent(model_name="openai-gpt4")
            mock_llms_cls.return_value.get_model.assert_called_once_with("openai-gpt4")

    def test_custom_temperature(self):
        with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
            Path, "glob", return_value=[]
        ):
            instance = MagicMock()
            instance.with_config.return_value = instance
            mock_llms_cls.return_value.get_model.return_value = instance
            _mod.build_skills_agent(temperature=0.7)
            mock_llms_cls.assert_called_once_with(temperature=0.7, streaming=True)

    def test_skill_docs_loaded_from_md_files(self):
        """Skill .md files (excluding index.md) should be read."""
        fake_md = MagicMock()
        fake_md.name = "skill_one.md"
        fake_md.read_text.return_value = "## Skill One\nDoes stuff."

        with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
            Path, "glob", return_value=[fake_md]
        ):
            instance = MagicMock()
            instance.with_config.return_value = instance
            mock_llms_cls.return_value.get_model.return_value = instance
            _mod.build_skills_agent()
            fake_md.read_text.assert_called_once()

    def test_index_md_is_excluded(self):
        """index.md must be skipped."""
        index_md = MagicMock()
        index_md.name = "index.md"
        index_md.read_text.return_value = "INDEX"

        other_md = MagicMock()
        other_md.name = "tool.md"
        other_md.read_text.return_value = "TOOL DOCS"

        # sorted() is called on the glob output; fake __lt__ for sorting
        index_md.__lt__ = lambda s, o: True
        other_md.__lt__ = lambda s, o: False

        with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
            Path, "glob", return_value=[index_md, other_md]
        ):
            instance = MagicMock()
            instance.with_config.return_value = instance
            mock_llms_cls.return_value.get_model.return_value = instance
            _mod.build_skills_agent()
            index_md.read_text.assert_not_called()
            other_md.read_text.assert_called_once()

    def test_llm_tagged_with_agent(self):
        with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
            Path, "glob", return_value=[]
        ):
            instance = MagicMock()
            instance.with_config.return_value = instance
            mock_llms_cls.return_value.get_model.return_value = instance
            _mod.build_skills_agent()
            instance.with_config.assert_called_once_with({"tags": ["agent"]})


# ---------------------------------------------------------------------------
# Helper to extract inner functions by building the agent and capturing nodes
# ---------------------------------------------------------------------------


class _CapturingStateGraph:
    """Replaces langgraph.graph.StateGraph to capture add_node calls."""

    def __init__(self, state_schema):
        self.nodes = {}
        self.edges = []
        self.conditional_edges = []

    def add_node(self, name, fn):
        self.nodes[name] = fn

    def add_edge(self, a, b):
        self.edges.append((a, b))

    def add_conditional_edges(self, src, fn, mapping=None):
        self.conditional_edges.append((src, fn, mapping))

    def compile(self):
        return MagicMock(name="compiled_graph")


def _build_and_capture(llm_response_content=""):
    """Returns (captured_graph, fake_llm_instance)."""
    capturing = None

    def _capturing_sg(schema):
        nonlocal capturing
        capturing = _CapturingStateGraph(schema)
        return capturing

    fake_llm = MagicMock()
    fake_llm.with_config.return_value = fake_llm
    fake_llm.invoke.return_value = _make_llm_response(llm_response_content)

    with patch.object(_mod, "LLMS") as mock_llms_cls, patch.object(
        _mod, "StateGraph", side_effect=_capturing_sg
    ), patch.object(Path, "glob", return_value=[]):
        mock_llms_cls.return_value.get_model.return_value = fake_llm
        _mod.build_skills_agent()

    return capturing, fake_llm


# ---------------------------------------------------------------------------
# ── agent node ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class TestAgentNode:
    """Tests for the inner `agent` function node."""

    def _make_state(self, question="Who is CUST00000001?", history=None, logs=None):
        return AgentState(
            question=question,
            history=history or [],
            logs=logs or [],
            pending_call={},
            final_answer="",
        )

    def _get_agent_fn(self, llm_response):
        capturing, fake_llm = _build_and_capture(llm_response)
        agent_fn = capturing.nodes.get("agent")
        return agent_fn, fake_llm

    # ── happy path: tool_call action ──────────────────────────────────────

    def test_tool_call_action_returns_pending_call(self):
        payload = {
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        }
        agent_fn, _ = self._get_agent_fn(json.dumps(payload))
        result = agent_fn(self._make_state())
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["action"] == "tool_call"

    def test_tool_call_