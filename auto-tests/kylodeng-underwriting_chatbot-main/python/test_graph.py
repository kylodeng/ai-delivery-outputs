"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() happy path with various model_name / temperature / mode combinations
- build_agent() with default mode ("fast")
- build_agent() with explicit "deep" mode
- build_agent() delegates correctly to LLMS, create_agent, and tool factories
- Module-level Redis client and checkpointer initialisation (env-var override)
- Edge cases: boundary temperatures (0.0, 1.0, negative, >1), empty/unknown model names
- Error conditions: LLMS.get_model raises, _run_underwriting_assessment raises, create_agent raises

Mocks used:
- langchain.agents.create_agent          → unittest.mock.MagicMock / patch
- redis.asyncio.Redis                    → unittest.mock.MagicMock / patch
- langgraph.checkpoint.redis.aio.AsyncRedisSaver → unittest.mock.MagicMock / patch
- modules.tools.get_customer_profile     → sentinel / patch
- modules.tools.customer_lookalike       → sentinel / patch
- modules.assessment._run_underwriting_assessment → unittest.mock.MagicMock / patch
- modules.LLMS.LLMS                      → unittest.mock.MagicMock / patch

TODOs:
- TODO: Integration test verifying the agent actually processes a message end-to-end
        (requires a live Redis instance and a real LLM endpoint).
- TODO: Test that the checkpointer correctly persists state across multiple agent calls
        (requires AsyncRedisSaver internals or a Redis test-container).
