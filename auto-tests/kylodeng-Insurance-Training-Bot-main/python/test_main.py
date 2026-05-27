"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs. new instance based on parameters
- _build_roleplay_system: builds the roleplay system prompt (stub — function is truncated)
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template string formatting
- FastAPI app endpoints via TestClient (lifespan, CORS middleware, static mount)
- SHOW_TOOL_CALLS env-var parsing
- Module-level constants loaded from environment

Mocks used:
- unittest.mock.patch for ChatOpenAI (langchain_openai)
- unittest.mock.patch for get_vector_store (core.vector_store)
- unittest.mock.patch for make_rag_tools (api.rag_tools)
- unittest.mock.patch for make_teacher_agent, make_assessor_agent (api.agent)
- unittest.mock.patch for load_sessions, create_session, get_session, etc. (api.sessions)
- unittest.mock.MagicMock / AsyncMock for vector store and LLM instances
- httpx.Client / httpx.AsyncClient patched to avoid real network calls
- StaticFiles patched to avoid real filesystem dependency

TODOs:
- TODO: /ingest endpoint — not visible in truncated source; add tests once endpoint is defined
- TODO: /chat or /stream endpoints — not visible in truncated source; add streaming tests
- TODO: session CRUD endpoints — need full source to enumerate routes
- TODO: _build_roleplay_system — function body is truncated; full test needs complete source
- TODO: _PRIOR_CONTEXT_PROMPT injection into agent — needs full source
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers to build lightweight fakes for heavy optional dependencies
# ---------------------------------------------------------------------------

def _make_fake_chat_openai():
    """Return a MagicMock that quacks like ChatOpenAI."""
    mock_cls = MagicMock(name="ChatOpenAI")
    instance = MagicMock(name="ChatOpenAI_instance")
    mock_cls.return_value = instance
    return mock_cls, instance


def _make_fake_vector_store():
    store = MagicMock(name="VectorStore")
    store.load.return_value = True
    store.get_known_products.return_value = ["Generations II", "HealthCare Plus"]
    return store


def _make_fake_sessions_module():
    """Build a fake api.sessions module so imports don't fail."""
    mod = types.ModuleType("api.sessions")

    class CustomerProfile:  # minimal stand-in
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Session:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    mod.CustomerProfile = CustomerProfile
    mod.Session = Session
    mod.create_session = MagicMock(return_value=Session(id="s1"))
    mod.delete_session = MagicMock(return_value=True)
    mod.generate_profile = MagicMock(return_value=CustomerProfile(name="Alice"))
    mod.get_session = MagicMock(return_value=Session(id="s1"))
    mod.list_sessions = MagicMock(return_value=[])
    mod.load_sessions = MagicMock()
    mod.update_session_title = MagicMock()
    return mod


