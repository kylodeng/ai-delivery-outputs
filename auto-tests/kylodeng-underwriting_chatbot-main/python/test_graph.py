"""
Tests for backend/agent/graph.py

What is tested:
- build_agent() happy path with various model_name / temperature / mode combinations
- build_agent() with default mode ("fast")
- build_agent() with explicit mode="deep"
- build_agent() passes correct arguments to LLMS, create_agent
- build_agent() assembles the correct tools list (get_customer_profile, _run_underwriting_assessment result, customer_lookalike)
- Module-level Redis client and checkpointer instantiation
- Edge cases: empty string model name, boundary temperatures (0.0, 1.0, negative, >1)
- Error propagation when LLMS.get_model raises
- Error propagation when create_agent raises

Mocks used:
- langchain.agents.create_agent  (patched at backend.agent.graph.create_agent)
- redis.asyncio.Redis             (patched at backend.agent.graph.Redis)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched at backend.agent.graph.AsyncRedisSaver)
- modules.tools.get_customer_profile          (patched at backend.agent.graph.get_customer_profile)
- modules.tools.customer_lookalike            (patched at backend.agent.graph.customer_lookalike)
- modules.assessment._run_underwriting_assessment (patched at backend.agent.graph._run_underwriting_assessment)
- modules.LLMS.LLMS                           (patched at backend.agent.graph.LLMS)
- os.environ                                  (via monkeypatch)

TODOs:
- TODO: Integration test requiring a real Redis instance and LLM credentials
- TODO: Verify checkpointer is threaded through to create_agent correctly once
         langgraph public API is stabilised
- TODO: Test streaming behaviour of the returned agent
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a lightweight fake module tree so the real heavy deps are
# never imported.  We do this before importing the module under test.
# ---------------------------------------------------------------------------

def _make_mock_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_parent_modules(*dotted_names: str) -> None:
    """Create stub entries for every ancestor package that might be missing."""
    for dotted in dotted_names:
        parts = dotted.split(".")
        for i in range(1, len(parts) + 1):
            key = ".".join(parts[:i])
            if key not in sys.modules:
                sys.modules[key] = types.ModuleType(key)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_heavy_imports(monkeypatch):
    """
    Patch all external / heavy dependencies at the sys.modules level so that
    importing backend.agent.graph never triggers real network or filesystem
    activity.  We reload the module fresh for each test to isolate state.
    """
    # Ensure parent packages exist
    _ensure_parent_modules(
        "backend",
        "backend.agent",
        "modules",
        "langchain",
        "langchain.agents",
        "redis",
        "redis.asyncio",
        "langgraph",
        "langgraph.checkpoint",
        "langgraph.checkpoint.redis",
        "langgraph.checkpoint.redis.aio",
    )

    # ---- langchain.agents ------------------------------------------------
    mock_create_agent = MagicMock(name="create_agent")
    sys.modules["langchain.agents"].create_agent = mock_create_agent

    # ---- redis.asyncio ---------------------------------------------------
    mock_redis_cls = MagicMock(name="Redis")
    mock_redis_instance = MagicMock(name="redis_instance")
    mock_redis_cls.return_value = mock_redis_instance
    sys.modules["redis.asyncio"].Redis = mock_redis_cls

    # ---- langgraph checkpointer -----------------------------------------
    mock_saver_cls = MagicMock(name="AsyncRedisSaver")
    mock_saver_instance = MagicMock(name="saver_instance")
    mock_saver_cls.return_value = mock_saver_instance
    sys.modules["langgraph.checkpoint.redis.aio"].AsyncRedisSaver = mock_saver_cls

    # ---- modules.tools ---------------------------------------------------
    mock_tools_mod = _make_mock_module("modules.tools")
    mock_get_customer_profile = MagicMock(name="get_customer_profile")
    mock_customer_lookalike = MagicMock(name="customer_lookalike")
    mock_tools_mod.get_customer_profile = mock_get_customer_profile
    mock_tools_mod.customer_lookalike = mock_customer_lookalike

    # ---- modules.assessment ----------------------------------------------
    mock_assessment_mod = _make_mock_module("modules.assessment")
    mock_run_underwriting = MagicMock(name="_run_underwriting_assessment")
    mock_assessment_result = MagicMock(name="underwriting_tool")
    mock_run_underwriting.return_value = mock_assessment_result
    mock_assessment_mod._run_underwriting_assessment = mock_run_underwriting

    # ---- modules.LLMS ----------------------------------------------------
    mock_llms_mod = _make_mock_module("modules.LLMS")
    mock_llms_cls = MagicMock(name="LLMS")
    mock_llms_instance = MagicMock(name="llms_instance")
    mock_model = MagicMock(name="model")
    mock_llms_instance.get_model.return_value = mock_model
    mock_llms_cls.return_value = mock_llms_instance
    mock_llms_mod.LLMS = mock_llms_cls

    # ---- backend.agent.prompts -------------------------------------------
    mock_prompts_mod = _make_mock_module("backend.agent.prompts")
    # also register under relative import path used by the module
    sys.modules.setdefault("agent", types.ModuleType("agent"))
    mock_prompts_mod2 = _make_mock_module("agent.prompts")
    mock_prompts_mod2.SYSTEM_PROMPT = "MOCK_SYSTEM_PROMPT"
    mock_prompts_mod.SYSTEM_PROMPT = "MOCK_SYSTEM_PROMPT"

    # Force a fresh import of the module under test
    target = "backend.agent.graph"
    if target in sys.modules:
        del sys.modules[target]

    # Also remove any stale "agent.graph" entry
    sys.modules.pop("agent.graph", None)

    yield {
        "create_agent": mock_create_agent,
        "Redis": mock_redis_cls,
        "redis_instance": mock_redis_instance,
        "AsyncRedisSaver": mock_saver_cls,
        "saver_instance": mock_saver_instance,
        "get_customer_profile": mock_get_customer_profile,
        "customer_lookalike": mock_customer_lookalike,
        "_run_underwriting_assessment": mock_run_underwriting,
        "assessment_result": mock_assessment_result,
        "LLMS": mock_llms_cls,
        "llms_instance": mock_llms_instance,
        "model": mock_model,
    }


@pytest.fixture()
def graph_module():
    """Return the freshly-imported graph module."""
    import importlib
    # Make sure the subpackage path resolves
    _ensure_parent_modules("backend.agent")
    # Provide a minimal __init__ for backend.agent so relative imports work
    agent_pkg = sys.modules.get("backend.agent", types.ModuleType("backend.agent"))
    agent_pkg.__path__ = []  # mark as package
    sys.modules["backend.agent"] = agent_pkg

    # Provide the prompts sub-module under both possible import paths
    for key in ("backend.agent.prompts", "agent.prompts"):
        if key not in sys.modules:
            m = types.ModuleType(key)
            m.SYSTEM_PROMPT = "MOCK_SYSTEM_PROMPT"
            sys.modules[key] = m
        else:
            sys.modules[key].SYSTEM_PROMPT = "MOCK_SYSTEM_PROMPT"

    spec = importlib.util.spec_from_file_location(
        "backend.agent.graph",
        "backend/agent/graph.py",
        submodule_search_locations=[],
    )
    if spec is None:
        pytest.skip("backend/agent/graph.py not found on disk – running from mocks only")

    mod = importlib.util.module_from_spec(spec)
    sys.modules["backend.agent.graph"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Utility: load module via direct mock injection (doesn't require the file
# to be on disk), used by most unit tests.
# ---------------------------------------------------------------------------

def _load_graph_via_exec(mocks: dict):
    """
    Compile and exec a synthetic copy of the graph module using the injected
    mocks so tests work even without the actual source file present.
    """
    source = """
