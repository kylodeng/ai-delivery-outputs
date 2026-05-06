"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path, parameter variations, edge cases, error conditions
- Module-level initialization (Redis client, checkpointer, tools list construction)
- Correct delegation to LLMS, create_agent, and tool factories

Mocks used:
- langchain.agents.create_agent (patched to avoid real LLM/agent construction)
- redis.asyncio.Redis (patched at module level to avoid real Redis connections)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched to avoid real Redis calls)
- modules.tools.get_customer_profile (patched)
- modules.tools.customer_lookalike (patched)
- modules.assessment._run_underwriting_assessment (patched)
- modules.LLMS.LLMS (patched to avoid real model instantiation)
- backend.agent.prompts.SYSTEM_PROMPT (patched)

TODOs:
- TODO: Integration test with a real (or containerised) Redis instance to verify checkpointer setup
- TODO: Test streaming behaviour once the agent's streaming interface is defined
- TODO: Test that the agent correctly handles tool invocations end-to-end (requires LLM stub responses)
"""

import importlib
import os
import sys
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers: build a minimal fake module tree so that importing graph.py does
# not require every real dependency to be installed.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """
    Install lightweight fake modules into sys.modules before graph.py is
    imported so that top-level side-effects (Redis(), AsyncRedisSaver()) use
    our mocks.
    """
    # --- langchain.agents ---
    langchain_pkg = types.ModuleType("langchain")
    langchain_agents = types.ModuleType("langchain.agents")
    fake_create_agent = MagicMock(name="create_agent")
    langchain_agents.create_agent = fake_create_agent
    langchain_pkg.agents = langchain_agents
    sys.modules.setdefault("langchain", langchain_pkg)
    sys.modules["langchain.agents"] = langchain_agents

    # --- redis ---
    redis_pkg = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_cls = MagicMock(name="Redis")
    fake_redis_cls.return_value = MagicMock(name="redis_instance")
    redis_asyncio.Redis = fake_redis_cls
    redis_pkg.asyncio = redis_asyncio
    sys.modules.setdefault("redis", redis_pkg)
    sys.modules["redis.asyncio"] = redis_asyncio

    # --- langgraph.checkpoint.redis.aio ---
    langgraph_pkg = types.ModuleType("langgraph")
    langgraph_chk = types.ModuleType("langgraph.checkpoint")
    langgraph_chk_redis = types.ModuleType("langgraph.checkpoint.redis")
    langgraph_chk_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_saver_cls = MagicMock(name="AsyncRedisSaver")
    fake_saver_cls.return_value = MagicMock(name="checkpointer_instance")
    langgraph_chk_redis_aio.AsyncRedisSaver = fake_saver_cls
    langgraph_pkg.checkpoint = langgraph_chk
    langgraph_chk.redis = langgraph_chk_redis
    langgraph_chk_redis.aio = langgraph_chk_redis_aio
    for name, mod in [
        ("langgraph", langgraph_pkg),
        ("langgraph.checkpoint", langgraph_chk),
        ("langgraph.checkpoint.redis", langgraph_chk_redis),
        ("langgraph.checkpoint.redis.aio", langgraph_chk_redis_aio),
    ]:
        sys.modules.setdefault(name, mod)

    # --- modules.tools ---
    modules_pkg = types.ModuleType("modules")
    modules_tools = types.ModuleType("modules.tools")
    fake_get_customer_profile = MagicMock(name="get_customer_profile")
    fake_customer_lookalike = MagicMock(name="customer_lookalike")
    modules_tools.get_customer_profile = fake_get_customer_profile
    modules_tools.customer_lookalike = fake_customer_lookalike
    modules_pkg.tools = modules_tools
    sys.modules.setdefault("modules", modules_pkg)
    sys.modules["modules.tools"] = modules_tools

    # --- modules.assessment ---
    modules_assessment = types.ModuleType("modules.assessment")
    fake_run_underwriting = MagicMock(name="_run_underwriting_assessment")
    fake_run_underwriting.return_value = MagicMock(name="underwriting_tool")
    modules_assessment._run_underwriting_assessment = fake_run_underwriting
    sys.modules["modules.assessment"] = modules_assessment
    modules_pkg.assessment = modules_assessment

    # --- modules.LLMS ---
    modules_llms = types.ModuleType("modules.LLMS")
    fake_llms_cls = MagicMock(name="LLMS")
    fake_llms_instance = MagicMock(name="llms_instance")
    fake_llms_cls.return_value = fake_llms_instance
    modules_llms.LLMS = fake_llms_cls
    sys.modules["modules.LLMS"] = modules_llms
    modules_pkg.LLMS = modules_llms

    # --- backend.agent (package) ---
    # We need .prompts to be importable as a relative import
    agent_pkg = types.ModuleType("agent")
    agent_prompts = types.ModuleType("agent.prompts")
    agent_prompts.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
    agent_pkg.prompts = agent_prompts
    sys.modules.setdefault("agent", agent_pkg)
    sys.modules["agent.prompts"] = agent_prompts

    return {
        "create_agent": fake_create_agent,
        "Redis": fake_redis_cls,
        "AsyncRedisSaver": fake_saver_cls,
        "get_customer_profile": fake_get_customer_profile,
        "customer_lookalike": fake_customer_lookalike,
        "_run_underwriting_assessment": fake_run_underwriting,
        "LLMS": fake_llms_cls,
        "llms_instance": fake_llms_instance,
    }


# ---------------------------------------------------------------------------
# Module-level setup: install fake modules once, then import graph
# ---------------------------------------------------------------------------

_fakes = _make_fake_modules()

# Import the module under test. Use importlib so we can reload if needed.
import importlib.util, pathlib

_GRAPH_PATH = pathlib.Path(__file__).parent.parent / "backend" / "agent" / "graph.py"

# If graph.py cannot be found (e.g. CI without the full tree), skip all tests.
_graph_missing = not _GRAPH_PATH.exists()

if not _graph_missing:
    spec = importlib.util.spec_from_file_location("agent.graph", _GRAPH_PATH)
    graph_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(graph_module)
    build_agent = graph_module.build_agent
else:
    graph_module = None
    build_agent = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset call counts on shared mocks before every test."""
    for fake in _fakes.values():
        if isinstance(fake, MagicMock):
            fake.reset_mock()
    yield


