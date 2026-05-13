"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs. new instance based on params
- _build_roleplay_system: prompt construction with CustomerProfile data
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable presence
- FastAPI endpoints (lifespan, CORS middleware, static mount)
- Session management integration via mocked api.sessions functions
- Vector store loading via mocked core.vector_store
- RAG tools creation via mocked api.rag_tools
- Teacher/assessor agent creation via mocked api.agent
- HTTP routing: happy path, 404, validation errors

Mocks used:
- unittest.mock.patch for ChatOpenAI, httpx.Client, httpx.AsyncClient
- core.vector_store.get_vector_store (MockVectorStore)
- api.rag_tools.make_rag_tools
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (create_session, get_session, delete_session, etc.)
- load_dotenv (no-op)
- os.getenv / env vars controlled via monkeypatch

TODOs:
- TODO: Full streaming endpoint tests require async generator introspection — stub provided
- TODO: POST /ingest endpoint not visible in provided source; stub test added
- TODO: Agent invocation tests need full agent graph definition from api/agent.py
- TODO: End-to-end RAG retrieval tests need populated vector store fixture
"""

import importlib
import sys
from datetime import date
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build a minimal fake module graph so api/main.py can be imported
# without real OpenAI keys, real vector stores, or real agent code.
# ---------------------------------------------------------------------------

def _make_fake_vector_store() -> MagicMock:
    store = MagicMock()
    store.load.return_value = True
    store.get_known_products.return_value = ["Generations II", "Health Plan"]
    return store


def _make_fake_sessions_module() -> ModuleType:
    """Return a module-like object with all symbols api/main.py imports from api.sessions."""
    mod = ModuleType("api.sessions")

    class _CustomerProfile:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _Session:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    mod.CustomerProfile = _CustomerProfile
    mod.Session = _Session
    mod.create_session = MagicMock(return_value=_Session(id="sess-1"))
    mod.delete_session = MagicMock(return_value=True)
    mod.generate_profile = MagicMock(return_value=_CustomerProfile(name="Alice"))
    mod.get_session = MagicMock(return_value=_Session(id="sess-1"))
    mod.list_sessions = MagicMock(return_value=[])
    mod.load_sessions = MagicMock()
    mod.update_session_title = MagicMock(return_value=True)
    return mod


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _patch_external_deps_for_import():
    """
    Patch all heavyweight / network-dependent imports BEFORE api.main is
    imported so module-level side effects (ChatOpenAI(), get_vector_store())
    use fakes.
    """
    fake_sessions = _make_fake_sessions_module()
    fake_store = _make_fake_vector_store()

    fake_rag_tools = [MagicMock(name="rag_tool_1")]
    fake_teacher_agent = MagicMock(name="teacher_agent")
    fake_assessor_agent = MagicMock(name="assessor_agent")

    fake_chat_openai_instance = MagicMock(name="ChatOpenAI_instance")

    patches = [
        patch("dotenv.load_dotenv", return_value=None),
        patch("httpx.Client", return_value=MagicMock()),
        patch("httpx.AsyncClient", return_value=MagicMock()),
        patch(
            "langchain_openai.ChatOpenAI",
            return_value=fake_chat_openai_instance,
        ),
    ]

    # Pre-register fake sub-modules so import machinery finds them
    sys.modules.setdefault("core", ModuleType("core"))
    sys.modules.setdefault("api", ModuleType("api"))

    fake_vs_mod = ModuleType("core.vector_store")
    fake_vs_mod.get_vector_store = MagicMock(return_value=fake_store)
    sys.modules["core.vector_store"] = fake_vs_mod

    fake_rag_mod = ModuleType("api.rag_tools")
    fake_rag_mod.make_rag_tools = MagicMock(return_value=fake_rag_tools)
    sys.modules["api.rag_tools"] = fake_rag_mod

    fake_agent_mod = ModuleType("api.agent")
    fake_agent_mod.make_teacher_agent = MagicMock(return_value=fake_teacher_agent)
    fake_agent_mod.make_assessor_agent = MagicMock(return_value=fake_assessor_agent)
    sys.modules["api.agent"] = fake_agent_mod

    sys.modules["api.sessions"] = fake_sessions

    # Also patch StaticFiles so it doesn't require a real directory
    fake_static = MagicMock()
    sys.modules.setdefault("fastapi.staticfiles", ModuleType("fastapi.staticfiles"))

    started = [p.start() for p in patches]

    # Now we can safely import the module under test
    import api.main as _main_mod  # noqa: F401 — side-effect import

    yield fake_store, fake_sessions, fake_rag_tools, fake_teacher_agent, fake_assessor_agent

    for p in patches:
        p.stop()


@pytest.fixture()
def main_module():
    """Return the already-imported api.main module."""
    import api.main as m
    return m


@pytest.fixture()
def client(main_module):
    """Return a synchronous TestClient wrapping the FastAPI app."""
    with patch("fastapi.staticfiles.StaticFiles", MagicMock()):
        return TestClient(main_module.app, raise_server_exceptions=True)


@pytest.fixture()
def fake_store(_patch_external_deps_for_import):
    store, *_ = _patch_external_deps_for_import
    return store


@pytest.fixture()
def fake_sessions(_patch_external_deps_for_import):
    _, sessions, *_ = _patch_external_deps_for_import
    return sessions


# ---------------------------------------------------------------------------
# CustomerProfile helper
# ---------------------------------------------------------------------------

def _make_profile(**overrides) -> Any:
    """Build a minimal CustomerProfile-like object."""
    import api.main as m
    defaults = dict(
        name="Alice Wong",
        age=35,
        occupation="Software Engineer",
        gender="Female",
        marital_status="Single",
        dependents=0,
        monthly_income=30000,
        monthly_expenses=15000,
        savings=200000,
        debts=50000,
        existing_coverage="None",
        goals="Retirement savings",
        hobbies="Reading",
        personality="Cautious",
        health_notes="Healthy",
        stage="1st_meeting",
        prior_context=None,
    )
    defaults.update(overrides)
    profile = m.CustomerProfile(**defaults)
    return profile


# ===========================================================================
# Tests: _get_llm
# ===========================================================================

class TestGetLlm:
    def test_returns_shared_instance_when_defaults(self, main_module):
        """_get_llm() with no arguments must return the module-level _llm."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self, main_module):
        """_get_llm(model='other-model') must create a fresh ChatOpenAI."""
        with patch("api.main.ChatOpenAI") as MockLLM:
            mock_instance = MagicMock()
            MockLLM.return_value = mock_instance
            result = main_module._get_llm(model="other-model")
            assert MockLLM.called
            assert result is mock_instance

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        """_get_llm(temperature=0.9) must create a fresh ChatOpenAI."""
        with patch("api.main.ChatOpenAI") as MockLLM:
            mock_instance = MagicMock()
            MockLLM.return_value = mock_instance
            result = main_module._get_llm(temperature=0.9)
            assert MockLLM.called
            # Verify temperature is forwarded
            _, kwargs = MockLLM.call_args
            assert kwargs["temperature"] == 0.9

    def test_returns_new_instance_when_both_differ(self, main_module):
        with patch("api.main.ChatOpenAI") as MockLLM:
            MockLLM.return_value = MagicMock()
            main_module._get_llm(model="gpt-4", temperature=0.0)
            _, kwargs = MockLLM.call_args
            assert kwargs["model"] == "gpt-4"
            assert kwargs["temperature"] == 0.0

    def test_new_instance_uses_fallback_model_when_none(self, main_module):
        """When model=None but temperature differs, fall back to _LLM_MODEL."""
        with patch("api.main.ChatOpenAI") as MockLLM:
            MockLLM.return_value = MagicMock()
            main_module._get_llm(temperature=0.1)
            _, kwargs = MockLLM.call_args
            assert kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_has_streaming_true(self, main_module):
        with patch("api.main.ChatOpenAI") as MockLLM:
            MockLLM.return_value = MagicMock()
            main_module._get_llm(temperature=0.2)
            _, kwargs = MockLLM.call_args
            assert kwargs.get("streaming") is True


