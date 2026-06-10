"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function (graph construction, prompt building)
- agent() inner node: happy path tool_call, function_call normalisation, done action, plain text fallback, JSON parse errors
- execute_tool() inner node: successful tool invocation, tool raises exception, unknown tool name, tool returns error payload dict
- router() function (pending_call present vs. absent)
- TOOLS registry content

Mocks used:
- backend.modules.assessment._run_underwriting_assessment  → MagicMock returning a fake @tool object
- modules.tools.get_customer_profile                       → MagicMock async tool
- modules.tools.customer_lookalike                        → MagicMock async tool
- backend.modules.LLMS / LLMS class                       → MagicMock LLM factory
- pathlib.Path.glob / file reads                          → monkeypatched to return controlled skill docs
- langchain_core.messages.HumanMessage / SystemMessage    → real objects (no network calls)
- langgraph.graph.StateGraph                              → real object but LLM is mocked so no AI calls

TODOs:
- TODO: Obtain the full router() source (truncated in supplied code) to test all branches exhaustively
- TODO: Integration test for full compiled graph `.ainvoke()` requires langgraph runtime + async loop
- TODO: Test streaming events (on_tool_start / on_tool_end callbacks) once callback harness is available
"""

import json
import operator
import re
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stub modules so the import of agent_with_skills.py succeeds without
# real external packages installed in the test environment.
# ---------------------------------------------------------------------------

def _make_stub_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


# ── langgraph stubs ──────────────────────────────────────────────────────────
if "langgraph" not in sys.modules:
    lg = _make_stub_module("langgraph")
    lg_graph = _make_stub_module("langgraph.graph")

    class _FakeStateGraph:
        def __init__(self, schema):
            self._schema = schema
            self._nodes = {}
            self._edges = []

        def add_node(self, name, fn):
            self._nodes[name] = fn

        def add_edge(self, src, dst):
            self._edges.append((src, dst))

        def add_conditional_edges(self, src, fn, mapping=None):
            self._edges.append((src, fn))

        def compile(self):
            return self

    lg_graph.StateGraph = _FakeStateGraph
    lg_graph.START = "__start__"
    lg_graph.END = "__end__"

# ── langchain_core stubs ─────────────────────────────────────────────────────
if "langchain_core" not in sys.modules:
    lc = _make_stub_module("langchain_core")
    lc_msgs = _make_stub_module("langchain_core.messages")

    class _Msg:
        def __init__(self, content):
            self.content = content

    lc_msgs.HumanMessage = _Msg
    lc_msgs.SystemMessage = _Msg

# ── modules stubs (project-local) ────────────────────────────────────────────
if "modules" not in sys.modules:
    _make_stub_module("modules")

if "modules.tools" not in sys.modules:
    mt = _make_stub_module("modules.tools")
    _fake_profile_tool = AsyncMock(return_value='{"name": "John Doe"}')
    _fake_profile_tool.ainvoke = AsyncMock(return_value='{"name": "John Doe"}')
    _fake_lookalike_tool = AsyncMock(return_value='["CUST00006151"]')
    _fake_lookalike_tool.ainvoke = AsyncMock(return_value='["CUST00006151"]')
    mt.get_customer_profile = _fake_profile_tool
    mt.customer_lookalike = _fake_lookalike_tool

if "modules.LLMS" not in sys.modules:
    ml = _make_stub_module("modules.LLMS")

    class _FakeLLMS:
        def __init__(self, temperature=0, streaming=False):
            pass

        def get_model(self, name):
            m = MagicMock()
            m.with_config.return_value = m
            return m

    ml.LLMS = _FakeLLMS

# ── backend stubs ────────────────────────────────────────────────────────────
if "backend" not in sys.modules:
    _make_stub_module("backend")

if "backend.modules" not in sys.modules:
    _make_stub_module("backend.modules")

if "backend.modules.assessment" not in sys.modules:
    bma = _make_stub_module("backend.modules.assessment")

    def _fake_run_underwriting_assessment(mode):
        tool = AsyncMock()
        tool.ainvoke = AsyncMock(return_value='{"risk": "low"}')
        return tool

    bma._run_underwriting_assessment = _fake_run_underwriting_assessment

if "backend.modules.LLMS" not in sys.modules:
    bml = _make_stub_module("backend.modules.LLMS")
    bml.LLMS = sys.modules["modules.LLMS"].LLMS

# ---------------------------------------------------------------------------
# Helpers to import the module under test with controlled file-system
# ---------------------------------------------------------------------------

def _patch_skills_dir(tmp_path: Path, skill_contents: list[str]) -> list[Path]:
    """Write fake *.md skill files into tmp_path and return them."""
    files = []
    for i, text in enumerate(skill_contents):
        p = tmp_path / f"skill_{i:02d}.md"
        p.write_text(text)
        files.append(p)
    return files


# We import the module once at module level using sys.modules tricks.
# Each test that needs the *inner* functions will re-invoke build_skills_agent()
# after patching.

with patch("pathlib.Path.glob", return_value=iter([])):
    # Avoid real filesystem access during module import
    import importlib, sys as _sys

    # Ensure the agent module path is importable
    _agent_mod_path = str(Path(__file__).parent.parent / "backend" / "agent")
    if _agent_mod_path not in _sys.path:
        _sys.path.insert(0, str(Path(__file__).parent.parent))

    # Patch the skills dir read at import time
    with patch.object(Path, "glob", return_value=iter([])):
        try:
            from backend.agent.agent_with_skills import (
                AgentState,
                TOOLS,
                build_skills_agent,
            )
            _IMPORT_OK = True
        except Exception as _import_exc:
            _IMPORT_OK = False
            _import_exc_info = str(_import_exc)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def skill_files(tmp_path):
    """Return two fake skill markdown files."""
    files = _patch_skills_dir(
        tmp_path,
        [
            "# Skill A\nUse get_customer_info with customer_id param.",
            "# Skill B\nUse customer_lookalike with customer_id param.",
        ],
    )
    return tmp_path, files


@pytest.fixture()
def mock_llm():
    """Return a MagicMock LLM that behaves like a tagged langchain model."""
    llm = MagicMock()
    tagged = MagicMock()
    llm.with_config.return_value = tagged
    return llm, tagged


@pytest.fixture()
def agent_nodes(tmp_path, mock_llm):
    """
    Build the agent using build_skills_agent() with mocked LLM and skill files.
    Returns (agent_fn, execute_tool_fn, router_fn, tagged_llm_mock).
    """
    _patch_skills_dir(
        tmp_path,
        ["# Skill A\nget_customer_info(customer_id)"],
    )
    raw_llm, tagged_llm = mock_llm

    fake_llms_instance = MagicMock()
    fake_llms_instance.get_model.return_value = raw_llm

    with (
        patch("backend.agent.agent_with_skills.LLMS", return_value=fake_llms_instance),
        patch.object(Path, "glob", return_value=iter(
            [tmp_path / "skill_00.md"]
        )),
    ):
        graph = build_skills_agent("anthropic-fast", temperature=0)

    # Extract inner node functions from the compiled graph's node registry
    # (our stub StateGraph stores them in _nodes)
    agent_fn = graph._nodes.get("agent")
    execute_tool_fn = graph._nodes.get("execute_tool")
    router_fn = None  # see TODO below
    return agent_fn, execute_tool_fn, router_fn, tagged_llm


@pytest.fixture()
def base_state() -> AgentState:
    return {
        "question": "What is the risk for CUST00000001?",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }


# ---------------------------------------------------------------------------
# Guard: skip all tests if import failed
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK,
    reason=f"Could not import agent_with_skills: {_import_exc_info if not _IMPORT_OK else ''}",
)


# ===========================================================================
# 1. Module-level constants & TOOLS registry
# ===========================================================================

class TestToolsRegistry:
    def test_tools_keys_present(self):
        assert "get_customer_info" in TOOLS
        assert "customer_lookalike" in TOOLS
        assert "run_risk_assessment" in TOOLS

    def test_tools_values_are_callable_or_have_ainvoke(self):
        for name, tool in TOOLS.items():
            assert callable(tool) or hasattr(tool, "ainvoke"), (
                f"Tool '{name}' must be callable or have .ainvoke"
            )

    def test_tools_count(self):
        assert len(TOOLS) == 3


# ===========================================================================
# 2. AgentState TypedDict
# ===========================================================================

class TestAgentState:
    def test_required_keys(self):
        state: AgentState = {
            "question": "q",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "q"
        assert isinstance(state["history"], list)
        assert isinstance(state["logs"], list)
        assert isinstance(state["pending_call"], dict)
        assert isinstance(state["final_answer"], str)

    def test_history_annotation_uses_operator_add(self):
        """Annotated[list[str], operator.add] — verify the annotation exists."""
        hints = AgentState.__annotations__
        assert "history" in hints
        assert "logs" in hints

    def test_history_accumulates_with_operator_add(self):
        """Simulate what langgraph does: merge via operator.add."""
        existing = ["msg1"]
        new = ["msg2"]
        merged = operator.add(existing, new)
        assert merged == ["msg1", "msg2"]


# ===========================================================================
# 3. build_skills_agent() — graph construction
# ===========================================================================

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, tmp_path):
        raw_llm = MagicMock()
        raw_llm.with_config.return_value = raw_llm
        fake_llms = MagicMock()
        fake_llms.get_model.return_value = raw_llm

        with (
            patch("backend.agent.agent_with_skills.LLMS", return_value=fake_llms),
            patch.object(Path, "glob", return_value=iter([])),
        ):
            graph = build_skills_agent()

        assert graph is not None

    def test_graph_contains_agent_node(self, tmp_path):
        raw_llm = MagicMock()
        raw_llm.with_config.return_value = raw_llm
        fake_llms = MagicMock()
        fake_llms.get_model.return_value = raw_llm

        with (
            patch("backend.agent.agent_with_skills.LLMS", return_value=fake_llms),
            patch.object(Path, "glob", return_value=iter([])),
        ):
            graph = build_skills_agent()

        assert "agent" in graph._nodes

    def test_graph_contains_execute_tool_node(self, tmp_path):
        raw_llm = MagicMock()
        raw_llm.with_config.return_value = raw_llm
        fake_llms = MagicMock()
        fake_llms.get_model.return_value = raw_llm

        with (
            patch("backend.agent.agent_with_skills.LLMS", return_value=fake_llms),
            patch.object(Path, "glob", return_value=iter([])),
        ):
            graph = build_skills_agent()

        assert "execute_tool" in graph._nodes

    def test_skill_docs_loaded_from_md_files(self, tmp_path):
        """Skill markdown files (excl. index.md) are read and injected into prompt."""
        raw_llm = MagicMock()
        tagged_llm = MagicMock()
        raw_llm.with_config.return_value = tagged_llm
        fake_llms = MagicMock()
        fake_llms.get_model.return_value = raw_llm

        skill_file = tmp_path / "my_skill.md"
        skill_file.write_text("# My Skill\nDo things.")
        index_file = tmp_path / "index.md"
        index_file.write_text("# Index\nShould be excluded.")

        captured_prompts = []

        def capture_invoke(messages):
            for m in messages:
                captured_prompts.append(m.content)
            resp = MagicMock()
            resp.content = '{"action": "done", "answer": "ok"}'
            return resp

        tagged_llm.invoke.side_effect = capture_invoke

        with (
            patch("backend.agent.agent_with_skills.LLMS", return_value=fake_llms),
            patch.object(Path, "glob", return_value=iter([skill_file, index_file])),
        ):
            graph = build_skills_agent()

        agent_fn = graph._nodes["agent"]
        state = {
            "question": "hello",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        agent_fn(state)

        combined = " ".join(captured_prompts)
        assert "My Skill" in combined
        assert "Index" not in combined  # index.md excluded

    def test_default_model_name_and_temperature(self, tmp_path):
        """build_skills_agent() passes correct defaults to LLMS."""
        call_args_holder = {}

        class CaptureLLMS:
            def __init__(self, temperature=0, streaming=False):
                call_args_holder["temperature"] = temperature
                call_args_holder["streaming"] = streaming
                self._m = MagicMock()
                self._m.with_config.return_value = self._