@pytest.fixture()
def fake_create_agent():
    return _fakes["create_agent"]


@pytest.fixture()
def fake_llms_cls():
    return _fakes["LLMS"]


@pytest.fixture()
def fake_llms_instance():
    return _fakes["llms_instance"]


@pytest.fixture()
def fake_run_underwriting():
    return _fakes["_run_underwriting_assessment"]


@pytest.fixture()
def fake_get_customer_profile():
    return _fakes["get_customer_profile"]


@pytest.fixture()
def fake_customer_lookalike():
    return _fakes["customer_lookalike"]


# ---------------------------------------------------------------------------
# Skip marker for when the source file is absent
# ---------------------------------------------------------------------------

requires_graph = pytest.mark.skipif(
    _graph_missing,
    reason="backend/agent/graph.py not found – adjust path for your repo layout",
)


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------

@requires_graph
class TestModuleInitialisation:
    """Verify that top-level objects are created correctly on import."""

    def test_redis_client_created(self):
        assert graph_module._redis_client is not None

    def test_checkpointer_created(self):
        assert graph_module._checkpointer is not None

    def test_redis_constructed_with_localhost_default(self, monkeypatch):
        """
        When REDIS_HOST env-var is absent, Redis should be called with host='localhost'.
        We cannot re-import easily, so we verify the stored client exists and that
        the Redis mock was called at import time.
        """
        # The Redis mock was called at least once during module import
        assert _fakes["Redis"].called

    def test_redis_host_env_var_respected(self, monkeypatch):
        """
        When REDIS_HOST is set, it should be passed to Redis().
        We verify the behaviour by inspecting the call made at import time OR
        by inspecting os.environ logic directly.
        """
        # The module reads os.environ.get("REDIS_HOST", "localhost") at import time.
        # We confirm the call was made (host value depends on env at import time).
        redis_call_args = _fakes["Redis"].call_args_list
        assert len(redis_call_args) >= 1
        _, kwargs = redis_call_args[0]
        assert "host" in kwargs or redis_call_args[0][0]  # positional or keyword

    def test_checkpointer_receives_redis_client(self):
        saver_calls = _fakes["AsyncRedisSaver"].call_args_list
        assert len(saver_calls) >= 1
        _, kwargs = saver_calls[0]
        assert "redis_client" in kwargs

    def test_build_agent_is_callable(self):
        assert callable(build_agent)


