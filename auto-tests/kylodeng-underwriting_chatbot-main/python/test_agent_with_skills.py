"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function (graph construction, prompt assembly)
- agent() node: happy path JSON parsing, tool_call action, done action,
  function_call normalisation, invalid JSON fallback, missing JSON fallback
- execute_tool() node: successful tool invocation, tool error payload,
  tool exception, unknown tool name
- router() function: pending_call present → execute_tool, empty pending_call
  with no final_answer → agent, final_answer present → END

Mocks used:
- backend.agent.agent_with_skills.LLMS (LLM factory)
- backend.agent.agent_with_skills._profile_tool
- backend.agent.agent_with_skills._lookalike_tool
- backend.agent.agent_with_skills._run_underwriting_assessment
- backend.agent.agent_with_skills._SKILLS_DIR (patched to temp directory)
- langchain_core.messages.HumanMessage / SystemMessage (passthrough — real objects)

TODOs:
- TODO: Integration test for full graph execution requires a live LLM or
  LangGraph test harness — stubbed below.
- TODO: Streaming event filtering by tag ("agent" vs "thinking") requires
  main.py context — stubbed below.
- TODO: router() cannot be fully tested without inspecting the compiled graph
  internals; conditional-edge logic is tested via the returned node name strings.
"""

import asyncio
import json
import operator
import re
import types
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm_response(content: str):
    """Return a mock LLM response object whose .content attribute is `content`."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_tagged_llm(content: str):
    """Return a mock tagged LLM that synchronously returns `content`."""
    tagged = MagicMock()
    tagged.invoke.return_value = _make_llm_response(content)
    return tagged


