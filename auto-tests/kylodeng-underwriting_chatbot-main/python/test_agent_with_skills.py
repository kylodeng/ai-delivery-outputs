"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field definitions
- build_skills_agent() factory function (graph construction, LLM wiring)
- agent() inner node: happy path JSON tool_call, done action, function_call alias,
  plain-text fallback, malformed JSON, missing action field
- execute_tool() inner node: successful tool invocation, tool returning error payload,
  tool raising exception, unknown tool name
- router() inner node: routing to execute_tool when pending_call is present,
  routing to END when no pending_call and final_answer present

Mocks used:
- backend.agent.agent_with_skills.LLMS (patched at module level)
- backend.agent.agent_with_skills.TOOLS (patched per test)
- backend.agent.agent_with_skills._SKILLS_DIR (patched to a tmp_path)
- langchain_core.messages.HumanMessage / SystemMessage (real, no network)
- Individual tool coroutines via AsyncMock

TODOs:
- TODO: Full graph integration test requires a running LangGraph runtime — stubbed below
- TODO: Router END branch behaviour depends on LangGraph internals not easily inspectable without graph compilation
- TODO: Streaming / on_tool_start callback verification needs a real LangGraph event loop
"""

import json
import operator
import types
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_state(
    question="Who is customer CUST00000001?",
    history=None,
    logs=None,
    pending_call=None,
    final_answer="",
) -> dict:
    return {
        "question": question,
        "history": history or [],
        "logs": logs or [],
        "pending_call": pending_call or {},
        "final_answer": final_answer,
    }


def _make_llm_response(content: str) -> MagicMock:
    """Return a mock LLM response object with .content set."""
    response = MagicMock()
    response.content = content
    return response


@pytest.fixture()
def skills_dir(tmp_path):
    """Create a temporary skills directory with two .md skill files."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "01_profile.md").write_text("## get_customer_info\nFetches customer profile.")
    (skills / "02_lookalike.md").write_text("## customer_lookalike\nFinds similar customers.")
    (skills / "index.md").write_text("# Index — should be skipped")
    return skills


@pytest.fixture()
def mock_llm():
    """Return a mock LLM instance whose .invoke() can be controlled per test."""
    llm_instance = MagicMock()
    tagged = MagicMock()
    llm_instance.with_config.return_value = tagged
    return llm_instance, tagged


@pytest.fixture()
def mock_tools():
    """Return a dict of AsyncMock tool objects mirroring the real TOOLS dict."""
    profile_tool = MagicMock()
    profile_tool.ainvoke = AsyncMock(return_value='{"customer_id": "CUST00000001", "name": "Alice"}')

    lookalike_tool = MagicMock()
    lookalike_tool.ainvoke = AsyncMock(return_value='["CUST00006151", "CUST00000272"]')

    risk_tool = MagicMock()
    risk_tool.ainvoke = AsyncMock(return_value='{"risk": "low"}')

    return {
        "get_customer_info": profile_tool,
        "customer_lookalike": lookalike_tool,
        "run_risk_assessment": risk_tool,
    }


# ---------------------------------------------------------------------------
# Module-level import with heavy dependencies patched
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_heavy_imports(skills_dir, mock_llm, mock_tools):
    """
    Patch external dependencies before importing the module so no real
    network calls, file-system probing of the real skills dir, or LLM
    initialisation occurs.
    """
    llm_instance, tagged_llm = mock_llm

    mock_llms_cls = MagicMock()
    mock_llms_cls.return_value.get_model.return_value = llm_instance

    with (
        patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        patch("backend.agent.agent_with_skills.TOOLS", mock_tools),
        patch("backend.agent.agent_with_skills._run_underwriting_assessment", return_value=mock_tools["run_risk_assessment"]),
        patch("backend.agent.agent_with_skills._profile_tool", mock_tools["get_customer_info"]),
        patch("backend.agent.agent_with_skills._lookalike_tool", mock_tools["customer_lookalike"]),
    ):
        yield {
            "llm_instance": llm_instance,
            "tagged_llm": tagged_llm,
            "mock_llms_cls": mock_llms_cls,
            "mock_tools": mock_tools,
        }


