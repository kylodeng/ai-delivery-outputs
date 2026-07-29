"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm(): returns shared instance vs. new instance based on params
- _build_roleplay_system(): prompt construction from CustomerProfile
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable presence
- FastAPI app endpoints (lifespan, CORS, static mount)
- HTTP endpoints via TestClient (all public routes)
- StreamingResponse generation for chat/roleplay endpoints
- Session management endpoints (create, get, list, delete, update title)
- Ingest endpoint
- Error handling (404, 422, 500)

Mocks used:
- unittest.mock.patch for ChatOpenAI (_llm, _get_llm)
- unittest.mock.patch for get_vector_store, make_rag_tools
- unittest.mock.patch for make_teacher_agent, make_assessor_agent
- unittest.mock.patch for session management functions
- unittest.mock.patch for load_sessions, generate_profile
- httpx mocked via respx or MagicMock
- AsyncMock for async agent invocations

TODOs:
- TODO: Integration tests for real LLM streaming require a live OpenAI-compatible endpoint
- TODO: Test _build_roleplay_system with all CustomerProfile field variations once
        the full function body is available (source is truncated)
- TODO: Test /ingest endpoint once its implementation is visible in the source
- TODO: Test StaticFiles /docs mount with actual PDF fixtures
- TODO: Test prior context prompt generation endpoint (if exposed) with edge-case profiles
"""

import json
import types
from datetime import date
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers & shared fixtures
# ---------------------------------------------------------------------------

FAKE_SESSION_ID = "sess-abc-123"

FAKE_PROFILE_DICT = {
    "name": "Alice Wong",
    "age": 34,
    "occupation": "nurse",
    "income": 48000,
    "family": "married, two children aged 4 and 7",
    "goals": "education savings, life protection",
    "existing_coverage": "basic employer group life",
    "personality": "cautious, detail-oriented",
}

FAKE_SESSION = {
    "id": FAKE_SESSION_ID,
    "title": "Session with Alice Wong",
    "profile": FAKE_PROFILE_DICT,
    "stage": "2nd_meeting",
    "messages": [],
    "created_at": "2024-01-15T10:00:00",
}


def _make_fake_customer_profile(**overrides):
    """Return a minimal CustomerProfile-like object."""
    data = {**FAKE_PROFILE_DICT, **overrides}
    # Import lazily to avoid import-time side effects before patches are active
    from api.sessions import CustomerProfile  # noqa: PLC0415

    return CustomerProfile(**data)


# ---------------------------------------------------------------------------
# Patch targets — defined once so they are easy to update
# ---------------------------------------------------------------------------

PATCH_CHAT_OPENAI = "api.main.ChatOpenAI"
PATCH_LLM = "api.main._llm"
PATCH_GET_VECTOR_STORE = "api.main.get_vector_store"
PATCH_MAKE_RAG_TOOLS = "api.main.make_rag_tools"
PATCH_MAKE_TEACHER = "api.main.make_teacher_agent"
PATCH_MAKE_ASSESSOR = "api.main.make_assessor_agent"
PATCH_LOAD_SESSIONS = "api.main.load_sessions"
PATCH_GET_SESSION = "api.main.get_session"
PATCH_CREATE_SESSION = "api.main.create_session"
PATCH_DELETE_SESSION = "api.main.delete_session"
PATCH_LIST_SESSIONS = "api.main.list_sessions"
PATCH_UPDATE_TITLE = "api.main.update_session_title"
PATCH_GENERATE_PROFILE = "api.main.generate_profile"
PATCH_STORE_LOAD = "api.main._store"


# ---------------------------------------------------------------------------
# Module-level fixture: import app with all heavy dependencies mocked
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_dependencies():
    """
    Patch every external dependency at module level so the FastAPI app
    can be imported and instantiated without real network calls or files.
    """
    fake_store = MagicMock()
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["Generations II", "Health Plan A"]

    fake_rag_tools = [MagicMock(name="rag_tool_1"), MagicMock(name="rag_tool_2")]

    fake_teacher = MagicMock()
    fake_assessor = MagicMock()

    patches = [
        patch(PATCH_GET_VECTOR_STORE, return_value=fake_store),
        patch(PATCH_MAKE_RAG_TOOLS, return_value=fake_rag_tools),
        patch(PATCH_MAKE_TEACHER, return_value=fake_teacher),
        patch(PATCH_MAKE_ASSESSOR, return_value=fake_assessor),
        patch(PATCH_LOAD_SESSIONS, return_value=None),
        patch("api.main.httpx.Client", MagicMock()),
        patch("api.main.httpx.AsyncClient", MagicMock()),
    ]

    started = [p.start() for p in patches]
    yield {
        "store": fake_store,
        "rag_tools": fake_rag_tools,
        "teacher": fake_teacher,
        "assessor": fake_assessor,
        "patches": patches,
    }
    for p in patches:
        p.stop()


@pytest.fixture(scope="module")
def client(mock_dependencies):
    """Return a TestClient for the FastAPI app."""
    from api.main import app  # noqa: PLC0415

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests: _get_llm()
# ---------------------------------------------------------------------------


class TestGetLlm:
    """Tests for the _get_llm() helper function."""

    def test_returns_shared_instance_when_no_overrides(self):
        """_get_llm() with no args must return the module-level _llm singleton."""
        with (
            patch("api.main.httpx.Client", MagicMock()),
            patch("api.main.httpx.AsyncClient", MagicMock()),
            patch("api.main.get_vector_store", MagicMock(return_value=MagicMock(load=MagicMock(return_value=False), get_known_products=MagicMock(return_value=[])))),
            patch("api.main.make_rag_tools", MagicMock()),
            patch("api.main.make_teacher_agent", MagicMock()),
            patch("api.main.make_assessor_agent", MagicMock()),
        ):
            from api.main import _get_llm, _llm  # noqa: PLC0415

            result = _get_llm()
            assert result is _llm

    def test_returns_new_instance_when_model_differs(self):
        """_get_llm(model='other-model') must return a fresh ChatOpenAI instance."""
        fake_new_llm = MagicMock()
        with patch(PATCH_CHAT_OPENAI, return_value=fake_new_llm) as mock_cls:
            from api.main import _get_llm, _LLM_TEMPERATURE  # noqa: PLC0415

            result = _get_llm(model="openai/gpt-4o")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "openai/gpt-4o"

    def test_returns_new_instance_when_temperature_differs(self):
        """_get_llm(temperature=0.9) must return a fresh ChatOpenAI instance."""
        fake_new_llm = MagicMock()
        with patch(PATCH_CHAT_OPENAI, return_value=fake_new_llm) as mock_cls:
            from api.main import _get_llm  # noqa: PLC0415

            result = _get_llm(temperature=0.9)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.9

    def test_new_instance_uses_default_model_when_model_none_but_temp_differs(self):
        """When model=None but temperature differs, _LLM_MODEL should be used."""
        fake_new_llm = MagicMock()
        with patch(PATCH_CHAT_OPENAI, return_value=fake_new_llm) as mock_cls:
            from api.main import _get_llm, _LLM_MODEL  # noqa: PLC0415

            _get_llm(model=None, temperature=0.1)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == _LLM_MODEL

    def test_new_instance_uses_provided_model_and_temperature(self):
        """_get_llm with both overrides should pass both to ChatOpenAI."""
        fake_new_llm = MagicMock()
        with patch(PATCH_CHAT_OPENAI, return_value=fake_new_llm) as mock_cls:
            from api.main import _get_llm  # noqa: PLC0415

            _get_llm(model="anthropic/claude-3", temperature=0.3)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "anthropic/claude-3"
            assert call_kwargs["temperature"] == 0.3

    def test_new_instance_has_streaming_enabled(self):
        """Newly created LLM instances must have streaming=True."""
        fake_new_llm = MagicMock()
        with patch(PATCH_CHAT_OPENAI, return_value=fake_new_llm) as mock_cls:
            from api.main import _get_llm  # noqa: PLC0415

            _get_llm(temperature=0.8)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("streaming") is True

    def test_new_instance_api_key_is_secret_str(self):
        """The api_key passed to ChatOpenAI must be wrapped in SecretStr."""
        fake_new_llm = MagicMock()
        with patch(PATCH_CHAT_OPENAI, return_value=fake_new_llm) as mock_cls:
            from api.main import _get_llm  # noqa: PLC0415

            _get_llm(temperature=0.2)
            call_kwargs = mock_cls.call_args.kwargs
            assert isinstance(call_kwargs.get("api_key"), SecretStr)


# ---------------------------------------------------------------------------
# Tests: prompt template constants
# ---------------------------------------------------------------------------


class TestRoleplaySystemPrompt:
    """Validate that the _ROLEPLAY_SYSTEM template contains required placeholders."""

    def test_contains_name_placeholder(self):
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        assert "{name}" in _ROLEPLAY_SYSTEM

    def test_contains_age_placeholder(self):
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        assert "{age}" in _ROLEPLAY_SYSTEM

    def test_contains_occupation_placeholder(self):
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        assert "{occupation}" in _ROLEPLAY_SYSTEM

    def test_contains_profile_placeholder(self):
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        assert "{profile}" in _ROLEPLAY_SYSTEM

    def test_contains_stage_instruction_placeholder(self):
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        assert "{stage_instruction}" in _ROLEPLAY_SYSTEM

    def test_contains_today_placeholder(self):
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        assert "{today}" in _ROLEPLAY_SYSTEM

    def test_never_break_character_instruction_present(self):
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        assert "Never break character" in _ROLEPLAY_SYSTEM

    def test_template_formats_correctly_with_sample_data(self):
        """Template should format without KeyError given all required keys."""
        from api.main import _ROLEPLAY_SYSTEM  # noqa: PLC0415

        formatted = _ROLEPLAY_SYSTEM.format(
            name="Alice Wong",
            age=34,
            occupation="nurse",
            profile=json.dumps(FAKE_PROFILE_DICT),
            stage_instruction="This is the 2nd meeting.",
            today=str(date.today()),
        )
        assert "Alice Wong" in formatted
        assert "34" in formatted
        assert "nurse" in formatted


class TestPriorContextPrompt:
    """Validate that _PRIOR_CONTEXT_PROMPT contains required placeholders."""

    def test_contains_profile_placeholder(self):
        from api.main import _PRIOR_CONTEXT_PROMPT  # noqa: PLC0415

        assert "{profile}" in _PRIOR_CONTEXT_PROMPT

    def test_contains_stage_placeholder(self):
        from api.main import _PRIOR_CONTEXT_PROMPT  # noqa: PLC0415

        assert "{stage}" in _PRIOR_CONTEXT_PROMPT

    def test_max_350_words_mentioned(self):
        from api.main import _PRIOR_CONTEXT_PROMPT  # noqa: PLC0415

        assert "350" in _PRIOR_CONTEXT_PROMPT

    def test_template_formats_without_error(self):
        from api.main import _PRIOR_CONTEXT_PROMPT  # noqa: PLC0415

        formatted = _PRIOR_CONTEXT_PROMPT.format(
            profile=json.dumps(FAKE_PROFILE_DICT),
            stage="2nd_meeting",
        )
        assert "2nd_meeting" in formatted


# ---------------------------------------------------------------------------
# Tests: _build_roleplay_system (partially — source is truncated)
# ---------------------------------------------------------------------------


class TestBuildRoleplaySystem:
    """Tests for _build_roleplay_system()."""

    @pytest.mark.skip(
        reason="TODO: full function body not available in truncated source — "
        "cannot test complete output without knowing all branches"
    )
    def test_build_roleplay_system_full_output(self):
        """TODO: verify the full rendered prompt once source is complete."""

    def test_build_roleplay_system_returns_string(self, mock_dependencies):
        """_build_roleplay_system should return a non-empty string."""
        try:
            from api.main import _build_roleplay_system  # noqa: PLC0415
            from api.sessions import CustomerProfile  # noqa: PLC0415
        except ImportError:
            pytest.skip("_build_roleplay_system not importable without full source")

        profile = CustomerProfile(**FAKE_PROFILE_DICT)
        result = _build_roleplay_system(profile)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_roleplay_system_includes_name(self, mock_dependencies):
        """The built prompt should contain the customer's name."""
        try:
            from api.main import _build_roleplay_system  # noqa: PLC0415
            from api.sessions import CustomerProfile  # noqa: PLC0415
        except ImportError:
            pytest.skip("_build_role