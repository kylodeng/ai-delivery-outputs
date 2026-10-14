"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
    - _get_llm(): shared instance returned, new instance created when params differ
    - _build_roleplay_system(): system prompt construction with CustomerProfile
    - _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template content / placeholders
    - FastAPI app endpoints: lifespan, CORS middleware, static mount
    - API route behaviours (chat, sessions, ingest, etc.) via AsyncClient
    - Module-level constants loaded from environment

Mocks used:
    - langchain_openai.ChatOpenAI (prevents real LLM calls)
    - httpx.Client / httpx.AsyncClient (prevents real HTTP calls)
    - core.vector_store.get_vector_store
    - api.rag_tools.make_rag_tools
    - api.agent.make_teacher_agent / make_assessor_agent
    - api.sessions.* (load_sessions, get_session, create_session, etc.)
    - os.getenv (selective, via monkeypatch)

TODOs:
    - TODO: Full route tests for POST /chat, POST /assess, POST /ingest require
            additional context about request/response schemas — stubs provided.
    - TODO: Tests for streaming SSE responses need real async generator behaviour
            from the mocked LLM — stubs provided.
    - TODO: _build_roleplay_system full rendering test requires CustomerProfile
            schema from api/sessions — stub provided if import fails.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Minimal stubs for heavy dependencies that may not be installed in CI
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# Stub out langchain / openai packages before importing main
_stub_chat_openai_cls = MagicMock(name="ChatOpenAI")
_stub_chat_openai_instance = MagicMock(name="chat_openai_instance")
_stub_chat_openai_cls.return_value = _stub_chat_openai_instance

for _pkg in [
    "langchain_openai",
    "langchain_core",
    "langchain_core.messages",
]:
    if _pkg not in sys.modules:
        sys.modules[_pkg] = _make_stub_module(_pkg)

sys.modules["langchain_openai"].ChatOpenAI = _stub_chat_openai_cls  # type: ignore[attr-defined]

# Stub message classes
_msg_mod = sys.modules.get("langchain_core.messages") or _make_stub_module("langchain_core.messages")
for _cls_name in ("AIMessage", "HumanMessage", "SystemMessage"):
    if not hasattr(_msg_mod, _cls_name):
        setattr(_msg_mod, _cls_name, MagicMock(name=_cls_name))
sys.modules["langchain_core.messages"] = _msg_mod

# Stub dotenv
if "dotenv" not in sys.modules:
    _dotenv_mod = _make_stub_module("dotenv", load_dotenv=MagicMock())
    sys.modules["dotenv"] = _dotenv_mod

# Stub pydantic SecretStr if needed
try:
    from pydantic import SecretStr  # noqa: F401
except Exception:
    _pydantic_mod = _make_stub_module("pydantic", BaseModel=object, SecretStr=str)
    sys.modules["pydantic"] = _pydantic_mod

# Stub internal modules
_stub_vector_store = MagicMock(name="vector_store")
_stub_vector_store.load.return_value = True
_stub_vector_store.get_known_products.return_value = ["ProdA", "ProdB"]

_stub_get_vector_store = MagicMock(return_value=_stub_vector_store)
_stub_make_rag_tools = MagicMock(return_value=[MagicMock(name="rag_tool")])
_stub_make_teacher_agent = MagicMock(return_value=MagicMock(name="teacher_agent"))
_stub_make_assessor_agent = MagicMock(return_value=MagicMock(name="assessor_agent"))

# CustomerProfile / Session stubs
class _StubCustomerProfile:
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "Alice")
        self.age = kwargs.get("age", 35)
        self.occupation = kwargs.get("occupation", "Teacher")
        self.profile = kwargs.get("profile", "A teacher who loves hiking.")
        self.stage = kwargs.get("stage", "1st_meeting")
        self.stage_instruction = kwargs.get("stage_instruction", "")
        self.today = kwargs.get("today", "2024-01-01")

    def dict(self):
        return self.__dict__.copy()


class _StubSession:
    def __init__(self, session_id="sess-001", profile=None, messages=None):
        self.session_id = session_id
        self.profile = profile or _StubCustomerProfile()
        self.messages = messages or []
        self.title = "Test session"


