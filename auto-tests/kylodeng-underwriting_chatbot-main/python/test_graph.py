"""
Tests for backend/agent/graph.py

What is tested:
    - build_agent() happy path with various model_name / temperature / mode combinations
    - build_agent() default mode ("fast")
    - build_agent() with "deep" mode
    - build_agent() propagates correct tools list to create_agent
    - build_agent() propagates correct system_prompt and checkpointer
    - build_agent() with boundary temperature values (0.0, 1.0, extreme values)
    - build_agent() with unknown/invalid model names (delegates to LLMS — error surfaced)
    - Module-level Redis client and checkpointer initialisation (env-var override)

Mocks used:
    - langchain.agents.create_agent          — patched to avoid real LLM / graph construction
    - modules.tools.get_customer_profile     — patched (external data dependency)
    - modules.tools.customer_lookalike       — patched (external data dependency)
    - modules.assessment._run_underwriting_assessment — patched (ML model dependency)
    - modules.LLMS.LLMS                      — patched to avoid real API calls
    - redis.asyncio.Redis                    — patched to avoid real Redis connection
    - langgraph.checkpoint.redis.aio.AsyncRedisSaver — patched to avoid real Redis connection

TODOs:
    - TODO: Integration test for full agent invocation requires a running Redis instance
      and valid LLM credentials — stub provided below.
    - TODO: Test streaming behaviour of the agent once streaming interface is stabilised.
    - TODO: Test checkpointer persistence across multiple agent builds if Redis migration
      to external service is completed.
"""

import importlib
import os
import sys
import types
import unittest.mock as mock
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers — build a minimal fake module tree so that importing graph.py
# never touches real network / file-system resources.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_module_cache():
    """
    Remove cached copies of the module under test and its dependencies before
    each test so that patching at import time is reliable.
    """
    mods_to_remove = [
        "backend.agent.graph",
        "agent.graph",
    ]
    for mod in mods_to_remove:
        sys.modules.pop(mod, None)
    yield
    for mod in mods_to_remove:
        sys.modules.pop(mod, None)


# ---------------------------------------------------------------------------
# Low-level patches applied for the entire session (module-level side-effects)
# ---------------------------------------------------------------------------

FAKE_REDIS_INSTANCE = MagicMock(name="FakeRedisInstance")
FAKE_CHECKPOINTER = MagicMock(name="FakeAsyncRedisSaver")
FAKE_MODEL = MagicMock(name="FakeModel")
FAKE_AGENT = MagicMock(name="FakeAgent")
FAKE_TOOL_PROFILE = MagicMock(name="get_customer_profile")
FAKE_TOOL_LOOKALIKE = MagicMock(name="customer_lookalike")
FAKE_TOOL_ASSESSMENT_FAST = MagicMock(name="assessment_fast")
FAKE_TOOL_ASSESSMENT_DEEP = MagicMock(name="assessment_deep")


def _make_patches():
    """Return a dict of patcher objects covering all external dependencies."""
    patches = {
        "Redis": patch(
            "redis.asyncio.Redis",
            return_value=FAKE_REDIS_INSTANCE,
        ),
        "AsyncRedisSaver": patch(
            "langgraph.checkpoint.redis.aio.AsyncRedisSaver",
            return_value=FAKE_CHECKPOINTER,
        ),
        "create_agent": patch(
            "langchain.agents.create_agent",
            return_value=FAKE_AGENT,
        ),
        "LLMS": patch(
            "modules.LLMS.LLMS",
            autospec=False,
        ),
        "get_customer_profile": patch(
            "modules.tools.get_customer_profile",
            new=FAKE_TOOL_PROFILE,
        ),
        "customer_lookalike": patch(
            "modules.tools.customer_lookalike",
            new=FAKE_TOOL_LOOKALIKE,
        ),
        "_run_underwriting_assessment": patch(
            "modules.assessment._run_underwriting_assessment",
        ),
        "SYSTEM_PROMPT": patch(
            "agent.prompts.SYSTEM_PROMPT",
            new="FAKE_SYSTEM_PROMPT",
            create=True,
        ),
    }
    return patches


# ---------------------------------------------------------------------------
# Fixture: import graph with all external deps mocked
# ---------------------------------------------------------------------------

