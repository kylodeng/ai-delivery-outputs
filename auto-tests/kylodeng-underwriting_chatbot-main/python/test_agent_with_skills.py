"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field types
- build_skills_agent() factory: graph construction, prompt assembly, skill doc loading
- agent() node: happy-path tool_call response, done response, function_call normalisation,
  malformed JSON fallback, empty response fallback
- execute_tool() node: successful tool invocation, tool error payload, tool exception,
  unknown tool name
- router() function: routing to execute_tool when pending_call present, routing to END
  when pending_call is empty, routing to END when final_answer is set

Mocks used:
- backend.agent.agent_with_skills.LLMS  (prevents real LLM construction)
- backend.agent.agent_with_skills._profile_tool  (prevents real API calls)
- backend.agent.agent_with_skills._lookalike_tool  (prevents real API calls)
- backend.agent.agent_with_skills._run_underwriting_assessment  (prevents real assessment)
- backend.agent.agent_with_skills._SKILLS_DIR  (patched to a tmp_path fixture)
- langgraph.graph.StateGraph  (partially inspected via build_skills_agent return value)

TODOs:
- TODO: Integration test for full graph execution requires a running LangGraph runtime
- TODO: Test streaming behaviour of tagged_llm requires LangChain streaming harness
- TODO: Test on_tool_start / on_tool_end callback firing requires LangChain callback inspection
"""

import json
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _make_llm_response(content: str) -> MagicMock:
    """Return a mock LLM response object with .content set."""
    resp = MagicMock()
    resp.content = content
    return resp


def _make_tagged_llm(response_content: str) -> MagicMock:
    """Return a mock tagged LLM whose .invoke() returns a canned response."""
    tagged = MagicMock()
    tagged.invoke.return_value = _make_llm_response(response_content)
    return tagged


def _make_llm_stack(*responses: str):
    """Return a mock tagged LLM whose .invoke() cycles through multiple responses."""
    tagged = MagicMock()
    tagged.invoke.side_effect = [_make_llm_response(r) for r in responses]
    return tagged


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    """Create a temporary skills directory with two markdown skill files."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "skill_a.md").write_text("# Skill A\nGet customer info.")
    (skills / "skill_b.md").write_text("# Skill B\nRun risk assessment.")
    # index.md must be excluded
    (skills / "index.md").write_text("# Index – should be ignored")
    return skills


@pytest.fixture()
def mock_tools():
    """Return fresh mock tool objects for _profile_tool, _lookalike_tool, _run_underwriting_assessment."""
    profile = MagicMock()
    profile.ainvoke = AsyncMock(return_value='{"customer_id": "CUST00000001", "name": "Alice"}')

    lookalike = MagicMock()
    lookalike.ainvoke = AsyncMock(return_value='["CUST00006151", "CUST00000272"]')

    assessment_result = MagicMock()
    assessment_result.ainvoke = AsyncMock(return_value='{"risk": "low"}')

    # _run_underwriting_assessment("fast") returns the tool object
    run_assessment = MagicMock(return_value=assessment_result)

    return {
        "profile": profile,
        "lookalike": lookalike,
        "assessment_result": assessment_result,
        "run_assessment": run_assessment,
    }


@pytest.fixture()
def patched_module(skills_dir: Path, mock_tools):
    """
    Import agent_with_skills with all heavy external dependencies mocked so
    that build_skills_agent() can be called without network / GPU.
    """
    llms_instance = MagicMock()
    base_llm = MagicMock()
    llms_instance.get_model.return_value = base_llm

    with (
        patch("backend.agent.agent_with_skills.LLMS", return_value=llms_instance) as mock_llms,
        patch("backend.agent.agent_with_skills._profile_tool", mock_tools["profile"]),
        patch("backend.agent.agent_with_skills._lookalike_tool", mock_tools["lookalike"]),
        patch(
            "backend.agent.agent_with_skills._run_underwriting_assessment",
            mock_tools["run_assessment"],
        ),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
    ):
        # Re-import so module-level TOOLS is rebuilt with the mocked objects
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        yield mod, mock_llms, llms_instance, base_llm