- TODO: Verify streaming behaviour of the agent (depends on Chainlit/LangGraph runtime).
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call, sentinel
import pytest


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake module tree so that graph.py can be imported
# without real external dependencies.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """
    Create lightweight stub modules for every third-party / internal package
    imported by graph.py so that the module can be imported in isolation.
    """
    stubs = {}

    # --- redis.asyncio ---
    fake_redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_class = MagicMock(name="Redis")
    fake_redis_asyncio.Redis = fake_redis_class
    stubs["redis"] = types.ModuleType("redis")
    stubs["redis.asyncio"] = fake_redis_asyncio

    # --- langgraph.checkpoint.redis.aio ---
    fake_lg_cp = types.ModuleType("langgraph")
    fake_lg_cp_checkpoint = types.ModuleType("langgraph.checkpoint")
    fake_lg_cp_redis = types.ModuleType("langgraph.checkpoint.redis")
    fake_lg_cp_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_lg_cp_redis_aio.AsyncRedisSaver = MagicMock(name="AsyncRedisSaver")
    stubs["langgraph"] = fake_lg_cp
    stubs["langgraph.checkpoint"] = fake_lg_cp_checkpoint
    stubs["langgraph.checkpoint.redis"] = fake_lg_cp_redis
    stubs["langgraph.checkpoint.redis.aio"] = fake_lg_cp_redis_aio

    # --- langchain.agents ---
    fake_lc = types.ModuleType("langchain")
    fake_lc_agents = types.ModuleType("langchain.agents")
    fake_lc_agents.create_agent = MagicMock(name="create_agent")
    stubs["langchain"] = fake_lc
    stubs["langchain.agents"] = fake_lc_agents

    # --- modules.tools ---
    fake_modules = types.ModuleType("modules")
    fake_tools = types.ModuleType("modules.tools")
    fake_tools.get_customer_profile = sentinel.get_customer_profile
    fake_tools.customer_lookalike = sentinel.customer_lookalike
    stubs["modules"] = fake_modules
    stubs["modules.tools"] = fake_tools

    # --- modules.assessment ---
    fake_assessment = types.ModuleType("modules.assessment")
    fake_assessment._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment",
        return_value=sentinel.assessment_tool,
    )
    stubs["modules.assessment"] = fake_assessment

    # --- modules.LLMS ---
    fake_llms_mod = types.ModuleType("modules.LLMS")
    fake_llms_class = MagicMock(name="LLMS")
    fake_llms_instance = MagicMock(name="llms_instance")
    fake_llms_instance.get_model.return_value = sentinel.model
    fake_llms_class.return_value = fake_llms_instance
    fake_llms_mod.LLMS = fake_llms_class
    stubs["modules.LLMS"] = fake_llms_mod

    # --- agent.prompts (relative import) ---
    fake_agent_pkg = types.ModuleType("agent")
    fake_prompts = types.ModuleType("agent.prompts")
    fake_prompts.SYSTEM_PROMPT = "SYSTEM_PROMPT_STUB"
    stubs["agent"] = fake_agent_pkg
    stubs["agent.prompts"] = fake_prompts

    return stubs


# ---------------------------------------------------------------------------
# Fixture: import graph with all stubs injected
# ---------------------------------------------------------------------------

@pytest.fixture()
def graph_module(monkeypatch):
    """
    Import backend/agent/graph.py with all external dependencies stubbed out.
    Returns the imported module object and the stub dictionary for assertions.
    """
    stubs = _make_fake_modules()

    # Inject stubs into sys.modules before the import
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    # Ensure a clean import every time (remove cached version if present)
    for key in list(sys.modules.keys()):
        if "backend.agent.graph" in key or key == "agent.graph":
            monkeypatch.delitem(sys.modules, key, raising=False)

    # Add backend directory to path so relative imports resolve
    backend_path = os.path.join(os.path.dirname(__file__), "..", "backend")
    backend_path = os.path.abspath(backend_path)

    # We import via importlib using the file path directly
    spec = importlib.util.spec_from_file_location(
        "agent.graph",
        os.path.join(os.path.dirname(__file__), "..", "backend", "agent", "graph.py"),
    )

    if spec is None or spec.loader is None:
        pytest.skip("Cannot locate backend/agent/graph.py — adjust path if needed.")

    module = importlib.util.module_from_spec(spec)
    # Make the module believe it is part of the 'agent' package
    module.__package__ = "agent"
    spec.loader.exec_module(module)

    return module, stubs


# ---------------------------------------------------------------------------
# Tests: module-level initialisation
# ---------------------------------------------------------------------------

class TestModuleLevelInit:
    """Tests for Redis client and checkpointer created at import time."""

    def test_redis_client_created_with_default_host(self, graph_module):
        _, stubs = graph_module
        redis_cls = stubs["redis.asyncio"].Redis
        # Redis() must have been called during import
        redis_cls.assert_called_once()
        _, kwargs = redis_cls.call_args
        assert kwargs.get("port") == 6379
        assert kwargs.get("decode_responses") is False

    def test_redis_client_uses_env_variable(self, monkeypatch):
        """REDIS_HOST env-var should be forwarded to the Redis constructor."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        stubs = _make_fake_modules()
        for name, mod in stubs.items():
            monkeypatch.setitem(sys.modules, name, mod)
        for key in list(sys.modules.keys()):
            if key == "agent.graph":
                monkeypatch.delitem(sys.modules, key, raising=False)

        spec = importlib.util.spec_from_file_location(
            "agent.graph",
            os.path.join(os.path.dirname(__file__), "..", "backend", "agent", "graph.py"),
        )
        if spec is None or spec.loader is None:
            pytest.skip("Cannot locate backend/agent/graph.py")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "agent"
        spec.loader.exec_module(module)

        redis_cls = stubs["redis.asyncio"].Redis
        _, kwargs = redis_cls.call_args
        assert kwargs.get("host") == "my-redis-host"

    def test_redis_client_defaults_to_localhost(self, monkeypatch):
        """When REDIS_HOST is unset the host should be 'localhost'."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        stubs = _make_fake_modules()
        for name, mod in stubs.items():
            monkeypatch.setitem(sys.modules, name, mod)
        for key in list(sys.modules.keys()):
            if key == "agent.graph":
                monkeypatch.delitem(sys.modules, key, raising=False)

        spec = importlib.util.spec_from_file_location(
            "agent.graph",
            os.path.join(os.path.dirname(__file__), "..", "backend", "agent", "graph.py"),
        )
        if spec is None or spec.loader is None:
            pytest.skip("Cannot locate backend/agent/graph.py")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "agent"
        spec.loader.exec_module(module)

        redis_cls = stubs["redis.asyncio"].Redis
        _, kwargs = redis_cls.call_args
        assert kwargs.get("host") == "localhost"

    def test_async_redis_saver_created_with_redis_client(self, graph_module):
        _, stubs = graph_module
        saver_cls = stubs["langgraph.checkpoint.redis.aio"].AsyncRedisSaver
        saver_cls.assert_called_once()
        _, kwargs = saver_cls.call_args
        # The checkpointer receives the Redis client instance
        assert "redis_client" in kwargs


# ---------------------------------------------------------------------------
# Tests: build_agent happy paths
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    @pytest.mark.parametrize("model_name,temperature,mode", [
        ("gpt-4o", 0.0, "fast"),
        ("gpt-4o", 1.0, "fast"),
        ("gpt-4o-mini", 0.5, "deep"),
        ("claude-3-5-sonnet", 0.7, "fast"),
        ("claude-3-5-sonnet", 0.3, "deep"),
        ("o3-mini", 0.0, "deep"),
    ])
    def test_returns_create_agent_result(self, graph_module, model_name, temperature, mode):
        module, stubs = graph_module
        expected = MagicMock(name="agent_instance")
        stubs["langchain.agents"].create_agent.return_value = expected

        result = module.build_agent(model_name, temperature, mode)

        assert result is expected

    def test_default_mode_is_fast(self, graph_module):
        """Calling build_agent without mode should default to 'fast'."""
        module, stubs = graph_module
        mock_assessment = stubs["modules.assessment"]._run_underwriting_assessment
        mock_assessment.reset_mock()

        module.build_agent("gpt-4o", 0.5)

        mock_assessment.assert_called_once_with("fast")

    def test_fast_mode_calls_assessment_with_fast(self, graph_module):
        module, stubs = graph_module
        mock_assessment = stubs["modules.assessment"]._run_underwriting_assessment
        mock_assessment.reset_mock()

        module.build_agent("gpt-4o", 0.5, mode="fast")

        mock_assessment.assert_called_once_with("fast")

    def test_deep_mode_calls_assessment_with_deep(self, graph_module):
        module, stubs = graph_module
        mock_assessment = stubs["modules.assessment"]._run_underwriting_assessment
        mock_assessment.reset_mock()

        module.build_agent("gpt-4o", 0.5, mode="deep")

        mock_assessment.assert_called_once_with("deep")

    def test_llms_instantiated_with_correct_params(self, graph_module):
        module, stubs = graph_module
        llms_cls = stubs["modules.LLMS"].LLMS
        llms_cls.reset_mock()

        module.build_agent("gpt-4o-mini", 0.3, "fast")

        llms_cls.assert_called_once_with(temperature=0.3, streaming=True)

    def test_get_model_called_with_model_name(self, graph_module):
        module, stubs = graph_module
        llms_instance = stubs["modules.LLMS"].LLMS.return_value
        llms_instance.get_model.reset_mock()

        module.build_agent("claude-3-5-sonnet", 0.7, "fast")

        llms_instance.get_model.assert_called_once_with("claude-3-5-sonnet")

    def test_create_agent_called_with_correct_kwargs(self, graph_module):
        module, stubs = graph_module
        create_agent_mock = stubs["langchain.agents"].create_agent
        create_agent_mock.reset_mock()
        llms_instance = stubs["modules.LLMS"].LLMS.return_value
        llms_instance.get_model.return_value = sentinel.model

        module.build_agent("gpt-4o", 0.5, "fast")

        create_agent_mock.assert_called_once()
        _, kwargs = create_agent_mock.call_args
        assert kwargs["model"] is sentinel.model
        assert kwargs["system_prompt"] == "SYSTEM_PROMPT_STUB"
        # checkpointer should be the module-level _checkpointer
        assert kwargs["checkpointer"] is module._checkpointer

    def test_tools_list_contains_all_three_tools(self, graph_module):
        module, stubs = graph_module
        create_agent_mock = stubs["langchain.agents"].create_agent
        create_agent_mock.reset_mock()
        mock_assessment = stubs["modules.assessment"]._run_underwriting_assessment
        mock_assessment.reset_mock()
        mock_assessment.return_value = sentinel.assessment_tool

        module.build_agent("gpt-4o", 0.5, "fast")

        _, kwargs = create_agent_mock.call_args
        tools = kwargs["tools"]
        assert len(tools) == 3
        assert sentinel.get_customer_profile in tools
        assert sentinel.assessment_tool in tools
        assert sentinel.customer_lookalike in tools

    def test_tools_list_order(self, graph_module):
        """Tools should be [get_customer_profile, assessment, customer_lookalike]."""
        module, stubs = graph_module
        create_agent_mock = stubs["langchain.agents"].create_agent
        create_agent_mock.reset_mock()
        mock_assessment = stubs["modules.assessment"]._run_underwriting_assessment
        mock_assessment.return_value = sentinel.assessment_tool

        module.build_agent("gpt-4o", 0.5, "fast")

        _, kwargs = create_agent_mock.call_args
        tools = kwargs["tools"]
        assert tools[0]