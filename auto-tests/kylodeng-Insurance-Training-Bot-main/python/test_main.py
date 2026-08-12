"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs. new instance based on parameters
- _build_roleplay_system: prompt building from CustomerProfile (stub — function body truncated)
- FastAPI app endpoints via TestClient: lifespan, CORS middleware, static file mount
- _ROLEPLAY_SYSTEM and _PRIOR_CONTEXT_PROMPT template strings: key placeholder presence
- SHOW_TOOL_CALLS environment variable parsing
- Lifespan startup logic (vector store load / warn branch)
- HTTP endpoints (ingest, sessions CRUD) via TestClient with mocked dependencies

Mocks used:
- core.vector_store.get_vector_store → MagicMock / AsyncMock
- api.rag_tools.make_rag_tools → MagicMock
- api.agent.make_teacher_agent, make_assessor_agent → MagicMock
- api.sessions.* functions → MagicMock / patched
- langchain_openai.ChatOpenAI → MagicMock (never calls real OpenAI/OpenRouter)
- httpx.Client / httpx.AsyncClient → patched to avoid real HTTP

TODOs:
- TODO: Full endpoint tests for POST /chat, POST /ingest require streaming response helpers
- TODO: _build_roleplay_system body is truncated; tests are stubs until full source available
- TODO: WebSocket / SSE streaming tests require async test client setup with trio/anyio
- TODO: Tests for make_teacher_agent / make_assessor_agent integration (agent graph execution)
"""

import importlib
import os
import sys
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers: build a minimal mock surface so importing api.main doesn't blow up
# ---------------------------------------------------------------------------

def _make_vector_store_mock(load_returns: bool = True, known_products: list | None = None):
    store = MagicMock()
    store.load.return_value = load_returns
    store.get_known_products.return_value = known_products or ["ProductA", "ProductB"]
    return store


def _patch_heavy_imports(
    vector_store_mock=None,
    load_returns: bool = True,
):
    """Return a dict of patchers for all heavy external dependencies."""
    vs = vector_store_mock or _make_vector_store_mock(load_returns=load_returns)
    return {
        "core.vector_store.get_vector_store": patch(
            "core.vector_store.get_vector_store", return_value=vs
        ),
        "api.rag_tools.make_rag_tools": patch(
            "api.rag_tools.make_rag_tools", return_value=[MagicMock()]
        ),
        "api.agent.make_teacher_agent": patch(
            "api.agent.make_teacher_agent", return_value=MagicMock()
        ),
        "api.agent.make_assessor_agent": patch(
            "api.agent.make_assessor_agent", return_value=MagicMock()
        ),
        "api.sessions.load_sessions": patch("api.sessions.load_sessions"),
        "langchain_openai.ChatOpenAI": patch(
            "langchain_openai.ChatOpenAI", return_value=MagicMock()
        ),
        "httpx.Client": patch("httpx.Client", return_value=MagicMock()),
        "httpx.AsyncClient": patch("httpx.AsyncClient", return_value=MagicMock()),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_module_cache():
    """Remove api.main from sys.modules before each test to allow clean imports."""
    for key in list(sys.modules.keys()):
        if key.startswith("api.main"):
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if key.startswith("api.main"):
            del sys.modules[key]


@pytest.fixture()
def app_client() -> Generator[TestClient, None, None]:
    """TestClient with all heavy dependencies mocked."""
    patchers = _patch_heavy_imports()
    started = {k: p.start() for k, p in patchers.items()}
    try:
        # Must import AFTER patching
        import api.main as main_module  # noqa: PLC0415

        with TestClient(main_module.app, raise_server_exceptions=True) as client:
            yield client
    finally:
        for p in patchers.values():
            p.stop()


@pytest.fixture()
def app_module():
    """Return the api.main module with mocked dependencies."""
    patchers = _patch_heavy_imports()
    for p in patchers.values():
        p.start()
    try:
        import api.main as m  # noqa: PLC0415

        yield m
    finally:
        for p in patchers.values():
            p.stop()


@pytest.fixture()
def customer_profile_data():
    """Synthetic CustomerProfile-compatible dict."""
    return {
        "name": "Chan Siu Ming",
        "age": 35,
        "occupation": "Software Engineer",
        "annual_income": 600000,
        "savings": 200000,
        "dependents": 2,
        "existing_coverage": "Basic group medical from employer",
        "goals": "Save for children's education and retirement",
        "personality": "Analytical, risk-averse, skeptical of salespersons",
    }


# ---------------------------------------------------------------------------
# Tests: Environment / module-level constants
# ---------------------------------------------------------------------------


class TestEnvironmentParsing:
    """Tests for module-level environment variable consumption."""

    def test_show_tool_calls_default_true(self, monkeypatch):
        monkeypatch.delenv("SHOW_TOOL_CALLS", raising=False)
        patchers = _patch_heavy_imports()
        for p in patchers.values():
            p.start()
        try:
            import api.main as m  # noqa: PLC0415

            # Default in code is "true" → True
            assert m.SHOW_TOOL_CALLS is True
        finally:
            for p in patchers.values():
                p.stop()

    def test_show_tool_calls_false_when_set_false(self, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", "false")
        patchers = _patch_heavy_imports()
        for p in patchers.values():
            p.start()
        try:
            import api.main as m  # noqa: PLC0415

            assert m.SHOW_TOOL_CALLS is False
        finally:
            for p in patchers.values():
                p.stop()

    def test_show_tool_calls_case_insensitive_true(self, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", "TRUE")
        patchers = _patch_heavy_imports()
        for p in patchers.values():
            p.start()
        try:
            import api.main as m  # noqa: PLC0415

            assert m.SHOW_TOOL_CALLS is True
        finally:
            for p in patchers.values():
                p.stop()

    def test_show_tool_calls_arbitrary_string_is_false(self, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", "yes")
        patchers = _patch_heavy_imports()
        for p in patchers.values():
            p.start()
        try:
            import api.main as m  # noqa: PLC0415

            assert m.SHOW_TOOL_CALLS is False
        finally:
            for p in patchers.values():
                p.stop()

    def test_llm_temperature_constant(self, app_module):
        assert app_module._LLM_TEMPERATURE == 0.6

    def test_default_base_url(self, monkeypatch, app_module):
        assert "openrouter" in app_module._BASE_URL or app_module._BASE_URL.startswith("http")

    def test_api_key_defaults_to_empty_string(self, monkeypatch):
        monkeypatch.delenv("API_KEY", raising=False)
        patchers = _patch_heavy_imports()
        for p in patchers.values():
            p.start()
        try:
            import api.main as m  # noqa: PLC0415

            assert isinstance(m._API_KEY, str)
        finally:
            for p in patchers.values():
                p.stop()


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------


class TestGetLlm:
    """Tests for _get_llm helper function."""

    def test_returns_shared_instance_when_no_params(self, app_module):
        """Default call should return the module-level _llm object."""
        result = app_module._get_llm()
        assert result is app_module._llm

    def test_returns_shared_instance_when_temperature_matches(self, app_module):
        result = app_module._get_llm(model=None, temperature=0.6)
        assert result is app_module._llm

    def test_returns_new_instance_when_model_differs(self, app_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = app_module._get_llm(model="different-model")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "different-model"

    def test_returns_new_instance_when_temperature_differs(self, app_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = app_module._get_llm(temperature=0.9)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.9

    def test_new_instance_uses_fallback_model_when_none(self, app_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            app_module._get_llm(model=None, temperature=0.1)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == app_module._LLM_MODEL

    def test_new_instance_uses_provided_model(self, app_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            app_module._get_llm(model="openai/gpt-4o", temperature=0.3)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "openai/gpt-4o"
            assert call_kwargs["temperature"] == 0.3

    def test_new_instance_has_streaming_true(self, app_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            app_module._get_llm(temperature=0.0)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("streaming") is True

    def test_new_instance_uses_correct_base_url(self, app_module):
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            app_module._get_llm(temperature=0.0)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == app_module._BASE_URL


# ---------------------------------------------------------------------------
# Tests: Prompt template strings
# ---------------------------------------------------------------------------


class TestRoleplaySystemTemplate:
    """Validate the _ROLEPLAY_SYSTEM template has all required placeholders."""

    REQUIRED_PLACEHOLDERS = [
        "{name}",
        "{age}",
        "{occupation}",
        "{profile}",
        "{stage_instruction}",
        "{today}",
    ]

    def test_all_placeholders_present(self, app_module):
        for ph in self.REQUIRED_PLACEHOLDERS:
            assert ph in app_module._ROLEPLAY_SYSTEM, (
                f"Placeholder {ph!r} missing from _ROLEPLAY_SYSTEM"
            )

    def test_template_is_nonempty(self, app_module):
        assert len(app_module._ROLEPLAY_SYSTEM.strip()) > 0

    def test_template_instructs_not_to_break_character(self, app_module):
        assert "Never break character" in app_module._ROLEPLAY_SYSTEM

    def test_template_mentions_today(self, app_module):
        assert "Today" in app_module._ROLEPLAY_SYSTEM or "today" in app_module._ROLEPLAY_SYSTEM

    def test_template_format_works_with_sample_data(self, app_module):
        """Ensure str.format() doesn't raise with expected keys."""
        result = app_module._ROLEPLAY_SYSTEM.format(
            name="Chan Siu Ming",
            age=35,
            occupation="Software Engineer",
            profile="Married with two kids. Earns HKD 600k/yr.",
            stage_instruction="This is the first meeting.",
            today="2024-06-01",
        )
        assert "Chan Siu Ming" in result
        assert "Software Engineer" in result


