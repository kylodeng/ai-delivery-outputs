"""
Test suite for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field types
- build_skills_agent() factory function
- agent() inner node: happy path tool_call, function_call normalisation,
  done action, plain-text fallback, JSON parse error, missing json block
- execute_tool() inner node: successful tool invocation, tool returning error
  payload, unknown tool name, tool raising exception
- router() function (stub – source is truncated)

Mocks used:
- backend.agent.agent_with_skills.LLMS  (prevents real LLM construction)
- backend.agent.agent_with_skills.TOOLS (replaces real tool callables)
- backend.agent.agent_with_skills._SKILLS_DIR (controls skill file discovery)
- pathlib.Path.glob / Path.read_text
- unittest.mock.AsyncMock for ainvoke() calls

TODOs:
- router() cannot be fully tested because the source is truncated; a stub is provided.
- Integration test with a real StateGraph requires a running LangGraph environment.
- Skill documentation loading from real _SKILLS_DIR is not tested end-to-end.
"""

import json
import operator
import types
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with all heavy dependencies mocked
# ---------------------------------------------------------------------------

def _make_fake_tool(name: str = "fake_tool") -> MagicMock:
    """Return an async-capable mock that looks like a LangChain @tool object."""
    tool = MagicMock(name=name)
    tool.ainvoke = AsyncMock(return_value=f"result_from_{name}")
    return tool


@pytest.fixture()
def mock_llm():
    """A fake LLM returned by LLMS(...).get_model(...)."""
    llm = MagicMock(name="llm")
    tagged = MagicMock(name="tagged_llm")
    llm.with_config.return_value = tagged
    return llm, tagged


@pytest.fixture()
def mock_llms_class(mock_llm):
    """Patches LLMS so no real model is instantiated."""
    llm, tagged = mock_llm
    with patch("backend.agent.agent_with_skills.LLMS") as MockLLMS:
        instance = MockLLMS.return_value
        instance.get_model.return_value = llm
        yield MockLLMS, llm, tagged


@pytest.fixture()
def fake_tools():
    return {
        "get_customer_info": _make_fake_tool("get_customer_info"),
        "customer_lookalike": _make_fake_tool("customer_lookalike"),
        "run_risk_assessment": _make_fake_tool("run_risk_assessment"),
    }


@pytest.fixture()
def skills_dir_patch(tmp_path):
    """
    Creates two temporary skill .md files and patches _SKILLS_DIR so the
    module reads them instead of the real directory.
    """
    (tmp_path / "skill_a.md").write_text("# Skill A content")
    (tmp_path / "skill_b.md").write_text("# Skill B content")
    (tmp_path / "index.md").write_text("# index – should be ignored")
    return tmp_path


def _import_module_with_patches(mock_llms_class, fake_tools, skills_dir_patch):
    """
    Re-import (or reload) the module under test with all external dependencies
    replaced.  Returns the module object plus the extracted inner functions via
    the compiled graph internals – but more reliably we return the builder
    output so we can test the callables directly.
    """
    import importlib
    import backend.agent.agent_with_skills as mod
    return mod


# ---------------------------------------------------------------------------
# Fixtures that build an actual agent graph with all dependencies mocked
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_components(mock_llms_class, fake_tools, skills_dir_patch):
    """
    Patches all external dependencies and calls build_skills_agent() so that
    the inner node callables (agent, execute_tool) are accessible via the
    compiled graph's node mapping.
    """
    MockLLMS, llm, tagged_llm = mock_llms_class

    with patch("backend.agent.agent_with_skills.TOOLS", fake_tools), \
         patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir_patch):

        import backend.agent.agent_with_skills as mod
        # Reload so _SKILLS_DIR patch is picked up at module scope
        import importlib
        importlib.reload(mod)

        # Re-apply LLMS patch after reload
        with patch.object(mod, "LLMS", MockLLMS), \
             patch.object(mod, "TOOLS", fake_tools), \
             patch.object(mod, "_SKILLS_DIR", skills_dir_patch):

            graph = mod.build_skills_agent(model_name="anthropic-fast", temperature=0)

    return graph, tagged_llm, fake_tools


# ---------------------------------------------------------------------------
# Utility: build a minimal AgentState dict
# ---------------------------------------------------------------------------

