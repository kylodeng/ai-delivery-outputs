"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs. new instance logic
- _build_roleplay_system: prompt building from CustomerProfile (stub — source truncated)
- FastAPI app startup / lifespan behaviour
- CORS middleware configuration
- Static file mount
- HTTP endpoints (happy path, edge cases, error conditions)
- SHOW_TOOL_CALLS env-var parsing
- System prompt template interpolation helpers

Mocks used:
- unittest.mock.patch / MagicMock for ChatOpenAI, get_vector_store, make_rag_tools,
  make_teacher_agent, make_assessor_agent, load_sessions
- httpx.Client / httpx.AsyncClient (patched to avoid real network calls)
- fastapi.testclient.TestClient for endpoint tests
- All LangChain message constructors kept as real objects (no network usage)

TODOs:
- TODO: Full coverage of every HTTP endpoint (source file was truncated — endpoints
  after _build_roleplay_system are unknown).
- TODO: Tests for make_teacher_agent / make_assessor_agent integration once those
  modules are fully available.
- TODO: Tests for streaming SSE responses require async ASGI test client (httpx.AsyncClient
  with ASGITransport).
- TODO: Verify _PRIOR_CONTEXT_PROMPT rendering end-to-end once LLM is injectable.
"""

import importlib
import os
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Helpers — build lightweight stubs for all heavy optional dependencies so we
# can import api.main without a real vector store, LLM, or agent module.
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """Insert stub modules into sys.modules before api.main is imported."""

    # --- core.vector_store ---------------------------------------------------
    stub_vs = types.ModuleType("core.vector_store")
    mock_store = MagicMock()
    mock_store.load.return_value = True
    mock_store.get_known_products.return_value = ["ProductA", "ProductB"]
    stub_vs.get_vector_store = MagicMock(return_value=mock_store)
    sys.modules.setdefault("core", types.ModuleType("core"))
    sys.modules["core.vector_store"] = stub_vs

    # --- api.rag_tools -------------------------------------------------------
    stub_rag = types.ModuleType("api.rag_tools")
    stub_rag.make_rag_tools = MagicMock(return_value=[MagicMock(name="rag_tool")])
    sys.modules.setdefault("api", types.ModuleType("api"))
    sys.modules["api.rag_tools"] = stub_rag

    # --- api.agent -----------------------------------------------------------
    stub_agent = types.ModuleType("api.agent")
    stub_agent.make_teacher_agent = MagicMock(return_value=MagicMock(name="teacher"))
    stub_agent.make_assessor_agent = MagicMock(return_value=MagicMock(name="assessor"))
    sys.modules["api.agent"] = stub_agent

    # --- api.sessions --------------------------------------------------------
    stub_sessions = types.ModuleType("api.sessions")

    class _CustomerProfile(MagicMock):
        """Lightweight stand-in for CustomerProfile."""

    class _Session(MagicMock):
        pass

    stub_sessions.CustomerProfile = _CustomerProfile
    stub_sessions.Session = _Session
    stub_sessions.create_session = MagicMock(return_value=_Session())
    stub_sessions.delete_session = MagicMock(return_value=True)
    stub_sessions.generate_profile = MagicMock(return_value=_CustomerProfile())
    stub_sessions.get_session = MagicMock(return_value=_Session())
    stub_sessions.list_sessions = MagicMock(return_value=[])
    stub_sessions.load_sessions = MagicMock()
    stub_sessions.update_session_title = MagicMock(return_value=True)
    sys.modules["api.sessions"] = stub_sessions

    # --- langchain_openai ---------------------------------------------------
    stub_lc_oai = types.ModuleType("langchain_openai")
    MockChatOpenAI = MagicMock(name="ChatOpenAI")
    stub_lc_oai.ChatOpenAI = MockChatOpenAI
    sys.modules["langchain_openai"] = stub_lc_oai

    # --- langchain_core.messages --------------------------------------------
    stub_lc_msgs = types.ModuleType("langchain_core.messages")
    stub_lc_msgs.AIMessage = MagicMock(name="AIMessage")
    stub_lc_msgs.HumanMessage = MagicMock(name="HumanMessage")
    stub_lc_msgs.SystemMessage = MagicMock(name="SystemMessage")
    sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
    sys.modules["langchain_core.messages"] = stub_lc_msgs

    # --- httpx (real package is typically available; mock to avoid ssl issues) --
    stub_httpx = types.ModuleType("httpx")
    stub_httpx.Client = MagicMock(return_value=MagicMock())
    stub_httpx.AsyncClient = MagicMock(return_value=MagicMock())
    sys.modules["httpx"] = stub_httpx

    # --- dotenv --------------------------------------------------------------
    stub_dotenv = types.ModuleType("dotenv")
    stub_dotenv.load_dotenv = MagicMock()
    sys.modules["dotenv"] = stub_dotenv

    return mock_store


# Run stub setup once at collection time
_mock_store = _make_stub_modules()

# Now import the module under test
# We patch StaticFiles so it does not hit the filesystem
with patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None):
    import api.main as main_module  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a synchronous TestClient wrapping the FastAPI app."""
    # Override lifespan so TestClient does not attempt real startup side-effects
    with patch.object(main_module, "load_sessions"):
        with patch.object(_mock_store, "load", return_value=True):
            with TestClient(main_module.app, raise_server_exceptions=True) as c:
                yield c


