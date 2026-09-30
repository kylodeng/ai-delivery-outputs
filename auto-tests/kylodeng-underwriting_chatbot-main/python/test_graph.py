"""
Test module for backend/agent/graph.py

What is tested:
    - build_agent() function: happy path, edge cases, error conditions
    - Module-level Redis client and checkpointer initialisation
    - Correct wiring of model, tools, system prompt, and checkpointer

Mocks used:
    - unittest.mock.patch / MagicMock for:
        - redis.asyncio.Redis (prevents real Redis connections)
        - langgraph.checkpoint.redis.aio.AsyncRedisSaver (prevents real Redis calls)
        - langchain.agents.create_agent (prevents real LLM/agent creation)
        - modules.LLMS.LLMS (prevents real model instantiation)
        - modules.tools.get_customer_profile (imported callable)
        - modules.tools.customer_lookalike (imported callable)
        - modules.assessment._run_underwriting_assessment (prevents real ML calls)
        - backend.agent.prompts.SYSTEM_PROMPT

TODOs:
    - TODO: Integration test with a real (or test-container) Redis instance
    - TODO: Verify agent streaming behaviour once the agent interface is finalised
    - TODO: Test module-level Redis env-var override (REDIS_HOST) in isolation
            without reimporting the module (requires importlib.reload gymnastics)
"""

import importlib
import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a clean, fully-mocked module environment
# ---------------------------------------------------------------------------

def _make_module_mocks():
    """Return a dict of patch targets → MagicMock objects used across tests."""
    return {
        "redis_instance": MagicMock(name="redis_instance"),
        "checkpointer_instance": MagicMock(name="checkpointer_instance"),
        "agent_instance": MagicMock(name="agent_instance"),
        "llm_instance": MagicMock(name="llm_instance"),
        "model_instance": MagicMock(name="model_instance"),
    }


# ---------------------------------------------------------------------------
# Module-level fixture: patch heavy dependencies BEFORE importing graph.py
# so that the Redis client and checkpointer are never real.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_module_cache():
    """Remove cached graph module before each test to allow fresh imports."""
    for key in list(sys.modules.keys()):
        if "backend.agent.graph" in key or key.endswith("agent.graph"):
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if "backend.agent.graph" in key or key.endswith("agent.graph"):
            del sys.modules[key]


@pytest.fixture()
def mocks():
    return _make_module_mocks()


@pytest.fixture()
def patched_graph(mocks):
    """
    Import backend.agent.graph with all external dependencies patched.
    Returns (module, mocks_dict).
    """
    redis_mock_cls = MagicMock(return_value=mocks["redis_instance"])
    saver_mock_cls = MagicMock(return_value=mocks["checkpointer_instance"])
    create_agent_mock = MagicMock(return_value=mocks["agent_instance"])

    llms_instance = MagicMock()
    llms_instance.get_model.return_value = mocks["model_instance"]
    llms_mock_cls = MagicMock(return_value=llms_instance)
    mocks["llms_instance"] = llms_instance

    tool_profile = MagicMock(name="get_customer_profile")
    tool_lookalike = MagicMock(name="customer_lookalike")
    assessment_result = MagicMock(name="assessment_tool")
    run_assessment_mock = MagicMock(return_value=assessment_result)
    mocks["tool_profile"] = tool_profile
    mocks["tool_lookalike"] = tool_lookalike
    mocks["assessment_result"] = assessment_result
    mocks["run_assessment_mock"] = run_assessment_mock
    mocks["create_agent_mock"] = create_agent_mock
    mocks["llms_mock_cls"] = llms_mock_cls

    system_prompt_value = "MOCK_SYSTEM_PROMPT"

    with (
        patch("redis.asyncio.Redis", redis_mock_cls),
        patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", saver_mock_cls),
        patch("langchain.agents.create_agent", create_agent_mock),
        patch("modules.LLMS.LLMS", llms_mock_cls),
        patch("modules.tools.get_customer_profile", tool_profile),
        patch("modules.tools.customer_lookalike", tool_lookalike),
        patch("modules.assessment._run_underwriting_assessment", run_assessment_mock),
        patch.dict("os.environ", {"REDIS_HOST": "mock-redis-host"}),
    ):
        # Patch the SYSTEM_PROMPT that will be resolved at import time
        import backend.agent.prompts  # noqa: F401 – ensure package exists for patch
        with patch("backend.agent.graph.SYSTEM_PROMPT", system_prompt_value):
            import backend.agent.graph as graph_module  # noqa: E402
            mocks["system_prompt_value"] = system_prompt_value
            yield graph_module, mocks


