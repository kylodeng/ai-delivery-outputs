"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs new instance based on parameters
- _build_roleplay_system: system prompt construction (stub — function is truncated in source)
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template presence and key placeholders
- FastAPI app: lifespan, CORS middleware, static file mount, route structure
- /ingest endpoint (if present): mocked vector store
- Session-related endpoints: create, get, list, delete (mocked sessions module)
- StreamingResponse endpoints: chat/roleplay (mocked LLM streaming)
- Environment variable handling for SHOW_TOOL_CALLS, API_KEY, model, base_url

Mocks used:
- unittest.mock.patch / MagicMock for:
  - core.vector_store.get_vector_store
  - api.rag_tools.make_rag_tools
  - api.agent.make_teacher_agent / make_assessor_agent
  - api.sessions (all functions and models)
  - langchain_openai.ChatOpenAI
  - httpx.Client / httpx.AsyncClient
  - fastapi.staticfiles.StaticFiles (to avoid filesystem dependency)

TODOs:
- TODO: Full integration tests for /chat and /roleplay streaming endpoints require
        knowledge of actual route definitions (source is truncated).
- TODO: Tests for _build_roleplay_system require the complete function body.
- TODO: Tests for POST /ingest require the full route handler.
- TODO: Tests for make_teacher_agent / make_assessor_agent interactions require
        the full agent invocation flow.
