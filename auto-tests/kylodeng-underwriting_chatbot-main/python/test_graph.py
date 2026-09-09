"""
Tests for backend/agent/graph.py

What is tested:
- build_agent() happy path with valid model_name, temperature, and mode combinations
- build_agent() with all supported mode values ("fast", "deep", and edge/invalid modes)
- build_agent() boundary values for temperature (0.0, 1.0, extreme values)
- build_agent() error conditions (invalid model_name, LLMS failures, create_agent failures)
- Module-level Redis client and checkpointer initialisation (environment variable handling)
- Tool list construction (correct tools are passed to create_agent)

Mocks used:
- langchain.agents.create_agent (prevents real agent creation)
- modules.tools.get_customer_profile (prevents real tool calls)
- modules.tools.customer_lookalike (prevents real tool calls)
- modules.assessment._run_underwriting_assessment (prevents real assessment calls)
- modules.LLMS.LLMS (prevents real LLM instantiation)
- redis.asyncio.Redis (prevents real Redis connections)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (prevents real Redis saver creation)

TODOs:
- TODO: Integration test for full agent invocation once a test Redis instance is available
- TODO: Validate SYSTEM_PROMPT is correctly injected once prompts module is accessible in test env
- TODO: Test streaming behaviour of the agent once a mock LLM streaming interface is defined
"""

import os
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal stub tree so the module can be imported without
# real dependencies installed in the test environment.
# ---------------------------------------------------------------------------

def _make_stubs():
    """
    Create lightweight stub modules for every external dependency so that
    importing backend.agent.graph never touches real network/IO resources.
    """
    stubs = {}

    # --- redis.asyncio ---
    redis_asyncio_mod = types.ModuleType("redis.asyncio")
    mock_redis_instance = MagicMock(name="Redis-instance")
    redis_asyncio_mod.Redis = MagicMock(name="Redis", return_value=mock_redis_instance)
    stubs["redis"] = types.ModuleType("redis")
    stubs["redis.asyncio"] = redis_asyncio_mod

    # --- langgraph.checkpoint.redis.aio ---
    lg_cp_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    mock_checkpointer_instance = MagicMock(name="AsyncRedisSaver-instance")
    lg_cp_redis_aio.AsyncRedisSaver = MagicMock(
        name="AsyncRedisSaver", return_value=mock_checkpointer_instance
    )
    stubs["langgraph"] = types.ModuleType("langgraph")
    stubs["langgraph.checkpoint"] = types.ModuleType("langgraph.checkpoint")
    stubs["langgraph.checkpoint.redis"] = types.ModuleType("langgraph.checkpoint.redis")
    stubs["langgraph.checkpoint.redis.aio"] = lg_cp_redis_aio

    # --- langchain.agents ---
    langchain_agents_mod = types.ModuleType("langchain.agents")
    langchain_agents_mod.create_agent = MagicMock(name="create_agent")
    stubs["langchain"] = types.ModuleType("langchain")
    stubs["langchain.agents"] = langchain_agents_mod

    # --- modules.tools ---
    modules_mod = types.ModuleType("modules")
    modules_tools_mod = types.ModuleType("modules.tools")
    modules_tools_mod.get_customer_profile = MagicMock(name="get_customer_profile")
    modules_tools_mod.customer_lookalike = MagicMock(name="customer_lookalike")
    stubs["modules"] = modules_mod
    stubs["modules.tools"] = modules_tools_mod

    # --- modules.assessment ---
    modules_assessment_mod = types.ModuleType("modules.assessment")
    mock_assessment_tool = MagicMock(name="assessment_tool_instance")
    modules_assessment_mod._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment", return_value=mock_assessment_tool
    )
    stubs["modules.assessment"] = modules_assessment_mod

    # --- modules.LLMS ---
    modules_llms_mod = types.ModuleType("modules.LLMS")
    mock_llms_instance = MagicMock(name="LLMS-instance")
    mock_llms_instance.get_model = MagicMock(name="get_model", return_value=MagicMock(name="model"))
    modules_llms_mod.LLMS = MagicMock(name="LLMS", return_value=mock_llms_instance)
    stubs["modules.LLMS"] = modules_llms_mod

    # --- agent.prompts (relative import .prompts) ---
    agent_prompts_mod = types.ModuleType("agent.prompts")
    agent_prompts_mod.SYSTEM_PROMPT = "MOCK_SYSTEM_PROMPT"
    stubs["agent.prompts"] = agent_prompts_mod
    # also register under the package name that the relative import resolves to
    stubs["backend.agent.prompts"] = agent_prompts_mod

    return stubs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_sys_modules(monkeypatch):
    """
    Inject stub modules into sys.modules before every test and clean up after.
    The graph module is reloaded fresh for each test to pick up per-test mocks.
    """
    stubs = _make_stubs()

    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Ensure the package hierarchy exists so relative imports resolve
    for pkg in ("agent", "backend", "backend.agent"):
        if pkg not in sys.modules:
            monkeypatch.setitem(sys.modules, pkg, types.ModuleType(pkg))

    # Remove any previously cached version of graph so it is re-imported
    for key in list(sys.modules.keys()):
        if "graph" in key and "agent" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)

    yield stubs