# ---------------------------------------------------------------------------
# Tests – module initialisation
# ---------------------------------------------------------------------------

class TestModuleInitialisation:
    """Verify module-level Redis and checkpointer setup."""

    def test_redis_client_created(self, patched_graph):
        graph_module, mocks = patched_graph
        assert graph_module._redis_client is mocks["redis_instance"]

    def test_checkpointer_created(self, patched_graph):
        graph_module, mocks = patched_graph
        assert graph_module._checkpointer is mocks["checkpointer_instance"]

    def test_redis_host_env_var_forwarded(self, mocks):
        """REDIS_HOST env var should be passed to Redis constructor."""
        redis_mock_cls = MagicMock(return_value=mocks["redis_instance"])
        saver_mock_cls = MagicMock(return_value=mocks["checkpointer_instance"])

        llms_instance = MagicMock()
        llms_mock_cls = MagicMock(return_value=llms_instance)

        with (
            patch("redis.asyncio.Redis", redis_mock_cls),
            patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", saver_mock_cls),
            patch("langchain.agents.create_agent", MagicMock()),
            patch("modules.LLMS.LLMS", llms_mock_cls),
            patch("modules.tools.get_customer_profile", MagicMock()),
            patch("modules.tools.customer_lookalike", MagicMock()),
            patch("modules.assessment._run_underwriting_assessment", MagicMock()),
            patch.dict("os.environ", {"REDIS_HOST": "custom-redis-host"}),
        ):
            with patch("backend.agent.graph.SYSTEM_PROMPT", "PROMPT"):
                import backend.agent.graph  # noqa: F401

            redis_mock_cls.assert_called_once_with(
                host="custom-redis-host", port=6379, decode_responses=False
            )

    def test_redis_host_defaults_to_localhost(self, mocks):
        """When REDIS_HOST is absent, Redis should default to 'localhost'."""
        redis_mock_cls = MagicMock(return_value=mocks["redis_instance"])
        saver_mock_cls = MagicMock(return_value=mocks["checkpointer_instance"])

        env_without_redis = {k: v for k, v in os.environ.items() if k != "REDIS_HOST"}

        with (
            patch("redis.asyncio.Redis", redis_mock_cls),
            patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", saver_mock_cls),
            patch("langchain.agents.create_agent", MagicMock()),
            patch("modules.LLMS.LLMS", MagicMock(return_value=MagicMock())),
            patch("modules.tools.get_customer_profile", MagicMock()),
            patch("modules.tools.customer_lookalike", MagicMock()),
            patch("modules.assessment._run_underwriting_assessment", MagicMock()),
            patch.dict("os.environ", env_without_redis, clear=True),
        ):
            with patch("backend.agent.graph.SYSTEM_PROMPT", "PROMPT"):
                import backend.agent.graph  # noqa: F401

            redis_mock_cls.assert_called_once_with(
                host="localhost", port=6379, decode_responses=False
            )

    def test_checkpointer_receives_redis_client(self, mocks):
        """AsyncRedisSaver must be initialised with the Redis client."""
        redis_mock_cls = MagicMock(return_value=mocks["redis_instance"])
        saver_mock_cls = MagicMock(return_value=mocks["checkpointer_instance"])

        with (
            patch("redis.asyncio.Redis", redis_mock_cls),
            patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", saver_mock_cls),
            patch("langchain.agents.create_agent", MagicMock()),
            patch("modules.LLMS.LLMS", MagicMock(return_value=MagicMock())),
            patch("modules.tools.get_customer_profile", MagicMock()),
            patch("modules.tools.customer_lookalike", MagicMock()),
            patch("modules.assessment._run_underwriting_assessment", MagicMock()),
            patch.dict("os.environ", {"REDIS_HOST": "h"}),
        ):
            with patch("backend.agent.graph.SYSTEM_PROMPT", "PROMPT"):
                import backend.agent.graph  # noqa: F401

            saver_mock_cls.assert_called_once_with(
                redis_client=mocks["redis_instance"]
            )


