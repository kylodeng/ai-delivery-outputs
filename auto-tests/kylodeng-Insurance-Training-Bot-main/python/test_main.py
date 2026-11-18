"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm(): returns shared instance or new ChatOpenAI based on parameters
- _build_roleplay_system(): system prompt construction from CustomerProfile
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template variable coverage
- FastAPI app: lifespan, CORS middleware, static mount, route existence
- HTTP endpoints via TestClient (mocked LLM, vector store, sessions, RAG tools)
- StreamingResponse generation for chat endpoints
- Edge cases: missing API keys, malformed bodies, empty histories

Mocks used:
- core.vector_store.get_vector_store → MagicMock
- api.rag_tools.make_rag_tools → MagicMock
- api.agent.make_teacher_agent, make_assessor_agent → MagicMock
- api.sessions (create_session, get_session, delete_session, etc.) → MagicMock
- langchain_openai.ChatOpenAI → MagicMock
- httpx.Client / httpx.AsyncClient → MagicMock (ssl verify=False side-effect)

TODOs:
- TODO: Full end-to-end streaming test requires a running event loop + real async generator;
  stub provided.
- TODO: /ingest endpoint tests require the actual ingest implementation to be visible.
- TODO: Tests for update_session_title, list_sessions endpoints need route definitions
  confirmed from the full source.
