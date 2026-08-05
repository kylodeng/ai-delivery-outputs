"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
    - _get_llm: returns shared instance vs new instance based on params
    - _build_roleplay_system: system prompt construction from CustomerProfile
    - _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template string integrity
    - FastAPI app endpoints: lifespan, CORS middleware, static mount
    - API route behaviours (chat, sessions, ingest, etc.) via TestClient
    - Streaming responses
    - Error conditions (missing session, bad input, LLM failure)

Mocks used:
    - core.vector_store.get_vector_store (patched before import)
    - api.rag_tools.make_rag_tools
    - api.agent.make_teacher_agent / make_assessor_agent
    - api.sessions (create_session, get_session, delete_session, list_sessions,
                    load_sessions, update_session_title, generate_profile)
    - langchain_openai.ChatOpenAI
    - httpx.Client / httpx.AsyncClient
    - os.getenv (selective)

TODOs:
    - TODO: Full streaming SSE endpoint tests require knowing the exact route
      signatures defined after _build_roleplay_system (source truncated).
    - TODO: /ingest endpoint tests need actual PDF fixture or mock loader.
    - TODO: Integration test with real OpenRouter API key (skip in CI).
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build lightweight stubs for heavy dependencies before import
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: patch everything that fires at import / module-level
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _stub_heavy_deps():
    """
    Pre-populate sys.modules with lightweight stubs so that importing
    api.main does NOT make real network calls or file-system touches.
    """
    # --- langchain stubs ---
    lc_messages = _make_stub_module(
        "langchain_core.messages",
        AIMessage=MagicMock,
        HumanMessage=MagicMock,
        SystemMessage=MagicMock,
    )
    lc_core = _make_stub_module("langchain_core", messages=lc_messages)

    fake_chat_openai_instance = MagicMock()
    fake_chat_openai_instance.stream = MagicMock(return_value=iter([]))
    fake_chat_openai_cls = MagicMock(return_value=fake_chat_openai_instance)
    lc_openai = _make_stub_module(
        "langchain_openai",
        ChatOpenAI=fake_chat_openai_cls,
    )

    # --- pydantic stubs (use real pydantic if available) ---
    try:
        import pydantic  # noqa: F401
    except ImportError:
        pydantic_stub = _make_stub_module(
            "pydantic",
            BaseModel=object,
            SecretStr=lambda x: x,
        )
        sys.modules.setdefault("pydantic", pydantic_stub)

    # --- httpx stubs ---
    try:
        import httpx  # noqa: F401
    except ImportError:
        httpx_stub = _make_stub_module(
            "httpx",
            Client=MagicMock,
            AsyncClient=MagicMock,
        )
        sys.modules.setdefault("httpx", httpx_stub)

    # --- dotenv stub ---
    dotenv_stub = _make_stub_module("dotenv", load_dotenv=lambda: None)

    # --- core / api stubs ---
    fake_store = MagicMock()
    fake_store.load = MagicMock(return_value=True)
    fake_store.get_known_products = MagicMock(return_value=["ProductA", "ProductB"])

    fake_vs_module = _make_stub_module(
        "core.vector_store",
        get_vector_store=MagicMock(return_value=fake_store),
    )
    fake_core = _make_stub_module("core", vector_store=fake_vs_module)

    fake_rag_tools = _make_stub_module(
        "api.rag_tools",
        make_rag_tools=MagicMock(return_value=[MagicMock()]),
    )

    fake_teacher_agent = MagicMock()
    fake_assessor_agent = MagicMock()
    fake_agent_module = _make_stub_module(
        "api.agent",
        make_teacher_agent=MagicMock(return_value=fake_teacher_agent),
        make_assessor_agent=MagicMock(return_value=fake_assessor_agent),
    )

    fake_customer_profile_cls = MagicMock()
    fake_session_cls = MagicMock()

    fake_sessions_module = _make_stub_module(
        "api.sessions",
        CustomerProfile=fake_customer_profile_cls,
        Session=fake_session_cls,
        create_session=MagicMock(),
        delete_session=MagicMock(),
        generate_profile=MagicMock(),
        get_session=MagicMock(),
        list_sessions=MagicMock(return_value=[]),
        load_sessions=MagicMock(),
        update_session_title=MagicMock(),
    )

    overrides = {
        "langchain_core": lc_core,
        "langchain_core.messages": lc_messages,
        "langchain_openai": lc_openai,
        "dotenv": dotenv_stub,
        "core": fake_core,
        "core.vector_store": fake_vs_module,
        "api.rag_tools": fake_rag_tools,
        "api.agent": fake_agent_module,
        "api.sessions": fake_sessions_module,
    }

    original = {}
    for k, v in overrides.items():
        original[k] = sys.modules.get(k)
        sys.modules[k] = v

    yield {
        "fake_store": fake_store,
        "fake_chat_openai_cls": fake_chat_openai_cls,
        "fake_chat_openai_instance": fake_chat_openai_instance,
        "fake_sessions_module": fake_sessions_module,
        "fake_vs_module": fake_vs_module,
        "fake_rag_tools": fake_rag_tools,
        "fake_agent_module": fake_agent_module,
    }

    # restore
    for k, v in original.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


