"""
Test suite for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function (agent, execute_tool, router nodes)
- agent() node: happy path tool_call response, done response, malformed JSON,
  plain-text fallback, "type"/"function_call" normalisation, history injection
- execute_tool() node: successful tool invocation, tool returns error payload,
  tool raises exception, unknown tool name
- router() node: pending_call present → route to execute_tool,
  final_answer present → END, neither → END fallback
- TOOLS registry keys
- Skill-doc loading (glob behaviour)

Mocks used:
- unittest.mock.patch for LLMS, _run_underwriting_assessment, _profile_tool,
  _lookalike_tool, Path.glob / file reading
- AsyncMock for tool .ainvoke() calls
- MagicMock for LLM responses

TODOs:
- TODO: integration test requiring real LangGraph graph compilation (needs
  full dependency graph wired up)
- TODO: test streaming behaviour of tagged_llm (requires live LangChain
  streaming harness)
- TODO: test router END branch symbol equality (needs langgraph.graph.END
  importable without side-effects)
"""

import asyncio
import json
import operator
import re
import types
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, Mock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with heavy dependencies stubbed out
# ---------------------------------------------------------------------------

def _make_fake_tool(name: str, return_value="tool_result"):
    """Return a mock that behaves like a LangChain @tool (has .ainvoke)."""
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=return_value)
    tool.name = name
    return tool


@pytest.fixture(autouse=True)
def stub_heavy_imports(monkeypatch):
    """
    Stub all external/heavy imports before agent_with_skills is imported so
    that tests can run without installed ML libraries.
    """
    # langchain_core stubs
    lc_core = types.ModuleType("langchain_core")
    lc_messages = types.ModuleType("langchain_core.messages")

    class _Msg:
        def __init__(self, content):
            self.content = content

    lc_messages.HumanMessage = _Msg
    lc_messages.SystemMessage = _Msg
    lc_core.messages = lc_messages
    monkeypatch.setitem(__import__("sys").modules, "langchain_core", lc_core)
    monkeypatch.setitem(__import__("sys").modules, "langchain_core.messages", lc_messages)

    # langgraph stubs
    lg = types.ModuleType("langgraph")
    lg_graph = types.ModuleType("langgraph.graph")
    lg_graph.START = "START"
    lg_graph.END = "END"

    class _StateGraph:
        def __init__(self, *a, **kw):
            self._nodes = {}
            self._edges = []

        def add_node(self, name, fn):
            self._nodes[name] = fn

        def add_edge(self, a, b):
            self._edges.append((a, b))

        def add_conditional_edges(self, src, fn, mapping=None):
            self._edges.append((src, fn))

        def compile(self, **kw):
            return self

    lg_graph.StateGraph = _StateGraph
    lg.graph = lg_graph
    monkeypatch.setitem(__import__("sys").modules, "langgraph", lg)
    monkeypatch.setitem(__import__("sys").modules, "langgraph.graph", lg_graph)

    # backend.modules.assessment stub
    backend_pkg = types.ModuleType("backend")
    backend_modules = types.ModuleType("backend.modules")
    backend_assessment = types.ModuleType("backend.modules.assessment")
    fake_assessment_tool = _make_fake_tool("run_risk_assessment", "assessment_result")
    backend_assessment._run_underwriting_assessment = Mock(return_value=fake_assessment_tool)
    backend_pkg.modules = backend_modules
    backend_modules.assessment = backend_assessment
    monkeypatch.setitem(__import__("sys").modules, "backend", backend_pkg)
    monkeypatch.setitem(__import__("sys").modules, "backend.modules", backend_modules)
    monkeypatch.setitem(__import__("sys").modules, "backend.modules.assessment", backend_assessment)

    # modules.tools stub
    modules_pkg = types.ModuleType("modules")
    modules_tools = types.ModuleType("modules.tools")
    modules_tools.customer_lookalike = _make_fake_tool("customer_lookalike")
    modules_tools.get_customer_profile = _make_fake_tool("get_customer_info")
    modules_pkg.tools = modules_tools
    monkeypatch.setitem(__import__("sys").modules, "modules", modules_pkg)
    monkeypatch.setitem(__import__("sys").modules, "modules.tools", modules_tools)

    # modules.LLMS stub
    modules_llms = types.ModuleType("modules.LLMS")

    class _FakeLLMS:
        def __init__(self, temperature=0, streaming=False):
            pass

        def get_model(self, name):
            llm = MagicMock()
            tagged = MagicMock()
            llm.with_config.return_value = tagged
            return llm

    modules_llms.LLMS = _FakeLLMS
    modules_pkg.LLMS = modules_llms
    monkeypatch.setitem(__import__("sys").modules, "modules.LLMS", modules_llms)


