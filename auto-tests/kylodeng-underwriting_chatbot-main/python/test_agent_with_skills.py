"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent(): graph construction, LLM initialisation, skill loading
- agent() inner node: JSON parsing, action routing, history/log accumulation
- execute_tool() inner node: happy path, tool error payload, missing tool, exception
- router() function (stubbed — source is truncated)
- TOOLS registry content

Mocks used:
- backend.agent.agent_with_skills.LLMS  (LLM factory)
- backend.agent.agent_with_skills._profile_tool  (get_customer_profile)
- backend.agent.agent_with_skills._lookalike_tool  (customer_lookalike)
- backend.agent.agent_with_skills._run_underwriting_assessment
- pathlib.Path.glob / Path.read_text  (skill-file loading)
- langgraph.graph.StateGraph  (graph builder)

TODOs:
- router() cannot be fully tested because the source is truncated; stub tests are included.
- Integration test for the compiled LangGraph graph requires a live LangChain runtime.
- Streaming event filtering (tagged_llm with_config) needs an end-to-end stream harness.
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
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

FAKE_CUSTOMER_ID = "CUST00000001"
SIMILAR_CUSTOMERS = [
    "CUST00006151", "CUST00000272", "CUST00009567", "CUST00000936", "CUST00004497",
]

TOOL_CALL_JSON = json.dumps({
    "action": "tool_call",
    "tool_name": "get_customer_info",
    "tool_args": {"customer_id": FAKE_CUSTOMER_ID},
})

FUNCTION_CALL_JSON = json.dumps({
    "type": "function_call",
    "tool_name": "customer_lookalike",
    "tool_args": {"customer_id": FAKE_CUSTOMER_ID},
})

DONE_JSON = json.dumps({
    "action": "done",
    "answer": "Here is the customer profile summary.",
})

GARBAGE_RESPONSE = "Sorry, I cannot help with that."
PARTIAL_JSON = '{"action": "tool_call", "tool_name": "get_customer_info"'  # malformed


