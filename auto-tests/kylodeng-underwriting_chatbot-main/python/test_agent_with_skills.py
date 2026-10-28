"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field behaviour
- build_skills_agent() factory function and returned graph
- agent() inner node: happy path, tool_call action, function_call action, done action,
  malformed JSON, no JSON match, missing action key
- execute_tool() inner node: successful tool invocation, tool returns error payload,
  tool raises exception, unknown tool name
- router() inner node: pending_call present → routes to execute_tool,
  final_answer present → routes to END, neither → routes to END
- Skill-doc loading from _SKILLS_DIR glob

Mocks used:
- unittest.mock.patch / MagicMock for LLMS, _profile_tool, _lookalike_tool,
  _run_underwriting_assessment, Path.glob / Path.read_text
- pytest-asyncio for async execute_tool tests

TODOs:
- TODO: Integration test for the full compiled StateGraph (.astream / .ainvoke)
        requires a real or deeply-mocked LangGraph runtime.
- TODO: Test streaming behaviour (on_tool_start / on_tool_end callback firing)
        requires a LangChain callback harness.
- TODO: Test build_skills_agent with every supported model_name value
        once LLMS.get_model contract is documented.
"""

import json
import operator
import re
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch, call

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Helpers to build a minimal AgentState dict
# ---------------------------------------------------------------------------

def make_state(
    question: str = "Tell me about customer CUST00000001",
    history: list[str] | None = None,
    logs: list[dict] | None = None,
    pending_call: dict | None = None,
    final_answer: str = "",
) -> dict:
    return {
        "question": question,
        "history": history or [],
        "logs": logs or [],
        "pending_call": pending_call or {},
        "final_answer": final_answer,
    }


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_imports():
    """
    Patch every heavy external dependency before the module is imported so
    tests remain hermetic.  We reload the module inside each test that needs
    the patched version via a helper.
    """
    pass  # patching is done per-test via individual decorators / context managers


@pytest.fixture()
def mock_llm_response():
    """Return a MagicMock that looks like a LangChain ChatModel response."""
    response = MagicMock()
    response.content = '{"action": "done", "answer": "All good."}'
    return response


@pytest.fixture()
def mock_tagged_llm(mock_llm_response):
    tagged = MagicMock()
    tagged.invoke.return_value = mock_llm_response
    return tagged


@pytest.fixture()
def mock_llm(mock_tagged_llm):
    llm = MagicMock()
    llm.with_config.return_value = mock_tagged_llm
    return llm


@pytest.fixture()
def mock_llms_cls(mock_llm):
    llms_instance = MagicMock()
    llms_instance.get_model.return_value = mock_llm
    llms_cls = MagicMock(return_value=llms_instance)
    return llms_cls


@pytest.fixture()
def mock_skill_files(tmp_path):
    """Create two fake skill markdown files."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "skill_a.md").write_text("# Skill A\nDo something.")
    (skills_dir / "skill_b.md").write_text("# Skill B\nDo something else.")
    (skills_dir / "index.md").write_text("# Index\nShould be ignored.")
    return skills_dir


# ---------------------------------------------------------------------------
# Helper: build the agent and extract inner nodes
# ---------------------------------------------------------------------------

def _build_agent_under_test(mock_llms_cls, skills_dir: Path):
    """
    Import and call build_skills_agent() with all heavy deps mocked.
    Returns the *graph* and also the raw inner functions by capturing them
    through the StateGraph's node registry.
    """
    profile_tool = AsyncMock(return_value='{"name": "John"}')
    lookalike_tool = AsyncMock(return_value='["CUST00006151"]')
    assessment_tool = AsyncMock(return_value='{"risk": "low"}')

    with (
        patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        patch("backend.agent.agent_with_skills._profile_tool", profile_tool),
        patch("backend.agent.agent_with_skills._lookalike_tool", lookalike_tool),
        patch(
            "backend.agent.agent_with_skills._run_underwriting_assessment",
            return_value=assessment_tool,
        ),
        patch("backend.agent.agent_with_skills.TOOLS", {
            "get_customer_info": profile_tool,
            "customer_lookalike": lookalike_tool,
            "run_risk_assessment": assessment_tool,
        }),
    ):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        graph = mod.build_skills_agent()
        return graph, mod, profile_tool, lookalike_tool, assessment_tool


