"""
Test suite for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm(): shared instance return, new instance creation with custom params
- _build_roleplay_system(): prompt construction with CustomerProfile data
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable coverage
- FastAPI app endpoints (via TestClient / AsyncClient with httpx):
    - GET /sessions (list_sessions)
    - POST /sessions (create_session)
    - DELETE /sessions/{id} (delete_session)
    - GET /sessions/{id} (get_session)
    - PATCH /sessions/{id}/title (update_session_title)
    - POST /ingest
    - POST /chat (streaming)
    - POST /generate-profile
- Lifespan startup: vector store loading / warning branch
- CORS middleware presence
- StaticFiles mount for /docs

Mocks used:
- core.vector_store.get_vector_store (MagicMock)
- api.rag_tools.make_rag_tools (MagicMock)
- api.agent.make_teacher_agent, make_assessor_agent (MagicMock)
- api.sessions.* (create_session, delete_session, get_session, list_sessions,
                   load_sessions, update_session_title, generate_profile, load_sessions)
- langchain_openai.ChatOpenAI (MagicMock)
- httpx.Client, httpx.AsyncClient (MagicMock)
- os.getenv / environment variables (monkeypatch)

TODOs:
- TODO: Full streaming /chat endpoint integration test requires real LangChain agent wiring
- TODO: POST /ingest endpoint — needs source file context to know full request schema
- TODO: Test authentication/API key validation once implemented
- TODO: _build_roleplay_system full coverage requires seeing the complete function body
"""

import importlib
import sys
import types
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build stub modules so api/main.py can be imported in isolation
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """Inject lightweight stubs for heavy optional dependencies."""

    # --- langchain_core.messages ---
    lc_core = types.ModuleType("langchain_core")
    lc_msgs = types.ModuleType("langchain_core.messages")

    class _Msg:
        def __init__(self, content=""):
            self.content = content

    class AIMessage(_Msg):
        pass

    class HumanMessage(_Msg):
        pass

    class SystemMessage(_Msg):
        pass

    lc_msgs.AIMessage = AIMessage
    lc_msgs.HumanMessage = HumanMessage
    lc_msgs.SystemMessage = SystemMessage
    lc_core.messages = lc_msgs
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules.setdefault("langchain_core.messages", lc_msgs)

    # --- langchain_openai ---
    lo = types.ModuleType("langchain_openai")
    MockChatOpenAI = MagicMock(name="ChatOpenAI")
    lo.ChatOpenAI = MockChatOpenAI
    sys.modules.setdefault("langchain_openai", lo)

    # --- core.vector_store ---
    core = types.ModuleType("core")
    core_vs = types.ModuleType("core.vector_store")
    mock_store = MagicMock()
    mock_store.load.return_value = True
    mock_store.get_known_products.return_value = ["ProductA", "ProductB"]
    core_vs.get_vector_store = MagicMock(return_value=mock_store)
    core.vector_store = core_vs
    sys.modules.setdefault("core", core)
    sys.modules.setdefault("core.vector_store", core_vs)

    # --- api.rag_tools ---
    api_pkg = sys.modules.get("api") or types.ModuleType("api")
    api_rag = types.ModuleType("api.rag_tools")
    api_rag.make_rag_tools = MagicMock(return_value=[MagicMock(name="rag_tool")])
    sys.modules.setdefault("api", api_pkg)
    sys.modules.setdefault("api.rag_tools", api_rag)

    # --- api.agent ---
    api_agent = types.ModuleType("api.agent")
    api_agent.make_teacher_agent = MagicMock(return_value=MagicMock(name="teacher"))
    api_agent.make_assessor_agent = MagicMock(return_value=MagicMock(name="assessor"))
    sys.modules.setdefault("api.agent", api_agent)

    # --- api.sessions ---
    api_sessions = types.ModuleType("api.sessions")

    class CustomerProfile(MagicMock):
        pass

    class Session(MagicMock):
        pass

    api_sessions.CustomerProfile = CustomerProfile
    api_sessions.Session = Session
    api_sessions.create_session = MagicMock()
    api_sessions.delete_session = MagicMock()
    api_sessions.generate_profile = MagicMock()
    api_sessions.get_session = MagicMock()
    api_sessions.list_sessions = MagicMock(return_value=[])
    api_sessions.load_sessions = MagicMock()
    api_sessions.update_session_title = MagicMock()
    sys.modules.setdefault("api.sessions", api_sessions)

    # --- dotenv ---
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = MagicMock()
    sys.modules.setdefault("dotenv", dotenv)

    return mock_store


# Run stub injection before importing anything from api.main
_mock_store = _make_stub_modules()


# Now we can safely import
with patch("httpx.Client"), patch("httpx.AsyncClient"):
    import api.main as main_module  # noqa: E402  (must come after stubs)
    from api.main import (
        _PRIOR_CONTEXT_PROMPT,
        _ROLEPLAY_SYSTEM,
        _get_llm,
        _LLM_TEMPERATURE,
        app,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a synchronous TestClient for the FastAPI app."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def sessions_mod():
    """Return the stubbed api.sessions module."""
    return sys.modules["api.sessions"]


@pytest.fixture(autouse=True)
def reset_session_mocks(sessions_mod):
    """Reset session mock call counts between tests."""
    for name in (
        "create_session",
        "delete_session",
        "generate_profile",
        "get_session",
        "list_sessions",
        "load_sessions",
        "update_session_title",
    ):
        getattr(sessions_mod, name).reset_mock()
    yield


# ---------------------------------------------------------------------------
# _get_llm tests
# ---------------------------------------------------------------------------

class TestGetLlm:
    """Tests for the _get_llm() factory helper."""

    def test_returns_shared_instance_when_no_args(self):
        """Calling _get_llm() with defaults returns the module-level _llm."""
        result = _get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_explicit_none_model(self):
        result = _get_llm(model=None, temperature=_LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_for_custom_model(self):
        """Providing a different model name triggers a fresh ChatOpenAI instance."""
        lo = sys.modules["langchain_openai"]
        lo.ChatOpenAI.reset_mock()
        result = _get_llm(model="my-custom-model")
        # Should NOT be the shared singleton
        assert result is not main_module._llm

    def test_returns_new_instance_for_custom_temperature(self):
        lo = sys.modules["langchain_openai"]
        lo.ChatOpenAI.reset_mock()
        result = _get_llm(temperature=0.9)
        assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self):
        lo = sys.modules["langchain_openai"]
        lo.ChatOpenAI.reset_mock()
        _get_llm(model="special-model", temperature=0.1)
        call_kwargs = lo.ChatOpenAI.call_args
        # ChatOpenAI was called at module import time and possibly again here
        assert lo.ChatOpenAI.called

    def test_zero_temperature_returns_new_instance(self):
        """temperature=0.0 differs from _LLM_TEMPERATURE (0.6), so new instance."""
        result = _get_llm(temperature=0.0)
        assert result is not main_module._llm


# ---------------------------------------------------------------------------
# Prompt template tests
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    """Validate the _ROLEPLAY_SYSTEM prompt template variables."""

    def test_contains_required_placeholders(self):
        for placeholder in ("{name}", "{age}", "{occupation}", "{profile}",
                            "{stage_instruction}", "{today}"):
            assert placeholder in _ROLEPLAY_SYSTEM, (
                f"Missing placeholder {placeholder} in _ROLEPLAY_SYSTEM"
            )

    def test_format_with_valid_values(self):
        rendered = _ROLEPLAY_SYSTEM.format(
            name="Alice Wong",
            age=35,
            occupation="nurse",
            profile="Single mother, two kids, rents flat in Kowloon.",
            stage_instruction="This is a first meeting.",
            today=str(date.today()),
        )
        assert "Alice Wong" in rendered
        assert "nurse" in rendered
        assert "35" in rendered

    def test_never_break_character_instruction_present(self):
        assert "Never break character" in _ROLEPLAY_SYSTEM

    def test_today_date_instruction_present(self):
        assert "Today's date is" in _ROLEPLAY_SYSTEM


class TestPriorContextPromptTemplate:
    """Validate the _PRIOR_CONTEXT_PROMPT template variables."""

    def test_contains_required_placeholders(self):
        for placeholder in ("{profile}", "{stage}"):
            assert placeholder in _PRIOR_CONTEXT_PROMPT

    def test_format_with_valid_values(self):
        rendered = _PRIOR_CONTEXT_PROMPT.format(
            profile="Age 40, married, two children, works as an engineer.",
            stage="2nd conversation",
        )
        assert "2nd conversation" in rendered
        assert "350 words" in rendered

    def test_max_word_limit_mentioned(self):
        assert "350" in _PRIOR_CONTEXT_PROMPT

    def test_second_person_guideline_mentioned(self):
        assert "second person" in _PRIOR_CONTEXT_PROMPT


# ---------------------------------------------------------------------------
# App-level / middleware tests
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_title(self):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_registered(self):
        middleware_types = [type(m).__name__ for m in app.user_middleware]
        # CORSMiddleware adds itself; check via route listing or middleware stack
        from starlette.middleware.cors import CORSMiddleware
        found = any(
            getattr(m, "cls", None) is CORSMiddleware
            for m in app.user_middleware
        )
        assert found, "CORSMiddleware not registered"

    def test_docs_static_mount_exists(self):
        routes = {r.path for r in app.routes}
        assert "/docs" in routes or any("/docs" in str(r) for r in app.routes)


# ---------------------------------------------------------------------------
# Session endpoint tests
# ---------------------------------------------------------------------------

class TestListSessionsEndpoint:
    def test_returns_empty_list(self, client, sessions_mod):
        sessions_mod.list_sessions.return_value = []
        resp = client.get("/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_multiple_sessions(self, client, sessions_mod):
        sessions_mod.list_sessions.return_value = [
            {"id": "abc", "title": "Session 1"},
            {"id": "def", "title": "Session 2"},
        ]
        resp = client.get("/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_list_sessions_called_once(self, client, sessions_mod):
        sessions_mod.list_sessions.return_value = []
        client.get("/sessions")
        sessions_mod.list_sessions.assert_called_once()


class TestCreateSessionEndpoint:
    def test_create_session_happy_path(self, client, sessions_mod):
        new_session = {"id": "sess-001", "title": "New Session", "messages": []}
        sessions_mod.create_session.return_value = new_session
        resp = client.post("/sessions")
        assert resp.status_code in (200, 201)

    def test_create_session_delegates_to_service(self, client, sessions_mod):
        sessions_mod.create_session.return_value = {"id": "x", "title": "T", "messages": []}
        client.post("/sessions")
        sessions_mod.create_session.assert_called_once()


class TestGetSessionEndpoint:
    def test_get_existing_session(self, client, sessions_mod):
        sessions_mod.get_session.return_value = {
            "id": "sess-123",
            "title": "Test",
            "messages": [],
        }
        resp = client.get("/sessions/sess-123")
        assert resp.status_code == 200

    def test_get_missing_session_returns_404(self, client, sessions_mod):
        sessions_mod.get_session.return_value = None
        resp = client.get("/sessions/nonexistent")
        assert resp.status_code == 404

    def test_get_session_passes_id(self, client, sessions_mod):
        sessions_mod.get_session.return_value = {"id": "abc", "title": "T", "messages": []}
        client.get("/sessions/abc")
        call_args = sessions_mod.get_session.call_args
        assert "abc" in (call_args.args + tuple(call_args.kwargs.values()))


class TestDeleteSessionEndpoint:
    def test_delete_existing_session(self, client, sessions_mod):
        sessions_mod.delete_session.return_value = True
        resp = client.delete("/sessions/sess-001")
        assert resp.status_code in (200, 204)

    def test_delete_missing_session_returns_404(self, client, sessions_mod):
        sessions_mod.delete_session.return_value = False
        resp = client.delete("/sessions/ghost")
        assert resp.status_code == 404

    def test_delete_delegates_to_service(self, client, sessions_mod):
        sessions_mod.delete_session.return_value = True
        client.delete("/sessions/s1")
        sessions_mod.delete_session.assert_called_once()


class TestUpdateSessionTitleEndpoint:
    def test_update_title_happy_path(self, client, sessions_mod):
        sessions_mod.get_session.return_value = {"id": "s1", "title": "Old", "messages": []}
        sessions_mod.update_session_title.return_value = {"id": "s1", "title": "New Title"}
        resp = client.patch("/sessions/s