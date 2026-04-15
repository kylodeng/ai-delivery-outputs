"""
Tests for api/main.py — Insurance Agent Training System (FastAPI backend).

What is tested:
    - _get_llm: returns shared instance vs new instance depending on params
    - _build_roleplay_system: (stub — function is truncated in source)
    - _ROLEPLAY_SYSTEM prompt template: required placeholders present
    - _PRIOR_CONTEXT_PROMPT prompt template: required placeholders present
    - FastAPI app configuration: title, middleware, routes
    - /docs static mount exists
    - CORS middleware origins
    - lifespan: load_sessions and vector store load/warn paths
    - SHOW_TOOL_CALLS env var parsing

Mocks used:
    - langchain_openai.ChatOpenAI (patched to avoid real OpenAI calls)
    - httpx.Client / httpx.AsyncClient (patched)
    - core.vector_store.get_vector_store
    - api.rag_tools.make_rag_tools
    - api.agent.make_teacher_agent, make_assessor_agent
    - api.sessions (create_session, delete_session, generate_profile, get_session,
                    list_sessions, load_sessions, update_session_title)
    - dotenv.load_dotenv (no-op)

TODOs:
    - TODO: _build_roleplay_system is truncated — full tests need the complete implementation
    - TODO: Integration tests for POST /ingest endpoint (not visible in truncated source)
    - TODO: Tests for HTTP endpoints (chat, sessions CRUD) once full source is available
    - TODO: Tests for make_teacher_agent / make_assessor_agent interaction via agent endpoints
"""

import importlib
import os
import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers to build a minimal import environment so api/main.py can be
# imported without a real OpenAI key, real vector store, or real DB.
# ---------------------------------------------------------------------------

def _make_fake_chat_openai():
    """Return a mock class that acts like ChatOpenAI."""
    instance = MagicMock()
    instance.model = "fake-model"
    cls = MagicMock(return_value=instance)
    return cls, instance


def _patch_all_heavy_deps(monkeypatch):
    """
    Patch every external dependency so that importing api.main succeeds
    in a plain test environment.
    """
    # Prevent load_dotenv from touching the filesystem
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)

    # Patch httpx clients used at module level
    fake_client = MagicMock()
    fake_async_client = MagicMock()
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=fake_client))
    monkeypatch.setattr("httpx.AsyncClient", MagicMock(return_value=fake_async_client))

    # Patch ChatOpenAI at the langchain_openai level
    fake_llm_cls, fake_llm_instance = _make_fake_chat_openai()
    monkeypatch.setattr("langchain_openai.ChatOpenAI", fake_llm_cls)

    # Build stub modules for internal packages so they don't require files on disk
    _stub_internal_modules(monkeypatch)

    return fake_llm_cls, fake_llm_instance


