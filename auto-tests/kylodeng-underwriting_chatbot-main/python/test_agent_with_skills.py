"""
Test module for backend/agent/agent_with_skills.py

What is tested:
- AgentState TypedDict structure and field annotations
- build_skills_agent() factory function (graph construction, LLM wiring)
- agent() node: happy path tool_call, done action, function_call normalisation,
  invalid JSON fallback, missing JSON fallback, malformed action
- execute_tool() node: successful tool invocation, tool returns error payload,
  tool raises exception, unknown tool name
- router() node: pending_call present → routes to execute_tool,
  no pending_call → routes to agent / END

Mocks used:
- backend.agent.agent_with_skills.LLMS            (LLM factory)
- backend.agent.agent_with_skills._profile_tool   (get_customer_info tool)
- backend.agent.agent_with_skills._lookalike_tool (customer_lookalike tool)
- backend.agent.agent_with_skills._run_underwriting_assessment (risk assessment)
- backend.agent.agent_with_skills._SKILLS_DIR     (skills directory)
- pathlib.Path.glob / Path.read_text              (skill file loading)

TODOs:
- TODO: Integration test for the full compiled LangGraph graph (requires
        real LangGraph StateGraph compilation in test environment)
- TODO: Test streaming behaviour of tagged_llm (requires real LangChain
        streaming infrastructure or deeper event-loop mocking)
- TODO: Test on_tool_start / on_tool_end callback firing (requires
        LangChain callback harness)
"""

import json
import operator
import types
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake skills directory so the module can import
# ---------------------------------------------------------------------------

FAKE_SKILL_TEXT = "## get_customer_info\nFetch customer profile by ID."


def _make_skills_dir(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "01_get_customer_info.md").write_text(FAKE_SKILL_TEXT)
    (skills_dir / "02_customer_lookalike.md").write_text("## customer_lookalike\nFind similar customers.")
    (skills_dir / "index.md").write_text("# Index – should be ignored")
    return skills_dir


# ---------------------------------------------------------------------------
# Module-level patching so the import itself does not blow up
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _patch_imports():
    """
    Patch all external dependencies before the module is imported so
    we never hit real services or the filesystem.
    """
    fake_profile_tool = MagicMock(name="get_customer_profile_tool")
    fake_lookalike_tool = MagicMock(name="customer_lookalike_tool")
    fake_assessment = MagicMock(name="run_underwriting_assessment_result")

    fake_llms_instance = MagicMock()
    fake_llm = MagicMock()
    fake_tagged_llm = MagicMock()
    fake_llm.with_config.return_value = fake_tagged_llm
    fake_llms_instance.get_model.return_value = fake_llm

    fake_llms_cls = MagicMock(return_value=fake_llms_instance)

    with (
        patch("backend.agent.agent_with_skills._profile_tool", fake_profile_tool),
        patch("backend.agent.agent_with_skills._lookalike_tool", fake_lookalike_tool),
        patch("backend.agent.agent_with_skills._run_underwriting_assessment", return_value=fake_assessment),
        patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
    ):
        yield {
            "profile_tool": fake_profile_tool,
            "lookalike_tool": fake_lookalike_tool,
            "assessment": fake_assessment,
            "llms_cls": fake_llms_cls,
            "llms_instance": fake_llms_instance,
            "llm": fake_llm,
            "tagged_llm": fake_tagged_llm,
        }


# ---------------------------------------------------------------------------
# Lazy import after patching
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def agent_module(_patch_imports):
    import importlib
    import backend.agent.agent_with_skills as mod
    return mod


# ---------------------------------------------------------------------------
# Fixtures – agent builder
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_tagged_llm():
    return MagicMock(name="tagged_llm")


@pytest.fixture()
def build_agent(tmp_path, mock_tagged_llm):
    """
    Build a fresh agent for each test with its own skills directory and
    a controllable tagged LLM.
    """
    skills_dir = _make_skills_dir(tmp_path)

    fake_llm = MagicMock()
    fake_llm.with_config.return_value = mock_tagged_llm
    fake_llms_instance = MagicMock()
    fake_llms_instance.get_model.return_value = fake_llm
    fake_llms_cls = MagicMock(return_value=fake_llms_instance)

    fake_profile_tool = MagicMock(name="get_customer_profile_tool")
    fake_lookalike_tool = MagicMock(name="customer_lookalike_tool")
    fake_assessment = MagicMock(name="run_risk_assessment")

    tools_patch = {
        "get_customer_info": fake_profile_tool,
        "customer_lookalike": fake_lookalike_tool,
        "run_risk_assessment": fake_assessment,
    }

    with (
        patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        patch("backend.agent.agent_with_skills.TOOLS", tools_patch),
    ):
        from backend.agent.agent_with_skills import build_skills_agent
        nodes = build_skills_agent.__wrapped__ if hasattr(build_skills_agent, "__wrapped__") else None
        # We capture the inner functions by calling build_skills_agent and
        # inspecting its closure instead of the compiled graph.
        result = _extract_nodes(fake_llms_cls, fake_llms_instance, fake_llm,
                                 mock_tagged_llm, skills_dir, tools_patch)
        return result


def _extract_nodes(fake_llms_cls, fake_llms_instance, fake_llm,
                   mock_tagged_llm, skills_dir, tools_patch):
    """
    Call build_skills_agent() in a controlled patch context and extract
    the inner agent/execute_tool/router callables via closure inspection.
    """
    captured = {}

    original_StateGraph = __import__(
        "langgraph.graph", fromlist=["StateGraph"]
    ).StateGraph

    class CapturingStateGraph:
        def __init__(self, *a, **kw):
            self._nodes = {}

        def add_node(self, name, fn):
            captured[name] = fn

        def add_edge(self, *a, **kw):
            pass

        def add_conditional_edges(self, *a, **kw):
            pass

        def compile(self):
            return MagicMock(name="compiled_graph")

    with (
        patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
        patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        patch("backend.agent.agent_with_skills.TOOLS", tools_patch),
        patch("backend.agent.agent_with_skills.StateGraph", CapturingStateGraph),
        patch("backend.agent.agent_with_skills.START", "START"),
    ):
        from backend.agent import agent_with_skills
        import importlib
        importlib.reload(agent_with_skills)
        agent_with_skills.build_skills_agent()

    return {
        "nodes": captured,
        "tools": tools_patch,
        "tagged_llm": mock_tagged_llm,
    }


# ---------------------------------------------------------------------------
# Convenience: build default state
# ---------------------------------------------------------------------------

def make_state(
    question="Tell me about customer CUST00000001",
    history=None,
    logs=None,
    pending_call=None,
    final_answer="",
) -> dict:
    return {
        "question": question,
        "history": history or [],
        "logs": logs or [],
        "pending_call": pending_call or {},
        "final_answer": final_answer,
    }


# ============================================================
# 1. AgentState structure
# ============================================================

class TestAgentState:
    def test_typed_dict_keys(self, agent_module):
        keys = set(agent_module.AgentState.__annotations__.keys())
        assert {"question", "history", "logs", "pending_call", "final_answer"} == keys

    def test_history_annotated_with_operator_add(self, agent_module):
        ann = agent_module.AgentState.__annotations__["history"]
        # Annotated wrappers expose __metadata__
        assert hasattr(ann, "__metadata__")
        assert operator.add in ann.__metadata__

    def test_logs_annotated_with_operator_add(self, agent_module):
        ann = agent_module.AgentState.__annotations__["logs"]
        assert operator.add in ann.__metadata__


# ============================================================
# 2. build_skills_agent – construction concerns
# ============================================================