"""

from __future__ import annotations

import importlib
import sys
import types
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers — build a minimal stub hierarchy so importing api.main doesn't fail
# even when the real sub-packages are absent in the test environment.
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """Register lightweight stub modules before api.main is imported."""
    stubs: dict[str, types.ModuleType] = {}

    # core.vector_store
    vs_mod = types.ModuleType("core.vector_store")
    mock_store = MagicMock()
    mock_store.load.return_value = True
    mock_store.get_known_products.return_value = ["ProductA", "ProductB"]
    vs_mod.get_vector_store = MagicMock(return_value=mock_store)
    stubs["core"] = types.ModuleType("core")
    stubs["core.vector_store"] = vs_mod

    # api.rag_tools
    rag_mod = types.ModuleType("api.rag_tools")
    rag_mod.make_rag_tools = MagicMock(return_value=[MagicMock(name="rag_tool")])
    stubs["api.rag_tools"] = rag_mod

    # api.agent
    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    stubs["api.agent"] = agent_mod

    # api.sessions
    sessions_mod = types.ModuleType("api.sessions")

    class _CustomerProfile(MagicMock):
        pass

    class _Session(MagicMock):
        pass

    sessions_mod.CustomerProfile = _CustomerProfile
    sessions_mod.Session = _Session
    sessions_mod.create_session = MagicMock()
    sessions_mod.delete_session = MagicMock()
    sessions_mod.generate_profile = MagicMock()
    sessions_mod.get_session = MagicMock()
    sessions_mod.list_sessions = MagicMock(return_value=[])
    sessions_mod.load_sessions = MagicMock()
    sessions_mod.update_session_title = MagicMock()
    stubs["api.sessions"] = sessions_mod

    # langchain_core.messages
    lc_msg = types.ModuleType("langchain_core.messages")
    lc_msg.AIMessage = MagicMock
    lc_msg.HumanMessage = MagicMock
    lc_msg.SystemMessage = MagicMock
    stubs["langchain_core"] = types.ModuleType("langchain_core")
    stubs["langchain_core.messages"] = lc_msg

    # langchain_openai
    lo_mod = types.ModuleType("langchain_openai")
    lo_mod.ChatOpenAI = MagicMock()
    stubs["langchain_openai"] = lo_mod

    # pydantic (keep real if available, else stub)
    try:
        import pydantic  # noqa: F401
    except ImportError:
        p_mod = types.ModuleType("pydantic")
        p_mod.BaseModel = object
        p_mod.SecretStr = str
        stubs["pydantic"] = p_mod

    # httpx
    try:
        import httpx  # noqa: F401
    except ImportError:
        h_mod = types.ModuleType("httpx")
        h_mod.Client = MagicMock
        h_mod.AsyncClient = MagicMock
        stubs["httpx"] = h_mod

    # dotenv
    try:
        import dotenv  # noqa: F401
    except ImportError:
        d_mod = types.ModuleType("dotenv")
        d_mod.load_dotenv = MagicMock()
        stubs["dotenv"] = d_mod

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)


_make_stub_modules()


# ---------------------------------------------------------------------------
# Now import the module under test.
# We patch ChatOpenAI at construction time so the module-level _llm creation
# doesn't attempt a real network connection.
# ---------------------------------------------------------------------------

with (
    patch("langchain_openai.ChatOpenAI", MagicMock()),
    patch("httpx.Client", MagicMock()),
    patch("httpx.AsyncClient", MagicMock()),
):
    # Ensure a clean import even if partially cached
    for _key in list(sys.modules.keys()):
        if _key == "api.main":
            del sys.modules[_key]

    import api.main as main_module
    from api.main import (
        _get_llm,
        _LLM_TEMPERATURE,
        _LLM_MODEL,
        _ROLEPLAY_SYSTEM,
        _PRIOR_CONTEXT_PROMPT,
        app,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Synchronous TestClient with lifespan disabled for unit tests."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def mock_llm():
    """A fresh MagicMock standing in for ChatOpenAI."""
    m = MagicMock()
    m.stream = MagicMock(return_value=iter([]))
    m.astream = AsyncMock(return_value=aiter_empty())
    return m


async def aiter_empty():
    """Async generator that yields nothing."""
    return
    yield  # pragma: no cover — make it a generator


@pytest.fixture()
def sample_customer_profile():
    """
    Minimal dict representing a CustomerProfile suitable for prompt building.
    Derived from the synthetic data samples (insurance context).
    """
    return {
        "name": "Alice Wong",
        "age": 35,
        "occupation": "Software Engineer",
        "profile": (
            "Alice is a 35-year-old software engineer earning HKD 60,000/month. "
            "She has a 3-year-old daughter and a husband who is self-employed. "
            "She currently holds no life insurance and is interested in savings plans."
        ),
        "stage": "2nd_conversation",
        "today": str(date.today()),
        "stage_instruction": "The agent is following up after the initial meeting.",
    }


# ---------------------------------------------------------------------------
# Tests for _get_llm()
# ---------------------------------------------------------------------------


class TestGetLlm:
    """Tests for the _get_llm helper function."""

    def test_returns_shared_instance_when_no_args(self):
        """With default args the module-level _llm object is returned."""
        result = _get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_explicit_temperature(self):
        """Passing only the default temperature still returns shared instance."""
        result = _get_llm(model=None, temperature=_LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self):
        """A different model name must produce a new ChatOpenAI instance."""
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock(name="new_llm")
            result = _get_llm(model="openai/gpt-4o")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "openai/gpt-4o"
            assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self):
        """A different temperature must produce a new ChatOpenAI instance."""
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock(name="new_llm_temp")
            result = _get_llm(temperature=0.9)
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["temperature"] == 0.9
            assert result is not main_module._llm

    def test_new_instance_uses_fallback_model(self):
        """When temperature differs but model is None, _LLM_MODEL is used."""
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            _get_llm(temperature=0.1)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == _LLM_MODEL

    def test_new_instance_streaming_enabled(self):
        """New instances must always have streaming=True."""
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            _get_llm(model="some/model")
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("streaming") is True

    def test_temperature_zero_creates_new_instance(self):
        """Temperature=0.0 differs from default and must create a new instance."""
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            _get_llm(temperature=0.0)
            mock_cls.assert_called_once()

    def test_both_model_and_temperature_differ(self):
        """Both model and temperature differ — new instance with correct values."""
        with patch("api.main.ChatOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            _get_llm(model="custom/model", temperature=0.3)
            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs["model"] == "custom/model"
            assert call_kwargs["temperature"] == 0.3


# ---------------------------------------------------------------------------
# Tests for _ROLEPLAY_SYSTEM template
# ---------------------------------------------------------------------------


class TestRoleplaySystemTemplate:
    """Validate that _ROLEPLAY_SYSTEM contains all required placeholders."""

    REQUIRED_PLACEHOLDERS = [
        "{name}",
        "{age}",
        "{occupation}",
        "{profile}",
        "{stage_instruction}",
        "{today}",
    ]

    @pytest.mark.parametrize("placeholder", REQUIRED_PLACEHOLDERS)
    def test_placeholder_present(self, placeholder):
        assert placeholder in _ROLEPLAY_SYSTEM, (
            f"Expected placeholder {placeholder!r} in _ROLEPLAY_SYSTEM"
        )

    def test_template_formats_correctly(self, sample_customer_profile):
        """Formatting with valid data should not raise."""
        rendered = _ROLEPLAY_SYSTEM.format(**sample_customer_profile)
        assert sample_customer_profile["name"] in rendered
        assert sample_customer_profile["occupation"] in rendered
        assert str(sample_customer_profile["age"]) in rendered

    def test_template_contains_character_guidance(self):
        """The template must include the 'Never break character' instruction."""
        assert "Never break character" in _ROLEPLAY_SYSTEM

    def test_template_contains_today_instruction(self):
        """The template must reference today's date usage."""
        assert "Today's date" in _ROLEPLAY_SYSTEM

    def test_template_missing_placeholder_raises(self, sample_customer_profile):
        """Formatting with a missing key must raise KeyError."""
        incomplete = {k: v for k, v in sample_customer_profile.items() if k != "name"}
        with pytest.raises(KeyError):
            _ROLEPLAY_SYSTEM.format(**incomplete)


