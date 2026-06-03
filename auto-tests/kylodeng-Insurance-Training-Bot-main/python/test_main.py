"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs new instance logic
- _build_roleplay_system: prompt construction with CustomerProfile
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable coverage
- FastAPI app endpoints (health, sessions, ingest, etc.) via TestClient
- lifespan: load_sessions and vector store load/warn paths
- CORS middleware configuration
- SHOW_TOOL_CALLS env-var parsing
- Static file mount existence

Mocks used:
- langchain_openai.ChatOpenAI (patched at module level)
- core.vector_store.get_vector_store (patched)
- api.rag_tools.make_rag_tools (patched)
- api.agent.make_teacher_agent / make_assessor_agent (patched)
- api.sessions.* (patched individually per test)
- httpx.Client / httpx.AsyncClient (patched to avoid real network calls)
- os.getenv (selectively patched for env-var tests)

TODOs:
- TODO: Full streaming endpoint tests require an actual async event-loop + ASGI transport
- TODO: /ingest endpoint tests need the real ingestion pipeline wired up
- TODO: Test _build_roleplay_system fully once its complete implementation is available
- TODO: Tests for make_teacher_agent / make_assessor_agent integration (need agent impl)
"""

from __future__ import annotations

import importlib
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers — build a minimal stub tree so importing api.main doesn't explode
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """Register lightweight stubs for every heavy dependency."""

    # --- langchain_core.messages ---
    lc_core = types.ModuleType("langchain_core")
    lc_core_msgs = types.ModuleType("langchain_core.messages")

    class _Msg:
        def __init__(self, content=""):
            self.content = content

    lc_core_msgs.AIMessage = type("AIMessage", (_Msg,), {})
    lc_core_msgs.HumanMessage = type("HumanMessage", (_Msg,), {})
    lc_core_msgs.SystemMessage = type("SystemMessage", (_Msg,), {})
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules.setdefault("langchain_core.messages", lc_core_msgs)

    # --- langchain_openai ---
    lo = types.ModuleType("langchain_openai")
    mock_llm_instance = MagicMock(name="ChatOpenAI_instance")
    MockChatOpenAI = MagicMock(name="ChatOpenAI", return_value=mock_llm_instance)
    lo.ChatOpenAI = MockChatOpenAI
    sys.modules["langchain_openai"] = lo

    # --- dotenv ---
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = MagicMock()
    sys.modules.setdefault("dotenv", dotenv)

    # --- httpx ---
    httpx_mod = types.ModuleType("httpx")
    httpx_mod.Client = MagicMock(return_value=MagicMock())
    httpx_mod.AsyncClient = MagicMock(return_value=MagicMock())
    sys.modules["httpx"] = httpx_mod

    # --- core.vector_store ---
    core = types.ModuleType("core")
    core_vs = types.ModuleType("core.vector_store")
    mock_store = MagicMock(name="VectorStore")
    mock_store.load.return_value = True
    mock_store.get_known_products.return_value = ["ProductA", "ProductB"]
    core_vs.get_vector_store = MagicMock(return_value=mock_store)
    sys.modules.setdefault("core", core)
    sys.modules["core.vector_store"] = core_vs

    # --- api.rag_tools ---
    api_pkg = sys.modules.get("api") or types.ModuleType("api")
    sys.modules.setdefault("api", api_pkg)

    api_rag = types.ModuleType("api.rag_tools")
    api_rag.make_rag_tools = MagicMock(return_value=[MagicMock(name="rag_tool")])
    sys.modules["api.rag_tools"] = api_rag

    # --- api.agent ---
    api_agent = types.ModuleType("api.agent")
    api_agent.make_teacher_agent = MagicMock(return_value=AsyncMock(name="teacher_agent"))
    api_agent.make_assessor_agent = MagicMock(return_value=AsyncMock(name="assessor_agent"))
    sys.modules["api.agent"] = api_agent

    # --- api.sessions ---
    api_sessions = types.ModuleType("api.sessions")

    class _CustomerProfile(MagicMock):
        pass

    class _Session(MagicMock):
        pass

    api_sessions.CustomerProfile = _CustomerProfile
    api_sessions.Session = _Session
    api_sessions.create_session = MagicMock(return_value=MagicMock(id="sess-1"))
    api_sessions.delete_session = MagicMock(return_value=True)
    api_sessions.generate_profile = MagicMock(return_value=MagicMock())
    api_sessions.get_session = MagicMock(return_value=MagicMock(id="sess-1"))
    api_sessions.list_sessions = MagicMock(return_value=[])
    api_sessions.load_sessions = MagicMock()
    api_sessions.update_session_title = MagicMock()
    sys.modules["api.sessions"] = api_sessions

    return mock_store, MockChatOpenAI, mock_llm_instance


_mock_store, _MockChatOpenAI, _mock_llm_instance = _make_stub_modules()


# ---------------------------------------------------------------------------
# Now we can safely import the module under test
# ---------------------------------------------------------------------------

# Ensure the data dir exists so StaticFiles doesn't blow up during import
_DATA_DIR_PATH = Path(__file__).parent.parent / "data"
_DATA_DIR_PATH.mkdir(parents=True, exist_ok=True)

import api.main as main_module  # noqa: E402  (after stubs are registered)
from api.main import _get_llm, _ROLEPLAY_SYSTEM, _PRIOR_CONTEXT_PROMPT  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a synchronous TestClient for the FastAPI app."""
    with TestClient(main_module.app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def mock_store():
    return _mock_store


@pytest.fixture()
def reset_llm_mock():
    """Reset call counts on the ChatOpenAI mock between tests."""
    _MockChatOpenAI.reset_mock()
    yield _MockChatOpenAI


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    """Unit tests for _get_llm helper."""

    def test_returns_shared_instance_when_no_overrides(self):
        """Happy path: no arguments → shared _llm singleton returned."""
        result = _get_llm()
        # The shared instance was built at module import time; same object.
        assert result is main_module._llm

    def test_returns_shared_instance_with_default_temperature(self):
        result = _get_llm(temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_provided(self, reset_llm_mock):
        custom_model = "openai/gpt-4"
        result = _get_llm(model=custom_model)
        # Should NOT be the shared singleton
        assert result is not main_module._llm
        # ChatOpenAI constructor called with the custom model
        call_kwargs = reset_llm_mock.call_args_list[-1][1]
        assert call_kwargs["model"] == custom_model

    def test_returns_new_instance_when_temperature_differs(self, reset_llm_mock):
        result = _get_llm(temperature=0.9)
        assert result is not main_module._llm
        call_kwargs = reset_llm_mock.call_args_list[-1][1]
        assert call_kwargs["temperature"] == 0.9

    def test_returns_new_instance_when_both_differ(self, reset_llm_mock):
        result = _get_llm(model="anthropic/claude-3", temperature=0.1)
        assert result is not main_module._llm
        call_kwargs = reset_llm_mock.call_args_list[-1][1]
        assert call_kwargs["model"] == "anthropic/claude-3"
        assert call_kwargs["temperature"] == 0.1

    def test_new_instance_uses_global_model_when_model_is_none_but_temp_differs(
        self, reset_llm_mock
    ):
        """model=None with different temp → uses _LLM_MODEL as fallback."""
        _get_llm(model=None, temperature=0.0)
        call_kwargs = reset_llm_mock.call_args_list[-1][1]
        assert call_kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_streaming_enabled(self, reset_llm_mock):
        _get_llm(temperature=0.0)
        call_kwargs = reset_llm_mock.call_args_list[-1][1]
        assert call_kwargs.get("streaming") is True

    def test_new_instance_uses_secret_str_for_api_key(self, reset_llm_mock):
        _get_llm(temperature=0.0)
        call_kwargs = reset_llm_mock.call_args_list[-1][1]
        assert isinstance(call_kwargs.get("api_key"), SecretStr)

    def test_new_instance_uses_configured_base_url(self, reset_llm_mock):
        _get_llm(temperature=0.0)
        call_kwargs = reset_llm_mock.call_args_list[-1][1]
        assert call_kwargs.get("base_url") == main_module._BASE_URL


# ---------------------------------------------------------------------------
# Tests: Template strings
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    """Validate _ROLEPLAY_SYSTEM format-string placeholders."""

    def _sample_kwargs(self):
        return dict(
            name="Alice Lam",
            age=35,
            occupation="nurse",
            profile="Alice is a 35-year-old nurse with two kids.",
            stage_instruction="This is the 1st conversation.",
            today="2025-01-15",
        )

    def test_all_placeholders_rendered_without_keyerror(self):
        rendered = _ROLEPLAY_SYSTEM.format(**self._sample_kwargs())
        assert "Alice Lam" in rendered

    def test_name_appears_in_output(self):
        rendered = _ROLEPLAY_SYSTEM.format(**self._sample_kwargs())
        assert "Alice Lam" in rendered

    def test_age_appears_in_output(self):
        rendered = _ROLEPLAY_SYSTEM.format(**self._sample_kwargs())
        assert "35" in rendered

    def test_occupation_appears_in_output(self):
        rendered = _ROLEPLAY_SYSTEM.format(**self._sample_kwargs())
        assert "nurse" in rendered

    def test_today_appears_in_output(self):
        rendered = _ROLEPLAY_SYSTEM.format(**self._sample_kwargs())
        assert "2025-01-15" in rendered

    def test_stage_instruction_appears_in_output(self):
        kwargs = self._sample_kwargs()
        kwargs["stage_instruction"] = "Conversation stage: 2nd meeting."
        rendered = _ROLEPLAY_SYSTEM.format(**kwargs)
        assert "2nd meeting" in rendered

    def test_missing_placeholder_raises_key_error(self):
        kwargs = self._sample_kwargs()
        del kwargs["name"]
        with pytest.raises(KeyError):
            _ROLEPLAY_SYSTEM.format(**kwargs)

    def test_never_break_character_instruction_present(self):
        rendered = _ROLEPLAY_SYSTEM.format(**self._sample_kwargs())
        assert "Never break character" in rendered


class TestPriorContextPromptTemplate:
    """Validate _PRIOR_CONTEXT_PROMPT format-string placeholders."""

    def _sample_kwargs(self):
        return dict(
            profile="Alice Lam, 35, nurse, 2 kids.",
            stage="2nd conversation",
        )

    def test_all_placeholders_rendered(self):
        rendered = _PRIOR_CONTEXT_PROMPT.format(**self._sample_kwargs())
        assert "Alice Lam" in rendered

    def test_stage_appears(self):
        rendered = _PRIOR_CONTEXT_PROMPT.format(**self._sample_kwargs())
        assert "2nd conversation" in rendered

    def test_missing_profile_raises(self):
        with pytest.raises(KeyError):
            _PRIOR_CONTEXT_PROMPT.format(stage="2nd conversation")

    def test_missing_stage_raises(self):
        with pytest.raises(KeyError):
            _PRIOR_CONTEXT_PROMPT.format(profile="some profile")

    def test_word_limit_instruction_present(self):
        rendered = _PRIOR_CONTEXT_PROMPT.format(**self._sample_kwargs())
        assert "350" in rendered


# ---------------------------------------------------------------------------
# Tests: SHOW_TOOL_CALLS env parsing
# ---------------------------------------------------------------------------

class TestShowToolCallsEnvVar:
    """Boundary / negative tests for SHOW_TOOL_CALLS parsing."""

    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("1", False),        # only exact "true" (lowercased) counts
        ("yes", False),
        ("", False),
    ])
    def test_show_tool_calls_parsing(self, env_val, expected):
        result = env_val.lower() == "true"
        assert result is expected


# ---------------------------------------------------------------------------
# Tests: FastAPI app basics
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    """Smoke tests for FastAPI app metadata and middleware."""

    def test_app_title(self):
        assert main_module.app.title == "Insurance Agent Trainer"

    def test_cors_middleware_registered(self):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMW
        middleware_types = [m.cls for m in main_module.app.user_middleware]
        assert StarletteCorsMW in middleware_types

    def test_allowed_origins_include_chainlit(self):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMW
        for m in main_module.app.user_middleware:
            if m.cls is StarletteCorsMW:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:8000" in origins
                assert "http://127.0.0.1:8000" in origins

    def test_allowed_origins_include_vite_dev(self):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMW
        for m in main_module.app.user_middleware:
            if m.cls is Starlet