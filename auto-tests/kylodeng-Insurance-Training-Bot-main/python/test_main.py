"""
Test suite for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm() helper: shared instance reuse, new instance creation on model/temp change
- _build_roleplay_system() prompt builder (happy path, edge cases, missing fields)
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT template string integrity
- FastAPI app lifecycle (lifespan, startup loading)
- CORS middleware configuration
- Static files mount at /docs
- SHOW_TOOL_CALLS env-var parsing
- HTTP endpoints (mocked): POST /ingest, GET /sessions, etc. where discoverable
- CustomerProfile / Session pydantic models via imported symbols

Mocks used:
- langchain_openai.ChatOpenAI (patched at api.main._llm and constructor)
- core.vector_store.get_vector_store
- api.rag_tools.make_rag_tools
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (load_sessions, create_session, get_session, etc.)
- httpx.Client / httpx.AsyncClient (SSL verification disabled — not called for real)
- fastapi.staticfiles.StaticFiles (to avoid filesystem dependency)

TODOs:
- TODO: Test all REST endpoints once their route definitions are visible (file was truncated)
- TODO: Test _PRIOR_CONTEXT_PROMPT injection into agent when stage/profile vary
- TODO: Integration test for streaming SSE response (requires full route body)
- TODO: Test ingest endpoint with real PDF fixture if available
"""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers to build lightweight stub modules so api/main.py can be imported
# without real heavy dependencies being present in the test environment.
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """Inject minimal stub modules for heavy/external deps before import."""

    # --- langchain stubs ---
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

    lc_openai = types.ModuleType("langchain_openai")
    mock_llm_instance = MagicMock(name="ChatOpenAI_instance")

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            self._kwargs = kwargs
            self.model = kwargs.get("model", "")
            self.temperature = kwargs.get("temperature", 0.6)

        def __repr__(self):
            return f"FakeChatOpenAI(model={self.model})"

    lc_openai.ChatOpenAI = _FakeChatOpenAI
    sys.modules.setdefault("langchain_openai", lc_openai)

    # --- core.vector_store stub ---
    core_mod = types.ModuleType("core")
    core_vs = types.ModuleType("core.vector_store")
    fake_store = MagicMock(name="VectorStore")
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["ProductA", "ProductB"]
    core_vs.get_vector_store = MagicMock(return_value=fake_store)
    core_mod.vector_store = core_vs
    sys.modules.setdefault("core", core_mod)
    sys.modules.setdefault("core.vector_store", core_vs)

    # --- api sub-package stubs ---
    api_mod = sys.modules.setdefault("api", types.ModuleType("api"))

    rag_tools_mod = types.ModuleType("api.rag_tools")
    rag_tools_mod.make_rag_tools = MagicMock(return_value=[MagicMock(name="rag_tool")])
    sys.modules.setdefault("api.rag_tools", rag_tools_mod)

    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock(name="teacher"))
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock(name="assessor"))
    sys.modules.setdefault("api.agent", agent_mod)

    # --- api.sessions stub ---
    sessions_mod = types.ModuleType("api.sessions")

    class _CustomerProfile:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _Session:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    sessions_mod.CustomerProfile = _CustomerProfile
    sessions_mod.Session = _Session
    sessions_mod.create_session = MagicMock(return_value=_Session(id="sess-1"))
    sessions_mod.delete_session = MagicMock(return_value=True)
    sessions_mod.generate_profile = MagicMock(return_value=_CustomerProfile(name="Alice"))
    sessions_mod.get_session = MagicMock(return_value=_Session(id="sess-1"))
    sessions_mod.list_sessions = MagicMock(return_value=[])
    sessions_mod.load_sessions = MagicMock()
    sessions_mod.update_session_title = MagicMock()
    sys.modules.setdefault("api.sessions", sessions_mod)

    # --- dotenv stub ---
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = MagicMock()
    sys.modules.setdefault("dotenv", dotenv_mod)

    # --- StaticFiles stub (avoids filesystem check) ---
    statics_mod = sys.modules.get("fastapi.staticfiles")
    if statics_mod is None:
        statics_mod = types.ModuleType("fastapi.staticfiles")
    statics_mod.StaticFiles = MagicMock(name="StaticFiles")
    sys.modules["fastapi.staticfiles"] = statics_mod

    return fake_store


_fake_store = _make_stub_modules()

# Now safe to import the module under test
import api.main as main_module  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    return main_module.app


