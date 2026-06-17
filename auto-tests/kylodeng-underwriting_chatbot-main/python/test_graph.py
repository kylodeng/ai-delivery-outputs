"""
Tests for backend/agent/graph.py

What is tested:
    - build_agent() happy path with valid model_name, temperature, and mode values
    - build_agent() with different mode values ("fast", "deep", and edge-case values)
    - build_agent() temperature boundary values (0.0, 1.0, extreme values)
    - build_agent() error propagation when LLMS.get_model() raises
    - build_agent() error propagation when create_agent raises
    - Module-level Redis and checkpointer initialization
    - Tool list composition (correct tools passed to create_agent)
    - REDIS_HOST environment variable handling

Mocks used:
    - langchain.agents.create_agent (patched to avoid real LLM/graph construction)
    - redis.asyncio.Redis (patched at module level to avoid real Redis connection)
    - langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched to avoid real Redis)
    - modules.tools.get_customer_profile (patched)
    - modules.tools.customer_lookalike (patched)
    - modules.assessment._run_underwriting_assessment (patched)
    - modules.LLMS.LLMS (patched to avoid real model loading)
    - backend.agent.prompts.SYSTEM_PROMPT (patched)

TODOs:
    - TODO: Integration test for actual Redis connectivity once a test Redis instance is available
    - TODO: Test streaming behaviour of the returned agent once agent interface is stable
    - TODO: Test checkpointer persistence across agent rebuilds
"""

import os
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_mock_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.__name__ = name
    return tool


