"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path with various model names, temperatures, and modes
- build_agent() with default mode parameter
- build_agent() with "fast" and "deep" modes
- build_agent() edge cases: boundary temperatures, empty strings, invalid types
- Module-level Redis client and checkpointer initialization
- Tool list construction inside build_agent()
- Error propagation from LLMS, create_agent, and _run_underwriting_assessment

Mocks used:
- langchain.agents.create_agent (patched to avoid real LLM/agent construction)
- redis.asyncio.Redis (patched at module level to avoid real Redis connection)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched to avoid real Redis connection)
- modules.tools.get_customer_profile (patched)
- modules.tools.customer_lookalike (patched)
- modules.assessment._run_underwriting_assessment (patched)
- modules.LLMS.LLMS (patched to avoid real model instantiation)
- backend.agent.prompts.SYSTEM_PROMPT (patched)

TODOs:
- TODO: Integration test with a real (or containerised) Redis instance once the migration is complete
- TODO: Verify checkpointer is correctly passed to create_agent once langgraph API is stable
- TODO: Test streaming behaviour of the returned agent (requires LLM streaming harness)
- TODO: Validate SYSTEM_PROMPT contents when prompt management is finalised
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake module tree so importing graph.py never
# touches real network resources or heavy dependencies.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """
    Insert lightweight stub modules into sys.modules so that importing
    backend.agent.graph does not trigger real Redis connections, LLM calls,
    or heavy library imports.
    """
    # --- redis.asyncio ---
    fake_redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_cls = MagicMock(name="Redis")
    fake_redis_asyncio.Redis = fake_redis_cls

    fake_redis = types.ModuleType("redis")
    fake_redis.asyncio = fake_redis_asyncio

    sys.modules.setdefault("redis", fake_redis)
    sys.modules.setdefault("redis.asyncio", fake_redis_asyncio)

    # --- langgraph.checkpoint.redis.aio ---
    fake_lg_cp_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_async_redis_saver_cls = MagicMock(name="AsyncRedisSaver")
    fake_lg_cp_redis_aio.AsyncRedisSaver = fake_async_redis_saver_cls

    for mod_name in [
        "langgraph",
        "langgraph.checkpoint",
        "langgraph.checkpoint.redis",
        "langgraph.checkpoint.redis.aio",
    ]:
        sys.modules.setdefault(mod_name, types.ModuleType(mod_name))
    sys.modules["langgraph.checkpoint.redis.aio"] = fake_lg_cp_redis_aio

    # --- langchain.agents ---
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_create_agent = MagicMock(name="create_agent")
    fake_lc_agents.create_agent = fake_create_agent

    fake_langchain = types.ModuleType("langchain")
    fake_langchain.agents = fake_lc_agents
    sys.modules.setdefault("langchain", fake_langchain)
    sys.modules.setdefault("langchain.agents", fake_lc_agents)

    # --- modules.tools ---
    fake_modules = types.ModuleType("modules")
    fake_tools = types.ModuleType("modules.tools")
    fake_tools.get_customer_profile = MagicMock(name="get_customer_profile")
    fake_tools.customer_lookalike = MagicMock(name="customer_lookalike")
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
    fake_llms_mod = types.ModuleType("modules.LLMS")
    fake_llms_instance = MagicMock(name="llms_instance")
    fake_llms_instance.get_model.return_value = MagicMock(name="model")
    fake_llms_cls = MagicMock(name="LLMS", return_value=fake_llms_instance)
    fake_llms_mod.LLMS = fake_llms_cls
    fake_modules.LLMS = fake_llms_mod
    sys.modules.setdefault("modules.LLMS", fake_llms_mod)

    # --- backend (package stubs) ---
    fake_backend = types.ModuleType("backend")
    fake_agent_pkg = types.ModuleType("backend.agent")

    fake_prompts_mod = types.ModuleType("backend.agent.prompts")
    fake_prompts_mod.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"

    sys.modules.setdefault("backend", fake_backend)
    sys.modules.setdefault("backend.agent", fake_agent_pkg)
    sys.modules.setdefault("backend.agent.prompts", fake_prompts_mod)

    # The relative import ".prompts" inside graph.py resolves to the package's
    # prompts sub-module; register it under that key too.
    fake_agent_pkg.prompts = fake_prompts_mod

    return {
        "Redis": fake_redis_cls,
        "AsyncRedisSaver": fake_async_redis_saver_cls,
        "create_agent": fake_create_agent,
        "get_customer_profile": fake_tools.get_customer_profile,
        "customer_lookalike": fake_tools.customer_lookalike,
        "_run_underwriting_assessment": fake_assessment._run_underwriting_assessment,
        "LLMS": fake_llms_cls,
        "llms_instance": fake_llms_instance,
    }


# Run once before any test collection so imports inside conftest / fixtures
# also see the stubs.
_STUBS = _make_fake_modules()


# ---------------------------------------------------------------------------
# Import the module under test AFTER stubs are registered.
# We use importlib so we can reload between tests when needed.
# ---------------------------------------------------------------------------

# Guard: remove any previously cached real module
sys.modules.pop("backend.agent.graph", None)

# We import via a path trick because the file lives at backend/agent/graph.py
# and may not be on sys.path as a package.  Add the repo root if necessary.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


@pytest.fixture(autouse=True)
def reset_stubs():
    """Reset all mock call counts before every test."""
    for stub in _STUBS.values():
        if isinstance(stub, MagicMock):
            stub.reset_mock()
    yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def graph_module():
    """
    Import (or reload) the graph module with all stubs in place.
    Returns the module object.
    """
    sys.modules.pop("backend.agent.graph", None)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "backend.agent.graph",
        os.path.join(os.path.dirname(__file__), "graph.py"),
        submodule_search_locations=[],
    )
    if spec is None or spec.loader is None:
        pytest.skip("graph.py not found – adjust path if running from a different CWD")
    mod = importlib.util.module_from_spec(spec)
    # Make relative imports resolvable
    mod.__package__ = "backend.agent"
    sys.modules["backend.agent.graph"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture()
def fake_agent():
    return MagicMock(name="built_agent")


@pytest.fixture()
def configured_create_agent(fake_agent):
    """Ensure create_agent stub returns a predictable fake agent."""
    _STUBS["create_agent"].return_value = fake_agent
    return _STUBS["create_agent"]


# ---------------------------------------------------------------------------
# Tests: module-level initialisation
# ---------------------------------------------------------------------------

class TestModuleLevelInit:
    """Verify that Redis and AsyncRedisSaver are instantiated on import."""

    def test_redis_client_created_with_default_host(self, graph_module):
        """Redis() should be called with host from env (default 'localhost') and port 6379."""
        _STUBS["Redis"].assert_called()
        _, kwargs = _STUBS["Redis"].call_args
        assert kwargs.get("port") == 6379
        assert "host" in kwargs

    def test_redis_client_decode_responses_false(self, graph_module):
        """decode_responses must be False (binary data for checkpointing)."""
        _, kwargs = _STUBS["Redis"].call_args
        assert kwargs.get("decode_responses") is False

    def test_async_redis_saver_created(self, graph_module):
        """AsyncRedisSaver should be instantiated with the Redis client."""
        _STUBS["AsyncRedisSaver"].assert_called_once()

    def test_redis_host_from_env(self, monkeypatch):
        """REDIS_HOST env var should be forwarded to Redis()."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        _STUBS["Redis"].reset_mock()
        sys.modules.pop("backend.agent.graph", None)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "backend.agent.graph",
            os.path.join(os.path.dirname(__file__), "graph.py"),
            submodule_search_locations=[],
        )
        if spec is None or spec.loader is None:
            pytest.skip("graph.py not found")
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "backend.agent"
        sys.modules["backend.agent.graph"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        _, kwargs = _STUBS["Redis"].call_args
        assert kwargs.get("host") == "my-redis-host"

    def test_redis_host_defaults_to_localhost(self, monkeypatch):
        """When REDIS_HOST is unset, host should default to 'localhost'."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        _STUBS["Redis"].reset_mock()
        sys.modules.pop("backend.agent.graph", None)

        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "backend.agent.graph",
            os.path.join(os.path.dirname(__file__), "graph.py"),
            submodule_search_locations=[],
        )
        if spec is None or spec.loader is None:
            pytest.skip("graph.py not found")
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "backend.agent"
        sys.modules["backend.agent.graph"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        _, kwargs = _STUBS["Redis"].call_args
        assert kwargs.get("host") == "localhost"


# ---------------------------------------------------------------------------
# Tests: build_agent – happy path
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_object(self, graph_module, configured_create_agent, fake_agent):
        result = graph_module.build_agent("gpt-4o", 0.5)
        assert result is fake_agent

    def test_default_mode_is_fast(self, graph_module, configured_create_agent):
        graph_module.build_agent("gpt-4o", 0.7)
        _STUBS["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_fast_mode_explicit(self, graph_module, configured_create_agent):
        graph_module.build_agent("gpt-4o", 0.7, mode="fast")
        _STUBS["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_deep_mode(self, graph_module, configured_create_agent):
        graph_module.build_agent("gpt-4o", 0.7, mode="deep")
        _STUBS["_run_underwriting_assessment"].assert_called_once_with("deep")

    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-3.5-turbo",
        "claude-3-5-sonnet",
        "gemini-pro",
    ])
    def test_various_model_names_forwarded_to_get_model(
        self, graph_module, configured_create_agent, model_name
    ):
        graph_module.build_agent(model_name, 0.5)
        _STUBS["llms_instance"].get_model.assert_called_with(model_name)

    @pytest.mark.parametrize("temperature", [0.0, 0.1, 0.5, 0.9, 1.0])
    def test_temperature_forwarded_to_llms(
        self, graph_module, configured_create_agent, temperature
    ):
        graph_module.build_agent("gpt-4o", temperature)
        _STUBS["LLMS"].assert_called_with(temperature=temperature, streaming=True)

    def test_streaming_always_true(self, graph_module, configured_create_agent):
        graph_module.build_agent("gpt-4o", 0.3)
        _, kwargs = _STUBS["LLMS"].call_args
        assert kwargs.get("streaming") is True

    def test_create_agent_called_with_system_prompt(self, graph_module, configured_create_agent):
        graph_module.build_agent("gpt-4o", 0.5)
        _, kwargs = _STUBS["create_agent"].call_args
        assert "system_prompt" in kwargs

    def test_create_agent_called_with_checkpointer(self, graph_module, configured_create_agent):
        graph_module.build_agent("gpt-4o", 0.5)
        _, kwargs = _STUBS["create_agent"].call_args
        assert "checkpointer" in kwargs

    def test_create_agent_called_with_model(self, graph_module, configured_create_agent):
        fake_model = MagicMock(name="model_obj")
        _STUBS["llms_instance"].get_model.return_value = fake_model
        graph_module.build_agent("gpt-4o", 0.5)
        _, kwargs = _STUBS["create_agent"].call_args
        assert kwargs.get("model") is fake_model

    def test_tools_list_contains_three_items(self, graph_module, configured_create_agent):
        graph_module.