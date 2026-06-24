"""
Tests for backend/agent/graph.py

What is tested:
- build_agent(): happy path with various model names, temperatures, and modes
- build_agent(): edge cases (boundary temperatures, unknown mode strings)
- build_agent(): error conditions (LLMS raises, create_agent raises, invalid arguments)
- Module-level Redis / checkpointer initialisation (env var override)
- Tool list composition passed to create_agent

Mocks used:
- unittest.mock.patch for:
    - langchain.agents.create_agent
    - redis.asyncio.Redis
    - langgraph.checkpoint.redis.aio.AsyncRedisSaver
    - modules.tools.get_customer_profile
    - modules.tools.customer_lookalike
    - modules.assessment._run_underwriting_assessment
    - modules.LLMS.LLMS
- os.environ patching via monkeypatch / patch.dict

TODOs:
- TODO: Integration test that verifies the agent actually calls the underlying LLM
        (requires a live or containerised Redis + LLM endpoint).
- TODO: Verify checkpointer thread-safety / async behaviour once AsyncRedisSaver
        internals are stable.
- TODO: Confirm exact signature of create_agent when langchain version is pinned.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers to build lightweight fake modules so the real heavy imports never
# execute during the test run.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """Return a dict of fake top-level + sub-modules needed to import graph.py."""

    # --- langchain.agents ---
    langchain_pkg = types.ModuleType("langchain")
    langchain_agents = types.ModuleType("langchain.agents")
    mock_create_agent = MagicMock(name="create_agent")
    langchain_agents.create_agent = mock_create_agent
    langchain_pkg.agents = langchain_agents

    # --- redis ---
    redis_pkg = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")
    mock_redis_cls = MagicMock(name="Redis")
    redis_asyncio.Redis = mock_redis_cls
    redis_pkg.asyncio = redis_asyncio

    # --- langgraph.checkpoint.redis.aio ---
    langgraph_pkg = types.ModuleType("langgraph")
    langgraph_checkpoint = types.ModuleType("langgraph.checkpoint")
    langgraph_checkpoint_redis = types.ModuleType("langgraph.checkpoint.redis")
    langgraph_checkpoint_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    mock_saver_cls = MagicMock(name="AsyncRedisSaver")
    langgraph_checkpoint_redis_aio.AsyncRedisSaver = mock_saver_cls
    langgraph_pkg.checkpoint = langgraph_checkpoint
    langgraph_checkpoint.redis = langgraph_checkpoint_redis
    langgraph_checkpoint_redis.aio = langgraph_checkpoint_redis_aio

    # --- modules.tools ---
    modules_pkg = types.ModuleType("modules")
    modules_tools = types.ModuleType("modules.tools")
    mock_get_customer_profile = MagicMock(name="get_customer_profile")
    mock_customer_lookalike = MagicMock(name="customer_lookalike")
    modules_tools.get_customer_profile = mock_get_customer_profile
    modules_tools.customer_lookalike = mock_customer_lookalike
    modules_pkg.tools = modules_tools

    # --- modules.assessment ---
    modules_assessment = types.ModuleType("modules.assessment")
    mock_run_underwriting = MagicMock(name="_run_underwriting_assessment")
    mock_run_underwriting.return_value = MagicMock(name="underwriting_tool_instance")
    modules_assessment._run_underwriting_assessment = mock_run_underwriting
    modules_pkg.assessment = modules_assessment

    # --- modules.LLMS ---
    modules_llms_mod = types.ModuleType("modules.LLMS")
    mock_llms_cls = MagicMock(name="LLMS")
    modules_llms_mod.LLMS = mock_llms_cls
    modules_pkg.LLMS = modules_llms_mod

    # --- .prompts (backend.agent.prompts) ---
    # Will be injected as the relative sibling module; handled separately via
    # sys.modules key matching the package path.

    return {
        "langchain": langchain_pkg,
        "langchain.agents": langchain_agents,
        "redis": redis_pkg,
        "redis.asyncio": redis_asyncio,
        "langgraph": langgraph_pkg,
        "langgraph.checkpoint": langgraph_checkpoint,
        "langgraph.checkpoint.redis": langgraph_checkpoint_redis,
        "langgraph.checkpoint.redis.aio": langgraph_checkpoint_redis_aio,
        "modules": modules_pkg,
        "modules.tools": modules_tools,
        "modules.assessment": modules_assessment,
        "modules.LLMS": modules_llms_mod,
        # Stubs for the relative import .prompts
        "backend": types.ModuleType("backend"),
        "backend.agent": types.ModuleType("backend.agent"),
    }


# ---------------------------------------------------------------------------
# Fixture: load graph module with all heavy dependencies stubbed out.
# We reload the module fresh for each test to avoid cross-test pollution.
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_modules():
    """Install fake modules into sys.modules and yield them; clean up after."""
    fakes = _make_fake_modules()

    # Build a prompts stub with SYSTEM_PROMPT
    prompts_stub = types.ModuleType("backend.agent.prompts")
    prompts_stub.SYSTEM_PROMPT = "You are an underwriting assistant."

    # Register everything
    originals = {}
    keys_to_inject = list(fakes.keys()) + ["backend.agent.prompts"]
    for key in keys_to_inject:
        originals[key] = sys.modules.get(key)

    for key, mod in fakes.items():
        sys.modules[key] = mod
    sys.modules["backend.agent.prompts"] = prompts_stub

    yield fakes, prompts_stub

    # Restore
    for key, original in originals.items():
        if original is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = original

    # Remove any previously loaded graph module so it doesn't bleed
    sys.modules.pop("backend.agent.graph", None)
    sys.modules.pop("agent.graph", None)


@pytest.fixture()
def graph_module(fake_modules, monkeypatch):
    """Import (or reimport) backend.agent.graph with fakes in place."""
    # Ensure the package structure exists in sys.modules
    agent_pkg = sys.modules.get("backend.agent") or types.ModuleType("backend.agent")
    agent_pkg.__path__ = []  # mark as package
    agent_pkg.__package__ = "backend.agent"
    sys.modules["backend.agent"] = agent_pkg

    backend_pkg = sys.modules.get("backend") or types.ModuleType("backend")
    backend_pkg.__path__ = []
    sys.modules["backend"] = backend_pkg

    # Remove stale cached graph module if any
    sys.modules.pop("backend.agent.graph", None)

    monkeypatch.setenv("REDIS_HOST", "test-redis-host")

    import importlib.util, os

    graph_path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "agent", "graph.py"
    )
    # Fallback: try relative from CWD
    if not os.path.exists(graph_path):
        graph_path = os.path.join("backend", "agent", "graph.py")

    spec = importlib.util.spec_from_file_location(
        "backend.agent.graph",
        graph_path,
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "backend.agent"
    sys.modules["backend.agent.graph"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Convenience accessors so tests stay readable
# ---------------------------------------------------------------------------

def _get_mocks(fake_modules):
    fakes, prompts_stub = fake_modules
    return {
        "create_agent": fakes["langchain.agents"].create_agent,
        "Redis": fakes["redis.asyncio"].Redis,
        "AsyncRedisSaver": fakes["langgraph.checkpoint.redis.aio"].AsyncRedisSaver,
        "LLMS": fakes["modules.LLMS"].LLMS,
        "get_customer_profile": fakes["modules.tools"].get_customer_profile,
        "customer_lookalike": fakes["modules.tools"].customer_lookalike,
        "_run_underwriting_assessment": fakes["modules.assessment"]._run_underwriting_assessment,
        "SYSTEM_PROMPT": prompts_stub.SYSTEM_PROMPT,
    }


# ===========================================================================
# Tests: module-level initialisation
# ===========================================================================

class TestModuleLevelInit:
    """Verify Redis client and checkpointer are created at import time."""

    def test_redis_client_created_with_env_host(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["Redis"].assert_called()
        call_kwargs = mocks["Redis"].call_args
        # host should come from REDIS_HOST env var set in fixture
        assert call_kwargs.kwargs.get("host") == "test-redis-host" or \
               (call_kwargs.args and call_kwargs.args[0] == "test-redis-host") or \
               call_kwargs.kwargs.get("host") == "test-redis-host"

    def test_redis_client_uses_port_6379(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        call_kwargs = mocks["Redis"].call_args
        assert call_kwargs.kwargs.get("port") == 6379

    def test_redis_decode_responses_false(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        call_kwargs = mocks["Redis"].call_args
        assert call_kwargs.kwargs.get("decode_responses") is False

    def test_checkpointer_created_with_redis_client(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["AsyncRedisSaver"].assert_called_once()
        saver_kwargs = mocks["AsyncRedisSaver"].call_args.kwargs
        # The redis_client passed should be the return value of Redis(...)
        assert saver_kwargs.get("redis_client") == mocks["Redis"].return_value

    def test_module_exposes_build_agent(self, graph_module):
        assert hasattr(graph_module, "build_agent")
        assert callable(graph_module.build_agent)


# ===========================================================================
# Tests: build_agent — happy path
# ===========================================================================

class TestBuildAgentHappyPath:

    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-4o-mini",
        "claude-3-5-sonnet",
        "o1-preview",
    ])
    def test_returns_create_agent_result(self, graph_module, fake_modules, model_name):
        mocks = _get_mocks(fake_modules)
        expected = MagicMock(name="agent_instance")
        mocks["create_agent"].return_value = expected

        result = graph_module.build_agent(model_name=model_name, temperature=0.7)

        assert result is expected

    @pytest.mark.parametrize("model_name,temperature,mode", [
        ("gpt-4o", 0.0, "fast"),
        ("gpt-4o-mini", 1.0, "deep"),
        ("claude-3-5-sonnet", 0.5, "fast"),
        ("o1-preview", 0.3, "deep"),
    ])
    def test_create_agent_called_once(self, graph_module, fake_modules,
                                     model_name, temperature, mode):
        mocks = _get_mocks(fake_modules)
        mocks["create_agent"].reset_mock()

        graph_module.build_agent(model_name=model_name, temperature=temperature, mode=mode)

        mocks["create_agent"].assert_called_once()

    def test_create_agent_receives_system_prompt(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["create_agent"].reset_mock()

        graph_module.build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mocks["create_agent"].call_args
        assert kwargs.get("system_prompt") == mocks["SYSTEM_PROMPT"]

    def test_create_agent_receives_checkpointer(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["create_agent"].reset_mock()

        graph_module.build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mocks["create_agent"].call_args
        assert kwargs.get("checkpointer") == mocks["AsyncRedisSaver"].return_value

    def test_llms_instantiated_with_correct_params(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["LLMS"].reset_mock()

        graph_module.build_agent(model_name="gpt-4o", temperature=0.7)

        mocks["LLMS"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["LLMS"].reset_mock()

        graph_module.build_agent(model_name="gpt-4o-mini", temperature=0.3)

        llms_instance = mocks["LLMS"].return_value
        llms_instance.get_model.assert_called_once_with("gpt-4o-mini")

    def test_default_mode_is_fast(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["_run_underwriting_assessment"].reset_mock()

        graph_module.build_agent(model_name="gpt-4o", temperature=0.5)

        mocks["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_deep_mode_passed_to_underwriting(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["_run_underwriting_assessment"].reset_mock()

        graph_module.build_agent(model_name="gpt-4o", temperature=0.5, mode="deep")

        mocks["_run_underwriting_assessment"].assert_called_once_with("deep")

    def test_fast_mode_explicit(self, graph_module, fake_modules):
        mocks = _get_mocks(fake_modules)
        mocks["_run_underwriting_assessment"].reset_mock()

        graph_module.build_agent(model_name="gpt-4o", temperature=0.5, mode="fast")

        mocks["_run_underwriting_assessment"].assert_called_once_with("fast")


# ===========================================================================
# Tests: build_agent — tool list
# ===========================================================================

class TestBuildAgentTools:

    def test_tools_list_has_three_items(self, graph_module,