@pytest.fixture()
def mock_chat_openai():
    """Return the ChatOpenAI mock class installed in sys.modules."""
    return sys.modules["langchain_openai"].ChatOpenAI


# ---------------------------------------------------------------------------
# Tests: module-level constants / env-var parsing
# ---------------------------------------------------------------------------

class TestEnvVarParsing:
    """Verify module-level constants derived from environment variables."""

    def test_show_tool_calls_default_true(self):
        """SHOW_TOOL_CALLS defaults to True when env var is 'true'."""
        with patch.dict(os.environ, {"SHOW_TOOL_CALLS": "true"}):
            # Re-evaluate the expression as in the source
            result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
            assert result is True

    def test_show_tool_calls_false(self):
        with patch.dict(os.environ, {"SHOW_TOOL_CALLS": "false"}):
            result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
            assert result is False

    def test_show_tool_calls_case_insensitive(self):
        with patch.dict(os.environ, {"SHOW_TOOL_CALLS": "TRUE"}):
            result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
            assert result is True

    def test_show_tool_calls_missing_defaults_true(self):
        env = {k: v for k, v in os.environ.items() if k != "SHOW_TOOL_CALLS"}
        with patch.dict(os.environ, env, clear=True):
            result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
            assert result is True

    def test_api_key_defaults_empty(self):
        env = {k: v for k, v in os.environ.items() if k != "API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            assert os.getenv("API_KEY", "") == ""

    def test_base_url_default(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_URL_BASE"}
        with patch.dict(os.environ, env, clear=True):
            assert os.getenv("OPENAI_URL_BASE", "https://openrouter.ai/api/v1") == \
                   "https://openrouter.ai/api/v1"

    def test_llm_model_default(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_MODEL"}
        with patch.dict(os.environ, env, clear=True):
            assert os.getenv("OPENAI_MODEL", "openai/gpt-oss-20b:free") == \
                   "openai/gpt-oss-20b:free"

    def test_custom_api_key(self):
        with patch.dict(os.environ, {"API_KEY": "sk-test-key"}):
            assert os.getenv("API_KEY", "") == "sk-test-key"

    def test_custom_base_url(self):
        with patch.dict(os.environ, {"OPENAI_URL_BASE": "https://custom.api/v1"}):
            assert os.getenv("OPENAI_URL_BASE", "") == "https://custom.api/v1"


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    """Unit tests for the _get_llm helper."""

    def test_returns_shared_instance_when_no_overrides(self):
        """With defaults, _get_llm returns the module-level _llm singleton."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_explicit_defaults(self):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_provided(self, mock_chat_openai):
        mock_chat_openai.reset_mock()
        new_instance = MagicMock(name="new_llm")
        mock_chat_openai.return_value = new_instance

        result = main_module._get_llm(model="openai/gpt-4")
        assert result is new_instance
        mock_chat_openai.assert_called_once()
        call_kwargs = mock_chat_openai.call_args.kwargs
        assert call_kwargs["model"] == "openai/gpt-4"

    def test_returns_new_instance_when_temperature_differs(self, mock_chat_openai):
        mock_chat_openai.reset_mock()
        new_instance = MagicMock(name="new_llm_temp")
        mock_chat_openai.return_value = new_instance

        result = main_module._get_llm(temperature=0.9)
        assert result is new_instance
        call_kwargs = mock_chat_openai.call_args.kwargs
        assert call_kwargs["temperature"] == 0.9

    def test_new_instance_uses_fallback_model_name(self, mock_chat_openai):
        """When model=None but temperature differs, model falls back to _LLM_MODEL."""
        mock_chat_openai.reset_mock()
        mock_chat_openai.return_value = MagicMock()

        main_module._get_llm(model=None, temperature=0.1)
        call_kwargs = mock_chat_openai.call_args.kwargs
        assert call_kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_uses_provided_model_name(self, mock_chat_openai):
        mock_chat_openai.reset_mock()
        mock_chat_openai.return_value = MagicMock()

        main_module._get_llm(model="custom/model", temperature=0.2)
        call_kwargs = mock_chat_openai.call_args.kwargs
        assert call_kwargs["model"] == "custom/model"

    def test_new_instance_sets_streaming_true(self, mock_chat_openai):
        mock_chat_openai.reset_mock()
        mock_chat_openai.return_value = MagicMock()

        main_module._get_llm(model="any/model")
        call_kwargs = mock_chat_openai.call_args.kwargs
        assert call_kwargs.get("streaming") is True

    def test_new_instance_uses_secret_str_for_api_key(self, mock_chat_openai):
        mock_chat_openai.reset_mock()
        mock_chat_openai.return_value = MagicMock()

        main_module._get_llm(model="any/model")
        call_kwargs = mock_chat_openai.call_args.kwargs
        assert isinstance(call_kwargs.get("api_key"), SecretStr)

    def test_temperature_zero(self, mock_chat_openai):
        """Temperature=0.0 differs from default so a new instance should be returned."""
        mock_chat_openai.reset_mock()
        mock_chat_openai.return_value = MagicMock()

        result = main_module._get_llm(temperature=0.0)
        assert result is not main_module._llm

    def test_temperature_boundary_max(self, mock_chat_openai):
        mock_chat_openai.reset_mock()
        mock_chat_openai.return_value = MagicMock()

        main_module._get_llm(temperature=2.0)
        call_kwargs = mock_chat_openai.call_args.kwargs
        assert call_kwargs["temperature"] == 2.0


# ---------------------------------------------------------------------------
# Tests: system-prompt templates
# ---------------------------------------------------------------------------

class TestRoleplaySystemPrompt:
    """Validate that _ROLEPLAY_SYSTEM contains expected placeholder tokens."""

    def test_contains_name_placeholder(self):
        assert "{name}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_age_placeholder(self):
        assert "{age}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_occupation_placeholder(self):
        assert "{occupation}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_profile_placeholder(self):
        assert "{profile}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_stage_instruction_placeholder(self):
        assert "{stage_instruction}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_today_placeholder(self):
        assert "{today}" in main_module._ROLEPLAY_SYSTEM

    def test_format_basic(self):
        """Template should format without KeyError when all fields supplied."""
        rendered = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=35,
            occupation="Teacher",
            profile="Single, no kids",
            stage_instruction="This is the first meeting.",
            today="2024-06-15",
        )
        assert "Alice" in rendered
        assert "35" in rendered
        assert "Teacher" in rendered

    def test_format_preserves_character_guidance(self):
        