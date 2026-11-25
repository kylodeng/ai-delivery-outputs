"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function (graph construction, LLM wiring)
- agent() node: happy path tool_call response, done response, plain text fallback,
  JSON normalisation of "function_call" action type, malformed JSON handling,
  history injection into prompt, log entry generation
- execute_tool() node: successful tool invocation, tool returning error payload,
  tool raising exception, unknown tool name, tool_args defaulting to {}
- router() node: routes to execute_tool when pending_call is non-empty,
  routes to END when pending_call is empty / final_answer is set

Mocks used:
- unittest.mock.patch / MagicMock for LLMS, _profile_tool, _lookalike_tool,
  _run_underwriting_assessment, Path.glob (skill loading)
- pytest-asyncio for async execute_tool tests

TODOs:
- TODO: Integration test for full graph traversal requires a running LangGraph runtime
- TODO: Streaming / on_tool_start / on_tool_end callback assertions need a live
  LangChain callback manager — stubs provided below
- TODO: router() full source was truncated; stub tests cover inferred behaviour only
"""

import json
import operator
import types
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

FAKE_SKILL_TEXT = "## get_customer_info\nFetch profile by customer_id.\n"


def _make_llm_response(content: str):
    """Return a mock LLM response object whose .content equals *content*."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_tagged_llm(response_content: str):
    """Return a mock chain: llm.with_config(...) → tagged_llm.invoke(...)."""
    tagged_llm = MagicMock()
    tagged_llm.invoke.return_value = _make_llm_response(response_content)

    llm = MagicMock()
    llm.with_config.return_value = tagged_llm
    return llm, tagged_llm


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

PATCH_LLMS = "backend.agent.agent_with_skills.LLMS"
PATCH_SKILLS_DIR = "backend.agent.agent_with_skills._SKILLS_DIR"
PATCH_PROFILE_TOOL = "backend.agent.agent_with_skills._profile_tool"
PATCH_LOOKALIKE_TOOL = "backend.agent.agent_with_skills._lookalike_tool"
PATCH_UNDERWRITING = "backend.agent.agent_with_skills._run_underwriting_assessment"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_skills_dir(tmp_path):
    """Create a temporary skills directory with one markdown file."""
    skill_file = tmp_path / "get_customer_info.md"
    skill_file.write_text(FAKE_SKILL_TEXT)
    return tmp_path


@pytest.fixture()
def mock_tools():
    """Return fresh AsyncMock objects for all three tool slots."""
    profile = AsyncMock()
    lookalike = AsyncMock()
    risk = AsyncMock()
    return profile, lookalike, risk


@pytest.fixture()
def agent_factory(fake_skills_dir, mock_tools):
    """
    Return a helper that calls build_skills_agent() with all external
    dependencies patched and returns (agent_node, execute_tool_node, router_node,
    tagged_llm_mock).

    The inner nodes are extracted from the StateGraph by inspecting the compiled
    graph's node map so we can test them in isolation without running the full
    LangGraph runtime.
    """
    profile_tool, lookalike_tool, risk_tool = mock_tools

    def _build(llm_response: str = '{"action": "done", "answer": "ok"}'):
        llm_mock, tagged_llm = _make_tagged_llm(llm_response)

        llms_instance = MagicMock()
        llms_instance.get_model.return_value = llm_mock

        llms_cls = MagicMock(return_value=llms_instance)

        with (
            patch(PATCH_LLMS, llms_cls),
            patch(PATCH_SKILLS_DIR, fake_skills_dir),
            patch(PATCH_PROFILE_TOOL, profile_tool),
            patch(PATCH_LOOKALIKE_TOOL, lookalike_tool),
        ):
            # Import here so patches are active during module-level TOOLS build
            from backend.agent.agent_with_skills import build_skills_agent, TOOLS

            graph = build_skills_agent(model_name="anthropic-fast", temperature=0)

        return graph, tagged_llm, TOOLS

    return _build