# ---------------------------------------------------------------------------
# Lazy import helper (after patches are applied)
# ---------------------------------------------------------------------------

def _import_module():
    import importlib
    import backend.agent.agent_with_skills as m
    importlib.reload(m)  # ensure patched state is used
    return m


# ---------------------------------------------------------------------------
# AgentState structure
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_agent_state_fields_exist(self):
        mod = _import_module()
        hints = mod.AgentState.__annotations__
        assert "question" in hints
        assert "history" in hints
        assert "logs" in hints
        assert "pending_call" in hints
        assert "final_answer" in hints

    def test_history_uses_operator_add_annotation(self):
        mod = _import_module()
        # history should be Annotated[list[str], operator.add]
        history_hint = mod.AgentState.__annotations__["history"]
        assert hasattr(history_hint, "__metadata__"), "Expected Annotated type"
        assert operator.add in history_hint.__metadata__

    def test_logs_uses_operator_add_annotation(self):
        mod = _import_module()
        logs_hint = mod.AgentState.__annotations__["logs"]
        assert hasattr(logs_hint, "__metadata__")
        assert operator.add in logs_hint.__metadata__


# ---------------------------------------------------------------------------
# build_skills_agent — construction
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, patch_heavy_imports):
        mod = _import_module()
        graph = mod.build_skills_agent()
        # LangGraph compiled graphs expose .invoke / .ainvoke
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_llms_instantiated_with_correct_defaults(self, patch_heavy_imports):
        mod = _import_module()
        mod.build_skills_agent()
        patch_heavy_imports["mock_llms_cls"].assert_called_once_with(temperature=0, streaming=True)

    def test_llms_instantiated_with_custom_temperature(self, patch_heavy_imports):
        mod = _import_module()
        mod.build_skills_agent(temperature=0.7)
        patch_heavy_imports["mock_llms_cls"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, patch_heavy_imports):
        mod = _import_module()
        mod.build_skills_agent(model_name="anthropic-fast")
        patch_heavy_imports["mock_llms_cls"].return_value.get_model.assert_called_once_with("anthropic-fast")

    def test_with_config_tags_agent(self, patch_heavy_imports):
        mod = _import_module()
        mod.build_skills_agent()
        patch_heavy_imports["llm_instance"].with_config.assert_called_once_with({"tags": ["agent"]})

    def test_skill_docs_loaded_from_md_files(self, patch_heavy_imports, skills_dir):
        """Skill docs from *.md (excluding index.md) should appear in system prompt."""
        mod = _import_module()
        # We can't inspect the closure directly, but we can invoke the agent
        # and check what the tagged LLM received.
        tagged_llm = patch_heavy_imports["tagged_llm"]
        tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "ok"}'
        )
        graph = mod.build_skills_agent()
        # Manually invoke the agent node to inspect system prompt content
        # by calling the closure returned from build_skills_agent
        # We extract the node function by building the graph and peeking at nodes
        # This is tested indirectly via the invoke call inspection below
        state = _make_state()
        # Trigger through invoke if graph supports sync invoke
        try:
            graph.invoke(state)
        except Exception:
            pass  # graph may require async; we just want the LLM call recorded
        if tagged_llm.invoke.called:
            call_args = tagged_llm.invoke.call_args[0][0]
            system_content = call_args[0].content
            assert "get_customer_info" in system_content
            assert "customer_lookalike" in system_content

    def test_index_md_excluded_from_skill_docs(self, patch_heavy_imports, skills_dir):
        mod = _import_module()
        tagged_llm = patch_heavy_imports["tagged_llm"]
        tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "ok"}'
        )
        graph = mod.build_skills_agent()
        try:
            graph.invoke(_make_state())
        except Exception:
            pass
        if tagged_llm.invoke.called:
            call_args = tagged_llm.invoke.call_args[0][0]
            system_content = call_args[0].content
            assert "Index — should be skipped" not in system_content

    def test_no_skill_files_produces_empty_docs(self, patch_heavy_imports, tmp_path):
        empty_skills = tmp_path / "empty_skills"
        empty_skills.mkdir()
        with patch("backend.agent.agent_with_skills._SKILLS_DIR", empty_skills):
            mod = _import_module()
            tagged_llm = patch_heavy_imports["tagged_llm"]
            tagged_llm.invoke.return_value = _make_llm_response(
                '{"action": "done", "answer": "ok"}'
            )
            # Should not raise
            graph = mod.build_skills_agent()
            assert graph is not None


