"""
Test module for backend/agent/graph.py

What is tested:
- build_agent(): happy path with various model names, temperatures, and modes
- build_agent(): edge cases (boundary temperatures, unknown mode strings)
- build_agent(): error conditions (invalid model names, LLMS failures, tool construction failures)
- Module-level Redis client and checkpointer initialisation (via env-var patching)

Mocks used:
- langchain.agents.create_agent          → mock.patch
- redis.asyncio.Redis                    → mock.patch
- langgraph.checkpoint.redis.aio.AsyncRedisSaver → mock.patch
- modules.tools.get_customer_profile     → mock.patch (imported object)
- modules.tools.customer_lookalike       → mock.patch (imported object)
- modules.assessment._run_underwriting_assessment → mock.patch
- modules.LLMS.LLMS                      → mock.patch
- os.environ                             → monkeypatch / mock.patch.dict

TODOs:
- TODO: Integration test verifying the agent actually routes tool calls correctly
        requires a real (or containerised) Redis instance and LLM endpoint.
- TODO: Test checkpointer persistence behaviour across multiple build_agent() calls
        once Redis is migrated to an external service.
- TODO: Verify SYSTEM_PROMPT content is forwarded verbatim to create_agent;
        needs access to backend/prompts/SYSTEM_PROMPT definition.
"""