# ---------------------------------------------------------------------------
# AgentState structure
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_typeddict_keys(self):
        from backend.agent.agent_with_skills import AgentState
        keys = AgentState.__annotations__.keys()
        assert "question" in keys
        assert "history" in keys
        assert "logs" in keys
        assert "pending_call" in keys
        assert "final_answer" in keys

    def test_valid_state_construction(self):
        from backend.agent.agent_with_skills import AgentState
        state: AgentState = {
            "question": "What is the risk for CUST00000001?",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        }
        assert state["question"] == "What is the risk for CUST00000001?"
        assert state["history"] == []


# ---------------------------------------------------------------------------
# build_skills_agent – construction behaviour
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, patched_module):
        mod, _, _, base_llm = patched_module
        base_llm.with_config.return_value = _make_tagged_llm("{}")
        graph = mod.build_skills_agent()
        # The compiled LangGraph graph exposes an .invoke / .ainvoke interface
        assert hasattr(graph, "invoke") or hasattr(graph, "ainvoke")

    def test_llms_called_with_correct_defaults(self, patched_module):
        mod, mock_llms_cls, llms_instance, base_llm = patched_module
        base_llm.with_config.return_value = _make_tagged_llm("{}")
        mod.build_skills_agent()
        mock_llms_cls.assert_called_once_with(temperature=0, streaming=True)
        llms_instance.get_model.assert_called_once_with("anthropic-fast")

    def test_llms_called_with_custom_params(self, patched_module):
        mod, mock_llms_cls, llms_instance, base_llm = patched_module
        base_llm.with_config.return_value = _make_tagged_llm("{}")
        mod.build_skills_agent(model_name="openai-gpt4", temperature=0.5)
        mock_llms_cls.assert_called_once_with(temperature=0.5, streaming=True)
        llms_instance.get_model.assert_called_once_with("openai-gpt4")

    def test_tagged_llm_uses_agent_tag(self, patched_module):
        mod, _, _, base_llm = patched_module
        tagged = _make_tagged_llm("{}")
        base_llm.with_config.return_value = tagged
        mod.build_skills_agent()
        call_kwargs = base_llm.with_config.call_args
        config_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]
        assert "agent" in str(config_arg)

    def test_skill_docs_loaded_excludes_index(self, patched_module, skills_dir):
        """index.md must NOT appear in skill_docs injected into system prompt."""
        mod, _, _, base_llm = patched_module
        captured_prompts = []

        def capture_invoke(messages, **kwargs):
            captured_prompts.append(messages[0].content)
            return _make_llm_response('{"action": "done", "answer": "ok"}')

        tagged = MagicMock()
        tagged.invoke.side_effect = capture_invoke
        base_llm.with_config.return_value = tagged

        graph = mod.build_skills_agent()
        # Invoke the graph with a minimal state
        graph.invoke({
            "question": "test",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        })

        assert len(captured_prompts) >= 1
        combined = " ".join(captured_prompts)
        assert "Index – should be ignored" not in combined
        assert "Skill A" in combined
        assert "Skill B" in combined

    def test_empty_skills_dir(self, tmp_path, mock_tools):
        """build_skills_agent should work even when no skill files exist."""
        empty_skills = tmp_path / "empty_skills"
        empty_skills.mkdir()

        llms_instance = MagicMock()
        base_llm = MagicMock()
        llms_instance.get_model.return_value = base_llm
        tagged = _make_tagged_llm('{"action": "done", "answer": "nothing"}')
        base_llm.with_config.return_value = tagged

        with (
            patch("backend.agent.agent_with_skills.LLMS", return_value=llms_instance),
            patch("backend.agent.agent_with_skills._profile_tool", mock_tools["profile"]),
            patch("backend.agent.agent_with_skills._lookalike_tool", mock_tools["lookalike"]),
            patch(
                "backend.agent.agent_with_skills._run_underwriting_assessment",
                mock_tools["run_assessment"],
            ),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", empty_skills),
        ):
            import importlib
            import backend.agent.agent_with_skills as mod2
            importlib.reload(mod2)
            graph = mod2.build_skills_agent()
            assert graph is not None


