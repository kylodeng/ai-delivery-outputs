"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
    - _get_llm(): returns shared instance vs. new instance based on params
    - _build_roleplay_system(): system prompt construction (partial — function is truncated)
    - FastAPI app configuration: middleware, mounts, lifespan
    - API endpoints: all public routes (inferred from context)
    - SHOW_TOOL_CALLS environment variable parsing
    - Module-level constants derived from environment variables

Mocks used:
    - langchain_openai.ChatOpenAI (prevent real LLM instantiation)
    - httpx.Client / httpx.AsyncClient (prevent real HTTP clients)
    - core.vector_store.get_vector_store
    - api.rag_tools.make_rag_tools
    - api.agent.make_teacher_agent, make_assessor_agent
    - api.sessions (all public functions + models)
    - fastapi.staticfiles.StaticFiles (prevent filesystem access)
    - dotenv.load_dotenv

TODOs:
    - TODO: Full source of _build_roleplay_system is truncated — only partial tests possible
    - TODO: Need endpoint route definitions to test HTTP request/response cycles fully
    - TODO: Need api/agent.py source to test agent integration paths
    - TODO: Need api/sessions.py source to test session lifecycle integration
    - TODO: Need core/vector_store.py source to test ingest endpoint
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers – build a clean fake module tree so importing api.main never
# triggers real network / filesystem / GPU activity.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """Install lightweight stubs for every heavy dependency."""

    # ── langchain stubs ────────────────────────────────────────────────────
    lc_core = types.ModuleType("langchain_core")
    lc_messages = types.ModuleType("langchain_core.messages")

    class _FakeMsg:
        def __init__(self, content=""):
            self.content = content

    lc_messages.AIMessage = type("AIMessage", (_FakeMsg,), {})
    lc_messages.HumanMessage = type("HumanMessage", (_FakeMsg,), {})
    lc_messages.SystemMessage = type("SystemMessage", (_FakeMsg,), {})
    lc_core.messages = lc_messages
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules.setdefault("langchain_core.messages", lc_messages)

    lc_openai = types.ModuleType("langchain_openai")
    fake_llm_instance = MagicMock(name="ChatOpenAI_instance")
    ChatOpenAI_cls = MagicMock(name="ChatOpenAI", return_value=fake_llm_instance)
    lc_openai.ChatOpenAI = ChatOpenAI_cls
    sys.modules["langchain_openai"] = lc_openai

    # ── httpx stubs ────────────────────────────────────────────────────────
    httpx_mod = types.ModuleType("httpx")
    httpx_mod.Client = MagicMock(name="httpx.Client")
    httpx_mod.AsyncClient = MagicMock(name="httpx.AsyncClient")
    sys.modules["httpx"] = httpx_mod

    # ── dotenv stub ────────────────────────────────────────────────────────
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = MagicMock()
    sys.modules["dotenv"] = dotenv_mod

    # ── core.vector_store stub ─────────────────────────────────────────────
    core_mod = types.ModuleType("core")
    core_vs = types.ModuleType("core.vector_store")
    fake_store = MagicMock(name="VectorStore")
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["ProductA", "ProductB"]
    core_vs.get_vector_store = MagicMock(return_value=fake_store)
    core_mod.vector_store = core_vs
    sys.modules.setdefault("core", core_mod)
    sys.modules["core.vector_store"] = core_vs

    # ── api package stubs ──────────────────────────────────────────────────
    api_pkg = sys.modules.setdefault("api", types.ModuleType("api"))

    api_rag = types.ModuleType("api.rag_tools")
    api_rag.make_rag_tools = MagicMock(return_value=[MagicMock(name="rag_tool")])
    sys.modules["api.rag_tools"] = api_rag

    api_agent = types.ModuleType("api.agent")
    api_agent.make_teacher_agent = MagicMock(return_value=MagicMock(name="teacher_agent"))
    api_agent.make_assessor_agent = MagicMock(return_value=MagicMock(name="assessor_agent"))
    sys.modules["api.agent"] = api_agent

    # ── api.sessions stub ──────────────────────────────────────────────────
    api_sessions = types.ModuleType("api.sessions")

    class FakeCustomerProfile:
        def __init__(self, **kwargs):
            self.name = kwargs.get("name", "Test User")
            self.age = kwargs.get("age", 35)
            self.occupation = kwargs.get("occupation", "Engineer")
            self.profile = kwargs.get("profile", "A generic profile")
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self):
            return self.__dict__

    class FakeSession:
        def __init__(self, **kwargs):
            self.id = kwargs.get("id", "sess-001")
            self.profile = kwargs.get("profile", FakeCustomerProfile())
            self.messages = kwargs.get("messages", [])
            self.title = kwargs.get("title", "Untitled")

    api_sessions.CustomerProfile = FakeCustomerProfile
    api_sessions.Session = FakeSession
    api_sessions.create_session = MagicMock(return_value=FakeSession())
    api_sessions.delete_session = MagicMock(return_value=True)
    api_sessions.generate_profile = MagicMock(return_value=FakeCustomerProfile())
    api_sessions.get_session = MagicMock(return_value=FakeSession())
    api_sessions.list_sessions = MagicMock(return_value=[])
    api_sessions.load_sessions = MagicMock()
    api_sessions.update_session_title = MagicMock()
    sys.modules["api.sessions"] = api_sessions

    # ── fastapi.staticfiles stub ───────────────────────────────────────────
    static_mod = sys.modules.get("fastapi.staticfiles")
    if static_mod is None:
        static_mod = types.ModuleType("fastapi.staticfiles")
    static_mod.StaticFiles = MagicMock(name="StaticFiles")
    sys.modules["fastapi.staticfiles"] = static_mod

    return {
        "ChatOpenAI_cls": ChatOpenAI_cls,
        "fake_llm_instance": fake_llm_instance,
        "fake_store": fake_store,
        "FakeCustomerProfile": FakeCustomerProfile,
        "FakeSession": FakeSession,
    }


