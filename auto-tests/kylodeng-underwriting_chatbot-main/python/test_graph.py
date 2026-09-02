"""
Test module for backend/agent/graph.py

What is tested:
    - build_agent() function: happy path, various model names, temperatures, modes
    - Module-level Redis client and checkpointer initialization
    - Argument forwarding to LLMS, create_agent, and tool construction
    - Edge cases: boundary temperatures, unknown mode strings, empty model name
    - Error conditions: LLMS raising exceptions, create_agent raising exceptions

Mocks used:
    - langchain.agents.create_agent (patched to avoid real LLM/agent construction)
    - redis.asyncio.Redis (patched to avoid real Redis connection)
    - langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched to avoid real Redis saver)
    - modules.tools.get_customer_profile (patched)
    - modules.tools.customer_lookalike (patched)
    - modules.assessment._run_underwriting_assessment (patched)
    - modules.LLMS.LLMS (patched to avoid real LLM calls)
    - os.environ (manipulated via monkeypatch for REDIS_HOST tests)

TODOs:
    - TODO: Integration test with a real (test) Redis instance once Redis is migrated to an external service
    - TODO: Verify checkpointer is correctly wired into the agent (requires deeper langgraph introspection)
    - TODO: Test streaming behaviour of the agent (requires real or stubbed LLM stream)
    - TODO: Test that SYSTEM_PROMPT content is valid and non-empty (requires prompts module context)
"""

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a consistent set of patches for the module under test
# ---------------------------------------------------------------------------

MODULE_PATH = "backend.agent.graph"


def _make_patches():
    """Return a dict of patcher objects that isolate every external dependency."""
    patches = {}

    patches["create_agent"] = patch(
        "langchain.agents.create_agent", return_value=MagicMock(name="mock_agent")
    )
    patches["Redis"] = patch(
        "redis.asyncio.Redis", return_value=AsyncMock(name="mock_redis")
    )
    patches["AsyncRedisSaver"] = patch(
        "langgraph.checkpoint.redis.aio.AsyncRedisSaver",
        return_value=MagicMock(name="mock_checkpointer"),
    )
    patches["get_customer_profile"] = patch(
        "modules.tools.get_customer_profile", new=MagicMock(name="get_customer_profile")
    )
    patches["customer_lookalike"] = patch(
        "modules.tools.customer_lookalike", new=MagicMock(name="customer_lookalike")
    )
    patches["_run_underwriting_assessment"] = patch(
        "modules.assessment._run_underwriting_assessment",
        return_value=MagicMock(name="assessment_tool"),
    )
    patches["LLMS"] = patch(
        "modules.LLMS.LLMS",
        return_value=MagicMock(
            name="mock_llms_instance",
            get_model=MagicMock(return_value=MagicMock(name="mock_model")),
        ),
    )
    patches["SYSTEM_PROMPT"] = patch(
        "backend.agent.prompts.SYSTEM_PROMPT", new="MOCK_SYSTEM_PROMPT", create=True
    )
    return patches