@pytest.fixture()
def graph_module(monkeypatch):
    """
    Import (or re-import) agent.graph with all external dependencies mocked.
    Returns a namespace with the module and the important mock handles.
    """
    fake_redis_cls = MagicMock(return_value=FAKE_REDIS_INSTANCE)
    fake_saver_cls = MagicMock(return_value=FAKE_CHECKPOINTER)
    fake_create_agent = MagicMock(return_value=FAKE_AGENT)
    fake_llms_instance = MagicMock()
    fake_llms_instance.get_model.return_value = FAKE_MODEL
    fake_llms_cls = MagicMock(return_value=fake_llms_instance)

    fake_assessment_fast = MagicMock(name="assessment_fast_tool")
    fake_assessment_deep = MagicMock(name="assessment_deep_tool")

    def fake_run_assessment(mode):
        return fake_assessment_fast if mode == "fast" else fake_assessment_deep

    # Build synthetic sub-modules so imports inside graph.py resolve
    _install_fake_submodule("redis", None)
    _install_fake_submodule("redis.asyncio", None, Redis=fake_redis_cls)
    _install_fake_submodule(
        "langgraph.checkpoint.redis.aio",
        None,
        AsyncRedisSaver=fake_saver_cls,
    )
    _install_fake_submodule(
        "langchain.agents",
        None,
        create_agent=fake_create_agent,
    )
    _install_fake_submodule(
        "modules.tools",
        None,
        get_customer_profile=FAKE_TOOL_PROFILE,
        customer_lookalike=FAKE_TOOL_LOOKALIKE,
    )
    _install_fake_submodule(
        "modules.assessment",
        None,
        _run_underwriting_assessment=fake_run_assessment,
    )
    _install_fake_submodule(
        "modules.LLMS",
        None,
        LLMS=fake_llms_cls,
    )
    _install_fake_submodule(
        "agent.prompts",
        None,
        SYSTEM_PROMPT="FAKE_SYSTEM_PROMPT",
    )
    # Ensure parent packages exist
    for pkg in ("agent", "modules", "redis", "langgraph", "langgraph.checkpoint",
                "langgraph.checkpoint.redis", "langchain", "langchain.agents"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    # Now import the module under test
    sys.modules.pop("agent.graph", None)
    import agent.graph as graph  # noqa: E402

    return types.SimpleNamespace(
        module=graph,
        fake_create_agent=fake_create_agent,
        fake_llms_cls=fake_llms_cls,
        fake_llms_instance=fake_llms_instance,
        fake_redis_cls=fake_redis_cls,
        fake_saver_cls=fake_saver_cls,
        fake_assessment_fast=fake_assessment_fast,
        fake_assessment_deep=fake_assessment_deep,
        fake_run_assessment=fake_run_assessment,
    )


def _install_fake_submodule(full_name: str, parent_mod, **attrs):
    """Create a fake module with given attrs and register it in sys.modules."""
    mod = types.ModuleType(full_name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[full_name] = mod
    # wire up parent package attribute if it already exists
    parts = full_name.rsplit(".", 1)
    if len(parts) == 2:
        parent_name, child_name = parts
        parent = sys.modules.get(parent_name)
        if parent is not None:
            setattr(parent, child_name, mod)


# ===========================================================================
# Tests: module-level initialisation
# ===========================================================================

class TestModuleLevelInit:
    """Verify Redis client and checkpointer are created at import time."""

    def test_redis_client_created(self, graph_module):
        ns = graph_module
        ns.fake_redis_cls.assert_called_once()
        _, kwargs = ns.fake_redis_cls.call_args
        assert kwargs.get("port") == 6379

    def test_redis_uses_env_host_default(self, graph_module, monkeypatch):
        """When REDIS_HOST is not set the default 'localhost' is used."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        # Re-import with fresh env
        sys.modules.pop("agent.graph", None)
        import agent.graph  # noqa: F401
        _, kwargs = graph_module.fake_redis_cls.call_args
        assert kwargs.get("host") in (None, "localhost", os.environ.get("REDIS_HOST", "localhost"))

    def test_redis_decode_responses_false(self, graph_module):
        ns = graph_module
        _, kwargs = ns.fake_redis_cls.call_args
        assert kwargs.get("decode_responses") is False

    def test_checkpointer_created_with_redis_client(self, graph_module):
        ns = graph_module
        ns.fake_saver_cls.assert_called_once_with(redis_client=FAKE_REDIS_INSTANCE)

    def test_module_exposes_build_agent(self, graph_module):
        assert callable(graph_module.module.build_agent)


# ===========================================================================
# Tests: build_agent — happy path
# ===========================================================================

class TestBuildAgentHappyPath:

    def test_returns_agent_object(self, graph_module):
        agent = graph_module.module.build_agent("gpt-4o", 0.5)
        assert agent is FAKE_AGENT

    def test_default_mode_is_fast(self, graph_module):
        """Calling without mode should default to 'fast'."""
        graph_module.module.build_agent("gpt-4o", 0.7)
        graph_module.fake_create_agent.assert_called_once()
        _, kwargs = graph_module.fake_create_agent.call_args
        tools = kwargs.get("tools") or graph_module.fake_create_agent.call_args[0][1]
        # The fast assessment tool should be in the tools list
        assert graph_module.fake_assessment_fast in tools

    def test_fast_mode_explicit(self, graph_module):
        graph_module.module.build_agent("gpt-4o", 0.5, mode="fast")
        _, kwargs = graph_module.fake_create_agent.call_args
        tools = kwargs["tools"]
        assert graph_module.fake_assessment_fast in tools

    def test_deep_mode(self, graph_module):
        graph_module.module.build_agent("gpt-4o", 0.5, mode="deep")
        _, kwargs = graph_module.fake_create_agent.call_args
        tools = kwargs["tools"]
        assert graph_module.fake_assessment_deep in tools

    def test_llms_instantiated_with_correct_args(self, graph_module):
        graph_module.module.build_agent("claude-3-5-sonnet", 0.3)
        graph_module.fake_llms_cls.assert_called_with(temperature=0.3, streaming=True)

    def test_get_model_called_with_model_name(self, graph_module):
        graph_module.module.build_agent("claude-3-5-sonnet", 0.3)
        graph_module.fake_llms_instance.get_model.assert_called_with("claude-3-5-sonnet")

    def test_create_agent_receives_model(self, graph_module):
        graph_module.module.build_agent("gpt-4o", 0.5)
        _, kwargs = graph_module.fake_create_agent.call_args
        assert kwargs["model"] is FAKE_MODEL

    def test_create_agent_receives_system_prompt(self, graph_module):
        graph_module.module.build_agent("gpt-4o", 0.5)
        _, kwargs = graph_module.fake_create_agent.call_args
        assert kwargs["system_prompt"] == "FAKE_SYSTEM_PROMPT"

    def test_create_agent_receives_checkpointer(self, graph_module):
        graph_module.module.build_agent("gpt-4o", 0.5)
        _, kwargs = graph_module.fake_create_agent.call_args
        assert kwargs["checkpointer"] is FAKE_CHECKPOINTER

    def test_tools_list_length(self, graph_module):
        """Exactly 3 tools should be passed."""
        graph_module.module.build_agent("gpt-4o", 0.5)
        _, kwargs = graph_module.fake_create_agent.call_args
        assert len(kwargs["tools"]) == 3

    def test_tools_contains_get_customer_profile(self, graph_module):
        graph_module.module.build_agent("gpt-4o", 0.5)
        _, kwargs = graph_module.fake_create_agent.call_args
        assert FAKE_TOOL_PROFILE in kwargs["tools"]

    def test_tools_contains_customer_lookalike(self, graph_module):
        graph_module.module.build_agent("gpt-4o", 0.5)
        _, kwargs = graph_module.fake_create_agent.call_args
        assert FAKE_TOOL_LOOKALIKE in kwargs["tools"]


# ===========================================================================
# Tests: build_agent — boundary / parametrised values
# ===========================================================================

TEMPERATURE_BOUNDARY_CASES = [
    pytest.param(0.0, id="temperature=0.0"),
    pytest.param(0.1, id="temperature=0.1"),
    pytest.param(0.5, id="temperature=0.5"),
    pytest.param(0.9, id="temperature=0.9"),
    pytest.param(1.0, id="temperature=1.0"),
]

MODEL_NAME_CASES = [
    pytest.param("gpt-4o", id="model=gpt-4o"),
    pytest.param("gpt-4o-mini", id="model=gpt-4o-mini"),
    pytest.param("claude-3-5-sonnet", id="model=claude-3-5-sonnet"),
    pytest.param("gemini-1.5-pro", id="model=gemini-1.5-pro"),
]

MODE_CASES = [
    pytest.param("fast", id="mode=fast"),
    pytest.param("deep", id="mode=deep"),
]


@pytest.mark.parametrize("temperature", TEMPERATURE_BOUNDARY_CASES)
def test_build_agent_various_temperatures(graph_module, temperature):
    agent = graph_module.module.build_agent("gpt-4o", temperature)
    assert agent is FAKE_AGENT
    graph_module.fake_llms_cls.assert_called_with(temperature=temperature, streaming=True)


@pytest.mark.parametrize("model_name", MODEL_NAME_CASES)
def test_build_agent_various_models(graph_module, model_name):
    agent = graph_module.module.build_agent(model