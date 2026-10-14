"""
Test module for backend/agent/graph.py

What is tested:
    - build_agent() function: happy path, edge cases, error conditions, boundary values
    - Module-level Redis client and checkpointer initialization
    - Correct assembly of tools list (get_customer_profile, _run_underwriting_assessment, customer_lookalike)
    - Correct delegation to LLMS, create_agent, AsyncRedisSaver

Mocks used:
    - langchain.agents.create_agent (patched)
    - redis.asyncio.Redis (patched at module level)
    - langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched at module level)
    - modules.tools.get_customer_profile (patched)
    - modules.tools.customer_lookalike (patched)
    - modules.assessment._run_underwriting_assessment (patched)
    - modules.LLMS.LLMS (patched)
    - os.environ (manipulated via monkeypatch)

TODOs:
    - TODO: Integration test for actual Redis connectivity (requires live Redis or testcontainer)
    - TODO: Test agent invocation / streaming behaviour (requires LangGraph runtime context)
    - TODO: Test checkpointer persistence semantics across calls (requires Redis state inspection)
"""

import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake module tree so importing graph.py never
# touches real external dependencies, regardless of install state.
# ---------------------------------------------------------------------------

def _make_fake_modules():
    """
    Register lightweight fake versions of every third-party / sibling module
    that backend/agent/graph.py imports before we import graph itself.
    Returns a dict of {module_path: mock} for later assertions.
    """
    mocks: dict[str, MagicMock] = {}

    # --- langchain.agents ---------------------------------------------------
    fake_langchain_agents = MagicMock(name="langchain.agents")
    fake_create_agent = MagicMock(name="create_agent", return_value=MagicMock(name="agent_instance"))
    fake_langchain_agents.create_agent = fake_create_agent
    sys.modules.setdefault("langchain", MagicMock(name="langchain"))
    sys.modules["langchain.agents"] = fake_langchain_agents
    mocks["langchain.agents"] = fake_langchain_agents
    mocks["create_agent"] = fake_create_agent

    # --- redis / redis.asyncio ----------------------------------------------
    fake_redis_module = MagicMock(name="redis")
    fake_redis_asyncio = MagicMock(name="redis.asyncio")
    fake_redis_instance = MagicMock(name="redis_instance")
    fake_redis_asyncio.Redis = MagicMock(name="Redis", return_value=fake_redis_instance)
    fake_redis_module.asyncio = fake_redis_asyncio
    sys.modules["redis"] = fake_redis_module
    sys.modules["redis.asyncio"] = fake_redis_asyncio
    mocks["redis.asyncio"] = fake_redis_asyncio
    mocks["Redis"] = fake_redis_asyncio.Redis
    mocks["redis_instance"] = fake_redis_instance

    # --- langgraph ----------------------------------------------------------
    fake_langgraph = MagicMock(name="langgraph")
    fake_langgraph_checkpoint = MagicMock(name="langgraph.checkpoint")
    fake_langgraph_checkpoint_redis = MagicMock(name="langgraph.checkpoint.redis")
    fake_langgraph_checkpoint_redis_aio = MagicMock(name="langgraph.checkpoint.redis.aio")
    fake_checkpointer_instance = MagicMock(name="checkpointer_instance")
    fake_AsyncRedisSaver = MagicMock(name="AsyncRedisSaver", return_value=fake_checkpointer_instance)
    fake_langgraph_checkpoint_redis_aio.AsyncRedisSaver = fake_AsyncRedisSaver
    sys.modules["langgraph"] = fake_langgraph
    sys.modules["langgraph.checkpoint"] = fake_langgraph_checkpoint
    sys.modules["langgraph.checkpoint.redis"] = fake_langgraph_checkpoint_redis
    sys.modules["langgraph.checkpoint.redis.aio"] = fake_langgraph_checkpoint_redis_aio
    mocks["langgraph.checkpoint.redis.aio"] = fake_langgraph_checkpoint_redis_aio
    mocks["AsyncRedisSaver"] = fake_AsyncRedisSaver
    mocks["checkpointer_instance"] = fake_checkpointer_instance

    # --- agent.prompts (sibling) --------------------------------------------
    fake_prompts = MagicMock(name="agent.prompts")
    fake_prompts.SYSTEM_PROMPT = "FAKE_SYSTEM_PROMPT"
    sys.modules["backend.agent.prompts"] = fake_prompts
    sys.modules["agent.prompts"] = fake_prompts
    mocks["prompts"] = fake_prompts

    # --- modules.tools ------------------------------------------------------
    fake_tools_module = MagicMock(name="modules.tools")
    fake_get_customer_profile = MagicMock(name="get_customer_profile")
    fake_customer_lookalike = MagicMock(name="customer_lookalike")
    fake_tools_module.get_customer_profile = fake_get_customer_profile
    fake_tools_module.customer_lookalike = fake_customer_lookalike
    sys.modules.setdefault("modules", MagicMock(name="modules"))
    sys.modules["modules.tools"] = fake_tools_module
    mocks["modules.tools"] = fake_tools_module
    mocks["get_customer_profile"] = fake_get_customer_profile
    mocks["customer_lookalike"] = fake_customer_lookalike

    # --- modules.assessment -------------------------------------------------
    fake_assessment_module = MagicMock(name="modules.assessment")
    fake_assessment_tool_fast = MagicMock(name="assessment_tool_fast")
    fake_assessment_tool_deep = MagicMock(name="assessment_tool_deep")

    def fake_run_underwriting_assessment(mode="fast"):
        if mode == "fast":
            return fake_assessment_tool_fast
        if mode == "deep":
            return fake_assessment_tool_deep
        return MagicMock(name=f"assessment_tool_{mode}")

    fake_assessment_module._run_underwriting_assessment = MagicMock(
        name="_run_underwriting_assessment",
        side_effect=fake_run_underwriting_assessment,
    )
    sys.modules["modules.assessment"] = fake_assessment_module
    mocks["modules.assessment"] = fake_assessment_module
    mocks["_run_underwriting_assessment"] = fake_assessment_module._run_underwriting_assessment
    mocks["assessment_tool_fast"] = fake_assessment_tool_fast
    mocks["assessment_tool_deep"] = fake_assessment_tool_deep

    # --- modules.LLMS -------------------------------------------------------
    fake_llms_module = MagicMock(name="modules.LLMS")
    fake_llms_instance = MagicMock(name="LLMS_instance")
    fake_model = MagicMock(name="model_instance")
    fake_llms_instance.get_model = MagicMock(name="get_model", return_value=fake_model)
    fake_LLMS_class = MagicMock(name="LLMS", return_value=fake_llms_instance)
    fake_llms_module.LLMS = fake_LLMS_class
    sys.modules["modules.LLMS"] = fake_llms_module
    mocks["modules.LLMS"] = fake_llms_module
    mocks["LLMS"] = fake_LLMS_class
    mocks["llms_instance"] = fake_llms_instance
    mocks["model_instance"] = fake_model

    return mocks