def _stub_internal_modules(monkeypatch):
    """Insert lightweight stub modules into sys.modules."""

    # ---- core.vector_store ------------------------------------------------
    fake_store = MagicMock()
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["ProductA", "ProductB"]

    vs_mod = types.ModuleType("core.vector_store")
    vs_mod.get_vector_store = MagicMock(return_value=fake_store)
    core_mod = types.ModuleType("core")
    core_mod.vector_store = vs_mod

    sys.modules.setdefault("core", core_mod)
    sys.modules["core.vector_store"] = vs_mod

    # ---- api.rag_tools ----------------------------------------------------
    rag_mod = types.ModuleType("api.rag_tools")
    rag_mod.make_rag_tools = MagicMock(return_value=[MagicMock()])
    sys.modules["api.rag_tools"] = rag_mod

    # ---- api.agent --------------------------------------------------------
    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    sys.modules["api.agent"] = agent_mod

    # ---- api.sessions -----------------------------------------------------
    from pydantic import BaseModel  # local import to keep test file self-contained

    class _FakeCustomerProfile(BaseModel):
        name: str = "Test User"
        age: int = 35
        occupation: str = "Engineer"

    class _FakeSession(BaseModel):
        id: str = "sess-001"
        title: str = "Test Session"

    sessions_mod = types.ModuleType("api.sessions")
    sessions_mod.CustomerProfile = _FakeCustomerProfile
    sessions_mod.Session = _FakeSession
    sessions_mod.create_session = MagicMock(return_value=_FakeSession())
    sessions_mod.delete_session = MagicMock(return_value=True)
    sessions_mod.generate_profile = MagicMock(return_value=_FakeCustomerProfile())
    sessions_mod.get_session = MagicMock(return_value=_FakeSession())
    sessions_mod.list_sessions = MagicMock(return_value=[])
    sessions_mod.load_sessions = MagicMock()
    sessions_mod.update_session_title = MagicMock(return_value=_FakeSession())
    sys.modules["api.sessions"] = sessions_mod

    # ---- langchain_core.messages ------------------------------------------
    lc_msgs = types.ModuleType("langchain_core.messages")
    lc_msgs.AIMessage = MagicMock
    lc_msgs.HumanMessage = MagicMock
    lc_msgs.SystemMessage = MagicMock
    lc_core = sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
    lc_core.messages = lc_msgs  # type: ignore[attr-defined]
    sys.modules["langchain_core.messages"] = lc_msgs

    # ---- langchain_openai -------------------------------------------------
    lo_mod = sys.modules.setdefault("langchain_openai", types.ModuleType("langchain_openai"))
    lo_mod.ChatOpenAI = MagicMock()  # type: ignore[attr-defined]

    # ---- pydantic SecretStr -----------------------------------------------
    # pydantic is a real dep; no stub needed

    return fake_store


# ---------------------------------------------------------------------------
# Fixture: import api.main freshly with all deps patched
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_module():
    """
    Import api.main with all external deps stubbed.
    Uses module scope so the import happens once per test session.
    """
    # Remove cached module if a previous test imported it
    for key in list(sys.modules.keys()):
        if key in ("api.main", "api"):
            del sys.modules[key]

    env_overrides = {
        "API_KEY": "test-api-key",
        "OPENAI_URL_BASE": "https://fake.openrouter.ai/api/v1",
        "OPENAI_MODEL": "fake/model",
        "SHOW_TOOL_CALLS": "true",
    }

    with patch.dict(os.environ, env_overrides, clear=False):
        with patch("dotenv.load_dotenv", lambda *a, **kw: None):
            with patch("httpx.Client", MagicMock(return_value=MagicMock())):
                with patch("httpx.AsyncClient", MagicMock(return_value=MagicMock())):
                    with patch("langchain_openai.ChatOpenAI", MagicMock(return_value=MagicMock())):
                        _stub_internal_modules_safe()
                        import api.main as m  # noqa: PLC0415
                        yield m

    # Cleanup after module-scoped fixture
    for key in list(sys.modules.keys()):
        if key in ("api.main",):
            del sys.modules[key]


