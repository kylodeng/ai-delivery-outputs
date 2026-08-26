"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: shared instance return, new instance creation for different model/temperature
- _build_roleplay_system: prompt construction with CustomerProfile data
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable completeness
- FastAPI endpoints via TestClient: health/lifespan, CORS middleware, static mounts
- Lifespan startup: load_sessions called, vector store load branches (success/failure)
- Session management endpoints (create, get, list, delete, update title)
- Chat/stream endpoints (happy path, error conditions, missing session)
- Ingest endpoint stubs

Mocks used:
- langchain_openai.ChatOpenAI (patched at api.main._llm and constructor)
- core.vector_store.get_vector_store
- api.rag_tools.make_rag_tools
- api.agent.make_teacher_agent, make_assessor_agent
- api.sessions.* (create_session, delete_session, generate_profile, get_session,
                  list_sessions, load_sessions, update_session_title)
- httpx.Client / httpx.AsyncClient (SSL verify=False)

TODOs:
- TODO: Full streaming response body validation requires async generator testing
- TODO: Ingest endpoint needs more detail once implementation is available
- TODO: _build_roleplay_system relies on undisclosed CustomerProfile fields — expand once confirmed
- TODO: Agent make_teacher_agent / make_assessor_agent stream logic needs integration tests
"""

import importlib
import os
import sys
import types
from datetime import date
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so that importing api.main does not require the full
# package tree to be installed in the test environment.
# ---------------------------------------------------------------------------

def _install_stub(dotted: str, obj=None):
    """Install a stub module at *dotted* path if it is not already present."""
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        if name not in sys.modules:
            mod = types.ModuleType(name)
            sys.modules[name] = mod
    if obj is not None:
        sys.modules[dotted] = obj


# ── langchain stubs ──────────────────────────────────────────────────────────

_langchain_core = types.ModuleType("langchain_core")
_langchain_core_msgs = types.ModuleType("langchain_core.messages")


class _FakeAIMessage:
    def __init__(self, content=""):
        self.content = content


class _FakeHumanMessage:
    def __init__(self, content=""):
        self.content = content


class _FakeSystemMessage:
    def __init__(self, content=""):
        self.content = content


_langchain_core_msgs.AIMessage = _FakeAIMessage
_langchain_core_msgs.HumanMessage = _FakeHumanMessage
_langchain_core_msgs.SystemMessage = _FakeSystemMessage
_langchain_core.messages = _langchain_core_msgs
sys.modules.setdefault("langchain_core", _langchain_core)
sys.modules.setdefault("langchain_core.messages", _langchain_core_msgs)

_langchain_openai_mod = types.ModuleType("langchain_openai")


class _FakeChatOpenAI:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


_langchain_openai_mod.ChatOpenAI = _FakeChatOpenAI
sys.modules.setdefault("langchain_openai", _langchain_openai_mod)

# ── dotenv stub ──────────────────────────────────────────────────────────────

_dotenv_mod = types.ModuleType("dotenv")
_dotenv_mod.load_dotenv = lambda *a, **kw: None
sys.modules.setdefault("dotenv", _dotenv_mod)

# ── httpx stub ───────────────────────────────────────────────────────────────

_httpx_mod = types.ModuleType("httpx")


class _FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeAsyncClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


_httpx_mod.Client = _FakeClient
_httpx_mod.AsyncClient = _FakeAsyncClient
sys.modules.setdefault("httpx", _httpx_mod)

# ── core / api sub-package stubs ─────────────────────────────────────────────

_core_mod = types.ModuleType("core")
_core_vs_mod = types.ModuleType("core.vector_store")
_fake_store = MagicMock()
_fake_store.load.return_value = True
_fake_store.get_known_products.return_value = ["prod_a", "prod_b"]
_core_vs_mod.get_vector_store = MagicMock(return_value=_fake_store)
sys.modules.setdefault("core", _core_mod)
sys.modules.setdefault("core.vector_store", _core_vs_mod)

_api_mod = types.ModuleType("api")
_api_rag_mod = types.ModuleType("api.rag_tools")
_api_rag_mod.make_rag_tools = MagicMock(return_value=[])
sys.modules.setdefault("api", _api_mod)
sys.modules.setdefault("api.rag_tools", _api_rag_mod)

_api_agent_mod = types.ModuleType("api.agent")
_api_agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
_api_agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
sys.modules.setdefault("api.agent", _api_agent_mod)

_api_sessions_mod = types.ModuleType("api.sessions")


class _FakeCustomerProfile:
    """Minimal stand-in for CustomerProfile."""

    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "Alice Tester")
        self.age = kwargs.get("age", 35)
        self.occupation = kwargs.get("occupation", "Teacher")
        self.profile = kwargs.get("profile", "A test profile string.")
        self.stage = kwargs.get("stage", "1st conversation")
        self.stage_instruction = kwargs.get("stage_instruction", "")
        self.model_fields = {}

    def model_dump(self):
        return {
            "name": self.name,
            "age": self.age,
            "occupation": self.occupation,
            "profile": self.profile,
            "stage": self.stage,
            "stage_instruction": self.stage_instruction,
        }


class _FakeSession:
    def __init__(self, session_id="sess-001", profile=None, title="Test Session"):
        self.session_id = session_id
        self.profile = profile or _FakeCustomerProfile()
        self.title = title
        self.messages = []
        self.created_at = "2024-01-01T00:00:00"


_api_sessions_mod.CustomerProfile = _FakeCustomerProfile
_api_sessions_mod.Session = _FakeSession
_api_sessions_mod.create_session = MagicMock(return_value=_FakeSession())
_api_sessions_mod.delete_session = MagicMock(return_value=True)
_api_sessions_mod.generate_profile = MagicMock(return_value=_FakeCustomerProfile())
_api_sessions_mod.get_session = MagicMock(return_value=_FakeSession())
_api_sessions_mod.list_sessions = MagicMock(return_value=[_FakeSession()])
_api_sessions_mod.load_sessions = MagicMock()
_api_sessions_mod.update_session_title = MagicMock(return_value=True)
sys.modules.setdefault("api.sessions", _api_sessions_mod)

# ── StaticFiles stub (avoids filesystem dependency) ──────────────────────────

_fastapi_staticfiles = types.ModuleType("fastapi.staticfiles")


class _FakeStaticFiles:
    def __init__(self, *args, **kwargs):
        pass


_fastapi_staticfiles.StaticFiles = _FakeStaticFiles
sys.modules["fastapi.staticfiles"] = _fastapi_staticfiles

# ---------------------------------------------------------------------------
# Now import the module under test
# ---------------------------------------------------------------------------

# Patch StaticFiles before the module mounts it
with patch("fastapi.staticfiles.StaticFiles", _FakeStaticFiles):
    import api.main as main_module  # noqa: E402  (must be after stubs)

from fastapi.testclient import TestClient  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Return a synchronous TestClient wrapping the FastAPI app."""
    # Override lifespan to be a no-op during testing
    with patch.object(main_module, "load_sessions"):
        with TestClient(main_module.app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture()
def fake_session():
    return _FakeSession(session_id="sess-001", profile=_FakeCustomerProfile())


@pytest.fixture()
def sample_customer_profile():
    return _FakeCustomerProfile(
        name="Bob Chan",
        age=42,
        occupation="Engineer",
        profile="Bob is a 42-year-old engineer living in Hong Kong with two children.",
        stage="2nd conversation",
        stage_instruction="The customer remembers the first call.",
    )


# ---------------------------------------------------------------------------
# Tests for _get_llm
# ---------------------------------------------------------------------------


class TestGetLlm:
    """Tests for the _get_llm helper."""

    def test_returns_shared_instance_when_no_args(self):
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_when_temperature_matches(self):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_for_different_model(self):
        result = main_module._get_llm(model="openai/gpt-4")
        assert result is not main_module._llm
        assert isinstance(result, _FakeChatOpenAI)

    def test_returns_new_instance_for_different_temperature(self):
        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm
        assert isinstance(result, _FakeChatOpenAI)

    def test_returns_new_instance_for_model_and_temperature(self):
        result = main_module._get_llm(model="anthropic/claude-3", temperature=0.1)
        assert result is not main_module._llm

    def test_new_instance_uses_provided_model(self):
        result = main_module._get_llm(model="my-custom-model")
        assert result.model == "my-custom-model"

    def test_new_instance_uses_provided_temperature(self):
        result = main_module._get_llm(temperature=0.99)
        assert result.temperature == 0.99

    def test_new_instance_falls_back_to_global_model_when_none(self):
        result = main_module._get_llm(temperature=0.2)
        assert result.model == main_module._LLM_MODEL

    def test_new_instance_has_streaming_enabled(self):
        result = main_module._get_llm(model="test-model")
        assert result.streaming is True


# ---------------------------------------------------------------------------
# Tests for _ROLEPLAY_SYSTEM template
# ---------------------------------------------------------------------------


class TestRoleplaySystemTemplate:
    """Verify the roleplay system prompt template contains required placeholders."""

    required_placeholders = [
        "{name}",
        "{age}",
        "{occupation}",
        "{profile}",
        "{stage_instruction}",
        "{today}",
    ]

    def test_template_contains_all_placeholders(self):
        for placeholder in self.required_placeholders:
            assert placeholder in main_module._ROLEPLAY_SYSTEM, (
                f"Missing placeholder: {placeholder}"
            )

    def test_template_is_non_empty(self):
        assert len(main_module._ROLEPLAY_SYSTEM.strip()) > 0

    def test_template_format_succeeds_with_valid_data(self):
        result = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=30,
            occupation="Nurse",
            profile="Alice is a nurse.",
            stage_instruction="",
            today=str(date.today()),
        )
        assert "Alice" in result
        assert "Nurse" in result