@pytest.fixture()
def graph_module(_patch_sys_modules):
    """
    Import (or reload) the graph module under test after stubs are in place.
    Returns the module object so tests can access build_agent and module-level globals.
    """
    # Dynamically locate and load the module
    import importlib.util, pathlib

    graph_path = pathlib.Path(__file__).parent / "graph.py"

    # Provide a fake __package__ so relative imports work
    spec = importlib.util.spec_from_file_location(
        "agent.graph",
        graph_path,
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "agent"
    sys.modules["agent.graph"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mock_create_agent(_patch_sys_modules):
    return _patch_sys_modules["langchain.agents"].create_agent


@pytest.fixture()
def mock_llms_class(_patch_sys_modules):
    return _patch_sys_modules["modules.LLMS"].LLMS


@pytest.fixture()
def mock_run_assessment(_patch_sys_modules):
    return _patch_sys_modules["modules.assessment"]._run_underwriting_assessment


@pytest.fixture()
def mock_redis_class(_patch_sys_modules):
    return _patch_sys_modules["redis.asyncio"].Redis


@pytest.fixture()
def mock_async_redis_saver(_patch_sys_modules):
    return _patch_sys_modules["langgraph.checkpoint.redis.aio"].AsyncRedisSaver


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------


class TestModuleLevelInitialisation:
    """Tests for Redis client and checkpointer created at import time."""

    def test_redis_client_created_with_default_host(
        self, graph_module, mock_redis_class, monkeypatch
    ):
        """Redis should default to 'localhost' when REDIS_HOST is not set."""
        # The module was already imported; verify Redis() was called at least once
        mock_redis_class.assert_called()
        _, kwargs = mock_redis_class.call_args
        assert kwargs.get("port") == 6379
        assert kwargs.get("decode_responses") is False

    def test_redis_host_from_env_variable(self, monkeypatch, _patch_sys_modules):
        """When REDIS_HOST env var is set, Redis should use that host."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host.example.com")

        # Re-import graph so module-level code re-runs with the new env var
        import importlib.util, pathlib

        for key in list(sys.modules.keys()):
            if key in ("agent.graph",):
                del sys.modules[key]

        graph_path = pathlib.Path(__file__).parent / "graph.py"
        spec = importlib.util.spec_from_file_location("agent.graph", graph_path)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "agent"
        sys.modules["agent.graph"] = mod
        spec.loader.exec_module(mod)

        mock_redis = _patch_sys_modules["redis.asyncio"].Redis
        call_kwargs = mock_redis.call_args[1]
        assert call_kwargs.get("host") == "my-redis-host.example.com"

    def test_async_redis_saver_created_with_redis_client(
        self, graph_module, mock_async_redis_saver, mock_redis_class
    ):
        """AsyncRedisSaver must be initialised with the Redis client instance."""
        mock_async_redis_saver.assert_called()
        _, kwargs = mock_async_redis_saver.call_args
        # The redis_client passed must be the return value of Redis()
        assert kwargs.get("redis_client") is mock_redis_class.return_value

    def test_redis_default_host_when_env_not_set(self, monkeypatch, _patch_sys_modules):
        """When REDIS_HOST is absent, host should be 'localhost'."""
        monkeypatch.delenv("REDIS_HOST", raising=False)

        import importlib.util, pathlib

        for key in list(sys.modules.keys()):
            if key == "agent.graph":
                del sys.modules[key]

        graph_path = pathlib.Path(__file__).parent / "graph.py"
        spec = importlib.util.spec_from_file_location("agent.graph", graph_path)
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "agent"
        sys.modules["agent.graph"] = mod
        spec.loader.exec_module(mod)

        mock_redis = _patch_sys_modules["redis.asyncio"].Redis
        call_kwargs = mock_redis.call_args[1]
        assert call_kwargs.get("host") == "localhost"


# ---------------------------------------------------------------------------
# build_agent – happy path
# ---------------------------------------------------------------------------


class TestBuildAgentHappyPath:

    def test_returns_create_agent_result(self, graph_module, mock_create_agent):
        """build_agent must return whatever create_agent returns."""
        expected = MagicMock(name="agent-instance")
        mock_create_agent.return_value = expected

        result = graph_module.build_agent("gpt-4o", 0.7)

        assert result is expected

    def test_create_agent_called_once(self, graph_module, mock_create_agent):
        """create_agent must be called exactly once per build_agent call."""
        graph_module.build_agent("gpt-4o", 0.5)
        assert mock_create_agent.call_count == 1

    def test_create_agent_receives_system_prompt(self, graph_module, mock_create_agent):
        """create_agent must receive SYSTEM_PROMPT as system_prompt kwarg."""
        graph_module.build_agent("gpt-4o", 0.3)
        _, kwargs = mock_create_agent.call_args
        assert kwargs.get("system_prompt") == "MOCK_SYSTEM_PROMPT"

    def test_create_agent_receives_checkpointer(self, graph_module, mock_create_agent, mock_async_redis_saver):
        """create_agent must receive the module-level checkpointer."""
        graph_module.build_agent("gpt-4o", 0.3)
        _, kwargs = mock_create_agent.call_args
        assert kwargs.get("checkpointer") is mock_async_redis_saver.return_value

    def test_llms_instantiated_with_correct_params(self, graph_module, mock_llms_class):
        """LLMS must be called with the provided temperature and streaming=True."""
        graph_module.build_agent("claude-3", 0.9)
        mock_llms_class.assert_called_with(temperature=0.9, streaming=True)

    def test_get_model_called_with_model_name(self, graph_module, mock_llms_class):
        """get_model must be called with the model_name argument."""
        graph_module.build_agent("claude-3", 0.9)
        mock_llms_class.return_value.get_model.assert_called_with("claude-3")

    def test_model_passed_to_create_agent(self, graph_module, mock_create_agent, mock_llms_class):
        """The model returned by get_model must be forwarded to create_agent."""
        expected_model = MagicMock(name="specific-model")
        mock_llms_class.return_value.get_model.return_value = expected_model

        graph_module.build_agent("gpt-4o-mini", 0.1)

        _, kwargs = mock_create_agent.call_args
        assert kwargs.get("model") is expected_model

    def test_default_mode_is_fast(self, graph_module, mock_run_assessment):
        """When mode is omitted the default should be 'fast'."""
        graph_module.build_agent("gpt-4o", 0.5)
        mock_run_assessment.assert_called_with("fast")

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_mode_forwarded_to_assessment(self, graph_module, mock_run_assessment, mode):
        """_run_underwriting_assessment must be called with the given mode."""
        graph_module.build_agent("gpt-4o", 0.5, mode=mode)
        mock_run_assessment.assert_called_with(mode)


# ---------------------------------------------------------------------------
# build_agent – tool list construction
# ---------------------------------------------------------------------------


class TestBuildAgentToolList:

    def test_tools_list_has_three_items(self, graph_module, mock_create_agent):
        """The tools list passed to create_agent must contain exactly 3 items."""
        graph_module.build_agent("gpt-4o", 0.5)
        _, kwargs = mock_create_agent.call_args
        assert len(kwargs.get("tools", [])) == 3

    def test_tools_contains_get_customer_profile(
        self, graph_module, mock_create_agent, _patch_sys_modules
    ):
        """get_customer_profile must be included in the tools list."""
        graph_module.build_agent("gpt-4o", 0.5)
        _, kwargs = mock_create_agent.call_args
        tools = kwargs.get("tools", [])
        assert _patch_sys_modules["modules.tools"].get_customer_profile in tools

    def test_tools_contains_customer_lookalike(