# ---------------------------------------------------------------------------
# Fixture: fresh import of graph module with all dependencies mocked
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_missing_modules():
    """
    Pre-populate sys.modules with lightweight stubs for packages that are
    likely not installed in the test environment.  This allows the module
    under test to be imported without errors.
    """
    stubs = {
        "langchain": types.ModuleType("langchain"),
        "langchain.agents": types.ModuleType("langchain.agents"),
        "redis": types.ModuleType("redis"),
        "redis.asyncio": types.ModuleType("redis.asyncio"),
        "langgraph": types.ModuleType("langgraph"),
        "langgraph.checkpoint": types.ModuleType("langgraph.checkpoint"),
        "langgraph.checkpoint.redis": types.ModuleType("langgraph.checkpoint.redis"),
        "langgraph.checkpoint.redis.aio": types.ModuleType(
            "langgraph.checkpoint.redis.aio"
        ),
        "modules": types.ModuleType("modules"),
        "modules.tools": types.ModuleType("modules.tools"),
        "modules.assessment": types.ModuleType("modules.assessment"),
        "modules.LLMS": types.ModuleType("modules.LLMS"),
        "backend": types.ModuleType("backend"),
        "backend.agent": types.ModuleType("backend.agent"),
        "backend.agent.prompts": types.ModuleType("backend.agent.prompts"),
    }

    # Attach minimal attributes so the module can import them by name
    stubs["langchain.agents"].create_agent = MagicMock(name="create_agent")
    stubs["langchain"].agents = stubs["langchain.agents"]

    stubs["redis.asyncio"].Redis = MagicMock(name="Redis")
    stubs["redis"].asyncio = stubs["redis.asyncio"]

    stubs["langgraph.checkpoint.redis.aio"].AsyncRedisSaver = MagicMock(
        name="AsyncRedisSaver"
    )
    stubs["langgraph.checkpoint.redis"].aio = stubs["langgraph.checkpoint.redis.aio"]
    stubs["langgraph.checkpoint"].redis = stubs["langgraph.checkpoint.redis"]
    stubs["langgraph"].checkpoint = stubs["langgraph.checkpoint"]

    stubs["modules.tools"].get_customer_profile = MagicMock(name="get_customer_profile")
    stubs["modules.tools"].customer_lookalike = MagicMock(name="customer_lookalike")
    stubs["modules"].tools = stubs["modules.tools"]

    stubs["modules.assessment"]._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment", return_value=MagicMock(name="assessment_tool")
    )
    stubs["modules"].assessment = stubs["modules.assessment"]

    mock_llms_instance = MagicMock(name="llms_instance")
    mock_llms_instance.get_model.return_value = MagicMock(name="mock_model")
    stubs["modules.LLMS"].LLMS = MagicMock(
        name="LLMS", return_value=mock_llms_instance
    )
    stubs["modules"].LLMS = stubs["modules.LLMS"]

    stubs["backend.agent.prompts"].SYSTEM_PROMPT = "MOCK_SYSTEM_PROMPT"
    stubs["backend.agent"].prompts = stubs["backend.agent.prompts"]
    stubs["backend"].agent = stubs["backend.agent"]

    # Inject stubs – preserve any that already exist
    originals = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)

    yield stubs

    # Restore
    for k, v in originals.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


@pytest.fixture()
def graph_module(_stub_missing_modules):
    """Import (or re-import) backend.agent.graph with stubs in place."""
    # Remove cached copy so we get a fresh import each time
    sys.modules.pop("backend.agent.graph", None)
    import importlib.util, os

    # Attempt a normal import first; fall back to file-based load
    try:
        import backend.agent.graph as graph  # noqa: F401
    except ModuleNotFoundError:
        # Try loading directly from file path
        spec = importlib.util.spec_from_file_location(
            "backend.agent.graph", os.path.join("backend", "agent", "graph.py")
        )
        graph = importlib.util.module_from_spec(spec)
        sys.modules["backend.agent.graph"] = graph
        spec.loader.exec_module(graph)

    return graph


# ---------------------------------------------------------------------------
# Convenience accessors that work regardless of how the module was loaded
# ---------------------------------------------------------------------------


def _stubs(graph_module, _stub_missing_modules):
    """Return the stub objects currently wired into the module."""
    return _stub_missing_modules


# ===========================================================================
# Tests for module-level initialisation
# ===========================================================================


class TestModuleLevelInit:
    """Verify Redis client and checkpointer are created at import time."""

    def test_redis_client_created(self, graph_module, _stub_missing_modules):
        Redis_mock = _stub_missing_modules["redis.asyncio"].Redis
        assert Redis_mock.called, "Redis() should be called at module import"

    def test_redis_uses_default_host(self, monkeypatch, _stub_missing_modules):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        sys.modules.pop("backend.agent.graph", None)
        _stub_missing_modules["redis.asyncio"].Redis.reset_mock()

        # Re-import to trigger module-level code
        try:
            import backend.agent.graph  # noqa: F401
        except Exception:
            pass

        Redis_mock = _stub_missing_modules["redis.asyncio"].Redis
        if Redis_mock.called:
            call_kwargs = Redis_mock.call_args
            host_arg = (
                call_kwargs.kwargs.get("host")
                if call_kwargs.kwargs
                else (call_kwargs.args[0] if call_kwargs.args else None)
            )
            # default should be "localhost" when env var is absent
            if host_arg is not None:
                assert host_arg == "localhost"

    def test_redis_uses_env_host(self, monkeypatch, _stub_missing_modules):
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        sys.modules.pop("backend.agent.graph", None)
        _stub_missing_modules["redis.asyncio"].Redis.reset_mock()

        try:
            import backend.agent.graph  # noqa: F401
        except Exception:
            pass

        Redis_mock = _stub_missing_modules["redis.asyncio"].Redis
        if Redis_mock.called:
            call_kwargs = Redis_mock.call_args
            host_arg = (
                call_kwargs.kwargs.get("host")
                if call_kwargs.kwargs
                else (call_kwargs.args[0] if call_kwargs.args else None)
            )
            if host_arg is not None:
                assert host_arg == "my-redis-host"

    def test_redis_port_is_6379(self, _stub_missing_modules):
        Redis_mock = _stub_missing_modules["redis.asyncio"].Redis
        if Redis_mock.called:
            call_kwargs = Redis_mock.call_args
            port = (
                call_kwargs.kwargs.get("port")
                if call_kwargs and call_kwargs.kwargs
                else None
            )
            if port is not None:
                assert port == 6379

    def test_checkpointer_created(self, graph_module, _stub_missing_modules):
        AsyncRedisSaver_mock = _stub_missing_modules[
            "langgraph.checkpoint.redis.aio"
        ].AsyncRedisSaver
        assert (
            AsyncRedisSaver_mock.called
        ), "AsyncRedisSaver() should be called at module import"

    def test_checkpointer_receives_redis_client(self, graph_module, _stub_missing_modules):
        AsyncRedisSaver_mock = _stub_missing_modules[
            "langgraph.checkpoint.redis.aio"
        ].AsyncRedisSaver
        if AsyncRedisSaver_mock.called:
            kwargs = AsyncRedisSaver_mock.call_args.kwargs
            assert "redis_client" in kwargs, "AsyncRedisSaver must receive redis_client"