import importlib
import os
import sys
import types
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake module tree so graph.py can be imported
# without real Redis / LangChain / LangGraph installed in the test environment.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """
    Insert lightweight stubs for every third-party / sibling module that
    graph.py imports at module level, so the import itself succeeds inside
    each test without hitting real network or file-system resources.
    """
    stubs = {}

    # --- redis.asyncio ---
    fake_redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_cls = mock.MagicMock(name="Redis")
    fake_redis_asyncio.Redis = fake_redis_cls
    stubs["redis"] = types.ModuleType("redis")
    stubs["redis.asyncio"] = fake_redis_asyncio

    # --- langgraph.checkpoint.redis.aio ---
    fake_lg_cp = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_lg_cp.AsyncRedisSaver = mock.MagicMock(name="AsyncRedisSaver")
    stubs["langgraph"] = types.ModuleType("langgraph")
    stubs["langgraph.checkpoint"] = types.ModuleType("langgraph.checkpoint")
    stubs["langgraph.checkpoint.redis"] = types.ModuleType("langgraph.checkpoint.redis")
    stubs["langgraph.checkpoint.redis.aio"] = fake_lg_cp

    # --- langchain.agents ---
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_lc_agents.create_agent = mock.MagicMock(name="create_agent")
    stubs["langchain"] = types.ModuleType("langchain")
    stubs["langchain.agents"] = fake_lc_agents

    # --- .prompts (relative) → agent.prompts ---
    fake_prompts = types.ModuleType("agent.prompts")
    fake_prompts.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
    stubs["agent.prompts"] = fake_prompts

    # --- modules.tools ---
    fake_tools = types.ModuleType("modules.tools")
    fake_tools.get_customer_profile = mock.MagicMock(name="get_customer_profile")
    fake_tools.customer_lookalike = mock.MagicMock(name="customer_lookalike")
    stubs["modules"] = types.ModuleType("modules")
    stubs["modules.tools"] = fake_tools

    # --- modules.assessment ---
    fake_assessment = types.ModuleType("modules.assessment")
    fake_assessment._run_underwriting_assessment = mock.MagicMock(
        name="_run_underwriting_assessment",
        return_value=mock.MagicMock(name="assessment_tool"),
    )
    stubs["modules.assessment"] = fake_assessment

    # --- modules.LLMS ---
    fake_llms_mod = types.ModuleType("modules.LLMS")
    fake_llms_cls = mock.MagicMock(name="LLMS")
    fake_llms_mod.LLMS = fake_llms_cls
    stubs["modules.LLMS"] = fake_llms_mod

    return stubs


# ---------------------------------------------------------------------------
# Fixture: fresh import of graph module for every test
# ---------------------------------------------------------------------------

@pytest.fixture()
def graph_module(monkeypatch):
    """
    Patch sys.modules with fake stubs, then (re-)import agent.graph so that
    each test starts with a clean, fully-mocked module.
    """
    stubs = _make_fake_modules()

    # Remove any previously cached version of the module under test
    for key in list(sys.modules.keys()):
        if "agent.graph" in key or key == "agent.graph":
            del sys.modules[key]

    with mock.patch.dict(sys.modules, stubs):
        # Ensure REDIS_HOST has a predictable value
        monkeypatch.setenv("REDIS_HOST", "test-redis-host")

        # agent.graph uses relative imports → register parent package stub
        if "agent" not in sys.modules:
            agent_pkg = types.ModuleType("agent")
            sys.modules["agent"] = agent_pkg

        import agent.graph as graph  # noqa: PLC0415
        yield graph


# ---------------------------------------------------------------------------
# Convenience accessors (re-read from sys.modules inside each test)
# ---------------------------------------------------------------------------

def _create_agent_mock():
    return sys.modules["langchain.agents"].create_agent


def _llms_mock():
    return sys.modules["modules.LLMS"].LLMS


def _assessment_mock():
    return sys.modules["modules.assessment"]._run_underwriting_assessment


def _tools_mock():
    tools_mod = sys.modules["modules.tools"]
    return tools_mod.get_customer_profile, tools_mod.customer_lookalike


# ===========================================================================
# Tests – module-level initialisation
# ===========================================================================

class TestModuleInit:
    def test_redis_client_created_with_env_host(self, graph_module):
        """Redis() should be called with the REDIS_HOST env-var value."""
        redis_cls = sys.modules["redis.asyncio"].Redis
        redis_cls.assert_called_once()
        call_kwargs = redis_cls.call_args
        assert call_kwargs.kwargs.get("host") == "test-redis-host" or (
            call_kwargs.args and call_kwargs.args[0] == "test-redis-host"
        )

    def test_redis_client_port_is_6379(self, graph_module):
        redis_cls = sys.modules["redis.asyncio"].Redis
        call_kwargs = redis_cls.call_args
        assert call_kwargs.kwargs.get("port") == 6379

    def test_redis_decode_responses_false(self, graph_module):
        redis_cls = sys.modules["redis.asyncio"].Redis
        call_kwargs = redis_cls.call_args
        assert call_kwargs.kwargs.get("decode_responses") is False

    def test_checkpointer_created_with_redis_client(self, graph_module):
        saver_cls = sys.modules["langgraph.checkpoint.redis.aio"].AsyncRedisSaver
        saver_cls.assert_called_once()

    def test_redis_host_defaults_to_localhost(self, monkeypatch):
        """When REDIS_HOST is unset the host should fall back to 'localhost'."""
        monkeypatch.delenv("REDIS_HOST", raising=False)

        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]

        stubs = _make_fake_modules()
        with mock.patch.dict(sys.modules, stubs):
            if "agent" not in sys.modules:
                sys.modules["agent"] = types.ModuleType("agent")
            import agent.graph  # noqa: PLC0415

        redis_cls = stubs["redis.asyncio"].Redis
        call_kwargs = redis_cls.call_args
        host_value = call_kwargs.kwargs.get("host") or (
            call_kwargs.args[0] if call_kwargs.args else None
        )
        assert host_value == "localhost"


# ===========================================================================
# Tests – build_agent()
# ===========================================================================

class TestBuildAgentHappyPath:

    @pytest.mark.parametrize("model_name,temperature,mode", [
        ("gpt-4o", 0.0, "fast"),
        ("gpt-4o", 0.7, "deep"),
        ("gpt-3.5-turbo", 0.5, "fast"),
        ("claude-3-opus", 1.0, "deep"),
    ])
    def test_returns_create_agent_result(self, graph_module, model_name, temperature, mode):
        """build_agent should return whatever create_agent returns."""
        expected = mock.MagicMock(name="agent_instance")
        _create_agent_mock().return_value = expected

        result = graph_module.build_agent(model_name, temperature, mode)

        assert result is expected

    def test_llms_instantiated_with_correct_params(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.3, "fast")

        llms_cls = _llms_mock()
        llms_cls.assert_called_once_with(temperature=0.3, streaming=True)

    def test_get_model_called_with_model_name(self, graph_module):
        model_name = "gpt-4o-mini"
        graph_module.build_agent(model_name, 0.5, "fast")

        llms_instance = _llms_mock().return_value
        llms_instance.get_model.assert_called_once_with(model_name)

    def test_create_agent_called_with_model(self, graph_module):
        fake_model = mock.MagicMock(name="model")
        _llms_mock().return_value.get_model.return_value = fake_model

        graph_module.build_agent("gpt-4o", 0.0, "fast")

        create_agent = _create_agent_mock()
        call_kwargs = create_agent.call_args.kwargs
        assert call_kwargs["model"] is fake_model

    def test_create_agent_called_with_system_prompt(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.0, "fast")

        create_agent = _create_agent_mock()
        call_kwargs = create_agent.call_args.kwargs
        assert call_kwargs["system_prompt"] == "FAKE_SYSTEM_PROMPT"

    def test_create_agent_called_with_checkpointer(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.0, "fast")

        create_agent = _create_agent_mock()
        call_kwargs = create_agent.call_args.kwargs
        assert call_kwargs["checkpointer"] is graph_module._checkpointer

    def test_tools_list_has_three_elements(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.0, "fast")

        create_agent = _create_agent_mock()
        tools = create_agent.call_args.kwargs["tools"]
        assert len(tools) == 3

    def test_tools_contains_get_customer_profile(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.0, "fast")

        tools = _create_agent_mock().call_args.kwargs["tools"]
        get_cp, _ = _tools_mock()
        assert get_cp in tools

    def test_tools_contains_customer_lookalike(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.0, "fast")

        tools = _create_agent_mock().call_args.kwargs["tools"]
        _, cl = _tools_mock()
        assert cl in tools

    def test_tools_contains_assessment_result(self, graph_module):
        """The assessment tool should be the *return value* of _run_underwriting_assessment."""
        fake_assessment_tool = mock.MagicMock(name="assessment_tool_instance")
        _assessment_mock().return_value = fake_assessment_tool

        graph_module.build_agent("gpt-4o", 0.0, "fast")

        tools = _create_agent_mock().call_args.kwargs["tools"]
        assert fake_assessment_tool in tools

    def test_assessment_called_with_fast_mode(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.0, "fast")
        _assessment_mock().assert_called_with("fast")

    def test_assessment_called_with_deep_mode(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.0, "deep")
        _assessment_mock().assert_called_with("deep")

    def test_default_mode_is_fast(self, graph_module):
        """build_agent(model, temp) without mode kwarg should use 'fast'."""
        graph_module.build_agent("gpt-4o", 0.0)
        _assessment_mock().assert_called_with("fast")


# ===========================================================================
# Tests – edge / boundary cases
# ===========================================================================

class TestBuildAgentEdgeCases:

    def test_temperature_zero(self, graph_module):
        result = graph_module.build_agent("gpt-4o", 0.0, "fast")
        assert result is _create_agent_mock().return_value

    def test_temperature_one(self, graph_module):
        result = graph_module.build_agent("gpt-4o", 1.0, "fast")
        assert result is _create_agent_mock().return_value

    def test_temperature_very_small(self, graph_module):
        result = graph_module.build_agent("gpt-4o", 1e-10, "fast")
        assert result is _create_agent_mock().return_value

    def test_temperature_very_large(self, graph_module):
        """Boundary: extremely high temperature — build_agent itself shouldn't raise."""
        result = graph_module.build_agent("gpt-4o", 2.0, "fast")
        assert result is _create_agent_mock().return_value

    def test_unknown_mode_string_still_passes_to_assessment(self, graph_module):
        """build_agent does not validate mode; arbitrary strings are forwarded."""
        graph_module.build_agent("gpt-4o", 0.5, "ultra")
        _assessment_mock().assert_called_with("ultra")

    def test_empty_mode_string(self, graph_module):
        graph_module.build_agent("gpt-4o", 0.5, "")
        _assessment_mock().assert_called_with("")

    def test_empty_model_name_forwarded(self, graph_module):
        """build_agent does not validate model_name; empty string is forwarded."""
        graph_module.build_agent("", 0.5, "fast")
        _llms_mock().return_value.get_model.assert_called_once_with("")

    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-3.5-turbo",
        "claude-3-opus",
        "gemini-pro",
        "mistral-7b",
    ])
    def test_various_model