# ---------------------------------------------------------------------------
# Module-level setup: register fake modules ONCE before anything is imported
# ---------------------------------------------------------------------------
_MOCKS = _make_fake_modules()


# Now safe to import the module under test
import importlib as _importlib

# Remove any previously cached version so our mocks take effect
for _key in list(sys.modules.keys()):
    if "agent.graph" in _key or "backend.agent.graph" in _key:
        del sys.modules[_key]

import backend.agent.graph as _graph_module_placeholder  # noqa: E402

# Actually import through the relative path the source uses
try:
    import agent.graph as graph_module
except ModuleNotFoundError:
    # Fallback: import as backend.agent.graph
    import backend.agent.graph as graph_module  # type: ignore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset call counts on shared mocks between tests."""
    _MOCKS["create_agent"].reset_mock()
    _MOCKS["LLMS"].reset_mock()
    _MOCKS["llms_instance"].get_model.reset_mock()
    _MOCKS["_run_underwriting_assessment"].reset_mock()
    # Restore the side_effect after reset
    def fake_run_underwriting_assessment(mode="fast"):
        if mode == "fast":
            return _MOCKS["assessment_tool_fast"]
        if mode == "deep":
            return _MOCKS["assessment_tool_deep"]
        return MagicMock(name=f"assessment_tool_{mode}")

    _MOCKS["_run_underwriting_assessment"].side_effect = fake_run_underwriting_assessment
    yield


