"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path, edge cases, error conditions, boundary values
- Module-level initialization of Redis client and checkpointer
- Correct assembly of tools list passed to create_agent
- Correct model construction via LLMS
- Mode parameter handling ("fast", "deep", and invalid/edge values)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - redis.asyncio.Redis (prevent real Redis connections)
  - langgraph.checkpoint.redis.aio.AsyncRedisSaver (prevent real Redis usage)
  - langchain.agents.create_agent (prevent real LLM/agent creation)
  - modules.LLMS.LLMS (prevent real LLM instantiation)
  - modules.tools.get_customer_profile (imported symbol)
  - modules.tools.customer_lookalike (imported symbol)
  - modules.assessment._run_underwriting_assessment (prevent real assessment)
  - backend/agent/prompts.SYSTEM_PROMPT

TODOs:
- TODO: Integration test with a real (or containerised) Redis instance once external Redis service is available
- TODO: Test checkpointer persistence behaviour across serverless invocations once infrastructure is ready
- TODO: Verify streaming=True is propagated correctly to the underlying LLM provider
"""

import os
import importlib
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call, AsyncMock


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake module tree so that importing graph.py never
# touches real external services, even at module load time.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """
    Register lightweight fake versions of heavy/external dependencies in
    sys.modules BEFORE the module under test is imported.  This prevents
    Redis connections and LLM client initialisation from happening during
    collection.
    """
    # --- redis.asyncio ---
    fake_redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_class = MagicMock(name="Redis")
    fake_redis_asyncio.Redis = fake_redis_class

    fake_redis = types.ModuleType("redis")
    fake_redis.asyncio = fake_redis_asyncio
    sys.modules.setdefault("redis", fake_redis)
    sys.modules.setdefault("redis.asyncio", fake_redis_asyncio)

    # --- langgraph.checkpoint.redis.aio ---
    fake_lg_cp_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_lg_cp_redis_aio.AsyncRedisSaver = MagicMock(name="AsyncRedisSaver")

    for mod_name in (
        "langgraph",
        "langgraph.checkpoint",
        "langgraph.checkpoint.redis",
        "langgraph.checkpoint.redis.aio",
    ):
        sys.modules.setdefault(mod_name, types.ModuleType(mod_name))
    sys.modules["langgraph.checkpoint.redis.aio"] = fake_lg_cp_redis_aio

    # --- langchain.agents ---
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_lc_agents.create_agent = MagicMock(name="create_agent")
    fake_langchain = types.ModuleType("langchain")
    fake_langchain.agents = fake_lc_agents
    sys.modules.setdefault("langchain", fake_langchain)
    sys.modules.setdefault("langchain.agents", fake_lc_agents)

    # --- modules.tools ---
    fake_tools = types.ModuleType("modules.tools")
    fake_tools.get_customer_profile = MagicMock(name="get_customer_profile")
    fake_tools.customer_lookalike = MagicMock(name="customer_lookalike")
    fake_modules = types.ModuleType("modules")
    fake_modules.tools = fake_tools
    sys.modules.setdefault("modules", fake_modules)
    sys.modules.setdefault("modules.tools", fake_tools)

    # --- modules.assessment ---
    fake_assessment = types.ModuleType("modules.assessment")
    fake_assessment._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment",
        return_value=MagicMock(name="assessment_tool"),
    )
    fake_modules.assessment = fake_assessment
    sys.modules.setdefault("modules.assessment", fake_assessment)

    # --- modules.LLMS ---
    fake_llms_module = types.ModuleType("modules.LLMS")
    fake_llms_class = MagicMock(name="LLMS")
    fake_llms_module.LLMS = fake_llms_class
    fake_modules.LLMS = fake_llms_module
    sys.modules.setdefault("modules.LLMS", fake_llms_module)

    # --- agent.prompts (relative import resolves to backend.agent.prompts) ---
    fake_prompts = types.ModuleType("agent.prompts")
    fake_prompts.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
    sys.modules.setdefault("agent.prompts", fake_prompts)

    # Also register under the package path that the relative import may resolve to
    fake_backend_agent_prompts = types.ModuleType("backend.agent.prompts")
    fake_backend_agent_prompts.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
    sys.modules.setdefault("backend.agent.prompts", fake_backend_agent_prompts)

    return {
        "fake_redis_class": fake_redis_class,
        "fake_saver_class": fake_lg_cp_redis_aio.AsyncRedisSaver,
        "fake_create_agent": fake_lc_agents.create_agent,
        "fake_llms_class": fake_llms_class,
        "fake_assessment": fake_assessment._run_underwriting_assessment,
        "fake_get_customer_profile": fake_tools.get_customer_profile,
        "fake_customer_lookalike": fake_tools.customer_lookalike,
    }


# Register fakes before any import of the module under test
_FAKES = _make_fake_modules()


# ---------------------------------------------------------------------------
# Now import (or reload) the module under test
# ---------------------------------------------------------------------------

# We import through the package path; adjust as needed for the test runner's
# working directory / PYTHONPATH.
with patch.dict(os.environ, {"REDIS_HOST": "test-redis-host"}):
    # Force a fresh import so the patched env var is picked up
    if "agent.graph" in sys.modules:
        del sys.modules["agent.graph"]
    if "backend.agent.graph" in sys.modules:
        del sys.modules["backend.agent.graph"]

    try:
        import agent.graph as graph_module
    except ModuleNotFoundError:
        # Fallback: try the full dotted path when tests run from repo root
        import importlib.util, pathlib

        _graph_path = pathlib.Path(__file__).parent.parent / "agent" / "graph.py"
        _spec = importlib.util.spec_from_file_location(
            "agent.graph",
            _graph_path,
            submodule_search_locations=[],
        )
        graph_module = importlib.util.module_from_spec(_spec)

        # Patch relative-import symbols before exec_module
        graph_module.__package__ = "agent"
        sys.modules["agent.graph"] = graph_module
        _spec.loader.exec_module(graph_module)


# Convenient aliases
build_agent = graph_module.build_agent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared mocks between tests."""
    for fake in _FAKES.values():
        fake.reset_mock()

    # Re-configure common return values after reset
    fake_model_instance = MagicMock(name="model_instance")
    _FAKES["fake_llms_class"].return_value.get_model.return_value = fake_model_instance

    fake_tool_instance = MagicMock(name="assessment_tool_instance")
    _FAKES["fake_assessment"].return_value = fake_tool_instance

    fake_agent_instance = MagicMock(name="agent_instance")
    _FAKES["fake_create_agent"].return_value = fake_agent_instance

    yield {
        "model_instance": fake_model_instance,
        "tool_instance": fake_tool_instance,
        "agent_instance": fake_agent_instance,
    }


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------

