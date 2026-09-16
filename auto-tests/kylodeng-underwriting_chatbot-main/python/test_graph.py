"""
Test module for backend/agent/graph.py

What is tested:
    - build_agent() function: happy path, edge cases, error conditions, boundary values
    - Module-level Redis client and checkpointer initialization
    - Correct wiring of model, tools, system_prompt, and checkpointer into create_agent

Mocks used:
    - unittest.mock.patch / MagicMock for:
        - redis.asyncio.Redis (prevents real Redis connections)
        - langgraph.checkpoint.redis.aio.AsyncRedisSaver (prevents real Redis connections)
        - langchain.agents.create_agent (prevents real LLM/agent construction)
        - modules.LLMS.LLMS (prevents real LLM instantiation)
        - modules.tools.get_customer_profile (imported tool)
        - modules.tools.customer_lookalike (imported tool)
        - modules.assessment._run_underwriting_assessment (callable returning a tool)
        - backend.agent.prompts.SYSTEM_PROMPT

TODOs:
    - TODO: Integration test for full agent execution requires a live Redis instance and LLM credentials
    - TODO: Test AsyncRedisSaver setup_async() call once graph lifecycle is clearer
    - TODO: Test that the returned agent can invoke tools end-to-end (needs LangGraph harness)
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake module tree so graph.py can be imported
# without real dependencies installed (CI environments).
# ---------------------------------------------------------------------------

def _make_stub_modules():
    """
    Pre-populate sys.modules with lightweight stubs for every external
    dependency that graph.py tries to import at module load time.
    """
    stubs = {}

    # langchain.agents
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_agents.create_agent = MagicMock(name="create_agent")
    stubs["langchain"] = types.ModuleType("langchain")
    stubs["langchain.agents"] = langchain_agents

    # redis.asyncio
    redis_mod = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")
    redis_asyncio.Redis = MagicMock(name="Redis")
    stubs["redis"] = redis_mod
    stubs["redis.asyncio"] = redis_asyncio

    # langgraph.checkpoint.redis.aio
    langgraph_mod = types.ModuleType("langgraph")
    langgraph_checkpoint = types.ModuleType("langgraph.checkpoint")
    langgraph_checkpoint_redis = types.ModuleType("langgraph.checkpoint.redis")
    langgraph_checkpoint_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    langgraph_checkpoint_redis_aio.AsyncRedisSaver = MagicMock(name="AsyncRedisSaver")
    stubs["langgraph"] = langgraph_mod
    stubs["langgraph.checkpoint"] = langgraph_checkpoint
    stubs["langgraph.checkpoint.redis"] = langgraph_checkpoint_redis
    stubs["langgraph.checkpoint.redis.aio"] = langgraph_checkpoint_redis_aio

    # modules.tools
    modules_mod = types.ModuleType("modules")
    modules_tools = types.ModuleType("modules.tools")
    modules_tools.get_customer_profile = MagicMock(name="get_customer_profile")
    modules_tools.customer_lookalike = MagicMock(name="customer_lookalike")
    stubs["modules"] = modules_mod
    stubs["modules.tools"] = modules_tools

    # modules.assessment
    modules_assessment = types.ModuleType("modules.assessment")
    _fake_assessment_tool = MagicMock(name="assessment_tool_instance")
    modules_assessment._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment",
        return_value=_fake_assessment_tool,
    )
    stubs["modules.assessment"] = modules_assessment

    # modules.LLMS
    modules_llms = types.ModuleType("modules.LLMS")
    _fake_llms_instance = MagicMock(name="llms_instance")
    _fake_llms_instance.get_model.return_value = MagicMock(name="model_instance")
    modules_llms.LLMS = MagicMock(name="LLMS", return_value=_fake_llms_instance)
    stubs["modules.LLMS"] = modules_llms

    # agent.prompts  (relative import resolved as backend.agent.prompts)
    # We patch both possible names so it works regardless of package root.
    for pkg_prefix in ("agent", "backend.agent"):
        prompts_mod = types.ModuleType(f"{pkg_prefix}.prompts")
        prompts_mod.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
        stubs[f"{pkg_prefix}.prompts"] = prompts_mod

    return stubs


# ---------------------------------------------------------------------------
# Fixture: import graph with all external deps stubbed
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def graph_module():
    """
    Import backend/agent/graph.py with every external dependency stubbed.
    Returns the imported module object plus the key mock objects for
    assertion in individual tests.
    """
    stubs = _make_stub_modules()

    # Temporarily inject stubs
    original = {}
    for name, mod in stubs.items():
        original[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # Ensure the package hierarchy exists so relative imports resolve
    for pkg in ("agent", "backend", "backend.agent"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    # Remove any previously cached version of graph so it reloads cleanly
    for key in list(sys.modules.keys()):
        if "graph" in key and "agent" in key:
            del sys.modules[key]

    try:
        # graph.py lives at backend/agent/graph.py; we import it as a plain
        # file-level module so we do not need the package installed.
        import importlib.util, pathlib

        graph_path = pathlib.Path(__file__).parent.parent / "agent" / "graph.py"
        if not graph_path.exists():
            # Fallback: try relative to CWD (e.g. when running from repo root)
            graph_path = pathlib.Path("backend") / "agent" / "graph.py"

        spec = importlib.util.spec_from_file_location(
            "agent.graph",
            str(graph_path),
            submodule_search_locations=[],
        )
        mod = importlib.util.module_from_spec(spec)
        # Make relative imports resolve correctly
        mod.__package__ = "agent"
        sys.modules["agent.graph"] = mod
        spec.loader.exec_module(mod)
    finally:
        # Restore original sys.modules entries (cleanup)
        for name, orig_mod in original.items():
            if orig_mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = orig_mod

    return mod, stubs


# ---------------------------------------------------------------------------
# Fixtures used per test (fresh mocks each test)
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_mocks(graph_module):
    """
    Return the graph module and reset all relevant mocks before each test.
    """
    mod, stubs = graph_module

    # Reset call counts / return values
    create_agent_mock = stubs["langchain.agents"].create_agent
    create_agent_mock.reset_mock()

    llms_class_mock = stubs["modules.LLMS"].LLMS
    llms_class_mock.reset_mock()

    llms_instance_mock = llms_class_mock.return_value
    llms_instance_mock.reset_mock()
    llms_instance_mock.get_model.return_value = MagicMock(name="model_instance")

    assessment_mock = stubs["modules.assessment"]._run_underwriting_assessment
    assessment_mock.reset_mock()
    _fake_tool = MagicMock(name="assessment_tool")
    assessment_mock.return_value = _fake_tool

    return mod, stubs


# ---------------------------------------------------------------------------
# Tests: module-level initialisation
# ---------------------------------------------------------------------------

class TestModuleLevelInit:
    """Verify that Redis client and checkpointer are created at import time."""

    def test_redis_client_created(self, graph_module):
        mod, stubs = graph_module
        redis_cls = stubs["redis.asyncio"].Redis
        # Redis() was called at module load time
        assert redis_cls.called, "Redis() should be called during module initialisation"

    def test_redis_host_default(self, graph_module):
        mod, stubs = graph_module
        redis_cls = stubs["redis.asyncio"].Redis
        # Verify 'host' kwarg was passed (default or env-based)
        call_kwargs = redis_cls.call_args[1] if redis_cls.call_args else {}
        call_args = redis_cls.call_args[0] if redis_cls.call_args else ()
        # host can be positional or keyword
        host_value = call_kwargs.get("host") or (call_args[0] if call_args else None)
        assert host_value is not None, "Redis should receive a 'host' argument"

    def test_redis_port_6379(self, graph_module):
        mod, stubs = graph_module
        redis_cls = stubs["redis.asyncio"].Redis
        call_kwargs = redis_cls.call_args[1] if redis_cls.call_args else {}
        assert call_kwargs.get("port") == 6379

    def test_redis_decode_responses_false(self, graph_module):
        mod, stubs = graph_module
        redis_cls = stubs["redis.asyncio"].Redis
        call_kwargs = redis_cls.call_args[1] if redis_cls.call_args else {}
        assert call_kwargs.get("decode_responses") is False

    def test_checkpointer_created_with_redis_client(self, graph_module):
        mod, stubs = graph_module
        saver_cls = stubs["langgraph.checkpoint.redis.aio"].AsyncRedisSaver
        assert saver_cls.called, "AsyncRedisSaver() should be called during module initialisation"
        # The redis_client kwarg should be the Redis instance
        redis_instance = stubs["redis.asyncio"].Redis.return_value
        call_kwargs = saver_cls.call_args[1] if saver_cls.call_args else {}
        assert call_kwargs.get("redis_client") is redis_instance

    def test_module_exposes_build_agent(self, graph_module):
        mod, _ = graph_module
        assert hasattr(mod, "build_agent"), "graph module should expose build_agent"
        assert callable(mod.build_agent)


# ---------------------------------------------------------------------------
# Tests: build_agent() happy path
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_create_agent_result(self, fresh_mocks):
        mod, stubs = fresh_mocks
        expected = MagicMock(name="agent_result")
        stubs["langchain.agents"].create_agent.return_value = expected

        result = mod.build_agent("gpt-4o", 0.7)
        assert result is expected

    def test_llms_instantiated_with_temperature_and_streaming(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.5)
        stubs["modules.LLMS"].LLMS.assert_called_once_with(temperature=0.5, streaming=True)

    def test_llms_get_model_called_with_model_name(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("claude-3-sonnet", 0.3)
        llms_instance = stubs["modules.LLMS"].LLMS.return_value
        llms_instance.get_model.assert_called_once_with("claude-3-sonnet")

    def test_create_agent_called_with_model(self, fresh_mocks):
        mod, stubs = fresh_mocks
        fake_model = MagicMock(name="my_model")
        stubs["modules.LLMS"].LLMS.return_value.get_model.return_value = fake_model

        mod.build_agent("gpt-4o", 0.7)

        create_agent_mock = stubs["langchain.agents"].create_agent
        call_kwargs = create_agent_mock.call_args[1]
        assert call_kwargs["model"] is fake_model

    def test_create_agent_called_with_system_prompt(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.7)

        create_agent_mock = stubs["langchain.agents"].create_agent
        call_kwargs = create_agent_mock.call_args[1]
        assert call_kwargs["system_prompt"] == mod.SYSTEM_PROMPT

    def test_create_agent_called_with_checkpointer(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.7)

        create_agent_mock = stubs["langchain.agents"].create_agent
        call_kwargs = create_agent_mock.call_args[1]
        assert call_kwargs["checkpointer"] is mod._checkpointer

    def test_create_agent_called_with_tools_list(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.7)

        create_agent_mock = stubs["langchain.agents"].create_agent
        call_kwargs = create_agent_mock.call_args[1]
        tools = call_kwargs["tools"]
        assert isinstance(tools, list)
        assert len(tools) == 3

    def test_tools_include_get_customer_profile(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.7)

        tools = stubs["langchain.agents"].create_agent.call_args[1]["tools"]
        assert stubs["modules.tools"].get_customer_profile in tools

    def test_tools_include_customer_lookalike(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.7)

        tools = stubs["langchain.agents"].create_agent.call_args[1]["tools"]
        assert stubs["modules.tools"].customer_lookalike in tools

    def test_tools_include_assessment_tool(self, fresh_mocks):
        mod, stubs = fresh_mocks
        fake_tool = MagicMock(name="assessment_tool")
        stubs["modules.assessment"]._run_underwriting_assessment.return_value = fake_tool

        mod.build_agent("gpt-4o", 0.7)

        tools = stubs["langchain.agents"].create_agent.call_args[1]["tools"]
        assert fake_tool in tools

    def test_default_mode_is_fast(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.7)
        stubs["modules.assessment"]._run_underwriting_assessment.assert_called_once_with("fast")

    def test_deep_mode_passed_to_assessment(self, fresh_mocks):
        mod, stubs = fresh_mocks
        mod.build_agent("gpt-4o", 0.7, mode="deep")
        stubs["modules.assessment"]._run_underwriting_assessment.assert_called_once_with("deep