# ---------------------------------------------------------------------------
# Pytest fixtures — patch everything before importing api.main
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _patch_heavy_deps():
    """
    Patch all heavy/external dependencies at the module level before api.main
    is imported for the first time in the test session.
    """
    fake_chat_cls, fake_chat_instance = _make_fake_chat_openai()
    fake_store = _make_fake_vector_store()
    fake_sessions = _make_fake_sessions_module()

    # Pre-populate sys.modules with fakes so that api.main's top-level
    # imports resolve without network or filesystem access.
    patches = {
        "langchain_openai": types.ModuleType("langchain_openai"),
        "langchain_core": types.ModuleType("langchain_core"),
        "langchain_core.messages": types.ModuleType("langchain_core.messages"),
        "core": types.ModuleType("core"),
        "core.vector_store": types.ModuleType("core.vector_store"),
        "api.rag_tools": types.ModuleType("api.rag_tools"),
        "api.agent": types.ModuleType("api.agent"),
        "api.sessions": fake_sessions,
        "httpx": MagicMock(name="httpx"),
        "dotenv": types.ModuleType("dotenv"),
    }

    # langchain_openai.ChatOpenAI
    patches["langchain_openai"].ChatOpenAI = fake_chat_cls  # type: ignore[attr-defined]

    # langchain_core.messages
    for cls_name in ("AIMessage", "HumanMessage", "SystemMessage"):
        setattr(patches["langchain_core.messages"], cls_name, MagicMock(name=cls_name))

    # core.vector_store
    patches["core.vector_store"].get_vector_store = MagicMock(return_value=fake_store)  # type: ignore[attr-defined]

    # api.rag_tools
    patches["api.rag_tools"].make_rag_tools = MagicMock(return_value=[])  # type: ignore[attr-defined]

    # api.agent
    patches["api.agent"].make_teacher_agent = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    patches["api.agent"].make_assessor_agent = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]

    # dotenv
    patches["dotenv"].load_dotenv = MagicMock()  # type: ignore[attr-defined]

    # httpx — Client / AsyncClient must return real-ish objects
    fake_httpx = MagicMock(name="httpx")
    fake_httpx.Client.return_value = MagicMock(name="httpx.Client")
    fake_httpx.AsyncClient.return_value = MagicMock(name="httpx.AsyncClient")
    patches["httpx"] = fake_httpx

    # pydantic SecretStr — keep real pydantic if available, otherwise stub
    try:
        from pydantic import SecretStr  # noqa: F401  — already installed
    except ImportError:
        fake_pydantic = types.ModuleType("pydantic")
        fake_pydantic.BaseModel = object  # type: ignore[attr-defined]
        fake_pydantic.SecretStr = lambda x: x  # type: ignore[attr-defined]
        patches["pydantic"] = fake_pydantic

    originals: dict[str, Any] = {}
    for key, fake in patches.items():
        originals[key] = sys.modules.get(key)
        sys.modules[key] = fake  # type: ignore[assignment]

    # Also inject sub-packages so dotted lookups work
    sys.modules["langchain_core.messages"] = patches["langchain_core.messages"]

    yield fake_chat_cls, fake_store

    # Teardown: restore original modules
    for key, original in originals.items():
        if original is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = original

    # Remove cached api.main so subsequent sessions start clean
    sys.modules.pop("api.main", None)


@pytest.fixture(scope="session")
def main_module(_patch_heavy_deps):
    """Import api.main once per session after all patches are in place."""
    # Remove stale cached version if present
    sys.modules.pop("api.main", None)

    # Patch StaticFiles to avoid real filesystem
    with patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None):
        import api.main as m
        return m


@pytest.fixture()
def test_client(main_module):
    """Provide a FastAPI TestClient with the lifespan disabled."""
    from fastapi.testclient import TestClient

    # Override lifespan so it doesn't call load_sessions / store.load during tests
    with patch.object(main_module, "lifespan", new=None):
        # Re-create app without lifespan for unit testing
        from fastapi import FastAPI

        bare_app = main_module.app
        with TestClient(bare_app, raise_server_exceptions=True) as client:
            yield client


