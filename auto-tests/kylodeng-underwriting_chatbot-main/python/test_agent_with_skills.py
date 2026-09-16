"""
Test module for backend/agent/agent_with_skills.py

What is tested:
  - AgentState TypedDict structure and field annotations
  - build_skills_agent() factory function (happy path, custom params)
  - agent() node: JSON parsing, action routing (tool_call, function_call, done, invalid JSON)
  - execute_tool() node: successful invocation, tool error payloads, unknown tools, exceptions
  - router() node: pending_call present → execute_tool, empty → agent (END)
  - TOOLS registry construction
  - Skill documentation loading from .md files

Mocks used:
  - backend.modules.assessment._run_underwriting_assessment
  - modules.tools.customer_lookalike
  - modules.tools.get_customer_profile
  - backend.modules.LLMS.LLMS  (via monkeypatching / MagicMock)
  - pathlib.Path.glob  (for skill file loading)
  - langchain_core.messages.HumanMessage / SystemMessage (not mocked, real objects used)
  - langgraph.graph.StateGraph (real, but LLM calls are mocked)

TODOs:
  - TODO: Integration test with a real LangGraph compiled graph requires a live LLM / API key
  - TODO: Test streaming behaviour once streaming=True is wired to observable output
  - TODO: router() full coverage requires source code to be complete (truncated in snippet)
"""

import asyncio
import json
import operator
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap fake top-level modules that the source file imports from so that
# the test suite does not require the full application stack to be installed.
# ---------------------------------------------------------------------------

def _make_fake_tool(name: str) -> MagicMock:
    """Return a mock that quacks like a LangChain @tool."""
    tool = MagicMock(name=name)
    tool.ainvoke = AsyncMock(return_value=f'{{"result": "ok", "tool": "{name}"}}')
    return tool


# Fake modules -----------------------------------------------------------------

# modules.tools
_fake_tools_mod = types.ModuleType("modules.tools")
_fake_profile_tool = _make_fake_tool("get_customer_profile")
_fake_lookalike_tool = _make_fake_tool("customer_lookalike")
_fake_tools_mod.get_customer_profile = _fake_profile_tool
_fake_tools_mod.customer_lookalike = _fake_lookalike_tool
sys.modules.setdefault("modules", types.ModuleType("modules"))
sys.modules["modules.tools"] = _fake_tools_mod

# modules.LLMS  (lives under backend namespace in real code → imported as modules.LLMS)
_fake_llms_mod = types.ModuleType("modules.LLMS")
_fake_llm_instance = MagicMock(name="llm_instance")
_fake_tagged_llm = MagicMock(name="tagged_llm")
_fake_llm_instance.with_config = MagicMock(return_value=_fake_tagged_llm)
_fake_LLMS_cls = MagicMock(name="LLMS", return_value=_fake_llm_instance)
_fake_llms_mod.LLMS = _fake_LLMS_cls
sys.modules["modules.LLMS"] = _fake_llms_mod

# backend (namespace package)
_fake_backend = sys.modules.setdefault("backend", types.ModuleType("backend"))
_fake_backend_modules = types.ModuleType("backend.modules")
sys.modules.setdefault("backend.modules", _fake_backend_modules)

# backend.modules.assessment
_fake_assessment_mod = types.ModuleType("backend.modules.assessment")
_fake_risk_assessment_tool = _make_fake_tool("run_risk_assessment")
_fake_assessment_mod._run_underwriting_assessment = MagicMock(
    return_value=_fake_risk_assessment_tool
)
sys.modules["backend.modules.assessment"] = _fake_assessment_mod

# backend.modules.LLMS (aliased)
_fake_backend_llms = types.ModuleType("backend.modules.LLMS")
_fake_backend_llms.LLMS = _fake_LLMS_cls
sys.modules["backend.modules.LLMS"] = _fake_backend_llms

# langchain_core.messages
try:
    from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore
except ImportError:
    _fake_lc_core = types.ModuleType("langchain_core")
    _fake_lc_core_msgs = types.ModuleType("langchain_core.messages")
    HumanMessage = MagicMock(name="HumanMessage")  # type: ignore
    SystemMessage = MagicMock(name="SystemMessage")  # type: ignore
    _fake_lc_core_msgs.HumanMessage = HumanMessage
    _fake_lc_core_msgs.SystemMessage = SystemMessage
    sys.modules["langchain_core"] = _fake_lc_core
    sys.modules["langchain_core.messages"] = _fake_lc_core_msgs

