"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() happy path with various model_name / temperature / mode combinations
- build_agent() default mode ("fast") behaviour
- build_agent() with "deep" mode
- build_agent() propagates correct arguments to LLMS, create_agent, and tool factories
- Module-level Redis client and checkpointer initialisation (env-var driven host)
- Edge cases: empty string model name, boundary temperatures (0.0, 1.0, negative, >1)
- Error conditions: LLMS.get_model raises, create_agent raises, _run_underwriting_assessment raises

Mocks used:
- langchain.agents.create_agent            → unittest.mock.MagicMock / patch
- redis.asyncio.Redis                      → unittest.mock.MagicMock / patch
- langgraph.checkpoint.redis.aio.AsyncRedisSaver → unittest.mock.MagicMock / patch
- modules.tools.get_customer_profile       → sentinel / patch
- modules.tools.customer_lookalike         → sentinel / patch
- modules.assessment._run_underwriting_assessment → unittest.mock.MagicMock / patch
- modules.LLMS.LLMS                        → unittest.mock.MagicMock / patch

TODOs:
- TODO: Integration test requiring a live Redis instance — stubbed below
- TODO: Verify exact SYSTEM_PROMPT value injected into create_agent — needs prompts fixture
- TODO: Test async checkpoint setup/teardown if AsyncRedisSaver exposes async lifecycle methods
"""

import importlib
import os
import sys
from unittest.mock import MagicMock, patch, call, sentinel

import pytest


# ---------------------------------------------------------------------------
# Helpers: build a clean import of the module under test with all
# heavy dependencies replaced so the module-level code does not fail.
# ---------------------------------------------------------------------------

MOCK_SYSTEM_PROMPT = "You are a test underwriting agent."


def _make_patches():
    """Return a dict of {dotted_target: MagicMock} for every external dep."""
    mock_redis_instance = MagicMock(name="redis_instance")
    mock_redis_cls = MagicMock(return_value=mock_redis_instance, name="Redis")

    mock_checkpointer_instance = MagicMock(name="checkpointer_instance")
    mock_checkpointer_cls = MagicMock(
        return_value=mock_checkpointer_instance, name="AsyncRedisSaver"
    )

    mock_create_agent = MagicMock(name="create_agent")
    mock_get_customer_profile = MagicMock(name="get_customer_profile")
    mock_customer_lookalike = MagicMock(name="customer_lookalike")
    mock_run_underwriting = MagicMock(
        name="_run_underwriting_assessment", return_value=MagicMock(name="assessment_tool")
    )

    mock_llms_instance = MagicMock(name="llms_instance")
    mock_llms_cls = MagicMock(return_value=mock_llms_instance, name="LLMS")

    return {
        "redis.asyncio.Redis": mock_redis_cls,
        "langgraph.checkpoint.redis.aio.AsyncRedisSaver": mock_checkpointer_cls,
        "langchain.agents.create_agent": mock_create_agent,
        "modules.tools.get_customer_profile": mock_get_customer_profile,
        "modules.tools.customer_lookalike": mock_customer_lookalike,
        "modules.assessment._run_underwriting_assessment": mock_run_underwriting,
        "modules.LLMS.LLMS": mock_llms_cls,
        "agent.prompts.SYSTEM_PROMPT": MOCK_SYSTEM_PROMPT,
    }, {
        "redis_cls": mock_redis_cls,
        "redis_instance": mock_redis_instance,
        "checkpointer_cls": mock_checkpointer_cls,
        "checkpointer_instance": mock_checkpointer_instance,
        "create_agent": mock_create_agent,
        "get_customer_profile": mock_get_customer_profile,
        "customer_lookalike": mock_customer_lookalike,
        "run_underwriting": mock_run_underwriting,
        "llms_cls": mock_llms_cls,
        "llms_instance": mock_llms_instance,
    }


# ---------------------------------------------------------------------------
# Fixture: fresh module import with all patches applied
# ---------------------------------------------------------------------------

@pytest.fixture()
def graph_module(monkeypatch):
    """
    Import backend/agent/graph.py in isolation with all external deps mocked.
    Re-imports the module fresh each time to honour env-var changes.
    """
    patches, mocks = _make_patches()

    active_patches = []
    for target, mock_obj in patches.items():
        p = patch(target, mock_obj)
        p.start()
        active_patches.append(p)

    # Remove cached module so we get a fresh import
    for key in list(sys.modules.keys()):
        if "agent.graph" in key or key == "agent.graph":
            del sys.modules[key]

    try:
        import agent.graph as graph
        yield graph, mocks
    finally:
        for p in active_patches:
            p.stop()
        for key in list(sys.modules.keys()):
            if "agent.graph" in key or key == "agent.graph":
                del sys.modules[key]


# ---------------------------------------------------------------------------
# Convenience fixture that also returns the mocks dict directly
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_mocks(graph_module):
    graph, mocks = graph_module
    return graph, mocks


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------

class TestModuleLevelInit:
    def test_redis_client_created_with_localhost_default(self, monkeypatch):
        """Redis should default to 'localhost' when REDIS_HOST is not set."""
        monkeypatch.delenv("REDIS_HOST", raising=False)

        patches, mocks = _make_patches()
        active = [patch(t, m).start() for t, m in patches.items()]
        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]
        try:
            import agent.graph  # noqa: F401
            mocks["redis_cls"].assert_called_once_with(
                host="localhost", port=6379, decode_responses=False
            )
        finally:
            for p in active:
                p.stop()
            for key in list(sys.modules.keys()):
                if "agent.graph" in key:
                    del sys.modules[key]

    def test_redis_client_uses_env_var_host(self, monkeypatch):
        """Redis should use REDIS_HOST env var when present."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")

        patches, mocks = _make_patches()
        active = [patch(t, m).start() for t, m in patches.items()]
        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]
        try:
            import agent.graph  # noqa: F401
            mocks["redis_cls"].assert_called_once_with(
                host="my-redis-host", port=6379, decode_responses=False
            )
        finally:
            for p in active:
                p.stop()
            for key in list(sys.modules.keys()):
                if "agent.graph" in key:
                    del sys.modules[key]

    def test_checkpointer_created_with_redis_client(self, graph_module):
        """AsyncRedisSaver must receive the Redis client instance."""
        _, mocks = graph_module
        mocks["checkpointer_cls"].assert_called_once_with(
            redis_client=mocks["redis_instance"]
        )

    def test_module_exposes_build_agent(self, graph_module):
        graph, _ = graph_module
        assert callable(graph.build_agent)


