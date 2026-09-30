"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
- _get_llm: returns shared instance vs. new instance based on params
- _build_roleplay_system: prompt construction with CustomerProfile data
- _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template integrity
- FastAPI app endpoints (via TestClient): lifespan, CORS, static mounts
- Edge cases: missing env vars, boundary values for temperature, empty profiles

Mocks used:
- unittest.mock.patch for ChatOpenAI, get_vector_store, make_rag_tools,
  make_teacher_agent, make_assessor_agent, load_sessions, httpx.Client,
  httpx.AsyncClient
- MagicMock / AsyncMock for vector store, LLM, agent instances
- TestClient (httpx-based) from fastapi.testclient for HTTP-level tests

TODOs:
- TODO: Full streaming endpoint tests require real async generator behaviour
        from the agent — stub tests provided with pytest.mark.skip.
- TODO: /ingest endpoint not visible in the truncated source — stub provided.
- TODO: Session CRUD endpoints not fully visible — stubs provided.
- TODO: _build_roleplay_system is partially truncated; tests cover visible logic only.
"""

import importlib
import os
import sys
import types
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers — build a minimal fake module tree so importing api.main does not
# require the full project to be installed.
# ---------------------------------------------------------------------------

def _make_fake_customer_profile(**kwargs):
    """Return a simple namespace that mimics CustomerProfile fields."""
    defaults = dict(
        name="Alice Tan",
        age=35,
        occupation="Nurse",
        profile="Single mother of two, rents a flat in Kowloon. "
                "Monthly income HKD 28,000. Concerned about children's education.",
        stage="2nd conversation",
        stage_instruction="You vaguely remember chatting before.",
        today=str(date.today()),
    )
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Module-level patches applied before api.main is imported so that heavy
# dependencies (langchain, httpx SSL, vector store …) are never executed.
# ---------------------------------------------------------------------------

# We patch at the top level so every test in this file benefits.
_PATCH_TARGETS = {
    "httpx.Client": MagicMock(),
    "httpx.AsyncClient": MagicMock(),
}


@pytest.fixture(scope="session", autouse=True)
def _patch_heavy_imports():
    """
    Inject fake modules for project-internal imports that live outside
    the api package so that importing api.main succeeds in a unit-test
    environment without a fully installed project.
    """
    fake_vs_instance = MagicMock()
    fake_vs_instance.load.return_value = True
    fake_vs_instance.get_known_products.return_value = ["ProductA", "ProductB"]

    fake_vector_store_mod = types.ModuleType("core.vector_store")
    fake_vector_store_mod.get_vector_store = MagicMock(return_value=fake_vs_instance)

    fake_rag_tools_mod = types.ModuleType("api.rag_tools")
    fake_rag_tools_mod.make_rag_tools = MagicMock(return_value=[])

    fake_agent_mod = types.ModuleType("api.agent")
    fake_agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    fake_agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())

    fake_customer_profile = MagicMock()
    fake_session = MagicMock()

    fake_sessions_mod = types.ModuleType("api.sessions")
    fake_sessions_mod.CustomerProfile = fake_customer_profile
    fake_sessions_mod.Session = fake_session
    fake_sessions_mod.create_session = MagicMock(return_value=MagicMock())
    fake_sessions_mod.delete_session = MagicMock()
    fake_sessions_mod.generate_profile = AsyncMock(return_value=_make_fake_customer_profile())
    fake_sessions_mod.get_session = MagicMock(return_value=None)
    fake_sessions_mod.list_sessions = MagicMock(return_value=[])
    fake_sessions_mod.load_sessions = MagicMock()
    fake_sessions_mod.update_session_title = MagicMock()

    # Stub langchain modules
    fake_lc_messages = types.ModuleType("langchain_core.messages")
    fake_lc_messages.AIMessage = MagicMock()
    fake_lc_messages.HumanMessage = MagicMock()
    fake_lc_messages.SystemMessage = MagicMock()

    fake_lc_openai = types.ModuleType("langchain_openai")
    fake_lc_openai.ChatOpenAI = MagicMock()

    fake_core = types.ModuleType("core")
    fake_api = types.ModuleType("api")

    mods_to_inject = {
        "core": fake_core,
        "core.vector_store": fake_vector_store_mod,
        "api.rag_tools": fake_rag_tools_mod,
        "api.agent": fake_agent_mod,
        "api.sessions": fake_sessions_mod,
        "langchain_core": types.ModuleType("langchain_core"),
        "langchain_core.messages": fake_lc_messages,
        "langchain_openai": fake_lc_openai,
    }

    # Stash originals
    originals = {}
    for key, mod in mods_to_inject.items():
        originals[key] = sys.modules.get(key)
        sys.modules[key] = mod

    # Also make 'api' resolvable (it may already exist in editable installs)
    if "api" not in sys.modules:
        sys.modules["api"] = fake_api

    with (
        patch("httpx.Client", return_value=MagicMock()),
        patch("httpx.AsyncClient", return_value=MagicMock()),
    ):
        # Now import (or reload) api.main
        if "api.main" in sys.modules:
            import api.main as main_mod
            importlib.reload(main_mod)
        else:
            import api.main as main_mod  # noqa: F401

    yield

    # Restore
    for key, orig in originals.items():
        if orig is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = orig


# ---------------------------------------------------------------------------
# Import the module under test AFTER the session fixture has run.
# We use a lazy import helper so individual test functions can access the
# already-imported module safely.
# ---------------------------------------------------------------------------

def _main():
    import api.main as m
    return m


# ===========================================================================
# Tests for module-level constants
# ===========================================================================

class TestModuleConstants:
    def test_llm_temperature_is_float(self):
        m = _main()
        assert isinstance(m._LLM_TEMPERATURE, float)

    def test_llm_temperature_value(self):
        m = _main()
        assert 0.0 <= m._LLM_TEMPERATURE <= 2.0

    def test_show_tool_calls_is_bool(self):
        m = _main()
        assert isinstance(m.SHOW_TOOL_CALLS, bool)

    def test_base_url_not_empty(self):
        m = _main()
        assert m._BASE_URL  # must be truthy

    def test_llm_model_not_empty(self):
        m = _main()
        assert m._LLM_MODEL

    def test_roleplay_system_contains_placeholders(self):
        m = _main()
        for placeholder in ("{name}", "{age}", "{occupation}", "{profile}",
                            "{stage_instruction}", "{today}"):
            assert placeholder in m._ROLEPLAY_SYSTEM, (
                f"Missing placeholder {placeholder} in _ROLEPLAY_SYSTEM"
            )

    def test_prior_context_prompt_contains_placeholders(self):
        m = _main()
        for placeholder in ("{profile}", "{stage}"):
            assert placeholder in m._PRIOR_CONTEXT_PROMPT, (
                f"Missing placeholder {placeholder} in _PRIOR_CONTEXT_PROMPT"
            )

    def test_roleplay_system_is_non_empty_string(self):
        m = _main()
        assert isinstance(m._ROLEPLAY_SYSTEM, str)
        assert len(m._ROLEPLAY_SYSTEM) > 50

    def test_prior_context_prompt_is_non_empty_string(self):
        m = _main()
        assert isinstance(m._PRIOR_CONTEXT_PROMPT, str)
        assert len(m._PRIOR_CONTEXT_PROMPT) > 50


# ===========================================================================
# Tests for _get_llm
# ===========================================================================

class TestGetLlm:
    """Tests for the _get_llm factory function."""

    def test_returns_shared_instance_when_no_overrides(self):
        m = _main()
        result = m._get_llm()
        assert result is m._llm

    def test_returns_shared_instance_with_explicit_default_temperature(self):
        m = _main()
        result = m._get_llm(model=None, temperature=m._LLM_TEMPERATURE)
        assert result is m._llm

    def test_returns_new_instance_when_model_differs(self):
        m = _main()
        mock_chat = MagicMock()
        with patch("api.main.ChatOpenAI", return_value=mock_chat) as MockChat:
            result = m._get_llm(model="some-other-model")
            MockChat.assert_called_once()
            call_kwargs = MockChat.call_args.kwargs
            assert call_kwargs["model"] == "some-other-model"
            assert result is mock_chat

    def test_returns_new_instance_when_temperature_differs(self):
        m = _main()
        mock_chat = MagicMock()
        new_temp = m._LLM_TEMPERATURE + 0.1
        with patch("api.main.ChatOpenAI", return_value=mock_chat) as MockChat:
            result = m._get_llm(temperature=new_temp)
            MockChat.assert_called_once()
            call_kwargs = MockChat.call_args.kwargs
            assert call_kwargs["temperature"] == pytest.approx(new_temp)
            assert result is mock_chat

    def test_new_instance_uses_fallback_model_when_none_given_with_different_temp(self):
        m = _main()
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as MockChat:
            m._get_llm(model=None, temperature=0.9)
            call_kwargs = MockChat.call_args.kwargs
            assert call_kwargs["model"] == m._LLM_MODEL

    def test_new_instance_uses_explicit_model_and_temperature(self):
        m = _main()
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as MockChat:
            m._get_llm(model="custom-model", temperature=1.0)
            call_kwargs = MockChat.call_args.kwargs
            assert call_kwargs["model"] == "custom-model"
            assert call_kwargs["temperature"] == pytest.approx(1.0)

    def test_new_instance_has_streaming_enabled(self):
        m = _main()
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as MockChat:
            m._get_llm(temperature=0.1)
            call_kwargs = MockChat.call_args.kwargs
            assert call_kwargs.get("streaming") is True

    def test_new_instance_api_key_is_secret_str(self):
        m = _main()
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as MockChat:
            m._get_llm(temperature=0.1)
            call_kwargs = MockChat.call_args.kwargs
            assert isinstance(call_kwargs.get("api_key"), SecretStr)

    def test_temperature_boundary_zero(self):
        m = _main()
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as MockChat:
            m._get_llm(temperature=0.0)
            call_kwargs = MockChat.call_args.kwargs
            assert call_kwargs["temperature"] == pytest.approx(0.0)

    def test_temperature_boundary_two(self):
        m = _main()
        with patch("api.main.ChatOpenAI", return_value=MagicMock()) as MockChat:
            m._get_llm(temperature=2.0)
            call_kwargs = MockChat.call_args.kwargs
            assert call_kwargs["temperature"] == pytest.approx(2.0)


# ===========================================================================
# Tests for _build_roleplay_system (partially visible source)
# ===========================================================================

class TestBuildRoleplaySystem:
    """Tests for the _build_roleplay_system helper."""

    def _profile(self, **kwargs):
        return _make_fake_customer_profile(**kwargs)

    def test_name_appears_in_output(self):
        m = _main()
        if not hasattr(m, "_build_roleplay_system"):
            pytest.skip("_build_roleplay_system not yet visible in truncated source")
        p = self._profile(name="Bob Lee")
        result = m._build_roleplay_system(p)
        assert "Bob Lee" in result

    def test_age_appears_in_output(self):
        m = _main()
        if not hasattr(m, "_build_roleplay_system"):
            pytest.skip("_build_roleplay_system not yet visible in truncated source")
        p = self._profile(age=42)
        result = m._build_roleplay_system(p)
        assert "42" in result

    def test_occupation_appears_in_output(self):
        m = _main()
        if not hasattr(m, "_build_roleplay_system"):
            pytest.skip("_build_roleplay_system not yet visible in truncated source")
        p = self._profile(occupation="Teacher")
        result = m._build_roleplay_system(p)
        assert "Teacher" in result

    def test_profile_text_appears_in_output(self):
        m = _main()
        if not hasattr(m, "_build_roleplay_system"):
            pytest.skip("_build_roleplay_system not yet visible in truncated source")
        p = self._profile(profile="Loves hiking and has two dogs.")
        result = m._build_roleplay_system(p)
        assert "Loves hiking" in result

    def test_today_date_appears_in_output(self):
        m = _main()
        if not hasattr(m, "_build_roleplay_system"):
            pytest.skip("_build_roleplay_system not yet visible in truncated source")
        today_str = str(date.today())
        p = self._profile(today=today_str)
        result = m._build_roleplay_system(p)
        assert today_str in result

    def test_stage_instruction_appears_in_output(self):
        m = _main()
        if not hasattr(m, "_build_roleplay_system"):
            pytest.skip("_build_roleplay_system not yet visible in truncated source")
        p = self._profile(stage_instruction="You recall meeting before.")
        result = m._build_roleplay_system(p)
        assert "You recall meeting before." in result

    def test_returns_string(self):
        m =