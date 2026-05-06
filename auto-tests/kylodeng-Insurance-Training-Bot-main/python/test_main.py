"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm() helper: shared instance reuse, new instance creation for custom model/temperature
- _build_roleplay_system() prompt builder
- _ROLEPLAY_SYSTEM and _PRIOR_CONTEXT_PROMPT template strings
- FastAPI app configuration (middleware, mounts, title)
- Lifespan context manager (load_sessions, vector store load/warn paths)
- HTTP endpoints (where discoverable from the source; stubs for endpoints cut off in source)

Mocks used:
- langchain_openai.ChatOpenAI — patched to avoid real LLM calls
- httpx.Client / httpx.AsyncClient — patched at module level
- core.vector_store.get_vector_store — patched to avoid filesystem/DB access
- api.rag_tools.make_rag_tools — patched
- api.agent.make_teacher_agent / make_assessor_agent — patched
- api.sessions.* — patched to avoid filesystem state
- fastapi.staticfiles.StaticFiles — patched to avoid directory-existence check

TODOs:
- TODO: Full endpoint tests for POST /ingest, POST /chat, GET /sessions, etc.
        need request/response schemas not visible in the truncated source.
- TODO: Test _build_roleplay_system() with real CustomerProfile once the
        function body (cut off in source) is available.