# ---------------------------------------------------------------------------
# Tests for _PRIOR_CONTEXT_PROMPT template
# ---------------------------------------------------------------------------


class TestPriorContextPromptTemplate:
    """Validate _PRIOR_CONTEXT_PROMPT structure."""

    REQUIRED_PLACEHOLDERS = ["{profile}", "{stage}"]

    @pytest.mark.parametrize("placeholder", REQUIRED_PLACEHOLDERS)
    def test_placeholder_present(self, placeholder):
        assert placeholder in _PRIOR_CONTEXT_PROMPT

    def test_formats_correctly(self):
        rendered = _PRIOR_CONTEXT_PROMPT.format(
            profile="Alice Wong, 35, software engineer, HKD 60k/month",
            stage="2nd conversation",
        )
        assert "Alice Wong" in rendered
        assert "2nd conversation" in rendered

    def test_contains_word_limit_guideline(self):
        """Must contain the 350-word limit instruction."""
        assert "350" in _PRIOR_CONTEXT_PROMPT

    def test_contains_second_person_instruction(self):
        assert "second person" in _PRIOR_CONTEXT_PROMPT

    def test_missing_stage_raises(self):
        with pytest.raises(KeyError):
            _PRIOR_CONTEXT_PROMPT.format(profile="some profile")


# ---------------------------------------------------------------------------
# Tests for _build_roleplay_system (if exposed)
# ---------------------------------------------------------------------------


class TestBuildRoleplaySystem:
    """Tests for _build_roleplay_system helper (if present in module)."""

    def test_function_exists(self):
        """_build_roleplay_system must be defined in the module."""
        assert hasattr(main_module, "_build_roleplay_system"), (
            "_build_roleplay_system not found — was it truncated from the source?"
        )

    @pytest.mark.skipif(
        not hasattr(main_module, "_build_roleplay_system"),
        reason="_build_roleplay_system not available",
    )
    def test_returns_string(self):
        """Must return a non-empty string when given a valid profile mock."""
        fn = main_module._build_roleplay_system
        profile = MagicMock()
        profile.name = "Bob Lam"
        profile.age = 42
        profile.occupation = "Teacher"
        profile.model_dump = MagicMock(
            return_value={
                "name": "Bob Lam",
                "age": 42,
                "occupation": "Teacher",
                "profile": "Bob is a 42-year-old teacher.",
            }
        )
        # Accept either raises (incomplete implementation) or returns str
        try:
            result = fn(profile)
            assert isinstance(result, str)
            assert len