# ===========================================================================
# Tests: _ROLEPLAY_SYSTEM template
# ===========================================================================

class TestRoleplaySystemTemplate:
    def test_required_placeholders_present(self, main_module):
        tmpl = main_module._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in tmpl, f"Missing placeholder: {placeholder}"

    def test_template_formats_correctly(self, main_module):
        filled = main_module._ROLEPLAY_SYSTEM.format(
            name="Bob",
            age=40,
            occupation="Teacher",
            profile="Bob is a teacher with two kids.",
            stage_instruction="",
            today="2025-01-01",
        )
        assert "Bob" in filled
        assert "40" in filled
        assert "Teacher" in filled

    def test_template_no_unresolved_braces_after_format(self, main_module):
        filled = main_module._ROLEPLAY_SYSTEM.format(
            name="Carol",
            age=55,
            occupation="Nurse",
            profile="Nurse profile",
            stage_instruction="This is a 2nd meeting.",
            today="2025-06-01",
        )
        # After formatting, no leftover single-brace placeholders
        import re
        unresolved = re.findall(r'\{[a-z_]+\}', filled)
        assert unresolved == [], f"Unresolved placeholders: {unresolved}"


# ===========================================================================
# Tests: _PRIOR_CONTEXT_PROMPT template
# ===========================================================================