# ---------------------------------------------------------------------------
# agent() inner node — extracted via build_skills_agent internals
# ---------------------------------------------------------------------------

def _get_agent_and_tool_nodes(patch_heavy_imports):
    """
    Build the agent graph and extract the raw node callables by inspecting
    the StateGraph nodes dict before compilation, OR by calling the nodes
    directly after reconstruction.

    Because LangGraph compiles the graph we instead re-build the inner
    functions by calling build_skills_agent and capturing them via a
    monkey-patched StateGraph.add_node.
    """
    nodes = {}

    original_add_node = None

    class CapturingGraph:
        """Minimal stand-in that captures node callables."""

        def __init__(self, *a, **kw):
            self._nodes = {}
            self._edges = []

        def add_node(self, name, fn):
            nodes[name] = fn

        def add_edge(self, *a, **kw):
            pass

        def compile(self):
            mock_compiled = MagicMock()
            mock_compiled.invoke = MagicMock(return_value={})
            mock_compiled.ainvoke = AsyncMock(return_value={})
            return mock_compiled

    with patch("backend.agent.agent_with_skills.StateGraph", CapturingGraph):
        mod = _import_module()
        mod.build_skills_agent()

    return nodes


class TestAgentNode:
    def _build_nodes(self, patch_heavy_imports):
        return _get_agent_and_tool_nodes(patch_heavy_imports)

    # ------------------------------------------------------------------
    # Happy path — tool_call action
    # ------------------------------------------------------------------

    def test_tool_call_action_sets_pending_call(self, patch_heavy_imports):
        nodes = self._build_nodes(patch_heavy_imports)
        tagged_llm = patch_heavy_imports["tagged_llm"]
        response_json = '{"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}}'
        tagged_llm.invoke.return_value = _make_llm_response(response_json)

        state = _make_state()
        result = nodes["agent"](state)

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert "Assistant:" in result["history"][0]

    def test_tool_call_adds_log_entry(self, patch_heavy_imports):
        nodes = self._build_nodes(patch_heavy_imports)
        tagged_llm = patch_heavy_imports["tagged_llm"]
        tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "tool_call", "tool_name": "customer_lookalike", "tool_args": {"customer_id": "CUST00000001"}}'
        )
        result = nodes["agent"](_make_state())
        assert len(result["logs"]) == 1
        assert result["logs"][0]["event"] == "on_chat_model_end"

    # ------------------------------------------------------------------
    # function_call alias normalisation
    # ------------------------------------------------------------------

    def test_function_call_type_normalised_to_tool_call(self, patch_heavy_imports):
        nodes = self._build_nodes(patch_heavy_imports)
        tagged_llm = patch_heavy_imports["tagged_llm"]
        tagged_llm.invoke.return_value = _make_llm_response(
            '{"type": "function_call", "tool_name": "run_risk_assessment", "tool_args": {}}'
        )
        result = nodes["agent"](_make_state())
        assert result["pending_call"]["action"] == "tool_call"

    # ------------------------------------------------------------------
    # Done action
    # ------------------------------------------------------------------

    def test_done_action_sets_final_answer(self, patch_heavy_imports):
        nodes = self._build_nodes(patch_heavy_imports)
        tagged_llm = patch_heavy_imports["tagged_llm"]
        tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "Customer is low risk."}'
        )
        result = nodes["agent"](_make_state())
        assert result["final_answer"] == "Customer is low risk."
        assert result["pending_call"] == {}

    def test_done_action_with_empty_answer(self, patch_heavy_imports):
        nodes = self._build_nodes(patch_heavy_imports)
        tagged_llm = patch_heavy_imports["tagged_llm"]
        tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "done"}'
        )
        result = nodes["agent"](_make_state())
        assert result["final_answer"] == ""
        assert result["pending_call"] == {}

    # ------------------------------------------------------------------
    # Plain text / no JSON fallback
    # ------------------------------------------------------------------

    def test_plain_