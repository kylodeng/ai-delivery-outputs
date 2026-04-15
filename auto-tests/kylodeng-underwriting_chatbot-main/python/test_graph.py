"""
Tests for backend/agent/graph.py

What is tested:
- build_agent() happy path with valid model_name, temperature, and mode values
- build_agent() with different mode values ("fast", "deep", and edge cases)
- build_agent() boundary values for temperature (0.0, 1.0, extremes)
- build_agent() error conditions (invalid model_name, invalid temperature type)
- Module-level Redis client and checkpointer initialisation
- Tool list construction inside build_agent()

Mocks used:
- backend.agent.graph.LLMS                         — prevents real LLM API calls
- backend.agent.graph.create_agent                 — prevents real agent construction
- backend.agent.graph.get_customer_profile         — stub tool
- backend.agent.graph.customer_lookalike           — stub tool
- backend.agent.graph._run_underwriting_assessment — prevents real assessment calls
- backend.agent.graph.Redis                        — prevents real Redis connections
- backend.agent.graph.AsyncRedisSaver              — prevents real Redis saver init
- os.environ                                       — controls REDIS_HOST resolution

TODOs:
- TODO: Integration test verifying the agent can process a real underwriting request
        requires a running Redis instance and valid LLM credentials.
- TODO: Test that _checkpointer is correctly wired into create_agent once
        AsyncRedisSaver internals are stable / documented.
- TODO: Test build_agent with an exhaustive list of supported model_name values
        from the model registry (needs access to LLMS.supported_models or equivalent).
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a fresh import of graph.py with all external deps mocked
# ---------------------------------------------------------------------------

MODULE_PATH = "backend.agent.graph"


def _make_mock_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.__name__ = name
    return tool


def _reload_graph_module(extra_env: dict | None = None):
    """
    Remove graph from sys.modules and re-import it so module-level code
    (Redis / AsyncRedisSaver instantiation) executes under our patches.
    """
    for key in list(sys.modules.keys()):
        if "backend.agent.graph" in key:
            del sys.modules[key]

    env_patch = extra_env or {}
    with patch.dict(os.environ, env_patch, clear=False):
        import importlib
        mod = importlib.import_module(MODULE_PATH)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Prevent any real Redis connection at import time and during tests."""
    mock_redis_instance = MagicMock()
    mock_redis_cls = MagicMock(return_value=mock_redis_instance)
    monkeypatch.setattr("redis.asyncio.Redis", mock_redis_cls, raising=False)
    return mock_redis_cls, mock_redis_instance


@pytest.fixture(autouse=True)
def mock_async_redis_saver(monkeypatch):
    mock_saver_instance = MagicMock()
    mock_saver_cls = MagicMock(return_value=mock_saver_instance)
    monkeypatch.setattr(
        "langgraph.checkpoint.redis.aio.AsyncRedisSaver",
        mock_saver_cls,
        raising=False,
    )
    return mock_saver_cls, mock_saver_instance


@pytest.fixture()
def mock_llms():
    with patch(f"{MODULE_PATH}.LLMS") as mock_cls:
        mock_model = MagicMock(name="mock_llm_model")
        mock_instance = MagicMock()
        mock_instance.get_model.return_value = mock_model
        mock_cls.return_value = mock_instance
        yield mock_cls, mock_instance, mock_model


@pytest.fixture()
def mock_create_agent():
    with patch(f"{MODULE_PATH}.create_agent") as mock_ca:
        mock_ca.return_value = MagicMock(name="mock_agent")
        yield mock_ca


@pytest.fixture()
def mock_tools():
    with patch(f"{MODULE_PATH}.get_customer_profile") as mock_gcp, \
         patch(f"{MODULE_PATH}.customer_lookalike") as mock_cl, \
         patch(f"{MODULE_PATH}._run_underwriting_assessment") as mock_rua:
        mock_assessment_tool = MagicMock(name="assessment_tool")
        mock_rua.return_value = mock_assessment_tool
        yield mock_gcp, mock_cl, mock_rua, mock_assessment_tool