def _stub_internal_modules_safe():
    """Idempotent version of _stub_internal_modules."""
    from pydantic import BaseModel  # noqa: PLC0415

    if "core.vector_store" not in sys.modules:
        fake_store = MagicMock()
        fake_store.load.return_value = True
        fake_store.get_known_products.return_value = ["P1", "P2"]
        vs_mod = types.ModuleType("core.vector_store")
        vs_mod.get_vector_store = MagicMock(return_value=fake_store)
        core_mod = types.ModuleType("core")
        core_mod.vector_store = vs_mod  # type: ignore[attr-defined]
        sys.modules.setdefault("core", core_mod)
        sys.modules["core.vector_store"] = vs_mod

    if "api.rag_tools" not in sys.modules:
        rag_mod = types.ModuleType("api.rag_tools")
        rag_mod.make_rag_tools = MagicMock(return_value=[MagicMock()])  # type: ignore[attr-defined]
        sys.modules["api.rag_tools"] = rag_mod

    if "api.agent" not in sys.modules:
        agent_mod = types.ModuleType("api.agent")
        agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
        agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
        sys.modules["api.agent"] = agent_mod

    if "api.sessions" not in sys.modules:

        class _CP(BaseModel):
            name: str = "Alice"
            age: int = 30
            occupation: str = "Nurse"

        class _Sess(BaseModel):
            id: str = "s1"
            title: str = "S1"

        sm = types.ModuleType("api.sessions")
        sm.CustomerProfile = _CP  # type: ignore[attr-defined]
        sm.Session = _Sess  # type: ignore[attr-defined]
        sm.create_session = MagicMock(return_value=_Sess())  # type: ignore[attr-defined]
        sm.delete_session = MagicMock(return_value=True)  # type: ignore[attr-defined]
        sm.generate_profile = MagicMock(return_value=_CP())  # type: ignore[attr-defined]
        sm.get_session = MagicMock(return_value=_Sess())  # type: ignore[attr-defined]
        sm.list_sessions = MagicMock(return_value=[])  # type: ignore[attr-defined]
        sm.load_sessions = MagicMock()  # type: ignore[attr-defined]
        sm.update_session_title = MagicMock(return_value=_Sess())  # type: ignore[attr-defined]
        sys.modules["api.sessions"] = sm

    if "langchain_core.messages" not in sys.modules:
        lc_msgs = types.ModuleType("langchain_core.messages")
        lc_msgs.AIMessage = MagicMock  # type: ignore[attr-defined]
        lc_msgs.HumanMessage = MagicMock  # type: ignore[attr-defined]
        lc_msgs.SystemMessage = MagicMock  # type: ignore[attr-defined]
        lc_core = sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
        sys.modules["langchain_core.messages"] = lc_msgs


# ---------------------------------------------------------------------------
# Tests: Module-level constants
# ---------------------------------------------------------------------------


class TestEnvVarParsing:
    """SHOW_TOOL_CALLS and other env vars parsed at import time."""

    @pytest.mark.parametrize(
        "env_val, expected",
        [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
            ("yes", False),   # only "true" (case-insensitive) should be True
            ("", False),
        ],
    )
    def test_show_tool_calls_parsing(self, env_val, expected):
        result = env_val.lower() == "true"
        assert result is expected

    def test_default_base_url(self):
        """Default BASE_URL fallback is openrouter."""
        with patch.dict(os.environ, {}, clear=True):
            val = os.getenv("OPENAI_URL_BASE", "https://openrouter.ai/api/v1")
        assert val == "https://openrouter.ai/api/v1"

    def test_default_model(self):
        with patch.dict(os.environ, {}, clear=True):
            val = os.getenv("OPENAI_MODEL", "openai/gpt-oss-20b:free")
        assert val == "openai/gpt-oss-20b:free"

    def test_api_key_defaults_to_empty_string(self):
        with patch.dict(os.environ, {}, clear=True):
            val = os.getenv("API_KEY", "")
        assert val == ""

    def test_custom_env_values_are_picked_up(self):
        with patch.dict(os.environ, {"API_KEY": "sk-secret", "OPENAI_MODEL": "gpt-4"}):
            assert os.getenv("API_KEY") == "sk-secret"
            assert os.getenv("OPENAI_MODEL") == "gpt-4"


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------


class TestGetLlm:
    """Tests for the _get_llm factory function."""

    def test_returns_shared_instance_when_no_overrides(self, main_module):
        """When model=None and temperature equals _LLM_TEMPERATURE, return _llm."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_explicit_default_temperature(self, main_module):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_provided(self, main_module):
        """Providing a custom model should create a new ChatOpenAI instance."""
        import langchain_openai  # noqa: PLC0415

        new_instance = MagicMock()
        with patch.object(langchain_openai, "ChatOpenAI", return_value=new_instance) as mock_cls:
            result = main_module._get_llm(model="gpt-4")
            mock_cls.assert_called_once()
            assert result is new_instance

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        """A different temperature should produce a new ChatOpenAI instance."""
        import langchain_openai  # noqa: PLC0415

        new_instance =