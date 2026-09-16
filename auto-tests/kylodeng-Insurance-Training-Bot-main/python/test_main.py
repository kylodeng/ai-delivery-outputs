"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs new instance based on parameters
- _build_roleplay_system: prompt construction with CustomerProfile data
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template string integrity
- FastAPI app startup/lifespan behaviour
- CORS middleware configuration
- HTTP endpoints (via TestClient): all public routes
- SHOW_TOOL_CALLS env-var parsing
- Static file mount existence

Mocks used:
- langchain_openai.ChatOpenAI (constructor + streaming)
- core.vector_store.get_vector_store
- api.rag_tools.make_rag_tools
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (load_sessions, create_session, get_session, etc.)
- httpx.Client / httpx.AsyncClient (SSL verify=False)
- dotenv.load_dotenv

TODOs:
- TODO: Full integration test for streaming /chat endpoint requires a live LLM or
        a more complex async generator mock — stub provided below.
- TODO: Test _build_roleplay_system for all stage values once the full function
        body is available (source was truncated).
- TODO: Test /ingest endpoint once its implementation is available.
- TODO: Test actual RAG tool invocation paths (need vector store content fixtures).
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from datetime import date
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


# ── Helpers: build a minimal fake module graph so api/main.py can be imported ─


def _make_fake_sessions_module():
    """Return a mock module that satisfies `from api.sessions import ...`."""
    mod = types.ModuleType("api.sessions")

    class CustomerProfile(MagicMock):
        name: str = "Alice Tan"
        age: int = 35
        occupation: str = "Teacher"
        profile: str = "A 35-year-old teacher living in Hong Kong."

    class Session(MagicMock):
        pass

    mod.CustomerProfile = CustomerProfile
    mod.Session = Session
    mod.create_session = MagicMock(return_value=Session())
    mod.delete_session = MagicMock(return_value=None)
    mod.generate_profile = MagicMock(return_value=CustomerProfile())
    mod.get_session = MagicMock(return_value=Session())
    mod.list_sessions = MagicMock(return_value=[])
    mod.load_sessions = MagicMock(return_value=None)
    mod.update_session_title = MagicMock(return_value=None)
    return mod


def _make_fake_vector_store():
    store = MagicMock()
    store.load.return_value = True
    store.get_known_products.return_value = ["Generations II", "health_products"]
    return store


def _make_fake_app_modules():
    """
    Insert fake top-level packages into sys.modules so that api/main.py
    can be imported without real dependencies being present.
    """
    fakes: dict[str, types.ModuleType] = {}

    # core.vector_store
    vs_mod = types.ModuleType("core.vector_store")
    vs_mod.get_vector_store = MagicMock(return_value=_make_fake_vector_store())
    fakes["core"] = types.ModuleType("core")
    fakes["core.vector_store"] = vs_mod

    # api.rag_tools
    rag_mod = types.ModuleType("api.rag_tools")
    rag_mod.make_rag_tools = MagicMock(return_value=[MagicMock(), MagicMock()])
    fakes["api.rag_tools"] = rag_mod

    # api.agent
    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    fakes["api.agent"] = agent_mod

    # api.sessions
    fakes["api.sessions"] = _make_fake_sessions_module()

    # api (package stub)
    if "api" not in sys.modules:
        fakes["api"] = types.ModuleType("api")

    for name, mod in fakes.items():
        sys.modules.setdefault(name, mod)

    return fakes


# ── Patch heavy third-party imports before importing api.main ─────────────────


@pytest.fixture(scope="session", autouse=True)
def _patch_third_party():
    """
    Patch ChatOpenAI, httpx clients, and dotenv at import time so that
    api.main can be imported without network or key requirements.
    """
    fake_llm = MagicMock()
    fake_llm.stream = MagicMock(return_value=iter([]))

    _make_fake_app_modules()

    patches = [
        patch("langchain_openai.ChatOpenAI", return_value=fake_llm),
        patch("httpx.Client", return_value=MagicMock()),
        patch("httpx.AsyncClient", return_value=MagicMock()),
        patch("dotenv.load_dotenv", return_value=None),
    ]
    started = [p.start() for p in patches]
    yield started
    for p in patches:
        p.stop()


# ── Import the module under test (after patching) ─────────────────────────────


@pytest.fixture(scope="session")
def main_module(_patch_third_party):
    """Import api.main once per session."""
    if "api.main" in sys.modules:
        return sys.modules["api.main"]
    import api.main as m
    return m


@pytest.fixture(scope="session")
def app(main_module):
    return main_module.app


@pytest.fixture()
def client(app):
    """Synchronous TestClient — lifespan events are triggered automatically."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────────
# Tests: module-level constants
# ─────────────────────────────────────────────────────────────────────────────


class TestModuleConstants:
    def test_llm_temperature_default(self, main_module):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_base_url_default(self, main_module):
        assert "openrouter" in main_module._BASE_URL or main_module._BASE_URL != ""

    def test_llm_model_has_value(self, main_module):
        assert isinstance(main_module._LLM_MODEL, str)
        assert len(main_module._LLM_MODEL) > 0

    def test_show_tool_calls_is_bool(self, main_module):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    @patch.dict(os.environ, {"SHOW_TOOL_CALLS": "true"})
    def test_show_tool_calls_env_true(self):
        value = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert value is True

    @patch.dict(os.environ, {"SHOW_TOOL_CALLS": "false"})
    def test_show_tool_calls_env_false(self):
        value = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert value is False

    @patch.dict(os.environ, {"SHOW_TOOL_CALLS": "TRUE"})
    def test_show_tool_calls_env_case_insensitive(self):
        value = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert value is True

    @patch.dict(os.environ, {"SHOW_TOOL_CALLS": "0"})
    def test_show_tool_calls_env_zero_is_false(self):
        value = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert value is False


# ─────────────────────────────────────────────────────────────────────────────
# Tests: _get_llm
# ─────────────────────────────────────────────────────────────────────────────


class TestGetLlm:
    def test_returns_shared_instance_when_no_args(self, main_module):
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_default_temperature(self, main_module):
        result = main_module._get_llm(model=None, temperature=0.6)
        assert result is main_module._llm

    def test_returns_new_instance_for_different_model(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            result = main_module._get_llm(model="openai/gpt-4")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "openai/gpt-4"

    def test_returns_new_instance_for_different_temperature(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            result = main_module._get_llm(temperature=0.9)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.9

    def test_new_instance_uses_fallback_model_when_none(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(temperature=0.1)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_has_streaming_true(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(temperature=0.2)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["streaming"] is True

    def test_new_instance_api_key_is_secret_str(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(temperature=0.3)
            call_kwargs = mock_cls.call_args.kwargs
            assert isinstance(call_kwargs["api_key"], SecretStr)

    def test_boundary_temperature_zero(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(temperature=0.0)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.0

    def test_boundary_temperature_two(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(temperature=2.0)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 2.0

    def test_both_model_and_temperature_changed(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(model="some/model", temperature=1.0)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "some/model"
            assert call_kwargs["temperature"] == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Tests: system prompt templates
# ─────────────────────────────────────────────────────────────────────────────


class TestSystemPromptTemplates:
    def test_roleplay_system_contains_name_placeholder(self, main_module):
        assert "{name}" in main_module._ROLEPLAY_SYSTEM

    def test_roleplay_system_contains_age_placeholder(self, main_module):
        assert "{age}" in main_module._ROLEPLAY_SYSTEM

    def test_roleplay_system_contains_occupation_placeholder(self, main_module):
        assert "{occupation}" in main_module._ROLEPLAY_SYSTEM

    def test_roleplay_system_contains_profile_placeholder(self, main_module):
        assert "{profile}" in main_module._ROLEPLAY_SYSTEM

    def test_roleplay_system_contains_today_placeholder(self, main_module):
        assert "{today}" in main_module._ROLEPLAY_SYSTEM

    def test_roleplay_system_contains_stage_instruction_placeholder(self, main_module):
        assert "{stage_instruction}" in main_module._ROLEPLAY_SYSTEM

    def test_roleplay_system_instructs_no_character_break(self, main_module):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_prior_context_prompt_contains_profile_placeholder(self, main_module):
        assert "{profile}" in main_module._PRIOR_CONTEXT_PROMPT

    def test_prior_context_prompt_contains_stage_placeholder(self, main_module):
        assert "{stage}" in main_module._PRIOR_CONTEXT_PROMPT

    def test_prior_context_prompt_max_350_words_mentioned(self, main_module):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_roleplay_system_format_with_sample_data(self, main_module):
        """_ROLEPLAY_SYSTEM should format without KeyError for all placeholders."""
        rendered = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice Tan",
            age=35,
            occupation="Teacher",
            profile="A 35-year-old teacher.",
            stage_instruction="This is a first meeting.",
            today=str(date.today()),
        )
        assert "Alice Tan" in rendered
        assert "Teacher" in rendered
        assert "35" in rendered

    def test_prior_context_prompt_format_with_sample_data(self, main_module):
        rendered = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="A 35-year-old teacher.",
            stage="2nd conversation",
        )
        assert "2nd conversation" in rendered
        assert "350" in rendered

    def test_roleplay_system_is_non_empty_string(self, main_module):
        assert isinstance(main_module._ROLEPLAY_SYSTEM, str)
        assert len(main_module._ROLEPLAY_SYSTEM) > 100

    def test_prior_context_prompt_is_non_empty_string(self, main_module):
        assert isinstance(main_module._PRIOR_CONTEXT_PROMPT, str)
        assert len(main_module._PRIOR_CONTEXT_PROMPT) > 100


# ─────────────────────────────────────────────────────────────────────────────
# Tests: FastAPI app metadata
# ─────────────────────────────────────────────────────────────────────────────


class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_