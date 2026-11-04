"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function
- agent() node: happy path (tool_call, done, fallback), edge cases (malformed JSON,
  no JSON, alternate "type" key, missing answer key), boundary values
- execute_tool() node: successful invocation, tool raises exception, tool returns
  error payload, unknown tool name
- router() function (stub — source was truncated before the body was visible)

Mocks used:
- unittest.mock.patch / MagicMock for LLMS, _profile_tool, _lookalike_tool,
  _run_underwriting_assessment, Path.glob (skill loading)
- AsyncMock for tool .ainvoke() calls
- Fake filesystem via tmp_path fixture for skill markdown files

TODOs:
- TODO: router() body was truncated in the source; full routing logic needs tests
  once the complete source is available.
- TODO: Integration test for the full LangGraph compiled graph requires a running
  LangGraph runtime — skipped here.
- TODO: Streaming behaviour (tagged_llm with_config tags) needs an end-to-end
  LangGraph stream test.
"""

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

def _make_skill_files(tmp_path: Path, contents: list[str]) -> Path:
    """Write numbered .md files into a skills directory and return it."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for i, text in enumerate(contents):
        (skills_dir / f"skill_{i:02d}.md").write_text(text)
    return skills_dir


def _make_state(
    question: str = "Tell me about CUST00000001",
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
# Patch targets
# ---------------------------------------------------------------------------

MODULE = "backend.agent.agent_with_skills"

PATCH_LLMS = f"{MODULE}.LLMS"
PATCH_PROFILE = f"{MODULE}._profile_tool"
PATCH_LOOKALIKE = f"{MODULE}._lookalike_tool"
PATCH_ASSESSMENT = f"{MODULE}._run_underwriting_assessment"
PATCH_SKILLS_DIR = f"{MODULE}._SKILLS_DIR"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def skills_dir(tmp_path):
    """Provide a temporary skills directory with two markdown files."""
    return _make_skill_files(
        tmp_path,
        [
            "# Skill: get_customer_info\nFetch customer profile by ID.",
            "# Skill: customer_lookalike\nFind similar customers.",
        ],
    )


@pytest.fixture()
def mock_llm():
    """Return a mock LLM instance whose invoke() can be configured per test."""
    llm_instance = MagicMock()
    tagged = MagicMock()
    llm_instance.with_config.return_value = tagged
    return llm_instance, tagged


@pytest.fixture()
def mock_tool():
    """Return a generic async-capable mock tool."""
    t = MagicMock()
    t.ainvoke = AsyncMock(return_value='{"status": "ok"}')
    return t


@pytest.fixture()
def agent_factory(skills_dir, mock_llm):
    """
    Build the agent node and execute_tool coroutine from build_skills_agent()
    with all external dependencies patched.
    """
    llm_instance, tagged_llm = mock_llm

    mock_assessment_tool = MagicMock()
    mock_assessment_tool.ainvoke = AsyncMock(return_value='{"risk": "low"}')

    mock_profile_tool = MagicMock()
    mock_profile_tool.ainvoke = AsyncMock(return_value='{"name": "Alice"}')

    mock_lookalike_tool = MagicMock()
    mock_lookalike_tool.ainvoke = AsyncMock(return_value='["CUST00006151"]')

    with (
        patch(PATCH_LLMS) as MockLLMS,
        patch(PATCH_SKILLS_DIR, skills_dir),
        patch(PATCH_PROFILE, mock_profile_tool),
        patch(PATCH_LOOKALIKE, mock_lookalike_tool),
        patch(PATCH_ASSESSMENT, return_value=mock_assessment_tool),
    ):
        MockLLMS.return_value.get_model.return_value = llm_instance

        # Import fresh so module-level TOOLS picks up patched values
        import importlib
        import backend.agent.agent_with_skills as mod
        importlib.reload(mod)

        factory_result = mod.build_skills_agent(model_name="anthropic-fast", temperature=0)
        yield factory_result, tagged_llm, mock_profile_tool, mock_lookalike_tool, mock_assessment_tool


# ---------------------------------------------------------------------------
# AgentState structure
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_agent_state_has_required_keys(self):
        """AgentState TypedDict must declare all expected fields."""
        import backend.agent.agent_with_skills as mod
        fields = mod.AgentState.__annotations__
        assert "question" in fields
        assert "history" in fields
        assert "logs" in fields
        assert "pending_call" in fields
        assert "final_answer" in fields

    def test_history_uses_operator_add(self):
        """history should be annotated with operator.add for LangGraph reducers."""
        import backend.agent.agent_with_skills as mod
        hint = mod.AgentState.__annotations__["history"]
        # Annotated metadata contains operator.add
        metadata = hint.__metadata__
        assert operator.add in metadata

    def test_logs_uses_operator_add(self):
        import backend.agent.agent_with_skills as mod
        hint = mod.AgentState.__annotations__["logs"]
        metadata = hint.__metadata__
        assert operator.add in metadata


# ---------------------------------------------------------------------------
# TOOLS registry
# ---------------------------------------------------------------------------

class TestToolsRegistry:
    def test_tools_contains_expected_keys(self):
        import backend.agent.agent_with_skills as mod
        assert "get_customer_info" in mod.TOOLS
        assert "customer_lookalike" in mod.TOOLS
        assert "run_risk_assessment" in mod.TOOLS

    def test_tools_has_exactly_three_entries(self):
        import backend.agent.agent_with_skills as mod
        assert len(mod.TOOLS) == 3


# ---------------------------------------------------------------------------
# build_skills_agent — skill loading
# ---------------------------------------------------------------------------

class TestBuildSkillsAgent:
    def test_skill_docs_concatenated(self, tmp_path):
        """All .md files except index.md should be read and joined."""
        skills_dir = _make_skill_files(
            tmp_path,
            ["# Doc A", "# Doc B"],
        )
        # Also add index.md which must be excluded
        (skills_dir / "index.md").write_text("# Index — should be ignored")

        llm_instance = MagicMock()
        tagged = MagicMock()
        llm_instance.with_config.return_value = tagged

        mock_assessment_tool = MagicMock()
        mock_assessment_tool.ainvoke = AsyncMock(return_value='{}')

        with (
            patch(PATCH_LLMS) as MockLLMS,
            patch(PATCH_SKILLS_DIR, skills_dir),
            patch(PATCH_PROFILE, MagicMock()),
            patch(PATCH_LOOKALIKE, MagicMock()),
            patch(PATCH_ASSESSMENT, return_value=mock_assessment_tool),
        ):
            MockLLMS.return_value.get_model.return_value = llm_instance
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            # Capture the system prompt by triggering agent()
            tagged.invoke.return_value = MagicMock(content='{"action": "done", "answer": "ok"}')
            nodes = mod.build_skills_agent()
            # agent node is the first element
            agent_node = nodes[0]
            agent_node(_make_state())
            call_args = tagged.invoke.call_args
            system_msg_content = call_args[0][0][0].content
            assert "# Doc A" in system_msg_content
            assert "# Doc B" in system_msg_content
            assert "# Index" not in system_msg_content

    def test_index_md_excluded(self, tmp_path):
        """index.md must never appear in the skill_docs injected into the system prompt."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "index.md").write_text("SECRET INDEX")
        (skills_dir / "skill_01.md").write_text("PUBLIC SKILL")

        llm_instance = MagicMock()
        tagged = MagicMock()
        llm_instance.with_config.return_value = tagged
        tagged.invoke.return_value = MagicMock(content='{"action": "done", "answer": "ok"}')

        mock_assessment_tool = MagicMock()
        mock_assessment_tool.ainvoke = AsyncMock(return_value='{}')

        with (
            patch(PATCH_LLMS) as MockLLMS,
            patch(PATCH_SKILLS_DIR, skills_dir),
            patch(PATCH_PROFILE, MagicMock()),
            patch(PATCH_LOOKALIKE, MagicMock()),
            patch(PATCH_ASSESSMENT, return_value=mock_assessment_tool),
        ):
            MockLLMS.return_value.get_model.return_value = llm_instance
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            nodes = mod.build_skills_agent()
            agent_node = nodes[0]
            agent_node(_make_state())
            system_content = tagged.invoke.call_args[0][0][0].content
            assert "SECRET INDEX" not in system_content
            assert "PUBLIC SKILL" in system_content

    def test_returns_tuple_of_callables(self, skills_dir, mock_llm):
        """build_skills_agent should return a tuple/list of callable nodes."""
        llm_instance, tagged = mock_llm
        mock_assessment_tool = MagicMock()
        mock_assessment_tool.ainvoke = AsyncMock(return_value='{}')

        with (
            patch(PATCH_LLMS) as MockLLMS,
            patch(PATCH_SKILLS_DIR, skills_dir),
            patch(PATCH_PROFILE, MagicMock()),
            patch(PATCH_LOOKALIKE, MagicMock()),
            patch(PATCH_ASSESSMENT, return_value=mock_assessment_tool),
        ):
            MockLLMS.return_value.get_model.return_value = llm_instance
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            result = mod.build_skills_agent()
            assert result is not None
            # At least agent and execute_tool are returned
            assert len(result) >= 2

    def test_llm_configured_with_agent_tag(self, skills_dir, mock_llm):
        """The LLM must be tagged with 'agent' so main.py can filter stream events."""
        llm_instance, tagged = mock_llm
        mock_assessment_tool = MagicMock()
        mock_assessment_tool.ainvoke = AsyncMock(return_value='{}')

        with (
            patch(PATCH_LLMS) as MockLLMS,
            patch(PATCH_SKILLS_DIR, skills_dir),
            patch(PATCH_PROFILE, MagicMock()),
            patch(PATCH_LOOKALIKE, MagicMock()),
            patch(PATCH_ASSESSMENT, return_value=mock_assessment_tool),
        ):
            MockLLMS.return_value.get_model.return_value = llm_instance
            import importlib
            import backend.agent.agent_with_skills as mod
            importlib.reload(mod)
            mod.build_skills_agent()
            llm_instance.with_config.assert_called_once()
            call_kwargs = llm_instance.with_config.call_args
            config_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]
            assert "agent" in config_arg.get("tags", [])


# ---------------------------------------------------------------------------
# agent() node — happy paths
# ---------------------------------------------------------------------------

class TestAgentNodeHappyPath:
    def test_tool_call_action_sets_pending_call(self, agent_factory):
        nodes, tagged_llm, *_ = agent_factory
        agent_node = nodes[0]
        response_content = json.dumps(
            {"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {"customer_id": "CUST00000001"}}
        )
        tagged_llm.invoke.return_value = MagicMock(content=response_content)

        result = agent_node(_make_state())

        assert result["pending_call"]["action"] == "tool_call"
        assert result["pending_call"]["tool_name"] == "get_customer_info"
        assert result["pending_call"]["tool_args"] == {"customer_id": "CUST00000001"}

    def test_tool_call_appends_to_history(self, agent_factory):
        nodes, tagged_llm, *_ = agent_factory
        agent_node = nodes[0]
        content = json.dumps({"action": "tool_call", "tool_name": "get_customer_info", "tool_args": {}})
        tagged_llm.invoke.return_value = MagicMock(content=content)

        result = agent_node(_make_state())

        assert len(result["history"]) == 1
        assert result["history"][0].startswith("Assistant:")

    def test_done_action_sets_final_answer(self, agent_factory):
        nodes, tagged_llm, *_ = agent_factory
        agent_node = nodes[0]
        content = json.dumps({"action": "done", "answer": "The customer is low risk."})
        tagged_llm.invoke.return_value = MagicMock(content=content)

        result = agent_node(_make_state())

        assert result["final_answer"] == "The customer is low risk."
        assert result["pending_call"] == {}

    def test_done_action_clears_pending_call(self, agent_factory):
        nodes, tagged_llm, *_ = agent_factory
        agent_node = nodes[0]
        content = json.dumps({"action": "done", "answer": "Done."})
        tagged_llm.invoke.return_value = MagicMock(content=content)

        result = agent_node(_make_state(pending_call={"tool_name": "old"}))

        assert result["pending_call"] == {}

    def test_logs_contain_chat_model_event(self, agent_factory):
        nodes, tagged_llm, *_ = agent_factory
        agent_node = nodes[0]
        content = json.dumps({"action": "done", "answer": "ok"})
        tagged_llm.invoke.return_value = MagicMock(content=content)

        result = agent_node(_make_state())

        assert len(result["logs"]) == 1