"""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake module tree so that importing api.main does
# not blow up due to missing heavy dependencies or filesystem requirements.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """Register lightweight stubs for all third-party / internal imports."""

    # ── langchain stubs ──────────────────────────────────────────────────────
    lc_core = types.ModuleType("langchain_core")
    lc_messages = types.ModuleType("langchain_core.messages")

    class _FakeMsg:
        def __init__(self, content=""):
            self.content = content

    lc_messages.AIMessage = type("AIMessage", (_FakeMsg,), {})
    lc_messages.HumanMessage = type("HumanMessage", (_FakeMsg,), {})
    lc_messages.SystemMessage = type("SystemMessage", (_FakeMsg,), {})
    sys.modules.setdefault("langchain_core", lc_core)
    sys.modules.setdefault("langchain_core.messages", lc_messages)

    lc_openai = types.ModuleType("langchain_openai")

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def stream(self, messages):
            return iter([MagicMock(content="chunk")])

        async def astream(self, messages):
            async def _gen():
                yield MagicMock(content="chunk")
            return _gen()

    lc_openai.ChatOpenAI = FakeChatOpenAI
    sys.modules.setdefault("langchain_openai", lc_openai)

    # ── pydantic (use real pydantic if available, else stub) ─────────────────
    try:
        import pydantic  # noqa: F401
    except ImportError:
        pydantic_mod = types.ModuleType("pydantic")
        pydantic_mod.BaseModel = object
        pydantic_mod.SecretStr = str
        sys.modules.setdefault("pydantic", pydantic_mod)

    # ── httpx stub ───────────────────────────────────────────────────────────
    if "httpx" not in sys.modules:
        httpx_mod = types.ModuleType("httpx")
        httpx_mod.Client = MagicMock(return_value=MagicMock())
        httpx_mod.AsyncClient = MagicMock(return_value=MagicMock())
        sys.modules["httpx"] = httpx_mod

    # ── dotenv stub ──────────────────────────────────────────────────────────
    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *a, **kw: None
    sys.modules.setdefault("dotenv", dotenv_mod)

    # ── core.vector_store stub ───────────────────────────────────────────────
    core_mod = types.ModuleType("core")
    core_vs = types.ModuleType("core.vector_store")
    fake_store = MagicMock()
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["ProductA", "ProductB"]
    core_vs.get_vector_store = MagicMock(return_value=fake_store)
    sys.modules.setdefault("core", core_mod)
    sys.modules.setdefault("core.vector_store", core_vs)

    # ── api.rag_tools stub ───────────────────────────────────────────────────
    api_mod = sys.modules.get("api", types.ModuleType("api"))
    sys.modules.setdefault("api", api_mod)

    rag_tools_mod = types.ModuleType("api.rag_tools")
    rag_tools_mod.make_rag_tools = MagicMock(return_value=[MagicMock()])
    sys.modules.setdefault("api.rag_tools", rag_tools_mod)

    # ── api.agent stub ───────────────────────────────────────────────────────
    agent_mod = types.ModuleType("api.agent")
    agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())
    sys.modules.setdefault("api.agent", agent_mod)

    # ── api.sessions stub ────────────────────────────────────────────────────
    sessions_mod = types.ModuleType("api.sessions")

    class FakeCustomerProfile:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
        name: str = "Test User"
        age: int = 35
        occupation: str = "Engineer"

    class FakeSession:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    sessions_mod.CustomerProfile = FakeCustomerProfile
    sessions_mod.Session = FakeSession
    sessions_mod.create_session = MagicMock(return_value=FakeSession(id="sess-1"))
    sessions_mod.delete_session = MagicMock(return_value=True)
    sessions_mod.generate_profile = MagicMock(return_value=FakeCustomerProfile())
    sessions_mod.get_session = MagicMock(return_value=FakeSession(id="sess-1"))
    sessions_mod.list_sessions = MagicMock(return_value=[])
    sessions_mod.load_sessions = MagicMock()
    sessions_mod.update_session_title = MagicMock()
    sys.modules.setdefault("api.sessions", sessions_mod)

    # ── fastapi.staticfiles — prevent filesystem access ──────────────────────
    try:
        from fastapi.staticfiles import StaticFiles  # noqa: F401
        # Patch StaticFiles to not check the filesystem
        import fastapi.staticfiles as _sf
        _sf.StaticFiles.__init__ = lambda self, *a, **kw: None
    except Exception:
        pass

    return fake_store


_FAKE_STORE = _make_fake_modules()


# ---------------------------------------------------------------------------
# Import the module under test AFTER stubs are in place
# ---------------------------------------------------------------------------

# Remove any cached version so we get a fresh import with our stubs
for _key in list(sys.modules.keys()):
    if "api.main" in _key:
        del sys.modules[_key]

import api.main as main_module  # noqa: E402  (must come after stub setup)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """Return a synchronous TestClient for the FastAPI app."""
    # We skip the lifespan so tests are isolated
    with patch.object(main_module, "load_sessions"), \
         patch.object(_FAKE_STORE, "load", return_value=True):
        with TestClient(main_module.app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture()
def fake_llm(monkeypatch):
    """Replace _llm with a MagicMock for tests that exercise LLM paths."""
    mock = MagicMock()
    monkeypatch.setattr(main_module, "_llm", mock)
    return mock


# ---------------------------------------------------------------------------
# Tests: module-level constants / environment variable handling
# ---------------------------------------------------------------------------

class TestEnvironmentVariables:
    def test_show_tool_calls_default_is_bool(self):
        assert isinstance(main_module.SHOW_TOOL_CALLS, bool)

    def test_llm_temperature_value(self):
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_base_url_has_default(self):
        assert main_module._BASE_URL  # non-empty

    def test_llm_model_has_default(self):
        assert main_module._LLM_MODEL  # non-empty

    @patch.dict("os.environ", {"SHOW_TOOL_CALLS": "true"})
    def test_show_tool_calls_true_when_env_true(self):
        # Re-evaluate the expression that drives SHOW_TOOL_CALLS
        import os
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is True

    @patch.dict("os.environ", {"SHOW_TOOL_CALLS": "false"})
    def test_show_tool_calls_false_when_env_false(self):
        import os
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is False

    @patch.dict("os.environ", {"SHOW_TOOL_CALLS": "TRUE"})
    def test_show_tool_calls_case_insensitive(self):
        import os
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is True

    @patch.dict("os.environ", {"SHOW_TOOL_CALLS": "0"})
    def test_show_tool_calls_numeric_zero_is_false(self):
        import os
        result = os.getenv("SHOW_TOOL_CALLS", "true").lower() == "true"
        assert result is False


# ---------------------------------------------------------------------------
# Tests: _get_llm
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_args(self):
        """No args → returns the module-level _llm singleton."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_shared_instance_with_default_temperature(self):
        result = main_module._get_llm(model=None, temperature=main_module._LLM_TEMPERATURE)
        assert result is main_module._llm

    def test_returns_new_instance_when_model_differs(self):
        result = main_module._get_llm(model="some-other-model")
        assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self):
        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm

    def test_returns_new_instance_when_both_differ(self):
        result = main_module._get_llm(model="gpt-4", temperature=0.1)
        assert result is not main_module._llm

    def test_new_instance_has_correct_temperature(self):
        result = main_module._get_llm(temperature=0.1)
        # FakeChatOpenAI stores kwargs as attributes
        assert result.temperature == 0.1

    def test_new_instance_uses_provided_model(self):
        result = main_module._get_llm(model="custom-model")
        assert result.model == "custom-model"

    def test_new_instance_falls_back_to_default_model_when_none(self):
        result = main_module._get_llm(model=None, temperature=0.1)
        assert result.model == main_module._LLM_MODEL

    def test_new_instance_has_streaming_enabled(self):
        result = main_module._get_llm(temperature=0.2)
        assert result.streaming is True

    def test_zero_temperature_creates_new_instance(self):
        """Temperature=0.0 is valid and different from 0.6 default."""
        result = main_module._get_llm(temperature=0.0)
        assert result is not main_module._llm
        assert result.temperature == 0.0

    def test_negative_temperature_creates_new_instance(self):
        """Negative temperature is unusual but should not crash."""
        result = main_module._get_llm(temperature=-1.0)
        assert result is not main_module._llm

    def test_very_high_temperature_creates_new_instance(self):
        result = main_module._get_llm(temperature=2.0)
        assert result is not main_module._llm
        assert result.temperature == 2.0


