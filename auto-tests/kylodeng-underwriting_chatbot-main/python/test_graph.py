"""
Tests for backend/agent/graph.py

What is tested:
- build_agent(): happy path with various model names, temperatures, and modes
- build_agent(): edge cases (boundary temperatures, unknown mode strings)
- build_agent(): error conditions (invalid model name propagation, LLMS failures)
- Module-level initialisation of Redis client and checkpointer
- Tool list composition passed to create_agent
- SYSTEM_PROMPT forwarding to create_agent

Mocks used:
- unittest.mock.patch / MagicMock for:
  - redis.asyncio.Redis              (no real Redis connection)
  - langgraph.checkpoint.redis.aio.AsyncRedisSaver
  - langchain.agents.create_agent
  - modules.tools.get_customer_profile
  - modules.tools.customer_lookalike
  - modules.assessment._run_underwriting_assessment
  - modules.LLMS.LLMS
  - agent.prompts.SYSTEM_PROMPT

TODOs:
- TODO: Integration test verifying agent can actually invoke tools end-to-end
        (requires a real or in-process Redis and LLM stub server).
- TODO: Test async checkpointer setup once AsyncRedisSaver async interface is finalised.
- TODO: Test streaming behaviour of the returned agent (needs LLM streaming harness).
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# Helpers: build a fully-mocked module environment so we never touch real
# external services during import or test execution.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """Return a dict of fake modules that must be injected before importing graph."""

    # --- redis.asyncio ---
    redis_asyncio = types.ModuleType("redis.asyncio")
    fake_redis_instance = MagicMock(name="FakeRedisInstance")
    redis_asyncio.Redis = MagicMock(name="Redis", return_value=fake_redis_instance)
    redis_mod = types.ModuleType("redis")
    redis_mod.asyncio = redis_asyncio

    # --- langgraph.checkpoint.redis.aio ---
    lg_cp_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    fake_checkpointer = MagicMock(name="FakeCheckpointer")
    lg_cp_redis_aio.AsyncRedisSaver = MagicMock(
        name="AsyncRedisSaver", return_value=fake_checkpointer
    )
    lg_cp = types.ModuleType("langgraph.checkpoint")
    lg_cp_redis = types.ModuleType("langgraph.checkpoint.redis")
    lg = types.ModuleType("langgraph")
    lg.checkpoint = lg_cp
    lg_cp.redis = lg_cp_redis
    lg_cp_redis.aio = lg_cp_redis_aio

    # --- langchain.agents ---
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_agents.create_agent = MagicMock(name="create_agent")
    langchain_mod = types.ModuleType("langchain")
    langchain_mod.agents = langchain_agents

    # --- modules.tools ---
    modules_tools = types.ModuleType("modules.tools")
    modules_tools.get_customer_profile = MagicMock(name="get_customer_profile")
    modules_tools.customer_lookalike = MagicMock(name="customer_lookalike")

    # --- modules.assessment ---
    modules_assessment = types.ModuleType("modules.assessment")
    fake_assessment_tool = MagicMock(name="FakeAssessmentTool")
    modules_assessment._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment", return_value=fake_assessment_tool
    )

    # --- modules.LLMS ---
    modules_llms = types.ModuleType("modules.LLMS")
    fake_llms_instance = MagicMock(name="FakeLLMSInstance")
    fake_model = MagicMock(name="FakeModel")
    fake_llms_instance.get_model = MagicMock(return_value=fake_model)
    modules_llms.LLMS = MagicMock(name="LLMS", return_value=fake_llms_instance)

    # --- modules (parent) ---
    modules_mod = types.ModuleType("modules")
    modules_mod.tools = modules_tools
    modules_mod.assessment = modules_assessment
    modules_mod.LLMS = modules_llms

    # --- agent.prompts ---
    agent_prompts = types.ModuleType("agent.prompts")
    agent_prompts.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"

    return {
        "redis": redis_mod,
        "redis.asyncio": redis_asyncio,
        "langgraph": lg,
        "langgraph.checkpoint": lg_cp,
        "langgraph.checkpoint.redis": lg_cp_redis,
        "langgraph.checkpoint.redis.aio": lg_cp_redis_aio,
        "langchain": langchain_mod,
        "langchain.agents": langchain_agents,
        "modules": modules_mod,
        "modules.tools": modules_tools,
        "modules.assessment": modules_assessment,
        "modules.LLMS": modules_llms,
        "agent": types.ModuleType("agent"),
        "agent.prompts": agent_prompts,
    }


@pytest.fixture(autouse=True)
def _clean_graph_import():
    """Remove cached graph module before/after every test for isolation."""
    keys_to_remove = [k for k in sys.modules if "agent.graph" in k or k == "agent.graph"]
    for k in keys_to_remove:
        del sys.modules[k]
    yield
    keys_to_remove = [k for k in sys.modules if "agent.graph" in k or k == "agent.graph"]
    for k in keys_to_remove:
        del sys.modules[k]


@pytest.fixture()
def fake_mods():
    """Inject fake modules and return them for assertion use."""
    fakes = _make_fake_modules()
    original = {}
    for name, mod in fakes.items():
        original[name] = sys.modules.get(name)
        sys.modules[name] = mod
    yield fakes
    for name, orig in original.items():
        if orig is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig


def _import_graph(fake_mods):
    """Import (or re-import) agent.graph with fakes in place."""
    # Ensure the parent 'agent' package resolves to our fake
    if "agent" not in sys.modules:
        sys.modules["agent"] = fake_mods["agent"]
    import importlib
    graph = importlib.import_module("backend.agent.graph") if "backend" in sys.modules else None
    # Fallback: direct import path used when running tests from repo root
    spec = importlib.util.spec_from_file_location(
        "agent.graph", "backend/agent/graph.py"
    )
    if spec is None:
        pytest.skip("backend/agent/graph.py not found on disk; skipping import-dependent tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent.graph"] = module
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# Tests: module-level initialisation
# ===========================================================================

class TestModuleLevelInit:
    def test_redis_client_created_with_env_host(self, fake_mods, monkeypatch):
        """Redis() should be called with the REDIS_HOST env var when set."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        _import_graph(fake_mods)
        fake_mods["redis.asyncio"].Redis.assert_called_once_with(
            host="my-redis-host", port=6379, decode_responses=False
        )

    def test_redis_client_created_with_default_host(self, fake_mods, monkeypatch):
        """Redis() should default to 'localhost' when REDIS_HOST is not set."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        _import_graph(fake_mods)
        fake_mods["redis.asyncio"].Redis.assert_called_once_with(
            host="localhost", port=6379, decode_responses=False
        )

    def test_async_redis_saver_receives_redis_client(self, fake_mods, monkeypatch):
        """AsyncRedisSaver must be initialised with the Redis instance."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        _import_graph(fake_mods)
        fake_redis_instance = fake_mods["redis.asyncio"].Redis.return_value
        fake_mods["langgraph.checkpoint.redis.aio"].AsyncRedisSaver.assert_called_once_with(
            redis_client=fake_redis_instance
        )