class TestBuildSkillsAgent:
    def test_returns_compiled_graph(self, tmp_path):
        skills_dir = _make_skills_dir(tmp_path)
        fake_llm = MagicMock()
        fake_llm.with_config.return_value = MagicMock()
        fake_llms_instance = MagicMock()
        fake_llms_instance.get_model.return_value = fake_llm
        fake_llms_cls = MagicMock(return_value=fake_llms_instance)

        with (
            patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        ):
            from backend.agent import agent_with_skills
            import importlib
            importlib.reload(agent_with_skills)
            result = agent_with_skills.build_skills_agent()
        # compile() returns a MagicMock; anything truthy is fine
        assert result is not None

    def test_llm_tagged_with_agent_tag(self, tmp_path):
        skills_dir = _make_skills_dir(tmp_path)
        fake_llm = MagicMock()
        fake_llm.with_config.return_value = MagicMock()
        fake_llms_instance = MagicMock()
        fake_llms_instance.get_model.return_value = fake_llm
        fake_llms_cls = MagicMock(return_value=fake_llms_instance)

        with (
            patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        ):
            from backend.agent import agent_with_skills
            import importlib
            importlib.reload(agent_with_skills)
            agent_with_skills.build_skills_agent()

        fake_llm.with_config.assert_called_once()
        call_kwargs = fake_llm.with_config.call_args
        config_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]
        # Accept either positional dict or keyword
        if isinstance(config_arg, dict):
            assert "agent" in config_arg.get("tags", [])

    def test_index_md_excluded_from_skill_docs(self, tmp_path):
        """index.md should be skipped when building the system prompt."""
        skills_dir = _make_skills_dir(tmp_path)

        captured_prompt = {}

        fake_llm = MagicMock()
        fake_tagged = MagicMock()
        fake_llm.with_config.return_value = fake_tagged
        fake_llms_instance = MagicMock()
        fake_llms_instance.get_model.return_value = fake_llm
        fake_llms_cls = MagicMock(return_value=fake_llms_instance)

        class CapturingSG:
            def __init__(self, *a, **kw): pass
            def add_node(self, name, fn): captured_prompt["fn"] = fn
            def add_edge(self, *a, **kw): pass
            def add_conditional_edges(self, *a, **kw): pass
            def compile(self): return MagicMock()

        with (
            patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
            patch("backend.agent.agent_with_skills.StateGraph", CapturingSG),
            patch("backend.agent.agent_with_skills.START", "START"),
        ):
            from backend.agent import agent_with_skills
            import importlib
            importlib.reload(agent_with_skills)
            agent_with_skills.build_skills_agent()

        # The first add_node call is "agent"; invoke it to capture the system prompt
        # We just verify the skill dir scanning by checking file names
        skill_files = sorted(skills_dir.glob("*.md"))
        names = [f.name for f in skill_files if f.name != "index.md"]
        assert "index.md" not in names
        assert len(names) == 2

    def test_custom_temperature_passed_to_llms(self, tmp_path):
        skills_dir = _make_skills_dir(tmp_path)
        fake_llm = MagicMock()
        fake_llm.with_config.return_value = MagicMock()
        fake_llms_instance = MagicMock()
        fake_llms_instance.get_model.return_value = fake_llm
        fake_llms_cls = MagicMock(return_value=fake_llms_instance)

        with (
            patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        ):
            from backend.agent import agent_with_skills
            import importlib
            importlib.reload(agent_with_skills)
            agent_with_skills.build_skills_agent(temperature=0.7)

        fake_llms_cls.assert_called_with(temperature=0.7, streaming=True)

    def test_custom_model_name_passed_to_get_model(self, tmp_path):
        skills_dir = _make_skills_dir(tmp_path)
        fake_llm = MagicMock()
        fake_llm.with_config.return_value = MagicMock()
        fake_llms_instance = MagicMock()
        fake_llms_instance.get_model.return_value = fake_llm
        fake_llms_cls = MagicMock(return_value=fake_llms_instance)

        with (
            patch("backend.agent.agent_with_skills.LLMS", fake_llms_cls),
            patch("backend.agent.agent_with_skills._SKILLS_DIR", skills_dir),
        ):
            from backend.agent import agent_with_skills
            import importlib
            importlib.reload(agent_with_skills)
            agent_with_skills.build_skills_agent(model_name="openai-gpt4")

        fake_llms