# ---------------------------------------------------------------------------
# build_agent — happy path
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:
    def test_returns_create_agent_result(self, agent_mocks):
        graph, mocks = agent_mocks
        expected = MagicMock(name="agent_result")
        mocks["create_agent"].return_value = expected

        result = graph.build_agent("gpt-4o", 0.7)

        assert result is expected

    def test_llms_instantiated_with_correct_args(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.5)

        mocks["llms_cls"].assert_called_once_with(temperature=0.5, streaming=True)

    def test_get_model_called_with_model_name(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("claude-3", 0.3)

        mocks["llms_instance"].get_model.assert_called_once_with("claude-3")

    def test_default_mode_is_fast(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7)

        mocks["run_underwriting"].assert_called_once_with("fast")

    def test_fast_mode_explicit(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7, mode="fast")

        mocks["run_underwriting"].assert_called_once_with("fast")

    def test_deep_mode(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7, mode="deep")

        mocks["run_underwriting"].assert_called_once_with("deep")

    def test_create_agent_receives_model(self, agent_mocks):
        graph, mocks = agent_mocks
        fake_model = MagicMock(name="model")
        mocks["llms_instance"].get_model.return_value = fake_model

        graph.build_agent("gpt-4o", 0.7)

        call_kwargs = mocks["create_agent"].call_args
        assert call_kwargs.kwargs["model"] is fake_model

    def test_create_agent_receives_system_prompt(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7)

        call_kwargs = mocks["create_agent"].call_args
        assert call_kwargs.kwargs["system_prompt"] == MOCK_SYSTEM_PROMPT

    def test_create_agent_receives_checkpointer(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7)

        call_kwargs = mocks["create_agent"].call_args
        assert call_kwargs.kwargs["checkpointer"] is mocks["checkpointer_instance"]

    def test_create_agent_tools_list_has_three_items(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7)

        call_kwargs = mocks["create_agent"].call_args
        tools = call_kwargs.kwargs["tools"]
        assert len(tools) == 3

    def test_create_agent_tools_contains_get_customer_profile(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7)

        tools = mocks["create_agent"].call_args.kwargs["tools"]
        assert mocks["get_customer_profile"] in tools

    def test_create_agent_tools_contains_customer_lookalike(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.7)

        tools = mocks["create_agent"].call_args.kwargs["tools"]
        assert mocks["customer_lookalike"] in tools

    def test_create_agent_tools_contains_assessment_tool(self, agent_mocks):
        graph, mocks = agent_mocks
        assessment_tool = MagicMock(name="assessment_tool_instance")
        mocks["run_underwriting"].return_value = assessment_tool

        graph.build_agent("gpt-4o", 0.7)

        tools = mocks["create_agent"].call_args.kwargs["tools"]
        assert assessment_tool in tools


# ---------------------------------------------------------------------------
# build_agent — parameterised inputs from synthetic data
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model_name, temperature, mode",
    [
        ("gpt-4o", 0.0, "fast"),
        ("gpt-4o", 1.0, "fast"),
        ("gpt-4o", 0.7, "deep"),
        ("claude-3-opus", 0.5, "fast"),
        ("claude-3-opus", 0.5, "deep"),
        ("gemini-pro", 0.3, "fast"),
        ("gemini-pro", 0.9, "deep"),
    ],
)
def test_build_agent_parametrised(model_name, temperature, mode, agent_mocks):
    """build_agent should succeed for all supported model/temp/mode combos."""
    graph, mocks = agent_mocks
    graph.build_agent(model_name, temperature, mode)

    mocks["llms_cls"].assert_called_with(temperature=temperature, streaming=True)
    mocks["llms_instance"].get_model.assert_called_with(model_name)
    mocks["run_underwriting"].assert_called_with(mode)
    mocks["create_agent"].assert_called()