# ===========================================================================
# Tests: build_agent — happy paths
# ===========================================================================

class TestBuildAgentHappyPath:
    @pytest.mark.parametrize("model_name,temperature,mode", [
        ("gpt-4o", 0.0, "fast"),
        ("gpt-4o-mini", 0.5, "fast"),
        ("gpt-4o", 1.0, "deep"),
        ("claude-3-5-sonnet", 0.3, "deep"),
        ("gpt-4o-mini", 0.7, "fast"),
    ])
    def test_returns_agent(self, fake_mods, monkeypatch, model_name, temperature, mode):
        """build_agent should return whatever create_agent returns."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)

        fake_agent = MagicMock(name="FakeAgent")
        fake_mods["langchain.agents"].create_agent.return_value = fake_agent

        result = graph.build_agent(model_name, temperature, mode)
        assert result is fake_agent

    def test_llms_instantiated_with_correct_args(self, fake_mods, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)
        fake_mods["modules.LLMS"].LLMS.reset_mock()

        graph.build_agent("gpt-4o", 0.2, "fast")

        fake_mods["modules.LLMS"].LLMS.assert_called_once_with(temperature=0.2, streaming=True)

    def test_get_model_called_with_model_name(self, fake_mods, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)
        llms_instance = fake_mods["modules.LLMS"].LLMS.return_value

        graph.build_agent("gpt-4o-mini", 0.5, "fast")

        llms_instance.get_model.assert_called_once_with("gpt-4o-mini")

    def test_create_agent_called_with_correct_kwargs(self, fake_mods, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)

        fake_model = fake_mods["modules.LLMS"].LLMS.return_value.get_model.return_value
        fake_checkpointer = fake_mods["langgraph.checkpoint.redis.aio"].AsyncRedisSaver.return_value
        system_prompt = fake_mods["agent.prompts"].SYSTEM_PROMPT

        graph.build_agent("gpt-4o", 0.0, "fast")

        fake_mods["langchain.agents"].create_agent.assert_called_once_with(
            model=fake_model,
            tools=graph.build_agent.__wrapped__  # not used; checked below
            if hasattr(graph.build_agent, "__wrapped__") else None,
            system_prompt=system_prompt,
            checkpointer=fake_checkpointer,
        )
        # More robust: inspect the actual call kwargs
        call_kwargs = fake_mods["langchain.agents"].create_agent.call_args[1]
        assert call_kwargs["model"] is fake_model
        assert call_kwargs["system_prompt"] == system_prompt
        assert call_kwargs["checkpointer"] is fake_checkpointer

    def test_tools_list_has_three_elements(self, fake_mods, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)
        fake_mods["langchain.agents"].create_agent.reset_mock()

        graph.build_agent("gpt-4o", 0.0, "fast")

        call_kwargs = fake_mods["langchain.agents"].create_agent.call_args[1]
        assert len(call_kwargs["tools"]) == 3

    def test_tools_contains_get_customer_profile(self, fake_mods, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)
        fake_mods["langchain.agents"].create_agent.reset_mock()

        graph.build_agent("gpt-4o", 0.0, "fast")

        call_kwargs = fake_mods["langchain.agents"].create_agent.call_args[1]
        assert fake_mods["modules.tools"].get_customer_profile in call_kwargs["tools"]

    def test_tools_contains_customer_lookalike(self, fake_mods, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)
        fake_mods["langchain.agents"].create_agent.reset_mock()

        graph.build_agent("gpt-4o", 0.0, "fast")

        call_kwargs = fake_mods["langchain.agents"].create_agent.call_args[1]
        assert fake_mods["modules.tools"].customer_lookalike in call_kwargs["tools"]

    def test_tools_contains_assessment_result(self, fake_mods, monkeypatch):
        """The result of _run_underwriting_assessment(mode) — not the callable itself — is in tools."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)
        fake_mods["langchain.agents"].create_agent.reset_mock()
        fake_assessment_tool = fake_mods["modules.assessment"]._run_underwriting_assessment.return_value

        graph.build_agent("gpt-4o", 0.0, "fast")

        call_kwargs = fake_mods["langchain.agents"].create_agent.call_args[1]
        assert fake_assessment_tool in call_kwargs["tools"]


# ===========================================================================
# Tests: build_agent — mode forwarding
# ===========================================================================

class TestBuildAgentModeForwarding:
    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_assessment_called_with_mode(self, fake_mods, monkeypatch, mode):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = _import_graph(fake_mods)
        assessment_mock = fake_mods["modules.assessment"]._run_underwriting_assessment
        assessment_mock.reset_mock()

        graph.build_agent("gpt-4o", 0.5, mode)

        assessment_mock.assert_called_once_with(mode)

    def test_unknown_mode_still_forwarded(self, fake_mods, monkeypatch):
        """