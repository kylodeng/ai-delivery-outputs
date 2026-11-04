"""
Tests for backend/agent/graph.py

What is tested:
- build_agent(): happy path with various model names, temperatures, and modes
- build_agent(): edge cases (boundary temperatures, unknown mode strings)
- build_agent(): error conditions (invalid model name, LLMS/create_agent failures)
- Module-level Redis client and checkpointer initialisation (env var handling)
- Tool list construction (correct tools passed to create_agent)

Mocks used:
- backend.agent.graph.Redis          — prevents real Redis connections
- backend.agent.graph.AsyncRedisSaver — prevents real Redis checkpointer setup
- backend.agent.graph.LLMS           — prevents real LLM instantiation
- backend.agent.graph.create_agent   — prevents real agent construction
- backend.agent.graph.get_customer_profile  — imported tool stub
- backend.agent.graph.customer_lookalike    — imported tool stub
- backend.agent.graph._run_underwriting_assessment — imported tool stub
- os.environ                          — controlled via monkeypatch

TODOs:
- TODO: Integration test with a real (or docker-based) Redis instance to verify
        checkpointer persistence behaviour across calls.
- TODO: Verify that the agent returned by build_agent() can actually invoke tools
        end-to-end once the LLM/tool contracts are fully stabilised.
- TODO: Test streaming behaviour of the model (requires LLM streaming mock).
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

FAKE_MODEL_INSTANCE = MagicMock(name="FakeLLMModel")
FAKE_AGENT_INSTANCE = MagicMock(name="FakeAgent")

FAST_TOOL = MagicMock(name="fast_assessment_tool")
DEEP_TOOL = MagicMock(name="deep_assessment_tool")


def _make_assessment_side_effect(fast_tool, deep_tool):
    """Return a side_effect callable for _run_underwriting_assessment."""
    def _side_effect(mode):
        return deep_tool if mode == "deep" else fast_tool
    return _side_effect


# ---------------------------------------------------------------------------
# Fixture: patch every external dependency before the module is imported
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def patched_graph(monkeypatch):
    """
    Re-import backend.agent.graph with all external dependencies patched.
    Returns a namespace with the module and all relevant mocks.
    """
    # Remove cached module so each test gets a fresh import
    for key in list(sys.modules.keys()):
        if "backend.agent.graph" in key or key == "agent.graph":
            del sys.modules[key]

    mock_redis_instance = AsyncMock(name="RedisInstance")
    mock_redis_cls = MagicMock(return_value=mock_redis_instance, name="RedisCls")

    mock_checkpointer_instance = MagicMock(name="CheckpointerInstance")
    mock_saver_cls = MagicMock(
        return_value=mock_checkpointer_instance, name="AsyncRedisSaverCls"
    )

    mock_llms_instance = MagicMock(name="LLMSInstance")
    mock_llms_instance.get_model.return_value = FAKE_MODEL_INSTANCE
    mock_llms_cls = MagicMock(return_value=mock_llms_instance, name="LLMSCls")

    mock_create_agent = MagicMock(return_value=FAKE_AGENT_INSTANCE, name="create_agent")

    mock_get_customer_profile = MagicMock(name="get_customer_profile")
    mock_customer_lookalike = MagicMock(name="customer_lookalike")
    mock_run_assessment = MagicMock(
        side_effect=_make_assessment_side_effect(FAST_TOOL, DEEP_TOOL),
        name="_run_underwriting_assessment",
    )
    mock_system_prompt = "MOCK_SYSTEM_PROMPT"

    patches = {
        "redis.asyncio.Redis": mock_redis_cls,
        "langgraph.checkpoint.redis.aio.AsyncRedisSaver": mock_saver_cls,
        "langchain.agents.create_agent": mock_create_agent,
        "modules.tools.get_customer_profile": mock_get_customer_profile,
        "modules.tools.customer_lookalike": mock_customer_lookalike,
        "modules.assessment._run_underwriting_assessment": mock_run_assessment,
        "modules.LLMS.LLMS": mock_llms_cls,
    }

    with patch.dict("os.environ", {"REDIS_HOST": "mock-redis-host"}, clear=False):
        with patch("redis.asyncio.Redis", mock_redis_cls), \
             patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", mock_saver_cls), \
             patch("langchain.agents.create_agent", mock_create_agent), \
             patch("modules.tools.get_customer_profile", mock_get_customer_profile), \
             patch("modules.tools.customer_lookalike", mock_customer_lookalike), \
             patch("modules.assessment._run_underwriting_assessment", mock_run_assessment), \
             patch("modules.LLMS.LLMS", mock_llms_cls):

            # Build a fake package hierarchy so the relative import works
            # without a real package installation.
            agent_pkg = types.ModuleType("agent")
            agent_pkg.__path__ = []
            sys.modules.setdefault("agent", agent_pkg)

            prompts_mod = types.ModuleType("agent.prompts")
            prompts_mod.SYSTEM_PROMPT = mock_system_prompt
            sys.modules["agent.prompts"] = prompts_mod

            tools_mod = types.ModuleType("modules.tools")
            tools_mod.get_customer_profile = mock_get_customer_profile
            tools_mod.customer_lookalike = mock_customer_lookalike
            sys.modules["modules.tools"] = tools_mod

            assessment_mod = types.ModuleType("modules.assessment")
            assessment_mod._run_underwriting_assessment = mock_run_assessment
            sys.modules["modules.assessment"] = assessment_mod

            llms_mod = types.ModuleType("modules.LLMS")
            llms_mod.LLMS = mock_llms_cls
            sys.modules["modules.LLMS"] = llms_mod

            redis_asyncio_mod = types.ModuleType("redis.asyncio")
            redis_asyncio_mod.Redis = mock_redis_cls
            sys.modules.setdefault("redis", types.ModuleType("redis"))
            sys.modules["redis.asyncio"] = redis_asyncio_mod

            langgraph_mod = types.ModuleType("langgraph")
            langgraph_checkpoint_mod = types.ModuleType("langgraph.checkpoint")
            langgraph_redis_mod = types.ModuleType("langgraph.checkpoint.redis")
            langgraph_aio_mod = types.ModuleType("langgraph.checkpoint.redis.aio")
            langgraph_aio_mod.AsyncRedisSaver = mock_saver_cls
            sys.modules.setdefault("langgraph", langgraph_mod)
            sys.modules.setdefault("langgraph.checkpoint", langgraph_checkpoint_mod)
            sys.modules.setdefault("langgraph.checkpoint.redis", langgraph_redis_mod)
            sys.modules["langgraph.checkpoint.redis.aio"] = langgraph_aio_mod

            langchain_mod = types.ModuleType("langchain")
            langchain_agents_mod = types.ModuleType("langchain.agents")
            langchain_agents_mod.create_agent = mock_create_agent
            sys.modules.setdefault("langchain", langchain_mod)
            sys.modules["langchain.agents"] = langchain_agents_mod

            # Now import the module under test
            spec = importlib.util.spec_from_file_location(
                "agent.graph",
                os.path.join(os.path.dirname(__file__), "backend", "agent", "graph.py"),
            )
            if spec is None or spec.loader is None:
                # Fallback: construct module manually from source
                graph_mod = types.ModuleType("agent.graph")
                graph_mod.__package__ = "agent"
                source_path = os.path.join(
                    os.path.dirname(__file__), "backend", "agent", "graph.py"
                )
                try:
                    with open(source_path) as fh:
                        source = fh.read()
                    exec(compile(source, source_path, "exec"), graph_mod.__dict__)
                except FileNotFoundError:
                    # Source not on disk during unit-test CI — build a minimal stub
                    graph_mod.build_agent = None
            else:
                graph_mod = importlib.util.module_from_spec(spec)
                graph_mod.__package__ = "agent"
                spec.loader.exec_module(graph_mod)

            sys.modules["agent.graph"] = graph_mod

            yield types.SimpleNamespace(
                mod=graph_mod,
                redis_cls=mock_redis_cls,
                redis_instance=mock_redis_instance,
                saver_cls=mock_saver_cls,
                checkpointer=mock_checkpointer_instance,
                llms_cls=mock_llms_cls,
                llms_instance=mock_llms_instance,
                create_agent=mock_create_agent,
                get_customer_profile=mock_get_customer_profile,
                customer_lookalike=mock_customer_lookalike,
                run_assessment=mock_run_assessment,
                system_prompt=mock_system_prompt,
            )


# ---------------------------------------------------------------------------
# Convenience fixture that always resets mock call counts
# ---------------------------------------------------------------------------

@pytest.fixture()
def ctx(patched_graph):
    patched_graph.create_agent.reset_mock()
    patched_graph.llms_cls.reset_mock()
    patched_graph.llms_instance.get_model.reset_mock()
    patched_graph.run_assessment.reset_mock()
    return patched_graph


# ===========================================================================
# Tests — Module-level initialisation
# ===========================================================================


class TestModuleLevelInitialisation:
    """Verify that Redis client and checkpointer are created at import time."""

    def test_redis_client_created(self, patched_graph):
        """Redis() must have been called once during module import."""
        patched_graph.redis_cls.assert_called_once()

    def test_redis_host_from_env(self, patched_graph):
        """The REDIS_HOST env var must be forwarded to Redis()."""
        _, kwargs = patched_graph.redis_cls.call_args
        assert kwargs.get("host") == "mock-redis-host"

    def test_redis_port_is_6379(self, patched_graph):
        """Port must default to 6379."""
        _, kwargs = patched_graph.redis_cls.call_args
        assert kwargs.get("port") == 6379

    def test_redis_decode_responses_false(self, patched_graph):
        """decode_responses must be False (binary checkpointing)."""
        _, kwargs = patched_graph.redis_cls.call_args
        assert kwargs.get("decode_responses") is False

    def test_checkpointer_created_with_redis_client(self, patched_graph):
        """AsyncRedisSaver must receive the Redis instance."""
        patched_graph.saver_cls.assert_called_once_with(
            redis_client=patched_graph.redis_instance
        )

    def test_module_exposes_build_agent(self, patched_graph):
        """build_agent must be a callable exported from the module."""
        assert callable(patched_graph.mod.build_agent)


class TestRedisHostDefault:
    """Verify fallback when REDIS_HOST is not set."""

    def test_redis_host_defaults_to_localhost(self):
        """When REDIS_HOST is absent Redis must connect to localhost."""
        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]

        mock_redis_cls = MagicMock()
        mock_redis_instance = AsyncMock()
        mock_redis_cls.return_value = mock_redis_instance
        mock_saver_cls = MagicMock()

        env_without_redis = {k: v for k, v in os.environ.items() if k != "REDIS_HOST"}

        with patch.dict("os.environ", env_without_redis, clear=True):
            with patch("redis.asyncio.Redis", mock_redis_cls), \
                 patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", mock_saver_cls):
                # Reload module via exec to pick up clean env
                mod = types.ModuleType("agent.graph_default_host")
                mod.__package__ = "agent"
                source_path = os.path.join(
                    os.path.dirname(__file__), "backend", "agent", "graph.py"
                )
                try:
                    with open(source_path) as fh:
                        source = fh.read()
                    exec(compile(source, source_path, "exec"), mod.__dict__)
                    _, kwargs = mock_redis_cls.call_args
                    assert kwargs.get("host") == "localhost"
                except FileNotFoundError:
                    pytest.skip("Source file not found on disk; skipping env default test.")


# ===========================================================================
# Tests — build_agent() happy paths
# ===========================================================================


class TestBuildAgentHappyPath:

    def test_returns_agent_instance(self, ctx):
        """build_agent must return whatever create_agent returns."""
        ctx.create_agent.return_value = FAKE_AGENT_INSTANCE
        result = ctx.mod.build_agent("gpt-4o", 0.5)
        assert result is FAKE_AGENT_INSTANCE

    def test_llms_instantiated_with_temperature_and_streaming(self, ctx):
        """LLMS must be called with the supplied temperature and streaming=True."""
        ctx.mod.build_agent("gpt-4o", 0.7)
        ctx.llms_cls.assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, ctx):
        """LLMS().get_model must be called with the supplied model name."""
        ctx.mod.build_agent("gpt-4o", 0.5)
        ctx.llms_instance.get_model.assert_called_once_with("gpt-4o")

    def test_create_agent_called_with_correct_model(self, ctx):
        """create_agent must receive the model returned by get_model."""
        ctx.llms_instance.get_model.return_value = FAKE_MODEL_INSTANCE
        ctx.mod.build_agent("gpt-4o", 0.5)
        call_kwargs = ctx.create_agent.call_args.kwargs
        assert call_kwargs["model"] is FAKE_MODEL_INSTANCE

    def test_create_agent_receives_system_prompt(self, ctx):
        """create_agent must receive the SYSTEM_PROMPT constant."""
        ctx.mod.build_agent("gpt-4o", 0.5)
        call_kwargs = ctx.create_agent.call_args.kwargs
        assert call_kwargs["system_prompt"] == ctx.system_prompt

    def test_create_agent_receives_checkpointer(self, ctx):
        """create_agent must receive the module-level _checkpointer."""
        ctx.mod.build_agent("gpt-4o", 0.5)
        call_kwargs = ctx.create_agent.call_args.kwargs
        assert call_kwargs["checkpointer"] is ctx.checkpointer

    def test_tools_list_has_three_items(self, ctx):
        """create_agent must receive exactly three tools