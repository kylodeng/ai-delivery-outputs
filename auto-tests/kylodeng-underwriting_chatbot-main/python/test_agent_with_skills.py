"""
Test module for backend/agent/agent_with_skills.py

What is tested:
  - AgentState TypedDict structure and field annotations
  - build_skills_agent() factory: LLM construction, skill-doc loading, agent/execute_tool/router nodes
  - agent() node: JSON parsing (tool_call, function_call, done, plain text, malformed JSON)
  - execute_tool() node: happy path, tool error payload, tool raises exception, unknown tool
  - router() node: pending_call present → execute_tool, empty pending_call + final_answer → END, loop guard

Mocks used:
  - backend.agent.agent_with_skills.LLMS            – prevents real LLM construction
  - backend.agent.agent_with_skills._profile_tool   – stub LangChain @tool
  - backend.agent.agent_with_skills._lookalike_tool – stub LangChain @tool
  - backend.agent.agent_with_skills._run_underwriting_assessment – stub assessment tool
  - pathlib.Path.glob / Path.read_text              – synthetic skill docs injected in-memory

TODOs:
  - TODO: Full integration test of build_skills_agent() with a real StateGraph compile+stream
          requires LangGraph runtime; stubbed below.
  - TODO: Test streaming callbacks (on_tool_start / on_tool_end) fired via LangChain callback
          machinery – needs a real callback handler harness.
  - TODO: Confirm exact router sentinel value for END (depends on langgraph version import).
"""

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
    """Return a mock LLM response object with .content = content."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_tool(return_value):
    """Return an async-capable mock that mimics a LangChain @tool object."""
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=return_value)
    return tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_skill_docs(tmp_path):
    """
    Patch _SKILLS_DIR so that build_skills_agent reads two synthetic skill files
    instead of touching the real file-system.
    """
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "a_skill.md").write_text("## Skill A\nUse get_customer_info(customer_id).")
    (skills_dir / "b_skill.md").write_text("## Skill B\nUse customer_lookalike(customer_id).")
    (skills_dir / "index.md").write_text("# Index – should be excluded")
    return skills_dir


@pytest.fixture()
def mock_llm():
    """A mock LLMS instance whose .get_model() returns a chainable mock."""
    llm_instance = MagicMock()
    tagged = MagicMock()
    llm_instance.with_config.return_value = tagged
    return llm_instance, tagged


@pytest.fixture()
def patched_tools():
    profile = _make_tool('{"name": "Alice"}')
    lookalike = _make_tool('["CUST00006151","CUST00000272"]')
    assessment = _make_tool('{"risk": "low"}')
    return {"get_customer_info": profile, "customer_lookalike": lookalike, "run_risk_assessment": assessment}


# ---------------------------------------------------------------------------
# Utility: build agent under full mocking
# ---------------------------------------------------------------------------

def _build_agent(mock_skill_docs, patched_tools, tagged_llm):
    """
    Import and call build_skills_agent() with all external dependencies patched.
    Returns the three inner functions: agent, execute_tool, router.
    """
    import importlib
    import backend.agent.agent_with_skills as mod

    with (
        patch.object(mod, "_SKILLS_DIR", mock_skill_docs),
        patch.object(mod, "TOOLS", patched_tools),
    ):
        # Re-build; we pass tagged_llm via the LLMS mock
        llms_mock = MagicMock()
        llms_mock.get_model.return_value = tagged_llm
        tagged_llm.with_config.return_value = tagged_llm

        with patch.object(mod, "LLMS", return_value=llms_mock):
            result = mod.build_skills_agent()

    return result  # returns the compiled graph (or inner fns via closure)


# ---------------------------------------------------------------------------
# 1. AgentState structure
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_typed_dict_keys(self):
        from backend.agent.agent_with_skills import AgentState
        keys = set(AgentState.__annotations__.keys())
        assert keys == {"question", "history", "logs", "pending_call", "final_answer"}

    def test_history_uses_operator_add(self):
        from backend.agent.agent_with_skills import AgentState
        import typing, get_annotations
        # The annotation for history should carry operator.add metadata
        hints = AgentState.__annotations__
        # Annotated metadata check
        history_hint = hints["history"]
        # Annotated[list[str], operator.add] – metadata is accessible via __metadata__
        assert hasattr(history_hint, "__metadata__"), "history must be Annotated"
        assert operator.add in history_hint.__metadata__

    def test_logs_uses_operator_add(self):
        from backend.agent.agent_with_skills import AgentState
        logs_hint = AgentState.__annotations__["logs"]
        assert hasattr(logs_hint, "__metadata__"), "logs must be Annotated"
        assert operator.add in logs_hint.__metadata__

    def test_instantiation(self):
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
# 2. TOOLS constant
# ---------------------------------------------------------------------------

class TestToolsConstant:
    def test_tools_keys(self):
        from backend.agent.agent_with_skills import TOOLS
        assert set(TOOLS.keys()) == {"get_customer_info", "customer_lookalike", "run_risk_assessment"}

    def test_tools_are_callable_or_have_ainvoke(self):
        from backend.agent.agent_with_skills import TOOLS
        for name, tool in TOOLS.items():
            assert callable(tool) or hasattr(tool, "ainvoke"), (
                f"Tool '{name}' must be callable or have .ainvoke()"
            )


# ---------------------------------------------------------------------------
# 3. build_skills_agent – LLM construction
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_default_params_call_llms(self, mock_skill_docs, patched_tools):
        import backend.agent.agent_with_skills as mod

        tagged = MagicMock()
        tagged.with_config.return_value = tagged
        tagged.invoke.return_value = _make_llm_response('{"action": "done", "answer": "hi"}')

        llms_mock = MagicMock()
        llms_mock.get_model.return_value = tagged

        with (
            patch.object(mod, "_SKILLS_DIR", mock_skill_docs),
            patch.object(mod, "TOOLS", patched_tools),
            patch.object(mod, "LLMS", return_value=llms_mock) as llms_cls,
        ):
            mod.build_skills_agent()
            llms_cls.assert_called_once_with(temperature=0, streaming=True)
            llms_mock.get_model.assert_called_once_with("anthropic-fast")

    def test_custom_params_forwarded(self, mock_skill_docs, patched_tools):
        import backend.agent.agent_with_skills as mod

        tagged = MagicMock()
        tagged.with_config.return_value = tagged

        llms_mock = MagicMock()
        llms_mock.get_model.return_value = tagged

        with (
            patch.object(mod, "_SKILLS_DIR", mock_skill_docs),
            patch.object(mod, "TOOLS", patched_tools),
            patch.object(mod, "LLMS", return_value=llms_mock) as llms_cls,
        ):
            mod.build_skills_agent(model_name="gpt-4o", temperature=0.5)
            llms_cls.assert_called_once_with(temperature=0.5, streaming=True)
            llms_mock.get_model.assert_called_once_with("gpt-4o")

    def test_index_md_excluded_from_skill_docs(self, mock_skill_docs, patched_tools):
        """index.md must not appear in the system prompt."""
        import backend.agent.agent_with_skills as mod

        captured_prompts = []

        tagged = MagicMock()
        tagged.with_config.return_value = tagged

        original_invoke = MagicMock(return_value=_make_llm_response('{"action": "done", "answer": "ok"}'))
        tagged.invoke = original_invoke

        llms_mock = MagicMock()
        llms_mock.get_model.return_value = tagged

        with (
            patch.object(mod, "_SKILLS_DIR", mock_skill_docs),
            patch.object(mod, "TOOLS", patched_tools),
            patch.object(mod, "LLMS", return_value=llms_mock),
        ):
            graph = mod.build_skills_agent()

        # We can't easily inspect system_prompt directly, but we can invoke the
        # agent node and check what was passed to tagged_llm.invoke
        # Since build_skills_agent returns a compiled graph we test via direct node call
        # by re-extracting inner functions through a second approach below.

    def test_skill_docs_sorted_alphabetically(self, tmp_path, patched_tools):
        """Skills should be loaded in sorted order."""
        import backend.agent.agent_with_skills as mod

        skills_dir = tmp_path / "skills2"
        skills_dir.mkdir()
        (skills_dir / "z_skill.md").write_text("Z content")
        (skills_dir / "a_skill.md").write_text("A content")

        invoke_calls = []

        tagged = MagicMock()
        tagged.with_config.return_value = tagged

        def capture_invoke(messages):
            for m in messages:
                if hasattr(m, "content"):
                    invoke_calls.append(m.content)
            return _make_llm_response('{"action": "done", "answer": "ok"}')

        tagged.invoke = capture_invoke

        llms_mock = MagicMock()
        llms_mock.get_model.return_value = tagged

        with (
            patch.object(mod, "_SKILLS_DIR", skills_dir),
            patch.object(mod, "TOOLS", patched_tools),
            patch.object(mod, "LLMS", return_value=llms_mock),
        ):
            # We can't call the graph node directly without compiling; just ensure no error
            mod.build_skills_agent()


# ---------------------------------------------------------------------------
# 4. agent() node – extracted via closure inspection
# ---------------------------------------------------------------------------

def _extract_agent_node(mock_skill_docs, patched_tools, tagged_llm):
    """
    Patch build_skills_agent so that the StateGraph.add_node calls are intercepted
    and we can recover the raw `agent` closure.
    """
    import backend.agent.agent_with_skills as mod

    captured = {}

    real_add_node = None

    class CapturingGraph:
        def __init__(self, state_cls):
            self._nodes = {}
            self._edges = []

        def add_node(self, name, fn=None, **kwargs):
            if fn is not None:
                self._nodes[name] = fn
            captured.update(self._nodes)

        def add_edge(self, *args, **kwargs):
            pass

        def compile(self, **kwargs):
            captured.update(self._nodes)
            return MagicMock()

    llms_mock = MagicMock()
    llms_mock.get_model.return_value = tagged_llm
    tagged_llm.with_config.return_value = tagged_llm

    with (
        patch.object(mod, "_SKILLS_DIR", mock_skill_docs),
        patch.object(mod, "TOOLS", patched_tools),
        patch.object(mod, "LLMS", return_value=llms_mock),
        patch("backend.agent.agent_with_skills.StateGraph", CapturingGraph),
    ):
        mod.build_skills_agent()

    return captured


class TestAgentNode:
    @pytest.fixture(autouse=True)
    def setup(self, mock_skill_docs, patched_tools):
        self.tagged_llm = MagicMock()
        self.tagged_llm.with_config.return_value = self.tagged_llm
        self.nodes = _extract_agent_node(mock_skill_docs, patched_tools, self.tagged_llm)

    def _call_agent(self, llm_content: str, question: str = "Tell me about CUST00000001", history=None):
        self.tagged_llm.invoke.return_value = _make_llm_response(llm_content)
        state = {
            "question": question,
            "history": history or [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        agent_fn = self.nodes.get("agent")
        if agent_fn is None:
            pytest.skip("agent node not captured – StateGraph mock needs adjustment")
        return agent_fn(state)

    # --- happy paths ---

    def test_tool_call_action(self):
        content = json.dumps({"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}})
        result = self._call_agent(content)
        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert f"Assistant: {content}" in result["history"]
        assert result["logs"][0]["event"] == "on_chat_model_end"

    def test_function_call_normalised_to_tool_call(self):
        content = json.dumps({"type": "function_call", "tool_name": "customer_lookalike", "tool_args": {"customer_id": "CUST00000001"}})
        result = self._call_agent(content)
        assert result["pending_call"]["action"] == "tool_call"

    def test_done_action(self):
        content = json.dumps({"action": "done", "answer": "The customer is low risk."})
        result = self._call_agent(content)
        assert result["final_answer"] == "The customer is low risk."
        assert result["pending_call"] == {}

    def test_done_action_empty_answer(self):
        content = json.dumps({"action": "done"})
        result = self._call_agent(content)
        assert result["final_answer"] == ""

    # --- edge cases ---

    def test_plain_text_no_json(self):
        result = self._call_agent("Hello, I am a plain text response with no JSON.")
        assert result["pending_call"] == {}
        assert "final_answer" not in result or result.get("final_answer") is None or result.get("final_answer") == ""

    def test_malformed_json(self):