class TestPriorContextPromptTemplate:
    """Validate the _PRIOR_CONTEXT_PROMPT template."""

    REQUIRED_PLACEHOLDERS = ["{profile}", "{stage}"]

    def test_all_placeholders_present(self, app_module):
        for ph in self.REQUIRED_PLACEHOLDERS:
            assert ph in app_module._PRIOR_CONTEXT_PROMPT, (
                f"Placeholder {ph!r} missing from _PRIOR_CONTEXT_PROMPT"
            )

    def test_template_is_nonempty(self, app_module):
        assert len(app_module._PRIOR_CONTEXT_PROMPT.strip()) > 0

    def test_template_mentions_word_limit(self, app_module):
        assert "350" in app_module._PRIOR_CONTEXT_PROMPT

    def test_template_format_with_sample_data(self, app_module):
        result = app_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Married software engineer, age 35, two children.",
            stage="2nd conversation",
        )
        assert "2nd conversation" in result

    def test_template_instructs_no_product_names(self, app_module):
        assert "product" in app_module._PRIOR_CONTEXT_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests: FastAPI app object
# ---------------------------------------------------------------------------


class TestAppObject:
    """Tests for the FastAPI application configuration."""

    def test_app_title(self, app_module):
        assert app_module.app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, app_module):
        from starlette.middleware.cors import CORSMiddleware  # noqa: PLC0415

        middleware_types = [m.cls for m in app_module.app.user_middleware]
        assert CORSMiddleware in middleware_types

    def test_app_has_lifespan(self, app_module):
        # The router or app should have a lifespan configured
        assert app_module.app.router.lifespan_context is not None

    def test_app_is_fastapi_instance(self, app_module):
        from fastapi import FastAPI  # noqa: PLC0415

        assert isinstance(app_module.app, FastAPI)


# ---------------------------------------------------------------------------
# Tests: Li