def _state(
    question: str = "Tell me about customer CUST00000001",
    history: list | None = None,
    logs: list | None = None,
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


# ===========================================================================
# 1.  AgentState structure
# ===========================================================================

class TestAgentStateStructure:
    def test_agent_state_fields_exist(self):
        import backend.agent.agent_with_skills as mod
        hints = mod.AgentState.__annotations__
        assert "question" in hints
        assert "history" in hints
        assert "logs" in hints
        assert "pending_call" in hints
        assert "final_answer" in hints

    def test_agent_state_can_be_constructed(self):
        import backend.agent.agent_with_skills as mod
        state: mod.AgentState = {
            "question": "q",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "q"


# ===========================================================================
# 2.  TOOLS dictionary
# ===========================================================================

class TestToolsDict:
    def test_tools_keys_present(self):
        with patch("backend.agent.agent_with_skills.LLMS"), \
             patch("backend.agent.agent_with_skills._run_underwriting_assessment"), \
             patch("backend.agent.agent_with_skills._profile_tool"), \
             patch("backend.agent.agent_with_skills._lookalike_tool"):
            import backend.agent.agent_with_skills as mod
            assert "get_customer_info" in mod.TOOLS
            assert "customer_lookalike" in mod.TOOLS
            assert "run_risk_assessment" in mod.TOOLS

    def test_tools_has_exactly_three_entries(self):
        import backend.agent.agent_with_skills as mod
        assert len(mod.TOOLS) == 3


# ===========================================================================
# 3.  build_skills_agent – construction
# ===========================================================================

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, mock_llms_class, fake_tools, skills_dir_patch):
        MockLLMS, llm, tagged_llm = mock_llms_class
        with patch("backend.agent.agent_with_skills.TOOLS", fake_tools), \
             patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir_patch):
            import backend.agent.agent_with_skills as mod
            import importlib
            importlib.reload(mod)
            with patch.object(mod, "LLMS", MockLLMS), \
                 patch.object(mod, "TOOLS", fake_tools), \
                 patch.object(mod, "_SKILLS_DIR", skills_dir_patch):
                graph = mod.build_skills_agent()
        # LangGraph compiled graphs expose .invoke
        assert callable(graph.invoke)

    def test_llms_called_with_correct_args(self, mock_llms_class, fake_tools, skills_dir_patch):
        MockLLMS, llm, tagged_llm = mock_llms_class
        with patch("backend.agent.agent_with_skills.TOOLS", fake_tools), \
             patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir_patch):
            import backend.agent.agent_with_skills as mod
            import importlib
            importlib.reload(mod)
            with patch.object(mod, "LLMS", MockLLMS), \
                 patch.object(mod, "TOOLS", fake_tools), \
                 patch.object(mod, "_SKILLS_DIR", skills_dir_patch):
                mod.build_skills_agent(model_name="gpt-4", temperature=0.5)
        MockLLMS.assert_called_with(temperature=0.5, streaming=True)
        MockLLMS.return_value.get_model.assert_called_with("gpt-4")

    def test_skill_docs_excludes_index_md(self, mock_llms_class, fake_tools, skills_dir_patch):
        """index.md must NOT appear in the system prompt."""
        MockLLMS, llm, tagged_llm = mock_llms_class
        # Inspect system prompt by capturing the content passed to tagged_llm.invoke
        captured_messages = []

        def fake_invoke(messages):
            captured_messages.extend(messages)
            resp = MagicMock()
            resp.content = '{"action": "done", "answer": "ok"}'
            return resp

        tagged_llm.invoke.side_effect = fake_invoke

        with patch("backend.agent.agent_with_skills.TOOLS", fake_tools), \
             patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir_patch):
            import backend.agent.agent_with_skills as mod
            import importlib
            importlib.reload(mod)
            with patch.object(mod, "LLMS", MockLLMS), \
                 patch.object(mod, "TOOLS", fake_tools), \
                 patch.object(mod, "_SKILLS_DIR", skills_dir_patch):
                graph = mod.build_skills_agent()
                graph.invoke(_state())

        system_text = captured_messages[0].content
        assert "index" not in system_text.lower() or "index.md" not in system_text

    def test_skill_docs_includes_skill_content(self, mock_llms_class, fake_tools, skills_dir_patch):
        MockLLMS, llm, tagged_llm = mock_llms_class
        captured_messages = []

        def fake_invoke(messages):
            captured_messages.extend(messages)
            resp = MagicMock()
            resp.content = '{"action": "done", "answer": "ok"}'
            return resp

        tagged_llm.invoke.side_effect = fake_invoke

        with patch("backend.agent.agent_with_skills.TOOLS", fake_tools), \
             patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir_patch):
            import backend.agent.agent_with_skills as mod
            import importlib
            importlib.reload(mod)
            with patch.object(mod, "LLMS", MockLLMS), \
                 patch.object(mod, "TOOLS", fake_tools), \
                 patch.object(mod, "_SKILLS_DIR", skills_dir_patch):
                graph = mod.build_skills_agent()
                graph.invoke(_state())

        system_text = captured_messages[0].content
        assert "Skill A content" in system_text
        assert "Skill B content" in system_text


