"""
Tests for backend/agent/graph.py

What is tested:
- build_agent(): happy path with various model names, temperatures, and modes
- build_agent(): edge cases (boundary temperatures, unknown mode strings)
- build_agent(): error conditions (LLMS raises, create_agent raises, invalid args)
- Module-level initialisation of Redis client and checkpointer
- Correct tools list composition
- SYSTEM_PROMPT is forwarded to create_agent
- _checkpointer is forwarded to create_agent

Mocks used:
- langchain.agents.create_agent           → unittest.mock.MagicMock / patch
- redis.asyncio.Redis                     → unittest.mock.MagicMock / patch
- langgraph.checkpoint.redis.aio.AsyncRedisSaver → unittest.mock.MagicMock / patch
- modules.tools.get_customer_profile      → unittest.mock.MagicMock
- modules.tools.customer_lookalike        → unittest.mock.MagicMock
- modules.assessment._run_underwriting_assessment → unittest.mock.MagicMock
- modules.LLMS.LLMS                       → unittest.mock.MagicMock
- agent.prompts.SYSTEM_PROMPT             → patched string constant

TODOs:
- TODO: integration test against a real (or containerised) Redis instance
- TODO: test async behaviour of AsyncRedisSaver once async agent invocation is wired up
- TODO: verify checkpointer.setup() / teardown lifecycle if called during agent init
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a fully-mocked module environment so that importing
# backend/agent/graph.py never touches real Redis, LangChain, or LangGraph.
# ---------------------------------------------------------------------------

FAKE_SYSTEM_PROMPT = "You are a helpful underwriting assistant."


def _make_fake_modules():
    """
    Return a dict of fake top-level + sub-modules that graph.py imports.
    We inject these into sys.modules *before* importing the module under test.
    """
    fake_modules: dict[str, types.ModuleType] = {}

    # --- redis.asyncio ---
    redis_pkg = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")
    mock_redis_class = MagicMock(name="Redis")
    redis_asyncio.Redis = mock_redis_class
    redis_pkg.asyncio = redis_asyncio
    fake_modules["redis"] = redis_pkg
    fake_modules["redis.asyncio"] = redis_asyncio

    # --- langgraph.checkpoint.redis.aio ---
    lg_pkg = types.ModuleType("langgraph")
    lg_checkpoint = types.ModuleType("langgraph.checkpoint")
    lg_redis = types.ModuleType("langgraph.checkpoint.redis")
    lg_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    mock_saver_class = MagicMock(name="AsyncRedisSaver")
    lg_aio.AsyncRedisSaver = mock_saver_class
    lg_pkg.checkpoint = lg_checkpoint
    lg_checkpoint.redis = lg_redis
    lg_redis.aio = lg_aio
    fake_modules["langgraph"] = lg_pkg
    fake_modules["langgraph.checkpoint"] = lg_checkpoint
    fake_modules["langgraph.checkpoint.redis"] = lg_redis
    fake_modules["langgraph.checkpoint.redis.aio"] = lg_aio

    # --- langchain.agents ---
    lc_pkg = types.ModuleType("langchain")
    lc_agents = types.ModuleType("langchain.agents")
    mock_create_agent = MagicMock(name="create_agent")
    lc_agents.create_agent = mock_create_agent
    lc_pkg.agents = lc_agents
    fake_modules["langchain"] = lc_pkg
    fake_modules["langchain.agents"] = lc_agents

    # --- agent.prompts (relative import .prompts) ---
    # We register it under both possible lookup keys.
    prompts_mod = types.ModuleType("agent.prompts")
    prompts_mod.SYSTEM_PROMPT = FAKE_SYSTEM_PROMPT
    fake_modules["agent.prompts"] = prompts_mod

    # Also expose as backend.agent.prompts in case the interpreter uses
    # the full package path.
    backend_agent_prompts = types.ModuleType("backend.agent.prompts")
    backend_agent_prompts.SYSTEM_PROMPT = FAKE_SYSTEM_PROMPT
    fake_modules["backend.agent.prompts"] = backend_agent_prompts

    # --- modules.tools ---
    modules_pkg = types.ModuleType("modules")
    tools_mod = types.ModuleType("modules.tools")
    tools_mod.get_customer_profile = MagicMock(name="get_customer_profile")
    tools_mod.customer_lookalike = MagicMock(name="customer_lookalike")
    modules_pkg.tools = tools_mod
    fake_modules["modules"] = modules_pkg
    fake_modules["modules.tools"] = tools_mod

    # --- modules.assessment ---
    assessment_mod = types.ModuleType("modules.assessment")
    mock_run_assessment = MagicMock(name="_run_underwriting_assessment")
    # By default the factory returns a distinct callable per call
    mock_run_assessment.return_value = MagicMock(name="assessment_tool_instance")
    assessment_mod._run_underwriting_assessment = mock_run_assessment
    fake_modules["modules.assessment"] = assessment_mod

    # --- modules.LLMS ---
    llms_mod = types.ModuleType("modules.LLMS")
    mock_llms_class = MagicMock(name="LLMS")
    llms_mod.LLMS = mock_llms_class
    fake_modules["modules.LLMS"] = llms_mod

    return fake_modules


@pytest.fixture(scope="function")
def graph_module(monkeypatch):
    """
    Import (or re-import) backend.agent.graph with all external deps mocked.
    Yields the module object plus the key mock references for assertion.
    """
    fake = _make_fake_modules()

    # Patch sys.modules so that the relative imports inside graph.py resolve
    # to our fakes.  We also clear any previously cached version of the module.
    for key in list(sys.modules.keys()):
        if "agent.graph" in key or "backend.agent.graph" in key:
            del sys.modules[key]

    with patch.dict(sys.modules, fake):
        # Make the relative import work: graph.py lives in the 'agent' package.
        # We need an 'agent' package entry pointing to a module whose __path__
        # is resolvable.  We craft a minimal one if it isn't already present.
        if "agent" not in sys.modules:
            agent_pkg = types.ModuleType("agent")
            agent_pkg.__path__ = []  # marks it as a package
            agent_pkg.__package__ = "agent"
            sys.modules["agent"] = agent_pkg

        # Ensure prompts is reachable as 'agent.prompts'
        sys.modules["agent.prompts"] = fake["agent.prompts"]

        # Now import the real source file by path so we don't need it on
        # PYTHONPATH.  We use importlib.util.
        import importlib.util

        graph_path = os.path.join(
            os.path.dirname(__file__), "..", "backend", "agent", "graph.py"
        )
        graph_path = os.path.normpath(graph_path)

        spec = importlib.util.spec_from_file_location(
            "agent.graph",
            graph_path,
            submodule_search_locations=[],
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "agent"
        sys.modules["agent.graph"] = mod
        spec.loader.exec_module(mod)

        yield mod, fake


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def _mocks(fake: dict):
    """Unpack the mocks we care about from the fake module dict."""
    return {
        "Redis": fake["redis.asyncio"].Redis,
        "AsyncRedisSaver": fake["langgraph.checkpoint.redis.aio"].AsyncRedisSaver,
        "create_agent": fake["langchain.agents"].create_agent,
        "LLMS": fake["modules.LLMS"].LLMS,
        "get_customer_profile": fake["modules.tools"].get_customer_profile,
        "customer_lookalike": fake["modules.tools"].customer_lookalike,
        "_run_underwriting_assessment": fake["modules.assessment"]._run_underwriting_assessment,
    }


# ===========================================================================
# Module-level initialisation tests
# ===========================================================================

class TestModuleLevelInit:
    """Verify that Redis and AsyncRedisSaver are wired up at import time."""

    def test_redis_client_created_with_correct_host_default(self, graph_module):
        _, fake = graph_module
        m = _mocks(fake)
        # Redis() must have been called during module import
        m["Redis"].assert_called_once()
        _, kwargs = m["Redis"].call_args
        assert kwargs.get("port") == 6379
        assert kwargs.get("decode_responses") is False

    def test_redis_client_uses_env_host(self, monkeypatch, graph_module):
        """REDIS_HOST env var should be forwarded to Redis constructor."""
        _, fake = graph_module
        m = _mocks(fake)
        _, kwargs = m["Redis"].call_args
        # When env var is not set the default is "localhost"
        expected_host = os.environ.get("REDIS_HOST", "localhost")
        assert kwargs.get("host") == expected_host

    def test_async_redis_saver_created_with_redis_client(self, graph_module):
        _, fake = graph_module
        m = _mocks(fake)
        m["AsyncRedisSaver"].assert_called_once()
        _, kwargs = m["AsyncRedisSaver"].call_args
        assert "redis_client" in kwargs
        # The redis_client passed in should be the return value of Redis()
        assert kwargs["redis_client"] == m["Redis"].return_value

    def test_checkpointer_is_module_level_attribute(self, graph_module):
        mod, _ = graph_module
        assert hasattr(mod, "_checkpointer")

    def test_redis_client_is_module_level_attribute(self, graph_module):
        mod, _ = graph_module
        assert hasattr(mod, "_redis_client")


# ===========================================================================
# build_agent() – happy path
# ===========================================================================

class TestBuildAgentHappyPath:

    @pytest.mark.parametrize("model_name,temperature,mode", [
        ("gpt-4o", 0.0, "fast"),
        ("gpt-4o", 1.0, "deep"),
        ("gpt-3.5-turbo", 0.5, "fast"),
        ("claude-3-sonnet", 0.7, "deep"),
        ("gpt-4o-mini", 0.2, "fast"),
    ])
    def test_returns_create_agent_result(self, graph_module, model_name, temperature, mode):
        mod, fake = graph_module
        m = _mocks(fake)
        result = mod.build_agent(model_name, temperature, mode)
        assert result == m["create_agent"].return_value

    def test_llms_instantiated_with_correct_temperature(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["LLMS"].reset_mock()

        mod.build_agent("gpt-4o", 0.3, "fast")

        m["LLMS"].assert_called_once_with(temperature=0.3, streaming=True)

    def test_llms_streaming_always_true(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["LLMS"].reset_mock()

        mod.build_agent("some-model", 0.9, "fast")

        _, kwargs = m["LLMS"].call_args
        assert kwargs["streaming"] is True

    def test_get_model_called_with_model_name(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["LLMS"].reset_mock()

        mod.build_agent("claude-3-haiku", 0.1, "fast")

        llms_instance = m["LLMS"].return_value
        llms_instance.get_model.assert_called_once_with("claude-3-haiku")

    def test_run_underwriting_assessment_called_with_mode_fast(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["_run_underwriting_assessment"].reset_mock()

        mod.build_agent("gpt-4o", 0.0, "fast")

        m["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_run_underwriting_assessment_called_with_mode_deep(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["_run_underwriting_assessment"].reset_mock()

        mod.build_agent("gpt-4o", 0.0, "deep")

        m["_run_underwriting_assessment"].assert_called_once_with("deep")

    def test_create_agent_receives_correct_model(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["create_agent"].reset_mock()

        mod.build_agent("gpt-4o", 0.5, "fast")

        _, kwargs = m["create_agent"].call_args
        expected_model = m["LLMS"].return_value.get_model.return_value
        assert kwargs["model"] == expected_model

    def test_create_agent_receives_system_prompt(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["create_agent"].reset_mock()

        mod.build_agent("gpt-4o", 0.5, "fast")

        _, kwargs = m["create_agent"].call_args
        assert kwargs["system_prompt"] == FAKE_SYSTEM_PROMPT

    def test_create_agent_receives_checkpointer(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["create_agent"].reset_mock()

        mod.build_agent("gpt-4o", 0.5, "fast")

        _, kwargs = m["create_agent"].call_args
        assert kwargs["checkpointer"] == mod._checkpointer

    def test_create_agent_receives_three_tools(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["create_agent"].reset_mock()

        mod.build_agent("gpt-4o", 0.5, "fast")

        _, kwargs = m["create_agent"].call_args
        tools = kwargs["tools"]
        assert len(tools) == 3

    def test_tools_list_contains_get_customer_profile(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["create_agent"].reset_mock()

        mod.build_agent("gpt-4o", 0.5, "fast")

        _, kwargs = m["create_agent"].call_args
        assert m["get_customer_profile"] in kwargs["tools"]

    def test_tools_list_contains_customer_lookalike(self, graph_module):
        mod, fake = graph_module
        m = _mocks(fake)
        m["create_agent"].reset_mock()

        mod.build_agent("gpt-4o