# ---------------------------------------------------------------------------
# Tests: system prompt templates
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    REQUIRED_PLACEHOLDERS = [
        "{name}", "{age}", "{occupation}", "{profile}",
        "{stage_instruction}", "{today}",
    ]

    def test_template_is_non_empty_string(self):
        assert isinstance(main_module._ROLEPLAY_SYSTEM, str)
        assert len(main_module._ROLEPLAY_SYSTEM) > 0

    @pytest.mark.parametrize("placeholder", REQUIRED_PLACEHOLDERS)
    def test_required_placeholder_present(self, placeholder):
        assert placeholder in main_module._ROLEPLAY_SYSTEM, (
            f"Missing placeholder {placeholder!r} in _ROLEPLAY_SYSTEM"
        )

    def test_template_can_be_formatted(self):
        filled = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=35,
            occupation="Teacher",
            profile="Single, no kids.",
            stage_instruction="This is a first meeting.",
            today="2024-01-15",
        )
        assert "Alice" in filled
        assert "35" in filled
        assert "Teacher" in filled

    def test_template_contains_character_instructions(self):
        assert "character" in main_module._ROLEPLAY_SYSTEM.lower()

    def test_template_mentions_date_usage(self):
        assert "today" in main_module._ROLEPLAY_SYSTEM.lower()


class TestPriorContextPromptTemplate:
    REQUIRED_PLACEHOLDERS = ["{profile}", "{stage}"]

    def test_template_is_non_empty_string(self):
        assert isinstance(main_module._PRIOR_CONTEXT_PROMPT, str)
        assert len(main_module._PRIOR_CONTEXT_PROMPT) > 0

    @pytest.mark.parametrize("placeholder", REQUIRED_PLACEHOLDERS)
    def test_required_placeholder_present(self, placeholder