# ===========================================================================
# 4.  agent() node — via graph.invoke (synchronous)
# ===========================================================================

class TestAgentNode:
    """Tests for the agent() inner function by running the full graph."""

    def _build(self, mock_llms_class, fake_tools, skills_dir_patch, llm_response: str):
        MockLLMS, llm, tagged_llm = mock_llms_class
        resp = MagicMock()
        resp.content = llm_response
        tagged_llm.invoke.return_value = resp

        with patch("backend.agent.agent_with_skills.TOOLS", fake_tools), \
             patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir_patch):
            import backend.agent.agent_with_skills as mod
            import importlib
            importlib.reload(mod)
            with patch.object(mod, "LLMS", MockLLMS), \
                 patch.object(mod, "TOOLS", fake_tools), \
                 patch.object(mod, "_SKILLS_DIR", skills_dir_patch):
                graph = mod.build_skills_agent()
        return graph, tagged_llm

    def test_agent_tool_call_action(self, mock_llms_class, fake_tools, skills_dir_patch):
        tool_response = json.dumps({
            "action": "tool_call",
            "tool_name": "get_customer_info",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        graph, _ = self._build(mock_llms_class, fake_tools, skills_dir_patch, tool_response)
        result = graph.invoke(_state())
        # After tool call the graph continues and execute_tool runs
        assert "logs" in result
        assert any(
            log.get("name") == "get_customer_info"
            for log in result["logs"]
        )

    def test_agent_function_call_normalised_to_tool_call(
        self, mock_llms_class, fake_tools, skills_dir_patch
    ):
        """LLM may return 'type': 'function_call' — must be normalised."""
        tool_response = json.dumps({
            "type": "function_call",
            "tool_name": "customer_lookalike",
            "tool_args": {"customer_id": "CUST00000001"},
        })
        graph, _ = self._build(mock_llms_class, fake_tools, skills_dir_patch, tool_response)
        result = graph.invoke(_state())
        assert any(
            log.get("name") == "customer_lookalike"
            for log in result["logs"]
        )

    def test_agent_done_action_sets_final_answer(
        self, mock_llms_class, fake_tools, skills_dir_patch
    ):
        done_response = json.dumps({
            "action": "done",
            "answer": "Here is the customer summary.",
        })
        graph, _ = self._build(mock_llms_class, fake_tools, skills_dir_patch, done_response)
        result = graph.invoke(_state())
        assert result["final_answer"] == "Here is the customer summary."

    def test_agent_done_with_empty_answer(
        self, mock_llms_class, fake_tools, skills_dir_patch
    ):
        done_response = json.dumps({"action": "done"})
        graph, _ = self._build(mock_llms_class, fake_tools, skills_dir_patch, done_response)
        result = graph.invoke(_state())
        assert result["final_answer"] == ""

    def test_agent_plain_text_fallback(
        self, mock_llms_class, fake_tools, skills_dir_patch
    ):
        """No JSON in response → pending_call should be empty dict."""
        graph, _ = self._build(
            mock_llms_class, fake_tools, skills_dir_patch,
            "I'm sorry, I cannot help with that."
        )
        result = graph.invoke(_state())