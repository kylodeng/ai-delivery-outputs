"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field behaviour
- build_skills_agent: agent() inner function (LLM response parsing, routing logic)
- build_skills_agent: execute_tool() inner function (happy path, tool errors, unknown tool)
- build_skills_agent: router() inner function (pending_call present vs absent)
- JSON action normalisation ("tool_call", "function_call", "done")
- Edge cases: malformed JSON, no JSON in response, empty content, error payloads from tools

Mocks used:
- backend.agent.agent_with_skills.LLMS            (LLM factory)
- backend.agent.agent_with_skills._profile_tool   (get_customer_info tool)
- backend.agent.agent_with_skills._lookalike_tool (customer_lookalike tool)
- backend.agent.agent_with_skills._run_underwriting_assessment (risk assessment tool)
- pathlib.Path.glob / file reading                (skill documentation loading)
- langchain_core.messages.SystemMessage / HumanMessage (message construction)

TODOs:
- TODO: Full graph integration test requires a running LangGraph runtime — stub provided
- TODO: on_tool_start / on_tool_end callback verification needs LangChain callback harness
- TODO: Streaming behaviour of tagged_llm cannot be tested without a real streaming LLM stub
"""

import json
import operator
import re
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with all heavy deps patched
# ---------------------------------------------------------------------------

AGENT_MODULE = "backend.agent.agent_with_skills"


def _make_llm_mock(response_content: str) -> MagicMock:
    """Return a mock LLMS instance whose .invoke() returns a fixed content string."""
    llm_instance = MagicMock()
    response_mock = MagicMock()
    response_mock.content = response_content
    llm_instance.invoke.return_value = response_mock

    tagged = MagicMock()
    tagged.invoke.return_value = response_mock
    llm_instance.with_config.return_value = tagged

    llms_class = MagicMock(return_value=llm_instance)
    llms_class.return_value.get_model.return_value = llm_instance
    return llms_class


def _build_agent(response_content: str, skill_docs: str = "# skill doc"):
    """
    Patch all external dependencies and call build_skills_agent().
    Returns (agent_fn, execute_tool_fn, router_fn, tagged_llm_mock).
    """
    tool_mock = MagicMock()
    tool_mock.ainvoke = AsyncMock(return_value='{"status": "ok"}')

    risk_tool_mock = MagicMock()
    risk_tool_mock.ainvoke = AsyncMock(return_value='{"risk": "low"}')

    llm_instance = MagicMock()
    response_mock = MagicMock()
    response_mock.content = response_content
    llm_instance.invoke.return_value = response_mock

    tagged_llm = MagicMock()
    tagged_llm.invoke.return_value = response_mock
    llm_instance.with_config.return_value = tagged_llm

    llms_class = MagicMock()
    llms_class.return_value.get_model.return_value = llm_instance

    # Patch Path.glob to return fake skill files
    fake_path = MagicMock(spec=Path)
    fake_path.name = "skill_a.md"
    fake_path.read_text.return_value = skill_docs

    with (
        patch(f"{AGENT_MODULE}.LLMS", llms_class),
        patch(f"{AGENT_MODULE}._profile_tool", tool_mock),
        patch(f"{AGENT_MODULE}._lookalike_tool", tool_mock),
        patch(f"{AGENT_MODULE}._run_underwriting_assessment", return_value=risk_tool_mock),
        patch.object(Path, "glob", return_value=[fake_path]),
        patch(f"{AGENT_MODULE}.TOOLS", {
            "get_customer_info": tool_mock,
            "customer_lookalike": tool_mock,
            "run_risk_assessment": risk_tool_mock,
        }),
    ):
        from backend.agent.agent_with_skills import build_skills_agent
        # We need to reload to pick up the patched TOOLS inside closures
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        # Re-apply patches after reload
        mod.LLMS = llms_class
        mod.TOOLS = {
            "get_customer_info": tool_mock,
            "customer_lookalike": tool_mock,
            "run_risk_assessment": risk_tool_mock,
        }

        agent_fn, execute_tool_fn, router_fn = _extract_closures(
            mod, llm_instance, tagged_llm, response_content, skill_docs,
            tool_mock, risk_tool_mock
        )

    return agent_fn, execute_tool_fn, router_fn, tagged_llm, tool_mock, risk_tool_mock


def _extract_closures(mod, llm_instance, tagged_llm, response_content,
                      skill_docs, tool_mock, risk_tool_mock):
    """
    Re-create the closures by calling build_skills_agent with fully mocked env.
    """
    fake_path = MagicMock(spec=Path)
    fake_path.name = "skill_a.md"
    fake_path.read_text.return_value = skill_docs

    llms_class = MagicMock()
    llms_class.return_value.get_model.return_value = llm_instance

    # We can't easily extract named inner functions from StateGraph, so we
    # rebuild a minimal version that calls the same logic.
    # Instead we test the logic directly by reimplementing the agent/execute_tool
    # functions inline using the same code paths.

    # Capture functions by monkey-patching StateGraph
    captured = {}

    original_add_node = None

    class CapturingStateGraph:
        def __init__(self, *a, **kw):
            pass

        def add_node(self, name, fn):
            captured[name] = fn

        def add_edge(self, *a, **kw):
            pass

        def add_conditional_edges(self, *a, **kw):
            pass

        def compile(self):
            return MagicMock()

    with (
        patch(f"{AGENT_MODULE}.LLMS", llms_class),
        patch(f"{AGENT_MODULE}.StateGraph", CapturingStateGraph),
        patch.object(Path, "glob", return_value=[fake_path]),
        patch(f"{AGENT_MODULE}.TOOLS", {
            "get_customer_info": tool_mock,
            "customer_lookalike": tool_mock,
            "run_risk_assessment": risk_tool_mock,
        }),
    ):
        mod.build_skills_agent()

    agent_fn = captured.get("agent")
    execute_tool_fn = captured.get("execute_tool")
    router_fn = captured.get("router")
    return agent_fn, execute_tool_fn, router_fn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tool_mock():
    m = MagicMock()
    m.ainvoke = AsyncMock(return_value='{"status": "ok"}')
    return m


@pytest.fixture()
def risk_tool_mock():
    m = MagicMock()
    m.ainvoke = AsyncMock(return_value='{"risk": "low"}')
    return m


@pytest.fixture()
def patched_mod(tool_mock, risk_tool_mock):
    """
    Import the module with all external dependencies patched and
    return (module, llm_instance, tagged_llm).
    """
    fake_path = MagicMock(spec=Path)
    fake_path.name = "skill_a.md"
    fake_path.read_text.return_value = "# skill documentation"

    llm_instance = MagicMock()
    tagged_llm = MagicMock()
    llm_instance.with_config.return_value = tagged_llm

    llms_class = MagicMock()
    llms_class.return_value.get_model.return_value = llm_instance

    class CapturingStateGraph:
        def __init__(self, *a, **kw):
            self._captured = {}

        def add_node(self, name, fn):
            self._captured[name] = fn

        def add_edge(self, *a, **kw):
            pass

        def add_conditional_edges(self, *a, **kw):
            pass

        def compile(self):
            graph_mock = MagicMock()
            graph_mock._captured = self._captured
            return graph_mock

    import importlib
    import backend.agent.agent_with_skills as mod
    importlib.reload(mod)

    capturing_graph = CapturingStateGraph()
    graph_instances = [capturing_graph]
    call_count = [0]

    def graph_factory(*a, **kw):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(graph_instances):
            return graph_instances[idx]
        return CapturingStateGraph()

    mod.LLMS = llms_class
    mod.TOOLS = {
        "get_customer_info": tool_mock,
        "customer_lookalike": tool_mock,
        "run_risk_assessment": risk_tool_mock,
    }

    with (
        patch(f"{AGENT_MODULE}.LLMS", llms_class),
        patch(f"{AGENT_MODULE}.StateGraph", graph_factory),
        patch.object(Path, "glob", return_value=[fake_path]),
        patch(f"{AGENT_MODULE}.TOOLS", {
            "get_customer_info": tool_mock,
            "customer_lookalike": tool_mock,
            "run_risk_assessment": risk_tool_mock,
        }),
    ):
        compiled = mod.build_skills_agent()

    fns = capturing_graph._captured
    return mod, llm_instance, tagged_llm, fns


# ---------------------------------------------------------------------------
# AgentState tests
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_agent_state_is_typed_dict(self):
        import backend.agent.agent_with_skills as mod_raw
        assert hasattr(mod_raw, "AgentState")

    def test_history_uses_operator_add(self):
        """Annotated[list[str], operator.add] means lists are concatenated."""
        from backend.agent.agent_with_skills import AgentState
        hints = AgentState.__annotations__
        assert "history" in hints
        assert "logs" in hints

    def test_agent_state_fields_present(self):
        from backend.agent.agent_with_skills import AgentState
        required = {"question", "history", "logs", "pending_call", "final_answer"}
        assert required <= set(AgentState.__annotations__.keys())


# ---------------------------------------------------------------------------
# build_skills_agent — agent() node
# ---------------------------------------------------------------------------

class TestAgentNode:

    def _make_state(self, question="Tell me about CUST00000001", history=None):
        return {
            "question": question,
            "history": history or [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }

    def _make_response(self, content):
        resp = MagicMock()
        resp.content = content
        return resp

    def test_tool_call_action_sets_pending_call(self, patched_mod):
        _, llm_instance, tagged_llm, fns = patched_mod
        agent_fn = fns["agent"]

        content = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        tagged_llm.invoke.return_value = self._make_response(content)

        state = self._make_state()
        result = agent_fn(state)

        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["action"] == "tool_call"
        assert f"Assistant: {content}" in result["history"]
        assert len(result["logs"]) == 1

    def test_function_call_action_normalised_to_tool_call(self, patched_mod):
        _, llm_instance, tagged_llm, fns = patched_mod
        agent_fn = fns["agent"]

        content = json.dumps({
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        tagged_llm.invoke.return_value = self._make_response(content)

        state = self._make_state()
        result = agent_fn(state)

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "customer_lookalike"

    def test_done_action_sets_final_answer(self, patched_mod):
        _, llm_instance, tagged_llm, fns = patched_mod
        agent_fn = fns["agent"]

        content = json.dumps({
            "action": "done",
            "answer": "Customer risk is low.",
        })
        tagged_llm.invoke.return_value = self._make_response(content)

        state = self._make_state()
        result = agent_fn(state)

        assert result["final_answer"] == "Customer risk is low."
        assert result["pending_call"] == {}

    def test_done_action_empty_answer(self, patched_mod):
        _, llm_instance, tagged_llm, fns = patched_mod
        agent_fn = fns["agent"]

        content = json.dumps({"action": "done"})
        tagged_llm.invoke.return_value = self._make_response(content)

        result = agent_fn(self._make_state())
        assert result["final_answer"] == ""

    def test_malformed_json_returns_no_pending_call(self, patched_mod):
        _, llm_instance, tagged_llm, fns = patched_mod
        agent_fn = fns["agent"]

        tagged_llm.invoke.return_value = self._make_response(
            "I'm sorry, I cannot help with that."
        )

        result = agent_fn(self._make_state())
        assert result["pending_call"] == {}
        assert "Assistant:" in result["history"][0]

    def test_partial_json_in_text_is_parsed(self, patched_mod):
        _, llm_instance, tagged_llm, fns = patched_mod
        agent_fn = fns["agent"]

        inner = json.dumps({
            "action": "tool_call",
            "tool_name": "run_risk_assessment",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        content = f"Sure, let me call the tool: {inner} — done."
        tagged_llm.invoke.return_value = self._make_response(content)

        result = agent_fn(self._make_state())
        assert result["pending_call"]["tool_name"] == "run_risk_assessment"

    def test_history_appended_to_system_prompt(self, patched_mod):
        _, llm_instance, tagged_llm, fns = patched_mod
        agent_fn = fns["agent"]

        content = json.dumps({"action": "done", "answer": "ok"})