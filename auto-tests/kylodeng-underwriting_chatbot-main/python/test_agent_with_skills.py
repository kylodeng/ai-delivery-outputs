```python
"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function (graph construction, LLM wiring)
- agent() inner node: happy path tool_call, done action, function_call normalisation,
  JSON parse errors, plain-text fallback, missing json block
- execute_tool() inner node: successful invocation, tool error payload, exception path,
  unknown tool name, non-dict/non-string result
- router() function (pending_call truthy → execute_tool, falsy → agent, final_answer set → END)
- TOOLS dict keys present

Mocks used:
- backend.agent.agent_with_skills.LLMS            (LLM factory)
- backend.agent.agent_with_skills._profile_tool   (get_customer_profile @tool)
- backend.agent.agent_with_skills._lookalike_tool (customer_lookalike @tool)
- backend.agent.agent_with_skills._run_underwriting_assessment (assessment factory)
- pathlib.Path.glob / Path.read_text              (skill docs loading)
- langchain_core.messages.SystemMessage / HumanMessage (message construction)

TODOs:
- TODO: Integration test with a real StateGraph.astream() — needs full LangGraph runtime
- TODO: Test streaming behaviour of tagged_llm — needs LangChain streaming harness
- TODO: Test skill_docs content injection — needs fixture .md files on disk
- TODO: Test on_tool_start / on_tool_end callback hooks fired by LangChain
"""

import asyncio
import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers to build a minimal importable environment for the module under test
# ---------------------------------------------------------------------------

def _make_tool_stub(name: str) -> MagicMock:
    """Return a MagicMock that behaves enough like a LangChain @tool."""
    stub = MagicMock()
    stub.ainvoke = AsyncMock(return_value=f"result_from_{name}")
    stub.with_config = MagicMock(return_value=stub)
    return stub


def _make_llm_stub():
    llm = MagicMock()
    llm.with_config = MagicMock(return_value=llm)
    response = MagicMock()
    response.content = '{"action": "done", "answer": "ok"}'
    llm.invoke = MagicMock(return_value=response)
    return llm


def _make_llms_class(llm_stub):
    cls = MagicMock()
    instance = MagicMock()
    instance.get_model = MagicMock(return_value=llm_stub)
    cls.return_value = instance
    return cls


# ---------------------------------------------------------------------------
# Module-level patch context: we patch heavy dependencies BEFORE importing
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _patch_heavy_deps():
    """
    Patch all external/heavy imports so the module can be imported in tests
    without real network / DB / filesystem access.
    """
    profile_tool = _make_tool_stub("get_customer_profile")
    lookalike_tool = _make_tool_stub("customer_lookalike")
    assessment_tool = _make_tool_stub("run_risk_assessment")
    assessment_factory = MagicMock(return_value=assessment_tool)

    llm_stub = _make_llm_stub()
    llms_cls = _make_llms_class(llm_stub)

    # Minimal fake skill doc so glob doesn't need real files
    fake_path = MagicMock(spec=Path)
    fake_path.name = "skill_a.md"
    fake_path.read_text = MagicMock(return_value="# Skill A\nDoes something.")

    fake_glob = MagicMock(return_value=[fake_path])

    patches = [
        patch("backend.modules.assessment._run_underwriting_assessment", assessment_factory),
        patch("modules.tools.get_customer_profile", profile_tool),
        patch("modules.tools.customer_lookalike", lookalike_tool),
        patch("backend.modules.assessment._run_underwriting_assessment", assessment_factory),
    ]

    # We need to inject fake modules before the target module is imported
    fake_modules = {
        "modules": types.ModuleType("modules"),
        "modules.tools": types.ModuleType("modules.tools"),
        "modules.LLMS": types.ModuleType("modules.LLMS"),
        "backend.modules": types.ModuleType("backend.modules"),
        "backend.modules.assessment": types.ModuleType("backend.modules.assessment"),
        "langchain_core": types.ModuleType("langchain_core"),
        "langchain_core.messages": types.ModuleType("langchain_core.messages"),
        "langgraph": types.ModuleType("langgraph"),
        "langgraph.graph": types.ModuleType("langgraph.graph"),
    }

    # Populate fake modules with necessary attributes
    fake_modules["modules.tools"].get_customer_profile = profile_tool
    fake_modules["modules.tools"].customer_lookalike = lookalike_tool
    fake_modules["modules.LLMS"].LLMS = llms_cls
    fake_modules["backend.modules.assessment"]._run_underwriting_assessment = assessment_factory

    # LangChain message stubs
    HumanMessage = MagicMock(side_effect=lambda content: {"role": "human", "content": content})
    SystemMessage = MagicMock(side_effect=lambda content: {"role": "system", "content": content})
    fake_modules["langchain_core.messages"].HumanMessage = HumanMessage
    fake_modules["langchain_core.messages"].SystemMessage = SystemMessage

    # LangGraph stubs
    START = "START"
    fake_graph_instance = MagicMock()
    fake_graph_instance.add_node = MagicMock()
    fake_graph_instance.add_edge = MagicMock()
    fake_graph_instance.add_conditional_edges = MagicMock()
    fake_graph_instance.compile = MagicMock(return_value=MagicMock())

    StateGraph = MagicMock(return_value=fake_graph_instance)
    fake_modules["langgraph.graph"].StateGraph = StateGraph
    fake_modules["langgraph.graph"].START = START

    # Store stubs so fixtures can access them
    stubs = {
        "profile_tool": profile_tool,
        "lookalike_tool": lookalike_tool,
        "assessment_tool": assessment_tool,
        "assessment_factory": assessment_factory,
        "llm_stub": llm_stub,
        "llms_cls": llms_cls,
        "fake_path": fake_path,
        "StateGraph": StateGraph,
        "fake_graph_instance": fake_graph_instance,
    }

    # Inject into sys.modules BEFORE import
    original_modules = {}
    for mod_name, mod in fake_modules.items():
        original_modules[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = mod

    # Also ensure backend.agent is importable
    if "backend" not in sys.modules:
        sys.modules["backend"] = types.ModuleType("backend")
    if "backend.agent" not in sys.modules:
        sys.modules["backend.agent"] = types.ModuleType("backend.agent")

    # Patch Path.glob to return our fake skill file
    with patch.object(Path, "glob", fake_glob):
        # Now import (or reload) the module under test
        if "backend.agent.agent_with_skills" in sys.modules:
            del sys.modules["backend.agent.agent_with_skills"]

        import backend.agent.agent_with_skills as _mod
        sys.modules["backend.agent.agent_with_skills"] = _mod
        stubs["module"] = _mod

    yield stubs

    # Restore original sys.modules entries
    for mod_name, original in original_modules.items():
        if original is None:
            sys.modules.pop(mod_name, None)
        else:
            sys.modules[mod_name] = original


@pytest.fixture
def mod(_patch_heavy_deps):
    return _patch_heavy_deps["module"]


@pytest.fixture
def llm_stub(_patch_heavy_deps):
    return _patch_heavy_deps["llm_stub"]


@pytest.fixture
def profile_tool(_patch_heavy_deps):
    return _patch_heavy_deps["profile_tool"]


@pytest.fixture
def lookalike_tool(_patch_heavy_deps):
    return _patch_heavy_deps["lookalike_tool"]


@pytest.fixture
def assessment_tool(_patch_heavy_deps):
    return _patch_heavy_deps["assessment_tool"]


# ---------------------------------------------------------------------------
# Helper: extract the inner node functions from a built agent
# ---------------------------------------------------------------------------

def _build_and_extract(mod, llm_stub, response_content: str):
    """Build agent graph and extract (agent_fn, execute_tool_fn, router_fn)."""
    llm_stub.with_config = MagicMock(return_value=llm_stub)
    resp = MagicMock()
    resp.content = response_content
    llm_stub.invoke = MagicMock(return_value=resp)

    graph_mock = MagicMock()
    graph_mock.add_node = MagicMock()
    graph_mock.add_edge = MagicMock()
    graph_mock.add_conditional_edges = MagicMock()
    compiled = MagicMock()
    graph_mock.compile = MagicMock(return_value=compiled)

    captured = {}

    original_StateGraph = sys.modules["langgraph.graph"].StateGraph

    def capturing_StateGraph(state_schema):
        captured["instance"] = graph_mock
        return graph_mock

    sys.modules["langgraph.graph"].StateGraph = capturing_StateGraph

    try:
        result = mod.build_skills_agent()
    finally:
        sys.modules["langgraph.graph"].StateGraph = original_StateGraph

    # Extract node callables captured via add_node calls
    nodes = {}
    for call_args in graph_mock.add_node.call_args_list:
        args = call_args[0]
        if len(args) >= 2:
            nodes[args[0]] = args[1]
        elif len(args) == 1 and callable(args[0]):
            nodes[args[0].__name__] = args[0]

    # Extract router via add_conditional_edges
    router_fn = None
    for call_args in graph_mock.add_conditional_edges.call_args_list:
        args = call_args[0]
        if len(args) >= 2 and callable(args[1]):
            router_fn = args[1]

    return nodes, router_fn, compiled


def _make_state(**overrides) -> dict:
    base = {
        "question": "What is the risk for CUST00000001?",
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_tools_keys_present(self, mod):
        assert "get_customer_info" in mod.TOOLS
        assert "customer_lookalike" in mod.TOOLS
        assert "run_risk_assessment" in mod.TOOLS

    def test_tools_has_exactly_three_entries(self, mod):
        assert len(mod.TOOLS) == 3

    def test_skills_dir_is_path(self, mod):
        assert isinstance(mod._SKILLS_DIR, Path)


# ---------------------------------------------------------------------------
# Tests: AgentState structure
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_agent_state_is_typed_dict(self, mod):
        from typing import get_type_hints
        hints = get_type_hints(mod.AgentState, include_extras=True)
        assert "question" in hints
        assert "history" in hints
        assert "logs" in hints
        assert "pending_call" in hints
        assert "final_answer" in hints

    def test_agent_state_can_be_instantiated_as_dict(self, mod):
        state: mod.AgentState = {
            "question": "hello",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "hello"


# ---------------------------------------------------------------------------
# Tests: build_skills_agent
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, mod, llm_stub):
        nodes, router_fn, compiled = _build_and_extract(
            mod, llm_stub, '{"action": "done", "answer": "ok"}'
        )
        assert compiled is not None

    def test_llms_called_with_correct_defaults(self, mod, _patch_heavy_deps):
        llms_cls = _patch_heavy_deps["llms_cls"]
        llms_cls.reset_mock()
        mod.build_skills_agent()
        llms_cls.assert_called()
        call_kwargs = llms_cls.call_args[1]
        assert call_kwargs.get("temperature") == 0
        assert call_kwargs.get("streaming") is True

    def test_llms_called_with_custom_temperature(self, mod, _patch_heavy_deps):
        llms_cls = _patch_heavy_deps["llms_cls"]
        llms_cls.reset_mock()
        mod.build_skills_agent(temperature=0.7)
        call_kwargs = llms_cls.call_args[1]
        assert call_kwargs.get("temperature") == 0.7

    def test_get_model_called_with_model_name(self, mod, _patch_heavy_deps):
        llms_cls = _patch_heavy_deps["llms_cls"]
        instance = MagicMock()
        instance.get_model = MagicMock(return_value=_patch_heavy_deps["llm_stub"])
        llms_cls.return_value = instance
        mod.build_skills_agent(model_name="anthropic-fast")
        instance.get_model.assert_called_with("anthropic-fast")

    def test_with_config_tags_agent(self, mod, llm_stub):
        llm_stub.with_config = MagicMock(return_value=llm_stub)
        mod.build_skills_agent()
        llm_stub.with_config.assert_called()
        call_args = llm_stub.with_config.call_args[0][0]
        assert "agent" in call_args.get("tags", [])

    @pytest.mark.skip(reason="TODO: requires real .md skill files on disk for content injection test")
    def test_skill_docs_injected_into_system_prompt(self, mod, llm_stub):
        pass


# ---------------------------------------------------------------------------
# Tests: agent() node — we extract it from the built graph
# ---------------------------------------------------------------------------

class TestAgentNode:

    def _get_agent_fn(self, mod, llm_stub, content):
        nodes, _, _ = _build_and_extract(mod, llm_stub, content)
        return nodes.get("agent")

    # --- happy path: tool_call action ---
    def test_tool_call_action_sets_pending_call(self, mod, llm_stub):
        content = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        agent_fn = self._get_agent_fn(mod, llm_stub, content)
        state = _make_state(question="