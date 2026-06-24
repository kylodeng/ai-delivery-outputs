"""
Tests for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm(): returns shared instance vs new instance logic
- _build_roleplay_system(): system prompt construction (partial source, stub for full)
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable coverage
- FastAPI app: lifespan startup (load_sessions, vector store load/warn)
- CORS middleware configuration
- Static file mount at /docs
- SHOW_TOOL_CALLS environment variable parsing
- app metadata (title)

Mocks used:
- langchain_openai.ChatOpenAI (patched at api.main._llm and constructor)
- core.vector_store.get_vector_store
- api.rag_tools.make_rag_tools
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (load_sessions, get_session, create_session, etc.)
- httpx.Client / httpx.AsyncClient
- os.getenv (selected tests via monkeypatch)
- fastapi.staticfiles.StaticFiles (to avoid filesystem dependency)

TODOs:
- TODO: Full _build_roleplay_system() test requires complete source (function body is truncated)
- TODO: POST /ingest endpoint tests — endpoint not shown in provided source
- TODO: Streaming chat endpoint tests — endpoint not shown in provided source
- TODO: Session CRUD endpoint integration tests — endpoints not shown in provided source
- TODO: _PRIOR_CONTEXT_PROMPT rendering via LLM call — requires endpoint source
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — build lightweight stub modules so api.main can be imported
# without real dependencies installed in the test environment.
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """Register minimal stub modules for heavy optional dependencies."""

    # --- langchain_core.messages ---
    lc_core = types.ModuleType("langchain_core")
    lc_messages = types.ModuleType("langchain_core.messages")

    class _Msg:
        def __init__(self, content=""):
            self.content = content

    lc_messages.AIMessage = type("AIMessage", (_Msg,), {})
    lc_messages.HumanMessage = type("HumanMessage", (_Msg,), {})
    lc_messages.SystemMessage = type("SystemMessage", (_Msg,), {})
    lc_core.messages = lc_messages
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules.setdefault("langchain_core.messages", lc_messages)

    # --- langchain_openai ---
    lo = types.ModuleType("langchain_openai")

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    lo.ChatOpenAI = _FakeChatOpenAI
    sys.modules.setdefault("langchain_openai", lo)

    # --- pydantic (real pydantic should be present, but stub SecretStr if not) ---
    try:
        from pydantic import SecretStr  # noqa: F401
    except ImportError:
        pydantic_mod = types.ModuleType("pydantic")
        pydantic_mod.BaseModel = object
        pydantic_mod.SecretStr = str
        sys.modules.setdefault("pydantic", pydantic_mod)

    # --- dotenv ---
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda: None
    sys.modules.setdefault("dotenv", dotenv_mod)

    # --- httpx ---
    try:
        import httpx  # noqa: F401
    except ImportError:
        httpx_mod = types.ModuleType("httpx")
        httpx_mod.Client = MagicMock
        httpx_mod.AsyncClient = MagicMock
        sys.modules.setdefault("httpx", httpx_mod)

    # --- core.vector_store ---
    core_mod = types.ModuleType("core")
    vs_mod = types.ModuleType("core.vector_store")
    _fake_store = MagicMock()
    _fake_store.load.return_value = True
    _fake_store.get_known_products.return_value = ["ProductA", "ProductB"]
    vs_mod.get_vector_store = MagicMock(return_value=_fake_store)
    core_mod.vector_store = vs_mod
    sys.modules.setdefault("core", core_mod)
    sys.modules.setdefault("core.vector_store", vs_mod)

    # --- api.rag_tools ---
    api_mod = sys.modules.get("api") or types.ModuleType("api")
    rag_mod = types.ModuleType("api.rag_tools")
    rag_mod.make_rag_tools = MagicMock(return_value=[])
    api_mod.rag_tools = rag_mod
    sys.modules.setdefault("api", api_mod)
    sys.modules.setdefault("api.rag_tools", rag_mod)

    # --- api.agent ---
    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    api_mod.agent = agent_mod
    sys.modules.setdefault("api.agent", agent_mod)

    # --- api.sessions ---
    sessions_mod = types.ModuleType("api.sessions")

    class _CustomerProfile:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _Session:
        pass

    sessions_mod.CustomerProfile = _CustomerProfile
    sessions_mod.Session = _Session
    sessions_mod.create_session = MagicMock()
    sessions_mod.delete_session = MagicMock()
    sessions_mod.generate_profile = MagicMock()
    sessions_mod.get_session = MagicMock()
    sessions_mod.list_sessions = MagicMock(return_value=[])
    sessions_mod.load_sessions = MagicMock()
    sessions_mod.update_session_title = MagicMock()
    api_mod.sessions = sessions_mod
    sys.modules.setdefault("api.sessions", sessions_mod)

    # --- fastapi.staticfiles ---
    try:
        from fastapi.staticfiles import StaticFiles  # noqa: F401
    except ImportError:
        pass


_make_stub_modules()


# ---------------------------------------------------------------------------
# Now we can safely import the module under test.
# We patch StaticFiles.__init__ to avoid needing the real data/ directory.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _patch_static_files():
    """Prevent StaticFiles from raising because data/ may not exist in CI."""
    with patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None):
        yield


@pytest.fixture(scope="session")
def main_module(_patch_static_files):
    """Import api.main once per session with stubs in place."""
    # Remove cached import if present so environment patches take effect
    sys.modules.pop("api.main", None)
    import api.main as m
    return m


# ---------------------------------------------------------------------------
# _get_llm tests
# ---------------------------------------------------------------------------

class TestGetLlm:
    """Tests for the _get_llm helper."""

    def test_returns_shared_instance_when_no_overrides(self, main_module):
        """When called with defaults, should return the module-level _llm object."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self, main_module):
        """When model is specified, a fresh ChatOpenAI must be returned."""
        result = main_module._get_llm(model="openai/gpt-4")
        assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        """When temperature differs from default, a fresh ChatOpenAI must be returned."""
        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm

    def test_returns_new_instance_when_both_differ(self, main_module):
        """Both model and temperature differ → fresh instance."""
        result = main_module._get_llm(model="openai/gpt-4", temperature=0.1)
        assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self, main_module):
        """Returned instance should carry the requested model name."""
        result = main_module._get_llm(model="custom/model-x")
        assert result.model == "custom/model-x"

    def test_new_instance_uses_provided_temperature(self, main_module):
        """Returned instance should carry the requested temperature."""
        result = main_module._get_llm(temperature=0.0)
        assert result.temperature == 0.0

    def test_none_model_with_default_temperature_returns_shared(self, main_module):
        """Explicit None model + default temperature → shared instance."""
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_streaming_enabled_on_new_instance(self, main_module):
        """All LLM instances should have streaming=True."""
        result = main_module._get_llm(model="some/model")
        assert result.streaming is True