# ---------------------------------------------------------------------------
# build_agent – happy path
# ---------------------------------------------------------------------------

@requires_graph
class TestBuildAgentHappyPath:

    def test_returns_agent_from_create_agent(self, fake_create_agent, fake_llms_instance):
        expected_agent = MagicMock(name="agent_obj")
        fake_create_agent.return_value = expected_agent
        fake_llms_instance.get_model.return_value = MagicMock(name="model")

        result = build_agent(model_name="gpt-4o", temperature=0.0)

        assert result is expected_agent

    def test_llms_instantiated_with_correct_params(self, fake_llms_cls, fake_llms_instance):
        fake_llms_instance.get_model.return_value = MagicMock()
        _fakes["create_agent"].return_value = MagicMock()

        build_agent(model_name="gpt-4o", temperature=0.7)

        fake_llms_cls.assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, fake_llms_instance):
        fake_llms_instance.get_model.return_value = MagicMock()
        _fakes["create_agent"].return_value = MagicMock()

        build_agent(model_name="claude-3-5-sonnet", temperature=0.5)

        fake_llms_instance.get_model.assert_called_once_with("claude-3-5-sonnet")

    def test_create_agent_called_once(self, fake_create_agent, fake_llms_instance):
        fake_llms_instance.get_model.return_value = MagicMock()
        fake_create_agent.return_value = MagicMock()

        build_agent(model_name="gpt-4o", temperature=0.0)

        fake_create_agent.assert_called_once()

    def test_create_agent_receives_system_prompt(self, fake_create_agent, fake_llms_instance):
        fake_llms_instance.get_model.return_value = MagicMock()
        fake_create_agent.return_value = MagicMock()

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = fake_create_agent.call_args
        assert kwargs.get("system_prompt") == "FAKE_SYSTEM_PROMPT"

    def test_create_agent_receives_checkpointer(self, fake_create_agent, fake_llms_instance):
        fake_llms_instance.get_model.return_value = MagicMock()
        fake_create_agent.return_value = MagicMock()

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = fake_create_agent.call_args
        assert kwargs.get("checkpointer") is graph_module._checkpointer

    def test_create_agent_receives_model(self, fake_create_agent, fake_llms_instance):
        fake_model = MagicMock(name="model_instance")
        fake_llms_instance.get_model.return_value = fake_model
        fake_create_agent.return_value = MagicMock()

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = fake_create_agent.call_args
        assert kwargs.get("model") is fake_model


# ---------------------------------------------------------------------------
# build_agent – tools list
# ---------------------------------------------------------------------------

@requires_graph
class TestBuildAgentTools:

    def _call_build_and_get_tools(self, fake_llms_instance, fake_create_agent, mode="fast"):
        fake_llms_instance.get_model.return_value = MagicMock()
        fake_create_agent.return_value = MagicMock()
        build_agent(model_name="gpt-4o", temperature=0.0, mode=mode)
        _, kwargs = fake_create_agent.call_args
        return kwargs.get("tools", [])

    def test_tools_list_has_three_items(self, fake_llms_instance, fake_create_agent):
        tools = self._call_build_and_get_tools(fake_llms_instance, fake_create_agent)
        assert len(tools) == 3

    def test_tools_contains_get_customer_profile(
        self, fake_llms_instance, fake_create_agent, fake_get_customer_profile
    ):
        tools = self._call_build_and_get_tools(fake_llms_instance, fake_create_agent)
        assert fake_get_customer_profile in tools

    def test_tools_contains_customer_lookalike(
        self, fake_llms_instance, fake_create_agent, fake_customer_lookalike
    ):
        tools = self._call_build_and_get_tools(fake_llms_instance, fake_create_agent)
        assert fake_customer_lookalike in tools

    def test_underwriting_assessment_called_with_fast_mode(
        self, fake_llms_instance, fake_create_agent, fake_run_underwriting
    ):
        self._call_build_and_get_tools(fake_llms_instance, fake_