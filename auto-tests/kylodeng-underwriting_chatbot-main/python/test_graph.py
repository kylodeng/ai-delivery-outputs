"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path, parameter variations, edge cases, error conditions
- Module-level Redis client and checkpointer initialization
- Model construction via LLMS
- Tool list assembly (get_customer_profile, _run_underwriting_assessment, customer_lookalike)
- create_agent() invocation with correct arguments

Mocks used:
- redis.asyncio.Redis (patched at module level to avoid real Redis connections)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched to avoid real Redis I/O)
- langchain.agents.create_agent (patched to intercept agent construction)
- modules.tools.get_customer_profile (patched)
- modules.tools.customer_lookalike (patched)
- modules.assessment._run_underwriting_assessment (patched)
- modules.LLMS.LLMS (patched)

TODOs:
- TODO: Integration test for actual Redis connectivity once an external Redis service is configured
- TODO: Test agent invocation / streaming behaviour once LangGraph runtime is available in test env
- TODO: Test checkpointer persistence across simulated serverless restarts
- TODO: Verify SYSTEM_PROMPT content is injected correctly (needs prompts module fixture)
"""

import importlib
import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to reload the module under test with patched globals
# ---------------------------------------------------------------------------

MODULE_PATH = "backend.agent.graph"
AGENT_MODULE = "agent.graph"  # relative import path used internally

# Patch targets (absolute)
PATCH_REDIS = "redis.asyncio.Redis"
PATCH_REDIS_SAVER = "langgraph.checkpoint.redis.aio.AsyncRedisSaver"
PATCH_CREATE_AGENT = "langchain.agents.create_agent"
PATCH_LLMS = "modules.LLMS.LLMS"
PATCH_GET_CUSTOMER_PROFILE = "modules.tools.get_customer_profile"
PATCH_CUSTOMER_LOOKALIKE = "modules.tools.customer_lookalike"
PATCH_RUN_UNDERWRITING = "modules.assessment._run_underwriting_assessment"


def _make_mock_tool(name: str) -> MagicMock:
    """Return a named MagicMock that can stand in for a LangChain tool."""
    mock = MagicMock(name=name)
    mock.__name__ = name
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def env_defaults(monkeypatch):
    """Ensure REDIS_HOST is set to a safe default during all tests."""
    monkeypatch.setenv("REDIS_HOST", "localhost-test")


@pytest.fixture()
def mock_redis_client():
    return AsyncMock(name="MockRedisClient")


@pytest.fixture()
def mock_checkpointer():
    return MagicMock(name="MockAsyncRedisSaver")


@pytest.fixture()
def mock_model():
    return MagicMock(name="MockLLMModel")


@pytest.fixture()
def mock_llms_cls(mock_model):
    llms_instance = MagicMock(name="MockLLMSInstance")
    llms_instance.get_model.return_value = mock_model

    llms_cls = MagicMock(name="MockLLMSClass", return_value=llms_instance)
    return llms_cls, llms_instance


@pytest.fixture()
def mock_tools():
    get_customer_profile = _make_mock_tool("get_customer_profile")
    customer_lookalike = _make_mock_tool("customer_lookalike")
    assessment_tool = _make_mock_tool("underwriting_assessment_tool")
    run_underwriting = MagicMock(
        name="_run_underwriting_assessment", return_value=assessment_tool
    )
    return {
        "get_customer_profile": get_customer_profile,
        "customer_lookalike": customer_lookalike,
        "assessment_tool": assessment_tool,
        "_run_underwriting_assessment": run_underwriting,
    }


@pytest.fixture()
def mock_create_agent():
    agent = MagicMock(name="MockAgent")
    create_agent = MagicMock(name="create_agent", return_value=agent)
    return create_agent, agent


@pytest.fixture()
def graph_module(
    mock_redis_client,
    mock_checkpointer,
    mock_llms_cls,
    mock_tools,
    mock_create_agent,
):
    """
    Import (or reload) backend.agent.graph with all external dependencies patched.
    Returns a tuple: (module, mocks_dict).
    """
    llms_cls, llms_instance = mock_llms_cls
    create_agent_fn, agent = mock_create_agent

    patches = {
        PATCH_REDIS: MagicMock(return_value=mock_redis_client),
        PATCH_REDIS_SAVER: MagicMock(return_value=mock_checkpointer),
        PATCH_CREATE_AGENT: create_agent_fn,
        PATCH_LLMS: llms_cls,
        PATCH_GET_CUSTOMER_PROFILE: mock_tools["get_customer_profile"],
        PATCH_CUSTOMER_LOOKALIKE: mock_tools["customer_lookalike"],
        PATCH_RUN_UNDERWRITING: mock_tools["_run_underwriting_assessment"],
    }

    # Remove cached module so re-import picks up fresh patches
    for key in list(sys.modules.keys()):
        if "agent.graph" in key or key == MODULE_PATH:
            del sys.modules[key]

    with patch.multiple("", **{}):  # no-op outer
        started_patches = []
        try:
            for target, mock_obj in patches.items():
                p = patch(target, mock_obj)
                p.start()
                started_patches.append(p)

            # Ensure the package path is importable
            import importlib.util

            spec = importlib.util.find_spec("backend.agent.graph")
            if spec is None:
                # Try relative path for projects where sys.path includes backend/
                import importlib as il

                mod = il.import_module("agent.graph")
            else:
                mod = importlib.import_module("backend.agent.graph")

            yield mod, {
                "redis_cls": patches[PATCH_REDIS],
                "redis_saver_cls": patches[PATCH_REDIS_SAVER],
                "create_agent": create_agent_fn,
                "agent": agent,
                "llms_cls": llms_cls,
                "llms_instance": llms_instance,
                "tools": mock_tools,
            }
        finally:
            for p in started_patches:
                p.stop()
            # Clean up cached module again
            for key in list(sys.modules.keys()):
                if "agent.graph" in key or key == MODULE_PATH:
                    del sys.modules[key]


# ---------------------------------------------------------------------------
# Convenience: a simpler fixture that re-patches within the already-imported
# module's namespace (avoids full reimport overhead for per-test cases).
# ---------------------------------------------------------------------------


@pytest.fixture()
def build_agent_under_test(
    mock_llms_cls,
    mock_tools,
    mock_create_agent,
    mock_checkpointer,
):
    """
    Patch the module's internal references directly so we can call build_agent
    without a full reload. Yields (build_agent_fn, mocks_dict).
    """
    llms_cls, llms_instance = mock_llms_cls
    create_agent_fn, agent = mock_create_agent

    graph_targets = [
        ("backend.agent.graph.LLMS", llms_cls),
        ("backend.agent.graph.get_customer_profile", mock_tools["get_customer_profile"]),
        ("backend.agent.graph.customer_lookalike", mock_tools["customer_lookalike"]),
        (
            "backend.agent.graph._run_underwriting_assessment",
            mock_tools["_run_underwriting_assessment"],
        ),
        ("backend.agent.graph.create_agent", create_agent_fn),
        ("backend.agent.graph._checkpointer", mock_checkpointer),
    ]

    started = []
    try:
        for target, mock_obj in graph_targets:
            p = patch(target, mock_obj)
            p.start()
            started.append(p)

        # Import after patching
        try:
            from backend.agent import graph as graph_mod
        except ImportError:
            from agent import graph as graph_mod  # type: ignore[no-redef]

        yield graph_mod.build_agent, {
            "llms_cls": llms_cls,
            "llms_instance": llms_instance,
            "create_agent": create_agent_fn,
            "agent": agent,
            "tools": mock_tools,
            "checkpointer": mock_checkpointer,
        }
    finally:
        for p in started:
            p.stop()


# ---------------------------------------------------------------------------
# Parametrised input data derived from synthetic samples
# ---------------------------------------------------------------------------

VALID_MODEL_NAMES = [
    "gpt-4o",
    "gpt-3.5-turbo",
    "claude-3-opus",
    "mistral-large",
]

VALID_TEMPERATURES = [0.0, 0.5, 1.0]

VALID_MODES = ["fast", "deep"]


# ---------------------------------------------------------------------------
# Tests: module-level initialisation
# ---------------------------------------------------------------------------


class TestModuleLevelInitialisation:
    """Verify that module import creates Redis client and checkpointer correctly."""

    @patch(PATCH_REDIS_SAVER)
    @patch(PATCH_REDIS)
    def test_redis_client_created_with_env_host(
        self, mock_redis_cls, mock_saver_cls, monkeypatch
    ):
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        # Remove cached module to force reimport
        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]

        try:
            import importlib

            try:
                importlib.import_module("backend.agent.graph")
            except ImportError:
                importlib.import_module("agent.graph")
        except Exception:
            pass  # module may fail for other reasons; we only care about Redis call

        mock_redis_cls.assert_called_once_with(
            host="my-redis-host", port=6379, decode_responses=False
        )

    @patch(PATCH_REDIS_SAVER)
    @patch(PATCH_REDIS)
    def test_redis_client_defaults_to_localhost(
        self, mock_redis_cls, mock_saver_cls, monkeypatch
    ):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]

        try:
            import importlib

            try:
                importlib.import_module("backend.agent.graph")
            except ImportError:
                importlib.import_module("agent.graph")
        except Exception:
            pass

        mock_redis_cls.assert_called_once_with(
            host="localhost", port=6379, decode_responses=False
        )

    @patch(PATCH_REDIS_SAVER)
    @patch(PATCH_REDIS)
    def test_checkpointer_created_with_redis_client(
        self, mock_redis_cls, mock_saver_cls
    ):
        redis_instance = AsyncMock()
        mock_redis_cls.return_value = redis_instance

        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]

        try:
            import importlib

            try:
                importlib.import_module("backend.agent.graph")
            except ImportError:
                importlib.import_module("agent.graph")
        except Exception:
            pass

        mock_saver_cls.assert_called_once_with(redis_client=redis_instance)

    @patch(PATCH_REDIS_SAVER)
    @patch(PATCH_REDIS)
    def test_redis_port_is_always_6379(self, mock_redis_cls, mock_saver_cls):
        for key in list(sys.modules.keys()):
            if "agent.graph" in key:
                del sys.modules[key]

        try:
            import importlib

            try:
                importlib.import_module("backend.agent.graph")
            except ImportError:
                importlib.import_module("agent.graph")
        except Exception:
            pass

        call_kwargs = mock_redis_cls.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("port") == 6379


# ---------------------------------------------------------------------------
# Tests: build_agent happy path
# ---------------------------------------------------------------------------


class TestBuildAgentHappyPath:
    """Happy-path tests for build_agent()."""

    def _do_build(self, build_agent, mocks, model_name, temperature, mode="fast"):
        result = build_agent(model_name=model_name, temperature=temperature, mode=mode)
        return result

    def test_returns_agent_object(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        result = build_agent(model_name="gpt-4o", temperature=0.5)
        assert result is mocks["agent"]

    def test_llms_instantiated_with_correct_temperature(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        build_agent(model_name="gpt-4o", temperature=0.7)
        mocks["llms_cls"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_llms_streaming_always_true(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        build_agent(model_name="gpt-4o", temperature=0.0)
        _, kwargs = mocks["llms_cls"].call_args
        assert kwargs["streaming"] is True

    def test_get_model_called_with_model_name(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        build_agent(model_name="claude-3-opus", temperature=0.5)
        mocks["llms_instance"].get_model.assert_called_once_with("claude-3-opus")

    def test_create_agent_called_once(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        build_agent(model_name="gpt-4o", temperature=0.5)
        mocks["create_agent"].assert_called_once()

    def test_create_agent_receives_model(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        build_agent(model_name="gpt-4o", temperature=0.5)
        _, kwargs = mocks["create_agent"].call_args
        assert kwargs["model"] is mocks["llms_instance"].get_model.return_value

    def test_create_agent_receives_checkpointer(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        build_agent(model_name="gpt-4o", temperature=0.5)
        _, kwargs = mocks["create_agent"].call_args
        assert kwargs["checkpointer"] is mocks["checkpointer"]

    def test_create_agent_receives_system_prompt(self, build_agent_under_test):
        build_agent, mocks = build_agent_under_test
        build_agent(model_name="gpt-4o", temperature=0.5)
        _, kwargs =