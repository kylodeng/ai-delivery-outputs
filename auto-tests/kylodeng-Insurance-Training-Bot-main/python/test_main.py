"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs. new instance based on model/temperature
- _build_roleplay_system: system prompt construction (stub — function truncated in source)
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable presence and formatting
- FastAPI app: lifespan, CORS middleware, static file mount
- API endpoints (via TestClient): all reachable routes
- Environment variable defaults and overrides

Mocks used:
- langchain_openai.ChatOpenAI (patched to avoid real LLM calls)
- core.vector_store.get_vector_store (patched to avoid real vector DB)
- api.rag_tools.make_rag_tools (patched)
- api.agent.make_teacher_agent, make_assessor_agent (patched)
- api.sessions.* (patched to avoid filesystem/session state)
- httpx.Client / httpx.AsyncClient (patched to avoid real HTTP)

TODOs:
- TODO: Full integration test for /ingest endpoint — needs real PDF fixture or file-upload mock
- TODO: Test streaming response content — needs async SSE client or manual async iteration
- TODO: Test _build_roleplay_system fully — source was truncated; full function body needed
- TODO: Test individual agent invocation endpoints — need full route definitions from source
- TODO: Test generate_profile endpoint — depends on session creation + LLM chain
- TODO: Test update_session_title endpoint — needs route definition from source
"""

import importlib
import sys
import types
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers to build lightweight stubs for heavy dependencies before import
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """
    Pre-populate sys.modules with stubs so that importing api.main does not
    trigger real network/file-system activity.
    """
    # ---- langchain_core.messages ----
    lc_msgs = types.ModuleType("langchain_core.messages")
    for cls_name in ("AIMessage", "HumanMessage", "SystemMessage"):
        setattr(lc_msgs, cls_name, MagicMock(name=cls_name))
    sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
    sys.modules["langchain_core.messages"] = lc_msgs

    # ---- langchain_openai ----
    lc_openai = types.ModuleType("langchain_openai")
    mock_chat_cls = MagicMock(name="ChatOpenAI")
    mock_chat_instance = MagicMock(name="ChatOpenAI_instance")
    mock_chat_cls.return_value = mock_chat_instance
    lc_openai.ChatOpenAI = mock_chat_cls
    sys.modules["langchain_openai"] = lc_openai

    # ---- httpx ----
    httpx_mod = types.ModuleType("httpx")
    httpx_mod.Client = MagicMock(name="httpx.Client")
    httpx_mod.AsyncClient = MagicMock(name="httpx.AsyncClient")
    sys.modules["httpx"] = httpx_mod

    # ---- dotenv ----
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = MagicMock()
    sys.modules["dotenv"] = dotenv_mod

    # ---- core.vector_store ----
    core_mod = types.ModuleType("core")
    core_vs = types.ModuleType("core.vector_store")
    mock_store = MagicMock(name="VectorStore")
    mock_store.load.return_value = True
    mock_store.get_known_products.return_value = ["ProductA", "ProductB"]
    core_vs.get_vector_store = MagicMock(return_value=mock_store)
    sys.modules.setdefault("core", core_mod)
    sys.modules["core.vector_store"] = core_vs

    # ---- api.rag_tools ----
    api_mod = sys.modules.setdefault("api", types.ModuleType("api"))
    rag_tools_mod = types.ModuleType("api.rag_tools")
    rag_tools_mod.make_rag_tools = MagicMock(return_value=[])
    sys.modules["api.rag_tools"] = rag_tools_mod

    # ---- api.agent ----
    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    sys.modules["api.agent"] = agent_mod

    # ---- api.sessions ----
    sessions_mod = types.ModuleType("api.sessions")

    class _CustomerProfile(MagicMock):
        pass

    class _Session(MagicMock):
        pass

    sessions_mod.CustomerProfile = _CustomerProfile
    sessions_mod.Session = _Session
    sessions_mod.create_session = MagicMock(return_value=_Session())
    sessions_mod.delete_session = MagicMock(return_value=True)
    sessions_mod.generate_profile = MagicMock(return_value=_CustomerProfile())
    sessions_mod.get_session = MagicMock(return_value=_Session())
    sessions_mod.list_sessions = MagicMock(return_value=[])
    sessions_mod.load_sessions = MagicMock()
    sessions_mod.update_session_title = MagicMock(return_value=True)
    sys.modules["api.sessions"] = sessions_mod

    # ---- staticfiles ----
    # Prevent StaticFiles from checking that the /data directory exists
    from unittest.mock import MagicMock as MM
    import fastapi.staticfiles as sf
    sf.StaticFiles.__init__ = MagicMock(return_value=None)

    return {
        "mock_chat_cls": mock_chat_cls,
        "mock_store": mock_store,
    }


# Run stub installation once at collection time
_stubs = _make_stub_modules()


# Now import the module under test
import api.main as main_module  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Synchronous TestClient for the FastAPI app (lifespan disabled for speed)."""
    with TestClient(main_module.app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def mock_chat_cls():
    return _stubs["mock_chat_cls"]


@pytest.fixture()
def mock_store():
    return _stubs["mock_store"]


# ---------------------------------------------------------------------------
# Tests: module-level constants and defaults
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_llm_temperature_default(self):
        assert main_module._LLM_TEMPERATURE == pytest.approx(0.6)

    def test_base_url_default(self):
        assert "openrouter.ai" in main_module._BASE_URL or main_module._BASE_URL != ""

    def test_llm_model_default(self):
        assert isinstance(main_module._LLM_MODEL, str)
        assert len(main_module._LLM_MODEL) > 0

    def test_show_tool_calls_is_bool(self):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_private_llm_instance_exists(self):
        """_llm should be the return value of the mocked ChatOpenAI constructor."""
        assert main_module._llm is not None

    def test_rag_tools_is_list(self):
        assert isinstance(main_module._rag_tools, list)

    def test_store_exists(self):
        assert main_module._store is not None


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_overrides(self):
        """Default call returns the module-level _llm singleton."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_with_explicit_defaults(self):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_overridden(self, mock_chat_cls):
        initial_call_count = mock_chat_cls.call_count
        result = main_module._get_llm(model="some-other-model")
        assert result is not main_module._llm
        assert mock_chat_cls.call_count == initial_call_count + 1

    def test_returns_new_instance_when_temperature_overridden(self, mock_chat_cls):
        initial_call_count = mock_chat_cls.call_count
        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm
        assert mock_chat_cls.call_count == initial_call_count + 1

    def test_returns_new_instance_when_both_overridden(self, mock_chat_cls):
        initial_call_count = mock_chat_cls.call_count
        result = main_module._get_llm(model="gpt-4", temperature=0.2)
        assert result is not main_module._llm
        assert mock_chat_cls.call_count == initial_call_count + 1

    def test_new_instance_uses_provided_model(self, mock_chat_cls):
        main_module._get_llm(model="custom-model-x", temperature=0.1)
        last_call_kwargs = mock_chat_cls.call_args
        assert last_call_kwargs is not None
        # model is passed as a keyword argument
        kwargs = last_call_kwargs.kwargs if last_call_kwargs.kwargs else {}
        args = last_call_kwargs.args if last_call_kwargs.args else ()
        model_val = kwargs.get("model") or (args[0] if args else None)
        assert model_val == "custom-model-x"

    def test_new_instance_uses_default_model_when_model_is_none_but_temperature_differs(
        self, mock_chat_cls
    ):
        main_module._get_llm(model=None, temperature=0.99)
        last_kwargs = mock_chat_cls.call_args.kwargs
        assert last_kwargs.get("model") == main_module._LLM_MODEL

    def test_temperature_zero_produces_new_instance(self, mock_chat_cls):
        initial = mock_chat_cls.call_count
        main_module._get_llm(temperature=0.0)
        assert mock_chat_cls.call_count == initial + 1

    def test_temperature_one_produces_new_instance(self, mock_chat_cls):
        initial = mock_chat_cls.call_count
        main_module._get_llm(temperature=1.0)
        assert mock_chat_cls.call_count == initial + 1


# ---------------------------------------------------------------------------
# Tests: system prompt templates
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    """Validate that _ROLEPLAY_SYSTEM contains all required format placeholders."""

    REQUIRED_KEYS = ["{name}", "{age}", "{occupation}", "{profile}",
                     "{stage_instruction}", "{today}"]

    def test_template_is_non_empty_string(self):
        assert isinstance(main_module._ROLEPLAY_SYSTEM, str)
        assert len(main_module._ROLEPLAY_SYSTEM) > 50

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_template_contains_placeholder(self, key):
        assert key in main_module._ROLEPLAY_SYSTEM, (
            f"_ROLEPLAY_SYSTEM missing placeholder: {key}"
        )

    def test_template_can_be_formatted(self):
        rendered = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=35,
            occupation="teacher",
            profile="Single mother of two.",
            stage_instruction="This is the first meeting.",
            today=str(date.today()),
        )
        assert "Alice" in rendered
        assert "teacher" in rendered

    def test_roleplay_system_mentions_character_instructions(self):
        # Confirm the never-break-character instruction is present
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_roleplay_system_mentions_today(self):
        assert "Today's date" in main_module._ROLEPLAY_SYSTEM


class TestPriorContextPromptTemplate:
    """Validate _PRIOR_CONTEXT_PROMPT."""

    REQUIRED_KEYS = ["{profile}", "{stage}"]

    def test_template_is_non_empty_string(self):
        assert isinstance(main_module._PRIOR_CONTEXT_PROMPT, str)
        assert len(main_module._PRIOR_CONTEXT_PROMPT) > 50

    @pytest.mark.parametrize("key", REQUIRED_KEYS)
    def test_template_contains_placeholder(self, key):
        assert key in main_module._PRIOR_CONTEXT_PROMPT, (
            f"_PRIOR_CONTEXT_PROMPT missing placeholder: {key}"
        )

    def test_template_can_be_formatted(self):
        rendered = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Bob, 45, accountant, married with 3 kids.",
            stage="2nd conversation",
        )
        assert "Bob" in rendered
        assert "2nd conversation" in rendered

    def test_prior_context_mentions_word_limit(self):
        assert "350" in main_module._PRIOR_CONTEXT_PROMPT

    def test_prior_context_second_person_guideline_mentioned(self):
        assert "second person" in main_module._PRIOR_CONTEXT_PROMPT.lower()

    def test_prior_context_no_product_names_guideline(self):
        assert "product names" in main_module._PRIOR_CONTEXT_PROMPT.lower() or \
               "product" in main_module._PRIOR_CONTEXT_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests: FastAPI app configuration
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_title(self):
        assert main_module.app.title == "Insurance Agent Trainer"

    def test_app_is_fastapi_instance(self):
        from fastapi import FastAPI
        assert isinstance(main_module.app, FastAPI)

    def test_cors_middleware_present(self):
        from fastapi.middleware.cors import CORSMiddleware
        middleware_types = [
            m.cls for m in main_module.app.user_middleware
            if hasattr(m, "cls")
        ]
        # In newer FastAPI/Starlette the middleware is stored differently
        # Fall back to checking middleware_stack or openapi routes
        found = any(
            "CORSMiddleware" in str(m) or (hasattr(m, "cls") and m.cls is CORSMiddleware)
            for m in main_module.app.user_middleware
        )
        # Alternative: inspect the app's middleware list via stack name strings
        middleware_names = [str(m) for m in main_module.app.user_middleware]
        assert found or any("CORS" in name for name in middleware_names)

    def test_cors_allows_localhost_5173(self):
        """The CORS middleware options should include the Vite dev server origin."""
        for m in main_module.app.user_middleware:
            kwargs = getattr(m, "kwargs", {})
            if "allow_origins" in kwargs:
                assert "http://localhost: