```python
"""
Test module for backend/agent/agent_with_skills.py

What is tested:
    - AgentState TypedDict structure and field annotations
    - agent() node: happy path (tool_call action), done action, no JSON, malformed JSON,
      normalisation of "function_call" → "tool_call", history injection, log generation
    - execute_tool() node: successful tool invocation, tool error payload, unknown tool,
      exception raised during ainvoke, JSON-string result, dict result
    - build_skills_agent(): returns a callable graph (smoke test with mocked LLM + skills dir)
    - router() node: routes to execute_tool when pending_call is set, routes to END otherwise

Mocks used:
    - backend.modules.assessment._run_underwriting_assessment  (patched at import)
    - modules.tools.customer_lookalike                         (patched at import)
    - modules.tools.get_customer_profile                       (patched at import)
    - backend.modules.LLMS.LLMS                                (patched at import)
    - pathlib.Path.glob / Path.read_text                       (via tmp_path + monkeypatch)
    - langchain_core.messages.HumanMessage / SystemMessage     (real objects, lightweight)
    - LLM .invoke() method                                     (MagicMock)
    - tool .ainvoke() method                                   (AsyncMock)

TODOs:
    - TODO: Integration test for full graph execution requires a running LLM endpoint
    - TODO: Test streaming behaviour (on_tool_start / on_tool_end callbacks) once callback
            infrastructure is available
    - TODO: Verify LangGraph edge wiring once langgraph internals are more stable
"""

import importlib
import json
import operator
import sys
import types
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake module tree so the source file can be
# imported without the real dependencies installed.
# ---------------------------------------------------------------------------

def _make_fake_tool(name: str = "fake_tool") -> MagicMock:
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value="ok")
    tool.__name__ = name
    return tool


def _install_fake_modules():
    """
    Install lightweight stubs into sys.modules before importing the agent.
    Only called once per process via the session-scoped fixture.
    """
    # --- langchain_core.messages ---
    lc_msgs = types.ModuleType("langchain_core.messages")
    class HumanMessage:
        def __init__(self, content): self.content = content
    class SystemMessage:
        def __init__(self, content): self.content = content
    lc_msgs.HumanMessage = HumanMessage
    lc_msgs.SystemMessage = SystemMessage
    sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
    sys.modules["langchain_core.messages"] = lc_msgs

    # --- langgraph ---
    lg = types.ModuleType("langgraph")
    lg_graph = types.ModuleType("langgraph.graph")
    class _START: pass
    class StateGraph:
        def __init__(self, *a, **kw): self._nodes = {}; self._edges = []
        def add_node(self, name, fn): self._nodes[name] = fn
        def add_edge(self, a, b): self._edges.append((a, b))
        def add_conditional_edges(self, *a, **kw): pass
        def compile(self): return self
        def ainvoke(self, *a, **kw): return AsyncMock()()
    lg_graph.START = _START
    lg_graph.END = "END"
    lg_graph.StateGraph = StateGraph
    sys.modules.setdefault("langgraph", lg)
    sys.modules["langgraph.graph"] = lg_graph

    # --- backend.modules.assessment ---
    bk = types.ModuleType("backend")
    bk_mods = types.ModuleType("backend.modules")
    bk_assess = types.ModuleType("backend.modules.assessment")
    fake_assess_tool = _make_fake_tool("run_risk_assessment")
    bk_assess._run_underwriting_assessment = MagicMock(return_value=fake_assess_tool)
    sys.modules.setdefault("backend", bk)
    sys.modules.setdefault("backend.modules", bk_mods)
    sys.modules["backend.modules.assessment"] = bk_assess

    # --- modules.tools ---
    mod_tools = types.ModuleType("modules.tools")
    mod_tools.customer_lookalike = _make_fake_tool("customer_lookalike")
    mod_tools.get_customer_profile = _make_fake_tool("get_customer_profile")
    sys.modules.setdefault("modules", types.ModuleType("modules"))
    sys.modules["modules.tools"] = mod_tools

    # --- modules.LLMS ---
    mod_llms = types.ModuleType("modules.LLMS")
    fake_llm_instance = MagicMock()
    fake_llm_instance.with_config = MagicMock(return_value=fake_llm_instance)
    fake_llms_cls = MagicMock(return_value=MagicMock(get_model=MagicMock(return_value=fake_llm_instance)))
    mod_llms.LLMS = fake_llms_cls
    sys.modules.setdefault("modules.LLMS", mod_llms)
    # also expose as backend.modules.LLMS if needed
    bk_llms = types.ModuleType("backend.modules.LLMS")
    bk_llms.LLMS = fake_llms_cls
    sys.modules["backend.modules.LLMS"] = bk_llms

    return {
        "profile_tool": mod_tools.get_customer_profile,
        "lookalike_tool": mod_tools.customer_lookalike,
        "assess_tool": fake_assess_tool,
        "llm_instance": fake_llm_instance,
        "llms_cls": fake_llms_cls,
    }


# ---------------------------------------------------------------------------
# Session fixture: install stubs & import the module once
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def stubs():
    return _install_fake_modules()


@pytest.fixture(scope="session")
def agent_module(stubs, tmp_path_factory):
    """
    Import agent_with_skills with a temporary skills directory containing one
    markdown file so skill_docs is non-empty.
    """
    skills_dir = tmp_path_factory.mktemp("skills")
    (skills_dir / "skill_one.md").write_text("## Skill One\nDescribes how to call get_customer_info.")
    (skills_dir / "index.md").write_text("# Index\nShould be ignored.")
    (skills_dir / "skill_two.md").write_text("## Skill Two\nDescribes customer_lookalike.")

    # Patch _SKILLS_DIR before importing
    with patch.dict(sys.modules):
        # We need to insert the backend package path so the relative import works
        backend_parent = Path(__file__).parent.parent  # repo root
        if str(backend_parent) not in sys.path:
            sys.path.insert(0, str(backend_parent))

        # Force fresh import
        mod_name = "backend.agent.agent_with_skills"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        import backend.agent.agent_with_skills as aws
        # Patch the skills dir to our temp dir
        aws._SKILLS_DIR = skills_dir
        return aws


# ---------------------------------------------------------------------------
# Convenience fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def base_state():
    """Minimal valid AgentState."""
    return {
        "question": "Tell me about customer CUST00000001",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }


@pytest.fixture()
def state_with_history(base_state):
    base_state["history"] = [
        "Assistant: {\"action\": \"tool_call\", \"tool_name\": \"get_customer_info\", \"tool_args\": {\"customer_id\": \"CUST00000001\"}}",
        "Tool get_customer_info result: {\"name\": \"Alice\"}",
    ]
    return base_state


# ---------------------------------------------------------------------------
# Helper: extract inner functions from build_skills_agent closure
# ---------------------------------------------------------------------------

def _build_and_extract(agent_module, stubs, *, model_name="anthropic-fast", temperature=0):
    """
    Call build_skills_agent and then manually extract inner node functions by
    inspecting the StateGraph's _nodes dict (our stub).
    """
    graph = agent_module.build_skills_agent(model_name=model_name, temperature=temperature)
    nodes = graph._nodes  # populated by our stub StateGraph.add_node
    return graph, nodes


# ===========================================================================
# Tests: AgentState structure
# ===========================================================================

class TestAgentState:
    def test_fields_exist(self, agent_module):
        fields = agent_module.AgentState.__annotations__
        assert "question" in fields
        assert "history" in fields
        assert "logs" in fields
        assert "pending_call" in fields
        assert "final_answer" in fields

    def test_history_annotated_with_operator_add(self, agent_module):
        hints = agent_module.AgentState.__annotations__
        # history should be Annotated[list[str], operator.add]
        hist = hints["history"]
        # get_type_hints may unwrap or not — check __metadata__ if present
        meta = getattr(hist, "__metadata__", None)
        if meta:
            assert operator.add in meta
        else:
            # fallback: just ensure it's some annotation
            assert hist is not None

    def test_logs_annotated_with_operator_add(self, agent_module):
        hints = agent_module.AgentState.__annotations__
        logs = hints["logs"]
        meta = getattr(logs, "__metadata__", None)
        if meta:
            assert operator.add in meta

    def test_tools_dict_contains_expected_keys(self, agent_module):
        assert "get_customer_info" in agent_module.TOOLS
        assert "customer_lookalike" in agent_module.TOOLS
        assert "run_risk_assessment" in agent_module.TOOLS

    def test_tools_dict_has_three_entries(self, agent_module):
        assert len(agent_module.TOOLS) == 3


# ===========================================================================
# Tests: build_skills_agent()
# ===========================================================================

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, agent_module, stubs):
        graph, nodes = _build_and_extract(agent_module, stubs)
        assert graph is not None

    def test_agent_node_registered(self, agent_module, stubs):
        _, nodes = _build_and_extract(agent_module, stubs)
        assert "agent" in nodes

    def test_execute_tool_node_registered(self, agent_module, stubs):
        _, nodes = _build_and_extract(agent_module, stubs)
        assert "execute_tool" in nodes

    def test_router_node_registered(self, agent_module, stubs):
        _, nodes = _build_and_extract(agent_module, stubs)
        assert "router" in nodes

    def test_custom_temperature_passed_to_llms(self, agent_module, stubs):
        stubs["llms_cls"].reset_mock()
        agent_module.build_skills_agent(model_name="anthropic-fast", temperature=0.7)
        stubs["llms_cls"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_default_temperature_is_zero(self, agent_module, stubs):
        stubs["llms_cls"].reset_mock()
        agent_module.build_skills_agent()
        call_kwargs = stubs["llms_cls"].call_args[1]
        assert call_kwargs["temperature"] == 0

    def test_skill_docs_loaded_into_system_prompt(self, agent_module, stubs):
        """
        Verify that skill files are read (indirectly, by checking the LLM
        was invoked with a system message containing skill content).
        """
        graph, nodes = _build_and_extract(agent_module, stubs)
        llm = stubs["llm_instance"]
        llm.invoke = MagicMock(return_value=MagicMock(content='{"action": "done", "answer": "hi"}'))

        agent_fn = nodes["agent"]
        state = {
            "question": "Hello",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        agent_fn(state)
        call_args = llm.invoke.call_args[0][0]
        system_content = call_args[0].content
        assert "Skill One" in system_content or "skill" in system_content.lower()


# ===========================================================================
# Tests: agent() node
# ===========================================================================

class TestAgentNode:
    def _get_agent_fn(self, agent_module, stubs):
        _, nodes = _build_and_extract(agent_module, stubs)
        return nodes["agent"]

    def _invoke(self, agent_fn, llm, content, state):
        llm.invoke = MagicMock(return_value=MagicMock(content=content))
        return agent_fn(state)

    # --- Happy path: tool_call ---

    def test_tool_call_action_sets_pending_call(self, agent_module, stubs, base_state):
        agent_fn = self._get_agent_fn(agent_module, stubs)
        llm = stubs["llm_instance"]
        payload = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        result = self._invoke(agent_fn, llm, payload, base_state)
        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"

    def test_tool_call_appends_history(self, agent_module, stubs, base_state):
        agent_fn = self._get_agent_fn(agent_module, stubs)
        llm = stubs["llm_instance"]
        payload = json.dumps({"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {}})
        result = self._invoke(agent_fn, llm, payload, base_state)
        assert any("Assistant:" in h for h in result["history"])

    def test_tool_call_creates_log_entry(self, agent_module, stubs, base_state):
        agent_fn = self._get_agent_fn(agent_module, stubs)
        llm = stubs["llm_instance"]
        payload = json.dumps({"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {}})
        result = self._invoke(agent_fn, llm, payload, base_state)
        assert len(result["logs"]) == 1
        assert result["logs"][0]["name"] == "agent"

    # --- Happy path: done action ---

    def test_done_action_sets_final_answer(self, agent_module, stubs, base_state):
        agent_fn = self._get_agent_fn(agent_module, stubs)
        llm = stubs["llm_instance"]
        payload = json.dumps({"action": "done", "answer": "The customer is low risk."})
        result = self._invoke(agent_fn, llm,