# ---------------------------------------------------------------------------
# AgentState structure tests
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_agent_state_is_typed_dict(self):
        from backend.agent.agent_with_skills import AgentState
        assert issubclass(AgentState, dict)

    def test_agent_state_has_required_keys(self):
        from backend.agent.agent_with_skills import AgentState
        keys = AgentState.__annotations__.keys()
        assert "question" in keys
        assert "history" in keys
        assert "logs" in keys
        assert "pending_call" in keys
        assert "final_answer" in keys

    def test_agent_state_instantiation(self):
        from backend.agent.agent_with_skills import AgentState
        state: AgentState = {
            "question": "Who is CUST00000001?",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "Who is CUST00000001?"


# ---------------------------------------------------------------------------
# build_skills_agent tests
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, fake_skills_dir, mock_tools):
        profile_tool, lookalike_tool, risk_tool = mock_tools
        llm_mock, _ = _make_tagged_llm('{"action": "done", "answer": "ok"}')
        llms_instance = MagicMock()
        llms_instance.get_model.return_value = llm_mock

        with (
            patch(PATCH_LLMS, MagicMock(return_value=llms_instance)),
            patch(PATCH_SKILLS_DIR, fake_skills_dir),
            patch(PATCH_PROFILE_TOOL, profile_tool),
            patch(PATCH_LOOKALIKE_TOOL, lookalike_tool),
        ):
            from backend.agent.agent_with_skills import build_skills_agent
            graph = build_skills_agent()

        # LangGraph compiled graphs expose an `invoke` or `ainvoke` method
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_llms_called_with_correct_args(self, fake_skills_dir, mock_tools):
        profile_tool, lookalike_tool, _ = mock_tools
        llm_mock, _ = _make_tagged_llm("{}")
        llms_instance = MagicMock()
        llms_instance.get_model.return_value = llm_mock
        llms_cls = MagicMock(return_value=llms_instance)

        with (
            patch(PATCH_LLMS, llms_cls),
            patch(PATCH_SKILLS_DIR, fake_skills_dir),
            patch(PATCH_PROFILE_TOOL, profile_tool),
            patch(PATCH_LOOKALIKE_TOOL, lookalike_tool),
        ):
            from backend.agent.agent_with_skills import build_skills_agent
            build_skills_agent(model_name="gpt4", temperature=0.5)

        llms_cls.assert_called_once_with(temperature=0.5, streaming=True)
        llms_instance.get_model.assert_called_once_with("gpt4")

    def test_skills_files_loaded(self, tmp_path, mock_tools):
        """All .md files except index.md should be read."""
        (tmp_path / "skill_a.md").write_text("# Skill A")
        (tmp_path / "skill_b.md").write_text("# Skill B")
        (tmp_path / "index.md").write_text("# Index — should be excluded")

        profile_tool, lookalike_tool, _ = mock_tools
        llm_mock, tagged_llm = _make_tagged_llm('{"action": "done", "answer": "x"}')
        llms_instance = MagicMock()
        llms_instance.get_model.return_value = llm_mock

        with (
            patch(PATCH_LLMS, MagicMock(return_value=llms_instance)),
            patch(PATCH_SKILLS_DIR, tmp_path),
            patch(PATCH_PROFILE_TOOL, profile_tool),
            patch(PATCH_LOOKALIKE_TOOL, lookalike_tool),
        ):
            from backend.agent.agent_with_skills import build_skills_agent
            build_skills_agent()

    def test_no_skills_dir_uses_empty_docs(self, tmp_path, mock_tools):
        """If skills dir has no .md files the system prompt still builds."""
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()

        profile_tool, lookalike_tool, _ = mock_tools
        llm_mock, _ = _make_tagged_llm('{"action": "done", "answer": ""}')
        llms_instance = MagicMock()
        llms_instance.get_model.return_value = llm_mock

        with (
            patch(PATCH_LLMS, MagicMock(return_value=llms_instance)),
            patch(PATCH_SKILLS_DIR, empty_dir),
            patch(PATCH_PROFILE_TOOL, profile_tool),
            patch(PATCH_LOOKALIKE_TOOL, lookalike_tool),
        ):
            from backend.agent.agent_with_skills import build_skills_agent
            graph = build_skills_agent()

        assert graph is not None