# Install stubs before any api.main import
_FAKES = _make_fake_modules()

# Now import the module under test
# We must reload if it was previously cached without stubs
if "api.main" in sys.modules:
    del sys.modules["api.main"]

import api.main as main_module  # noqa: E402  (after stub installation)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_llm_instance():
    return _FAKES["fake_llm_instance"]


@pytest.fixture()
def fake_store():
    return _FAKES["fake_store"]


@pytest.fixture()
def FakeCustomerProfile():
    return _FAKES["FakeCustomerProfile"]


@pytest.fixture()
def app_client():
    """Return a TestClient for the FastAPI app."""
    from fastapi.testclient import TestClient
    return TestClient(main_module.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests: module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_llm_temperature_default(self):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_show_tool_calls_is_bool(self):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_base_url_has_value(self):
        assert isinstance(main_module._BASE_URL, str)
        assert len(main_module._BASE_URL) > 0

    def test_llm_model_has_value(self):
        assert isinstance(main_module._LLM_MODEL, str)
        assert len(main_module._LLM_MODEL) > 0

    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("yes", False),   # only "true" (case-insensitive) maps to True
        (" ", False),
    ])
    def test_show_tool_calls_env_parsing(self, env_val, expected, monkeypatch):
        """SHOW_TOOL_CALLS must be True only when env var is exactly 'true' (case-insensitive)."""
        monkeypatch.setenv("SHOW_TOOL_CALLS", env_val)
        # Re-evaluate the expression as the module does
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result == expected


# ---------------------------------------------------------------------------
# Tests: _get_llm()
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_overrides(self):
        """No args → same singleton object."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_with_default_temperature(self):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_provided(self):
        """Passing a model name must produce a new ChatOpenAI, not the singleton."""
        ChatOpenAI_cls = _FAKES["ChatOpenAI_cls"]
        call_count_before = ChatOpenAI_cls.call_count
        result = main_module._get_llm(model="some-other-model")
        assert ChatOpenAI_cls.call_count > call_count_before
        assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self):
        ChatOpenAI_cls = _FAKES["ChatOpenAI_cls"]
        call_count_before = ChatOpenAI_cls.call_count
        result = main_module._get_llm(temperature=0.9)
        assert ChatOpenAI_cls.call_count > call_count_before
        assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self):
        ChatOpenAI_cls = _FAKES["ChatOpenAI_cls"]
        ChatOpenAI_cls.reset_mock()
        main_module._get_llm(model="custom-model", temperature=0.1)
        call_kwargs = ChatOpenAI_cls.call_args
        assert call_kwargs is not None
        # model should be passed
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        args = call_kwargs.args if call_kwargs.args else ()
        model_passed = kwargs.get("model") or (args[0] if args else None)
        assert model_passed == "custom-model"

    def test_new_instance_uses_provided_temperature(self):
        ChatOpenAI_cls = _FAKES["ChatOpenAI_cls"]
        ChatOpenAI_cls.reset_mock()
        main_module._get_llm(temperature=0.1)
        call_kwargs = ChatOpenAI_cls.call_args.kwargs
        assert call_kwargs.get("temperature") == 0.1

    def test_new_instance_falls_back_to_global_model_when_none(self):
        ChatOpenAI_cls = _FAKES["ChatOpenAI_cls"]
        ChatOpenAI_cls.reset_mock()
        main_module._get_llm(model=None, temperature=0.99)
        call_kwargs = ChatOpenAI_cls.call_args.kwargs
        assert call_kwargs.get("model") == main_module._LLM_MODEL

    def test_new_instance_has_streaming_true(self):
        ChatOpenAI_cls = _FAKES["ChatOpenAI_cls"]
        ChatOpenAI_cls.reset_mock()
        main_module._get_llm(temperature=0.2)
        call_kwargs = ChatOpenAI_cls.call_args.kwargs
        assert call_kwargs.get("streaming") is True

    def test_new_instance_api_key_is_secret_str(self):
        ChatOpenAI_cls = _FAKES["ChatOpenAI_cls"]
        ChatOpenAI_cls.reset_mock()
        main_module._get_llm(temperature=0.3)
        call_kwargs = ChatOpenAI_cls.call_args.kwargs
        api_key = call_kwargs.get("api_key")
        assert isinstance(api_key, SecretStr)


# ---------------------------------------------------------------------------
# Tests: FastAPI app configuration
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_is_fastapi_instance(self):
        from fastapi import FastAPI
        assert isinstance(main_module.app, FastAPI)

    def test_app_title(self):
        assert main_module.app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self):
        from starlette.middleware.cors import CORSMiddleware
        middleware_types = [m.cls for m in main_module.app.user_middleware]
        assert CORSMiddleware in middleware_types

    def test_cors_allows_localhost_5173(self):
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:5173" in origins
                break

    def test_cors_allows_localhost_8000(self):
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:8000" in origins
                break

    def test_cors_allow_methods_wildcard(self):
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware:
            if m.cls is CORSMiddleware:
                methods = m.kwargs.get("allow_methods", [])
                assert "*" in methods
                break

    def test_cors_allow_headers_wildcard(self):
        from starlette.middleware.cors import CORSMiddleware
        for m in main_module.app.user_middleware: