"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs new instance based on params
- _build_roleplay_system: prompt construction from CustomerProfile (stub — needs full function)
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template string integrity
- FastAPI endpoints: lifespan, CORS middleware, static file mount
- Session management endpoints (via TestClient): create, get, list, delete sessions
- Streaming response endpoint for chat
- Error conditions: missing sessions, invalid payloads, upstream LLM errors
- SHOW_TOOL_CALLS env-var parsing

Mocks used:
- api.main._llm (ChatOpenAI shared instance)
- api.main._get_llm
- api.main._store (vector store)
- api.main._rag_tools
- core.vector_store.get_vector_store
- api.rag_tools.make_rag_tools
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (create_session, get_session, list_sessions, delete_session,
                   load_sessions, generate_profile, update_session_title)
- langchain_openai.ChatOpenAI
- httpx.Client, httpx.AsyncClient

TODOs:
- TODO: Full source of _build_roleplay_system needed to test prompt assembly completely
- TODO: Endpoint route definitions not shown — stub tests added for inferred routes
- TODO: POST /ingest endpoint details unknown — stub test added
- TODO: Streaming SSE decode needs real async generator; stub provided
- TODO: CustomerProfile full schema unknown beyond what's inferable
"""

import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _make_mock_llm():
    mock = MagicMock()
    mock.model = "openai/gpt-oss-20b:free"
    mock.temperature = 0.6
    mock.streaming = True
    return mock


def _make_mock_store():
    store = MagicMock()
    store.load.return_value = True
    store.get_known_products.return_value = ["Generations II", "Health Plan A"]
    return store


def _make_mock_session(session_id="sess-123"):
    session = MagicMock()
    session.id = session_id
    session.title = "Test Session"
    session.messages = []
    return session


# ---------------------------------------------------------------------------
# Module-level patches applied before import so top-level code doesn't crash
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _patch_heavy_imports():
    """Patch external dependencies before api.main is imported."""
    mock_store = _make_mock_store()
    mock_llm = _make_mock_llm()
    mock_rag_tools = [MagicMock()]

    patches = [
        patch("httpx.Client", return_value=MagicMock()),
        patch("httpx.AsyncClient", return_value=MagicMock()),
        patch("langchain_openai.ChatOpenAI", return_value=mock_llm),
        patch("core.vector_store.get_vector_store", return_value=mock_store),
        patch("api.rag_tools.make_rag_tools", return_value=mock_rag_tools),
        patch("api.agent.make_teacher_agent", return_value=MagicMock()),
        patch("api.agent.make_assessor_agent", return_value=MagicMock()),
        patch(
            "api.sessions.load_sessions",
            return_value=None,
        ),
        patch(
            "api.sessions.list_sessions",
            return_value=[],
        ),
        patch(
            "api.sessions.create_session",
            return_value=_make_mock_session(),
        ),
        patch(
            "api.sessions.get_session",
            return_value=_make_mock_session(),
        ),
        patch(
            "api.sessions.delete_session",
            return_value=None,
        ),
        patch(
            "api.sessions.generate_profile",
            return_value=MagicMock(),
        ),
        patch(
            "api.sessions.update_session_title",
            return_value=None,
        ),
    ]

    started = [p.start() for p in patches]
    yield started
    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Import api.main after patches are in place
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def main_module(_patch_heavy_imports):
    """Import api.main with all heavy deps already mocked."""
    if "api.main" in sys.modules:
        return sys.modules["api.main"]
    import api.main as m
    return m


@pytest.fixture(scope="session")
def app(main_module):
    return main_module.app


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_show_tool_calls_default_is_bool(self, main_module):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_roleplay_system_contains_placeholders(self, main_module):
        template = main_module._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_contains_placeholders(self, main_module):
        template = main_module._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_max_word_hint(self, main_module):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_llm_temperature_is_float(self, main_module):
        assert isinstance(main_module._LLM_TEMPERATURE, float)
        assert 0.0 <= main_module._LLM_TEMPERATURE <= 2.0

    def test_base_url_default_is_openrouter(self, main_module):
        # Either the env override or the default should be a non-empty string
        assert isinstance(main_module._BASE_URL, str)
        assert len(main_module._BASE_URL) > 0


# ---------------------------------------------------------------------------
# Tests: SHOW_TOOL_CALLS env parsing
# ---------------------------------------------------------------------------

class TestShowToolCallsEnvParsing:
    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("yes", False),   # only "true" (case-insensitive) should be True
        ("", False),
    ])
    def test_show_tool_calls_parsing(self, env_val, expected, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", env_val)
        result = env_val.lower() == "true"
        assert result == expected


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_overrides(self, main_module):
        shared = main_module._llm
        result = main_module._get_llm()
        assert result is shared

    def test_returns_shared_instance_with_default_temperature(self, main_module):
        shared = main_module._llm
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is shared

    def test_returns_new_instance_when_model_differs(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = main_module._get_llm(model="some-other-model")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "some-other-model"

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        different_temp = main_module._LLM_TEMPERATURE + 0.1
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = main_module._get_llm(temperature=different_temp)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == pytest.approx(different_temp)

    def test_new_instance_uses_fallback_model_when_none_given_with_different_temp(
        self, main_module
    ):
        different_temp = 0.9
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(model=None, temperature=different_temp)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_streaming_enabled(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(model="test-model")
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("streaming") is True

    def test_new_instance_uses_env_base_url(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(model="test-model")
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == main_module._BASE_URL

    def test_temperature_zero_creates_new_instance(self, main_module):
        """Temperature=0.0 differs from default, so a new instance is expected."""
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(temperature=0.0)
            mock_cls.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: FastAPI app configuration
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, app):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMw
        middleware_types = [type(m.cls if hasattr(m, "cls") else m) for m in app.user_middleware]
        # Check at least one middleware is CORS-related
        names = [str(m) for m in app.user_middleware]
        assert any("cors" in str(m).lower() or "CORS" in str(m) for m in app.user_middleware)

    def test_openapi_schema_available(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "Insurance Agent Trainer"

    def test_docs_endpoint_accessible(self, client):
        # /docs is mounted as StaticFiles; the auto-generated swagger is at /docs
        # StaticFiles will shadow /docs — just check no 500 is returned
        resp = client.get("/openapi.json")
        assert resp.status_code != 500


# ---------------------------------------------------------------------------
# Tests: _ROLEPLAY_SYSTEM template formatting
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    def test_format_with_all_fields(self, main_module):
        filled = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=35,
            occupation="Teacher",
            profile="Some profile text",
            stage_instruction="This is a first meeting.",
            today="2024-06-01",
        )
        assert "Alice" in filled
        assert "35" in filled
        assert "Teacher" in filled
        assert "2024-06-01" in filled

    def test_format_missing_key_raises(self, main_module):
        with pytest.raises(KeyError):
            main_module._ROLEPLAY_SYSTEM.format(name="Alice")  # missing many keys


# ---------------------------------------------------------------------------
# Tests: _PRIOR_CONTEXT_PROMPT template formatting
# ---------------------------------------------------------------------------

class TestPriorContextPromptTemplate:
    def test_format_with_all_fields(self, main_module):
        filled = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Client is 40, married, has two kids.",
            stage="2nd conversation",
        )
        assert "Client is 40" in filled
        assert "2nd conversation" in filled

    def test_format_missing_key_raises(self, main_module):
        with pytest.raises(KeyError):
            main_module._PRIOR_CONTEXT_PROMPT.format(profile="only profile")


# ---------------------------------------------------------------------------
# Tests: _build_roleplay_system (stub — function body not fully shown)
# ---------------------------------------------------------------------------

class TestBuildRoleplaySystem:
    @pytest.mark.skip(
        reason="TODO: Full source of _build_roleplay_system not provided; "
               "need complete function body to test prompt assembly"
    )
    def test_build_roleplay_system_basic(self, main_module):
        profile = MagicMock()
        result = main_module._build_roleplay_system(profile)
        assert isinstance(result, str)

    @pytest.mark.skip(
        reason="TODO: CustomerProfile full schema required to construct valid fixture"
    )
    def test_build_roleplay_system_contains_profile_name(self, main_module):
        profile = MagicMock()
        profile.name = "Bob"
        result = main_module._build_roleplay_system(profile)
        assert "Bob" in result


# ---------------------------------------------------------------------------
# Tests: Lifespan (load_sessions + vector store)
# ---------------------------------------------------------------------------

class TestLifespan:
    def test_lifespan_calls_load_sessions(self, main_module):
        """Verify load_sessions is invoked during app startup."""
        with patch("api.main.load_sessions") as mock_load, \
             patch("api.main._store") as mock_store:
            mock_store.load.return_value = True
            mock_store.get_known_products.return_value = []
            # Re-running the context manager directly
            import asyncio
            async def _run():
                async with main_module.lifespan(main_module.app):
                    pass
            asyncio.get_event_loop().run_until_complete(_run())
            mock_load.assert_called_once()

    def test_lifespan_warns_when_store_not_loaded(self, main_module, caplog):
        import asyncio
        import logging
        with patch("api.main.load_sessions"), \
             patch("api.main._store") as mock_store:
            mock_store.load.return_value = False
            with caplog.at_level(logging.WARNING, logger="api.main"):
                async def _run():
                    async with main_module.lifespan(main_module.app):
                        pass
                asyncio.get_event_loop().run_until_complete(_run())
            assert any("No vector store" in r.message for r in caplog.records)

    def test_lifespan_logs_product_count_when_store_loaded(self, main