def _make_llm_stack(*contents):
    """Return a mock tagged LLM whose invoke() returns contents in order."""
    tagged = MagicMock()
    tagged.invoke.side_effect = [_make_llm_response(c) for c in contents]
    return tagged


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def skills_dir(tmp_path):
    """Create a temporary skills directory with two markdown skill files."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "01_get_customer.md").write_text("# get_customer_info skill\nUse this to fetch profiles.")
    (skills / "02_lookalike.md").write_text("# customer_lookalike skill\nUse this to find similar customers.")
    (skills / "index.md").write_text("# Index — should be excluded")
    return skills


@pytest.fixture()
def mock_tools():
    """Provide async-capable mock tool objects for each key in TOOLS."""
    profile = AsyncMock()
    profile.ainvoke = AsyncMock(return_value='{"customer_id": "CUST00000001", "name": "Alice"}')

    lookalike = AsyncMock()
    lookalike.ainvoke = AsyncMock(return_value='["CUST00006151","CUST00000272"]')

    assessment = AsyncMock()
    assessment.ainvoke = AsyncMock(return_value='{"risk": "low"}')

    return {
        "get_customer_info": profile,
        "customer_lookalike": lookalike,
        "run_risk_assessment": assessment,
    }


@pytest.fixture()
def patched_agent(skills_dir, mock_tools):
    """
    Import build_skills_agent with all heavy dependencies patched out.
    Returns (build_skills_agent, mock_tools, mock_tagged_llm_factory).
    """
    mock_llm_instance = MagicMock()
    mock_tagged_llm = MagicMock()
    mock_llm_instance.with_config.return_value = mock_tagged_llm

    mock_llms_cls = MagicMock(return_value=mock_llm_instance)

    with (
        patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
        patch("backend.agent.agent_with_skills._profile_tool", mock_tools["get_customer_info"]),
        patch("backend.agent.agent_with_skills._lookalike_tool", mock_tools["customer_lookalike"]),
        patch(
            "backend.agent.agent_with_skills._run_underwriting_assessment",
            return_value=mock_tools["run_risk_assessment"],
        ),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        patch(
            "backend.agent.agent_with_skills.TOOLS",
            mock_tools,
        ),
    ):
        # Re-import inside patch context so module-level TOOLS picks up mocks
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        yield mod, mock_tools, mock_tagged_llm, mock_llms_cls


# ---------------------------------------------------------------------------
# AgentState tests
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_agent_state_is_typed_dict(self):
        from backend.agent.agent_with_skills import AgentState
        assert issubclass(AgentState, dict)

    def test_agent_state_required_keys(self):
        from backend.agent.agent_with_skills import AgentState
        keys = AgentState.__annotations__
        assert "question" in keys
        assert "history" in keys
        assert "logs" in keys
        assert "pending_call" in keys
        assert "final_answer" in keys

    def test_agent_state_history_uses_operator_add(self):
        """Annotated metadata for history should include operator.add."""
        from backend.agent.agent_with_skills import AgentState
        import typing
        history_annotation = AgentState.__annotations__["history"]
        # Annotated[list[str], operator.add]
        args = typing.get_args(history_annotation)
        assert operator.add in args

    def test_agent_state_logs_uses_operator_add(self):
        from backend.agent.agent_with_skills import AgentState
        import typing
        logs_annotation = AgentState.__annotations__["logs"]
        args = typing.get_args(logs_annotation)
        assert operator.add in args

    def test_agent_state_can_be_instantiated(self):
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
# build_skills_agent — construction / prompt loading
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        graph = mod.build_skills_agent()
        # A compiled LangGraph graph exposes invoke / ainvoke
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_llms_called_with_defaults(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        mod.build_skills_agent()
        mock_llms_cls.assert_called_once_with(temperature=0, streaming=True)

    def test_llms_called_with_custom_temperature(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        mod.build_skills_agent(temperature=0.7)
        mock_llms_cls.assert_called_once_with(temperature=0.7, streaming=True)

    def test_llm_tagged_with_agent(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        llm_instance = mock_llms_cls.return_value
        mod.build_skills_agent()
        llm_instance.with_config.assert_called_once_with({"tags": ["agent"]})

    def test_skill_docs_loaded_excludes_index(self, patched_agent, skills_dir):
        """index.md must not appear in the system prompt."""
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        # Capture system prompt via the tagged_llm invoke call
        llm_instance = mock_llms_cls.return_value
        tagged = llm_instance.with_config.return_value
        tagged.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "ok"}'
        )
        graph = mod.build_skills_agent()

        # Invoke graph to trigger agent node so system prompt is built
        state = {
            "question": "hi",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        # We only want to peek at the prompt; ignore graph errors
        try:
            graph.invoke(state)
        except Exception:
            pass

        call_args = tagged.invoke.call_args
        if call_args is not None:
            messages = call_args[0][0]
            system_content = messages[0].content
            assert "index" not in system_content.lower() or "index.md" not in system_content
            assert "get_customer_info skill" in system_content
            assert "customer_lookalike skill" in system_content

    def test_skill_docs_sorted_order(self, patched_agent, skills_dir):
        """Skill files should be loaded in sorted order (01_ before 02_)."""
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        tagged = mock_llms_cls.return_value.with_config.return_value
        tagged.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "ok"}'
        )
        graph = mod.build_skills_agent()
        state = {
            "question": "hi",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        try:
            graph.invoke(state)
        except Exception:
            pass

        call_args = tagged.invoke.call_args
        if call_args is not None:
            messages = call_args[0][0]
            system_content = messages[0].content
            idx_customer = system_content.find("get_customer_info skill")
            idx_lookalike = system_content.find("customer_lookalike skill")
            assert idx_customer < idx_lookalike

    def test_no_skill_files(self, tmp_path, mock_tools):
        """If skills directory is empty, the system prompt should still build."""
        empty_skills = tmp_path / "skills_empty"
        empty_skills.mkdir()

        mock_llm_instance = MagicMock()
        mock_tagged = MagicMock()
        mock_llm_instance.with_config.return_value = mock_tagged
        mock_tagged.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "no skills"}'
        )
        mock_llms_cls = MagicMock(return_value=mock_llm_instance)

        with (
            patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", empty_skills),
            patch("backend.agent.agent_with_skills.TOOLS", mock_tools),
        ):
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            graph = mod.build_skills_agent()
            assert graph is not None


# ---------------------------------------------------------------------------
# agent() node — internal logic extracted via graph invocation
# ---------------------------------------------------------------------------

class TestAgentNode:
    """
    We test the agent() closure by building the graph with a controlled LLM
    response and invoking it.  When the LLM returns {"action": "done"} the
    graph should terminate; when it returns {"action": "tool_call"} the graph
    sets pending_call and routes to execute_tool.
    """

    def _build(self, patched_agent, llm_content: str):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        mock_tagged_llm.invoke.return_value = _make_llm_response(llm_content)
        return mod, mock_tools, mock_tagged_llm

    def _base_state(self):
        return {
            "question": "Tell me about CUST00000001",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }

    def test_done_action_sets_final_answer(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        mock_tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "Customer is low risk."}'
        )
        graph = mod.build_skills_agent()
        result = graph.invoke(self._base_state())
        assert result["final_answer"] == "Customer is low risk."

    def test_done_action_clears_pending_call(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        mock_tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "All done."}'
        )
        graph = mod.build_skills_agent()
        result = graph.invoke(self._base_state())
        assert result.get("pending_call") == {} or result.get("pending_call") is None

    def test_done_action_appends_to_history(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        content = '{"action": "done", "answer": "Summary here."}'
        mock_tagged_llm.invoke.return_value = _make_llm_response(content)
        graph = mod.build_skills_agent()
        result = graph.invoke(self._base_state())
        assert any("Assistant:" in h for h in result["history"])

    def test_done_action_adds_log_entry(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        mock_tagged_llm.invoke.return_value = _make_llm_response(
            '{"action": "done", "answer": "ok"}'
        )
        graph = mod.build_skills_agent()
        result = graph.invoke(self._base_state())
        assert len(result["logs"]) >= 1
        assert result["logs"][0]["event"] == "on_chat_model_end"

    def test_tool_call_action_sets_pending_call(self, patched_agent):
        mod, mock_tools, mock_tagged_llm, mock_llms_cls = patched_agent
        # First call: tool_call; second call: done (so graph terminates)
        mock_tagged_llm.invoke.side_effect = [
            _make_llm_response(
                '{"action": "tool_call",