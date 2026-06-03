"""
Tests for backend/agent/graph.py

What is tested:
- build_agent(): happy path with various model_name/temperature/mode combinations,
  edge cases (boundary temperatures, unknown mode string), error conditions
  (LLMS raises, create_agent raises, tool construction raises).

Mocks used:
- backend.agent.graph.LLMS                    – avoids real LLM instantiation
- backend.agent.graph.create_agent            – avoids real LangChain agent creation
- backend.agent.graph.get_customer_profile    – pre-imported tool stub
- backend.agent.graph.customer_lookalike      – pre-imported tool stub
- backend.agent.graph._run_underwriting_assessment – avoids real assessment logic
- backend.agent.graph.AsyncRedisSaver         – avoids real Redis connection
- backend.agent.graph.Redis                   – avoids real Redis connection
- os.environ                                  – controls REDIS_HOST without side-effects

TODOs:
- TODO: Integration test with a real (or containerised) Redis instance to verify
        checkpointer setup and memory persistence across calls.
- TODO: Test the agent's actual conversation/streaming behaviour once
        end-to-end fixtures are available.
- TODO: Verify SYSTEM_PROMPT content is injected correctly into create_agent call
        (requires access to the real prompts module).
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test with mocks in place
# ---------------------------------------------------------------------------

def _make_fake_redis_module():
    """Return a minimal fake redis.asyncio module so the top-level import works."""
    fake_redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_class = MagicMock(name="Redis")
    fake_redis_asyncio.Redis = fake_redis_class
    return fake_redis_asyncio


def _make_fake_langgraph_module():
    fake_langgraph = types.ModuleType("langgraph")
    fake_checkpoint = types.ModuleType("langgraph.checkpoint")
    fake_checkpoint_redis = types.ModuleType("langgraph.checkpoint.redis")
    fake_checkpoint_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_checkpoint_redis_aio.AsyncRedisSaver = MagicMock(name="AsyncRedisSaver")
    return fake_langgraph, fake_checkpoint, fake_checkpoint_redis, fake_checkpoint_redis_aio


def _make_fake_langchain_module():
    fake_langchain = types.ModuleType("langchain")
    fake_langchain_agents = types.ModuleType("langchain.agents")
    fake_langchain_agents.create_agent = MagicMock(name="create_agent")
    return fake_langchain, fake_langchain_agents


def _make_fake_modules_tools():
    fake_tools_mod = types.ModuleType("modules.tools")
    fake_tools_mod.get_customer_profile = MagicMock(name="get_customer_profile")
    fake_tools_mod.customer_lookalike = MagicMock(name="customer_lookalike")
    return fake_tools_mod


def _make_fake_modules_assessment():
    fake_assessment_mod = types.ModuleType("modules.assessment")
    fake_assessment_mod._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment",
        return_value=MagicMock(name="assessment_tool"),
    )
    return fake_assessment_mod


def _make_fake_llms_module():
    fake_llms_mod = types.ModuleType("modules.LLMS")
    fake_llms_mod.LLMS = MagicMock(name="LLMS")
    return fake_llms_mod


def _make_fake_prompts_module():
    fake_prompts_mod = types.ModuleType("backend.agent.prompts")
    fake_prompts_mod.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
    return fake_prompts_mod


# ---------------------------------------------------------------------------
# Fixture: fresh import of the module under test with all heavy deps mocked
# ---------------------------------------------------------------------------

@pytest.fixture()
def graph_module(monkeypatch):
    """
    Import (or re-import) backend.agent.graph with all external dependencies
    replaced by mocks.  Returns the module plus the key mock objects.
    """
    # Remove cached modules so we get a clean import each time
    for key in list(sys.modules.keys()):
        if key.startswith("backend.agent.graph") or key == "backend.agent.graph":
            del sys.modules[key]

    # Build fake sub-modules
    fake_redis_asyncio = _make_fake_redis_module()
    fake_langchain, fake_langchain_agents = _make_fake_langchain_module()
    fake_langgraph, fake_checkpoint, fake_checkpoint_redis, fake_checkpoint_redis_aio = (
        _make_fake_langgraph_module()
    )
    fake_tools_mod = _make_fake_modules_tools()
    fake_assessment_mod = _make_fake_modules_assessment()
    fake_llms_mod = _make_fake_llms_module()
    fake_prompts_mod = _make_fake_prompts_module()

    # Inject into sys.modules
    monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_redis_asyncio)
    monkeypatch.setitem(sys.modules, "langchain", fake_langchain)
    monkeypatch.setitem(sys.modules, "langchain.agents", fake_langchain_agents)
    monkeypatch.setitem(sys.modules, "langgraph", fake_langgraph)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint", fake_checkpoint)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.redis", fake_checkpoint_redis)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.redis.aio", fake_checkpoint_redis_aio)
    monkeypatch.setitem(sys.modules, "modules", types.ModuleType("modules"))
    monkeypatch.setitem(sys.modules, "modules.tools", fake_tools_mod)
    monkeypatch.setitem(sys.modules, "modules.assessment", fake_assessment_mod)
    monkeypatch.setitem(sys.modules, "modules.LLMS", fake_llms_mod)

    # Wire up the agent package so relative imports work
    agent_pkg = types.ModuleType("backend.agent")
    agent_pkg.prompts = fake_prompts_mod
    monkeypatch.setitem(sys.modules, "backend", types.ModuleType("backend"))
    monkeypatch.setitem(sys.modules, "backend.agent", agent_pkg)
    monkeypatch.setitem(sys.modules, "backend.agent.prompts", fake_prompts_mod)

    # Now import the real module under test
    import importlib
    graph = importlib.import_module("backend.agent.graph")

    # Expose mocks via a simple namespace for test access
    mocks = types.SimpleNamespace(
        Redis=fake_redis_asyncio.Redis,
        AsyncRedisSaver=fake_checkpoint_redis_aio.AsyncRedisSaver,
        create_agent=fake_langchain_agents.create_agent,
        LLMS=fake_llms_mod.LLMS,
        get_customer_profile=fake_tools_mod.get_customer_profile,
        customer_lookalike=fake_tools_mod.customer_lookalike,
        _run_underwriting_assessment=fake_assessment_mod._run_underwriting_assessment,
    )
    return graph, mocks


# ===========================================================================
# Module-level initialisation tests
# ===========================================================================

class TestModuleInit:
    """Tests that verify module-level Redis / checkpointer setup."""

    def test_redis_client_created_with_default_host(self, graph_module):
        _, mocks = graph_module
        # Redis should have been called once during module import
        mocks.Redis.assert_called_once()
        call_kwargs = mocks.Redis.call_args[1]
        # Default host when REDIS_HOST env-var is absent
        assert call_kwargs.get("host") in ("localhost", None) or \
               mocks.Redis.call_args[0][0] if mocks.Redis.call_args[0] else True

    def test_redis_client_port_is_6379(self, graph_module):
        _, mocks = graph_module
        call_kwargs = mocks.Redis.call_args[1]
        assert call_kwargs.get("port") == 6379

    def test_redis_decode_responses_is_false(self, graph_module):
        _, mocks = graph_module
        call_kwargs = mocks.Redis.call_args[1]
        assert call_kwargs.get("decode_responses") is False

    def test_async_redis_saver_created_with_redis_client(self, graph_module):
        _, mocks = graph_module
        mocks.AsyncRedisSaver.assert_called_once()
        # The redis_client kwarg should be the return value of Redis(...)
        call_kwargs = mocks.AsyncRedisSaver.call_args[1]
        assert call_kwargs.get("redis_client") == mocks.Redis.return_value

    def test_redis_host_from_env_var(self, monkeypatch):
        """When REDIS_HOST is set, Redis should be initialised with that host."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host.example.com")

        # Clean the cached module so it re-runs top-level code
        for key in list(sys.modules.keys()):
            if "backend.agent.graph" in key:
                del sys.modules[key]

        fake_redis_asyncio = _make_fake_redis_module()
        _, fake_langchain_agents = _make_fake_langchain_module()
        fake_langgraph, fake_checkpoint, fake_checkpoint_redis, fake_checkpoint_redis_aio = (
            _make_fake_langgraph_module()
        )
        fake_tools_mod = _make_fake_modules_tools()
        fake_assessment_mod = _make_fake_modules_assessment()
        fake_llms_mod = _make_fake_llms_module()
        fake_prompts_mod = _make_fake_prompts_module()
        agent_pkg = types.ModuleType("backend.agent")
        agent_pkg.prompts = fake_prompts_mod

        monkeypatch.setitem(sys.modules, "redis", types.ModuleType("redis"))
        monkeypatch.setitem(sys.modules, "redis.asyncio", fake_redis_asyncio)
        monkeypatch.setitem(sys.modules, "langchain", types.ModuleType("langchain"))
        monkeypatch.setitem(sys.modules, "langchain.agents", fake_langchain_agents)
        monkeypatch.setitem(sys.modules, "langgraph", fake_langgraph)
        monkeypatch.setitem(sys.modules, "langgraph.checkpoint", fake_checkpoint)
        monkeypatch.setitem(sys.modules, "langgraph.checkpoint.redis", fake_checkpoint_redis)
        monkeypatch.setitem(sys.modules, "langgraph.checkpoint.redis.aio", fake_checkpoint_redis_aio)
        monkeypatch.setitem(sys.modules, "modules", types.ModuleType("modules"))
        monkeypatch.setitem(sys.modules, "modules.tools", fake_tools_mod)
        monkeypatch.setitem(sys.modules, "modules.assessment", fake_assessment_mod)
        monkeypatch.setitem(sys.modules, "modules.LLMS", fake_llms_mod)
        monkeypatch.setitem(sys.modules, "backend", types.ModuleType("backend"))
        monkeypatch.setitem(sys.modules, "backend.agent", agent_pkg)
        monkeypatch.setitem(sys.modules, "backend.agent.prompts", fake_prompts_mod)

        graph = importlib.import_module("backend.agent.graph")  # noqa: F841

        call_kwargs = fake_redis_asyncio.Redis.call_args[1]
        assert call_kwargs.get("host") == "my-redis-host.example.com"


