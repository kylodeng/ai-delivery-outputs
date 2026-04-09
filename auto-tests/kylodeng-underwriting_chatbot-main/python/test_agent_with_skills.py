"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field behaviour
- build_skills_agent() factory function and the closures it returns:
  * agent() node – JSON parsing, action routing, history/log accumulation
  * execute_tool() node – happy path, tool error, unknown tool, JSON error payload
  * router() node – routing between agent and execute_tool nodes
- TOOLS registry presence
- Skill-doc loading logic (Path.glob)

Mocks used:
- backend.agent.agent_with_skills.LLMS  (LLM factory)
- backend.agent.agent_with_skills._profile_tool
- backend.agent.agent_with_skills._lookalike_tool
- backend.agent.agent_with_skills._run_underwriting_assessment
- backend.agent.agent_with_skills._SKILLS_DIR  (patched via monkeypatch)
- unittest.mock.AsyncMock for tool .ainvoke() calls

TODOs:
- TODO: Full StateGraph compilation test needs a running LangGraph runtime – stub provided
- TODO: LangChain streaming / on_tool_start / on_tool_end callback integration test
- TODO: Test tag propagation ("agent" tag) via real LangChain tracing callbacks
"""

import asyncio
import json
import operator
import types
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_llm_response(content: str):
    """Return a mock object that behaves like a LangChain chat response."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_tagged_llm(invoke_return: str):
    """Build the chain: llm.with_config() → tagged_llm that returns invoke_return."""
    tagged_llm = MagicMock()
    tagged_llm.invoke.return_value = _make_fake_llm_response(invoke_return)

    llm = MagicMock()
    llm.with_config.return_value = tagged_llm

    return llm, tagged_llm


def _make_mock_llms_class(llm_mock):
    """Return a mock LLMS class whose constructor yields llm_mock from .get_model()."""
    llms_instance = MagicMock()
    llms_instance.get_model.return_value = llm_mock

    MockLLMS = MagicMock(return_value=llms_instance)
    return MockLLMS


