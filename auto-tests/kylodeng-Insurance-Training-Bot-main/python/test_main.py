"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm(): returns shared instance vs new instance based on parameters
- _build_roleplay_system(): system prompt construction (stub — incomplete source)
- FastAPI app configuration: CORS middleware, static files mount, lifespan
- API endpoints: all public HTTP routes (GET, POST, DELETE)
- Streaming response behavior
- Session management integration
- RAG tools and vector store integration
- _ROLEPLAY_SYSTEM and _PRIOR_CONTEXT_PROMPT template strings

Mocks used:
- langchain_openai.ChatOpenAI (avoid real LLM calls)
- core.vector_store.get_vector_store (avoid real vector store I/O)
- api.rag_tools.make_rag_tools (avoid real tool construction)
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (create_session, get_session, delete_session, etc.)
- httpx.Client, httpx.AsyncClient (avoid real HTTP)
- fastapi.staticfiles.StaticFiles (avoid real filesystem dependency)

TODOs:
- TODO: Obtain full source of _build_roleplay_system() to test prompt assembly
- TODO: Obtain full source of all API route handlers to test request/response schemas
- TODO: Obtain CustomerProfile model definition for complete fixture coverage
- TODO: Obtain Session model definition for complete fixture coverage
- TODO: Test streaming SSE chunks end-to-end once route handler source is available
- TODO: Test /ingest endpoint once its handler source is available
"""

import asyncio
import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Minimal stub modules so api/main.py can be imported without real deps
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ── Stub: core.vector_store ──────────────────────────────────────────────────

_mock_store = MagicMock()
_mock_store.load.return_value = True
_mock_store.get_known_products.return_value = ["Generations II", "health_products"]

_core_vs_mod = _make_stub_module("core.vector_store", get_vector_store=MagicMock(return_value=_mock_store))
_core_mod = _make_stub_module("core")
sys.modules.setdefault("core", _core_mod)
sys.modules.setdefault("core.vector_store", _core_vs_mod)

# ── Stub: api.rag_tools ──────────────────────────────────────────────────────

_mock_rag_tools = [MagicMock(name="rag_tool_1"), MagicMock(name="rag_tool_2")]
_api_rag_mod = _make_stub_module("api.rag_tools", make_rag_tools=MagicMock(return_value=_mock_rag_tools))
sys.modules.setdefault("api.rag_tools", _api_rag_mod)

# ── Stub: api.agent ─────────────────────────────────────────────────────────

_mock_teacher_agent = MagicMock(name="teacher_agent")
_mock_assessor_agent = MagicMock(name="assessor_agent")
_api_agent_mod = _make_stub_module(
    "api.agent",
    make_teacher_agent=MagicMock(return_value=_mock_teacher_agent),
    make_assessor_agent=MagicMock(return_value=_mock_assessor_agent),
)
sys.modules.setdefault("api.agent", _api_agent_mod)

# ── Stub: api.sessions ───────────────────────────────────────────────────────

class _StubCustomerProfile:
    """Minimal CustomerProfile stub matching expected interface."""
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "Alice Tester")
        self.age = kwargs.get("age", 35)
        self.occupation = kwargs.get("occupation", "Teacher")
        self.profile = kwargs.get("profile", "Single, no dependents.")
        self.stage = kwargs.get("stage", "1st_meeting")

    def model_dump(self):
        return {
            "name": self.name,
            "age": self.age,
            "occupation": self.occupation,
            "profile": self.profile,
            "stage": self.stage,
        }


class _StubSession:
    def __init__(self, session_id="sess-001", profile=None):
        self.session_id = session_id
        self.profile = profile or _StubCustomerProfile()
        self.messages = []
        self.title = "Test Session"


_mock_create_session = MagicMock(return_value=_StubSession())
_mock_delete_session = MagicMock(return_value=True)
_mock_generate_profile = MagicMock(return_value=_StubCustomerProfile())
_mock_get_session = MagicMock(return_value=_StubSession())
_mock_list_sessions = MagicMock(return_value=[])
_mock_load_sessions = MagicMock()
_mock_update_session_title = MagicMock()

_api_sessions_mod = _make_stub_module(
    "api.sessions",
    CustomerProfile=_StubCustomerProfile,
    Session=_StubSession,
    create_session=_mock_create_session,
    delete_session=_mock_delete_session,
    generate_profile=_mock_generate_profile,
    get_session=_mock_get_session,
    list_sessions=_mock_list_sessions,
    load_sessions=_mock_load_sessions,
    update_session_title=_mock_update_session_title,
)
sys.modules.setdefault("api.sessions", _api_sessions_mod)

# ── Stub: langchain_openai ───────────────────────────────────────────────────

class _MockChatOpenAI:
    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self.model = kwargs.get("model", "test-model")
        self.temperature = kwargs.get("temperature", 0.6)

    def stream(self, messages):
        yield MagicMock(content="Hello ")
        yield MagicMock(content="world")

    async def astream(self, messages):
        for chunk in [MagicMock(content="Hello "), MagicMock(content="world")]:
            yield chunk

_lc_openai_mod = _make_stub_module("langchain_openai", ChatOpenAI=_MockChatOpenAI)
sys.modules.setdefault("langchain_openai", _lc_openai_mod)

# ── Stub: langchain_core.messages ────────────────────────────────────────────

class _AIMessage:
    def __init__(self, content): self.content = content
class _HumanMessage:
    def __init__(self, content): self.content = content
class _SystemMessage:
    def __init__(self, content): self.content = content

_lc_core_mod = _make_stub_module("langchain_core")
_lc_core_msg_mod = _make_stub_module(
    "langchain_core.messages",
    AIMessage=_AIMessage,
    HumanMessage=_HumanMessage,
    SystemMessage=_SystemMessage,
)
sys.modules.setdefault("langchain_core", _lc_core_mod)
sys.modules.setdefault("langchain_core.messages", _lc_core_msg_mod)

# ── Stub: httpx ──────────────────────────────────────────────────────────────

_httpx_mod = _make_stub_module(
    "httpx",
    Client=MagicMock(return_value=MagicMock()),
    AsyncClient=MagicMock(return_value=MagicMock()),
)
sys.modules.setdefault("httpx", _httpx_mod)

# ── Stub: dotenv ─────────────────────────────────────────────────────────────

_dotenv_mod = _make_stub_module("dotenv", load_dotenv=MagicMock())
sys.modules.setdefault("dotenv", _dotenv_mod)

# ── Stub: fastapi.staticfiles (avoid real FS check) ──────────────────────────

_static_files_cls = MagicMock()
_fastapi_static_mod = _make_stub_module("fastapi.staticfiles", StaticFiles=_static_files_cls)
# We override inside fastapi package already loaded by FastAPI itself, so we patch later.

# ---------------------------------------------------------------------------
# Now import the module under test
# ---------------------------------------------------------------------------

with (
    patch("fastapi.staticfiles.StaticFiles", _static_files_cls),
    patch.dict(os.environ, {
        "API_KEY": "test-key-123",
        "OPENAI_URL_BASE": "https://test.openrouter.ai/api/v1",
        "OPENAI_MODEL": "test/model",
        "SHOW_TOOL_CALLS": "true",
    }),
):
    import api.main as main_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    return main_module.app


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient
    # Use TestClient without triggering lifespan by default
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def async_client(app):
    """Async HTTPX client for async endpoint tests."""
    from httpx import AsyncClient as RealAsyncClient
    from httpx import ASGITransport
    return RealAsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture()
def sample_profile():
    return _StubCustomerProfile(
        name="Alice Tester",
        age=35,
        occupation="Teacher",
        profile="Single, rents apartment, no dependents. Interested in savings.",
        stage="1st_meeting",
    )


@pytest.fixture()
def sample_session(sample_profile):
    return _StubSession(session_id="sess-001", profile=sample_profile)


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset call counts on shared mocks between tests."""
    for m in [
        _mock_create_session,
        _mock_delete_session,
        _mock_generate_profile,
        _mock_get_session,
        _mock_list_sessions,
        _mock_load_sessions,
        _mock_update_session_title,
    ]:
        m.reset_mock()
    yield