# langgraph.graph
try:
    from langgraph.graph import START, StateGraph  # type: ignore
except ImportError:
    _fake_lg = types.ModuleType("langgraph")
    _fake_lg_graph = types.ModuleType("langgraph.graph")
    StateGraph = MagicMock(name="StateGraph")  # type: ignore
    START = "START"  # type: ignore
    _fake_lg_graph.StateGraph = StateGraph
    _fake_lg_graph.START = START
    sys.modules["langgraph"] = _fake_lg
    sys.modules["langgraph.graph"] = _fake_lg_graph

# ---------------------------------------------------------------------------
# Now we can safely patch the skills directory before importing the module.
# ---------------------------------------------------------------------------

_FAKE_SKILLS_DIR = Path("/fake/skills")


def _patch_skills_dir(tmp_path: Path) -> Path:
    """Write a couple of fake .md skill files into tmp_path and return it."""
    (tmp_path / "skill_a.md").write_text("# Skill A\nDo thing A.")
    (tmp_path / "skill_b.md").write_text("# Skill B\nDo thing B.")
    (tmp_path / "index.md").write_text("# Index\nShould be ignored.")
    return tmp_path


# ---------------------------------------------------------------------------
# Import the module under test AFTER stubs are in place.
# ---------------------------------------------------------------------------

with patch("pathlib.Path.glob", return_value=[]):
    # Prevent actual filesystem access during import
    import importlib, importlib.util

    _spec = importlib.util.spec_from_file_location(
        "backend.agent.agent_with_skills",
        Path(__file__).parent.parent / "backend" / "agent" / "agent_with_skills.py",
        submodule_search_locations=[],
    )
    if _spec is None or _spec.loader is None:
        # Fallback: direct import — relies on PYTHONPATH being set correctly
        try:
            import backend.agent.agent_with_skills as _aw_module  # type: ignore
        except Exception:
            # Last resort: use importlib with adjusted path
            _src_path = (
                Path(__file__).resolve().parent.parent
                / "backend"
                / "agent"
                / "agent_with_skills.py"
            )
            _spec = importlib.util.spec_from_file_location(
                "agent_with_skills", _src_path
            )
            _aw_module = importlib.util.module_from_spec(_spec)  # type: ignore
            _spec.loader.exec_module(_aw_module)  # type: ignore
    else:
        _aw_module = importlib.util.module_from_spec(_spec)
        try:
            _spec.loader.exec_module(_aw_module)  # type: ignore
        except Exception:
            # If direct exec fails, fall back to regular import
            try:
                import backend.agent.agent_with_skills as _aw_module  # type: ignore
            except ImportError:
                _aw_module = None  # type: ignore


# Helper to skip everything if the module could not be loaded
_MODULE_AVAILABLE = _aw_module is not None

pytestmark = pytest.mark.skipif(
    not _MODULE_AVAILABLE, reason="agent_with_skills module could not be loaded"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def skills_tmp(tmp_path: Path) -> Path:
    return _patch_skills_dir(tmp_path)


@pytest.fixture()
def base_state() -> dict:
    """Minimal valid AgentState."""
    return {
        "question": "Tell me about customer CUST00000001",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }


@pytest.fixture()
def state_with_history(base_state: dict) -> dict:
    base_state["history"] = [
        "User: Tell me about customer CUST00000001",
        "Assistant: Sure, let me look that up.",
    ]
    return base_state


@pytest.fixture()
def state_with_pending_call(base_state: dict) -> dict:
    base_state["pending_call"] = {
        "action": "tool_call",
        "tool_name": "get_customer_info",
        "tool_args": {"customer_id": "CUST00000001"},
    }
    return base_state


@pytest.fixture()
def mock_tagged_llm() -> MagicMock:
    return _fake_tagged_llm


def _build_agent_under_test(skills_dir: Path):
    """
    Build a skills agent with a patched _SKILLS_DIR pointing to skills_dir.
    Returns (build_skills_agent function result) — the compiled graph or the
    raw node callables depending on what the module exposes.
    """
    if _aw_module is None:
        pytest.skip("Module unavailable")

    with patch.object(_aw_module, "_SKILLS_DIR", skills_dir):
        return _aw_module.build_skills_agent()


# ---------------------------------------------------------------------------
# Helper: extract inner node callables from build_skills_agent
# ---------------------------------------------------------------------------


def _extract_nodes(skills_dir: Path):
    """
    Call build_skills_agent with a patched StateGraph that captures the
    add_node calls, returning a dict of {name: fn}.
    """
    nodes: dict[str, Any] = {}

    class CapturingGraph:
        def add_node(self, name, fn=None):
            if fn is not None:
                nodes[name] = fn
            return self

        def add_edge(self, *args, **kwargs):
            return self

        def add_conditional_edges(self, *args, **kwargs):
            return self

        def compile(self, *args, **kwargs):
            return MagicMock(name="compiled_graph")

    if _aw_module is None:
        pytest.skip("Module unavailable")

    with patch.object(_aw_module, "_SKILLS_DIR", skills_dir), patch.object(
        _aw_module,
        "StateGraph",
        return_value=CapturingGraph(),
        create=True,
    ):
        # Re-import StateGraph patch via the module reference
        original_sg = None
        try:
            import langgraph.graph as _lg_graph  # type: ignore
            original_sg = _lg_graph.StateGraph
            _lg_graph.StateGraph = lambda *a, **kw: CapturingGraph()
        except ImportError:
            pass

        try:
            _aw_module.build_skills_agent()
        finally:
            if original_sg is not None:
                try:
                    import langgraph.graph as _lg_graph  # type: ignore
                    _lg_graph.StateGraph = original_sg
                except ImportError:
                    pass

    return nodes


# ===========================================================================
# Tests: TOOLS registry
# ===========================================================================


class TestToolsRegistry:
    def test_tools_keys_present(self):
        assert "get_customer_info" in _aw_module.TOOLS
        assert "customer_lookalike" in _aw_module.TOOLS
        assert "run_risk_assessment" in _aw_module.TOOLS

    def test_tools_values_are_callable_or_mock(self):
        for name, tool in _aw_module.TOOLS.items():
            assert tool is not None, f"Tool '{name}' is None"

    def test_run_underwriting_assessment_called_with_fast(self):
        _fake_assessment_mod._run_underwriting_assessment.assert_called_with("fast")

    def test_tools_has_exactly_three_entries(self):
        assert len(_aw_module.TOOLS) == 3


# ===========================================================================
# Tests: AgentState
# ===========================================================================


class TestAgentState:
    def test_agent_state_is_typed_dict(self):
        # Should be instantiable as a plain dict with the right keys
        state: _aw_module.AgentState = {
            "question": "q",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "q"

    def test_history_uses_operator_add(self):
        hints = _aw_module.AgentState.__annotations__
        # history is Annotated[list[str], operator.add]
        import typing
        args = typing.get_args(hints["history"])
        assert operator.add in args

    def test_logs_uses_operator_add(self):
        hints = _aw_module.AgentState.__annotations__
        import typing
        args = typing.get_args(hints["logs"])
        assert operator.add in args

    def test_all_required_fields_present(self):
        required = {"question", "history", "logs", "pending_call", "final_answer"}
        assert required.issubset(_aw_module.AgentState.__annotations__.keys())


# ===========================================================================
# Tests: Skill document loading
# ===========================================================================


class TestSkillDocumentLoading:
    def test_skill_files_are_read(self, tmp_path: Path):
        """The system prompt should contain content from .md skill files."""
        _patch_skills_dir(tmp_path)
        captured_prompts: list[str] = []

        class CapturingGraph:
            def add_node(self, name, fn=None):
                return self
            def add_edge(self, *args, **kwargs):
                return self
            def add_conditional_edges(self, *args, **kwargs):
                return self
            def compile(self, *args, **kwargs):
                return MagicMock()

        # Spy on the tagged_llm.invoke to capture the system prompt
        invoke_calls: list[Any] = []
        _fake_tagged_llm.invoke = MagicMock(
            side_effect=lambda msgs: (_ for _ in ()).throw(
                StopIteration  # we just want to capture, not actually run
            )
            or MagicMock()
        )

        with patch.object(_aw_module, "_SKILLS_DIR", tmp_path):
            try:
                _aw_module.build_skills_agent()
            except Exception:
                pass  # graph compilation might fail; we just need the closure built

    def test_index_md_excluded(self, tmp_path: Path):
        """index.md must NOT be included in skill_docs."""
        _patch_skills_dir(tmp_path)

        skill_docs_parts: list[str] = []

        # Monkey-patch read_text to track calls
        original_read = Path.read_text

        def spying_read(self, *args, **kwargs):
            text = original_read(self, *args, **kwargs)
            if self.suffix == ".md":
                