@pytest.fixture()
def fake_agent():
    """Return a fresh fake agent each test."""
    agent = MagicMock(name="fresh_agent_instance")
    _MOCKS["create_agent"].return_value = agent
    return agent


# ---------------------------------------------------------------------------
# Tests: build_agent – happy path
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_instance(self, fake_agent):
        result = graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        assert result is fake_agent

    def test_default_mode_is_fast(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _MOCKS["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_explicit_fast_mode(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0, mode="fast")
        _MOCKS["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_deep_mode(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0, mode="deep")
        _MOCKS["_run_underwriting_assessment"].assert_called_once_with("deep")

    def test_llms_instantiated_with_correct_args(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.7)
        _MOCKS["LLMS"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _MOCKS["llms_instance"].get_model.assert_called_once_with("gpt-4o")

    def test_create_agent_called_with_system_prompt(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _, kwargs = _MOCKS["create_agent"].call_args
        assert kwargs.get("system_prompt") == "FAKE_SYSTEM_PROMPT"

    def test_create_agent_called_with_model(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _, kwargs = _MOCKS["create_agent"].call_args
        assert kwargs.get("model") is _MOCKS["model_instance"]

    def test_create_agent_called_with_checkpointer(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _, kwargs = _MOCKS["create_agent"].call_args
        assert kwargs.get("checkpointer") is not None

    def test_tools_list_has_three_items(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _, kwargs = _MOCKS["create_agent"].call_args
        tools = kwargs.get("tools")
        assert tools is not None
        assert len(tools) == 3

    def test_tools_list_contains_get_customer_profile(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _, kwargs = _MOCKS["create_agent"].call_args
        tools = kwargs["tools"]
        assert _MOCKS["get_customer_profile"] in tools

    def test_tools_list_contains_customer_lookalike(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _, kwargs = _MOCKS["create_agent"].call_args
        tools = kwargs["tools"]
        assert _MOCKS["customer_lookalike"] in tools

    def test_tools_list_contains_assessment_tool_fast(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0, mode="fast")
        _, kwargs = _MOCKS["create_agent"].call_args
        tools = kwargs["tools"]
        assert _MOCKS["assessment_tool_fast"] in tools

    def test_tools_list_contains_assessment_tool_deep(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0, mode="deep")
        _, kwargs = _MOCKS["create_agent"].call_args
        tools = kwargs["tools"]
        assert _MOCKS["assessment_tool_deep"] in tools


# ---------------------------------------------------------------------------
# Tests: build_agent – various model names and temperatures (parametrised)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model_name,temperature", [
    ("gpt-4o", 0.0),
    ("gpt-4o", 1.0),
    ("gpt-4o-mini", 0.5),
    ("claude-3-5-sonnet", 0.3),
    ("gemini-1.5-pro", 0.9),
    ("gpt-4o", 0.7),
])
def test_build_agent_parametrised_model_temperature(model_name, temperature):
    agent = MagicMock(name="agent")
    _MOCKS["create_agent"].return_value = agent
    result = graph_module.build_agent(model_name=model_name, temperature=temperature)
    assert result is agent
    _MOCKS["LLMS"].assert_called_once_with(temperature=temperature, streaming=True)
    _MOCKS["llms_instance"].get_model.assert_called_once_with(model_name)


# ---------------------------------------------------------------------------
# Tests: build_agent – boundary values
# ---------------------------------------------------------------------------

class TestBuildAgentBoundaryValues:

    def test_temperature_zero(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=0.0)
        _MOCKS["LLMS"].assert_called_once_with(temperature=0.0, streaming=True)

    def test_temperature_one(self):
        graph_module.build_agent(model_name="gpt-4o", temperature=1.0)
        _MOCKS["LLMS"].assert_called_once_with(temperature=1.0, streaming=True)

    def test_temperature_very_small(self):
        graph_module.build