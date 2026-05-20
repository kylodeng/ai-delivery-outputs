"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() happy path with various model names, temperatures, and modes
- build_agent() with default mode parameter ("fast")
- build_agent() edge cases: boundary temperatures (0.0, 1.0, extreme values)
- build_agent() with invalid/unsupported mode strings
- Module-level Redis client and checkpointer initialisation
- Environment variable handling for REDIS_HOST

Mocks used:
- langchain.agents.create_agent (patched to avoid real LLM/agent setup)
- redis.asyncio.Redis (patched to avoid real Redis connection)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched to avoid real Redis ops)
- modules.tools.get_customer_profile (patched)
- modules.tools.customer_lookalike (patched)
- modules.assessment._run_underwriting_assessment (patched)
- modules.LLMS.LLMS (patched to avoid real model instantiation)
- backend.agent.prompts.SYSTEM_PROMPT (patched)

TODOs:
- TODO: Integration test for full agent invocation once a test Redis instance is available
- TODO: Test checkpointer persistence behaviour across serverless resets (requires live Redis)
- TODO: Test streaming behaviour of the built agent (requires LLM stub that supports streaming)
- TODO: Validate that the agent correctly routes tool calls (requires graph execution environment)
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake module tree so graph.py can be imported
# without real dependencies installed in CI.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """
    Register lightweight stub modules under the names that graph.py imports
    so that importlib.import_module / the normal import machinery succeed even
    when the real packages are absent.
    """
    stubs = {}

    # langchain.agents
    langchain_pkg = types.ModuleType("langchain")
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_agents.create_agent = MagicMock(return_value=MagicMock(name="agent_instance"))
    langchain_pkg.agents = langchain_agents
    stubs["langchain"] = langchain_pkg
    stubs["langchain.agents"] = langchain_agents

    # redis / redis.asyncio
    redis_pkg = types.ModuleType("redis")
    redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_cls = MagicMock(name="Redis")
    redis_asyncio.Redis = fake_redis_cls
    redis_pkg.asyncio = redis_asyncio
    stubs["redis"] = redis_pkg
    stubs["redis.asyncio"] = redis_asyncio

    # langgraph.checkpoint.redis.aio
    langgraph_pkg = types.ModuleType("langgraph")
    langgraph_checkpoint = types.ModuleType("langgraph.checkpoint")
    langgraph_checkpoint_redis = types.ModuleType("langgraph.checkpoint.redis")
    langgraph_checkpoint_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_saver_cls = MagicMock(name="AsyncRedisSaver")
    langgraph_checkpoint_redis_aio.AsyncRedisSaver = fake_saver_cls
    langgraph_pkg.checkpoint = langgraph_checkpoint
    langgraph_checkpoint.redis = langgraph_checkpoint_redis
    langgraph_checkpoint_redis.aio = langgraph_checkpoint_redis_aio
    stubs["langgraph"] = langgraph_pkg
    stubs["langgraph.checkpoint"] = langgraph_checkpoint
    stubs["langgraph.checkpoint.redis"] = langgraph_checkpoint_redis
    stubs["langgraph.checkpoint.redis.aio"] = langgraph_checkpoint_redis_aio

    # backend.agent.prompts  (relative import .prompts)
    agent_prompts = types.ModuleType("backend.agent.prompts")
    agent_prompts.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
    stubs["backend.agent.prompts"] = agent_prompts

    # modules.tools
    modules_pkg = types.ModuleType("modules")
    modules_tools = types.ModuleType("modules.tools")
    modules_tools.get_customer_profile = MagicMock(name="get_customer_profile")
    modules_tools.customer_lookalike = MagicMock(name="customer_lookalike")
    modules_pkg.tools = modules_tools
    stubs["modules"] = modules_pkg
    stubs["modules.tools"] = modules_tools

    # modules.assessment
    modules_assessment = types.ModuleType("modules.assessment")
    modules_assessment._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment",
        return_value=MagicMock(name="assessment_tool"),
    )
    modules_pkg.assessment = modules_assessment
    stubs["modules.assessment"] = modules_assessment

    # modules.LLMS
    modules_llms = types.ModuleType("modules.LLMS")
    fake_llms_instance = MagicMock(name="llms_instance")
    fake_llms_instance.get_model.return_value = MagicMock(name="model_instance")
    fake_llms_cls = MagicMock(name="LLMS", return_value=fake_llms_instance)
    modules_llms.LLMS = fake_llms_cls
    modules_pkg.LLMS = modules_llms
    stubs["modules.LLMS"] = modules_llms

    return stubs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_sys_modules():
    """
    Inject stub modules before each test and clean up afterwards so that
    the real import state is not polluted.
    """
    stubs = _make_fake_modules()
    originals = {}
    for name, mod in stubs.items():
        originals[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # Also make sure the backend.agent namespace exists so relative imports work
    if "backend" not in sys.modules:
        backend_pkg = types.ModuleType("backend")
        sys.modules["backend"] = backend_pkg
        originals.setdefault("backend", None)
    if "backend.agent" not in sys.modules:
        backend_agent_pkg = types.ModuleType("backend.agent")
        sys.modules["backend.agent"] = backend_agent_pkg
        originals.setdefault("backend.agent", None)

    yield stubs

    # Teardown: restore originals and drop the graph module so it is
    # re-imported fresh on the next test.
    for name, original in originals.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    sys.modules.pop("backend.agent.graph", None)


@pytest.fixture()
def graph_module(_patch_sys_modules):
    """Import (or re-import) graph.py with the stubs in place."""
    # Remove cached copy if present so we get a fresh import each time
    sys.modules.pop("backend.agent.graph", None)

    # graph.py lives at backend/agent/graph.py; expose it as a top-level
    # importable name for the tests.
    import importlib.util, pathlib

    graph_path = pathlib.Path(__file__).parent / "graph.py"
    spec = importlib.util.spec_from_file_location("backend.agent.graph", graph_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["backend.agent.graph"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mock_create_agent(_patch_sys_modules):
    return _patch_sys_modules["langchain.agents"].create_agent


@pytest.fixture()
def mock_llms_cls(_patch_sys_modules):
    return _patch_sys_modules["modules.LLMS"].LLMS


@pytest.fixture()
def mock_run_assessment(_patch_sys_modules):
    return _patch_sys_modules["modules.assessment"]._run_underwriting_assessment


@pytest.fixture()
def mock_redis_cls(_patch_sys_modules):
    return _patch_sys_modules["redis.asyncio"].Redis


@pytest.fixture()
def mock_saver_cls(_patch_sys_modules):
    return _patch_sys_modules["langgraph.checkpoint.redis.aio"].AsyncRedisSaver


# ---------------------------------------------------------------------------
# Tests – module-level initialisation
# ---------------------------------------------------------------------------

class TestModuleInitialisation:
    """Tests that verify the module-level Redis and checkpointer setup."""

    def test_redis_client_created_on_import(self, graph_module, mock_redis_cls):
        """Redis client should be instantiated at module load time."""
        mock_redis_cls.assert_called()

    def test_redis_client_uses_default_host(self, monkeypatch, _patch_sys_modules):
        """When REDIS_HOST env var is absent, 'localhost' should be used."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        sys.modules.pop("backend.agent.graph", None)

        import importlib.util, pathlib

        redis_cls = _patch_sys_modules["redis.asyncio"].Redis
        redis_cls.reset_mock()

        graph_path = pathlib.Path(__file__).parent / "graph.py"
        spec = importlib.util.spec_from_file_location("backend.agent.graph", graph_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["backend.agent.graph"] = mod
        spec.loader.exec_module(mod)

        redis_cls.assert_called_once()
        _, kwargs = redis_cls.call_args
        assert kwargs.get("host") == "localhost"
        assert kwargs.get("port") == 6379

    def test_redis_client_uses_env_host(self, monkeypatch, _patch_sys_modules):
        """When REDIS_HOST env var is set it should be forwarded to Redis."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-server")
        sys.modules.pop("backend.agent.graph", None)

        import importlib.util, pathlib

        redis_cls = _patch_sys_modules["redis.asyncio"].Redis
        redis_cls.reset_mock()

        graph_path = pathlib.Path(__file__).parent / "graph.py"
        spec = importlib.util.spec_from_file_location("backend.agent.graph", graph_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["backend.agent.graph"] = mod
        spec.loader.exec_module(mod)

        redis_cls.assert_called_once()
        _, kwargs = redis_cls.call_args
        assert kwargs.get("host") == "my-redis-server"

    def test_redis_client_decode_responses_false(self, _patch_sys_modules, monkeypatch):
        """decode_responses must be False (binary checkpointer requirement)."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        sys.modules.pop("backend.agent.graph", None)

        import importlib.util, pathlib

        redis_cls = _patch_sys_modules["redis.asyncio"].Redis
        redis_cls.reset_mock()

        graph_path = pathlib.Path(__file__).parent / "graph.py"
        spec = importlib.util.spec_from_file_location("backend.agent.graph", graph_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["backend.agent.graph"] = mod
        spec.loader.exec_module(mod)

        _, kwargs = redis_cls.call_args
        assert kwargs.get("decode_responses") is False

    def test_checkpointer_created_on_import(self, graph_module, mock_saver_cls):
        """AsyncRedisSaver should be instantiated at module load time."""
        mock_saver_cls.assert_called()

    def test_checkpointer_receives_redis_client(self, graph_module, mock_saver_cls, mock_redis_cls):
        """AsyncRedisSaver should receive the Redis client instance."""
        _, kwargs = mock_saver_cls.call_args
        # The redis_client kwarg should be the return value of Redis(...)
        assert kwargs.get("redis_client") == mock_redis_cls.return_value


# ---------------------------------------------------------------------------
# Tests – build_agent happy paths
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:
    """Tests for successful agent construction."""

    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-4o-mini",
        "claude-3-5-sonnet",
        "gemini-pro",
    ])
    def test_returns_agent_for_valid_model_names(
        self, graph_module, mock_create_agent, mock_llms_cls, model_name
    ):
        agent = graph_module.build_agent(model_name=model_name, temperature=0.5)
        assert agent is mock_create_agent.return_value

    @pytest.mark.parametrize("temperature", [0.0, 0.1, 0.5, 0.9, 1.0])
    def test_returns_agent_for_valid_temperatures(
        self, graph_module, mock_create_agent, mock_llms_cls, temperature
    ):
        agent = graph_module.build_agent(model_name="gpt-4o", temperature=temperature)
        assert agent is mock_create_agent.return_value

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_returns_agent_for_supported_modes(
        self, graph_module, mock_create_agent, mock_run_assessment, mode
    ):
        agent = graph_module.build_agent(
            model_name="gpt-4o", temperature=0.7, mode=mode
        )
        assert agent is mock_create_agent.return_value
        mock_run_assessment.assert_called_with(mode)

    def test_default_mode_is_fast(
        self, graph_module, mock_create_agent, mock_run_assessment
    ):
        """build_agent should default to mode='fast' when not specified."""
        graph_module.build_agent(model_name="gpt-4o", temperature=0.5)
        mock_run_assessment.assert_called_with("fast")

    def test_llms_instantiated_with_correct_args(
        self, graph_module, mock_llms_cls
    ):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.3)
        mock_llms_cls.assert_called_once_with(temperature=0.3, streaming=True)

    def test_get_model_called_with_model_name(
        self, graph_module, mock_llms_cls
    ):
        graph_module.build_agent(model_name="claude-3-5-sonnet", temperature=0.2)
        llms_instance = mock_llms_cls.return_value
        llms_instance.get_model.assert_called_once_with("claude-3-5-sonnet")

    def test_create_agent_called_with_system_prompt(
        self, graph_module, mock_create_agent, _patch_sys_modules
    ):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.5)
        _, kwargs = mock_create_agent.call_args
        assert kwargs.get("system_prompt") == "FAKE_SYSTEM_PROMPT"

    def test_create_agent_receives_checkpointer(
        self, graph_module, mock_create_agent, mock_saver_cls
    ):
        graph_module.build_agent(model_name="gpt