# ---------------------------------------------------------------------------
# Tests – build_agent happy paths
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_instance(self, patched_graph):
        graph_module, mocks = patched_graph
        result = graph_module.build_agent("gpt-4o", 0.7)
        assert result is mocks["agent_instance"]

    def test_default_mode_is_fast(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.5)
        mocks["run_assessment_mock"].assert_called_with("fast")

    def test_mode_deep_passed_to_assessment(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.5, mode="deep")
        mocks["run_assessment_mock"].assert_called_with("deep")

    def test_mode_fast_passed_to_assessment(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.5, mode="fast")
        mocks["run_assessment_mock"].assert_called_with("fast")

    def test_llms_instantiated_with_correct_temperature(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.9)
        mocks["llms_mock_cls"].assert_called_once_with(temperature=0.9, streaming=True)

    def test_llms_instantiated_with_streaming_true(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("claude-3", 0.3)
        _, kwargs = mocks["llms_mock_cls"].call_args
        assert kwargs["streaming"] is True

    def test_get_model_called_with_model_name(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o-mini", 0.0)
        mocks["llms_instance"].get_model.assert_called_once_with("gpt-4o-mini")

    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-4o-mini",
        "claude-3-5-sonnet",
        "claude-3-haiku",
    ])
    def test_various_model_names(self, patched_graph, model_name):
        graph_module, mocks = patched_graph
        result = graph_module.build_agent(model_name, 0.5)
        mocks["llms_instance"].get_model.assert_called_with(model_name)
        assert result is mocks["agent_instance"]

    @pytest.mark.parametrize("temperature", [0.0, 0.1, 0.5, 0.9, 1.0])
    def test_various_temperatures(self, patched_graph, temperature):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", temperature)
        mocks["llms_mock_cls"].assert_called_with(temperature=temperature, streaming=True)

    def test_create_agent_called_with_correct_args(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.7)

        mocks["create_agent_mock"].assert_called_once()
        _, kwargs = mocks["create_agent_mock"].call_args

        assert kwargs["model"] is mocks["model_instance"]
        assert kwargs["system_prompt"] == mocks["system_prompt_value"]
        assert kwargs["checkpointer"] is mocks["checkpointer_instance"]

    def test_tools_list_has_three_items(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.7)

        _, kwargs = mocks["create_agent_mock"].call_args
        tools = kwargs["tools"]
        assert len(tools) == 3

    def test_tools_list_contains_customer_profile(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.7)

        _, kwargs = mocks["create_agent_mock"].call_args
        assert mocks["tool_profile"] in kwargs["tools"]

    def test_tools_list_contains_customer_lookalike(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.7)

        _, kwargs = mocks["create_agent_mock"].call_args
        assert mocks["tool_lookalike"] in kwargs["tools"]

    def test_tools_list_contains_assessment_result(self, patched_graph):
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.7, mode="fast")

        _, kwargs = mocks["create_agent_mock"].call_args
        assert mocks["assessment_result"] in kwargs["tools"]

    def test_checkpointer_shared_across_calls(self, patched_graph):
        """The module-level checkpointer must be reused, not recreated."""
        graph_module, mocks = patched_graph
        graph_module.build_agent("gpt-4o", 0.7)
        graph_module.build_agent("gpt-4o", 0.3)

        calls = mocks["create_agent_mock"].call_args