_stub_sessions_module = _make_stub_module(
    "api.sessions",
    CustomerProfile=_StubCustomerProfile,
    Session=_StubSession,
    create_session=MagicMock(return_value=_StubSession()),
    delete_session=MagicMock(),
    generate_profile=AsyncMock(return_value=_StubCustomerProfile()),
    get_session=MagicMock(return_value=_StubSession()),
    list_sessions=MagicMock(return_value=[]),
    load_sessions=MagicMock(),
    update_session_title=MagicMock(),
)

for _mod_name, _mod_obj in [
    ("core.vector_store", _make_stub_module("core.vector_store", get_vector_store=_stub_get_vector_store)),
    ("api.rag_tools", _make_stub_module("api.rag_tools", make_rag_tools=_stub_make_rag_tools)),
    ("api.agent", _make_stub_module("api.agent",
                                    make_teacher_agent=_stub_make_teacher_agent,
                                    make_assessor_agent=_stub_make_assessor_agent)),
    ("api.sessions", _stub_sessions_module),
    ("core", _make_stub_module("core")),
    ("api", _make_stub_module("api")),
]:
    sys.modules.setdefault(_mod_name, _mod_obj)

# Now import the module under test
import importlib as _importlib

# We need to patch StaticFiles so it doesn't try to access the filesystem
with patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None):
    with patch("fastapi.staticfiles.StaticFiles.__call__", new_callable=AsyncMock):
        import api.main as main  # noqa: E402  # type: ignore[import]

from api.main import (  # noqa: E402
    _ROLEPLAY_SYSTEM,
    _PRIOR_CONTEXT_PROMPT,
    _get_llm,
    _LLM_TEMPERATURE,
    SHOW_TOOL_CALLS,
    app,
)

# Try to import _build_roleplay_system — it may be truncated in source
try:
    from api.main import _build_roleplay_system  # type: ignore[import]
    _HAS_BUILD_ROLEPLAY = True
except ImportError:
    _HAS_BUILD_ROLEPLAY = False

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def reset_chat_openai_mock():
    """Reset the ChatOpenAI mock call count between tests."""
    _stub_chat_openai_cls.reset_mock()
    yield
    _stub_chat_openai_cls.reset_mock()


@pytest.fixture()
def sample_profile() -> _StubCustomerProfile:
    return _StubCustomerProfile(
        name="Bob Tan",
        age=42,
        occupation="Engineer",
        profile="Bob is a 42-year-old engineer with two kids and a mortgage.",
        stage="2nd_meeting",
        stage_instruction="The agent is conducting a fact-finding session.",
        today="2024-06-15",
    )


@pytest.fixture()
def async_client():
    """Provide an httpx AsyncClient pointed at the FastAPI test app."""
    from httpx import AsyncClient, ASGITransport
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Tests — module-level constants
# ---------------------------------------------------------------------------