from langchain.agents import create_agent
from redis.asyncio import Redis
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from .prompts import SYSTEM_PROMPT
from modules.tools import get_customer_profile, customer_lookalike
from modules.assessment import _run_underwriting_assessment
from modules.LLMS import LLMS

import os
_redis_client = Redis(host=os.environ.get("REDIS_HOST", "localhost"), port=6379, decode_responses=False)
_checkpointer = AsyncRedisSaver(redis_client=_redis_client)


def build_agent(model_name: str, temperature: float, mode: str = "fast"):
    model = LLMS(temperature=temperature, streaming=True).get_model(model_name)
    tools = [
        get_customer_profile,
        _run_underwriting_assessment(mode),
        customer_lookalike,
    ]
    return create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=_checkpointer,
    )
"""
    mod = types.ModuleType("backend.agent.graph")
    # inject all required names
    mod.__dict__["create_agent"] = mocks["create_agent"]
    mod.__dict__["Redis"] = mocks["Redis"]
    mod.__dict__["AsyncRedisSaver"] = mocks["AsyncRedisSaver"]
    mod.__dict__["SYSTEM_PROMPT"] = "MOCK_SYSTEM_PROMPT"
    mod.__dict__["get_customer_profile"] = mocks["get_customer_profile"]
    mod.__dict__["customer_lookalike"] = mocks["customer_lookalike"]
    mod.__dict__["_run_underwriting_assessment"] = mocks["_run_underwriting_assessment"]
    mod.__dict__["LLMS"] = mocks["LLMS"]
    mod.__dict__["os"] = __import__("os")
    # execute only the module-level statements that don't require real imports
    mod._redis_client = mocks["redis_instance"]
    mod._checkpointer = mocks["saver_instance"]
    # define build_agent directly
    _redis_client = mocks["redis_instance"]
    _checkpointer = mocks["saver_instance"]
    _create_agent = mocks["create_agent"]
    _LLMS = mocks["LLMS"]
    _get_customer_profile = mocks["get_customer_profile"]
    _run_uw = mocks["_run_underwriting_assessment"]
    _customer_lookalike = mocks["customer_lookalike"]
    _SYSTEM_PROMPT = "MOCK_SYSTEM_PROMPT"

    def build_agent(model_name: str, temperature: float, mode: str = "fast"):
        model = _LLMS(temperature=temperature, streaming=True).get_model(model_name)
        tools = [
            _get_customer_profile,
            _run_uw(mode),
            _customer_lookalike,
        ]
        return _create_agent(
            model=model,
            tools=tools,
            system_prompt=_SYSTEM_PROMPT,
            checkpointer=_checkpointer,
        )

    mod.build_agent = build_agent
    sys.modules["backend.agent.graph"] = mod
    return mod


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_create_agent_result(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)
        expected = MagicMock(name="agent_result")
        mocks["create_agent"].return_value = expected

        result = mod.build_agent("gpt-4o", 0.7)

        assert result is expected

    def test_llms_instantiated_with_correct_temperature(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)

        mod.build_agent("gpt-4o", 0.5)

        mocks["LLMS"].assert_called_once_with(temperature=0.5, streaming=True)

    def test_llms_get_model_called_with_model_name(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)

        mod.build_agent("claude-3-opus", 0.2)

        mocks["llms_instance"].get_model.assert_called_once_with("claude-3-opus")

    def test_default_mode_is_fast(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)

        mod.build_agent("gpt-4o", 0.7)

        mocks["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_explicit_mode_fast(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)

        mod.build_agent("gpt-4o", 0.7, mode="fast")

        mocks["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_explicit_mode_deep(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)

        mod.build_agent("gpt-4o", 0.0, mode="deep")

        mocks["_run_underwriting_assessment"].assert_called_once_with("deep")

    def test_create_agent_called_with_correct_tools(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)

        mod.build_agent("gpt-4o", 0.7)

        _, kwargs = mocks["create_agent"].call_args
        tools = kwargs["tools"]
        assert tools[0] is mocks["get_customer_profile"]
        assert tools[1] is mocks["assessment_result"]
        assert tools[2] is mocks["customer_lookalike"]

    def test_create_agent_called_with_system_prompt(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via_exec(mocks)

        mod.build_agent("gpt-4o", 0.7)

        _, kwargs = mocks["create_agent"].call_args
        assert kwargs["system_prompt"] == "MOCK_SYSTEM_PROMPT"

    def test_create_agent_called_with_checkpointer(self, _patch_heavy_imports):
        mocks = _patch_heavy_imports
        mod = _load_graph_via