"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path, edge cases, error conditions, boundary values
- Module-level Redis client and checkpointer initialisation
- Model construction via LLMS
- Tool list assembly (get_customer_profile, _run_underwriting_assessment, customer_lookalike)
- create_agent invocation with correct arguments

Mocks used:
- redis.asyncio.Redis (prevent real Redis connections)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (prevent real Redis interactions)
- langchain.agents.create_agent (prevent real LLM/agent construction)
- modules.LLMS.LLMS (prevent real model loading)
- modules.tools.get_customer_profile (stub tool)
- modules.tools.customer_lookalike (stub tool)
- modules.assessment._run_underwriting_assessment (stub tool factory)
- backend.agent.prompts.SYSTEM_PROMPT (stable sentinel value)
- os.environ (REDIS_HOST variable)

TODOs:
- TODO: Integration test with a real (or containerised) Redis instance to verify
        checkpointer persistence across calls — requires Redis container in CI.
- TODO: Test streaming behaviour of the returned agent once agent interface is clarified.
- TODO: Validate SYSTEM_PROMPT content against expected prompt spec when spec is finalised.
"""

import importlib
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / shared sentinels
# ---------------------------------------------------------------------------

SENTINEL_MODEL = MagicMock(name="sentinel_model")
SENTINEL_AGENT = MagicMock(name="sentinel_agent")
SENTINEL_TOOL_PROFILE = MagicMock(name="get_customer_profile_tool")
SENTINEL_TOOL_LOOKALIKE = MagicMock(name="customer_lookalike_tool")
SENTINEL_SYSTEM_PROMPT = "SENTINEL_SYSTEM_PROMPT"


def _make_assessment_tool(mode: str) -> MagicMock:
    """Return a distinct mock for each mode so tests can assert on identity."""
    m = MagicMock(name=f"assessment_tool_{mode}")
    return m


# ---------------------------------------------------------------------------
# Fixture: patch every external dependency before the module is imported so
# that importing graph.py itself does not attempt real Redis connections.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_all(monkeypatch):
    """
    Patch all external dependencies at the module level so that
    `backend.agent.graph` can be safely imported and re-imported in each test.
    """
    # Keep a registry of patches so individual tests can inspect call args.
    patches = {}

    # ---- Redis ----
    mock_redis_instance = AsyncMock(name="redis_instance")
    mock_redis_cls = MagicMock(return_value=mock_redis_instance, name="Redis")
    patches["Redis"] = mock_redis_cls
    patches["redis_instance"] = mock_redis_instance

    # ---- AsyncRedisSaver ----
    mock_saver_instance = MagicMock(name="checkpointer_instance")
    mock_saver_cls = MagicMock(return_value=mock_saver_instance, name="AsyncRedisSaver")
    patches["AsyncRedisSaver"] = mock_saver_cls
    patches["saver_instance"] = mock_saver_instance

    # ---- create_agent ----
    mock_create_agent = MagicMock(return_value=SENTINEL_AGENT, name="create_agent")
    patches["create_agent"] = mock_create_agent

    # ---- LLMS ----
    mock_llms_instance = MagicMock(name="llms_instance")
    mock_llms_instance.get_model.return_value = SENTINEL_MODEL
    mock_llms_cls = MagicMock(return_value=mock_llms_instance, name="LLMS")
    patches["LLMS"] = mock_llms_cls
    patches["llms_instance"] = mock_llms_instance

    # ---- tools ----
    patches["get_customer_profile"] = SENTINEL_TOOL_PROFILE
    patches["customer_lookalike"] = SENTINEL_TOOL_LOOKALIKE

    # ---- _run_underwriting_assessment ----
    mock_run_assessment = MagicMock(side_effect=_make_assessment_tool, name="_run_underwriting_assessment")
    patches["_run_underwriting_assessment"] = mock_run_assessment

    # ---- SYSTEM_PROMPT ----
    patches["SYSTEM_PROMPT"] = SENTINEL_SYSTEM_PROMPT

    with (
        patch("redis.asyncio.Redis", mock_redis_cls),
        patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", mock_saver_cls),
        patch("langchain.agents.create_agent", mock_create_agent),
        patch.dict("sys.modules", _build_stub_modules(patches)),
    ):
        # Force re-import of the graph module with all stubs in place.
        _remove_graph_module()
        import backend.agent.graph as graph_module  # noqa: F401  (side-effects matter)

        patches["graph_module"] = graph_module
        yield patches

    # Cleanup: remove the module so the next test starts fresh.
    _remove_graph_module()


def _remove_graph_module():
    """Remove cached graph module (and agent sub-package) from sys.modules."""
    for key in list(sys.modules.keys()):
        if "backend.agent.graph" in key:
            del sys.modules[key]


def _build_stub_modules(patches: dict) -> dict:
    """
    Build a dict of stub sys.modules entries so that the graph module's
    internal imports resolve to our mocks without touching real packages.
    """
    stub_modules: dict[str, types.ModuleType] = {}

    # -- backend.agent.prompts --
    prompts_mod = types.ModuleType("backend.agent.prompts")
    prompts_mod.SYSTEM_PROMPT = patches["SYSTEM_PROMPT"]  # type: ignore[attr-defined]
    stub_modules["backend"] = _ensure_package("backend")
    stub_modules["backend.agent"] = _ensure_package("backend.agent")
    stub_modules["backend.agent.prompts"] = prompts_mod

    # -- modules.tools --
    tools_mod = types.ModuleType("modules.tools")
    tools_mod.get_customer_profile = patches["get_customer_profile"]  # type: ignore[attr-defined]
    tools_mod.customer_lookalike = patches["customer_lookalike"]  # type: ignore[attr-defined]
    stub_modules["modules"] = _ensure_package("modules")
    stub_modules["modules.tools"] = tools_mod

    # -- modules.assessment --
    assessment_mod = types.ModuleType("modules.assessment")
    assessment_mod._run_underwriting_assessment = patches["_run_underwriting_assessment"]  # type: ignore[attr-defined]
    stub_modules["modules.assessment"] = assessment_mod

    # -- modules.LLMS --
    llms_mod = types.ModuleType("modules.LLMS")
    llms_mod.LLMS = patches["LLMS"]  # type: ignore[attr-defined]
    stub_modules["modules.LLMS"] = llms_mod

    return stub_modules


def _ensure_package(name: str) -> types.ModuleType:
    """Return existing module from sys.modules or create a blank package stub."""
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    mod.__path__ = []  # type: ignore[attr-defined]  # mark as package
    return mod


# ---------------------------------------------------------------------------
# Convenience accessor so each test can get the (freshly imported) graph module
# ---------------------------------------------------------------------------


@pytest.fixture()
def graph(_patch_all):
    return _patch_all["graph_module"]


@pytest.fixture()
def patches(_patch_all):
    return _patch_all


# ===========================================================================
# Tests: module-level initialisation
# ===========================================================================


class TestModuleLevelInitialisation:
    """Verify that importing graph.py triggers correct Redis/checkpointer setup."""

    def test_redis_constructed_with_default_host(self, patches):
        """When REDIS_HOST env var is absent, Redis should use 'localhost'."""
        # The module was imported during the fixture with no REDIS_HOST override;
        # verify the constructor was called with host='localhost'.
        call_kwargs = patches["Redis"].call_args
        assert call_kwargs is not None, "Redis() was never called"
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        args = call_kwargs.args if call_kwargs.args else ()

        host_value = kwargs.get("host") or (args[0] if args else None)
        assert host_value == "localhost"

    def test_redis_constructed_with_correct_port(self, patches):
        call_kwargs = patches["Redis"].call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        port_value = kwargs.get("port")
        assert port_value == 6379

    def test_redis_decode_responses_is_false(self, patches):
        call_kwargs = patches["Redis"].call_args
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("decode_responses") is False

    def test_checkpointer_constructed_with_redis_client(self, patches):
        saver_call = patches["AsyncRedisSaver"].call_args
        assert saver_call is not None, "AsyncRedisSaver() was never called"
        kwargs = saver_call.kwargs if saver_call.kwargs else {}
        assert kwargs.get("redis_client") is patches["redis_instance"]

    def test_module_exposes_build_agent(self, graph):
        assert callable(graph.build_agent)


class TestModuleLevelInitialisationWithEnvOverride:
    """Test that REDIS_HOST env var overrides the default host."""

    def test_redis_uses_env_host(self, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "my-redis-server")

        mock_redis_instance = AsyncMock()
        mock_redis_cls = MagicMock(return_value=mock_redis_instance)
        mock_saver_instance = MagicMock()
        mock_saver_cls = MagicMock(return_value=mock_saver_instance)
        mock_create_agent = MagicMock(return_value=SENTINEL_AGENT)
        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.return_value = SENTINEL_MODEL
        mock_llms_cls = MagicMock(return_value=mock_llms_instance)

        patches = {
            "Redis": mock_redis_cls,
            "AsyncRedisSaver": mock_saver_cls,
            "create_agent": mock_create_agent,
            "LLMS": mock_llms_cls,
            "llms_instance": mock_llms_instance,
            "redis_instance": mock_redis_instance,
            "saver_instance": mock_saver_instance,
            "get_customer_profile": SENTINEL_TOOL_PROFILE,
            "customer_lookalike": SENTINEL_TOOL_LOOKALIKE,
            "_run_underwriting_assessment": MagicMock(side_effect=_make_assessment_tool),
            "SYSTEM_PROMPT": SENTINEL_SYSTEM_PROMPT,
        }

        _remove_graph_module()
        with (
            patch("redis.asyncio.Redis", mock_redis_cls),
            patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", mock_saver_cls),
            patch("langchain.agents.create_agent", mock_create_agent),
            patch.dict("sys.modules", _build_stub_modules(patches)),
        ):
            import backend.agent.graph  # noqa: F401

            call_kwargs = mock_redis_cls.call_args.kwargs
            assert call_kwargs.get("host") == "my-redis-server"

        _remove_graph_module()
        monkeypatch.delenv("REDIS_HOST", raising=False)


# ===========================================================================
# Tests: build_agent()
# ===========================================================================


class TestBuildAgentHappyPath:
    """Happy-path scenarios for build_agent."""

    @pytest.mark.parametrize(
        "model_name, temperature, mode",
        [
            ("gpt-4o", 0.0, "fast"),
            ("gpt-4o", 0.7, "deep"),
            ("gpt-3.5-turbo", 0.5, "fast"),
            ("claude-3-opus", 1.0, "deep"),
        ],
    )
    def test_returns_agent(self, graph, patches, model_name, temperature, mode):
        result = graph.build_agent(model_name, temperature, mode)
        assert result is SENTINEL_AGENT

    def test_llms_instantiated_with_correct_temperature(self, graph, patches):
        patches["LLMS"].reset_mock()
        graph.build_agent("gpt-4o", 0.3, "fast")
        patches["LLMS"].assert_called_once_with(temperature=0.3, streaming=True)

    def test_llms_instantiated_with_streaming_true(self, graph, patches):
        patches["LLMS"].reset_mock()
        graph.build_agent("gpt-4o", 0.5, "fast")
        call_kwargs = patches["LLMS"].call_args.kwargs
        assert call_kwargs.get("streaming") is True

    def test_get_model_called_with_model_name(self, graph, patches):
        patches["llms_instance"].get_model.reset_mock()
        graph.build_agent("gpt-4o-mini", 0.0, "fast")
        patches["llms_instance"].get_model.assert_called_once_with("gpt-4o-mini")

    def test_create_agent_called_with_correct_model(self, graph, patches):
        patches["create_agent"].reset_mock()
        graph.build_agent("gpt-4o", 0.0, "fast")
        call_kwargs = patches["create_agent"].call_args.kwargs
        assert call_kwargs.get("model") is SENTINEL_MODEL

    def test_create_agent_called_with_system_prompt(self, graph, patches):
        patches["create_agent"].reset_mock()
        graph.build_agent("gpt-4o", 0.0, "fast")
        call_kwargs = patches["create_agent"].call_args.kwargs
        assert call_kwargs.get("system_prompt") == SENTINEL_SYSTEM_PROMPT

    def test_create_agent_called_with_checkpointer(self, graph, patches):
        patches["create_agent"].reset_mock()
        graph.build_agent("gpt-4o", 0.0, "fast")
        call_kwargs = patches["create_agent"].call_args.kwargs
        assert call_kwargs.get("checkpointer") is patches["saver_instance"]

    def test_tools_list_has_three_entries(self, graph, patches):
        patches["create_agent"].reset_mock()
        graph.build_agent("gpt-4o", 0.0, "fast")
        call_kwargs = patches["create_agent"].call_args.kwargs
        tools = call_kwargs.get("tools")
        assert tools is not None
        assert len(tools) == 3

    def test_tools_list_contains_get_customer_profile(self, graph, patches):
        patches["create_agent"].reset_mock()
        graph.build_agent("gpt-4o", 0.0, "fast")
        call_kwargs = patches["create_agent"].call_args.kwargs
        tools = call_kwargs.get("tools")
        assert SENTINEL_TOOL_PROFILE in tools

    def test_tools_list_contains_customer_lookalike(self, graph, patches):
        patches["create_agent"].reset_mock()