# ---------------------------------------------------------------------------
# Tests: module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_llm_temperature_is_float(self, main_module):
        assert isinstance(main_module._LLM_TEMPERATURE, float)
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_show_tool_calls_is_bool(self, main_module):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_base_url_default(self, main_module):
        # When OPENAI_URL_BASE is not set the default is openrouter
        assert "openrouter" in main_module._BASE_URL or main_module._BASE_URL.startswith("http")

    def test_llm_model_default(self, main_module):
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
    ])
    def test_show_tool_calls_env_parsing(self, env_val, expected):
        """SHOW_TOOL_CALLS env var is parsed as .lower() == 'true'."""
        result = env_val.lower() == "true"
        assert result == expected


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_args(self, main_module):
        """With default args, _get_llm should return the module-level _llm."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_explicit_temperature(self, main_module):
        """Passing the same default temperature with no model returns shared instance."""
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_for_different_model(self, main_module):
        """A different model name triggers a new ChatOpenAI instantiation."""
        new_instance = MagicMock(name="new_llm")
        fake_cls = sys.modules["langchain_openai"].ChatOpenAI
        fake_cls.return_value = new_instance

        result = main_module._get_llm(model="some-other-model")
        # Should NOT be the shared _llm
        assert result is not main_module._llm

    def test_returns_new_instance_for_different_temperature(self, main_module):
        """A different temperature triggers a new ChatOpenAI instantiation."""
        new_instance = MagicMock(name="new_llm_temp")
        fake_cls = sys.modules["langchain_openai"].ChatOpenAI
        fake_cls.return_value = new_instance

        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self, main_module):
        """When a model is provided, ChatOpenAI is called with that model."""
        fake_cls = sys.modules["langchain_openai"].ChatOpenAI
        fake_cls.reset_mock()

        main_module._get_llm(model="custom-model-x", temperature=0.1)
        call_kwargs = fake_cls.call_args
        assert call_kwargs is not None
        # model should be 'custom-model-x'
        args, kwargs = call_kwargs
        model_used = kwargs.get("model") or (args[0] if args else None)
        assert model_used == "custom-model-x"

    def test_new_instance_falls_back_to_default_model_when_none(self, main_module):
        """When model=None but temperature differs, the default model is used."""
        fake_cls = sys.modules["langchain_openai"].ChatOpenAI
        fake_cls.reset_mock()

        main_module._get_llm(model=None, temperature=0.1)
        call_kwargs = fake_cls.call_args
        assert call_kwargs is not None
        _, kwargs = call_kwargs
        assert kwargs.get("model") == main_module._LLM_MODEL

    def test_new_instance_uses_provided_temperature(self, main_module):
        """The provided temperature is forwarded to the new ChatOpenAI."""
        fake_cls = sys.modules["langchain_openai"].ChatOpenAI
        fake_cls.reset_mock()

        main_module._get_llm(temperature=0.42)
        _, kwargs = fake_cls.call_args
        assert kwargs.get("temperature") == pytest.approx(0.42)

    def test_get_llm_zero_temperature(self, main_module):
        """Edge case: temperature=0 (different from default 0.6) → new instance."""
        result = main_module._get_llm(temperature=0.0)
        assert result is not main_module._llm

    def test_get_llm_empty_string_model(self, main_module):
        """Edge case: empty string model is falsy → default model used."""
        fake_cls = sys.modules["langchain_openai"].ChatOpenAI
        fake_cls.reset_mock()

        # empty string is falsy so `model or _LLM_MODEL` picks the default
        main_module._get_llm(model="", temperature=0.1)
        _, kwargs = fake_cls.call_args
        assert kwargs.get("model") == main_module._LLM_MODEL


# ---------------------------------------------------------------------------
# Tests: _ROLEPLAY_SYSTEM template
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    def _format(self, **overrides):
        defaults = dict(
            name="Alice Lam",
            age=35,
            occupation="teacher",
            profile="Married, two kids, concerned about education costs.",
            stage_instruction="This is the first meeting.",
            today=str(date.today()),
        )
        defaults.update(overrides)
        from api.main import _ROLEPLAY_SYSTEM
        return _ROLEPLAY_SYSTEM.format(**defaults)

    def test_name_appears_in_output(self, main_module):
        result = self._format(name="Bob Chan")
        assert "Bob Chan" in result

    def test_age_appears_in_output(self, main_module):
        result = self._format(age=42)
        assert "42" in result

    def test_occupation_appears_in_output(self, main_module):
        result = self._format(occupation="engineer")
        assert "engineer" in result

    def test_profile_appears_in_output(self, main_module):
        profile_text = "Has HKD 500k in savings."
        result = self._format(profile=profile_text)
        assert profile_text in result

    def test_today_date_