@pytest.fixture()
def mock_system_prompt():
    with patch(f"{MODULE_PATH}.SYSTEM_PROMPT", "MOCK_SYSTEM_PROMPT"):
        yield "MOCK_SYSTEM_PROMPT"


# ---------------------------------------------------------------------------
# Convenience wrapper that applies all common patches
# ---------------------------------------------------------------------------

@pytest.fixture()
def patched_build_agent(mock_llms, mock_create_agent, mock_tools, mock_system_prompt):
    """
    Returns (build_agent, mock_llms, mock_create_agent, mock_tools).
    All external dependencies are patched.
    """
    from backend.agent.graph import build_agent  # noqa: PLC0415
    mock_gcp, mock_cl, mock_rua, mock_assessment_tool = mock_tools
    mock_llms_cls, mock_llms_inst, mock_model = mock_llms
    return {
        "build_agent": build_agent,
        "mock_llms_cls": mock_llms_cls,
        "mock_llms_inst": mock_llms_inst,
        "mock_model": mock_model,
        "mock_create_agent": mock_create_agent,
        "mock_gcp": mock_gcp,
        "mock_cl": mock_cl,
        "mock_rua": mock_rua,
        "mock_assessment_tool": mock_assessment_tool,
        "system_prompt": mock_system_prompt,
    }


# ---------------------------------------------------------------------------
# Tests – module-level initialisation
# ---------------------------------------------------------------------------

class TestModuleLevelInit:
    """Verify that Redis and AsyncRedisSaver are instantiated when the module loads."""

    def test_redis_client_created_with_default_host(self):
        """When REDIS_HOST is not set, Redis should use 'localhost'."""
        env = {}
        env.pop("REDIS_HOST", None)

        with patch("redis.asyncio.Redis") as mock_redis_cls, \
             patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver"), \
             patch("langchain.agents.create_agent"), \
             patch.dict(os.environ, {}, clear=False):
            # Remove REDIS_HOST from environment for this test
            clean_env = {k: v for k, v in os.environ.items() if k != "REDIS_HOST"}
            with patch.dict(os.environ, clean_env, clear=True):
                for key in list(sys.modules.keys()):
                    if "backend.agent.graph" in key:
                        del sys.modules[key]
                with patch("redis.asyncio.Redis") as mock_r, \
                     patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver"):
                    import importlib
                    importlib.import_module(MODULE_PATH)
                    mock_r.assert_called_once_with(
                        host="localhost", port=6379, decode_responses=False
                    )

    def test_redis_client_created_with_env_host(self):
        """When REDIS_HOST is set, Redis should use that host."""
        clean_env = {k: v for k, v in os.environ.items() if k != "REDIS_HOST"}
        clean_env["REDIS_HOST"] = "my-redis-host"

        with patch.dict(os.environ, clean_env, clear=True):
            for key in list(sys.modules.keys()):
                if "backend.agent.graph" in key:
                    del sys.modules[key]
            with patch("redis.asyncio.Redis") as mock_r, \
                 patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver"):
                import importlib
                importlib.import_module(MODULE_PATH)
                mock_r.assert_called_once_with(
                    host="my-redis-host", port=6379, decode_responses=False
                )

    def test_async_redis_saver_receives_redis_client(self):
        """AsyncRedisSaver should be initialised with the Redis client instance."""
        for key in list(sys.modules.keys()):
            if "backend.agent.graph" in key:
                del sys.modules[key]

        mock_redis_instance = MagicMock(name="redis_instance")
        with patch("redis.asyncio.Redis", return_value=mock_redis_instance) as _, \
             patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver") as mock_saver_cls:
            import importlib
            importlib.import_module(MODULE_PATH)
            mock_saver_cls.assert_called_once_with(redis_client=mock_redis_instance)