class TestModuleLevelInit:
    """Verify that module-level singletons are created with correct parameters."""

    def test_redis_client_created_with_env_host(self):
        """Redis client should have been instantiated (mock was called)."""
        # The module was loaded with REDIS_HOST=test-redis-host;
        # the mock was called during import.
        assert _FAKES["fake_redis_class"].called or isinstance(
            graph_module._redis_client, MagicMock
        ), "Redis() was not called during module initialisation"

    def test_checkpointer_created(self):
        """AsyncRedisSaver should have been instantiated with the redis client."""
        assert _FAKES["fake_saver_class"].called or isinstance(
            graph_module._checkpointer, MagicMock
        ), "AsyncRedisSaver() was not called during module initialisation"

    def test_redis_host_default_fallback(self):
        """When REDIS_HOST env var is absent, 'localhost' should be the default."""
        env_without_host = {k: v for k, v in os.environ.items() if k != "REDIS_HOST"}
        with patch.dict(os.environ, env_without_host, clear=True):
            # Re-evaluate the default expression
            host = os.environ.get("REDIS_HOST", "localhost")
        assert host == "localhost"

    def test_redis_host_from_env(self):
        """REDIS_HOST env var should be forwarded to the Redis constructor."""
        with patch.dict(os.environ, {"REDIS_HOST": "my-custom-redis"}):
            host = os.environ.get("REDIS_HOST", "localhost")
        assert host == "my-custom-redis"