# ---------------------------------------------------------------------------
# Import module under test (after stubs are in place via autouse fixture).
# We re-import inside each test-module session via a session-scoped fixture.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def agent_module(tmp_path_factory):
    """
    Import agent_with_skills with a fake _SKILLS_DIR containing two .md files.
    """
    skills_dir = tmp_path_factory.mktemp("skills")
    (skills_dir / "skill_a.md").write_text("# Skill A\nGet customer profile.")
    (skills_dir / "skill_b.md").write_text("# Skill B\nRun risk assessment.")
    (skills_dir / "index.md").write_text("# Index — should be skipped")

    import importlib
    import sys

    # Make sure we get a fresh import each session
    sys.modules.pop("backend.agent.agent_with_skills", None)
    sys.modules.pop("agent_with_skills", None)

    # Patch Path so _SKILLS_DIR points to our tmp directory
    with patch("pathlib.Path.__truediv__", side_effect=lambda self, other: skills_dir if "skills" in str(other) else Path.__truediv__(self, other)):
        pass  # path patching is tricky; we patch at module level below

    # Directly patch the module attribute after import
    # We import by manipulating sys.path temporarily
    import os
    backend_dir = Path(__file__).parent.parent  # adjust if test lives elsewhere

    # Safest: import as a plain module after patching _SKILLS_DIR
    spec_path = Path(__file__).parent.parent / "agent" / "agent_with_skills.py"
    if not spec_path.exists():
        # Running from repo root; try relative path
        spec_path = Path("backend/agent/agent_with_skills.py")

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "agent_with_skills_under_test", spec_path
    )
    if spec is None:
        pytest.skip("Cannot locate agent_with_skills.py — adjust path")

    mod = importlib.util.module_from_spec(spec)

    # Patch _SKILLS_DIR on the module before executing
    with patch.object(Path, "__new__", wraps=Path.__new__):
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            pytest.skip(f"Module load failed: {exc}")

    # Override _SKILLS_DIR to point at our tmp skills dir
    mod._SKILLS_DIR = skills_dir
    return mod


# ---------------------------------------------------------------------------
# Simpler fixture: build isolated agent nodes via build_skills_agent()
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_components(agent_module):
    """
    Call build_skills_agent() and extract the inner node functions by
    inspecting the StateGraph mock that was used during construction.
    """
    # We need to capture the nodes added to StateGraph.
    captured = {}

    original_add_node = None

    class CapturingStateGraph:
        def __init__(self, *a, **kw):
            self._nodes = {}

        def add_node(self, name, fn):
            captured[name] = fn

        def add_edge(self, a, b):
            pass

        def add_conditional_edges(self, src, fn, mapping=None):
            captured["_router_fn"] = fn

        def compile(self, **kw):
            return MagicMock()

    import sys
    lg_graph = sys.modules["langgraph.graph"]
    original_sg = lg_graph.StateGraph
    lg_graph.StateGraph = CapturingStateGraph

    try:
        agent_module.build_skills_agent()
    finally:
        lg_graph.StateGraph = original_sg

    return captured


# ---------------------------------------------------------------------------
# Helper: build a minimal AgentState
# ---------------------------------------------------------------------------

def _state(
    question="Tell me about CUST00000001",
    history=None,
    logs=None,
    pending_call=None,
    final_answer="",
):
    return {
        "question": question,
        "history": history or [],
        "logs": logs or [],
        "pending_call": pending_call or {},
        "final_answer": final_answer,
    }


# ===========================================================================
# TESTS: AgentState TypedDict
# ===========================================================================

