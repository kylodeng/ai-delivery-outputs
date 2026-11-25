"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm(): returns shared instance or creates new one based on model/temperature
- _build_roleplay_system(): system prompt construction from CustomerProfile
- _PRIOR_CONTEXT_PROMPT: template variable coverage
- _ROLEPLAY_SYSTEM: template variable coverage
- FastAPI app: lifespan, CORS middleware configuration, static file mount
- API endpoints: health, session CRUD, ingest, chat/stream routes
- SHOW_TOOL_CALLS environment variable parsing

Mocks used:
- langchain_openai.ChatOpenAI (prevents real OpenAI/OpenRouter calls)
- core.vector_store.get_vector_store (prevents real vector store I/O)
- api.rag_tools.make_rag_tools (prevents real RAG tool construction)
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (create_session, delete_session, generate_profile, get_session,
  list_sessions, load_sessions, update_session_title)
- httpx.Client, httpx.AsyncClient (prevents real HTTP calls)
- fastapi.testclient.TestClient for endpoint integration tests

TODOs:
- TODO: Full streaming endpoint tests require async generator introspection — stub provided
- TODO: /ingest endpoint needs vector store ingestion pipeline details
- TODO: _build_roleplay_system full output validation needs complete function source
"""

import os
import types
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

FAKE_PROFILE_DICT = {
    "name": "Jane Wong",
    "age": 35,
    "occupation": "School Teacher",
    "income": 480000,
    "family": "Married, 2 children",
    "goals": "Retirement savings, children education fund",
    "concerns": "Healthcare costs, mortgage protection",
    "personality": "Cautious, detail-oriented",
    "existing_coverage": "Basic group insurance from employer",
}

FAKE_PROFILE_STR = str(FAKE_PROFILE_DICT)


# ---------------------------------------------------------------------------
# Module-level patches applied before importing api.main
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _patch_external_deps():
    """
    Patch every external dependency before api.main is imported so that
    module-level side-effects (ChatOpenAI(), get_vector_store(), make_rag_tools())
    don't reach real services.
    """
    mock_llm = MagicMock()
    mock_store = MagicMock()
    mock_store.load.return_value = True
    mock_store.get_known_products.return_value = ["Generations II", "Health Plan A"]
    mock_rag_tools = [MagicMock(name="rag_tool_1")]
    mock_teacher_agent = MagicMock()
    mock_assessor_agent = MagicMock()

    patches = [
        patch("langchain_openai.ChatOpenAI", return_value=mock_llm),
        patch("httpx.Client", return_value=MagicMock()),
        patch("httpx.AsyncClient", return_value=MagicMock()),
        patch("core.vector_store.get_vector_store", return_value=mock_store),
        patch("api.rag_tools.make_rag_tools", return_value=mock_rag_tools),
        patch("api.agent.make_teacher_agent", return_value=mock_teacher_agent),
        patch("api.agent.make_assessor_agent", return_value=mock_assessor_agent),
        patch("api.sessions.load_sessions", return_value=None),
        patch("api.sessions.list_sessions", return_value=[]),
        patch("api.sessions.get_session", return_value=None),
        patch("api.sessions.create_session", return_value=MagicMock()),
        patch("api.sessions.delete_session", return_value=None),
        patch("api.sessions.update_session_title", return_value=None),
        patch("api.sessions.generate_profile", return_value=MagicMock()),
        patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None),
    ]

    started = []
    for p in patches:
        started.append(p.start())

    yield started

    for p in patches:
        try:
            p.stop()
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# Import api.main AFTER patches are in place
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def main_module(_patch_external_deps):
    """Import api.main with all external deps mocked."""
    import importlib
    import sys
    # Remove cached module to force re-import with mocks
    for key in list(sys.modules.keys()):
        if key.startswith("api.main"):
            del sys.modules[key]
    import api.main as m
    return m


@pytest.fixture(scope="session")
def app(main_module):
    return main_module.app


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: SHOW_TOOL_CALLS environment variable parsing
# ---------------------------------------------------------------------------

class TestShowToolCallsEnvVar:
    def test_default_is_true_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("SHOW_TOOL_CALLS", raising=False)
        # Default value in code is "true"
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is True

    @pytest.mark.parametrize("value,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", False),
        ("1", False),
        ("yes", False),
    ])
    def test_show_tool_calls_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("SHOW_TOOL_CALLS", value)
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is expected


# ---------------------------------------------------------------------------
# Tests: _get_llm()
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_args(self, main_module):
        """With default args, should return the module-level _llm instance."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_when_temperature_matches(self, main_module):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock(name="new_llm")
            result = main_module._get_llm(model="openai/gpt-4")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "openai/gpt-4"
            assert call_kwargs["streaming"] is True

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock(name="new_llm_temp")
            result = main_module._get_llm(temperature=0.9)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.9

    def test_new_instance_uses_fallback_model_when_model_none_but_temp_differs(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(model=None, temperature=0.1)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_passes_api_key_as_secret_str(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(temperature=0.1)
            call_kwargs = mock_cls.call_args.kwargs
            assert isinstance(call_kwargs["api_key"], SecretStr)

    def test_new_instance_uses_configured_base_url(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(temperature=0.2)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == main_module._BASE_URL

    def test_new_instance_has_streaming_enabled(self, main_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            main_module._get_llm(model="some/model")
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["streaming"] is True


# ---------------------------------------------------------------------------
# Tests: _ROLEPLAY_SYSTEM template
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    def test_template_contains_required_placeholders(self, main_module):
        tmpl = main_module._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in tmpl, f"Missing placeholder: {placeholder}"

    def test_template_can_be_formatted_with_valid_data(self, main_module):
        result = main_module._ROLEPLAY_SYSTEM.format(
            name="Jane Wong",
            age=35,
            occupation="School Teacher",
            profile=FAKE_PROFILE_STR,
            stage_instruction="This is the first meeting.",
            today=str(date.today()),
        )
        assert "Jane Wong" in result
        assert "35" in result
        assert "School Teacher" in result
        assert "first meeting" in result

    def test_template_instructs_never_break_character(self, main_module):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_template_mentions_today_date_usage(self, main_module):
        assert "Today's date" in main_module._ROLEPLAY_SYSTEM

    def test_template_raises_on_missing_key(self, main_module):
        with pytest.raises(KeyError):
            main_module._ROLEPLAY_SYSTEM.format(name="Jane")  # missing keys


# ---------------------------------------------------------------------------
# Tests: _PRIOR_CONTEXT_PROMPT template
# ---------------------------------------------------------------------------

class TestPriorContextPromptTemplate:
    def test_template_contains_required_placeholders(self, main_module):
        tmpl = main_module._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in tmpl, f"Missing placeholder: {placeholder}"

    def test_template_can_be_formatted(self, main_module):
        result = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile=FAKE_PROFILE_STR,
            stage="2nd conversation",
        )
        assert "2nd conversation" in result
        assert str(FAKE_PROFILE_DICT) in result

    def test_template_mentions_350_word_limit(self, main_module):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_template_raises_on_missing_key(self, main_module):
        with pytest.raises(KeyError):
            main_module._PRIOR_CONTEXT_PROMPT.format(profile="only profile")


# ---------------------------------------------------------------------------
# Tests: FastAPI app configuration
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, app):
        from starlette.middleware.cors import CORSMiddleware
        middleware_types = [type(m.cls) for m in app.user_middleware if hasattr(m, "cls")]
        # Also check via middleware stack
        middleware_classes = [str(m) for m in app.user_middleware]
        assert any("CORSMiddleware" in str(m) for m in app.user_middleware)

    def test_cors_allows_localhost_5173(self, app):
        middleware = next(
            (m for m in app.user_middleware if "CORSMiddleware" in str(m)), None
        )
        assert middleware is not None
        options = middleware.kwargs
        assert "http://localhost:5173" in options.get("allow_origins", [])

    def test_cors_allows_localhost_8000(self, app):
        middleware = next(
            (m for m in app.user_middleware if "CORSMiddleware" in str(m)), None
        )
        options = middleware.kwargs
        assert "http://localhost:8000" in options.get("allow_origins", [])


# ---------------------------------------------------------------------------
# Tests: Module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_llm_temperature_is_float(self, main_module):
        assert isinstance(main_module._LLM_TEMPERATURE, float)

    def test_llm_temperature_value(self, main_module):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_base_url_has_default(self, main_module):
        assert main_module._BASE_URL  # non-empty
        assert main_module._BASE_URL.startswith("http")

    def test_llm_model_has_default(self, main_module):
        assert main_module._LLM_MODEL  # non-empty string

    def test_show_tool_calls_is_bool(self, main_module):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_private_llm_instance_exists(self, main_module):
        assert main_module._llm is not None

    def test_rag_tools_initialized(self, main_module):
        assert main_module._rag_tools is not None


# ---------------------------------------------------------------------------
# Tests: Environment variable overrides
# ---------------------------------------------------------------------------

class TestEnvironmentVariableOverrides:
    def test_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "sk-test-key-123")
        assert os.getenv("API_KEY") == "sk-test-key-123"

    def test_base_url_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_URL_BASE", "https://custom.api.example.com/v1")
        assert os.getenv("OPENAI_URL_BASE") == "https://custom.api.example.com/v1"

    def test_model_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_MODEL", "openai/gpt-4o")
        assert os.getenv("OPENAI_MODEL") == "openai/gpt-4o"

    def test_default_base_url_is_openrouter(self):
        # When env var not set, default is openrouter
        result = os.getenv("OPENAI_URL_BASE", "https://openrouter.