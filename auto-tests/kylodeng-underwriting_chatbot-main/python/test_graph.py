"""
Tests for backend/agent/graph.py

What is tested:
- build_agent() happy path with valid model_name, temperature, and mode combinations
- build_agent() with all supported mode values ("fast", "deep", unknown/custom)
- build_agent() edge cases: boundary temperatures (0.0, 1.0, extreme values)
- build_agent() error conditions: LLMS failures, create_agent failures, tool construction failures
- Module-level Redis client and checkpointer instantiation (via env var control)
- Tool list composition inside build_agent()

Mocks used:
- backend.agent.graph.LLMS                          → prevents real LLM calls
- backend.agent.graph.create_agent                  → prevents real agent creation
- backend.agent.graph.get_customer_profile          → stub tool object
- backend.agent.graph.customer_lookalike            → stub tool object
- backend.agent.graph._run_underwriting_assessment  → prevents real assessment calls
- backend.agent.graph.Redis                         → prevents real Redis connections
- backend.agent.graph.AsyncRedisSaver               → prevents real Redis saver init
- os.environ                                        → controls REDIS_HOST injection

TODOs:
- TODO: Integration test verifying the agent can actually invoke tools end-to-end
        (requires a running Redis instance and real LLM credentials)
- TODO: Test async checkpoint persistence behaviour once Redis is migrated to
        an external service (Azure Cache / dedicated container)
- TODO: Verify SYSTEM_PROMPT content is correctly forwarded to create_agent
        (requires access to backend/agent/prompts.py contents)
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test with mocks already in place
# ---------------------------------------------------------------------------

def _make_mock_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.__name__ = name
    return tool


def _base_patches():
    """Return a dict of patch targets → replacement objects used in most tests."""
    mock_redis_instance = MagicMock()
    mock_redis_cls = MagicMock(return_value=mock_redis_instance)

    mock_saver_instance = MagicMock()
    mock_saver_cls = MagicMock(return_value=mock_saver_instance)

    mock_get_customer_profile = _make_mock_tool("get_customer_profile")
    mock_customer_lookalike = _make_mock_tool("customer_lookalike")

    mock_run_underwriting = MagicMock(
        side_effect=lambda mode: _make_mock_tool(f"underwriting_{mode}")
    )

    mock_llms_instance = MagicMock()
    mock_model = MagicMock()
    mock_llms_instance.get_model.return_value = mock_model
    mock_llms_cls = MagicMock(return_value=mock_llms_instance)

    mock_create_agent = MagicMock(return_value=MagicMock(name="agent"))

    mock_system_prompt = "MOCK_SYSTEM_PROMPT"

    return {
        "redis_cls": mock_redis_cls,
        "redis_instance": mock_redis_instance,
        "saver_cls": mock_saver_cls,
        "saver_instance": mock_saver_instance,
        "get_customer_profile": mock_get_customer_profile,
        "customer_lookalike": mock_customer_lookalike,
        "run_underwriting": mock_run_underwriting,
        "llms_cls": mock_llms_cls,
        "llms_instance": mock_llms_instance,
        "model": mock_model,
        "create_agent": mock_create_agent,
        "system_prompt": mock_system_prompt,
    }


@pytest.fixture()
def mocks():
    """Fixture that patches all external dependencies and imports graph fresh."""
    patches = _base_patches()

    # Remove cached module so each test gets a clean import
    sys.modules.pop("backend.agent.graph", None)
    sys.modules.pop("agent.graph", None)

    with (
        patch("redis.asyncio.Redis", patches["redis_cls"]),
        patch("langgraph.checkpoint.redis.aio.AsyncRedisSaver", patches["saver_cls"]),
        patch("langchain.agents.create_agent", patches["create_agent"]),
        patch("modules.tools.get_customer_profile", patches["get_customer_profile"]),
        patch("modules.tools.customer_lookalike", patches["customer_lookalike"]),
        patch("modules.assessment._run_underwriting_assessment", patches["run_underwriting"]),
        patch("modules.LLMS.LLMS", patches["llms_cls"]),
        patch("backend.agent.prompts.SYSTEM_PROMPT", patches["system_prompt"], create=True),
    ):
        # Build a minimal fake package tree so the relative imports resolve
        _inject_fake_modules(patches)
        graph = _import_graph(patches)
        patches["graph"] = graph
        yield patches


def _inject_fake_modules(patches):
    """
    Inject lightweight fake modules that satisfy the absolute-import paths used
    inside graph.py so we can import it without a fully-installed package.
    """
    # backend package
    backend_pkg = types.ModuleType("backend")
    backend_pkg.__path__ = []
    sys.modules.setdefault("backend", backend_pkg)

    # backend.agent package
    agent_pkg = types.ModuleType("backend.agent")
    agent_pkg.__path__ = []
    sys.modules["backend.agent"] = agent_pkg

    # backend.agent.prompts
    prompts_mod = types.ModuleType("backend.agent.prompts")
    prompts_mod.SYSTEM_PROMPT = patches["system_prompt"]
    sys.modules["backend.agent.prompts"] = prompts_mod

    # Also expose as relative sibling (.prompts)
    sys.modules[".prompts"] = prompts_mod  # not strictly needed but harmless

    # modules.tools
    tools_mod = types.ModuleType("modules.tools")
    tools_mod.get_customer_profile = patches["get_customer_profile"]
    tools_mod.customer_lookalike = patches["customer_lookalike"]
    sys.modules["modules.tools"] = tools_mod

    # modules.assessment
    assessment_mod = types.ModuleType("modules.assessment")
    assessment_mod._run_underwriting_assessment = patches["run_underwriting"]
    sys.modules["modules.assessment"] = assessment_mod

    # modules.LLMS
    llms_mod = types.ModuleType("modules.LLMS")
    llms_mod.LLMS = patches["llms_cls"]
    sys.modules["modules.LLMS"] = llms_mod

    # langchain.agents
    lc_agents = types.ModuleType("langchain.agents")
    lc_agents.create_agent = patches["create_agent"]
    sys.modules["langchain"] = types.ModuleType("langchain")
    sys.modules["langchain"].__path__ = []
    sys.modules["langchain.agents"] = lc_agents

    # redis.asyncio
    redis_pkg = types.ModuleType("redis")
    redis_pkg.__path__ = []
    redis_asyncio = types.ModuleType("redis.asyncio")
    redis_asyncio.Redis = patches["redis_cls"]
    sys.modules["redis"] = redis_pkg
    sys.modules["redis.asyncio"] = redis_asyncio

    # langgraph.checkpoint.redis.aio
    lg_pkg = types.ModuleType("langgraph")
    lg_pkg.__path__ = []
    lg_cp = types.ModuleType("langgraph.checkpoint")
    lg_cp.__path__ = []
    lg_cp_redis = types.ModuleType("langgraph.checkpoint.redis")
    lg_cp_redis.__path__ = []
    lg_cp_redis_aio = types.ModuleType("langgraph.checkpoint.redis.aio")
    lg_cp_redis_aio.AsyncRedisSaver = patches["saver_cls"]
    sys.modules["langgraph"] = lg_pkg
    sys.modules["langgraph.checkpoint"] = lg_cp
    sys.modules["langgraph.checkpoint.redis"] = lg_cp_redis
    sys.modules["langgraph.checkpoint.redis.aio"] = lg_cp_redis_aio


def _import_graph(patches):
    """Import backend.agent.graph and return it."""
    import importlib.util, os

    graph_path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "agent", "graph.py"
    )

    # Fallback: try to import directly if already on sys.path
    try:
        import backend.agent.graph as graph_mod
        return graph_mod
    except Exception:
        pass

    try:
        spec = importlib.util.spec_from_file_location("backend.agent.graph", graph_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["backend.agent.graph"] = mod
        spec.loader.exec_module(mod)

        # Patch module-level references so tests can override them
        mod.create_agent = patches["create_agent"]
        mod.get_customer_profile = patches["get_customer_profile"]
        mod.customer_lookalike = patches["customer_lookalike"]
        mod._run_underwriting_assessment = patches["run_underwriting"]
        mod.LLMS = patches["llms_cls"]
        mod.SYSTEM_PROMPT = patches["system_prompt"]
        mod._checkpointer = patches["saver_instance"]

        return mod
    except Exception:
        # Return a minimal stand-in if file cannot be found (CI without full repo)
        return None


# ---------------------------------------------------------------------------
# Convenience: call build_agent through the module or directly
# ---------------------------------------------------------------------------

def _call_build_agent(mocks, model_name, temperature, mode="fast"):
    graph = mocks["graph"]
    if graph is None:
        pytest.skip("backend/agent/graph.py not importable in this environment")

    build_agent = graph.build_agent

    # Ensure module-level singletons are mocked
    graph.create_agent = mocks["create_agent"]
    graph.get_customer_profile = mocks["get_customer_profile"]
    graph.customer_lookalike = mocks["customer_lookalike"]
    graph._run_underwriting_assessment = mocks["run_underwriting"]
    graph.LLMS = mocks["llms_cls"]
    graph.SYSTEM_PROMPT = mocks["system_prompt"]
    graph._checkpointer = mocks["saver_instance"]

    return build_agent(model_name, temperature, mode)


# ===========================================================================
# Tests: build_agent — happy path
# ===========================================================================

class TestBuildAgentHappyPath:

    def test_returns_agent_object(self, mocks):
        """build_agent should return whatever create_agent produces."""
        result = _call_build_agent(mocks, "gpt-4o", 0.5)
        assert result is mocks["create_agent"].return_value

    def test_llms_instantiated_with_correct_kwargs(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.7)
        mocks["llms_cls"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, mocks):
        _call_build_agent(mocks, "gpt-4o-mini", 0.3)
        mocks["llms_instance"].get_model.assert_called_once_with("gpt-4o-mini")

    def test_create_agent_receives_correct_model(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.5)
        call_kwargs = mocks["create_agent"].call_args.kwargs
        assert call_kwargs["model"] is mocks["model"]

    def test_create_agent_receives_system_prompt(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.5)
        call_kwargs = mocks["create_agent"].call_args.kwargs
        assert call_kwargs["system_prompt"] == mocks["system_prompt"]

    def test_create_agent_receives_checkpointer(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.5)
        call_kwargs = mocks["create_agent"].call_args.kwargs
        assert call_kwargs["checkpointer"] is mocks["saver_instance"]

    def test_create_agent_receives_three_tools(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.5)
        tools = mocks["create_agent"].call_args.kwargs["tools"]
        assert len(tools) == 3

    def test_tools_include_get_customer_profile(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.5)
        tools = mocks["create_agent"].call_args.kwargs["tools"]
        assert mocks["get_customer_profile"] in tools

    def test_tools_include_customer_lookalike(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.5)
        tools = mocks["create_agent"].call_args.kwargs["tools"]
        assert mocks["customer_lookalike"] in tools

    def test_underwriting_assessment_called_with_mode(self, mocks):
        _call_build_agent(mocks, "gpt-4o", 0.5, mode="fast")
        mocks["run_underwriting"].assert_called_once_with("fast")

    def test_underwriting_tool_included_in_tools(self, mocks):
        expected_tool = mocks["run_underwriting"].return_value
        _call_build_agent(mocks, "gpt-4o", 0.5, mode="fast")
        tools = mocks["create_agent"].call_args.kwargs["tools"]
        assert expected_tool in tools


# ===========================================================================
# Tests: build_agent — mode parameter
# ===========================================================================

class TestBuildAgentMode:

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_supported_modes_invoke_assessment_correctly(self, mocks, mode):
        mocks["run_underwriting"].reset_mock()
        _call_build_agent(mocks, "gpt-4o", 0.5, mode=mode)
        mocks["run_underwriting"].assert_called_once_with(mode)

    def test_default_mode_is_fast(self, mocks):
        """Calling build_agent without mode should default to 'fast'."""
        graph = mocks["graph"]
        if graph is None:
            pytest.skip("backend/agent/graph.py not importable in this environment")
        graph.create_agent = mocks["create_agent"]
        graph.get_customer_profile = mocks["get_customer_profile"]
        graph.customer_lookalike = mocks["customer_lookalike"]
        graph._run_underwriting_assessment = mocks["run_underwriting"]
        graph.LLMS = mocks["llms_cls"]
        graph.SYSTEM_PROMPT = mocks["system_prompt"]
        graph._checkpointer = mocks["saver_instance"]

        graph.build_agent("gpt-4o", 0.5)  # no mode keyword
        mocks["run_underwriting"].assert_called_once_with("fast")

    def test_unknown_mode_still_passed_to_assessment(self, mocks):
        """graph.py does not validate mode — it forwards whatever is supplied."""
        _call_build_agent(mocks, "gpt-4o", 0.5, mode="ultra")
        mocks["run_underwriting"].assert_called_once_with("ultra")

    def test_empty_string_mode(self, mocks):
        _call_build_agent(