# ---------------------------------------------------------------------------
# Tests for _PRIOR_CONTEXT_PROMPT template
# ---------------------------------------------------------------------------


class TestPriorContextPromptTemplate:
    """Verify the prior context prompt template contains required placeholders."""

    required_placeholders = ["{profile}", "{stage}"]

    def test_template_contains_all_placeholders(self):
        for placeholder in self.required_placeholders:
            assert placeholder in main_module._PRIOR_CONTEXT_PROMPT, (
                f"Missing placeholder: {placeholder}"
            )

    def test_template_is_non_empty(self):
        assert len(main_module._PRIOR_CONTEXT_PROMPT.strip()) > 0

    def test_template_format_succeeds(self):
        result = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Bob, 42, engineer.",
            stage="2nd conversation",
        )
        assert "2nd conversation" in result
        assert "Bob" in result


# ---------------------------------------------------------------------------
# Tests for _build_roleplay_system (if accessible)
# ---------------------------------------------------------------------------


class TestBuildRoleplaySystem:
    """Tests for _build_roleplay_system prompt builder."""

    def _call(self, profile):
        return main_module._build_roleplay_system(profile)

    def test_returns_string(self, sample_customer_profile):
        result = self._call(sample_customer_profile)
        assert isinstance(result, str)

    def test_contains_customer_name(self, sample_customer_profile):
        result = self._call(sample_customer_profile)
        assert sample_customer_profile.name in result

    def test_contains_customer_age(self, sample_customer_profile):
        result = self._call(sample_customer_profile)
        assert str(sample_customer_profile.age) in result

    def test_contains_occupation(self, sample_customer_profile):
        result = self._call(sample_customer_profile)
        assert sample_customer_profile.occupation in result

    def test_contains_profile_text(self, sample_customer_profile):
        result = self._call(sample_customer_profile)
        assert sample_customer_profile.profile in result

    def test_contains_todays_date(self, sample_customer_profile):
        result = self._call(sample_customer_profile)
        assert str(date.today().year) in result

    def test_minimal_profile(self):
        profile = _FakeCustomerProfile(
            name="X",
            age=0,
            occupation="",
            profile="",
            stage_instruction="",
        )
        result = self._call(profile)
        assert isinstance(result, str)

    def test_special_characters_in_profile(self):
        profile = _FakeCustomerProfile(
            name="李明",
            age=55,
            occupation="退休人士",
            profile="李明是一位退休工程師，住在香港。",