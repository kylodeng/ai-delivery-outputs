"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path with various model_name/temperature/mode combinations,
  edge cases (boundary temperatures, unknown mode strings), error conditions
  (invalid model, LLMS errors, create_agent errors).

Mocks used:
- backend.agent.graph.LLMS              — prevents real LLM instantiation
- backend.agent.graph.create_agent      — prevents real agent construction
- backend.agent.graph.get_customer_profile   — tool stub
- backend.agent.graph.customer_lookalike     — tool stub
- backend.agent.graph._run_underwriting_assessment — prevents real assessment call
- backend.agent.graph._redis_client     — not exercised directly but module-level
  Redis creation is patched at import time via os.environ
- backend.agent.graph._checkpointer     — patched to avoid real Redis connections

TODOs:
- TODO: integration test that verifies the agent actually processes a message end-to-end
        (requires a running Redis instance and valid LLM credentials).
- TODO: test that the checkpointer is correctly wired to the returned agent
        (needs LangGraph internals exposed or an integration environment).
- TODO: test Redis reconnection / failure handling at module import time.
"""

import os
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# We patch at the *graph* module's namespace so that already-imported names are
# replaced correctly.
MODULE = "backend.agent.graph"


def _make_mock_model():
    return MagicMock(name="mock_llm_model")


def _make_mock_agent():
    return MagicMock(name="mock_agent")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_redis_env(monkeypatch):
    """Ensure REDIS_HOST is set to a safe value before any import side-effects."""
    monkeypatch.setenv("REDIS_HOST", "localhost")


@pytest.fixture()
def mock_llms_class():
    with patch(f"{MODULE}.LLMS") as mock_cls:
        mock_instance = MagicMock(name="llms_instance")
        mock_model = _make_mock_model()
        mock_instance.get_model.return_value = mock_model
        mock_cls.return_value = mock_instance
        yield mock_cls, mock_instance, mock_model


@pytest.fixture()
def mock_create_agent():
    with patch(f"{MODULE}.create_agent") as mock_ca:
        mock_ca.return_value = _make_mock_agent()
        yield mock_ca


@pytest.fixture()
def mock_tools():
    """Patch every tool so build_agent doesn't need real implementations."""
    with patch(f"{MODULE}.get_customer_profile", new=MagicMock(name="get_customer_profile")) as mock_gcp, \
         patch(f"{MODULE}.customer_lookalike", new=MagicMock(name="customer_lookalike")) as mock_cl, \
         patch(f"{MODULE}._run_underwriting_assessment") as mock_rua:
        mock_rua.return_value = MagicMock(name="assessment_tool")
        yield mock_gcp, mock_cl, mock_rua


