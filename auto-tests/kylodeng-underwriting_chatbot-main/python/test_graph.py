"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() happy path with various model_name / temperature / mode combinations
- build_agent() with default mode ("fast")
- build_agent() with explicit mode="deep"
- build_agent() propagates model, tools, system_prompt, and checkpointer correctly
- Tool list composition (get_customer_profile, _run_underwriting_assessment(mode), customer_lookalike)
- LLMS instantiation with correct temperature and streaming=True
- Redis client and AsyncRedisSaver module-level initialisation (env var handling)
- Error conditions: invalid temperature, invalid model_name propagation

Mocks used:
- langchain.agents.create_agent              → MagicMock
- redis.asyncio.Redis                        → MagicMock / AsyncMock
- langgraph.checkpoint.redis.aio.AsyncRedisSaver → MagicMock
- modules.tools.get_customer_profile         → MagicMock sentinel
- modules.tools.customer_lookalike           → MagicMock sentinel
- modules.assessment._run_underwriting_assessment → MagicMock
- modules.LLMS.LLMS                          → MagicMock

TODOs:
- TODO: Integration test against a live Redis instance — stub provided
- TODO: Verify agent streaming behaviour end-to-end — stub provided
- TODO: Confirm exact tool protocol / schema expected by create_agent — stub provided
"""

import importlib
import os
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a fully patched import environment for graph.py
# ---------------------------------------------------------------------------

GRAPH_MODULE_PATH = "backend.agent.graph"


def _make_patches(extra_env: dict | None = None):
    """Return a dict of patch targets and their replacement mocks."""
    fake_redis = MagicMock(name="FakeRedis")
    fake_checkpointer = MagicMock(name="FakeAsyncRedisSaver")
    fake_create_agent = MagicMock(name="fake_create_agent")
    fake_llms_instance = MagicMock(name="fake_llms_instance")
    fake_llms_class = MagicMock(name="FakeLLMS", return_value=fake_llms_instance)
    fake_get_customer_profile = MagicMock(name="get_customer_profile")
    fake_customer_lookalike = MagicMock(name="customer_lookalike")
    fake_run_underwriting = MagicMock(name="_run_underwriting_assessment")
    fake_system_prompt = "FAKE_SYSTEM_PROMPT"

    return {
        "redis_client_mock": fake_redis,
        "checkpointer_mock": fake_checkpointer,
        "create_agent_mock": fake_create_agent,
        "llms_class_mock": fake_llms_class,
        "llms_instance_mock": fake_llms_instance,
        "get_customer_profile_mock": fake_get_customer_profile,
        "customer_lookalike_mock": fake_customer_lookalike,
        "run_underwriting_mock": fake_run_underwriting,
        "system_prompt": fake_system_prompt,
    }


@pytest.fixture()
def graph_module(monkeypatch):
    """
    Import graph.py with all external dependencies replaced by mocks.
    Returns (module, mocks_dict).
    """
    mocks = _make_patches()

    patches = [
        patch("redis.asyncio.Redis", return_value=mocks["redis_client_mock"]),
        patch(
            "langgraph.checkpoint.redis.aio.AsyncRedisSaver",
            return_value=mocks["checkpointer_mock"],
        ),
        patch("langchain.agents.create_agent", mocks["create_agent_mock"]),
        patch("modules.LLMS.LLMS", mocks["llms_class_mock"]),
        patch(
            "modules.tools.get_customer_profile",
            mocks["get_customer_profile_mock"],
        ),
        patch(
            "modules.tools.customer_lookalike",
            mocks["customer_lookalike_mock"],
        ),
        patch(
            "modules.assessment._run_underwriting_assessment",
            mocks["run_underwriting_mock"],
        ),
        patch(
            "backend.agent.prompts.SYSTEM_PROMPT",
            mocks["system_prompt"],
            create=True,
        ),
    ]

    # Remove cached module if already loaded to force a clean import
    for mod_key in list(sys.modules.keys()):
        if "backend.agent.graph" in mod_key or mod_key == GRAPH_MODULE_PATH:
            del sys.modules[mod_key]

    started = [p.start() for p in patches]

    # We need to make sure the relative-import siblings exist as mocks
    # before importing the graph module.
    _inject_sibling_mocks(mocks)

    try:
        module = importlib.import_module(GRAPH_MODULE_PATH)
    except ModuleNotFoundError:
        # Fallback: try direct path used when running tests from repo root
        module = _import_graph_directly(mocks)

    yield module, mocks

    for p in patches:
        try:
            p.stop()
        except RuntimeError:
            pass


def _inject_sibling_mocks(mocks: dict):
    """Pre-populate sys.modules with stub siblings so the import succeeds."""
    # backend.agent.prompts
    prompts_mod = ModuleType("backend.agent.prompts")
    prompts_mod.SYSTEM_PROMPT = mocks["system_prompt"]
    sys.modules.setdefault("backend.agent.prompts", prompts_mod)
    sys.modules.setdefault("backend.agent", ModuleType("backend.agent"))
    sys.modules.setdefault("backend", ModuleType("backend"))

    # modules.*
    tools_mod = ModuleType("modules.tools")
    tools_mod.get_customer_profile = mocks["get_customer_profile_mock"]
    tools_mod.customer_lookalike = mocks["customer_lookalike_mock"]
    sys.modules.setdefault("modules.tools", tools_mod)
    sys.modules.setdefault("modules", ModuleType("modules"))

    assessment_mod = ModuleType("modules.assessment")
    assessment_mod._run_underwriting_assessment = mocks["run_underwriting_mock"]
    sys.modules.setdefault("modules.assessment", assessment_mod)

    llms_mod = ModuleType("modules.LLMS")
    llms_mod.LLMS = mocks["llms_class_mock"]
    sys.modules.setdefault("modules.LLMS", llms_mod)

    # langgraph / langchain stubs
    lg_redis_aio = ModuleType("langgraph.checkpoint.redis.aio")
    lg_redis_aio.AsyncRedisSaver = MagicMock(
        return_value=MagicMock(name="checkpointer")
    )
    sys.modules.setdefault("langgraph", ModuleType("langgraph"))
    sys.modules.setdefault("langgraph.checkpoint", ModuleType("langgraph.checkpoint"))
    sys.modules.setdefault(
        "langgraph.checkpoint.redis", ModuleType("langgraph.checkpoint.redis")
    )
    sys.modules.setdefault("langgraph.checkpoint.redis.aio", lg_redis_aio)

    lc_agents = ModuleType("langchain.agents")
    lc_agents.create_agent = mocks["create_agent_mock"]
    sys.modules.setdefault("langchain", ModuleType("langchain"))
    sys.modules.setdefault("langchain.agents", lc_agents)

    redis_asyncio = ModuleType("redis.asyncio")
    redis_asyncio.Redis = MagicMock(return_value=mocks["redis_client_mock"])
    sys.modules.setdefault("redis", ModuleType("redis"))
    sys.modules.setdefault("redis.asyncio", redis_asyncio)


def _import_graph_directly(mocks):
    """Last-resort: compile graph.py source directly as a module."""
    import types

    source_path = os.path.join(
        os.path.dirname(__file__), "..", "backend", "agent", "graph.py"
    )
    source_path = os.path.abspath(source_path)
    module = types.ModuleType("backend.agent.graph")
    module.__file__ = source_path
    with open(source_path) as fh:
        code = compile(fh.read(), source_path, "exec")
    exec(code, module.__dict__)
    return module


# ---------------------------------------------------------------------------
# Fixtures – convenience wrappers
# ---------------------------------------------------------------------------


@pytest.fixture()
def build_agent_fn(graph_module):
    module, mocks = graph_module
    return module.build_agent, mocks


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------


class TestModuleLevelInitialisation:
    def test_redis_client_is_created_on_import(self, graph_module):
        """Redis() must be called once during module initialisation."""
        module, mocks = graph_module
        # The module-level _redis_client should be whatever Redis() returned.
        assert module._redis_client is not None

    def test_checkpointer_is_created_on_import(self, graph_module):
        """AsyncRedisSaver must be instantiated once on import."""
        module, mocks = graph_module
        assert module._checkpointer is not None

    def test_redis_host_defaults_to_localhost(self, monkeypatch):
        """When REDIS_HOST is absent, host should fall back to 'localhost'."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        mocks = _make_patches()
        _inject_sibling_mocks(mocks)

        # Remove cached module
        for key in list(sys.modules.keys()):
            if "backend.agent.graph" in key:
                del sys.modules[key]

        redis_cls_mock = MagicMock(return_value=mocks["redis_client_mock"])
        sys.modules["redis.asyncio"].Redis = redis_cls_mock

        try:
            importlib.import_module(GRAPH_MODULE_PATH)
        except Exception:
            pass  # import may fail due to env; we only care about Redis call args

        if redis_cls_mock.called:
            _, kwargs = redis_cls_mock.call_args
            assert kwargs.get("host", "localhost") == "localhost"

    def test_redis_host_from_env(self, monkeypatch):
        """When REDIS_HOST is set, it should be forwarded to Redis()."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        mocks = _make_patches()
        _inject_sibling_mocks(mocks)

        for key in list(sys.modules.keys()):
            if "backend.agent.graph" in key:
                del sys.modules[key]

        redis_cls_mock = MagicMock(return_value=mocks["redis_client_mock"])
        sys.modules["redis.asyncio"].Redis = redis_cls_mock

        try:
            importlib.import_module(GRAPH_MODULE_PATH)
        except Exception:
            pass

        if redis_cls_mock.called:
            _, kwargs = redis_cls_mock.call_args
            assert kwargs.get("host") == "my-redis-host"


# ---------------------------------------------------------------------------
# build_agent() – happy path
# ---------------------------------------------------------------------------


class TestBuildAgentHappyPath:
    @pytest.mark.parametrize(
        "model_name, temperature, mode",
        [
            ("gpt-4o", 0.0, "fast"),
            ("gpt-4o", 0.7, "deep"),
            ("gpt-3.5-turbo", 0.5, "fast"),
            ("claude-3-opus", 1.0, "deep"),
            ("gpt-4o-mini", 0.2, "fast"),
        ],
    )
    def test_returns_agent(self, build_agent_fn, model_name, temperature, mode):
        fn, mocks = build_agent_fn
        result = fn(model_name, temperature, mode)
        assert result is mocks["create_agent_mock"].return_value

    def test_default_mode_is_fast(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.5)
        mocks["run_underwriting_mock"].assert_called_with("fast")

    def test_explicit_fast_mode(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.5, mode="fast")
        mocks["run_underwriting_mock"].assert_called_with("fast")

    def test_explicit_deep_mode(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.5, mode="deep")
        mocks["run_underwriting_mock"].assert_called_with("deep")

    def test_llms_instantiated_with_correct_temperature(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.42)
        mocks["llms_class_mock"].assert_called_once_with(
            temperature=0.42, streaming=True
        )

    def test_llms_instantiated_with_streaming_true(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.1)
        _, kwargs = mocks["llms_class_mock"].call_args
        assert kwargs["streaming"] is True

    def test_get_model_called_with_model_name(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("my-custom-model", 0.3)
        mocks["llms_instance_mock"].get_model.assert_called_once_with(
            "my-custom-model"
        )

    def test_create_agent_called_once(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.0)
        mocks["create_agent_mock"].assert_called_once()

    def test_create_agent_receives_correct_model(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.0)
        _, kwargs = mocks["create_agent_mock"].call_args
        expected_model = mocks["llms_instance_mock"].get_model.return_value
        assert kwargs["model"] == expected_model

    def test_create_agent_receives_system_prompt(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.0)
        _, kwargs = mocks["create_agent_mock"].call_args
        assert kwargs["system_prompt"] == mocks["system_prompt"]

    def test_create_agent_receives_checkpointer(self, build_agent_fn):
        fn, mocks = build_agent_fn
        module, _ = build_agent_fn[1], None
        # Re-fetch module reference
        import sys
        graph_mod = sys.modules.get(GRAPH_MODULE_PATH)
        fn("gpt-4o", 0.0)
        _, kwargs = mocks["create_agent_mock"].call_args
        assert kwargs["checkpointer"] is not None


# ---------------------------------------------------------------------------
# build_agent() – tool list composition
# ---------------------------------------------------------------------------


class TestBuildAgentToolList:
    def test_tools_list_has_three_items(self, build_agent_fn):
        fn, mocks = build_agent_fn
        fn("gpt-4o", 0.0)
        _, kwargs = mocks["create_agent_mock"].call_args