# ---------------------------------------------------------------------------
# build_agent — boundary / edge cases
# ---------------------------------------------------------------------------

class TestBuildAgentBoundaryValues:
    def test_temperature_zero(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.0)
        mocks["llms_cls"].assert_called_once_with(temperature=0.0, streaming=True)

    def test_temperature_one(self, agent_mocks):
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 1.0)
        mocks["llms_cls"].assert_called_once_with(temperature=1.0, streaming=True)

    def test_temperature_negative_passed_through(self, agent_mocks):
        """build_agent itself does no validation; negative temp is forwarded."""
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", -0.1)
        mocks["llms_cls"].assert_called_once_with(temperature=-0.1, streaming=True)

    def test_temperature_greater_than_one_passed_through(self, agent_mocks):
        """build_agent itself does no validation; temp > 1 is forwarded."""
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 2.0)
        mocks["llms_cls"].assert_called_once_with(temperature=2.0, streaming=True)

    def test_empty_model_name_passed_through(self, agent_mocks):
        """An empty model name is forwarded; downstream raises if invalid."""
        graph, mocks = agent_mocks
        graph.build_agent("", 0.5)
        mocks["llms_instance"].get_model.assert_called_once_with("")

    def test_unknown_mode_passed_through(self, agent_mocks):
        """An unrecognised mode is forwarded to _run_underwriting_assessment."""
        graph, mocks = agent_mocks
        graph.build_agent("gpt-4o", 0.5, mode="turbo")
        mocks["