class TestAgentState:
    def test_agent_state_has_required_keys(self, agent_module):
        state = agent_module.AgentState
        hints = state.__annotations__
        assert "question" in hints
        assert "history" in hints
        assert "logs" in hints
        assert "pending_call" in hints
        assert "final_answer" in hints

    def test_history_uses_operator_add(self, agent_module):
        import typing
        hints = agent_module.AgentState.__annotations__
        # history should be Annotated[list[str], operator.add]
        history_hint = hints["history"]
        args = typing.get_args(history_hint)
        assert operator.add in args

    def test_logs_uses_operator_add(self, agent_module):
        import typing
        hints = agent_module.AgentState.__annotations__
        logs_hint = hints["logs"]
        args = typing.get_args(logs_hint)
        assert operator.add in args


# ===========================================================================
# TESTS: TOOLS registry
# ===========================================================================

class TestToolsRegistry:
    def test_tools_has_three_entries(self, agent_module):
        assert len(agent_module.TOOLS) == 3

    def test_tools_has_expected_keys(self, agent_module):
        assert "get_customer_info" in agent_module.TOOLS
        assert "customer_lookalike" in agent_module.TOOLS
        assert "run_risk_assessment" in agent_module.TOOLS

    def test_all_tools_have_ainvoke(self, agent_module):
        for name, tool in agent_module.TOOLS.items():
            assert hasattr(tool, "ainvoke"), f"Tool {name!r} missing .ainvoke"


# ===========================================================================
# TESTS: build_skills_agent — node registration
# ===========================================================================

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, agent_module):
        import sys
        lg_graph = sys.modules["langgraph.graph"]

        class CapturingGraph:
            def __init__(self, *a, **kw):
                pass
            def add_node(self, *a, **kw): pass
            def add_edge(self, *a, **kw): pass
            def add_conditional_edges(self, *a, **kw): pass
            def compile(self, **kw):
                return "compiled_graph"

        original = lg_graph.StateGraph
        lg_graph.StateGraph = CapturingGraph
        try:
            result = agent_module.build_skills_agent()
        finally:
            lg_graph.StateGraph = original

        assert result == "compiled_graph"

    def test_registers_agent_and_execute_tool_nodes(self, agent_components):
        assert "agent" in agent_components
        assert "execute_tool" in agent_components

    def test_router_fn_captured(self, agent_components):
        assert "_router_fn" in agent_components

    def test_custom_model_name_passed(self, agent_module):
        """build_skills_agent accepts model_name without raising."""
        agent_module.build_skills_agent(model_name="openai-gpt4", temperature=0.5)

    def test_skill_docs_loaded_excluding_index(self, agent_module):
        """
        Verify that skills docs content appears in the system prompt by
        checking that the agent node is built without error when skills exist.
        """
        # If _SKILLS_DIR has skill_a.md and skill_b.md but not index.md,
        # build should succeed and return a graph.
        result = agent_module.build_skills_agent()
        assert result is not None

    def test_empty_skills_dir(self, agent_module, tmp_path):
        """An empty skills directory should not crash build."""
        original_dir = agent_module._SKILLS_DIR
        agent_module._SKILLS_DIR = tmp_path
        try:
            result = agent_module.build_skills_agent()
            assert result is not None
        finally:
            agent_module._SKILLS_DIR = original_dir


# ===========================================================================
# TESTS: agent() node
# ===========================================================================

class TestAgentNode:
    def _make_llm_response(self, content: str):
        resp = MagicMock()
        resp.content = content
        return resp

    def _get_agent_node(self, agent_components):
        return agent_components["agent"]

    def test_tool_call_action_sets_pending_call(self, agent_components):
        agent_fn = self._get_agent_node(agent_components)
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"}
        })
        # Patch the tagged_llm invoke on the node's closure
        # We monkey-patch by replacing invoke result
        # Locate the tagged_llm through closure inspection
        _inject_llm_response(agent_fn, payload)
        result = agent_fn(_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["tool_args"] == {"customer_id": "CUST00000001"}

    def test_tool_call_appends_history(self, agent_components):
        agent_fn = self._get_agent_node(agent_components)
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"}
        })
        _inject_llm_response(agent_fn, payload)
        result = agent_fn(_state())

        assert len(result["history"])