def _make_skills_dir(tmp_path: Path, docs: dict[str, str] | None = None) -> Path:
    """
    Create a fake skills directory with .md files.
    docs = {filename: content}
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    if docs is None:
        docs = {
            "a_skill.md": "# Skill A\nHow to call get_customer_info.",
            "b_skill.md": "# Skill B\nHow to call customer_lookalike.",
        }
    for name, content in docs.items():
        (skills_dir / name).write_text(content)
    return skills_dir


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_tool():
    """A generic async-capable mock tool."""
    t = MagicMock()
    t.ainvoke = AsyncMock(return_value='{"status": "ok"}')
    return t


@pytest.fixture()
def base_state() -> dict:
    return {
        "question": "Tell me about customer CUST00000001",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }


# ---------------------------------------------------------------------------
# Utility: build the agent under test with full mocking
# ---------------------------------------------------------------------------

def _build_agent(tmp_path: Path, llm_response: str, mock_profile=None, mock_lookalike=None):
    """
    Patch all external dependencies, build the agent graph closures and return
    (agent_fn, execute_tool_fn, router_fn, tagged_llm_mock).
    """
    skills_dir = _make_skills_dir(tmp_path)

    llm, tagged_llm = _make_tagged_llm(llm_response)
    MockLLMS = _make_mock_llms_class(llm)

    profile_tool = mock_profile or MagicMock()
    profile_tool.ainvoke = AsyncMock(return_value='{"name": "John Doe"}')

    lookalike_tool = mock_lookalike or MagicMock()
    lookalike_tool.ainvoke = AsyncMock(return_value='["CUST00006151","CUST00000272"]')

    risk_assessment_tool = MagicMock()
    risk_assessment_tool.ainvoke = AsyncMock(return_value='{"risk": "low"}')

    mock_run_underwriting = MagicMock(return_value=risk_assessment_tool)

    with (
        patch("backend.agent.agent_with_skills.LLMS", MockLLMS),
        patch("backend.agent.agent_with_skills._profile_tool", profile_tool),
        patch("backend.agent.agent_with_skills._lookalike_tool", lookalike_tool),
        patch("backend.agent.agent_with_skills._run_underwriting_assessment", mock_run_underwriting),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
    ):
        # We import inside the patch context so the module-level TOOLS picks up mocks
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        graph_or_closures = mod.build_skills_agent()

    # After reload the module's closures are captured; return them for inspection
    return mod, tagged_llm, profile_tool, lookalike_tool, risk_assessment_tool


# ---------------------------------------------------------------------------
# NOTE: Because build_skills_agent creates nested closures and registers them
# into a StateGraph, we test the closures by extracting them through a thin
# wrapper approach rather than running the full graph compile step.
# ---------------------------------------------------------------------------

class TestAgentClosureDirectly:
    """
    Tests that directly exercise the `agent` inner closure by constructing it
    independently of LangGraph's compile step.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.tmp_path = tmp_path

    # ------------------------------------------------------------------
    # agent() – happy paths
    # ------------------------------------------------------------------

    def _run_agent_with_response(self, llm_response: str, state: dict):
        """
        Rebuild only the agent closure in a controlled way.
        """
        skills_dir = _make_skills_dir(self.tmp_path)
        llm, tagged_llm = _make_tagged_llm(llm_response)
        MockLLMS = _make_mock_llms_class(llm)

        profile_tool = MagicMock()
        profile_tool.ainvoke = AsyncMock(return_value='{"name": "John Doe"}')
        lookalike_tool = MagicMock()
        lookalike_tool.ainvoke = AsyncMock(return_value='["CUST00006151"]')
        risk_tool = MagicMock()
        risk_tool.ainvoke = AsyncMock(return_value='{"risk": "low"}')
        mock_run_underwriting = MagicMock(return_value=risk_tool)

        captured = {}

        original_add_node = None

        with (
            patch("backend.agent.agent_with_skills.LLMS", MockLLMS),
            patch("backend.agent.agent_with_skills._profile_tool", profile_tool),
            patch("backend.agent.agent_with_skills._lookalike_tool", lookalike_tool),
            patch("backend.agent.agent_with_skills._run_underwriting_assessment", mock_run_underwriting),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
            patch("backend.agent.agent_with_skills.StateGraph") as MockGraph,
        ):
            # Intercept add_node to capture closure references
            graph_instance = MagicMock()
            MockGraph.return_value = graph_instance

            def capture_add_node(name, fn):
                captured[name] = fn

            graph_instance.add_node.side_effect = capture_add_node
            graph_instance.add_edge = MagicMock()
            graph_instance.add_conditional_edges = MagicMock()
            graph_instance.compile.return_value = MagicMock()

            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            mod.build_skills_agent()

        return captured, tagged_llm

    # --- tool_call action ---

    def test_agent_returns_pending_call_on_tool_call_action(self, base_state):
        response = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        captured, tagged_llm = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["tool_args"] == {"customer_id": "CUST00000001"}
        assert len(result["history"]) == 1
        assert result["history"][0].startswith("Assistant:")
        assert len(result["logs"]) == 1

    def test_agent_normalises_function_call_type_to_tool_call(self, base_state):
        response = json.dumps({
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["pending_call"]["action"] == "tool_call"

    def test_agent_returns_final_answer_on_done_action(self, base_state):
        answer_text = "The customer profile has been retrieved successfully."
        response = json.dumps({
            "action": "done",
            "answer": answer_text,
        })
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["final_answer"] == answer_text
        assert result["pending_call"] == {}

    def test_agent_done_with_empty_answer_field(self, base_state):
        response = json.dumps({"action": "done"})
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["final_answer"] == ""
        assert result["pending_call"] == {}

    # --- non-JSON / malformed response ---

    def test_agent_handles_plain_text_response(self, base_state):
        response = "I cannot help with that request."
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["pending_call"] == {}
        assert len(result["history"]) == 1
        assert len(result["logs"]) == 1

    def test_agent_handles_malformed_json(self, base_state):
        response = '{"action": "tool_call", "tool_name": broken_json'
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["pending_call"] == {}

    def test_agent_handles_json_missing_action_key(self, base_state):
        response = json.dumps({"some_key": "some_value"})
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        # Neither tool_call nor done → falls through to plain text path
        assert result["pending_call"] == {}

    def test_agent_extracts_json_embedded_in_text(self, base_state):
        inner = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        response = f"Sure, here is what I will do:\n{inner}\nThat's the plan."
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["pending_call"]["action"] == "tool_call"

    # --- history injection ---

    def test_agent_includes_history_in_llm_call(self, base_state):
        response = json.dumps({"action": "done", "answer": "All done."})
        state_with_history = dict(base_state)
        state_with_history["history"] = [
            "Assistant: {\"action\": \"tool_call\", \"tool_name\": \"get_customer_info\", \"tool_args\": {}}",
            "Tool get_customer_info result: {\"name\": \"John\"}",
        ]
        captured, tagged_llm = self._run_agent_with_response(response, state_with_history)
        captured["agent"](state_with_history)

        call_args = tagged_llm.invoke.call_args
        messages = call_args[0][0]
        system_content = messages[0].content
        assert "Conversation History" in system_content

    def test_agent_appends_history_entry(self, base_state):
        response = json.dumps({"action": "done", "answer": "Done."})
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert any("Assistant:" in h for h in result["history"])

    def test_agent_log_entry_contains_agent_event(self, base_state):
        response = json.dumps({"action": "done", "answer": "ok"})
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["logs"][0]["event"] == "on_chat_model_end"
        assert result["logs"][0]["name"] == "agent"

    # --- whitespace handling ---

    def test_agent_strips_whitespace_from_llm_content(self, base_state):
        response = '  \n  {"action": "done", "answer": "trimmed"}  \n  '
        captured, _ = self._run_agent_with_response(response, base_state)
        result = captured["agent"](base_state)

        assert result["final_answer"] == "trimmed"


# ---------------------------------------------------------------------------
# execute_tool() closure tests
# ---------------------------------------------------------------------------

class TestExecuteToolClosure:
    """Tests for the async execute_tool inner closure."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.tmp_path = tmp_path

    def _capture_closures(self, profile_tool=None, lookalike_tool=None):
        skills_dir = _make_skills_dir(self.tmp_path)
        llm, tagged_llm = _make_tagged_llm("{}")
        MockLLMS = _make_mock_llms_class(llm)

        profile = profile_tool or MagicMock()
        if not hasattr(profile, 'ainvoke') or not callable(profile.ainvoke):
            profile.ainvoke = AsyncM