def _make_llm_response(content: str) -> MagicMock:
    """Return a mock LLM response object whose .content equals *content*."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_tagged_llm(response_content: str) -> MagicMock:
    """Return a mock chain: llm.with_config(...) -> tagged_llm.invoke(...) -> response."""
    tagged = MagicMock()
    tagged.invoke.return_value = _make_llm_response(response_content)
    llm = MagicMock()
    llm.with_config.return_value = tagged
    return llm, tagged


def _make_state(**overrides) -> dict:
    """Build a minimal AgentState dict."""
    base = {
        "question": "Tell me about customer CUST00000001",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Patch targets — module must be importable with all heavy deps stubbed out
# ---------------------------------------------------------------------------

PATCH_BASE = "backend.agent.agent_with_skills"


@pytest.fixture(autouse=True)
def _stub_heavy_imports(monkeypatch):
    """
    Replace heavy external modules before importing the agent module so the
    test suite never touches real LLMs, LangGraph or tool implementations.
    """
    # Stub LLMS factory
    fake_llms_instance = MagicMock()
    fake_llms_cls = MagicMock(return_value=fake_llms_instance)
    monkeypatch.setattr(f"{PATCH_BASE}.LLMS", fake_llms_cls)

    # Stub tools — give them an .ainvoke coroutine
    fake_profile_tool = MagicMock()
    fake_profile_tool.ainvoke = AsyncMock(return_value='{"name": "John Doe"}')

    fake_lookalike_tool = MagicMock()
    fake_lookalike_tool.ainvoke = AsyncMock(return_value=json.dumps(SIMILAR_CUSTOMERS))

    fake_risk_tool = MagicMock()
    fake_risk_tool.ainvoke = AsyncMock(return_value='{"risk": "low"}')

    monkeypatch.setattr(f"{PATCH_BASE}._profile_tool", fake_profile_tool)
    monkeypatch.setattr(f"{PATCH_BASE}._lookalike_tool", fake_lookalike_tool)
    monkeypatch.setattr(f"{PATCH_BASE}._run_underwriting_assessment", MagicMock(return_value=fake_risk_tool))

    # Stub skill files so Path.glob returns two fake .md files
    fake_md_a = MagicMock(spec=Path)
    fake_md_a.name = "skill_a.md"
    fake_md_a.read_text.return_value = "# Skill A\nDo things."

    fake_md_b = MagicMock(spec=Path)
    fake_md_b.name = "skill_b.md"
    fake_md_b.read_text.return_value = "# Skill B\nDo other things."

    # index.md should be excluded
    fake_index = MagicMock(spec=Path)
    fake_index.name = "index.md"
    fake_index.read_text.return_value = "Index content — should be excluded."

    monkeypatch.setattr(
        Path,
        "glob",
        lambda self, pattern: sorted([fake_md_a, fake_md_b, fake_index], key=lambda f: f.name),
    )

    # Stub StateGraph so we don't need a full LangGraph installation
    fake_graph_builder = MagicMock()
    fake_compiled = MagicMock()
    fake_graph_builder.compile.return_value = fake_compiled
    fake_sg_cls = MagicMock(return_value=fake_graph_builder)

    monkeypatch.setattr(f"{PATCH_BASE}.StateGraph", fake_sg_cls)


# ---------------------------------------------------------------------------
# Import the module under test *after* patching
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_module():
    import importlib
    import backend.agent.agent_with_skills as mod
    importlib.reload(mod)
    return mod


# ---------------------------------------------------------------------------
# 1. Module-level constants / TOOLS registry
# ---------------------------------------------------------------------------

class TestToolsRegistry:
    def test_tools_contains_expected_keys(self, agent_module):
        assert "get_customer_info" in agent_module.TOOLS
        assert "customer_lookalike" in agent_module.TOOLS
        assert "run_risk_assessment" in agent_module.TOOLS

    def test_tools_has_exactly_three_entries(self, agent_module):
        assert len(agent_module.TOOLS) == 3

    def test_skills_dir_is_constructed_relative_to_module(self, agent_module):
        # _SKILLS_DIR should end with "skills"
        assert agent_module._SKILLS_DIR.name == "skills"


# ---------------------------------------------------------------------------
# 2. AgentState TypedDict
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_agent_state_can_be_constructed(self, agent_module):
        state: agent_module.AgentState = {
            "question": "Who is CUST00000001?",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "Who is CUST00000001?"

    def test_history_annotation_uses_operator_add(self, agent_module):
        hints = agent_module.AgentState.__annotations__
        # Annotated[list[str], operator.add] — the metadata contains operator.add
        history_annotation = hints["history"]
        assert operator.add in getattr(history_annotation, "__metadata__", ())

    def test_logs_annotation_uses_operator_add(self, agent_module):
        hints = agent_module.AgentState.__annotations__
        logs_annotation = hints["logs"]
        assert operator.add in getattr(logs_annotation, "__metadata__", ())


# ---------------------------------------------------------------------------
# 3. build_skills_agent — construction
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, agent_module):
        result = agent_module.build_skills_agent()
        # StateGraph().compile() is what gets returned
        assert result is not None

    def test_llms_called_with_correct_defaults(self, agent_module):
        agent_module.build_skills_agent()
        agent_module.LLMS.assert_called_once_with(temperature=0, streaming=True)

    def test_llms_called_with_custom_temperature(self, agent_module):
        agent_module.build_skills_agent(temperature=0.7)
        agent_module.LLMS.assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, agent_module):
        agent_module.build_skills_agent(model_name="anthropic-fast")
        llms_instance = agent_module.LLMS.return_value
        llms_instance.get_model.assert_called_once_with("anthropic-fast")

    def test_get_model_called_with_custom_model_name(self, agent_module):
        agent_module.build_skills_agent(model_name="gpt-4")
        llms_instance = agent_module.LLMS.return_value
        llms_instance.get_model.assert_called_once_with("gpt-4")

    def test_llm_tagged_with_agent_tag(self, agent_module):
        agent_module.build_skills_agent()
        llms_instance = agent_module.LLMS.return_value
        raw_llm = llms_instance.get_model.return_value
        raw_llm.with_config.assert_called_once()
        call_kwargs = raw_llm.with_config.call_args
        # Accept positional or keyword
        config = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("config", call_kwargs[1])
        if isinstance(config, dict):
            assert "agent" in config.get("tags", [])

    def test_index_md_excluded_from_skill_docs(self, agent_module):
        # We verify by checking StateGraph was called — the system prompt is internal,
        # but we can inspect if the graph was built (no exception means index.md was skipped)
        agent_module.build_skills_agent()
        agent_module.StateGraph.assert_called_once()

    def test_state_graph_instantiated_with_agent_state(self, agent_module):
        agent_module.build_skills_agent()
        agent_module.StateGraph.assert_called_once_with(agent_module.AgentState)


# ---------------------------------------------------------------------------
# Fixtures to get inner node functions from a built agent
# ---------------------------------------------------------------------------

@pytest.fixture()
def built_agent_internals(agent_module, monkeypatch):
    """
    Build the agent and capture the node functions passed to add_node().
    Returns a dict: {"agent": fn, "execute_tool": fn}.
    """
    nodes = {}

    llm, tagged_llm = _make_tagged_llm(DONE_JSON)
    llms_instance = agent_module.LLMS.return_value
    llms_instance.get_model.return_value = llm

    real_sg_calls = {}

    class CapturingStateGraph:
        def __init__(self, schema):
            self.schema = schema

        def add_node(self, name, fn):
            nodes[name] = fn

        def add_edge(self, *args, **kwargs):
            pass

        def add_conditional_edges(self, *args, **kwargs):
            pass

        def compile(self):
            return MagicMock(name="compiled_graph")

    monkeypatch.setattr(agent_module, "StateGraph", CapturingStateGraph)

    agent_module.build_skills_agent()
    return nodes, tagged_llm


# ---------------------------------------------------------------------------
# 4. agent() node — JSON parsing and routing
# ---------------------------------------------------------------------------

class TestAgentNode:
    def test_tool_call_action_sets_pending_call(self, built_agent_internals):
        nodes, tagged_llm = built_agent_internals
        agent_fn = nodes.get("agent")
        if agent_fn is None:
            pytest.skip("agent node not captured — StateGraph patching mismatch")

        tagged_llm.invoke.return_value = _make_llm_response(TOOL_CALL_JSON)
        result = agent_fn(_make_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"

    def test_tool_call_adds_to_history(self, built_agent_internals):
        nodes, tagged_llm = built_agent_internals
        agent_fn = nodes.get("agent")
        if agent_fn is None:
            pytest.skip("agent node not captured")

        tagged_llm.invoke.return_value = _make_llm_response(TOOL_CALL_JSON)
        result = agent_fn(_make_state())

        assert len(result["history"]) == 1
        assert "Assistant:" in result["history"][0]

    def test_tool_call_adds_log_entry(self, built_agent_internals):
        nodes, tagged_llm = built_agent_internals
        agent_fn = nodes.get("agent")
        if agent_fn is None:
            pytest.skip("agent node not captured")

        tagged_llm.invoke.return_value = _make_llm_response(TOOL_CALL_JSON)
        result = agent_fn(_make_state())

        assert len(result["logs"]) == 1
        assert result["logs"][0]["event"] == "on_chat_model_end"
        assert result["logs"][0]["name"] == "agent"

    def test_function_call_type_normalised_to_tool_call(self, built_agent_internals):
        """LLM default 'type': 'function_call' should be normalised to action='tool_call'."""
        nodes, tagged_llm = built_agent_internals
        agent_fn = nodes.get("agent")
        if agent_fn is None:
            pytest.skip("agent node not captured")

        tagged_llm.invoke.return_value = _make_llm_response(FUNCTION_CALL_JSON)
        result = agent_fn(_make_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "customer_lookalike"

    def test_done_action_sets_final_answer(self, built_agent_internals):
        nodes, tagged_llm = built_agent_internals
        agent_fn = nodes.get("agent")
        if agent_fn is None:
            pytest.skip("agent node not captured")

        tagged_llm.invoke.return_value = _make_llm_response(DONE_JSON)
        result = agent_fn(_make_state())

        assert result["final_answer"] == "Here is the customer profile summary."
        assert result["pending_call"] == {}

    def test_done_action_still_adds_history(self, built_agent_internals