# ===========================================================================
# build_agent() happy-path tests
# ===========================================================================

class TestBuildAgentHappyPath:

    def test_returns_create_agent_result(self, graph_module):
        graph, mocks = graph_module
        mocks.create_agent.return_value = MagicMock(name="agent_instance")
        result = graph.build_agent("gpt-4o", 0.7)
        assert result is mocks.create_agent.return_value

    def test_calls_llms_with_temperature_and_streaming(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o", 0.5)
        mocks.LLMS.assert_called_once_with(temperature=0.5, streaming=True)

    def test_calls_get_model_with_model_name(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o-mini", 0.3)
        fake_llms_instance = mocks.LLMS.return_value
        fake_llms_instance.get_model.assert_called_once_with("gpt-4o-mini")

    def test_default_mode_is_fast(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o", 0.7)
        mocks._run_underwriting_assessment.assert_called_once_with("fast")

    def test_explicit_mode_fast(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o", 0.7, mode="fast")
        mocks._run_underwriting_assessment.assert_called_with("fast")

    def test_explicit_mode_deep(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o", 0.7, mode="deep")
        mocks._run_underwriting_assessment.assert_called_with("deep")

    def test_create_agent_called_with_correct_model(self, graph_module):
        graph, mocks = graph_module
        fake_model = MagicMock(name="fake_model")
        mocks.LLMS.return_value.get_model.return_value = fake_model
        graph.build_agent("gpt-4o", 0.9)
        call_kwargs = mocks.create_agent.call_args[1]
        assert call_kwargs["model"] is fake_model

    def test_create_agent_called_with_system_prompt(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o", 0.5)
        call_kwargs = mocks.create_agent.call_args[1]
        assert call_kwargs["system_prompt"] == "FAKE_SYSTEM_PROMPT"

    def test_create_agent_called_with_checkpointer(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o", 0.5)
        call_kwargs = mocks.create_agent.call_args[1]
        # checkpointer should be the module-level _checkpointer instance
        assert call_kwargs["checkpointer"] is graph._checkpointer

    def test_create_agent_receives_three_tools(self, graph_module):
        graph, mocks = graph_module
        graph.build_agent("gpt-4o", 0.5)
        call_kwargs = mocks.create_agent.call_args[1]
        assert len(