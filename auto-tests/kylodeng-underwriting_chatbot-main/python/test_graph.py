"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path, parameter variations, edge cases, error conditions
- Module-level initialization: Redis client and checkpointer creation
- Mode parameter behavior ("fast", "deep", and invalid/boundary values)
- Tool assembly within build_agent()
- Error propagation from LLMS, create_agent, and tool factories

Mocks used:
- langchain.agents.create_agent (patched to avoid real LLM/graph construction)
- redis.asyncio.Redis (patched at module level to avoid real Redis connections)
- langgraph.checkpoint.redis.aio.AsyncRedisSaver (patched to avoid real Redis)
- modules.tools.get_customer_profile (patched)
- modules.tools.customer_lookalike (patched)
- modules.assessment._run_underwriting_assessment (patched)
- modules.LLMS.LLMS (patched to avoid real LLM instantiation)
- os.environ (patched for REDIS_HOST tests)

TODOs:
- TODO: Integration test with a real Redis instance running in test environment
- TODO: Test checkpointer is correctly threaded through to the agent (requires
        deeper LangGraph internals access)
- TODO: Test streaming behavior of the returned agent (requires LangChain agent
        invocation infrastructure)
- TODO: Test that the agent correctly uses the system prompt end-to-end
"""

import os
import pytest
from unittest.mock import MagicMock, patch, call, AsyncMock


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_redis_and_saver():
    """
    Patch Redis and AsyncRedisSaver at import-time level so that the module-level
    singletons (_redis_client, _checkpointer) are replaced before any test runs.
    These patches are applied for every test automatically.
    """
    with patch("backend.agent.graph.Redis") as mock_redis_cls, \
         patch("backend.agent.graph.AsyncRedisSaver") as mock_saver_cls:

        mock_redis_instance = MagicMock()
        mock_redis_cls.return_value = mock_redis_instance

        mock_saver_instance = MagicMock()
        mock_saver_cls.return_value = mock_saver_instance

        yield {
            "redis_cls": mock_redis_cls,
            "redis_instance": mock_redis_instance,
            "saver_cls": mock_saver_cls,
            "saver_instance": mock_saver_instance,
        }


@pytest.fixture()
def mock_llms():
    with patch("backend.agent.graph.LLMS") as mock_llms_cls:
        mock_model_instance = MagicMock()
        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.return_value = mock_model_instance
        mock_llms_cls.return_value = mock_llms_instance
        yield {
            "cls": mock_llms_cls,
            "instance": mock_llms_instance,
            "model": mock_model_instance,
        }


@pytest.fixture()
def mock_tools():
    with patch("backend.agent.graph.get_customer_profile") as mock_profile, \
         patch("backend.agent.graph.customer_lookalike") as mock_lookalike, \
         patch("backend.agent.graph._run_underwriting_assessment") as mock_assessment:

        mock_assessment_tool = MagicMock(name="assessment_tool")
        mock_assessment.return_value = mock_assessment_tool

        yield {
            "get_customer_profile": mock_profile,
            "customer_lookalike": mock_lookalike,
            "_run_underwriting_assessment": mock_assessment,
            "assessment_tool": mock_assessment_tool,
        }


@pytest.fixture()
def mock_create_agent():
    with patch("backend.agent.graph.create_agent") as mock_ca:
        mock_agent = MagicMock(name="agent")
        mock_ca.return_value = mock_agent
        yield mock_ca, mock_agent


# ---------------------------------------------------------------------------
# Tests: build_agent — happy path
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_object(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, expected_agent = mock_create_agent
        from backend.agent.graph import build_agent

        result = build_agent(model_name="gpt-4o", temperature=0.0)

        assert result is expected_agent

    def test_create_agent_called_once(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0)

        mock_ca.assert_called_once()

    def test_create_agent_receives_model(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.5)

        _, kwargs = mock_ca.call_args
        assert kwargs["model"] is mock_llms["model"]

    def test_create_agent_receives_system_prompt(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        with patch("backend.agent.graph.SYSTEM_PROMPT", "MOCKED_PROMPT"):
            build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = mock_ca.call_args
        assert kwargs["system_prompt"] == "MOCKED_PROMPT"

    def test_create_agent_receives_checkpointer(self, mock_llms, mock_tools, mock_create_agent,
                                                mock_redis_and_saver):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent, _checkpointer

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = mock_ca.call_args
        assert kwargs["checkpointer"] is _checkpointer

    def test_create_agent_receives_three_tools(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = mock_ca.call_args
        assert len(kwargs["tools"]) == 3

    def test_tools_list_contains_get_customer_profile(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = mock_ca.call_args
        assert mock_tools["get_customer_profile"] in kwargs["tools"]

    def test_tools_list_contains_customer_lookalike(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = mock_ca.call_args
        assert mock_tools["customer_lookalike"] in kwargs["tools"]

    def test_tools_list_contains_assessment_tool(self, mock_llms, mock_tools, mock_create_agent):
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0)

        _, kwargs = mock_ca.call_args
        assert mock_tools["assessment_tool"] in kwargs["tools"]


# ---------------------------------------------------------------------------
# Tests: build_agent — LLMS construction
# ---------------------------------------------------------------------------

class TestBuildAgentLLMSConstruction:

    def test_llms_instantiated_with_temperature_and_streaming(self, mock_llms, mock_tools,
                                                               mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.7)

        mock_llms["cls"].assert_called_once_with(temperature=0.7, streaming=True)

    def test_get_model_called_with_model_name(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="claude-3-5-sonnet", temperature=0.0)

        mock_llms["instance"].get_model.assert_called_once_with("claude-3-5-sonnet")

    @pytest.mark.parametrize("model_name,temperature", [
        ("gpt-4o", 0.0),
        ("gpt-4o", 1.0),
        ("claude-3-5-sonnet", 0.5),
        ("gemini-pro", 0.9),
        ("gpt-3.5-turbo", 0.3),
    ])
    def test_various_model_name_temperature_combinations(self, model_name, temperature,
                                                          mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        result = build_agent(model_name=model_name, temperature=temperature)

        mock_llms["cls"].assert_called_with(temperature=temperature, streaming=True)
        mock_llms["instance"].get_model.assert_called_with(model_name)
        assert result is mock_create_agent[1]


# ---------------------------------------------------------------------------
# Tests: build_agent — mode parameter
# ---------------------------------------------------------------------------

class TestBuildAgentModeParameter:

    def test_default_mode_is_fast(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0)

        mock_tools["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_fast_mode_explicit(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0, mode="fast")

        mock_tools["_run_underwriting_assessment"].assert_called_once_with("fast")

    def test_deep_mode(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0, mode="deep")

        mock_tools["_run_underwriting_assessment"].assert_called_once_with("deep")

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_valid_modes_accepted(self, mode, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        result = build_agent(model_name="gpt-4o", temperature=0.0, mode=mode)

        mock_tools["_run_underwriting_assessment"].assert_called_with(mode)
        assert result is mock_create_agent[1]

    def test_mode_passed_to_assessment_factory_not_to_create_agent(self, mock_llms, mock_tools,
                                                                     mock_create_agent):
        """mode should configure the tool factory, not be forwarded to create_agent."""
        mock_ca, _ = mock_create_agent
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0, mode="deep")

        _, kwargs = mock_ca.call_args
        assert "mode" not in kwargs

    @pytest.mark.parametrize("invalid_mode", ["", "medium", "FAST", "DEEP", None, 123])
    def test_invalid_mode_still_forwards_to_assessment_factory(self, invalid_mode, mock_llms,
                                                                mock_tools, mock_create_agent):
        """
        build_agent itself does not validate the mode string; it forwards whatever
        is passed to _run_underwriting_assessment. Validation (if any) happens inside
        that factory. We verify the forwarding contract.
        """
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0, mode=invalid_mode)

        mock_tools["_run_underwriting_assessment"].assert_called_once_with(invalid_mode)


# ---------------------------------------------------------------------------
# Tests: build_agent — boundary / edge values
# ---------------------------------------------------------------------------

class TestBuildAgentBoundaryValues:

    def test_temperature_zero(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=0.0)

        mock_llms["cls"].assert_called_once_with(temperature=0.0, streaming=True)

    def test_temperature_one(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=1.0)

        mock_llms["cls"].assert_called_once_with(temperature=1.0, streaming=True)

    def test_temperature_two(self, mock_llms, mock_tools, mock_create_agent):
        """Some LLM providers allow temperature up to 2.0."""
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=2.0)

        mock_llms["cls"].assert_called_once_with(temperature=2.0, streaming=True)

    def test_temperature_negative_forwarded(self, mock_llms, mock_tools, mock_create_agent):
        """
        build_agent does not validate temperature; negative values should be forwarded.
        LLMS or the underlying provider will raise an error — not build_agent.
        """
        from backend.agent.graph import build_agent

        build_agent(model_name="gpt-4o", temperature=-0.1)

        mock_llms["cls"].assert_called_once_with(temperature=-0.1, streaming=True)

    def test_empty_model_name_forwarded(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="", temperature=0.0)

        mock_llms["instance"].get_model.assert_called_once_with("")

    def test_model_name_with_whitespace(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        build_agent(model_name="  gpt-4o  ", temperature=0.0)

        mock_llms["instance"].get_model.assert_called_once_with("  gpt-4o  ")


# ---------------------------------------------------------------------------
# Tests: build_agent — error conditions
# ---------------------------------------------------------------------------

class TestBuildAgentErrorConditions:

    def test_llms_raises_propagates(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        mock_llms["cls"].side_effect = ValueError("Unsupported model configuration")

        with pytest.raises(ValueError, match="Unsupported model configuration"):
            build_agent(model_name="gpt-4o", temperature=0.0)

    def test_get_model_raises_propagates(self, mock_llms, mock_tools, mock_create_agent):
        from backend.agent.graph import build_agent

        mock_llms["instance"].get_model.side_effect = KeyError("unknown-model")

        with pytest.raises(KeyError):
            build_agent