@pytest.fixture()
def mock_checkpointer():
    with patch(f"{MODULE}._checkpointer", new=MagicMock(name="mock_checkpointer")) as mock_cp:
        yield mock_cp


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_object(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """build_agent should return whatever create_agent returns."""
        from backend.agent.graph import build_agent

        agent = build_agent(model_name="gpt-4o", temperature=0.5)

        assert agent is mock_create_agent.return_value

    def test_llms_instantiated_with_correct_params(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """LLMS should be constructed with the provided temperature and streaming=True."""
        from backend.agent.graph import build_agent

        mock_cls, mock_instance, _ = mock_llms_class
        build_agent(model_name="gpt-4o", temperature=0.7)

        mock_cls.assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """LLMS.get_model should be called with the supplied model_name."""
        from backend.agent.graph import build_agent

        _, mock_instance, _ = mock_llms_class
        build_agent(model_name="gpt-4o-mini", temperature=0.3)

        mock_instance.get_model.assert_called_once_with("gpt-4o-mini")

    def test_create_agent_receives_correct_model(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """create_agent must receive the model returned by LLMS.get_model."""
        from backend.agent.graph import build_agent

        _, _, mock_model = mock_llms_class
        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_create_agent.call_args
        assert kwargs["model"] is mock_model

    def test_create_agent_receives_system_prompt(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """create_agent must receive SYSTEM_PROMPT as system_prompt."""
        from backend.agent.graph import build_agent
        from backend.agent.graph import SYSTEM_PROMPT as EXPECTED_PROMPT

        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_create_agent.call_args
        assert kwargs["system_prompt"] == EXPECTED_PROMPT

    def test_create_agent_receives_checkpointer(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """create_agent must receive the module-level _checkpointer."""
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_create_agent.call_args
        assert kwargs["checkpointer"] is mock_checkpointer

    def test_create_agent_receives_three_tools(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """The tools list passed to create_agent must contain exactly 3 items."""
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_create_agent.call_args
        assert len(kwargs["tools"]) == 3

    def test_tools_include_get_customer_profile(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """get_customer_profile must be present in the tools list."""
        from backend.agent.graph import build_agent, get_customer_profile

        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_create_agent.call_args
        assert get_customer_profile in kwargs["tools"]

    def test_tools_include_customer_lookalike(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """customer_lookalike must be present in the tools list."""
        from backend.agent.graph import build_agent, customer_lookalike

        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_create_agent.call_args
        assert customer_lookalike in kwargs["tools"]

    def test_tools_include_assessment_tool(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """The result of _run_underwriting_assessment(mode) must be in the tools list."""
        from backend.agent.graph import build_agent

        mock_gcp, mock_cl, mock_rua = mock_tools
        expected_tool = mock_rua.return_value

        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_create_agent.call_args
        assert expected_tool in kwargs["tools"]

    # --- Mode parameter ---

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_mode_passed_to_underwriting_assessment(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer, mode
    ):
        """_run_underwriting_assessment must be called with the mode argument."""
        from backend.agent.graph import build_agent

        mock_gcp, mock_cl, mock_rua = mock_tools
        build_agent(model_name="gpt-4o", temperature=0.5, mode=mode)

        mock_rua.assert_called_once_with(mode)

    def test_default_mode_is_fast(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """When mode is omitted, _run_underwriting_assessment should be called with 'fast'."""
        from backend.agent.graph import build_agent

        mock_gcp, mock_cl, mock_rua = mock_tools
        build_agent(model_name="gpt-4o", temperature=0.5)

        mock_rua.assert_called_once_with("fast")


# ---------------------------------------------------------------------------
# Parameterised inputs from synthetic data
# ---------------------------------------------------------------------------

class TestBuildAgentParameterised:

    @pytest.mark.parametrize("model_name,temperature,mode", [
        ("gpt-4o", 0.0, "fast"),
        ("gpt-4o-mini", 0.5, "fast"),
        ("gpt-4o", 1.0, "deep"),
        ("gpt-4o-mini", 0.7, "deep"),
        ("claude-3-5-sonnet", 0.3, "fast"),
    ])
    def test_various_model_temperature_mode_combinations(
        self,
        mock_llms_class,
        mock_create_agent,
        mock_tools,
        mock_checkpointer,
        model_name,
        temperature,
        mode,
    ):
        """build_agent should succeed for a variety of model/temperature/mode combos."""
        from backend.agent.graph import build_agent

        mock_cls, mock_instance, mock_model = mock_llms_class
        agent = build_agent(model_name=model_name, temperature=temperature, mode=mode)

        mock_cls.assert_called_once_with(temperature=temperature, streaming=True)
        mock_instance.get_model.assert_called_once_with(model_name)
        assert agent is not None


# ---------------------------------------------------------------------------
# Edge-case / boundary-value tests
# ---------------------------------------------------------------------------

class TestBuildAgentEdgeCases:

    def test_temperature_zero(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """Temperature of 0.0 (minimum) should be forwarded unchanged."""
        from backend.agent.graph import build_agent

        mock_cls, _, _ = mock_llms_class
        build_agent(model_name="gpt-4o", temperature=0.0)

        mock_cls.assert_called_once_with(temperature=0.0, streaming=True)

    def test_temperature_one(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """Temperature of 1.0 (maximum typical) should be forwarded unchanged."""
        from backend.agent.graph import build_agent

        mock_cls, _, _ = mock_llms_class
        build_agent(model_name="gpt-4o", temperature=1.0)

        mock_cls.assert_called_once_with(temperature=1.0, streaming=True)

    def test_temperature_above_one(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """Temperature values above 1.0 should still be forwarded — validation is the model's job."""
        from backend.agent.graph import build_agent

        mock_cls, _, _ = mock_llms_class
        build_agent(model_name="gpt-4o", temperature=2.0)

        mock_cls.assert_called_once_with(temperature=2.0, streaming=True)

    def test_unknown_mode_still_forwarded(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """An unrecognised mode string must still be forwarded to _run_underwriting_assessment."""
        from backend.agent.graph import build_agent

        mock_gcp, mock_cl, mock_rua = mock_tools
        build_agent(model_name="gpt-4o", temperature=0.5, mode="turbo")

        mock_rua.assert_called_once_with("turbo")

    def test_empty_string_model_name(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """An empty model name should be forwarded without modification."""
        from backend.agent.graph import build_agent

        _, mock_instance, _ = mock_llms_class
        build_agent(model_name="", temperature=0.5)

        mock_instance.get_model.assert_called_once_with("")

    def test_build_agent_called_twice_creates_two_agents(
        self, mock_llms_class, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """build_agent should be stateless — calling it twice should produce two agents."""
        from backend.agent.graph import build_agent

        agent1 = build_agent(model_name="gpt-4o", temperature=0.5)
        agent2 = build_agent(model_name="gpt-4o", temperature=0.5)

        assert mock_create_agent.call_count == 2


# ---------------------------------------------------------------------------
# Error / failure conditions
# ---------------------------------------------------------------------------

class TestBuildAgentErrorConditions:

    def test_llms_raises_propagates(
        self, mock_create_agent, mock_tools, mock_checkpointer
    ):
        """If LLMS() raises, build_agent must propagate the exception."""
        from backend.agent.graph import build_agent

        with patch(f"{MODULE}.LLMS", side_effect=RuntimeError("LLM init failed")):
            with pytest.raises(RuntimeError, match="LLM init failed"):
                build_agent(model_name="gpt-4o", temperature=0.5)

    def test_get_model_raises_propagates(
        self, mock_tools, mock_checkpointer
    ):
        """If LLMS.get_model() raises, build_agent must propagate the exception."""
        from backend.agent.graph import build_agent

        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.side_effect = ValueError("Unknown model name")

        with patch(f"{MODULE}.LLMS", return_value=mock_llms_instance):
            with patch(f"{MODULE}.create_agent"):
                with pytest.raises(ValueError, match="Unknown model name"):
                    build_agent(model_name="nonexistent-model", temperature=0.5)

    def test_create_agent_raises_propagates(
        self, mock_llms_class, mock_tools, mock_checkpointer
    ):
        """If create_agent