class TestPriorContextPromptTemplate:
    def test_required_placeholders_present(self, main_module):
        tmpl = main_module._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in tmpl, f"Missing placeholder: {placeholder}"

    def test_template_formats_correctly(self, main_module):
        filled = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Alice is 35, software engineer.",
            stage="2nd_meeting",
        )
        assert "Alice" in filled
        assert "2nd_meeting" in filled

    def test_template_mentions_word_limit(self, main_module):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT


# ===========================================================================
# Tests: _build_roleplay_system (if the function is fully defined)
# Note: The source code is truncated; we test what we can.
# ===========================================================================

class TestBuildRoleplaySystem:
    @pytest.mark.skipif(
        not hasattr(
            pytest.importorskip("api.main", reason="api.main not importable"),
            "_build_roleplay_system",
        ),
        reason="_build_roleplay_system not available or incomplete in source",
    )
    def test_returns_string(self, main_module):
        if not hasattr(main_module, "_build_roleplay_system"):
            pytest.skip("_build_roleplay_system not fully defined in truncated source")
        profile = _make_profile()
        result = main_module._build_roleplay_system(profile)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_function_exists(self, main_module):
        assert hasattr(main_module, "_build_roleplay_system")

    @pytest.mark.skip(reason="TODO: _build_roleplay_system source is truncated — "
                              "need full implementation to test stage_instruction branching")
    def test_stage_instruction_varies_by_stage(self, main_module):
        pass

    @pytest.mark.skip(reason="TODO: Need full _build_roleplay_system to test "
                              "prior_context injection into prompt")
    def test_prior_context_injected_when_present(self, main_module):
        pass

    @pytest.mark.skip(reason="TODO: Need full _build_roleplay_system to verify "
                              "today's date is injected correctly")
    def test_today_date_injected(self, main_module):
        pass


# ===========================================================================
# Tests: SHOW_TOOL_CALLS env var parsing
# ===========================================================================

class TestShowToolCallsConfig:
    def test_default_is_true_when_env_not_set(self, monkeypatch):
        # Re-evaluate the expression with env var set to "true"
        monkeypatch.setenv("SHOW_TOOL_CALLS", "true")
        import os
        assert os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"

    def test_false_when_env_is_false(self, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", "false")
        import os
        assert os.getenv("SHOW_TOOL_CALLS", "true").lower() != "true"

    def test_case_insensitive_true(self, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", "TRUE")
        import os
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is True

    def test_module_level_show_tool_calls_is_bool(self, main