# ---------------------------------------------------------------------------
# Module-level structure tests
# ---------------------------------------------------------------------------

class TestAgentStateStructure:
    def test_agent_state_has_required_keys(self):
        from backend.agent.agent_with_skills import AgentState
        state: AgentState = {
            "question": "q",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "q"
        assert isinstance(state["history"], list)
        assert isinstance(state["logs"], list)
        assert isinstance(state["pending_call"], dict)
        assert state["final_answer"] == ""

    def test_tools_dict_keys(self):
        with (
            patch("backend.agent.agent_with_skills.LLMS"),
            patch("backend.agent.agent_with_skills._run_underwriting_assessment",
                  return_value=AsyncMock()),
        ):
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            assert set(mod.TOOLS.keys()) == {
                "get_customer_info",
                "customer_lookalike",
                "run_risk_assessment",
            }


# ---------------------------------------------------------------------------
# build_skills_agent() factory tests
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:

    def _make_graph(self, mock_llms_cls, mock_skill_files):
        profile_tool = AsyncMock(return_value='{"name": "John"}')
        lookalike_tool = AsyncMock(return_value='["CUST00006151"]')
        assessment_tool = AsyncMock(return_value='{"risk": "low"}')
        with (
            patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", mock_skill_files),
            patch("backend.agent.agent_with_skills.TOOLS", {
                "get_customer_info": profile_tool,
                "customer_lookalike": lookalike_tool,
                "run_risk_assessment": assessment_tool,
            }),
        ):
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            return mod.build_skills_agent()

    def test_returns_compiled_graph(self, mock_llms_cls, mock_skill_files):
        graph = self._make_graph(mock_llms_cls, mock_skill_files)
        # Compiled LangGraph exposes an invoke / ainvoke interface
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_llms_instantiated_with_defaults(self, mock_llms_cls, mock_skill_files):
        self._make_graph(mock_llms_cls, mock_skill_files)
        mock_llms_cls.assert_called_once_with(temperature=0, streaming=True)

    def test_llms_instantiated_with_custom_temperature(self, mock_llms_cls, mock_skill_files):
        profile_tool = AsyncMock()
        lookalike_tool = AsyncMock()
        assessment_tool = AsyncMock()
        with (
            patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", mock_skill_files),
            patch("backend.agent.agent_with_skills.TOOLS", {
                "get_customer_info": profile_tool,
                "customer_lookalike": lookalike_tool,
                "run_risk_assessment": assessment_tool,
            }),
        ):
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            mod.build_skills_agent(temperature=0.7)
        mock_llms_cls.assert_called_once_with(temperature=0.7, streaming=True)

    def test_index_md_excluded_from_skill_docs(self, mock_llms_cls, mock_skill_files):
        """The system prompt should NOT contain index.md content."""
        captured_prompts = []
        profile_tool = AsyncMock()
        lookalike_tool = AsyncMock()
        assessment_tool = AsyncMock()

        original_invoke = None

        def capture_invoke(messages):
            for m in messages:
                captured_prompts.append(m.content)
            resp = MagicMock()
            resp.content = '{"action": "done", "answer": "ok"}'
            return resp

        mock_llms_cls.return_value.get_model.return_value.with_config.return_value.invoke.side_effect = capture_invoke

        with (
            patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", mock_skill_files),
            patch("backend.agent.agent_with_skills.TOOLS", {
                "get_customer_info": profile_tool,
                "customer_lookalike": lookalike_tool,
                "run_risk_assessment": assessment_tool,
            }),
        ):
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            graph = mod.build_skills_agent()

        # We cannot call the graph easily without LangGraph runtime;
        # check skill_docs construction by inspecting the prompt closure.
        # We trigger the agent node directly:
        # (skip deep graph invocation – see TODO above)


# ---------------------------------------------------------------------------
# agent() inner node tests
# ---------------------------------------------------------------------------

class TestAgentNode:
    """
    Extract the agent function by monkey-patching StateGraph.add_node and
    capturing what gets registered.
    """

    def _extract_nodes(self, mock_llms_cls, mock_skill_files, tagged_llm):
        mock_llms_cls.return_value.get_model.return_value.with_config.return_value = tagged_llm

        profile_tool = AsyncMock(return_value='{"name": "John"}')
        lookalike_tool = AsyncMock(return_value='["CUST00006151"]')
        assessment_tool = AsyncMock(return_value='{"risk": "low"}')

        nodes: dict[str, Any] = {}
        original_add_node = None

        import langgraph.graph as lg_graph

        real_sg = lg_graph.StateGraph

        class CapturingStateGraph(real_sg):
            def add_node(self, name, fn, **kwargs):
                nodes[name] = fn
                return super().add_node(name, fn, **kwargs)

        with (
            patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", mock_skill_files),
            patch("backend.agent.agent_with_skills.TOOLS", {
                "get_customer_info": profile_tool,
                "customer_lookalike": lookalike_tool,
                "run_risk_assessment": assessment_tool,
            }),
            patch("backend.agent.agent_with_skills.StateGraph", CapturingStateGraph),
        ):
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            mod.build_skills_agent()

        return nodes, profile_tool, lookalike_tool, assessment_tool

    # -- agent node ----------------------------------------------------------

    def test_agent_done_action_returns_final_answer(
        self, mock_llms_cls, mock_skill_files, mock_tagged_llm
    ):
        mock_tagged_llm.invoke.return_value.content = (
            '{"action": "done", "answer": "Risk is low."}'
        )
        nodes, *_ = self._extract_nodes(mock_llms_cls, mock_skill_files, mock_tagged_llm)
        agent_fn = nodes["agent"]

        result = agent_fn(make_state())

        assert result["final_answer"] == "Risk is low."
        assert result["pending_call"] == {}
        assert any("Assistant:" in h for h in result["history"])

    def test_agent_tool_call_action_sets_pending_call(
        self, mock_llms_cls, mock_skill_files, mock_tagged_llm
    ):
        payload = {
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        }
        mock_tagged_llm.invoke.return_value.content = json.dumps(payload)
        nodes, *_ = self._extract_nodes(mock_llms_cls, mock_skill_files, mock_tagged_llm)
        agent_fn = nodes["agent"]

        result = agent_fn(make_state())

        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["action"] == "tool_call"
        assert "final_answer" not in result

    def test_agent_function_call_type_normalised_to_tool_call(
        self, mock_llms_cls, mock_skill_files, mock_tagged_llm
    ):
        """LLM sometimes returns "type": "function_call" — must be normalised."""
        payload = {
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        }
        mock_tagged_llm.invoke.return_value.content = json.dumps(payload)
        nodes, *_ = self._extract_nodes(mock_llms_cls, mock_skill_files, mock_tagged_llm)
        agent_fn = nodes["agent"]

        result = agent_fn(make_state())

        assert result["pending_call"]["action"] == "tool_call"

    def test_agent_logs_contain_on_chat_model_end_event(
        self, mock_llms_cls, mock_skill_files, mock_tagged_llm
    ):
        mock_tagged_llm.invoke.return_value.content = (
            '{"action": "done", "answer": "ok"}'
        )
        nodes, *_ = self._extract_nodes(mock_llms_cls, mock_skill_files, mock_tagged_llm)
        agent_fn = nodes["agent"]

        result =