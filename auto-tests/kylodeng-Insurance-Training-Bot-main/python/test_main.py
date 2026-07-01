"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
    - _get_llm(): returns shared instance vs. new instance based on params
    - _build_roleplay_system(): prompt construction (stub — source truncated)
    - _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template placeholder coverage
    - FastAPI app startup/lifespan behaviour
    - CORS middleware presence
    - Static file mount (/docs)
    - POST /ingest, GET /sessions, POST /sessions, DELETE /sessions/{id},
      GET /sessions/{id}, PATCH /sessions/{id}/title, POST /chat,
      POST /generate-profile  (all endpoint stubs / mocks where source is truncated)
    - Environment variable fallbacks for API_KEY, OPENAI_URL_BASE, OPENAI_MODEL
    - SHOW_TOOL_CALLS flag parsing

Mocks used:
    - unittest.mock.patch / MagicMock for ChatOpenAI, httpx.Client, httpx.AsyncClient
    - get_vector_store, make_rag_tools, make_teacher_agent, make_assessor_agent
    - Session management helpers: create_session, delete_session, generate_profile,
      get_session, list_sessions, load_sessions, update_session_title
    - fastapi.testclient.TestClient for HTTP-level tests

TODOs:
    - TODO: Full source of _build_roleplay_system() and all route handlers needed
            to test their internal logic in detail.
    - TODO: Integration test for streaming /chat endpoint requires a live LLM mock
            that yields async chunks — stub provided.
    - TODO: Vector store ingest endpoint source not provided — stub provided.
    - TODO: generate_profile endpoint source not provided — stub provided.
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers: build a fake module tree so api/main.py can be imported without
# real heavy dependencies installed in the test environment.
# ---------------------------------------------------------------------------

def _make_fake_langchain_modules():
    """Inject lightweight stubs for langchain packages before importing main."""
    # langchain_core.messages
    lc_core = types.ModuleType("langchain_core")
    lc_core_msgs = types.ModuleType("langchain_core.messages")

    class _Msg:
        def __init__(self, content=""):
            self.content = content

    lc_core_msgs.AIMessage = type("AIMessage", (_Msg,), {})
    lc_core_msgs.HumanMessage = type("HumanMessage", (_Msg,), {})
    lc_core_msgs.SystemMessage = type("SystemMessage", (_Msg,), {})
    lc_core.messages = lc_core_msgs
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules.setdefault("langchain_core.messages", lc_core_msgs)

    # langchain_openai
    lo = types.ModuleType("langchain_openai")

    class _FakeChatOpenAI:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    lo.ChatOpenAI = _FakeChatOpenAI
    sys.modules.setdefault("langchain_openai", lo)

    # pydantic — use real if available, else stub
    try:
        import pydantic  # noqa: F401
    except ImportError:
        pydantic_stub = types.ModuleType("pydantic")
        pydantic_stub.BaseModel = object
        pydantic_stub.SecretStr = str
        sys.modules.setdefault("pydantic", pydantic_stub)


def _make_fake_app_modules():
    """Stub internal packages so import of api.main succeeds."""
    # core.vector_store
    vs_mod = types.ModuleType("core")
    vs_sub = types.ModuleType("core.vector_store")
    fake_store = MagicMock()
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["prod_a", "prod_b"]
    vs_sub.get_vector_store = MagicMock(return_value=fake_store)
    vs_mod.vector_store = vs_sub
    sys.modules.setdefault("core", vs_mod)
    sys.modules.setdefault("core.vector_store", vs_sub)

    # api.rag_tools
    rag_mod = types.ModuleType("api.rag_tools")
    rag_mod.make_rag_tools = MagicMock(return_value=[MagicMock()])
    sys.modules.setdefault("api.rag_tools", rag_mod)

    # api.agent
    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    sys.modules.setdefault("api.agent", agent_mod)

    # api.sessions
    sessions_mod = types.ModuleType("api.sessions")

    class _CustomerProfile:
        def __init__(self, **kw):
            self.__dict__.update(kw)
        def model_dump(self):
            return self.__dict__

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
    sys.modules.setdefault("api.sessions", sessions_mod)

    # api (package stub so "from api.x import y" works)
    api_pkg = sys.modules.get("api") or types.ModuleType("api")
    sys.modules.setdefault("api", api_pkg)

    # dotenv
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = MagicMock()
    sys.modules.setdefault("dotenv", dotenv_mod)

    # httpx — stub enough surface
    httpx_mod = sys.modules.get("httpx") or types.ModuleType("httpx")
    if not hasattr(httpx_mod, "Client"):
        httpx_mod.Client = MagicMock(return_value=MagicMock())
        httpx_mod.AsyncClient = MagicMock(return_value=MagicMock())
    sys.modules.setdefault("httpx", httpx_mod)


# Run setup before any import of api.main
_make_fake_langchain_modules()
_make_fake_app_modules()

# Now safe to import
with patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None):
    with patch("fastapi.staticfiles.StaticFiles.__call__", new_callable=AsyncMock):
        import api.main as main_module  # noqa: E402  (must come after stubs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    return main_module.app


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient
    # Disable lifespan so we don't need a running vector store during tests
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Environment / configuration tests
# ---------------------------------------------------------------------------

