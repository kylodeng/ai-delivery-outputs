"""
Test module for backend/agent/graph.py

What is tested:
    - build_agent() happy path with valid model_name, temperature, and mode combinations
    - build_agent() with default mode ("fast")
    - build_agent() with "deep" mode
    - build_agent() error propagation when LLMS.get_model() raises
    - build_agent() error propagation when create_agent raises
    - Tools list construction (correct tools passed to create_agent)
    - SYSTEM_PROMPT is forwarded to create_agent
    - _checkpointer is forwarded to create_agent
    - Temperature boundary values (0.0, 1.0, edge floats)
    - Invalid / empty model_name strings

Mocks used:
    - langchain.agents.create_agent          (patched at backend.agent.graph.create_agent)
    - redis.asyncio.Redis                    (patched at backend.agent.graph.Redis)
    - langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched at backend.agent.graph.AsyncRedisSaver)
    - modules.LLMS.LLMS                      (patched at backend.agent.graph.LLMS)
    - modules.tools.get_customer_profile     (patched at backend.agent.graph.get_customer_profile)
    - modules.tools.customer_lookalike       (patched at backend.agent.graph.customer_lookalike)
    - modules.assessment._run_underwriting_assessment (patched at backend.agent.graph._run_underwriting_assessment)
    - os.environ                             (patched via monkeypatch)

TODOs:
    - TODO: Integration test verifying the Redis checkpointer actually persists state
            across two separate agent invocations (requires a real / containerised Redis).
    - TODO: Test that the agent graph produces correct tool-call sequences given a sample
            underwriting conversation (requires full LangGraph runtime + LLM stub).
    - TODO: Verify AsyncRedisSaver.setup() is called before the agent is used
            (depends on caller / lifespan code not present in graph.py).
"""

import importlib
import sys
import types
import os
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – reload the module under test so that module-level side-effects
# (Redis client creation) are exercised under controlled conditions.
# ---------------------------------------------------------------------------

MODULE_PATH = "backend.agent.graph"


def _reload_graph_module():
    """Force a fresh import of backend.agent.graph so module-level code runs."""
    if MODULE_PATH in sys.modules:
        del sys.modules[MODULE_PATH]
    return importlib.import_module(MODULE_PATH)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_module_level_dependencies(monkeypatch):
    """
    Patch all heavy / network-touching dependencies *before* the module is
    imported so that module-level Redis construction never hits a real server.
    This fixture is autouse so every test benefits from it automatically.
    """
    mock_redis_instance = MagicMock(name="redis_instance")
    mock_redis_cls = MagicMock(name="Redis", return_value=mock_redis_instance)

    mock_saver_instance = MagicMock(name="checkpointer_instance")
    mock_saver_cls = MagicMock(name="AsyncRedisSaver", return_value=mock_saver_instance)

    patches = [
        patch("redis.asyncio.Redis", mock_redis_cls),
        patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", mock_saver_cls),
        patch("backend.agent.graph.Redis", mock_redis_cls),
        patch("backend.agent.graph.AsyncRedisSaver", mock_saver_cls),
    ]
    for p in patches:
        p.start()

    yield {
        "redis_cls": mock_redis_cls,
        "redis_instance": mock_redis_instance,
        "saver_cls": mock_saver_cls,
        "saver_instance": mock_saver_instance,
    }

    for p in patches:
        p.stop()

    # Clean up cached module so next test gets a fresh import
    sys.modules.pop(MODULE_PATH, None)


@pytest.fixture()
def mock_llms_cls():
    with patch("backend.agent.graph.LLMS") as mock_cls:
        mock_model = MagicMock(name="llm_model")
        mock_cls.return_value.get_model.return_value = mock_model
        yield mock_cls, mock_model


@pytest.fixture()
def mock_create_agent():
    with patch("backend.agent.graph.create_agent") as mock_ca:
        mock_agent = MagicMock(name="agent_instance")
        mock_ca.return_value = mock_agent
        yield mock_ca, mock_agent


@pytest.fixture()
def mock_tools():
    with patch("backend.agent.graph.get_customer_profile") as mock_gcp, \
         patch("backend.agent.graph.customer_lookalike") as mock_cl, \
         patch("backend.agent.graph._run_underwriting_assessment") as mock_rua:
        mock_rua.return_value = MagicMock(name="assessment_tool")
        yield {
            "get_customer_profile": mock_gcp,
            "customer_lookalike": mock_cl,
            "_run_underwriting_assessment": mock_rua,
            "assessment_tool": mock_rua.return_value,
        }


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------

class TestModuleLevelInitialisation:
    """Verify that importing the module creates the Redis client and checkpointer."""

    def test_redis_client_created_with_default_host(self, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)

        with patch("backend.agent.graph.Redis") as mock_redis, \
             patch("backend.agent.graph.AsyncRedisSaver") as mock_saver:
            mock_redis.return_value = MagicMock()
            mock_saver.return_value = MagicMock()

            sys.modules.pop(MODULE_PATH, None)
            importlib.import_module(MODULE_PATH)

            mock_redis.assert_called_once_with(
                host="localhost", port=6379, decode_responses=False
            )

    def test_redis_client_created_with_env_host(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")

        with patch("backend.agent.graph.Redis") as mock_redis, \
             patch("backend.agent.graph.AsyncRedisSaver") as mock_saver:
            mock_redis.return_value = MagicMock()
            mock_saver.return_value = MagicMock()

            sys.modules.pop(MODULE_PATH, None)
            importlib.import_module(MODULE_PATH)

            mock_redis.assert_called_once_with(
                host="my-redis-host", port=6379, decode_responses=False
            )

    def test_async_redis_saver_receives_redis_client(self, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)

        with patch("backend.agent.graph.Redis") as mock_redis, \
             patch("backend.agent.graph.AsyncRedisSaver") as mock_saver:
            sentinel_client = MagicMock(name="sentinel_redis")
            mock_redis.return_value = sentinel_client
            mock_saver.return_value = MagicMock()

            sys.modules.pop(MODULE_PATH, None)
            importlib.import_module(MODULE_PATH)

            mock_saver.assert_called_once_with(redis_client=sentinel_client)


# ---------------------------------------------------------------------------
# build_agent – happy path tests
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_instance(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        _, mock_agent = mock_create_agent
        result = build_agent("gpt-4o", 0.7)

        assert result is mock_agent

    def test_create_agent_called_once(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_ca, _ = mock_create_agent
        build_agent("gpt-4o", 0.7)

        mock_ca.assert_called_once()

    def test_llms_instantiated_with_correct_params(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_cls, _ = mock_llms_cls
        build_agent("gpt-4o", 0.5)

        mock_cls.assert_called_once_with(temperature=0.5, streaming=True)

    def test_get_model_called_with_model_name(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_cls, _ = mock_llms_cls
        build_agent("claude-3-opus", 0.3)

        mock_cls.return_value.get_model.assert_called_once_with("claude-3-opus")

    def test_system_prompt_forwarded_to_create_agent(
        self, mock_llms_cls, mock_create_agent, mock_tools
    ):
        from backend.agent.graph import build_agent, SYSTEM_PROMPT

        mock_ca, _ = mock_create_agent
        build_agent("gpt-4o", 0.7)

        _, kwargs = mock_ca.call_args
        assert kwargs.get("system_prompt") == SYSTEM_PROMPT

    def test_checkpointer_forwarded_to_create_agent(
        self, mock_llms_cls, mock_create_agent, mock_tools
    ):
        from backend.agent.graph import build_agent, _checkpointer

        mock_ca, _ = mock_create_agent
        build_agent("gpt-4o", 0.7)

        _, kwargs = mock_ca.call_args
        assert kwargs.get("checkpointer") is _checkpointer

    def test_model_forwarded_to_create_agent(
        self, mock_llms_cls, mock_create_agent, mock_tools
    ):
        from backend.agent.graph import build_agent

        mock_ca, _ = mock_create_agent
        _, mock_model = mock_llms_cls
        build_agent("gpt-4o", 0.7)

        _, kwargs = mock_ca.call_args
        assert kwargs.get("model") is mock_model


# ---------------------------------------------------------------------------
# build_agent – tools construction
# ---------------------------------------------------------------------------

class TestBuildAgentTools:

    def test_tools_list_has_three_entries(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_ca, _ = mock_create_agent
        build_agent("gpt-4o", 0.7)

        _, kwargs = mock_ca.call_args
        assert len(kwargs["tools"]) == 3

    def test_get_customer_profile_in_tools(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_ca, _ = mock_create_agent
        build_agent("gpt-4o", 0.7)

        _, kwargs = mock_ca.call_args
        assert mock_tools["get_customer_profile"] in kwargs["tools"]

    def test_customer_lookalike_in_tools(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_ca, _ = mock_create_agent
        build_agent("gpt-4o", 0.7)

        _, kwargs = mock_ca.call_args
        assert mock_tools["customer_lookalike"] in kwargs["tools"]

    def test_assessment_tool_in_tools(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_ca, _ = mock_create_agent
        build_agent("gpt-4o", 0.7)

        _, kwargs = mock_ca.call_args
        assert mock_tools["assessment_tool"] in kwargs["tools"]

    def test_run_underwriting_assessment_called_with_fast_mode(
        self, mock_llms_cls, mock_create_agent, mock_tools
    ):
        from backend.agent.graph import build_agent

        build_agent("gpt-4o", 0.7, mode="fast")

        mock_tools["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_run_underwriting_assessment_called_with_deep_mode(
        self, mock_llms_cls, mock_create_agent, mock_tools
    ):
        from backend.agent.graph import build_agent

        build_agent("gpt-4o", 0.7, mode="deep")

        mock_tools["_run_underwriting_assessment"].assert_called_once_with("deep")

    def test_default_mode_is_fast(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        build_agent("gpt-4o", 0.7)

        mock_tools["_run_underwriting_assessment"].assert_called_once_with("fast")


# ---------------------------------------------------------------------------
# build_agent – parametrised model/temperature combinations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model_name,temperature,mode", [
    ("gpt-4o", 0.0, "fast"),
    ("gpt-4o", 1.0, "fast"),
    ("gpt-4o", 0.5, "deep"),
    ("claude-3-opus", 0.7, "fast"),
    ("claude-3-opus", 0.3, "deep"),
    ("gpt-3.5-turbo", 0.9, "fast"),
    ("gemini-pro", 0.1, "deep"),
])
def test_build_agent_parametrised(model_name, temperature, mode, mock_llms_cls, mock_create_agent, mock_tools):
    from backend.agent.graph import build_agent

    mock_cls, mock_model = mock_llms_cls
    mock_ca, mock_agent = mock_create_agent

    result = build_agent(model_name, temperature, mode)

    mock_cls.assert_called_once_with(temperature=temperature, streaming=True)
    mock_cls.return_value.get_model.assert_called_once_with(model_name)
    mock_tools["_run_underwriting_assessment"].assert_called_once_with(mode)
    assert result is mock_agent


# ---------------------------------------------------------------------------
# build_agent – boundary / edge temperature values
# ---------------------------------------------------------------------------

class TestBuildAgentTemperatureBoundaries:

    def test_temperature_zero(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_cls, _ = mock_llms_cls
        build_agent("gpt-4o", 0.0)

        mock_cls.assert_called_once_with(temperature=0.0, streaming=True)

    def test_temperature_one(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_cls, _ = mock_llms_cls
        build_agent("gpt-4o", 1.0)

        mock_cls.assert_called_once_with(temperature=1.0, streaming=True)

    def test_temperature_very_small_positive(self, mock_llms_cls, mock_create_agent, mock_tools):
        from backend.agent.graph import build_agent

        mock_cls, _ = mock_llms_cls
        