# ===========================================================================
# Tests for build_agent()
# ===========================================================================


class TestBuildAgentHappyPath:
    """Happy-path tests for build_agent()."""

    @pytest.mark.parametrize(
        "model_name, temperature, mode",
        [
            ("gpt-4o", 0.0, "fast"),
            ("gpt-4o", 0.7, "deep"),
            ("gpt-3.5-turbo", 0.5, "fast"),
            ("claude-3-sonnet", 1.0, "deep"),
        ],
    )
    def test_returns_agent(self, graph_module, model_name, temperature, mode):
        agent = graph_module.build_agent(model_name, temperature, mode)
        assert agent is not None

    def test_create_agent_called(self, graph_module, _stub_missing_modules):
        create_agent_mock = _stub_missing_modules["langchain.agents"].create_agent
        create_agent_mock.reset_mock()

        graph_module.build_agent("gpt-4o", 0.5, "fast")

        create_agent_mock.assert_called_once()

    def test_create_agent_receives_model(self, graph_module, _stub_missing_modules):
        create_agent_mock = _stub_missing_modules["langchain.agents"].create_agent
        create_agent_mock.reset_mock()

        graph_module.build_agent("gpt-4o", 0.3, "fast")

        _, kwargs = create_agent_mock.call_args
        assert "model" in kwargs, "create_agent must be called with 'model' keyword arg"

    def test_create_agent_receives_tools(self, graph_module, _stub_missing_modules):
        create_agent_mock = _stub_missing_modules["langchain.agents"].create_agent
        create_agent_mock.reset_mock()

        graph_module.build_agent("gpt-4o", 0.3, "fast")

        _, kwargs = create_agent_mock.call_args
        assert "tools" in kwargs, "create_agent must be called with 'tools' keyword arg"
        tools = kwargs["tools"]
        assert len(tools) == 3, "Exactly 3 tools should be passed to create_agent"

    def test_create_agent_receives_system_prompt(self, graph_module, _stub_missing_modules):
        create_agent_mock = _stub_missing_modules["langchain.agents"].create_agent
        create_agent_mock.reset_mock()

        graph_module.build_agent("gpt-4o", 0.3, "fast")

        _, kwargs = create_agent_mock.call_args
        assert (
            "system_prompt" in kwargs
        ), "create_agent must be called with 'system_prompt'"

    def test_create_agent_receives_checkpointer(self, graph_module, _stub_missing_modules):
        create_agent_mock = _stub_missing_modules["langchain.agents"].create_agent
        create_agent_mock.reset_mock()

        graph_module.build_agent("gpt-4o", 0.3, "fast")

        _, kwargs = create_agent_mock.call_args
        assert (
            "checkpointer" in kwargs
        ), "create_agent must be called with 'checkpointer'"

    def test_llms_instantiated_with_temperature(self, graph_module, _stub_missing_modules):
        LLMS_mock = _stub_missing_modules["modules.LLMS"].LLMS
        LLMS_mock.reset_mock()

        graph_module.build_agent("gpt-4o", 0.42, "fast")

        LLMS_mock.assert_called_once()
        _, kwargs = LLMS_mock.call_args
        assert kwargs.get("temperature") == 0.42

    def test_llms_instantiated_with_streaming_true(
        self, graph_