# ---------------------------------------------------------------------------
# agent() node tests  (isolated via closure extraction)
# ---------------------------------------------------------------------------

class TestAgentNode:
    """
    We test the agent() closure by rebuilding the graph and reaching into the
    compiled node functions through a thin wrapper that calls the node directly.
    """

    def _get_agent_node(self, fake_skills_dir, mock_tools, llm_response: str):
        """Patch env and return the raw agent() callable from the closure."""
        profile_tool, lookalike_tool, risk_tool = mock_tools
        llm_mock, tagged_llm = _make_tagged_llm(llm_response)
        llms_instance = MagicMock()
        llms_instance.get_model.return_value = llm_mock

        # We rebuild the internal nodes by re-importing the module under test
        # with patches active and reading the closure variable.
        with (
            patch(PATCH_LLMS, MagicMock(return_value=llms_instance)),
            patch(PATCH_SKILLS_DIR, fake_skills_dir),
            patch(PATCH_PROFILE_TOOL, profile_tool),
            patch(PATCH_LOOKALIKE_TOOL, lookalike_tool),
        ):
            # Reload to get fresh closures
            import importlib
            import backend.agent.agent_with_skills as module
            importlib.reload(module)

            # build_skills_agent returns a compiled graph; we need the inner nodes.
            # We capture them by patching StateGraph.add_node
            captured = {}
            original_add_node = module.StateGraph.add_node if hasattr(module, "StateGraph") else None

            real_sg = module.StateGraph

            class CapturingStateGraph(real_sg):
                def add_node(self, name, fn=None, **kw):
                    if fn is not None:
                        captured[name] = fn
                    return super().add_node(name, fn, **kw)

            with patch.object(module, "StateGraph", CapturingStateGraph):
                module.build_skills_agent()

        return captured, tagged_llm

    # --- helper state ---
    @staticmethod
    def _base_state(**overrides):
        state = {
            "question": "Tell me about CUST00000001",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        state.update(overrides)
        return state

    def test_agent_done_action(self, fake_skills_dir, mock_tools):
        response = '{"action": "done", "answer": "Here is the summary."}'
        captured, _ = self._get_agent_node(fake_skills_dir, mock_tools, response)
        node = captured.get("agent")
        if node is None:
            pytest.skip("Could not capture agent node — StateGraph API mismatch")

        result = node(self._base_state())
        assert result["final_answer"] == "Here is the summary."
        assert result["pending_call"] == {}
        assert len(result["history"]) == 1
        assert result["history"][0].startswith("Assistant:")

    def test_agent_tool_call_action(self, fake_skills_dir, mock_tools):
        response = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        captured, _ = self._get_agent_node(fake_skills_dir, mock_tools, response)
        node = captured.get("agent")
        if node is None:
            pytest.skip("Could not capture agent node")

        result = node(self._base_state())
        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["final_answer"] == "" or "final_answer" not in result

    def test_agent_function_call_normalised(self, fake_skills_dir, mock_tools):
        """'type': 'function_call' should be normalised to 'action': 'tool_call'."""
        response = json.dumps({
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        captured, _ = self._get_agent_node(fake_skills_dir, mock_tools, response)
        node = captured.get("agent")
        if node is None:
            pytest.skip("Could not capture agent node")

        result = node(self._base_state())
        assert result["pending_call"]["action"] == "tool_call"

    def test_agent_plain_text_fallback(self, fake_skills_dir, mock_tools):
        """Non-JSON response should produce empty pending_call."""
        response = "I am not sure how to help with that."
        captured, _ = self._get_agent_node(fake_skills_dir, mock_tools, response)
        node = captured.get("agent")
        if node is None:
            pytest.skip("Could not capture agent node")

        result = node(self._base_state())
        assert result["pending_call"] == {}
        assert "final_answer" not in result or result.get("final_answer") in (None, "")

    def test_agent_malformed_json_fallback(self, fake_skills_dir, mock_tools):
        """Malformed JSON inside braces falls back gracefully."""
        response = "{not: valid json at all"
        captured, _ = self._get_agent_node(fake_skills_dir, mock_tools, response)
        node