@pytest.fixture(autouse=True)
def _patch_heavy_imports(monkeypatch):
    """
    Patch all external heavy dependencies before the module under test is
    imported so that no real network / model calls are made.
    """
    # --- redis ---
    mock_redis_instance = AsyncMock()
    mock_redis_cls = MagicMock(return_value=mock_redis_instance)

    # --- AsyncRedisSaver ---
    mock_checkpointer = MagicMock()
    mock_redis_saver_cls = MagicMock(return_value=mock_checkpointer)

    # --- tools ---
    mock_get_customer_profile = _make_mock_tool("get_customer_profile")
    mock_customer_lookalike = _make_mock_tool("customer_lookalike")

    # --- assessment ---
    mock_assessment_result = _make_mock_tool("_run_underwriting_assessment_result")
    mock_run_underwriting_assessment = MagicMock(return_value=mock_assessment_result)

    # --- LLMS ---
    mock_model_instance = MagicMock()
    mock_llms_instance = MagicMock()
    mock_llms_instance.get_model = MagicMock(return_value=mock_model_instance)
    mock_llms_cls = MagicMock(return_value=mock_llms_instance)

    # --- create_agent ---
    mock_agent = MagicMock()
    mock_create_agent = MagicMock(return_value=mock_agent)

    # --- SYSTEM_PROMPT ---
    mock_system_prompt = "MOCK_SYSTEM_PROMPT"

    # Build fake module stubs so that import inside graph.py succeeds
    fake_redis_module = types.ModuleType("redis")
    fake_redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_asyncio.Redis = mock_redis_cls
    fake_redis_module.asyncio = fake_redis_asyncio
    sys.modules["redis"] = fake_redis_module
    sys.modules["redis.asyncio"] = fake_redis_asyncio

    fake_langgraph = types.ModuleType("langgraph")
    fake_langgraph_checkpoint = types.ModuleType("langgraph.checkpoint")
    fake_langgraph_checkpoint_redis = types.ModuleType("langgraph.checkpoint.redis")
    fake_langgraph_checkpoint_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_langgraph_checkpoint_redis_aio.AsyncRedisSaver = mock_redis_saver_cls
    fake_langgraph.checkpoint = fake_langgraph_checkpoint
    fake_langgraph_checkpoint.redis = fake_langgraph_checkpoint_redis
    fake_langgraph_checkpoint_redis.aio = fake_langgraph_checkpoint_redis_aio
    sys.modules["langgraph"] = fake_langgraph
    sys.modules["langgraph.checkpoint"] = fake_langgraph_checkpoint
    sys.modules["langgraph.checkpoint.redis"] = fake_langgraph_checkpoint_redis
    sys.modules["langgraph.checkpoint.redis.aio"] = fake_langgraph_checkpoint_redis_aio

    fake_langchain_agents = types.ModuleType("langchain.agents")
    fake_langchain_agents.create_agent = mock_create_agent
    fake_langchain = types.ModuleType("langchain")
    fake_langchain.agents = fake_langchain_agents
    sys.modules["langchain"] = fake_langchain
    sys.modules["langchain.agents"] = fake_langchain_agents

    fake_tools_module = types.ModuleType("modules.tools")
    fake_tools_module.get_customer_profile = mock_get_customer_profile
    fake_tools_module.customer_lookalike = mock_customer_lookalike
    fake_modules = types.ModuleType("modules")
    fake_modules.tools = fake_tools_module
    sys.modules["modules"] = fake_modules
    sys.modules["modules.tools"] = fake_tools_module

    fake_assessment_module = types.ModuleType("modules.assessment")
    fake_assessment_module._run_underwriting_assessment = mock_run_underwriting_assessment
    sys.modules["modules.assessment"] = fake_assessment_module

    fake_llms_module = types.ModuleType("modules.LLMS")
    fake_llms_module.LLMS = mock_llms_cls
    sys.modules["modules.LLMS"] = fake_llms_module

    fake_prompts = types.ModuleType("agent.prompts")
    fake_prompts.SYSTEM_PROMPT = mock_system_prompt

    # We need to make ".prompts" resolvable as a relative import from "agent.graph"
    # Provide it under the package name the module will look for
    fake_agent_pkg = types.ModuleType("agent")
    fake_agent_pkg.prompts = fake_prompts
    sys.modules.setdefault("agent", fake_agent_pkg)
    sys.modules["agent.prompts"] = fake_prompts

    # Remove previously cached version so we get a fresh import each test
    sys.modules.pop("agent.graph", None)
    sys.modules.pop("backend.agent.graph", None)

    yield {
        "redis_cls": mock_redis_cls,
        "redis_instance": mock_redis_instance,
        "redis_saver_cls": mock_redis_saver_cls,
        "checkpointer": mock_checkpointer,
        "get_customer_profile": mock_get_customer_profile,
        "customer_lookalike": mock_customer_lookalike,
        "_run_underwriting_assessment": mock_run_underwriting_assessment,
        "assessment_result": mock_assessment_result,
        "llms_cls": mock_llms_cls,
        "llms_instance": mock_llms_instance,
        "model_instance": mock_model_instance,
        "create_agent": mock_create_agent,
        "agent": mock_agent,
        "system_prompt": mock_system_prompt,
    }

    # Cleanup after each test
    sys.modules.pop("agent.graph", None)


@pytest.fixture()
def graph_module(_patch_heavy_imports):
    """Import and return the graph module with all dependencies mocked."""
    import importlib
    import agent.graph as gm
    return gm, _patch_heavy_imports


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------

class TestModuleLevelInit:
    """Verify that module-level objects are created correctly on import."""

    def test_redis_client_created_with_default_host(self, monkeypatch, _patch_heavy_imports):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        sys.modules.pop("agent.graph", None)
        import agent.graph  # noqa: F401 — trigger module init

        _patch_heavy_imports["redis_cls"].assert_called_once_with(
            host="localhost", port=6379, decode_responses=False
        )

    def test_redis_client_created_with_env_host(self, monkeypatch, _patch_heavy_imports):
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        sys.modules.pop("agent.graph", None)
        import agent.graph  # noqa: F401

        _patch_heavy_imports["redis_cls"].assert_called_once_with(
            host="my-redis-host", port=6379, decode_responses=False
        )

    def test_checkpointer_created_with_redis_client(self, _patch_heavy_imports):
        sys.modules.pop("agent.graph", None)
        import agent.graph  # noqa: F401

        redis_instance = _patch_heavy_imports["redis_cls"].return_value
        _patch_heavy_imports["redis_saver_cls"].assert_called_once_with(
            redis_client=redis_instance
        )

    def test_module_exposes_build_agent(self, graph_module):
        gm, _ = graph_module
        assert callable(gm.build_agent)


