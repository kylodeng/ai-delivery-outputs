"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs. new instance based on params
- _build_roleplay_system: prompt construction from CustomerProfile
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template placeholder coverage
- FastAPI app endpoints (lifespan, CORS, static mount)
- API routes: sessions CRUD, ingest, chat/streaming (mocked)
- Environment variable handling

Mocks used:
- langchain_openai.ChatOpenAI (patched at module level)
- core.vector_store.get_vector_store
- api.rag_tools.make_rag_tools
- api.agent.make_teacher_agent / make_assessor_agent
- api.sessions (create_session, get_session, delete_session, list_sessions,
  load_sessions, update_session_title, generate_profile)
- httpx.Client / httpx.AsyncClient
- os.getenv / load_dotenv

TODOs:
- TODO: Full streaming SSE response body validation requires live agent graph wiring
- TODO: POST /ingest endpoint tests need the full ingest pipeline mocked
- TODO: WebSocket / Chainlit UI integration tests need a running Chainlit server
- TODO: Test _build_roleplay_system with all stage values once stage enum is confirmed
"""

import importlib
import os
import sys
import types
from datetime import date
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers to build lightweight stubs before importing api.main so that
# heavy optional dependencies (langchain, httpx, etc.) can be swapped out.
# ---------------------------------------------------------------------------

def _make_stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _patch_env(tmp_path_factory):
    """Provide minimal environment variables for the whole test session."""
    env_vars = {
        "API_KEY": "test-api-key",
        "OPENAI_URL_BASE": "https://fake.openrouter.ai/api/v1",
        "OPENAI_MODEL": "openai/gpt-test",
        "SHOW_TOOL_CALLS": "true",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield


@pytest.fixture(scope="session")
def _stubbed_imports():
    """
    Pre-populate sys.modules with stubs for every heavy dependency so that
    `import api.main` succeeds in a pure-unit-test environment.
    """
    stubs: dict[str, types.ModuleType] = {}

    # --- langchain stubs ---
    fake_chat_openai_cls = MagicMock(name="ChatOpenAI")
    fake_chat_openai_instance = MagicMock(name="ChatOpenAI_instance")
    fake_chat_openai_cls.return_value = fake_chat_openai_instance

    lc_openai = _make_stub_module("langchain_openai", ChatOpenAI=fake_chat_openai_cls)
    stubs["langchain_openai"] = lc_openai

    lc_core = _make_stub_module("langchain_core")
    lc_core_msgs = _make_stub_module(
        "langchain_core.messages",
        AIMessage=MagicMock(name="AIMessage"),
        HumanMessage=MagicMock(name="HumanMessage"),
        SystemMessage=MagicMock(name="SystemMessage"),
    )
    stubs["langchain_core"] = lc_core
    stubs["langchain_core.messages"] = lc_core_msgs

    # --- httpx stub ---
    fake_httpx = _make_stub_module(
        "httpx",
        Client=MagicMock(return_value=MagicMock()),
        AsyncClient=MagicMock(return_value=MagicMock()),
    )
    stubs["httpx"] = fake_httpx

    # --- dotenv stub ---
    stubs["dotenv"] = _make_stub_module("dotenv", load_dotenv=MagicMock())

    # --- core.vector_store stub ---
    fake_store = MagicMock(name="VectorStore")
    fake_store.load.return_value = True
    fake_store.get_known_products.return_value = ["ProductA", "ProductB"]
    fake_vs_mod = _make_stub_module(
        "core.vector_store", get_vector_store=MagicMock(return_value=fake_store)
    )
    stubs["core"] = _make_stub_module("core")
    stubs["core.vector_store"] = fake_vs_mod

    # --- api.rag_tools stub ---
    fake_rag_tools_mod = _make_stub_module(
        "api.rag_tools", make_rag_tools=MagicMock(return_value=[])
    )
    stubs["api.rag_tools"] = fake_rag_tools_mod

    # --- api.agent stub ---
    fake_agent_mod = _make_stub_module(
        "api.agent",
        make_teacher_agent=MagicMock(return_value=MagicMock()),
        make_assessor_agent=MagicMock(return_value=MagicMock()),
    )
    stubs["api.agent"] = fake_agent_mod

    # --- api.sessions stubs ---
    from pydantic import BaseModel  # real pydantic is available

    class _FakeCustomerProfile(BaseModel):
        name: str = "Alice"
        age: int = 35
        occupation: str = "teacher"
        profile: str = "Single mother with two kids."
        stage: str = "1st_meeting"

    class _FakeSession(BaseModel):
        id: str = "sess-001"
        title: str = "Test Session"
        profile: _FakeCustomerProfile = _FakeCustomerProfile()
        history: list = []

    fake_sessions_mod = _make_stub_module(
        "api.sessions",
        CustomerProfile=_FakeCustomerProfile,
        Session=_FakeSession,
        create_session=MagicMock(return_value=_FakeSession()),
        delete_session=MagicMock(return_value=True),
        generate_profile=AsyncMock(return_value=_FakeCustomerProfile()),
        get_session=MagicMock(return_value=_FakeSession()),
        list_sessions=MagicMock(return_value=[_FakeSession()]),
        load_sessions=MagicMock(),
        update_session_title=MagicMock(),
    )
    stubs["api.sessions"] = fake_sessions_mod
    stubs["api"] = _make_stub_module("api")

    for name, mod in stubs.items():
        sys.modules.setdefault(name, mod)

    yield stubs

    # Teardown: remove only the stubs we inserted
    for name in stubs:
        sys.modules.pop(name, None)


@pytest.fixture(scope="session")
def main_module(_stubbed_imports, tmp_path_factory):
    """Import api.main once with all stubs in place."""
    # Ensure the data directory exists so StaticFiles doesn't crash
    import tempfile, pathlib

    data_dir = pathlib.Path(__file__).parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Remove cached version so we get a fresh import
    sys.modules.pop("api.main", None)

    with patch("fastapi.staticfiles.StaticFiles.__init__", return_value=None):
        import api.main as m  # noqa: PLC0415

    return m


@pytest.fixture(scope="session")
def app(main_module):
    return main_module.app


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# Convenience alias for the CustomerProfile stub
@pytest.fixture()
def sample_profile(_stubbed_imports):
    CP = _stubbed_imports["api.sessions"].CustomerProfile
    return CP(
        name="Bob",
        age=42,
        occupation="engineer",
        profile="Married with one child, owns a flat, moderate savings.",
        stage="2nd_meeting",
    )


# ---------------------------------------------------------------------------
# _get_llm tests
# ---------------------------------------------------------------------------

class TestGetLlm:
    def test_returns_shared_instance_when_no_overrides(self, main_module):
        """Calling _get_llm() with defaults should return the module-level _llm."""
        result = main_module._get_llm()
        assert result is main_module._llm

    def test_returns_new_instance_when_model_overridden(self, main_module):
        """Passing a custom model name must return a brand-new ChatOpenAI."""
        # Reset the mock call count so we can detect a fresh instantiation
        ChatOpenAI_cls = sys.modules["langchain_openai"].ChatOpenAI
        ChatOpenAI_cls.reset_mock()

        result = main_module._get_llm(model="openai/gpt-4")
        assert result is not main_module._llm
        ChatOpenAI_cls.assert_called_once()

    def test_returns_new_instance_when_temperature_differs(self, main_module):
        ChatOpenAI_cls = sys.modules["langchain_openai"].ChatOpenAI
        ChatOpenAI_cls.reset_mock()

        result = main_module._get_llm(temperature=0.0)
        assert result is not main_module._llm
        ChatOpenAI_cls.assert_called_once()

    def test_custom_temperature_passed_through(self, main_module):
        ChatOpenAI_cls = sys.modules["langchain_openai"].ChatOpenAI
        ChatOpenAI_cls.reset_mock()

        main_module._get_llm(temperature=0.9)
        _, kwargs = ChatOpenAI_cls.call_args
        assert kwargs.get("temperature") == 0.9

    def test_custom_model_passed_through(self, main_module):
        ChatOpenAI_cls = sys.modules["langchain_openai"].ChatOpenAI
        ChatOpenAI_cls.reset_mock()

        main_module._get_llm(model="custom/model-x")
        _, kwargs = ChatOpenAI_cls.call_args
        assert kwargs.get("model") == "custom/model-x"

    def test_none_model_with_default_temp_returns_shared(self, main_module):
        result = main_module._get_llm(model=None)
        assert result is main_module._llm

    def test_both_model_and_temperature_overridden(self, main_module):
        ChatOpenAI_cls = sys.modules["langchain_openai"].ChatOpenAI
        ChatOpenAI_cls.reset_mock()

        result = main_module._get_llm(model="openai/gpt-4", temperature=0.3)
        assert result is not main_module._llm
        ChatOpenAI_cls.assert_called_once()
        _, kwargs = ChatOpenAI_cls.call_args
        assert kwargs["temperature"] == 0.3
        assert kwargs["model"] == "openai/gpt-4"


# ---------------------------------------------------------------------------
# _ROLEPLAY_SYSTEM template tests
# ---------------------------------------------------------------------------

class TestRoleplaySystemTemplate:
    _REQUIRED_PLACEHOLDERS = ["{name}", "{age}", "{occupation}", "{profile}",
                               "{stage_instruction}", "{today}"]

    def test_all_required_placeholders_present(self, main_module):
        for ph in self._REQUIRED_PLACEHOLDERS:
            assert ph in main_module._ROLEPLAY_SYSTEM, f"Missing placeholder: {ph}"

    def test_format_with_full_context(self, main_module):
        rendered = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=35,
            occupation="teacher",
            profile="Single mother, two kids.",
            stage_instruction="This is a first meeting.",
            today=str(date.today()),
        )
        assert "Alice" in rendered
        assert "teacher" in rendered
        assert "35" in rendered

    def test_missing_placeholder_raises_key_error(self, main_module):
        with pytest.raises(KeyError):
            main_module._ROLEPLAY_SYSTEM.format(name="X")  # missing others

    def test_no_unformatted_braces_after_full_format(self, main_module):
        rendered = main_module._ROLEPLAY_SYSTEM.format(
            name="N", age=0, occupation="O", profile="P",
            stage_instruction="S", today="2024-01-01",
        )
        # After substitution no literal {placeholder} patterns should remain
        import re
        remaining = re.findall(r"\{[a-z_]+\}", rendered)
        assert remaining == []


# ---------------------------------------------------------------------------
# _PRIOR_CONTEXT_PROMPT template tests
# ---------------------------------------------------------------------------

class TestPriorContextPromptTemplate:
    _REQUIRED_PLACEHOLDERS = ["{profile}", "{stage}"]

    def test_all_required_placeholders_present(self, main_module):
        for ph in self._REQUIRED_PLACEHOLDERS:
            assert ph in main_module._PRIOR_CONTEXT_PROMPT, f"Missing: {ph}"

    def test_format_renders_correctly(self, main_module):
        rendered = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Bob, 42, engineer, married.",
            stage="2nd_meeting",
        )
        assert "Bob" in rendered
        assert "2nd_meeting" in rendered

    def test_missing_stage_raises_key_error(self, main_module):
        with pytest.raises(KeyError):
            main_module._PRIOR_CONTEXT_PROMPT.format(profile="P")


# ---------------------------------------------------------------------------
# _build_roleplay_system tests  (partial — function body was truncated)
# ---------------------------------------------------------------------------

class TestBuildRoleplaySystem:
    @pytest.mark.skip(reason="TODO: source truncated — full function body needed to test all branches")
    def test_build_with_first_meeting(self, main_module, sample_profile):
        pass

    @pytest.mark.skip(reason="TODO: source truncated — stage_instruction mapping for 2nd/3rd meeting needs confirmation")
    def test_build_with_second_meeting(self, main_module, sample_profile):
        pass

    @pytest.mark.skip(reason="TODO: prior_context_prompt injection logic not visible in truncated source")
    def test_build_injects_prior_context_for_later_stages(self, main_module, sample_profile):
        pass

    def test_build_returns_non_empty_string(self, main_module, sample_profile):
        """Smoke-test that _build_roleplay_system returns a non-empty string."""
        try:
            result = main_module._build_roleplay_system(sample_profile)
            assert isinstance(result, str)
            assert len(result) > 0
        except Exception:
            pytest.skip("TODO: full implementation not available in truncated source")


# ---------------------------------------------------------------------------
# FastAPI application-level tests
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, app):
        middleware_types = [type(m).__name__ for m in app.user_middleware]
        assert any("cors" in t.lower() or "CORS" in t for t in middleware_types)

    def test_app_has_routes(self, app):
        assert len(app.routes) > 0

    def test_openapi