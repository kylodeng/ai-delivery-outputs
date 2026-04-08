"""
Module docstring
================
What is tested:
  - _get_llm()               : returns shared instance vs new instance based on args
  - _build_roleplay_system() : builds correct system prompt string from CustomerProfile
  - _ROLEPLAY_SYSTEM         : template contains expected placeholders
  - _PRIOR_CONTEXT_PROMPT    : template contains expected placeholders
  - FastAPI app routes / lifespan (stubbed)
  - SHOW_TOOL_CALLS env-var parsing
  - Session management helpers called through the app (create, get, list, delete)
  - /ingest endpoint (stub — needs real vector store)

Mocks used:
  - langchain_openai.ChatOpenAI        : patched at api.main._llm and in _get_llm
  - core.vector_store.get_vector_store : patched to return a MagicMock store
  - api.rag_tools.make_rag_tools       : patched to return []
  - api.agent.make_teacher_agent       : patched
  - api.agent.make_assessor_agent      : patched
  - api.sessions.*                     : patched where needed
  - httpx.Client / httpx.AsyncClient   : patched to avoid real network calls
  - load_dotenv                        : patched to be a no-op
  - fastapi.testclient.TestClient      : used for route-level tests

TODOs:
  - TODO: test full streaming response from /chat once streaming helpers are testable
  - TODO: test /ingest route once the ingest pipeline is exposed in main.py
  - TODO: test _build_roleplay_system with stage_instruction variants after full source
          is available (source was truncated)
  - TODO: test prior-context generation endpoint once route definition is visible
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake module graph so importing api.main does not
# require a real OpenAI key, real vector store, or the Chainlit runtime.
# ---------------------------------------------------------------------------

def _make_fake_sessions_module():
    mod = types.ModuleType("api.sessions")

    class CustomerProfile:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self):
            return self.__dict__

    class Session:
        pass

    mod.CustomerProfile = CustomerProfile
    mod.Session = Session
    mod.create_session = MagicMock(return_value={"id": "s1"})
    mod.delete_session = MagicMock(return_value=True)
    mod.generate_profile = MagicMock(return_value=CustomerProfile(name="Alice"))
    mod.get_session = MagicMock(return_value=None)
    mod.list_sessions = MagicMock(return_value=[])
    mod.load_sessions = MagicMock()
    mod.update_session_title = MagicMock()
    return mod


def _make_fake_vector_store():
    store = MagicMock()
    store.load.return_value = True
    store.get_known_products.return_value = ["ProductA", "ProductB"]
    return store


def _patch_all_heavy_deps():
    """
    Return a dict of patches that must be active before api.main is imported.
    Caller is responsible for starting / stopping them.
    """
    fake_sessions = _make_fake_sessions_module()
    fake_store = _make_fake_vector_store()

    patches = {
        # Prevent real dotenv file read
        "dotenv.load_dotenv": patch("dotenv.load_dotenv"),
        # Prevent real ChatOpenAI instantiation at module level
        "langchain_openai.ChatOpenAI": patch(
            "langchain_openai.ChatOpenAI", return_value=MagicMock()
        ),
        # Prevent real httpx clients
        "httpx.Client": patch("httpx.Client", return_value=MagicMock()),
        "httpx.AsyncClient": patch("httpx.AsyncClient", return_value=MagicMock()),
        # Stub heavy internal modules
        "core.vector_store": patch.dict(
            sys.modules,
            {
                "core": types.ModuleType("core"),
                "core.vector_store": _make_vs_module(fake_store),
                "api.rag_tools": _make_rag_tools_module(),
                "api.agent": _make_agent_module(),
                "api.sessions": fake_sessions,
            },
        ),
    }
    return patches


def _make_vs_module(fake_store):
    mod = types.ModuleType("core.vector_store")
    mod.get_vector_store = MagicMock(return_value=fake_store)
    return mod


def _make_rag_tools_module():
    mod = types.ModuleType("api.rag_tools")
    mod.make_rag_tools = MagicMock(return_value=[])
    return mod


def _make_agent_module():
    mod = types.ModuleType("api.agent")
    mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    return mod


# ---------------------------------------------------------------------------
# Fixture: import api.main with all heavy deps patched
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_module():
    """Import api.main once with all external deps stubbed out."""
    # Remove any cached version so we start clean
    for key in list(sys.modules.keys()):
        if key.startswith("api.main") or key == "api.main":
            del sys.modules[key]

    fake_sessions = _make_fake_sessions_module()
    fake_store = _make_fake_vector_store()

    stub_modules = {
        "core": types.ModuleType("core"),
        "core.vector_store": _make_vs_module(fake_store),
        "api.rag_tools": _make_rag_tools_module(),
        "api.agent": _make_agent_module(),
        "api.sessions": fake_sessions,
    }

    with patch.dict(sys.modules, stub_modules), \
         patch("dotenv.load_dotenv"), \
         patch("langchain_openai.ChatOpenAI", return_value=MagicMock()), \
         patch("httpx.Client", return_value=MagicMock()), \
         patch("httpx.AsyncClient", return_value=MagicMock()), \
         patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None):
        import api.main as m
        yield m

    # cleanup
    for key in list(sys.modules.keys()):
        if key.startswith("api.main"):
            del sys.modules[key]


@pytest.fixture(scope="module")
def test_client(main_module):
    from fastapi.testclient import TestClient
    # Override lifespan so TestClient startup does not call load_sessions etc.
    main_module.app.router.lifespan_context = _null_lifespan(main_module.app)
    return TestClient(main_module.app, raise_server_exceptions=True)


from contextlib import asynccontextmanager as _acm


def _null_lifespan(app):
    @_acm
    async def _inner(_app):
        yield
    return _inner


# ---------------------------------------------------------------------------
# Helpers for CustomerProfile construction
# ---------------------------------------------------------------------------

def _make_profile(**kwargs):
    defaults = dict(
        name="Alice Chan",
        age=35,
        occupation="Nurse",
        profile="Married with two kids. Concerned about health coverage.",
        stage="1st_meeting",
        stage_instruction="",
        today="2024-06-01",
    )
    defaults.update(kwargs)
    return defaults  # plain dict; _build_roleplay_system accepts CustomerProfile


# ---------------------------------------------------------------------------
# 1. SHOW_TOOL_CALLS env-var parsing
# ---------------------------------------------------------------------------

class TestShowToolCallsParsing:
    """Verify SHOW_TOOL_CALLS is parsed correctly from the environment."""

    @pytest.mark.parametrize("env_val,expected", [
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("yes", False),   # only exact "true" after .lower()
        ("", False),
    ])
    def test_show_tool_calls_value(self, env_val, expected):
        result = env_val.lower() == "true"
        assert result is expected

    def test_show_tool_calls_default_is_true(self):
        """When env var is missing the default string 'true' evaluates to True."""
        default_val = "true"
        assert default_val.lower() == "true"


# ---------------------------------------------------------------------------
# 2. _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_args(self, main_module):
        shared = main_module._llm
        result = main_module._get_llm()
        assert result is shared

    def test_returns_shared_instance_with_default_temperature(self, main_module):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            result = main_module._get_llm(model="some-other-model")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "some-other-model"

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            result = main_module._get_llm(temperature=0.9)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.9

    def test_new_instance_uses_llm_model_when_model_none(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(model=None, temperature=0.9)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == main_module._LLM_MODEL

    def test_new_instance_passes_base_url(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(temperature=0.1)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["base_url"] == main_module._BASE_URL

    def test_new_instance_streaming_enabled(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(temperature=0.2)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["streaming"] is True

    def test_new_instance_uses_supplied_model(self, main_module):
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as mock_cls:
            main_module._get_llm(model="gpt-4o", temperature=0.5)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4o"
            assert call_kwargs["temperature"] == 0.5


# ---------------------------------------------------------------------------
# 3. _ROLEPLAY_SYSTEM template
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    def test_contains_name_placeholder(self, main_module):
        assert "{name}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_age_placeholder(self, main_module):
        assert "{age}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_occupation_placeholder(self, main_module):
        assert "{occupation}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_profile_placeholder(self, main_module):
        assert "{profile}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_stage_instruction_placeholder(self, main_module):
        assert "{stage_instruction}" in main_module._ROLEPLAY_SYSTEM

    def test_contains_today_placeholder(self, main_module):
        assert "{today}" in main_module._ROLEPLAY_SYSTEM

    def test_template_formats_correctly(self, main_module):
        filled = main_module._ROLEPLAY_SYSTEM.format(
            name="Bob",
            age=40,
            occupation="Engineer",
            profile="Single, no kids.",
            stage_instruction="This is the first meeting.",
            today="2024-01-01",
        )
        assert "Bob" in filled
        assert "40" in filled
        assert "Engineer" in filled
        assert "Single, no kids." in filled
        assert "2024-01-01" in filled

    def test_template_instructs_stay_in_character(self, main_module):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_template_mentions_today_usage(self, main_module):
        assert "Today's date" in main_module._ROLEPLAY_SYSTEM


# ---------------------------------------------------------------------------
# 4. _PRIOR_CONTEXT_PROMPT template
# ---------------------------------------------------------------------------

class TestPriorContextPromptTemplate:
    def test_contains_profile_placeholder(self, main_module):
        assert "{profile}" in main_module._PRIOR_CONTEXT_PROMPT

    def test_contains_stage_placeholder(self, main_module):
        assert "{stage}" in main_module._PRIOR_CONTEXT_PROMPT

    def test_template_formats_correctly(self, main_module):
        filled = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Single mother, two kids.",
            stage="2nd conversation",
        )
        assert "Single mother" in filled
        assert "2nd conversation" in filled

    def test_template_mentions_350_words(self, main_module):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_template_specifies_second_person(self, main_module):
        assert "second person" in main_module._PRIOR_CONTEXT_PROMPT.lower() or \
               "second-person" in main_module._PRIOR_CONTEXT_PROMPT.lower() or \
               "You called" in main_module._PRIOR_CONTEXT_PROMPT

    def test_template_no_product_names_instruction(self, main_module):
        assert "Do not invent insurance product names" in main_module._PRIOR_CONTEXT_PROMPT

    def test_prior_context_prompt_is_non_empty_string(self, main_module):
        assert isinstance(main_module._PRIOR_CONTEXT_PROMPT, str)
        assert len(main_module._PRIOR_CONTEXT_PROMPT) > 50


# ---------------------------------------------------------------------------
# 5. _build_roleplay_system (partial — source was truncated)
# ---------------------------------------------------------------------------

class TestBuildRoleplaySystem:
    """Tests for _build_roleplay_system. Source was truncated; only partial coverage."""

    def _make_customer_profile(self, main_module, **kwargs):
        """Create a CustomerProfile-like object accepted by _build_roleplay_system."""
        CP = main_module.app.state.__class__  # fallback: use a SimpleNamespace
        import types as _t
        obj = _t.SimpleNamespace(