# ---------------------------------------------------------------------------
# Tests: Module-level constants and configuration
# ---------------------------------------------------------------------------

class TestModuleConstants:

    def test_show_tool_calls_default_true(self):
        """SHOW_TOOL_CALLS should be True when env var is 'true'."""
        with patch.dict(os.environ, {"SHOW_TOOL_CALLS": "true"}):
            assert main_module.SHOW_TOOL_CALLS is True

    def test_llm_temperature_constant(self):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_roleplay_system_contains_placeholders(self):
        """_ROLEPLAY_SYSTEM must contain all required format placeholders."""
        tpl = main_module._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in tpl, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_contains_placeholders(self):
        tpl = main_module._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in tpl, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_word_limit_mentioned(self):
        """Prompt should mention 350-word limit."""
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_roleplay_system_no_break_character_instruction(self):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM


# ---------------------------------------------------------------------------
# Tests: _get_llm()
# ---------------------------------------------------------------------------

class TestGetLlm:

    def test_returns_shared_instance_when_no_args(self):
        """With default args, _get_llm() must return the module-level _llm."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_with_explicit_default_temperature(self):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self):
        result = main_module._get_llm(model="other/model")
        assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self):
        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self):
        result = main_module._get_llm(model="custom/model-x", temperature=0.1)
        assert result.model == "custom/model-x"

    def test_new_instance_uses_provided_temperature(self):
        result = main_module._get_llm(model="custom/model-x", temperature=0.1)
        assert result.temperature == 0.1

    def test_new_instance_falls_back_to_default_model_when_none(self):
        """When model=None but temperature differs, should use _LLM_MODEL."""
        result = main_module._get_llm(model=None, temperature=0.1)
        assert result.model == main_module._LLM_MODEL

    def test_zero_temperature_creates_new_instance(self):
        result = main_module._get_llm(temperature=0.0)
        assert result is not main_module._llm
        assert result.temperature == 0.0

    def test_boundary_temperature_one_creates_new_instance(self):
        result = main_module._get_llm(temperature=1.0)
        assert result is not main_module._llm


# ---------------------------------------------------------------------------
# Tests: FastAPI app object
# ---------------------------------------------------------------------------

class TestAppConfiguration:

    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, app):
        from starlette.middleware.cors import CORSMiddleware
        middleware_types = [type(m) for m in app.user_middleware]
        # CORSMiddleware may be wrapped; check by class name string
        middleware_names = [str(m) for m in app.user_middleware]
        combined = str(app.user_middleware)
        assert "CORSMiddleware" in combined or any(
            "cors" in str(m).lower() for m in app.user_middleware
        )

    def test_app_is_fastapi_instance(self, app):
        from fastapi import FastAPI
        assert isinstance(app, FastAPI)

    def test_docs_route_mounted(self, app):
        """Static files should be mounted at /docs."""
        route_paths = [getattr(r, "path", None) for r in app.routes]
        assert "/docs" in route_paths

    def test_allowed_origins_include_localhost_5173(self, app):
        combined = str(app.user_middleware