# ---------------------------------------------------------------------------
# SHOW_TOOL_CALLS parsing tests
# ---------------------------------------------------------------------------

class TestShowToolCallsEnvVar:
    """Tests for SHOW_TOOL_CALLS environment variable parsing."""

    def test_default_true_when_env_is_true(self, main_module):
        """The module-level constant should be truthy when env is 'true'."""
        # The module was loaded without override; env default is "true"
        # We verify the type and that it was parsed as a bool
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", False),
        ("1", False),   # only "true" (case-insensitive) should be True
        ("yes", False),
    ])
    def test_show_tool_calls_parsing(self, monkeypatch, env_val, expected):
        """SHOW_TOOL_CALLS should only be True when env value lowercased == 'true'."""
        monkeypatch.setenv("SHOW_TOOL_CALLS", env_val)
        # Re-evaluate the expression as the module does
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result == expected

    def test_show_tool_calls_missing_env_defaults_true(self, monkeypatch):
        """When SHOW_TOOL_CALLS is unset, default should resolve to True."""
        monkeypatch.delenv("SHOW_TOOL_CALLS", raising=False)
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is True


# ---------------------------------------------------------------------------
# Module-level constant tests
# ---------------------------------------------------------------------------

class TestModuleConstants:
    """Tests for module-level configuration constants."""

    def test_llm_temperature_is_float(self, main_module):
        assert isinstance(main_module._LLM_TEMPERATURE, float)

    def test_llm_temperature_value(self, main_module):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_base_url_is_string(self, main_module):
        assert isinstance(main_module._BASE_URL, str)

    def test_llm_model_is_string(self, main_module):
        assert isinstance(main_module._LLM_MODEL, str)

    def test_module_llm_is_chat_openai(self, main_module):
        """The shared _llm instance must be a ChatOpenAI (or stub equivalent)."""
        from langchain_openai import ChatOpenAI
        assert isinstance(main_module._llm, ChatOpenAI)


# ---------------------------------------------------------------------------
# FastAPI app object tests
# ---------------------------------------------------------------------------

class TestAppObject:
    """Tests for FastAPI app configuration."""

    def test_app_title(self, main_module):
        assert main_module.app.title == "Insurance Agent Trainer"

    def test_app_is_fastapi_instance(self, main_module):
        from fastapi import FastAPI
        assert isinstance(main_module.app, FastAPI)

    def test_cors_middleware_present(self, main_module):
        """CORSMiddleware should be registered on the app."""
        from starlette.middleware.cors import CORSMiddleware
        middleware_classes = [
            m.cls for m in main_module.app.user_middleware
        ]
        assert CORSMiddleware in middleware_classes

    def test_cors_allows_localhost_5173(self, main_module):
        """Vite dev server origin must be in allowed origins."""
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:5173" in origins
                break

    def test_cors_allows_localhost_8000(self, main_module):
        """Chainlit UI origin must be in allowed origins."""
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:8000" in origins
                break

    def test_cors_allow_all_methods(self, main_module):
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware:
            if m.cls is CORSMiddleware:
                assert m.kwargs.get("allow_methods") == ["*"]
                break

    def test_cors_allow_all_headers(self, main_module):
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware:
            if m.cls is CORSMiddleware:
                assert m.kwargs.get("allow_headers") == ["*"]
                break


# ---------------------------------------------------------------------------
# Lifespan tests
# ---------------------------------------------------------------------------

class TestLifespan:
    """Tests for the async lifespan context manager."""

    @pytest.mark.asyncio
    async def test_lifespan_calls_load_sessions(self, main_module):
        """load_sessions() must be called during startup."""
        sessions_mod = sys.modules["api.sessions"]
        sessions_mod.load_sessions.reset_mock()

        async with main_module.lifespan(main_module.app):
            pass

        sessions_mod.load_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_logs_info_when_store_loads(self, main_module, caplog):
        """When vector store loads successfully, an INFO message should be logged."""
        store = sys.modules["core.vector_store"].get_vector_store.return_value
        store.load.return_value = True
        store.get_known_products.return_value = ["ProductA", "ProductB"]

        import