- TODO: Test streaming responses end-to-end (requires a running event loop + ASGI transport).
- TODO: Test SHOW_TOOL_CALLS env-var branch interaction with per-session override.
"""

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build lightweight stubs for heavy optional imports
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ---------------------------------------------------------------------------
# Fixtures — patch everything that touches the filesystem, network, or LLM
# before api.main is imported.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _patch_heavy_imports():
    """
    Session-scoped fixture that injects lightweight stubs for all external
    modules so that importing api.main never makes real network / FS calls.
    """
    fake_store = MagicMock()
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["ProductA", "ProductB"]

    fake_vs_mod = _make_stub_module("core.vector_store", get_vector_store=lambda: fake_store)
    fake_rag_mod = _make_stub_module("api.rag_tools", make_rag_tools=lambda store: [])
    fake_agent_mod = _make_stub_module(
        "api.agent",
        make_teacher_agent=MagicMock(return_value=MagicMock()),
        make_assessor_agent=MagicMock(return_value=MagicMock()),
    )

    fake_sessions_mod = _make_stub_module(
        "api.sessions",
        CustomerProfile=MagicMock,
        Session=MagicMock,
        create_session=MagicMock(return_value={"id": "s1"}),
        delete_session=MagicMock(),
        generate_profile=MagicMock(return_value={}),
        get_session=MagicMock(return_value=None),
        list_sessions=MagicMock(return_value=[]),
        load_sessions=MagicMock(),
        update_session_title=MagicMock(),
    )

    stubs = {
        "core": _make_stub_module("core"),
        "core.vector_store": fake_vs_mod,
        "api.rag_tools": fake_rag_mod,
        "api.agent": fake_agent_mod,
        "api.sessions": fake_sessions_mod,
    }

    # Patch ChatOpenAI so no real HTTP client is constructed
    mock_chat_openai = MagicMock()
    mock_chat_openai_instance = MagicMock()
    mock_chat_openai.return_value = mock_chat_openai_instance

    # Patch StaticFiles so it doesn't check for the directory on disk
    mock_static = MagicMock()

    with (
        patch.dict(sys.modules, stubs),
        patch("langchain_openai.ChatOpenAI", mock_chat_openai),
        patch("httpx.Client", MagicMock()),
        patch("httpx.AsyncClient", MagicMock()),
        patch("fastapi.staticfiles.StaticFiles", mock_static),
    ):
        # Remove cached module if a previous test session imported it
        for mod_name in list(sys.modules.keys()):
            if mod_name == "api.main" or mod_name.startswith("api.main."):
                del sys.modules[mod_name]

        yield


# ---------------------------------------------------------------------------
# Import api.main lazily inside tests so the session fixture applies first.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def main_module(_patch_heavy_imports):
    import importlib
    # Ensure a clean import after stubs are in place
    if "api.main" in sys.modules:
        return sys.modules["api.main"]
    import api.main as m
    return m


@pytest.fixture(scope="session")
def app(main_module):
    return main_module.app


@pytest.fixture()
def client(app):
    # Use TestClient without lifespan so we control setup manually
    return TestClient(app, raise_server_exceptions=True)


# ===========================================================================
# 1.  Module-level constants
# ===========================================================================

class TestModuleConstants:
    def test_llm_temperature_default(self, main_module):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_base_url_default(self, main_module):
        assert "openrouter" in main_module._BASE_URL or main_module._BASE_URL.startswith("http")

    def test_llm_model_default(self, main_module):
        assert isinstance(main_module._LLM_MODEL, str)
        assert len(main_module._LLM_MODEL) > 0

    def test_show_tool_calls_is_bool(self, main_module):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_roleplay_system_template_placeholders(self, main_module):
        template = main_module._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in template, f"Missing placeholder {placeholder}"

    def test_prior_context_prompt_placeholders(self, main_module):
        template = main_module._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in template, f"Missing placeholder {placeholder}"

    def test_roleplay_system_contains_character_guidance(self, main_module):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_prior_context_prompt_word_limit(self, main_module):
        assert "350 words" in main_module._PRIOR_CONTEXT_PROMPT


# ===========================================================================
# 2.  _get_llm()
# ===========================================================================

class TestGetLlm:
    """Tests for the _get_llm factory function."""

    def test_returns_shared_instance_when_no_overrides(self, main_module):
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_when_temperature_matches_default(self, main_module):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self, main_module):
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = main_module._get_llm(model="custom-model")
            # Should NOT be the shared instance
            assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            new_instance = MagicMock()
            mock_cls.return_value = new_instance
            result = main_module._get_llm(temperature=0.9)
            assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self, main_module):
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(model="my-special-model")
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["model"] == "my-special-model"

    def test_new_instance_uses_provided_temperature(self, main_module):
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(temperature=0.1)
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["temperature"] == 0.1

    def test_new_instance_falls_back_to_default_model_when_none(self, main_module):
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(model=None, temperature=0.99)
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_has_streaming_true(self, main_module):
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(temperature=0.2)
            call_kwargs = mock_cls.call_args[1]
            assert call_kwargs.get("streaming") is True

    def test_zero_temperature_creates_new_instance(self, main_module):
        """Temperature=0.0 differs from default 0.6, so a new instance must be returned."""
        with patch("langchain_openai.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = main_module._get_llm(temperature=0.0)
            assert result is not main_module._llm


# ===========================================================================
# 3.  FastAPI application configuration
# ===========================================================================

class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, app):
        from starlette.middleware.cors import CORSMiddleware
        middleware_types = [m.cls for m in app.user_middleware]
        assert CORSMiddleware in middleware_types

    def test_cors_allows_vite_origin(self, app):
        from starlette.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:5173" in origins
                break

    def test_cors_allows_chainlit_origin(self, app):
        from starlette.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:8000" in origins
                break

    def test_cors_allow_all_methods(self, app):
        from starlette.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                assert "*" in m.kwargs.get("allow_methods", [])
                break

    def test_app_is_fastapi_instance(self, app):
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)


# ===========================================================================
# 4.  Lifespan
# ===========================================================================

class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_calls_load_sessions(self, main_module):
        mock_load_sessions = MagicMock()
        mock_store = MagicMock()
        mock_store.load.return_value = True
        mock_store.get_known_products.return_value = []

        with (
            patch.object(main_module, "_store", mock_store),
            patch("api.sessions.load_sessions", mock_load_sessions),
        ):
            async with main_module.lifespan(main_module.app):
                pass

        mock_load_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_logs_warning_when_store_not_loaded(self, main_module):
        mock_store = MagicMock()
        mock_store.load.return_value = False

        with (
            patch.object(main_module, "_store", mock_store),
            patch("api.sessions.load_sessions", MagicMock()),
            patch.object(main_module.logger, "warning") as mock_warn,
        ):
            async with main_module.lifespan(main_module.app):
                pass

        mock_warn.assert_called_once()
        assert "ingest" in mock_warn.call_args[0][0].lower() or \
               "vector store" in mock_warn.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_lifespan_logs_info_when_store_loaded(self, main_module):
        mock_store = MagicMock()
        mock_store.load.return_value = True
        mock_store.get_known_products.return_value = ["P1", "P2"]

        with (
            patch.object(main_module, "_store", mock_store),
            patch("api.sessions.load_sessions", MagicMock()),
            patch.object(main_module.logger, "info") as mock_info,
        ):
            async with main_module.lifespan(main_module.app):
                pass

        # At least one info call should mention products / loading
        assert mock_info.called


# ===========================================================================
# 5.  Template string correctness (parameterised)
# ===========================================================================

_ROLEPLAY_FORMAT_CASES = [
    {
        "name": "Alice Chan",
        "age": 35,
        "occupation": "software engineer",
        "profile": "Married, two kids, mortgage.",
        "stage_instruction": "This is the first meeting.",
        "today": "2025-01-15",
    },
    {
        "name": "Bob Lee",
        "age": 50,
        "occupation": "restaurant owner",
        "profile": "Self-employed, no employees, HKD 2M savings.",
        "stage_instruction": "This is the third meeting.",
        "today": "2025-06-01",
    },
]


@pytest.mark.parametrize("ctx", _ROLEPLAY_FORMAT_CASES)
def test_roleplay_system_formats_without_keyerror(main_module, ctx):
    """_ROLEPLAY_SYSTEM must format cleanly with all required keys."""
    result = main_module._ROLEPLAY_SYSTEM.format(**ctx)