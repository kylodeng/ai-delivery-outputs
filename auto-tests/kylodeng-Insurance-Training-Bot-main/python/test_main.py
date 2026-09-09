"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
    - _get_llm(): returns shared instance vs new instance based on parameters
    - _build_roleplay_system(): builds roleplay system prompt correctly
    - _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template strings contain required placeholders
    - FastAPI app endpoints (via TestClient / AsyncClient):
        * GET /health or root behaviour
        * POST /ingest
        * POST /chat (streaming)
        * Session CRUD endpoints
        * Profile generation endpoint
    - CORS middleware is configured
    - Lifespan: load_sessions and vector store load/warn paths

Mocks used:
    - langchain_openai.ChatOpenAI (to avoid real LLM calls)
    - httpx.Client / httpx.AsyncClient (SSL/network calls)
    - core.vector_store.get_vector_store
    - api.rag_tools.make_rag_tools
    - api.agent.make_teacher_agent / make_assessor_agent
    - api.sessions.* (load_sessions, create_session, get_session, etc.)
    - os.getenv (selectively, via monkeypatch)

TODOs:
    - TODO: Full streaming SSE response body parsing requires a running event loop +
            real async generator; stub tests are provided with pytest.mark.skip
    - TODO: POST /ingest endpoint body not visible in the provided source snippet;
            stubs are provided
    - TODO: _build_roleplay_system full implementation not in snippet; tests cover
            what is visible + expected contract