# ---------------------------------------------------------------------------
# agent() node
# ---------------------------------------------------------------------------

class TestAgentNode:
    """
    We extract the agent() closure from the compiled graph by calling
    build_skills_agent and then invoking the graph step-by-step.
    Instead, we test the agent node behaviour through direct invocation
    of the inner function by reconstructing it.
    """

    def _build_agent_fn(self, tagged_llm, patched_module):
        """Build and return the raw agent closure under test."""
        mod, _, _, base_llm = patched_module
        base_llm.with_config.return_value = tagged_llm
        # We need to reach into the graph nodes; easiest is to rebuild
        # by invoking the graph with the state and inspecting output.
        # Instead return the module so callers can drive via graph.invoke.
        graph = mod.build_skills_agent()
        return graph

    def test_tool_call_response_sets_pending_call(self, patched_module):
        mod, _, _, base_llm = patched_module
        payload = '{"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}}'
        tagged = _make_llm_stack(
            payload,
            '{"action": "done", "answer": "Here is the info"}',
        )
        base_llm.with_config.return_value = tagged

        # Profile tool returns immediately
        mod.TOOLS["get_customer_info"].ainvoke = AsyncMock(
            return_value='{"name": "Alice"}'
        )

        graph = mod.build_skills_agent()
        result = graph.invoke({
            "question": "Tell me about CUST00000001",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        })
        assert result["final_answer"] == "Here is the info"

    def test_done_response_sets_final_answer(self, patched_module):
        mod, _, _, base_llm = patched_module
        tagged = _make_tagged_llm('{"action": "done", "answer": "Risk is low"}')
        base_llm.with_config.return_value = tagged

        graph = mod.build_skills_agent()
        result = graph.invoke({
            "question": "What is the risk?",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        })
        assert result["final_answer"] == "Risk is low"

    def test_function_call_action_normalised(self, patched_module):
        """LLM returning 'type': 'function_call' should be normalised to 'action': 'tool_call'."""
        mod, _, _, base_llm = patched_module
        payload = '{"type": "function_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}}'
        mod.TOOLS["get_customer_info"].ainvoke = AsyncMock(return_value='{"name": "Bob"}')
        tagged = _make_llm_stack(
            payload,
            '{"action": "done", "answer": "Bob info retrieved"}',
        )
        base_llm.with_config.return_value = tagged

        graph = mod.build_skills_agent()
        result = graph.invoke({
            "question": "Get info for CUST00000001",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        })
        assert result["final_answer"] == "Bob info retrieved"

    def test_malformed_json_falls_back_gracefully(self, patched_module):
        """Non-JSON content should not raise; agent should store content in history."""
        mod, _, _, base_llm = patched_module
        tagged = _make_tagged_llm("I cannot help with that right now.")
        base_llm.with_config.return_value = tagged

        graph = mod.build_skills_agent()
        result = graph.invoke({
            "question": "Random question",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        })
        # Should complete without exception; final_answer may be empty string
        assert "final_answer" in result

    def test_partial_json_no_action_falls_back(self, patched_module):
        """Valid JSON but missing 'action' key should fall through to empty pending_call."""
        mod, _, _, base_llm = patched_module
        tagged = _make_tagged_llm('{"tool_name": "get_customer_info"}')
        base_llm.with_config.return_value = tagged

        graph = mod.build_skills_agent()
        result = graph.invoke({
            "question": "What tools do you have?",
            "history": [],
            "logs": [],
            "pending_call": {},
            "final_answer": "",
        })
        assert "final_answer" in result

    def test_history_appended_to_system_prompt(self, patched_module):
        """History from state should appear in the system prompt passed to the LLM."""
        mod, _, _, base_llm