# ---------------------------------------------------------------------------
# Import the module under test AFTER stubs are in place
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def main_module(_stub_heavy_deps):
    """Import api.main once per session after all stubs are registered."""
    # Remove cached version so we get a fresh import with our stubs
    sys.modules.pop("api.main", None)
    sys.modules.pop("api", None)

    # Ensure the api package itself is importable as a namespace
    if "api" not in sys.modules:
        api_pkg = types.ModuleType("api")
        sys.modules["api"] = api_pkg

    import api.main as main  # noqa: PLC0415
    return main


@pytest.fixture(scope="session")
def test_client(main_module):
    """Return a synchronous TestClient wrapping the FastAPI app."""
    return TestClient(main_module.app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Convenience re-fixtures that narrow scope for mutation
# ---------------------------------------------------------------------------

@pytest.fixture()
def sessions_mod(_stub_heavy_deps):
    return _stub_heavy_deps["fake_sessions_module"]


@pytest.fixture()
def fake_store(_stub_heavy_deps):
    return _stub_heavy_deps["fake_store"]


@pytest.fixture()
def fake_chat_openai_cls(_stub_heavy_deps):
    return _stub_heavy_deps["fake_chat_openai_cls"]


# ===========================================================================
# Tests: _get_llm
# ===========================================================================

class TestGetLlm:
    """Unit tests for _get_llm helper."""

    def test_returns_shared_instance_when_no_overrides(self, main_module):
        """Default call returns the module-level _llm singleton."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_with_same_temperature(self, main_module):
        """Explicit default temperature still returns the singleton."""
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self, main_module, fake_chat_openai_cls):
        fake_chat_openai_cls.reset_mock()
        result = main_module._get_llm(model="some-other-model")
        # Should NOT be the singleton
        assert result is not main_module._llm
        # ChatOpenAI constructor should have been called with the custom model
        call_kwargs = fake_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("model") == "some-other-model"

    def test_returns_new_instance_when_temperature_differs(self, main_module, fake_chat_openai_cls):
        fake_chat_openai_cls.reset_mock()
        result = main_module._get_llm(temperature=0.99)
        assert result is not main_module._llm
        call_kwargs = fake_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("temperature") == 0.99

    def test_new_instance_uses_module_model_when_model_is_none(self, main_module, fake_chat_openai_cls):
        """When model=None but temperature differs, model defaults to _LLM_MODEL."""
        fake_chat_openai_cls.reset_mock()
        main_module._get_llm(temperature=0.1)
        call_kwargs = fake_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("model") == main_module._LLM_MODEL

    def test_new_instance_streaming_is_true(self, main_module, fake_chat_openai_cls):
        fake_chat_openai_cls.reset_mock()
        main_module._get_llm(temperature=0.0)
        call_kwargs = fake_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("streaming") is True

    def test_new_instance_uses_correct_base_url(self, main_module, fake_chat_openai_cls):
        fake_chat_openai_cls.reset_mock()
        main_module._get_llm(temperature=0.0)
        call_kwargs = fake_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("base_url") == main_module._BASE_URL


# ===========================================================================
# Tests: _ROLEPLAY_SYSTEM template
# ===========================================================================

class TestRoleplaySystemTemplate:
    """Validate the _ROLEPLAY_SYSTEM prompt template."""

    REQUIRED_PLACEHOLDERS = ["{name}", "{age}", "{occupation}", "{profile}",
                              "{stage_instruction}", "{today}"]

    def test_contains_all_required_placeholders(self, main_module):
        for ph in self.REQUIRED_PLACEHOLDERS:
            assert ph in main_module._ROLEPLAY_SYSTEM, (
                f"Missing placeholder {ph} in _ROLEPLAY_SYSTEM"
            )

    def test_is_non_empty_string(self, main_module):
        assert isinstance(main_module._ROLEPLAY_SYSTEM, str)
        assert len(main_module._ROLEPLAY_SYSTEM.strip()) > 0

    def test_instructs_to_never_break_character(self, main_module):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_mentions_today_date_usage(self, main_module):
        assert "Today's date" in main_module._ROLEPLAY_SYSTEM

    def test_can_be_formatted_with_valid_inputs(self, main_module):
        """Template should format without KeyError with valid keys."""
        rendered = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=35,
            occupation="teacher",
            profile="Single mother of two.",
            stage_instruction="This is a 1st meeting.",
            today="2024-01-15",
        )
        assert "Alice" in rendered
        assert "35" in rendered
        assert "teacher" in rendered

    def test_format_raises_on_missing_key(self, main_module):
        with pytest.raises(KeyError):
            main_module._ROLEPLAY_SYSTEM.format(name="Alice")  # missing keys


# ===========================================================================
# Tests: _PRIOR_CONTEXT_PROMPT template
# ===========================================================================

class TestPriorContextPromptTemplate:
    """Validate the _PRIOR_CONTEXT_PROMPT template."""

    REQUIRED_PLACEHOLDERS = ["{profile}", "{stage}"]

    def test_contains_required_placeholders(self, main_module):
        for ph in self.REQUIRED_PLACEHOLDERS:
            assert ph in main_module._PRIOR_CONTEXT_PROMPT, (
                f"Missing placeholder {ph}"
            )

    def test_is_non_empty_string(self, main_module):
        assert isinstance(main_module._PRIOR_CONTEXT_PROMPT, str)
        assert len(main_module._PRIOR_CONTEXT_PROMPT.strip()) > 0

    def test_max_word_count_mentioned(self, main_module):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_can_be_formatted(self, main_module):
        rendered = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="35-year-old teacher, single mother.",
            stage="2nd conversation",
        )
        assert "35-year-old teacher" in rendered
        assert "2nd conversation" in rendered

    def test_format_raises_on_missing_key(self, main_module):
        with pytest.raises(KeyError):
            main_module._PRIOR_CONTEXT_PROMPT.format(profile="x")  # stage missing


# ===========================================================================
# Tests: FastAPI app configuration
# ===========================================================================

class TestAppConfiguration:
    """Tests for the FastAPI application setup."""

    def test_app_title(self, main_module):
        assert main_module.app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, main_module):
        from starlette.middleware.cors import CORSMiddleware as StarletteCorsMW  # noqa: PLC0415
        middleware_types = [m.cls for m in main_module.app.user_middleware]
        assert StarletteCorsMW in middleware_types

    def test_show_tool_calls_is_bool(self, main_module):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_llm_temperature_constant(self, main_module):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_data_dir_attribute_exists(self, main_module):
        assert hasattr(main_module, "_DATA_DIR")

    def test_store_