# ---------------------------------------------------------------------------
# build_agent happy-path tests
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:
    """Happy-path scenarios for build_agent()."""

    def test_returns_agent(self, graph_module):
        gm, mocks = graph_module
        result = gm.build_agent("gpt-4o", 0.5)
        assert result is mocks["agent"]

    def test_llms_instantiated_with_correct_params(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.7)
        mocks["llms_cls"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5)
        mocks["llms_instance"].get_model.assert_called_once_with("gpt-4o")

    def test_create_agent_called_with_correct_args(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5)
        mocks["create_agent"].assert_called_once_with(
            model=mocks["model_instance"],
            tools=[
                mocks["get_customer_profile"],
                mocks["assessment_result"],
                mocks["customer_lookalike"],
            ],
            system_prompt=mocks["system_prompt"],
            checkpointer=mocks["checkpointer"],
        )

    def test_underwriting_assessment_called_with_default_mode(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5)
        mocks["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_tools_list_has_three_items(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5)
        _, kwargs = mocks["create_agent"].call_args
        assert len(kwargs["tools"]) == 3

    def test_system_prompt_forwarded(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5)
        _, kwargs = mocks["create_agent"].call_args
        assert kwargs["system_prompt"] == mocks["system_prompt"]

    def test_checkpointer_forwarded(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5)
        _, kwargs = mocks["create_agent"].call_args
        assert kwargs["checkpointer"] is mocks["checkpointer"]


# ---------------------------------------------------------------------------
# Mode parameter tests
# ---------------------------------------------------------------------------

class TestBuildAgentMode:
    """Verify that the mode argument is correctly forwarded."""

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_valid_modes(self, graph_module, mode):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5, mode=mode)
        mocks["_run_underwriting_assessment"].assert_called_once_with(mode)

    def test_default_mode_is_fast(self, graph_module):
        gm, mocks = graph_module
        # Call without explicit mode
        gm.build_agent("gpt-4o", 0.5)
        mocks["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_unknown_mode_still_forwarded(self, graph_module):
        """build_agent should not validate mode — it passes it straight through."""
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5, mode="turbo")
        mocks["_run_underwriting_assessment"].assert_called_once_with("turbo")

    def test_empty_string_mode_forwarded(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.5, mode="")
        mocks["_run_underwriting_assessment"].assert_called_once_with("")


# ---------------------------------------------------------------------------
# Temperature boundary tests
# ---------------------------------------------------------------------------

class TestBuildAgentTemperatureBoundary:
    """Boundary and edge values for the temperature parameter."""

    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_standard_temperatures(self, graph_module, temperature):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", temperature)
        mocks["llms_cls"].assert_called_once_with(temperature=temperature, streaming=True)

    def test_temperature_zero(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 0.0)
        mocks["llms_cls"].assert_called_once_with(temperature=0.0, streaming=True)

    def test_temperature_one(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 1.0)
        mocks["llms_cls"].assert_called_once_with(temperature=1.0, streaming=True)

    def test_temperature_above_one(self, graph_module):
        """Values >1 are invalid for most LLMs, but build_agent should still pass them through."""
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", 2.0)
        mocks["llms_cls"].assert_called_once_with(temperature=2.0, streaming=True)

    def test_negative_temperature_forwarded(self, graph_module):
        gm, mocks = graph_module
        gm.build_agent("gpt-4o", -0.1)
        mocks["llms