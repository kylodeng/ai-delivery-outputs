```python
"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field types
- build_skills_agent: agent node (happy path, tool_call action, done action, fallback)
- build_skills_agent: execute_tool node (happy path, tool not found, tool raises exception,
  tool returns error payload, non-string result)
- JSON parsing in agent node: regex extraction, normalisation of "function_call" → "tool_call"
- Edge cases: empty content, malformed JSON, missing keys in parsed response

Mocks used:
- backend.agent.agent_with_skills.LLMS (to avoid real LLM initialisation)
- backend.agent.agent_with_skills._profile_tool (LangChain @tool stub)
- backend.agent.agent_with_skills._lookalike_tool (LangChain @tool stub)
- backend.agent.agent_with_skills._run_underwriting_assessment (assessment stub)
- backend.agent.agent_with_skills._SKILLS_DIR (patched to a tmp directory)
- TOOLS dict entries replaced with AsyncMock stubs in execute_tool tests

TODOs:
- TODO: Integration test for the full StateGraph compiled and invoked end-to-end
  (requires real or fully-wired LangGraph runtime).
- TODO: Test router node once the truncated source code is available (source cut off).
- TODO: Test streaming behaviour / on_tool_start / on_tool_end callback firing via
  LangChain callback system (needs LangChain test harness).
- TODO: Test skill_docs loading with actual .md fixture files to verify prompt injection.
"""

import json
import operator
import types
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal AgentState dict
# ---------------------------------------------------------------------------

def make_state(
    question="Tell me about customer CUST00000001",
    history=None,
    logs=None,
    pending_call=None,
    final_answer="",
):
    return {
        "question": question,
        "history": history if history is not None else [],
        "logs": logs if logs is not None else [],
        "pending_call": pending_call if pending_call is not None else {},
        "final_answer": final_answer,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_llm_instance():
    """A fully mocked LLM instance returned by LLMS().get_model()."""
    llm = MagicMock()
    tagged = MagicMock()
    llm.with_config.return_value = tagged
    return llm, tagged


@pytest.fixture()
def mock_skills_dir(tmp_path):
    """Creates a temporary skills directory with one .md file."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "skill_01.md").write_text("# Skill 1\nGet customer profile.")
    (skills / "index.md").write_text("# Index\nShould be excluded.")
    return skills


@pytest.fixture()
def patched_agent_module(mock_llm_instance, mock_skills_dir):
    """
    Patches all heavy external dependencies so build_skills_agent() can be
    called without real services.
    Returns (build_skills_agent, tagged_llm_mock, tool_mocks).
    """
    llm_obj, tagged_llm = mock_llm_instance

    mock_profile_tool = AsyncMock()
    mock_lookalike_tool = AsyncMock()
    mock_risk_tool = AsyncMock()

    mock_llms_cls = MagicMock()
    mock_llms_cls.return_value.get_model.return_value = llm_obj

    # Patch at the module level inside agent_with_skills
    with (
        patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
        patch("backend.agent.agent_with_skills._profile_tool", mock_profile_tool),
        patch("backend.agent.agent_with_skills._lookalike_tool", mock_lookalike_tool),
        patch("backend.agent.agent_with_skills._run_underwriting_assessment", return_value=mock_risk_tool),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", mock_skills_dir),
        patch("backend.agent.agent_with_skills.TOOLS", {
            "get_customer_info": mock_profile_tool,
            "customer_lookalike": mock_lookalike_tool,
            "run_risk_assessment": mock_risk_tool,
        }),
    ):
        from backend.agent.agent_with_skills import build_skills_agent
        yield build_skills_agent, tagged_llm, {
            "get_customer_info": mock_profile_tool,
            "customer_lookalike": mock_lookalike_tool,
            "run_risk_assessment": mock_risk_tool,
        }


# ---------------------------------------------------------------------------
# AgentState structure tests
# ---------------------------------------------------------------------------

class TestAgentStateStructure:
    def test_minimal_state_construction(self):
        state = make_state()
        assert state["question"] == "Tell me about customer CUST00000001"
        assert isinstance(state["history"], list)
        assert isinstance(state["logs"], list)
        assert isinstance(state["pending_call"], dict)
        assert state["final_answer"] == ""

    def test_history_is_list_of_strings(self):
        state = make_state(history=["user: hello", "assistant: hi"])
        assert len(state["history"]) == 2
        assert all(isinstance(h, str) for h in state["history"])

    def test_logs_is_list_of_dicts(self):
        state = make_state(logs=[{"event": "on_tool_start", "name": "foo", "data": {}}])
        assert len(state["logs"]) == 1
        assert isinstance(state["logs"][0], dict)


# ---------------------------------------------------------------------------
# build_skills_agent – agent node tests
# ---------------------------------------------------------------------------

class TestAgentNode:
    """Tests for the inner `agent(state)` closure built by build_skills_agent."""

    def _build(self, patched_agent_module, llm_response_text):
        build_skills_agent, tagged_llm, tools = patched_agent_module
        # Configure what the LLM returns
        response_mock = MagicMock()
        response_mock.content = llm_response_text
        tagged_llm.invoke.return_value = response_mock
        agent_fn = self._extract_agent_node(build_skills_agent)
        return agent_fn, tagged_llm, tools

    def _extract_agent_node(self, build_skills_agent):
        """
        build_skills_agent returns a compiled StateGraph.  We reach into
        the closure by calling the function under test and inspecting nodes,
        OR we re-implement by calling a known internal. For now we monkey-
        patch StateGraph to capture the node callables.
        """
        # We'll collect nodes via a StateGraph spy
        added_nodes = {}

        class SpyStateGraph:
            def __init__(self, schema):
                pass

            def add_node(self, name, fn):
                added_nodes[name] = fn

            def add_edge(self, *args):
                pass

            def add_conditional_edges(self, *args, **kwargs):
                pass

            def compile(self):
                return MagicMock()

        with patch("backend.agent.agent_with_skills.StateGraph", SpyStateGraph), \
             patch("backend.agent.agent_with_skills.START", "START"):
            build_skills_agent()

        return added_nodes.get("agent")

    # ------------------------------------------------------------------ #

    def test_tool_call_action_returns_pending_call(self, patched_agent_module):
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        state = make_state()
        result = agent_fn(state)

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert len(result["history"]) == 1
        assert "Assistant:" in result["history"][0]
        assert len(result["logs"]) == 1
        assert result["logs"][0]["event"] == "on_chat_model_end"

    def test_function_call_normalised_to_tool_call(self, patched_agent_module):
        payload = json.dumps({
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        result = agent_fn(make_state())

        assert result["pending_call"]["action"] == "tool_call"

    def test_done_action_sets_final_answer(self, patched_agent_module):
        payload = json.dumps({
            "action": "done",
            "answer": "The customer risk is low.",
        })
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        result = agent_fn(make_state())

        assert result["final_answer"] == "The customer risk is low."
        assert result["pending_call"] == {}

    def test_done_action_missing_answer_key(self, patched_agent_module):
        payload = json.dumps({"action": "done"})
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        result = agent_fn(make_state())

        assert result["final_answer"] == ""
        assert result["pending_call"] == {}

    def test_malformed_json_falls_through_to_plain_response(self, patched_agent_module):
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, "This is just plain text.")
        result = agent_fn(make_state())

        assert result["pending_call"] == {}
        assert "Assistant: This is just plain text." in result["history"]

    def test_partial_json_in_prose_is_extracted(self, patched_agent_module):
        content = 'Here is my action: {"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}} — done.'
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, content)
        result = agent_fn(make_state())

        assert result["pending_call"]["action"] == "tool_call"

    def test_empty_content_fallback(self, patched_agent_module):
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, "   ")
        result = agent_fn(make_state())

        assert result["pending_call"] == {}

    def test_json_with_unknown_action_falls_through(self, patched_agent_module):
        payload = json.dumps({"action": "unknown_action", "data": "something"})
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        result = agent_fn(make_state())

        assert result["pending_call"] == {}

    def test_history_appended_on_every_call(self, patched_agent_module):
        payload = json.dumps({"action": "done", "answer": "ok"})
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        result = agent_fn(make_state(history=["User: hello"]))

        assert len(result["history"]) == 1  # only the new entry is returned; operator.add merges
        assert result["history"][0].startswith("Assistant:")

    def test_llm_invoked_with_system_and_human_message(self, patched_agent_module):
        payload = json.dumps({"action": "done", "answer": "ok"})
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        agent_fn(make_state(question="What is the risk?"))

        tagged_llm.invoke.assert_called_once()
        call_args = tagged_llm.invoke.call_args[0][0]
        # First message should be SystemMessage, second HumanMessage
        from langchain_core.messages import SystemMessage, HumanMessage
        assert any(isinstance(m, SystemMessage) for m in call_args)
        assert any(isinstance(m, HumanMessage) for m in call_args)

    def test_history_block_injected_into_system_prompt(self, patched_agent_module):
        payload = json.dumps({"action": "done", "answer": "ok"})
        agent_fn, tagged_llm, _ = self._build(patched_agent_module, payload)
        from langchain_core.messages import SystemMessage
        agent_fn(make_state(history=["User: prior turn"]))

        call_args = tagged_llm.invoke.call_args[0][0]
        system_msg = next(m for m in call_args if isinstance(m, SystemMessage))
        assert "Conversation History" in system_msg.content
        assert "User: prior turn" in system_msg.content


# ---------------------------------------------------------------------------
# build_skills_agent – execute_tool node tests
# ---------------------------------------------------------------------------

class TestExecuteToolNode:
    """Tests for the inner `execute_tool(state)` async closure."""

    def _extract_execute_tool_node(self, build_skills_agent):
        added_nodes = {}

        class SpyStateGraph:
            def __init__(self, schema):
                pass

            def add_node(self, name, fn):
                added_nodes[name] = fn

            def add_edge(self, *args):
                pass

            def add_conditional_edges(self, *args, **kwargs):
                pass

            def compile(self):
                return MagicMock()

        with patch("backend.agent.agent_with_skills.StateGraph", SpyStateGraph), \
             patch("backend.agent.agent_with_skills.START", "START"):
            build_skills_agent()

        return added_nodes.get("execute_tool")

    @pytest.mark.asyncio
    async def test_happy_path_returns_result_in_history(self, patched_agent_module):
        build_skills_agent, _, tools = patched_agent_module
        tools["get_customer_info"].ainvoke = AsyncMock(return_value='{"name": "Alice", "risk": "low"}')

        execute_tool = self._extract_execute_tool_node(build_skills_agent)
        state = make_state(pending_call={
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        result = await execute_tool(state)

        assert any("get_customer_info result" in h for h in result["history"])
        assert result["pending_call"] == {}

    @pytest.mark.asyncio
    async def test_tool_not_found_returns_error_message(self, patched_agent_module):
        build_skills_agent, _, tools = patched_agent_module

        execute_tool = self._extract_execute_tool_node(build_skills_agent)
        state = make_state(pending_call={
            "action": "tool_call",
            "tool_name": "nonexistent_tool",
            "tool_args": {},
        })
        result = await execute_tool(state)

        assert any("something went wrong" in h for h in result["history"])
        assert result["pending_call"] == {}

    @pytest.mark.asyncio
    async def test_tool_raises_exception_returns_error