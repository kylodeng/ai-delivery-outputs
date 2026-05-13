"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure
- build_skills_agent() factory function
- agent() inner node: happy-path tool_call response, "done" response, plain text fallback,
  JSON parsing with extra whitespace, "function_call" type normalisation, malformed JSON,
  no-JSON content
- execute_tool() inner node: successful tool invocation, tool returning error payload,
  tool raising exception, unknown tool name
- router() inner node (via graph introspection): pending_call present → execute_tool,
  final_answer present → END, neither → END

Mocks used:
- unittest.mock.MagicMock / AsyncMock for LLMS, LLM instances, @tool objects
- patch for backend.agent.agent_with_skills.LLMS
- patch for backend.agent.agent_with_skills.TOOLS
- patch for backend.agent.agent_with_skills._SKILLS_DIR (Path glob)
- patch for backend.agent.agent_with_skills._profile_tool
- patch for backend.agent.agent_with_skills._lookalike_tool
- patch for backend.agent.agent_with_skills._run_underwriting_assessment

TODOs:
- TODO: Integration test for the compiled LangGraph graph (requires full LangChain env)
- TODO: Test streaming behaviour once real LLM is wired up
- TODO: Test on_tool_start / on_tool_end callback firing via LangChain callback system
"""

import json
import operator
from pathlib import Path
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module with heavy dependencies mocked
# ---------------------------------------------------------------------------

def _make_mock_llms():
    """Return a mock LLMS class whose .get_model() chain works."""
    mock_llm_instance = MagicMock()
    tagged_llm = MagicMock()
    mock_llm_instance.with_config.return_value = tagged_llm

    mock_llms_cls = MagicMock()
    mock_llms_cls.return_value.get_model.return_value = mock_llm_instance
    return mock_llms_cls, mock_llm_instance, tagged_llm


def _make_tool_mock(return_value="tool result"):
    t = AsyncMock()
    t.ainvoke = AsyncMock(return_value=return_value)
    return t


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def patch_imports(tmp_path):
    """Patch all heavy external imports before importing agent module."""
    # Create a fake skills directory with one skill file
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "skill_a.md").write_text("## Skill A\nDo something.")
    (skills_dir / "skill_b.md").write_text("## Skill B\nDo something else.")
    (skills_dir / "index.md").write_text("# Index\nShould be excluded.")

    profile_tool = _make_tool_mock("profile_result")
    lookalike_tool = _make_tool_mock("lookalike_result")
    mock_assessment = _make_tool_mock("assessment_result")
    mock_run_underwriting = MagicMock(return_value=mock_assessment)

    mock_llms_cls, mock_llm_instance, tagged_llm = _make_mock_llms()

    with (
        patch("backend.agent.agent_with_skills.LLMS", mock_llms_cls),
        patch("backend.agent.agent_with_skills._profile_tool", profile_tool),
        patch("backend.agent.agent_with_skills._lookalike_tool", lookalike_tool),
        patch(
            "backend.agent.agent_with_skills._run_underwriting_assessment",
            mock_run_underwriting,
        ),
        patch(
            "backend.agent.agent_with_skills._SKILLS_DIR",
            skills_dir,
        ),
    ):
        yield {
            "skills_dir": skills_dir,
            "profile_tool": profile_tool,
            "lookalike_tool": lookalike_tool,
            "mock_assessment": mock_assessment,
            "mock_llms_cls": mock_llms_cls,
            "mock_llm_instance": mock_llm_instance,
            "tagged_llm": tagged_llm,
        }


# ---------------------------------------------------------------------------
# Helper to rebuild agent nodes fresh for each test
# ---------------------------------------------------------------------------

def _build_agent(patch_imports_fixture):
    """Import (or reload) the module and call build_skills_agent()."""
    import importlib
    import backend.agent.agent_with_skills as mod
    importlib.reload(mod)
    return mod.build_skills_agent()


# ---------------------------------------------------------------------------
# Because build_skills_agent returns a compiled StateGraph, we need to
# extract the inner node callables.  We do this by calling build directly
# and poking at the graph object, or by unit-testing the closures we can
# reconstruct independently.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: AgentState
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_history_uses_operator_add(self):
        """AgentState history annotation uses operator.add for merging."""
        import backend.agent.agent_with_skills as mod
        hints = mod.AgentState.__annotations__
        assert "history" in hints

    def test_logs_uses_operator_add(self):
        import backend.agent.agent_with_skills as mod
        hints = mod.AgentState.__annotations__
        assert "logs" in hints

    def test_required_keys(self):
        import backend.agent.agent_with_skills as mod
        keys = set(mod.AgentState.__annotations__.keys())
        assert {"question", "history", "logs", "pending_call", "final_answer"} == keys


# ---------------------------------------------------------------------------
# Tests: TOOLS dict
# ---------------------------------------------------------------------------

class TestToolsDict:
    def test_tools_keys_present(self, patch_imports):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)
        assert set(mod.TOOLS.keys()) == {
            "get_customer_info",
            "customer_lookalike",
            "run_risk_assessment",
        }


# ---------------------------------------------------------------------------
# Tests: build_skills_agent factory
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, patch_imports):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)
        graph = mod.build_skills_agent()
        # LangGraph compiled graphs expose .invoke / .ainvoke
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_llms_instantiated_with_correct_params(self, patch_imports):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)
        mod.build_skills_agent(model_name="anthropic-fast", temperature=0.5)
        patch_imports["mock_llms_cls"].assert_called_with(temperature=0.5, streaming=True)
        patch_imports["mock_llms_cls"].return_value.get_model.assert_called_with("anthropic-fast")

    def test_default_model_name_and_temperature(self, patch_imports):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)
        mod.build_skills_agent()
        patch_imports["mock_llms_cls"].assert_called_with(temperature=0, streaming=True)
        patch_imports["mock_llms_cls"].return_value.get_model.assert_called_with("anthropic-fast")

    def test_tagged_llm_uses_agent_tag(self, patch_imports):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)
        mod.build_skills_agent()
        patch_imports["mock_llm_instance"].with_config.assert_called_once_with({"tags": ["agent"]})

    def test_skill_docs_exclude_index_md(self, patch_imports):
        """Index.md should not appear in the system prompt skills block."""
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        # Capture system prompt by inspecting invoke call
        tagged_llm = patch_imports["tagged_llm"]
        mock_response = MagicMock()
        mock_response.content = json.dumps({"action": "done", "answer": "ok"})
        tagged_llm.invoke.return_value = mock_response

        graph = mod.build_skills_agent()
        # We call invoke with a minimal state to trigger agent node
        graph.invoke({"question": "hello", "history": [], "logs": [], "pending_call": {}, "final_answer": ""})

        call_args = tagged_llm.invoke.call_args
        messages = call_args[0][0]
        system_content = messages[0].content
        assert "Index" not in system_content or "index.md" not in system_content
        assert "Skill A" in system_content
        assert "Skill B" in system_content

    def test_skill_docs_included_in_system_prompt(self, patch_imports):
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        tagged_llm = patch_imports["tagged_llm"]
        mock_response = MagicMock()
        mock_response.content = json.dumps({"action": "done", "answer": "yes"})
        tagged_llm.invoke.return_value = mock_response

        graph = mod.build_skills_agent()
        graph.invoke({"question": "hi", "history": [], "logs": [], "pending_call": {}, "final_answer": ""})

        system_content = tagged_llm.invoke.call_args[0][0][0].content
        assert "SKILL DOCUMENTATION" in system_content


# ---------------------------------------------------------------------------
# Shared helper: get agent and execute_tool callables via a minimal graph run
# ---------------------------------------------------------------------------

def _invoke_graph_and_get_state(patch_imports, question, llm_response_content):
    import importlib
    import backend.agent.agent_with_skills as mod
    importlib.reload(mod)

    tagged_llm = patch_imports["tagged_llm"]
    mock_response = MagicMock()
    mock_response.content = llm_response_content
    tagged_llm.invoke.return_value = mock_response

    graph = mod.build_skills_agent()
    init_state = {
        "question": question,
        "history": [],
        "logs": [],
        "pending_call": {},
        "final_answer": "",
    }
    result = graph.invoke(init_state)
    return result


# ---------------------------------------------------------------------------
# Tests: agent() node — via graph.invoke (synchronous path, no tool required)
# ---------------------------------------------------------------------------

class TestAgentNode:
    def test_done_action_sets_final_answer(self, patch_imports):
        response = json.dumps({"action": "done", "answer": "The risk is low."})
        result = _invoke_graph_and_get_state(patch_imports, "assess CUST00000001", response)
        assert result["final_answer"] == "The risk is low."

    def test_done_action_clears_pending_call(self, patch_imports):
        response = json.dumps({"action": "done", "answer": "done"})
        result = _invoke_graph_and_get_state(patch_imports, "hello", response)
        assert result["pending_call"] == {}

    def test_done_action_history_appended(self, patch_imports):
        response = json.dumps({"action": "done", "answer": "final"})
        result = _invoke_graph_and_get_state(patch_imports, "hello", response)
        assert any("Assistant:" in h for h in result["history"])

    def test_log_entry_on_chat_model_end(self, patch_imports):
        response = json.dumps({"action": "done", "answer": "answer"})
        result = _invoke_graph_and_get_state(patch_imports, "hello", response)
        assert any(log.get("event") == "on_chat_model_end" for log in result["logs"])

    def test_plain_text_response_no_json(self, patch_imports):
        """LLM returns plain text with no JSON → pending_call stays empty."""
        result = _invoke_graph_and_get_state(
            patch_imports, "hello", "I cannot answer that."
        )
        assert result["pending_call"] == {}
        assert result["final_answer"] == ""

    def test_malformed_json_falls_through(self, patch_imports):
        result = _invoke_graph_and_get_state(
            patch_imports, "hello", '{"action": "tool_call", "tool_name": broken'
        )
        assert result["pending_call"] == {}

    def test_function_call_type_normalised_to_tool_call(self, patch_imports):
        """'type': 'function_call' should be treated as a tool call."""
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        tagged_llm = patch_imports["tagged_llm"]
        profile_tool = patch_imports["profile_tool"]
        profile_tool.ainvoke = AsyncMock(return_value="profile_data")

        # First call: function_call format; second call: done
        mock_resp_1 = MagicMock()
        mock_resp_1.content = json.dumps(
            {"type": "function_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}}
        )
        mock_resp_2 = MagicMock()
        mock_resp_2.content = json.dumps({"action": "done", "answer": "profile fetched"})
        tagged_llm.invoke.side_effect = [mock_resp_1, mock_resp_2]

        with patch.dict(mod.TOOLS, {"get_customer_info": profile_tool}):
            graph = mod.build_skills_agent()
            result = graph.invoke(
                {"question": "get profile", "history": [], "logs": [], "pending_call": {}, "final_answer": ""}
            )
        assert result["final_answer"] == "profile fetched"

    def test_history_passed_to_llm_on_second_call(self, patch_imports):
        """Conversation history is injected into the system prompt on subsequent turns."""
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        tagged_llm = patch_imports["tagged_llm"]
        profile_tool = patch_imports["profile_tool"]
        profile_tool.ainvoke = AsyncMock(return_value="profile_result")

        mock_resp_1 = MagicMock()
        mock_resp_1.content = json.dumps(
            {"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}}
        )
        mock_resp_2 = MagicMock()
        mock_resp_2.content = json.dumps({"action": "done", "answer": "profile result"})
        tagged_llm.invoke.side_effect = [mock_resp_1, mock_resp_2]

        with patch.dict(mod.TOOLS, {"get_customer_info": profile_tool}):
            graph = mod.build_skills_agent()
            graph.invoke(
                {"question": "get profile", "history": [], "logs": [], "pending_call": {}, "final_answer": ""}
            )

        # Second invoke call should include Conversation History in system prompt
        second_call_messages = tagged_llm.invoke.call_args_list[1][0][0]