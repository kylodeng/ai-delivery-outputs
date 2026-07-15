"""
Test module for api/main.py — Insurance Agent Training System FastAPI backend.

What is tested:
    - _get_llm(): returns shared instance vs. new instance based on params
    - _build_roleplay_system(): prompt construction from CustomerProfile
    - _ROLEPLAY_SYSTEM / _PRIOR_CONTEXT_PROMPT: template placeholders
    - FastAPI app endpoints (lifespan, CORS, static mount) via TestClient
    - Module-level constants loaded from environment variables
    - StreamingResponse generation for chat endpoints (mocked)
    - Session CRUD integration stubs

Mocks used:
    - langchain_openai.ChatOpenAI (patched at module level)
    - core.vector_store.get_vector_store
    - api.rag_tools.make_rag_tools
    - api.agent.make_teacher_agent, make_assessor_agent
    - api.sessions (create_session, delete_session, etc.)
    - httpx.Client / httpx.AsyncClient (SSL verify=False side-effects only)
    - fastapi.staticfiles.StaticFiles (to avoid filesystem dependency)

TODOs:
    - TODO: Full streaming endpoint tests need real async generator fixtures
    - TODO: /ingest endpoint tests require document upload fixtures
    - TODO: Integration tests for RAG tool calls need vector store populated
    - TODO: Tests for _PRIOR_CONTEXT_PROMPT generation via LLM need prompt capture
"""

import importlib
import os
import sys
import types
from datetime import date
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Helpers to build minimal fake modules so we can import api.main in isolation
# ---------------------------------------------------------------------------


def _make_fake_vector_store():
    vs = MagicMock()
    vs.load.return_value = True
    vs.get_known_products.return_value = ["ProductA", "ProductB"]
    return vs


def _make_fake_sessions_module():
    mod = types.ModuleType("api.sessions")

    class CustomerProfile(MagicMock):
        name: str = "Alice"
        age: int = 35
        occupation: str = "Engineer"
        profile: str = "A diligent engineer with two kids."

    mod.CustomerProfile = CustomerProfile
    mod.Session = MagicMock
    mod.create_session = MagicMock(return_value={"id": "sess-1"})
    mod.delete_session = MagicMock(return_value=True)
    mod.generate_profile = MagicMock(return_value=CustomerProfile())
    mod.get_session = MagicMock(return_value={"id": "sess-1"})
    mod.list_sessions = MagicMock(return_value=[])
    mod.load_sessions = MagicMock()
    mod.update_session_title = MagicMock()
    return mod


# ---------------------------------------------------------------------------
# Module-scope patches — applied before any import of api.main
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def patch_heavy_imports():
    """
    Patch all external heavy dependencies at the sys.modules level so that
    importing api.main does not require real OpenAI keys, vector stores, etc.
    """
    fake_vs = _make_fake_vector_store()
    fake_sessions = _make_fake_sessions_module()
    fake_chat_openai_instance = MagicMock()
    fake_chat_openai_instance.stream = MagicMock(return_value=iter([]))
    fake_chat_openai_cls = MagicMock(return_value=fake_chat_openai_instance)

    # Pre-populate sys.modules with fakes
    fake_core_vs_mod = types.ModuleType("core.vector_store")
    fake_core_vs_mod.get_vector_store = MagicMock(return_value=fake_vs)

    fake_rag_tools_mod = types.ModuleType("api.rag_tools")
    fake_rag_tools_mod.make_rag_tools = MagicMock(return_value=[MagicMock()])

    fake_agent_mod = types.ModuleType("api.agent")
    fake_agent_mod.make_teacher_agent = MagicMock(return_value=MagicMock())
    fake_agent_mod.make_assessor_agent = MagicMock(return_value=MagicMock())

    # Patch StaticFiles to avoid needing a real /data directory
    fake_static = MagicMock()
    fake_static_mod = types.ModuleType("fastapi.staticfiles")
    fake_static_mod.StaticFiles = MagicMock(return_value=fake_static)

    patches = {
        "core.vector_store": fake_core_vs_mod,
        "api.rag_tools": fake_rag_tools_mod,
        "api.agent": fake_agent_mod,
        "api.sessions": fake_sessions,
    }
    original = {}
    for key, val in patches.items():
        original[key] = sys.modules.get(key)
        sys.modules[key] = val

    with (
        patch("langchain_openai.ChatOpenAI", fake_chat_openai_cls),
        patch("fastapi.staticfiles.StaticFiles", MagicMock(return_value=fake_static)),
        patch.dict(os.environ, {
            "API_KEY": "test-api-key",
            "OPENAI_URL_BASE": "https://test.openrouter.ai/api/v1",
            "OPENAI_MODEL": "openai/gpt-test",
            "SHOW_TOOL_CALLS": "true",
        }),
    ):
        # Remove cached api.main so it reimports with our fakes
        sys.modules.pop("api.main", None)
        sys.modules.pop("api", None)

        # Ensure 'api' package exists as a module
        if "api" not in sys.modules:
            api_pkg = types.ModuleType("api")
            sys.modules["api"] = api_pkg

        yield fake_chat_openai_cls, fake_vs, fake_sessions

    # Restore
    for key, val in original.items():
        if val is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = val
    sys.modules.pop("api.main", None)