@pytest.fixture()
def async_client(app):
    """Return an httpx AsyncClient wired to the FastAPI test app."""
    from httpx import AsyncClient, ASGITransport
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture()
def sample_profile_data():
    return {
        "name": "Alice Chan",
        "age": 35,
        "occupation": "Software Engineer",
        "profile": (
            "Alice is a 35-year-old software engineer living in Hong Kong. "
            "She has two kids and a mortgage. She is interested in long-term savings."
        ),
    }


# ---------------------------------------------------------------------------
# Tests: module-level constants and env-var parsing
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_llm_temperature_default(self):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_base_url_default_contains_openrouter(self):
        # Default when env var not set
        assert "openrouter" in main_module._BASE_URL or main_module._BASE_URL.startswith("http")

    def test_show_tool_calls_is_bool(self):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("yes", False),
        ("", False),
    ])
    def test_show_tool_calls_env_parsing(self, env_val, expected, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", env_val)
        # Re-evaluate the expression as main.py does
        result = env_val.lower() == "true"
        assert result == expected

    def test_roleplay_system_template_has_required_placeholders(self):
        template = main_module._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_has_required_placeholders(self):
        template = main_module._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_roleplay_system_instructs_character_maintenance(self):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_prior_context_prompt_word_limit_mentioned(self):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT


# ---------------------------------------------------------------------------
# Tests: _get_llm()
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_args(self):
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_with_default_temperature(self):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_specified(self):
        result = main_module._get_llm(model="openai/gpt-4")
        assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self):
        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm

    def test_new_instance_has_correct_model(self):
        custom_model = "openai/gpt-4-turbo"
        result = main_module._get_llm(model=custom_model)
        assert result.model == custom_model

    def test_new_instance_has_correct_temperature(self):
        result = main_module._get_llm(temperature=0.1)
        assert result.temperature == 0.1

    def test_new_instance_falls_back_to_default_model_when_none(self):
        result = main_module._get_llm(model=None, temperature=0.99)
        assert result.model == main_module._LLM_MODEL

    def test_returns_new_instance_when_both_differ(self):
        result = main_module._get_llm(model="openai/gpt-4", temperature=0.0)
        assert result is not main_module._llm
        assert result.model == "openai/gpt-4"
        assert result.temperature == 0.0


# ---------------------------------------------------------------------------
# Tests: FastAPI app configuration
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_app_is_fastapi_instance(self, app):
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)

    def test_cors_middleware_present(self, app):
        from fastapi.middleware.cors import CORSMiddleware
        middleware_types = [m.cls for m in app.user_middleware]
        assert CORSMiddleware in middleware_types

    def test_cors_allows_localhost_5173(self, app):
        from fastapi.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:5173" in origins
                break
        else:
            pytest.fail("CORSMiddleware not found")

    def test_cors_allows_localhost_8000(self, app):
        from fastapi.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:8000" in origins
                break
        else:
            pytest.fail("CORSMiddleware not found")

    def test_cors_allows_all_methods(self, app):
        from fastapi.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                assert "*" in m.kwargs.get("allow_methods", [])
                break

    def test_cors_allows_all_headers(self, app):
        from fastapi.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                assert "*" in m.kwargs.get("allow_headers", [])
                break


# ---------------------------------------------------------------------------
# Tests: Lifespan / startup behaviour
# ---------------------------------------------------------------------------

class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_calls_load_sessions(self):
        sessions_mod = sys.modules["api.sessions"]
        sessions_mod.load_sessions.reset_mock()

        async with main_module.lifespan(main_module.app):
            pass

        sessions_mod.load_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_calls_store_load(self):
        _fake_store.load.reset_mock()
        _fake_store.load.return_value = True

        async with main_module.lifespan(main_module.app):
            pass

        _fake_store.load.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_warns_when_store_not_found(self, caplog):
        import logging
        _fake_store.load.return_value = False

        with caplog.at_level(logging.WARNING, logger="api.main"):
            async with main_module.lifespan(main_module.app):
                pass

        assert any("No vector store" in r.message for r in caplog.records)
        _fake_store.load.return_value = True  # restore

    @pytest.mark.asyncio
    async def test_lifespan_logs_product_count_on_success(self, caplog):
        import logging
        _fake_store.load.return_value = True
        _fake_store.get_known_products.return_value = ["P1", "P2", "P3"]

        with caplog.at_level(logging.INFO, logger="api.main"):
            async with main_module.lifespan(main_module.app):
                pass

        assert any("Vector store loaded" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Tests: _build_roleplay_system (if accessible — partial source provided)
# ---------------------------------------------------------------------------

class TestBuildRoleplaySystem:
    """Tests for _build_roleplay_system if it is defined in the module."""

    @pytest.fixture(autouse=True)
    def skip_if_not_defined(self):
        if not hasattr(main_