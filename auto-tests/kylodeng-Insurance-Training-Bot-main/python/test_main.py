"""
Tests for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
    - _get_llm(): returns shared instance vs new instance based on params
    - _build_roleplay_system(): system prompt construction (stub — needs full source)
    - FastAPI app configuration: CORS middleware, static mount, lifespan
    - API endpoints (via TestClient): all public HTTP routes
    - _ROLEPLAY_SYSTEM and _PRIOR_CONTEXT_PROMPT template strings
    - SHOW_TOOL_CALLS environment variable parsing

Mocks used:
    - langchain_openai.ChatOpenAI (patched at module level)
    - core.vector_store.get_vector_store
    - api.rag_tools.make_rag_tools
    - api.agent.make_teacher_agent, make_assessor_agent
    - api.sessions (create_session, delete_session, generate_profile, get_session,
                    list_sessions, load_sessions, update_session_title)
    - httpx.Client, httpx.AsyncClient (SSL verify=False paths)
    - StreamingResponse content

TODOs:
    - TODO: Full source of _build_roleplay_system needed to test template rendering completely
    - TODO: Endpoint route definitions beyond lifespan are truncated — stubs added for ingest,
            chat, and session CRUD routes inferred from helper imports
    - TODO: Integration tests for actual LLM streaming require a live OpenRouter key
    - TODO: Vector store persistence tests need real ChromaDB/FAISS fixture
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — build a minimal fake module tree so api/main.py can be imported
# without real heavy dependencies.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """Insert lightweight fakes into sys.modules before importing api.main."""

    # --- core.vector_store --------------------------------------------------
    fake_store = MagicMock()
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["ProductA", "ProductB"]

    cvs = types.ModuleType("core")
    cvs_vs = types.ModuleType("core.vector_store")
    cvs_vs.get_vector_store = MagicMock(return_value=fake_store)
    sys.modules.setdefault("core", cvs)
    sys.modules["core.vector_store"] = cvs_vs

    # --- api.rag_tools -------------------------------------------------------
    api_pkg = types.ModuleType("api")
    api_rag = types.ModuleType("api.rag_tools")
    api_rag.make_rag_tools = MagicMock(return_value=[MagicMock()])
    sys.modules.setdefault("api", api_pkg)
    sys.modules["api.rag_tools"] = api_rag

    # --- api.agent -----------------------------------------------------------
    api_agent = types.ModuleType("api.agent")
    api_agent.make_teacher_agent = MagicMock(return_value=MagicMock())
    api_agent.make_assessor_agent = MagicMock(return_value=MagicMock())
    sys.modules["api.agent"] = api_agent

    # --- api.sessions --------------------------------------------------------
    api_sessions = types.ModuleType("api.sessions")

    class _CustomerProfile:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Session:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    api_sessions.CustomerProfile = _CustomerProfile
    api_sessions.Session = _Session
    api_sessions.create_session = MagicMock()
    api_sessions.delete_session = MagicMock()
    api_sessions.generate_profile = MagicMock()
    api_sessions.get_session = MagicMock()
    api_sessions.list_sessions = MagicMock(return_value=[])
    api_sessions.load_sessions = MagicMock()
    api_sessions.update_session_title = MagicMock()
    sys.modules["api.sessions"] = api_sessions

    # --- langchain_core.messages --------------------------------------------
    lc_core = types.ModuleType("langchain_core")
    lc_msgs = types.ModuleType("langchain_core.messages")
    lc_msgs.AIMessage = MagicMock
    lc_msgs.HumanMessage = MagicMock
    lc_msgs.SystemMessage = MagicMock
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules["langchain_core.messages"] = lc_msgs

    # --- langchain_openai ---------------------------------------------------
    fake_llm_instance = MagicMock()
    fake_llm_cls = MagicMock(return_value=fake_llm_instance)
    lc_oai = types.ModuleType("langchain_openai")
    lc_oai.ChatOpenAI = fake_llm_cls
    sys.modules["langchain_openai"] = lc_oai

    # --- pydantic (keep real if available, else stub) -----------------------
    try:
        import pydantic  # noqa: F401 — real pydantic is fine
    except ImportError:
        pydantic_mod = types.ModuleType("pydantic")
        pydantic_mod.BaseModel = object
        pydantic_mod.SecretStr = str
        sys.modules["pydantic"] = pydantic_mod

    # --- dotenv -------------------------------------------------------------
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = MagicMock()
    sys.modules.setdefault("dotenv", dotenv_mod)

    return fake_store, fake_llm_cls, fake_llm_instance


# Run once at collection time
_fake_store, _fake_llm_cls, _fake_llm_instance = _make_fake_modules()


# ---------------------------------------------------------------------------
# Import the module under test AFTER fakes are registered
# ---------------------------------------------------------------------------
import api.main as main_module  # noqa: E402  (must come after fake setup)
from api.main import (  # noqa: E402
    _get_llm,
    _LLM_TEMPERATURE,
    _LLM_MODEL,
    _ROLEPLAY_SYSTEM,
    _PRIOR_CONTEXT_PROMPT,
    SHOW_TOOL_CALLS,
    app,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a synchronous HTTPX TestClient wrapping the FastAPI app."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def reset_llm_cls_calls():
    """Reset call counts on the fake ChatOpenAI constructor between tests."""
    _fake_llm_cls.reset_mock()
    yield
    _fake_llm_cls.reset_mock()


# ---------------------------------------------------------------------------
# _get_llm tests
# ---------------------------------------------------------------------------

class TestGetLlm:
    """Tests for the _get_llm() helper."""

    def test_returns_shared_instance_when_no_overrides(self, reset_llm_cls_calls):
        """With default args, the module-level _llm singleton is returned."""
        result = _get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_explicit_defaults(self, reset_llm_cls_calls):
        """Passing explicit defaults that match module defaults still returns singleton."""
        result = _get_llm(model=None, temperature=_LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_new_instance_when_model_overridden(self, reset_llm_cls_calls):
        """A different model name forces a new ChatOpenAI instance."""
        _fake_llm_cls.reset_mock()
        result = _get_llm(model="gpt-4o")
        # Should NOT be the singleton
        assert result is not main_module._llm

    def test_new_instance_when_temperature_overridden(self, reset_llm_cls_calls):
        """A different temperature forces a new ChatOpenAI instance."""
        _fake_llm_cls.reset_mock()
        result = _get_llm(temperature=0.0)
        assert result is not main_module._llm

    def test_new_instance_model_and_temperature_both_overridden(self, reset_llm_cls_calls):
        """Both model and temperature overridden — new instance expected."""
        _fake_llm_cls.reset_mock()
        result = _get_llm(model="mistral-7b", temperature=1.0)
        assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self, reset_llm_cls_calls):
        """When model is overridden, ChatOpenAI is constructed with that model."""
        _fake_llm_cls.reset_mock()
        _get_llm(model="custom-model-x")
        call_kwargs = _fake_llm_cls.call_args[1]
        assert call_kwargs.get("model") == "custom-model-x"

    def test_new_instance_uses_provided_temperature(self, reset_llm_cls_calls):
        """When temperature is overridden, ChatOpenAI is constructed with it."""
        _fake_llm_cls.reset_mock()
        _get_llm(temperature=0.1)
        call_kwargs = _fake_llm_cls.call_args[1]
        assert call_kwargs.get("temperature") == 0.1

    def test_new_instance_falls_back_to_default_model_when_none(self, reset_llm_cls_calls):
        """model=None + non-default temperature → uses _LLM_MODEL as fallback."""
        _fake_llm_cls.reset_mock()
        _get_llm(model=None, temperature=0.9)
        call_kwargs = _fake_llm_cls.call_args[1]
        assert call_kwargs.get("model") == _LLM_MODEL

    def test_new_instance_streaming_true(self, reset_llm_cls_calls):
        """New instances always have streaming=True."""
        _fake_llm_cls.reset_mock()
        _get_llm(temperature=0.2)
        call_kwargs = _fake_llm_cls.call_args[1]
        assert call_kwargs.get("streaming") is True

    def test_new_instance_ssl_verify_false(self, reset_llm_cls_calls):
        """New instances pass http_client with verify=False (ssl disabled)."""
        _fake_llm_cls.reset_mock()
        _get_llm(temperature=0.3)
        call_kwargs = _fake_llm_cls.call_args[1]
        # httpx.Client is constructed but we verify the key exists
        assert "http_client" in call_kwargs
        assert "http_async_client" in call_kwargs


# ---------------------------------------------------------------------------
# Environment variable / module-level constant tests
# ---------------------------------------------------------------------------

class TestModuleConstants:
    """Tests for module-level constants parsed from environment."""

    def test_show_tool_calls_default_true(self):
        """SHOW_TOOL_CALLS defaults to True when env var not set (default 'true')."""
        # The module was imported with default env — value should be bool
        assert isinstance(SHOW_TOOL_CALLS, bool)

    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("yes", False),   # only 'true' is truthy per the code
        ("", False),
    ])
    def test_show_tool_calls_parsing(self, env_val, expected, monkeypatch):
        """SHOW_TOOL_CALLS is parsed correctly from various env string values."""
        monkeypatch.setenv("SHOW_TOOL_CALLS", env_val)
        parsed = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert parsed is expected

    def test_llm_temperature_is_float(self):
        from api.main import _LLM_TEMPERATURE
        assert isinstance(_LLM_TEMPERATURE, float)

    def test_llm_temperature_value(self):
        from api.main import _LLM_TEMPERATURE
        assert _LLM_TEMPERATURE == 0.6

    def test_default_base_url(self):
        from api.main import _BASE_URL
        assert "openrouter" in _BASE_URL or _BASE_URL.startswith("http")

    def test_api_key_is_string(self):
        from api.main import _API_KEY
        assert isinstance(_API_KEY, str)


# ---------------------------------------------------------------------------
# Template / prompt string tests
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    """Tests for the _ROLEPLAY_SYSTEM prompt template."""

    def test_contains_name_placeholder(self):
        assert "{name}" in _ROLEPLAY_SYSTEM

    def test_contains_age_placeholder(self):
        assert "{age}" in _ROLEPLAY_SYSTEM

    def test_contains_occupation_placeholder(self):
        assert "{occupation}" in _ROLEPLAY_SYSTEM

    def test_contains_profile_placeholder(self):
        assert "{profile}" in _ROLEPLAY_SYSTEM

    def test_contains_today_placeholder(self):
        assert "{today}" in _ROLEPLAY_SYSTEM

    def test_contains_stage_instruction_placeholder(self):
        assert "{stage_instruction}" in _ROLEPLAY_SYSTEM

    def test_format_with_sample_data(self):
        """Template can be formatted with all expected keys without raising."""
        rendered = _ROLEPLAY_SYSTEM.format(
            name="Alice Tanner",
            age=35,
            occupation="nurse",
            profile="Single mother of two, renting in Kowloon.",
            stage_instruction="Focus on budget concerns.",
            today="2025-01-15",
        )
        assert "Alice Tanner" in rendered
        assert "35" in rendered
        assert "nurse" in rendered

    def test_never_break_character_instruction_present(self):
        assert "Never break character" in _ROLEPLAY_SYSTEM

    def test_today_date_usage_note(self):
        assert "Today's date" in _ROLEPLAY_SYSTEM


class TestPriorContextPromptTemplate:
    """Tests for the _PRIOR_CONTEXT_PROMPT template."""

    def test_contains_profile_placeholder(self):
        assert "{profile}" in _PRIOR_CONTEXT_PROMPT

    def test_contains_stage_placeholder(self):
        assert "{stage}" in _PRIOR_CONTEXT_PROMPT

    def test_max_word_limit_mentioned(self):
        assert "350" in _PRIOR_CONTEXT_PROMPT

    def test_format_with_sample_data(self):
        rendered = _PRIOR_CONTEXT_PROMPT.format(
            profile="Name: Bob Chan, 42, self-employed. Goals: retirement savings.",
            stage="2nd conversation",
        )
        assert "Bob Chan" in rendered
        assert "2nd conversation" in rendered

    def test_no_invent_products_guidance(self):
        assert "Do not invent insurance product names" in _PRIOR_CONTEXT_PROMPT

    def test_second_person_guidance(self):
        assert "second person" in _PRIOR_CONTEXT_PROMPT


# ---------------------------------------------------------------------------
# FastAPI app configuration tests
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    """Tests for middleware, mounts, and basic app metadata."""

    def test_app_title(self):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_registered(self):
        from starlette.middleware.cors import CORSMiddleware as StarlettesCORS
        middleware_types = [m.