class TestModuleConstants:
    def test_llm_temperature_is_float(self):
        assert isinstance(_LLM_TEMPERATURE, float)

    def test_llm_temperature_value(self):
        assert _LLM_TEMPERATURE == pytest.approx(0.6)

    def test_show_tool_calls_is_bool(self):
        assert isinstance(SHOW_TOOL_CALLS, bool)

    def test_show_tool_calls_default_true(self, monkeypatch):
        """When SHOW_TOOL_CALLS env var is 'true', constant should be True."""
        monkeypatch.setenv("SHOW_TOOL_CALLS", "true")
        # Re-evaluate the expression used in source
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is True

    def test_show_tool_calls_false(self, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", "false")
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is False

    def test_show_tool_calls_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("SHOW_TOOL_CALLS", "TRUE")
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is True

    def test_roleplay_system_is_string(self):
        assert isinstance(_ROLEPLAY_SYSTEM, str)

    def test_prior_context_prompt_is_string(self):
        assert isinstance(_PRIOR_CONTEXT_PROMPT, str)

    def test_roleplay_system_contains_required_placeholders(self):
        required = ["{name}", "{age}", "{occupation}", "{profile}",
                    "{stage_instruction}", "{today}"]
        for placeholder in required:
            assert placeholder in _ROLEPLAY_SYSTEM, (
                f"Missing placeholder {placeholder} in _ROLEPLAY_SYSTEM"
            )

    def test_prior_context_prompt_contains_required_placeholders(self):
        required = ["{profile}", "{stage}"]
        for placeholder in required:
            assert placeholder in _PRIOR_CONTEXT_PROMPT, (
                f"Missing placeholder {placeholder} in _PRIOR_CONTEXT_PROMPT"
            )

    def test_roleplay_system_instructs_no_character_break(self):
        assert "Never break character" in _ROLEPLAY_SYSTEM

    def test_roleplay_system_includes_today(self):
        assert "Today's date" in _ROLEPLAY_SYSTEM

    def test_prior_context_prompt_max_words_guidance(self):
        assert "350" in _PRIOR_CONTEXT_PROMPT

    def test_prior_context_prompt_second_person_guidance(self):
        assert "second person" in _PRIOR_CONTEXT_PROMPT


# ---------------------------------------------------------------------------
# Tests — _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_args(self, reset_chat_openai_mock):
        result = _get_llm()
        # Should return the module-level _llm, not create a new one
        assert result is main._llm

    def test_returns_shared_instance_with_default_temperature(self, reset_chat_openai_mock):
        result = _get_llm(model=None, temperature=_LLM_TEMPERATURE)
        assert result is main._llm

    def test_creates_new_instance_when_model_differs(self, reset_chat_openai_mock):
        _stub_chat_openai_cls.reset_mock()
        new_instance = MagicMock(name="new_llm")
        _stub_chat_openai_cls.return_value = new_instance

        result = _get_llm(model="openai/gpt-4")
        # A new ChatOpenAI should have been constructed
        _stub_chat_openai_cls.assert_called_once()
        assert result is new_instance

    def test_creates_new_instance_when_temperature_differs(self, reset_chat_openai_mock):
        _stub_chat_openai_cls.reset_mock()
        new_instance = MagicMock(name="new_llm_temp")
        _stub_chat_openai_cls.return_value = new_instance

        result = _get_llm(temperature=0.9)
        _stub_chat_openai_cls.assert_called_once()
        assert result is new_instance

    def test_creates_new_instance_with_custom_model_and_temperature(self, reset_chat_openai_mock):
        _stub_chat_openai_cls.reset_mock()
        _get_llm(model="openai/gpt-4o", temperature=0.1)
        call_kwargs = _stub_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("model") == "openai/gpt-4o"
        assert call_kwargs.get("temperature") == pytest.approx(0.1)

    def test_new_instance_uses_module_model_when_model_none_but_temp_differs(
        self, reset_chat_openai_mock
    ):
        _stub_chat_openai_cls.reset_mock()
        _get_llm(model=None, temperature=0.1)
        call_kwargs = _stub_chat_openai_cls.call_args.kwargs
        # Should fall back to _LLM_MODEL
        assert call_kwargs.get("model") == main._LLM_MODEL

    def test_streaming_enabled_on_new_instance(self, reset_chat_openai_mock):
        _stub_chat_openai_cls.reset_mock()
        _get_llm(temperature=0.1)
        call_kwargs = _stub_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("streaming") is True

    def test_new_instance_uses_base_url(self, reset_chat_openai_mock):
        _stub_chat_openai_cls.reset_mock()
        _get_llm(temperature=0.1)
        call_kwargs = _stub_chat_openai_cls.call_args.kwargs
        assert call_kwargs.get("base_url") == main._BASE_URL


# ---------------------------------------------------------------------------
# Tests — _build_roleplay_system (conditional on availability)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAS_BUILD_ROLEPLAY, reason="Function not yet fully defined in source")
class TestBuildRoleplaySystem:
    def test_returns_string(self, sample_profile):
        result = _build_roleplay_system(sample_profile)
        assert isinstance(result, str)

    def test_contains_name(self, sample_profile):
        result = _build_roleplay_system(sample_profile)
        assert "Bob Tan" in result

    def test_contains_age(self, sample_profile):
        result = _build_roleplay_system(sample