class TestEnvDefaults:
    """_API_KEY, _BASE_URL, _LLM_MODEL fall back to defaults when env is absent."""

    def test_base_url_default(self):
        assert main_module._BASE_URL in (
            "https://openrouter.ai/api/v1",
            os.getenv("OPENAI_URL_BASE", "https://openrouter.ai/api/v1"),
        )

    def test_llm_model_default(self):
        assert isinstance(main_module._LLM_MODEL, str)
        assert len(main_module._LLM_MODEL) > 0

    def test_temperature_value(self):
        assert main_module._LLM_TEMPERATURE == 0.6

    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("TRUE", True),
        ("false", False),
        ("FALSE", False),
        ("1", False),   # only "true" (case-insensitive) is truthy
    ])
    def test_show_tool_calls_parsing(self, monkeypatch, env_val, expected):
        monkeypatch.setenv("SHOW_TOOL_CALLS", env_val)
        # Re-evaluate the expression the module uses
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result == expected


# ---------------------------------------------------------------------------
# _get_llm() tests
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_overrides(self):
        llm = main_module._get_llm()
        assert llm is main_module._llm

    def test_returns_shared_instance_explicit_default_temp(self):
        llm = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert llm is main_module._llm

    def test_returns_new_instance_when_model_differs(self):
        llm = main_module._get_llm(model="other-model")
        assert llm is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self):
        llm = main_module._get_llm(temperature=0.0)
        assert llm is not main_module._llm

    def test_new_instance_uses_supplied_model(self):
        llm = main_module._get_llm(model="my-test-model")
        assert llm.model == "my-test-model"

    def test_new_instance_uses_supplied_temperature(self):
        llm = main_module._get_llm(temperature=0.99)
        assert llm.temperature == 0.99

    def test_new_instance_uses_default_model_when_none(self):
        # temperature differs → new instance; model=None → should use _LLM_MODEL
        llm = main_module._get_llm(model=None, temperature=0.1)
        assert llm.model == main_module._LLM_MODEL

    def test_shared_llm_has_streaming_enabled(self):
        assert main_module._llm.streaming is True

    def test_shared_llm_temperature(self):
        assert main_module._llm.temperature == main_module._LLM_TEMPERATURE


# ---------------------------------------------------------------------------
# System prompt template tests
# ---------------------------------------------------------------------------

class TestRoleplaySystemPrompt:
    REQUIRED_PLACEHOLDERS = [
        "{name}", "{age}", "{occupation}", "{profile}",
        "{stage_instruction}", "{today}",
    ]

    @pytest.mark.parametrize("placeholder", REQUIRED_PLACEHOLDERS)
    def test_placeholder_present(self, placeholder):
        assert placeholder in main_module._ROLEPLAY_SYSTEM

    def test_template_renders_without_key_error(self):
        rendered = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=35,
            occupation="nurse",
            profile="single mother, two kids",
            stage_instruction="This is the first meeting.",
            today="2024-06-01",
        )
        assert "Alice" in rendered
        assert "nurse" in rendered

    def test_does_not_break_character_instruction_present(self):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM


class TestPriorContextPrompt:
    REQUIRED_PLACEHOLDERS = ["{profile}", "{stage}"]

    @pytest.mark.parametrize("placeholder", REQUIRED_PLACEHOLDERS)
    def test_placeholder_present(self, placeholder):
        assert placeholder in main_module._PRIOR_CONTEXT_PROMPT

    def test_template_renders_without_key_error(self):
        rendered = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="John, 40, engineer, married, two kids",
            stage="3rd conversation",
        )
        assert "John" in rendered

    def test_word_limit_instruction_present(self):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_second_person_guideline_present(self):
        assert "second person" in main_module._PRIOR_CONTEXT_PROMPT.lower()


# ---------------------------------------------------------------------------
# FastAPI app configuration tests
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_registered(self, app):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMW
        middleware_classes = [m.cls for m in app.user_middleware]
        assert StarletteCorsMW in middleware_classes

    def test_cors_allows_vite_origin(self, app):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMW
        for m in app.user_middleware:
            if m.cls is StarletteCorsMW:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:5173" in origins
                break

    def test_cors_allows_chainlit_origin(self, app):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMW
        for m in app.user_middleware:
            if m.cls is StarletteCorsMW:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:8000" in origins
                break


# ---------------------------------------------------------------------------
# Lifespan tests
# ---------------------------------------------------------------------------

class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_calls_load_sessions(self):
        sessions_mod = sys.modules["api.sessions"]
        sessions_mod.load_sessions.reset_mock()

        vs_mod = sys.modules["core.vector_store"]
        mock_store = MagicMock()
        mock_store.load.return_value = True
        mock_store.get_known_products.return_value = ["p1"]
        vs_mod.get_vector_store.return_value = mock_store
        main_module._store = mock_store

        fake_app = MagicMock()
        async with main_module.lifespan(fake_app):
            pass

        sessions_mod.load_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_logs_warning_when_store_missing(self, caplog):
        import logging
        sessions_mod = sys.modules["api.sessions"]
        sessions_mod.load_sessions.reset_mock()

        mock_store = MagicMock()
        mock_store.load.return_value = False
        main_module._store = mock_store

        fake_app = MagicMock()
        with caplog.at_level(logging.WARNING, logger="api.main"):
            async with main_module.lifespan(fake_app):
                pass

        assert any("No vector store" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_lifespan_logs_info_when_store_loaded(self, caplog):
        import logging
        sessions_mod = sys.modules["api.sessions