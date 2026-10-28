"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path, various model/temperature/mode combinations,
  default mode parameter, edge cases for temperature and model_name values.

Mocks used:
- backend.agent.graph.LLMS — prevents real LLM instantiation
- backend.agent.graph.create_agent — prevents real agent creation
- backend.agent.graph.Redis — prevents real Redis connection
- backend.agent.graph.AsyncRedisSaver — prevents real Redis saver instantiation
- backend.agent.graph.get_customer_profile — stubbed tool
- backend.agent.graph.customer_lookalike — stubbed tool
- backend.agent.graph._run_underwriting_assessment — prevents real assessment execution
- backend.agent.graph.SYSTEM_PROMPT — stubbed prompt string

TODOs:
- TODO: Integration test for full agent graph execution requires a real or
        containerised Redis instance and model credentials.
- TODO: Test actual tool invocation within the agent once tool-calling
        behaviour can be exercised without a live LLM.
- TODO: Test checkpointer persistence behaviour (AsyncRedisSaver.setup / get / put)
        once Redis is migrated to an external service per the repo TODO.
- TODO: Verify SYSTEM_PROMPT content is correctly injected into the agent —
        needs deeper inspection of create_agent call kwargs.
"""

import os
import importlib
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

MOCK_SYSTEM_PROMPT = "You are an underwriting assistant."


@pytest.fixture(autouse=True)
def env_defaults(monkeypatch):
    """Ensure REDIS_HOST is predictable during tests."""
    monkeypatch.setenv("REDIS_HOST", "test-redis-host")


# ---------------------------------------------------------------------------
# Patch targets (module is imported lazily so patches apply before import)
# ---------------------------------------------------------------------------

PATCH_BASE = "backend.agent.graph"


@pytest.fixture()
def mock_redis_class():
    with patch(f"{PATCH_BASE}.Redis") as m:
        yield m


@pytest.fixture()
def mock_async_redis_saver():
    with patch(f"{PATCH_BASE}.AsyncRedisSaver") as m:
        yield m


@pytest.fixture()
def mock_llms():
    with patch(f"{PATCH_BASE}.LLMS") as m:
        yield m


@pytest.fixture()
def mock_create_agent():
    with patch(f"{PATCH_BASE}.create_agent") as m:
        yield m


@pytest.fixture()
def mock_get_customer_profile():
    with patch(f"{PATCH_BASE}.get_customer_profile", new=MagicMock(name="get_customer_profile")) as m:
        yield m


@pytest.fixture()
def mock_customer_lookalike():
    with patch(f"{PATCH_BASE}.customer_lookalike", new=MagicMock(name="customer_lookalike")) as m:
        yield m


@pytest.fixture()
def mock_run_underwriting_assessment():
    with patch(f"{PATCH_BASE}._run_underwriting_assessment") as m:
        m.return_value = MagicMock(name="assessment_tool")
        yield m


@pytest.fixture()
def mock_system_prompt():
    with patch(f"{PATCH_BASE}.SYSTEM_PROMPT", MOCK_SYSTEM_PROMPT):
        yield MOCK_SYSTEM_PROMPT


@pytest.fixture()
def all_mocks(
    mock_redis_class,
    mock_async_redis_saver,
    mock_llms,
    mock_create_agent,
    mock_get_customer_profile,
    mock_customer_lookalike,
    mock_run_underwriting_assessment,
    mock_system_prompt,
):
    """Convenience fixture that activates every mock at once."""
    return {
        "redis_class": mock_redis_class,
        "async_redis_saver": mock_async_redis_saver,
        "llms": mock_llms,
        "create_agent": mock_create_agent,
        "get_customer_profile": mock_get_customer_profile,
        "customer_lookalike": mock_customer_lookalike,
        "run_underwriting_assessment": mock_run_underwriting_assessment,
        "system_prompt": mock_system_prompt,
    }


# ---------------------------------------------------------------------------
# Utility: fresh import of the module under test
# ---------------------------------------------------------------------------

def import_graph_module():
    """
    Import (or re-import) backend.agent.graph.
    Must be called *after* all patches are in place so module-level code
    (Redis / AsyncRedisSaver construction) uses mocked objects.
    """
    import sys
    # Remove cached version so module-level code re-runs with current patches
    sys.modules.pop("backend.agent.graph", None)
    import backend.agent.graph as graph_module
    return graph_module


# ---------------------------------------------------------------------------
# Module-level initialisation tests
# ---------------------------------------------------------------------------

class TestModuleLevelInitialisation:
    """
    Validate that importing the module creates the Redis client and
    checkpointer with the expected arguments.
    """

    def test_redis_client_uses_env_host(self, all_mocks, monkeypatch):
        monkeypatch.setenv("REDIS_HOST", "my-custom-host")
        graph = import_graph_module()
        all_mocks["redis_class"].assert_called_once_with(
            host="my-custom-host",
            port=6379,
            decode_responses=False,
        )

    def test_redis_client_defaults_to_localhost(self, all_mocks, monkeypatch):
        monkeypatch.delenv("REDIS_HOST", raising=False)
        graph = import_graph_module()
        all_mocks["redis_class"].assert_called_once_with(
            host="localhost",
            port=6379,
            decode_responses=False,
        )

    def test_async_redis_saver_receives_redis_client(self, all_mocks):
        redis_instance = all_mocks["redis_class"].return_value
        graph = import_graph_module()
        all_mocks["async_redis_saver"].assert_called_once_with(
            redis_client=redis_instance
        )

    def test_module_exposes_build_agent(self, all_mocks):
        graph = import_graph_module()
        assert callable(graph.build_agent)


# ---------------------------------------------------------------------------
# build_agent — happy path
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_create_agent_result(self, all_mocks):
        graph = import_graph_module()
        fake_agent = MagicMock(name="agent")
        all_mocks["create_agent"].return_value = fake_agent

        result = graph.build_agent("gpt-4o", 0.7)

        assert result is fake_agent

    def test_llms_instantiated_with_correct_args(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.5)

        all_mocks["llms"].assert_called_once_with(temperature=0.5, streaming=True)

    def test_get_model_called_with_model_name(self, all_mocks):
        graph = import_graph_module()
        mock_llms_instance = all_mocks["llms"].return_value
        graph.build_agent("gpt-4o", 0.5)

        mock_llms_instance.get_model.assert_called_once_with("gpt-4o")

    def test_run_underwriting_assessment_called_with_mode(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.5, mode="fast")

        all_mocks["run_underwriting_assessment"].assert_called_once_with("fast")

    def test_create_agent_called_with_correct_kwargs(self, all_mocks):
        graph = import_graph_module()
        fake_model = all_mocks["llms"].return_value.get_model.return_value
        fake_assessment_tool = all_mocks["run_underwriting_assessment"].return_value
        fake_checkpointer = all_mocks["async_redis_saver"].return_value

        graph.build_agent("gpt-4o", 0.7)

        all_mocks["create_agent"].assert_called_once_with(
            model=fake_model,
            tools=[
                all_mocks["get_customer_profile"],
                fake_assessment_tool,
                all_mocks["customer_lookalike"],
            ],
            system_prompt=MOCK_SYSTEM_PROMPT,
            checkpointer=fake_checkpointer,
        )

    def test_default_mode_is_fast(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.7)

        all_mocks["run_underwriting_assessment"].assert_called_once_with("fast")

    def test_deep_mode_passed_through(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.7, mode="deep")

        all_mocks["run_underwriting_assessment"].assert_called_once_with("deep")


# ---------------------------------------------------------------------------
# build_agent — parametrised model / temperature combinations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model_name,temperature,mode", [
    ("gpt-4o", 0.0, "fast"),
    ("gpt-4o", 1.0, "fast"),
    ("gpt-4o", 0.7, "deep"),
    ("gpt-3.5-turbo", 0.5, "fast"),
    ("claude-3-opus", 0.3, "deep"),
    ("mistral-large", 0.9, "fast"),
])
def test_build_agent_parametrised(all_mocks, model_name, temperature, mode):
    graph = import_graph_module()
    fake_agent = MagicMock(name=f"agent_{model_name}")
    all_mocks["create_agent"].return_value = fake_agent

    result = graph.build_agent(model_name, temperature, mode)

    # LLMS constructed with the right temperature
    all_mocks["llms"].assert_called_once_with(temperature=temperature, streaming=True)
    # get_model called with the right name
    all_mocks["llms"].return_value.get_model.assert_called_once_with(model_name)
    # assessment called with the right mode
    all_mocks["run_underwriting_assessment"].assert_called_once_with(mode)
    # returns whatever create_agent returns
    assert result is fake_agent


# ---------------------------------------------------------------------------
# build_agent — boundary / edge cases for temperature
# ---------------------------------------------------------------------------

class TestBuildAgentTemperatureBoundaries:

    def test_temperature_zero(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.0)
        all_mocks["llms"].assert_called_once_with(temperature=0.0, streaming=True)

    def test_temperature_one(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 1.0)
        all_mocks["llms"].assert_called_once_with(temperature=1.0, streaming=True)

    def test_temperature_fractional(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.123456)
        all_mocks["llms"].assert_called_once_with(temperature=0.123456, streaming=True)


# ---------------------------------------------------------------------------
# build_agent — error / negative cases
# ---------------------------------------------------------------------------

class TestBuildAgentErrorConditions:

    def test_llms_raises_propagates(self, all_mocks):
        all_mocks["llms"].side_effect = ValueError("Unknown model provider")
        graph = import_graph_module()

        with pytest.raises(ValueError, match="Unknown model provider"):
            graph.build_agent("unknown-model", 0.5)

    def test_get_model_raises_propagates(self, all_mocks):
        all_mocks["llms"].return_value.get_model.side_effect = KeyError("model-not-found")
        graph = import_graph_module()

        with pytest.raises(KeyError):
            graph.build_agent("non-existent-model", 0.5)

    def test_assessment_raises_propagates(self, all_mocks):
        all_mocks["run_underwriting_assessment"].side_effect = RuntimeError("Assessment config missing")
        graph = import_graph_module()

        with pytest.raises(RuntimeError, match="Assessment config missing"):
            graph.build_agent("gpt-4o", 0.5, mode="deep")

    def test_create_agent_raises_propagates(self, all_mocks):
        all_mocks["create_agent"].side_effect = ConnectionError("Cannot connect to agent backend")
        graph = import_graph_module()

        with pytest.raises(ConnectionError, match="Cannot connect to agent backend"):
            graph.build_agent("gpt-4o", 0.5)

    def test_empty_model_name_passed_through(self, all_mocks):
        """build_agent should not validate model_name; that's LLMS's job."""
        graph = import_graph_module()
        graph.build_agent("", 0.5)
        all_mocks["llms"].return_value.get_model.assert_called_once_with("")

    def test_negative_temperature_passed_through(self, all_mocks):
        """build_agent does not validate temperature range; LLMS does."""
        graph = import_graph_module()
        graph.build_agent("gpt-4o", -0.1)
        all_mocks["llms"].assert_called_once_with(temperature=-0.1, streaming=True)

    def test_unexpected_mode_passed_through(self, all_mocks):
        """Unknown mode strings are forwarded without validation."""
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.5, mode="turbo")
        all_mocks["run_underwriting_assessment"].assert_called_once_with("turbo")


# ---------------------------------------------------------------------------
# build_agent — tool list composition
# ---------------------------------------------------------------------------

class TestBuildAgentToolList:

    def test_tools_list_has_three_elements(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.7)

        _, kwargs = all_mocks["create_agent"].call_args
        assert len(kwargs["tools"]) == 3

    def test_tools_list_contains_get_customer_profile(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.7)

        _, kwargs = all_mocks["create_agent"].call_args
        assert all_mocks["get_customer_profile"] in kwargs["tools"]

    def test_tools_list_contains_customer_lookalike(self, all_mocks):
        graph = import_graph_module()
        graph.build_agent("gpt-4o", 0.7)

        _, kwargs = all_mocks["create_agent"].call_args
        assert all_mocks["customer_lookalike"] in kwargs["tools"]

    def test_tools_list_contains_assessment_tool(self, all_mocks):
        graph = import_graph_module()
        fake_assessment_tool = all_mocks["run_underwriting_assessment"].return_value
        graph.build_agent("gpt-4o", 0.7)

        _, kwargs = all_mocks["create_agent"].call_args
        assert fake_assessment_tool in kwargs["tools"]

    def test_tools_order_is_stable(self, all_mocks):
        """Tool order must be deterministic for reproducible agent