# ---------------------------------------------------------------------------
# Import api.main after patches are in place
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def main_module(patch_heavy_imports):
    """Import api.main with all heavy deps patched."""
    sys.modules.pop("api.main", None)

    # Ensure api package stub exists
    if "api" not in sys.modules or not isinstance(sys.modules["api"], types.ModuleType):
        api_pkg = types.ModuleType("api")
        sys.modules["api"] = api_pkg

    import importlib
    import api.main as main  # noqa: PLC0415
    return main


@pytest.fixture(scope="module")
def app(main_module):
    return main_module.app


@pytest.fixture(scope="module")
def client(app):
    """Synchronous TestClient — lifespan is handled inline."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Concrete CustomerProfile for testing prompt builders
# ---------------------------------------------------------------------------

class ConcreteCustomerProfile:
    """A plain Python stand-in for CustomerProfile dataclass/model."""
    def __init__(self, **kwargs):
        self.name = kwargs.get("name", "Alice Tan")
        self.age = kwargs.get("age", 35)
        self.occupation = kwargs.get("occupation", "Software Engineer")
        self.profile = kwargs.get("profile", (
            "Alice is a 35-year-old software engineer with two young children. "
            "She is the primary breadwinner with a HKD 60,000 monthly salary."
        ))
        self.financial_goals = kwargs.get("financial_goals", "Retirement savings and children's education")
        self.existing_coverage = kwargs.get("existing_coverage", "Basic MPF only")
        self.personality = kwargs.get("personality", "Analytical, cautious with money")
        self.stage = kwargs.get("stage", "1st conversation")
        self.stage_instruction = kwargs.get("stage_instruction", "")
        self.today = kwargs.get("today", str(date.today()))

    def model_dump(self):
        return self.__dict__

    def __str__(self):
        return (
            f"Name: {self.name}\n"
            f"Age: {self.age}\n"
            f"Occupation: {self.occupation}\n"
            f"Profile: {self.profile}"
        )


# ===========================================================================
# Tests for module-level constants
# ===========================================================================

class TestModuleConstants:
    def test_show_tool_calls_default_true(self, main_module):
        assert main_module.SHOW_TOOL_CALLS is True

    def test_llm_temperature_is_float(self, main_module):
        assert isinstance(main_module._LLM_TEMPERATURE, float)
        assert main_module._LLM_TEMPERATURE == 0.6

    def test_roleplay_system_template_has_placeholders(self, main_module):
        template = main_module._ROLEPLAY_SYSTEM
        for placeholder in ["{name}", "{age}", "{occupation}", "{profile}",
                             "{stage_instruction}", "{today}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_prior_context_prompt_has_placeholders(self, main_module):
        template = main_module._PRIOR_CONTEXT_PROMPT
        for placeholder in ["{profile}", "{stage}"]:
            assert placeholder in template, f"Missing placeholder: {placeholder}"

    def test_roleplay_system_instructs_stay_in_character(self, main_module):
        assert "Never break character" in main_module._ROLEPLAY_SYSTEM

    def test_prior_context_prompt_word_limit(self, main_module):
        assert "350 words" in main_module._PRIOR_CONTEXT_PROMPT

    def test_prior_context_prompt_no_product_names_instruction(self, main_module):
        assert "Do not invent insurance product names" in main_module._PRIOR_CONTEXT_PROMPT


# ===========================================================================
# Tests for _get_llm()
# ===========================================================================

class TestGetLlm:
    def test_returns_shared_instance_when_no_overrides(self, main_module):
        shared = main_module._llm
        result = main_module._get_llm()
        assert result is shared

    def test_returns_shared_instance_with_default_temperature(self, main_module):
        shared = main_module._llm
        result = main_module._get_llm(model=None, temperature=0.6)
        assert result is shared

    def test_returns_new_instance_when_model_provided(self, main_module, patch_heavy_imports):
        fake_cls, _, _ = patch_heavy_imports
        result = main_module._get_llm(model="openai/gpt-4o")
        # Should NOT be the shared instance
        assert result is not main_module._llm

    def test_returns_new_instance_when_temperature_differs(self, main_module, patch_heavy_imports):
        result = main_module._get_llm(temperature=0.9)
        assert result is not main_module._llm

    def test_returns_new_instance_when_both_differ(self, main_module, patch_heavy_imports):
        result = main_module._get_llm(model="openai/gpt-4o", temperature=0.1)
        assert result is not main_module._llm

    def test_get_llm_with_zero_temperature(self, main_module):
        result = main_module._get_llm(temperature=0.0)
        assert result is not main_module._llm

    def test_get_llm_with_max_temperature(self, main_module):
        result = main_module._get_llm(temperature=2.0)
        assert result is not main_module._llm


# ===========================================================================
# Tests for _build_roleplay_system()
# ===========================================================================

class TestBuildRoleplaySystem:
    """Tests for _build_roleplay_system() — the function is partially shown;
    we test the string template formatting that is visible."""

    @pytest.fixture
    def profile(self):
        return ConcreteCustomerProfile()

    def test_roleplay_system_formats_name(self, main_module):
        """Directly test the _ROLEPLAY_SYSTEM template formatting."""
        profile = ConcreteCustomerProfile(name="Bob Lee", age=42, occupation="Doctor")
        today = str(date.today())
        result = main_module._ROLEPLAY_SYSTEM.format(
            name=profile.name,
            age=profile.age,
            occupation=profile.occupation,
            profile=str(profile),
            stage_instruction="",
            today=today,
        )
        assert "Bob Lee" in result
        assert "42" in result
        assert "Doctor" in result
        assert today in result

    def test_roleplay_system_formats_stage_instruction(self, main_module):
        stage_instruction = "This is the 2nd meeting."
        result = main_module._ROLEPLAY_SYSTEM.format(
            name="Alice",
            age=30,
            occupation="Nurse",
            profile="Some profile text",
            stage_instruction=stage_instruction,
            today=str(date.today()),
        )
        assert stage_instruction in result

    def test_roleplay_system_empty_stage_instruction(self, main_module):
        result = main_module._ROLEPLAY_SYSTEM.format(
            name="Charlie",
            age=55,
            occupation="Retired",
            profile="Retired person profile",
            stage_instruction="",
            today="2025-01-01",
        )
        assert "Charlie" in result
        assert "2025-01-01" in result

    def test_roleplay_system_contains_date_usage_hint(self, main_module):
        result = main_module._ROLEPLAY_SYSTEM.format(
            name="Dan",
            age=28,
            occupation="Teacher",
            profile="Teacher profile",
            stage_instruction="",
            today="2025-06-15",
        )
        assert "2025-06-15" in result
        assert "calculate ages" in result

    def test_prior_context_prompt_formats_correctly(self, main_module):
        result = main_module._PRIOR_CONTEXT_PROMPT.format(
            profile="Customer profile text here",
            stage="2nd conversation",
        )
        assert "Customer profile text here" in result
        assert "2nd conversation" in result

    @pytest.mark.parametrize("name,age,occupation", [
        ("Alice Tan", 35, "Software Engineer"),
        ("Bob Wong", 60, "Retiree"),
        ("Clara Ho", 28, "Nurse"),
        ("David Lam", 45, "Business Owner"),
    ])
    def test_roleplay_template_with_various_profiles(self, main_module, name, age, occupation):
        result = main_module._ROLEPLAY_SYSTEM.format(
            name=name,
            age=age,
            occupation=occupation,
            profile=f"{name} profile details",
            stage_instruction="",
            today=str(date.today()),
        )
        assert name in result
        assert str(age) in result
        assert occupation in result


# ===========================================================================
# Tests for FastAPI app configuration
# ===========================================================================

class TestAppConfiguration:
    def test_app_title(self, app):
        assert app.title == "Insurance Agent Trainer"

    def test_cors_middleware_present(self, app):
        middleware_types = [type(m).__name__ for m in app.user_middleware]
        # CORSMiddleware should appear
        middleware_class_names = [str(m) for m in app.user_middleware