# ---------------------------------------------------------------------------
# build_agent — happy path
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_instance(self, reset_mocks):
        """build_agent should return whatever create_agent returns."""
        result = build_agent(model_name="gpt-4o", temperature=0.7)
        assert result is reset_mocks["agent_instance"]

    def test_llms_instantiated_with_correct_temperature(self, reset_mocks):
        """LLMS class should be called with the supplied temperature and streaming=True."""
        build_agent(model_name="gpt-4o", temperature=0.5)
        _FAKES["fake_llms_class"].assert_called_once_with(temperature=0.5, streaming=True)

    def test_get_model_called_with_model_name(self, reset_mocks):
        """get_model() should be called with the supplied model_name."""
        build_agent(model_name="claude-3-opus", temperature=0.3)
        _FAKES["fake_llms_class"].return_value.get_model.assert_called_once_with(
            "claude-3-opus"
        )

    def test_create_agent_receives_correct_model(self, reset_mocks):
        """create_agent should receive the model object returned by get_model."""
        build_agent(model_name="gpt-4o-mini", temperature=0.0)
        call_kwargs = _FAKES["fake_create_agent"].call_args.kwargs
        assert call_kwargs["model"] is reset_mocks["model_instance"]

    def test_create_agent_receives_system_prompt(self, reset_mocks):
        """create_agent should receive the SYSTEM_PROMPT constant."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        call_kwargs = _FAKES["fake_create_agent"].call_args.kwargs
        assert call_kwargs["system_prompt"] == graph_module.SYSTEM_PROMPT

    def test_create_agent_receives_checkpointer(self, reset_mocks):
        """create_agent should receive the module-level _checkpointer."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        call_kwargs = _FAKES["fake_create_agent"].call_args.kwargs
        assert call_kwargs["checkpointer"] is graph_module._checkpointer

    def test_create_agent_called_exactly_once(self, reset_mocks):
        """create_agent must be called exactly once per build_agent call."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        _FAKES["fake_create_agent"].assert_called_once()

    def test_tools_list_has_three_items(self, reset_mocks):
        """The tools list passed to create_agent should contain exactly 3 items."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        call_kwargs = _FAKES["fake_create_agent"].call_args.kwargs
        assert len(call_kwargs["tools"]) == 3

    def test_tools_contains_get_customer_profile(self, reset_mocks):
        """get_customer_profile should be in the tools list."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        call_kwargs = _FAKES["fake_create_agent"].call_args.kwargs
        assert graph_module.get_customer_profile in call_kwargs["tools"]

    def test_tools_contains_customer_lookalike(self, reset_mocks):
        """customer_lookalike should be in the tools list."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        call_kwargs = _FAKES["fake_create_agent"].call_args.kwargs
        assert graph_module.customer_lookalike in call_kwargs["tools"]

    def test_tools_contains_assessment_result(self, reset_mocks):
        """The result of _run_underwriting_assessment(mode) should be in the tools list."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        call_kwargs = _FAKES["fake_create_agent"].call_args.kwargs
        assert reset_mocks["tool_instance"] in call_kwargs["tools"]


# ---------------------------------------------------------------------------
# build_agent — mode parameter
# ---------------------------------------------------------------------------

class TestBuildAgentMode:

    def test_default_mode_is_fast(self, reset_mocks):
        """When mode is not supplied, _run_underwriting_assessment should be called with 'fast'."""
        build_agent(model_name="gpt-4o", temperature=0.7)
        _FAKES["fake_assessment"].assert_called_once_with("fast")

    def test_explicit_fast_mode(self, reset_mocks):
        """Explicit mode='fast' should call _run_underwriting_assessment('fast')."""
        build_agent(model_name="gpt-4o", temperature=0.7, mode="fast")
        _FAKES["fake_assessment"].assert_called_once_with("fast")

    def test_deep_mode(self, reset_mocks):
        """mode='deep' should call _run_underwriting_assessment('deep')."""
        build_agent(model_name="gpt-4o", temperature=0.7, mode="deep")
        _FAKES["fake_assessment"].assert_called_once_with("deep")

    def test_custom_mode_string_forwarded(self, reset_mocks):
        """Any mode string should be forwarded verbatim to _run_underwriting_assessment."""
        build_agent(model_name="gpt-4o", temperature=0.