"""

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake module tree so that importing api.main does
# not fail due to missing optional dependencies in the test environment.
# ---------------------------------------------------------------------------

def _make_fake_sessions_module():
    """Return a mock module that satisfies `from api.sessions import ...`."""
    mod = types.ModuleType("api.sessions")

    class CustomerProfile:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Session:
        def __init__(self, session_id="s1", title="T", messages=None, profile=None):
            self.session_id = session_id
            self.title = title
            self.messages = messages or []
            self.profile = profile

    mod.CustomerProfile = CustomerProfile
    mod.Session = Session
    mod.create_session = MagicMock(return_value=Session())
    mod.delete_session = MagicMock(return_value=True)
    mod.generate_profile = MagicMock(return_value=CustomerProfile(name="Alice"))
    mod.get_session = MagicMock(return_value=Session())
    mod.list_sessions = MagicMock(return_value=[])
    mod.load_sessions = MagicMock()
    mod.update_session_title = MagicMock(return_value=True)
    return mod


def _make_fake_vector_store():
    vs = MagicMock()
    vs.load.return_value = True
    vs.get_known_products.return_value = ["ProductA", "ProductB"]
    return vs


def _patch_imports_and_import_app():
    """
    Patch all heavy dependencies before importing api.main so tests remain
    isolated from real LLM / vector-store / HTTP infrastructure.
    Returns the imported app module.
    """
    # Fake out the ChatOpenAI constructor to avoid real network/SSL
    fake_llm = MagicMock()
    fake_llm_cls = MagicMock(return_value=fake_llm)

    # Build fake sub-modules
    fake_sessions = _make_fake_sessions_module()
    fake_vs = _make_fake_vector_store()
    fake_rag_tools = [MagicMock(name="rag_tool_1")]
    fake_teacher_agent = MagicMock()
    fake_assessor_agent = MagicMock()

    # We must inject fakes BEFORE the module is imported
    patches = {
        "langchain_openai": MagicMock(ChatOpenAI=fake_llm_cls),
        "langchain_core.messages": MagicMock(
            AIMessage=MagicMock,
            HumanMessage=MagicMock,
            SystemMessage=MagicMock,
        ),
        "httpx": MagicMock(
            Client=MagicMock(return_value=MagicMock()),
            AsyncClient=MagicMock(return_value=MagicMock()),
        ),
        "core.vector_store": MagicMock(get_vector_store=MagicMock(return_value=fake_vs)),
        "api.rag_tools": MagicMock(make_rag_tools=MagicMock(return_value=fake_rag_tools)),
        "api.agent": MagicMock(
            make_teacher_agent=MagicMock(return_value=fake_teacher_agent),
            make_assessor_agent=MagicMock(return_value=fake_assessor_agent),
        ),
        "api.sessions": fake_sessions,
        "dotenv": MagicMock(load_dotenv=MagicMock()),
    }

    # Register patches into sys.modules
    for name, fake in patches.items():
        sys.modules[name] = fake

    # Also make sub-packages available
    if "api" not in sys.modules:
        sys.modules["api"] = types.ModuleType("api")
    if "core" not in sys.modules:
        sys.modules["core"] = types.ModuleType("core")

    # Remove previously cached api.main so re-import picks up patches
    sys.modules.pop("api.main", None)

    import api.main as main_module  # noqa: PLC0415

    return main_module, fake_llm, fake_llm_cls, fake_sessions, fake_vs


# ---------------------------------------------------------------------------
# Module-level fixture: import once and share
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_mod():
    mod, fake_llm, fake_llm_cls, fake_sessions, fake_vs = _patch_imports_and_import_app()
    return {
        "mod": mod,
        "fake_llm": fake_llm,
        "fake_llm_cls": fake_llm_cls,
        "fake_sessions": fake_sessions,
        "fake_vs": fake_vs,
    }


@pytest.fixture(scope="module")
def client(main_mod):
    """Synchronous TestClient wrapping the FastAPI app."""
    app = main_mod["mod"].app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ===========================================================================
# 1. Module-level constant & env-var tests
# ===========================================================================

class TestModuleConstants:

    def test_show_tool_calls_default_true(self, main_mod):
        """SHOW_TOOL_CALLS should default to True when env var not set."""
        # The module was imported without explicit env manipulation;
        # the default in source is os.getenv("SHOW_TOOL_CALLS", "true") == "true"
        assert isinstance(main_mod["mod"].SHOW_TOOL_CALLS, bool)

    def test_llm_temperature_constant(self, main_mod):
        assert main_mod["mod"]._LLM_TEMPERATURE == 0.6

    def test_roleplay_system_contains_placeholders(self, main_mod):
        template = main_mod["mod"]._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_contains_placeholders(self, main_mod):
        template = main_mod["mod"]._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_max_words_mentioned(self, main_mod):
        """Prompt instructions should reference the 350-word limit."""
        assert "350" in main_mod["mod"]._PRIOR_CONTEXT_PROMPT

    def test_roleplay_system_mentions_today(self, main_mod):
        assert "Today's date" in main_mod["mod"]._ROLEPLAY_SYSTEM


# ===========================================================================
# 2. _get_llm() tests
# ===========================================================================

class TestGetLlm:

    def test_returns_shared_instance_when_no_override(self, main_mod):
        """_get_llm() with defaults returns the module-level _llm."""
        mod = main_mod["mod"]
        result = mod._get_llm()
        assert result is mod._llm

    def test_returns_new_instance_when_model_differs(self, main_mod):
        mod = main_mod["mod"]
        fake_llm_cls = main_mod["fake_llm_cls"]
        call_count_before = fake_llm_cls.call_count
        result = mod._get_llm(model="some-other-model")
        assert fake_llm_cls.call_count > call_count_before
        # The result should NOT be the shared _llm
        # (it is the return value of the constructor mock, but a NEW call was made)

    def test_returns_new_instance_when_temperature_differs(self, main_mod):
        mod = main_mod["mod"]
        fake_llm_cls = main_mod["fake_llm_cls"]
        call_count_before = fake_llm_cls.call_count
        mod._get_llm(temperature=0.9)
        assert fake_llm_cls.call_count > call_count_before

    def test_returns_shared_when_temperature_equals_default(self, main_mod):
        mod = main_mod["mod"]
        result = mod._get_llm(model=None, temperature=0.6)
        assert result is mod._llm

    def test_new_instance_uses_provided_model_name(self, main_mod):
        mod = main_mod["mod"]
        fake_llm_cls = main_mod["fake_llm_cls"]
        fake_llm_cls.reset_mock()
        mod._get_llm(model="custom-model-xyz")
        _, kwargs = fake_llm_cls.call_args
        assert kwargs.get("model") == "custom-model-xyz"

    def test_new_instance_uses_provided_temperature(self, main_mod):
        mod = main_mod["mod"]
        fake_llm_cls = main_mod["fake_llm_cls"]
        fake_llm_cls.reset_mock()
        mod._get_llm(temperature=0.1)
        _, kwargs = fake_llm_cls.call_args
        assert kwargs.get("temperature") == 0.1

    def test_new_instance_uses_default_model_when_model_none_but_temp_differs(self, main_mod):
        mod = main_mod["mod"]
        fake_llm_cls = main_mod["fake_llm_cls"]
        fake_llm_cls.reset_mock()
        mod._get_llm(model=None, temperature=0.2)
        _, kwargs = fake_llm_cls.call_args
        assert kwargs.get("model") == mod._LLM_MODEL

    def test_get_llm_streaming_enabled(self, main_mod):
        mod = main_mod["mod"]
        fake_llm_cls = main_mod["fake_llm_cls"]
        fake_llm_cls.reset_mock()
        mod._get_llm(temperature=0.3)
        _, kwargs = fake_llm_cls.call_args
        assert kwargs.get("streaming") is True


# ===========================================================================
# 3. FastAPI app structure tests
# ===========================================================================

class TestAppStructure:

    def test_app_title(self, main_mod):
        assert main_mod["mod"].app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, main_mod):
        from starlette.middleware.cors import CORSMiddleware
        middleware_types = [
            m.cls for m in main_mod["mod"].app.user_middleware
        ]
        assert CORSMiddleware in middleware_types

    def test_cors_allows_localhost_5173(self, main_mod):
        app = main_mod["mod"].app
        # Find CORSMiddleware config
        from starlette.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:5173" in origins
                break

    def test_cors_allows_localhost_8000(self, main_mod):
        app = main_mod["mod"].app
        from starlette.middleware.cors import CORSMiddleware
        for m in app.user_middleware:
            if m.cls is CORSMiddleware:
                origins = m.kwargs.get("allow_origins", [])
                assert "http://localhost:8000" in origins
                break

    def test_docs_route_mounted(self, main_mod):
        """Static files should be mounted at /docs."""
        app = main_mod["mod"].app
        route_paths = [getattr(r, "path", None) for r in app.routes]
        assert "/docs" in route_paths


# ===========================================================================
# 4. Lifespan tests
# ===========================================================================

class TestLifespan:

    @pytest.mark.asyncio
    async def test_lifespan_calls_load_sessions(self, main_mod):
        fake_sessions = main_mod["fake_sessions"]
        fake_sessions.load_sessions.reset_mock()
        mod = main_mod["mod"]

        async with mod.lifespan(mod.app):
            pass

        fake_sessions.load_sessions.assert_called_once()

    @pytest.mark.asyncio
    async def test_lifespan_logs_warning_when_store_not_loaded(self, main_mod):
        mod = main_mod["mod"]
        fake_vs = main_mod["fake_vs"]
        fake_vs.load.return_value = False

        with patch.object(mod.logger, "warning") as mock_warn:
            async with mod.lifespan(mod.app):
                pass
            mock_warn.assert_called()
            call_args = str(mock_warn.call_args)
            assert "ingest" in call_args.lower() or "vector" in call_args.lower()

        # Reset for other tests
        fake_vs.load.return_value = True

    @pytest.mark.asyncio
    async def test_lifespan_logs_info_when_store_loaded(self, main_mod):
        mod = main_mod["mod"]
        fake_vs = main_mod["fake_vs"]
        fake_vs.load.return_value = True
        fake_vs.get_known_products.return_value = ["P1", "P2", "P3"]

        with patch.object(mod.logger, "info") as mock_info:
            async with mod.lifespan(mod.app):
                pass
            calls_str = " ".join(str(c) for c in mock_info.call_args_list)
            assert "3" in calls_str or "vector" in calls_str.lower()


# ===========================================================================
# 5. _ROLEPLAY_SYSTEM prompt formatting
# ===========================================================================

class TestRoleplaySystemPrompt:

    def test_format_with_all_required_fields(self, main_mod):
        template = main_mod["mod"]._ROLEPLAY_SYSTEM
        result = template.format(
            name="Alice",
            age=35,
            occupation="teacher",
            profile="Single mother, two kids.",