# ---------------------------------------------------------------------------
# Tests – build_agent happy paths
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_object(self, patched_build_agent):
        result = patched_build_agent["build_agent"]("gpt-4o", 0.7)
        assert result is patched_build_agent["mock_create_agent"].return_value

    def test_llms_instantiated_with_correct_params(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        patched_build_agent["mock_llms_cls"].assert_called_once_with(
            temperature=0.7, streaming=True
        )

    def test_get_model_called_with_model_name(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        patched_build_agent["mock_llms_inst"].get_model.assert_called_once_with("gpt-4o")

    def test_create_agent_called_with_model(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        call_kwargs = patched_build_agent["mock_create_agent"].call_args
        assert call_kwargs.kwargs["model"] is patched_build_agent["mock_model"]

    def test_create_agent_called_with_system_prompt(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        call_kwargs = patched_build_agent["mock_create_agent"].call_args
        assert call_kwargs.kwargs["system_prompt"] == "MOCK_SYSTEM_PROMPT"

    def test_create_agent_called_with_checkpointer(self, patched_build_agent):
        from backend.agent.graph import _checkpointer  # noqa: PLC0415
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        call_kwargs = patched_build_agent["mock_create_agent"].call_args
        assert call_kwargs.kwargs["checkpointer"] is _checkpointer

    def test_tools_list_has_three_items(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        call_kwargs = patched_build_agent["mock_create_agent"].call_args
        tools = call_kwargs.kwargs["tools"]
        assert len(tools) == 3

    def test_tools_list_contains_get_customer_profile(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        call_kwargs = patched_build_agent["mock_create_agent"].call_args
        tools = call_kwargs.kwargs["tools"]
        assert patched_build_agent["mock_gcp"] in tools

    def test_tools_list_contains_customer_lookalike(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        call_kwargs = patched_build_agent["mock_create_agent"].call_args
        tools = call_kwargs.kwargs["tools"]
        assert patched_build_agent["mock_cl"] in tools

    def test_tools_list_contains_assessment_result(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        call_kwargs = patched_build_agent["mock_create_agent"].call_args
        tools = call_kwargs.kwargs["tools"]
        assert patched_build_agent["mock_assessment_tool"] in tools


# ---------------------------------------------------------------------------
# Tests – mode parameter
# ---------------------------------------------------------------------------

class TestBuildAgentMode:

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_mode_passed_to_run_underwriting_assessment(self, patched_build_agent, mode):
        patched_build_agent["build_agent"]("gpt-4o", 0.7, mode=mode)
        patched_build_agent["mock_rua"].assert_called_once_with(mode)

    def test_default_mode_is_fast(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.7)
        patched_build_agent["mock_rua"].assert_called_once_with("fast")

    def test_mode_deep_calls_assessment_with_deep(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.5, mode="deep")
        patched_build_agent["mock_rua"].assert_called_once_with("deep")

    def test_custom_mode_string_passed_through(self, patched_build_agent):
        """build_agent should pass arbitrary mode strings without validation."""
        patched_build_agent["build_agent"]("gpt-4o", 0.5, mode="ultra")
        patched_build_agent["mock_rua"].assert_called_once_with("ultra")

    def test_empty_mode_string_passed_through(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.5, mode="")
        patched_build_agent["mock_rua"].assert_called_once_with("")


# ---------------------------------------------------------------------------
# Tests – temperature boundary values
# ---------------------------------------------------------------------------

class TestBuildAgentTemperature:

    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_valid_temperatures(self, patched_build_agent, temperature):
        result = patched_build_agent["build_agent"]("gpt-4o", temperature)
        assert result is not None
        patched_build_agent["mock_llms_cls"].assert_called_with(
            temperature=temperature, streaming=True
        )

    def test_temperature_zero(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 0.0)
        patched_build_agent["mock_llms_cls"].assert_called_once_with(
            temperature=0.0, streaming=True
        )

    def test_temperature_one(self, patched_build_agent):
        patched_build_agent["build_agent"]("gpt-4o", 1.0)
        patched_build_agent["mock_llms_cls"].assert_called_once_with(
            temperature=1.0, streaming=True
        )

    def