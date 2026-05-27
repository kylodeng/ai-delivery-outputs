"""
Test module for backend/agent/graph.py

What is tested:
- build_agent() function: happy path with valid model/temperature/mode combinations,
  edge cases for mode parameter, boundary values for temperature, error conditions
  when dependencies raise exceptions.

Mocks used:
- backend.agent.graph.LLMS                        (avoid real LLM instantiation)
- backend.agent.graph.create_agent                (avoid real agent creation)
- backend.agent.graph.get_customer_profile        (tool stub)
- backend.agent.graph.customer_lookalike          (tool stub)
- backend.agent.graph._run_underwriting_assessment (avoid real assessment logic)
- backend.agent.graph._redis_client               (avoid real Redis connection)
- backend.agent.graph._checkpointer               (avoid real Redis saver)
- os.environ                                      (control REDIS_HOST)

TODOs:
- TODO: Integration test for full agent invocation requires a running Redis instance
        and real LLM credentials — stub provided below.
- TODO: Test that the agent correctly uses the checkpointer for memory persistence
        across calls — requires LangGraph internals inspection.
- TODO: Test SYSTEM_PROMPT content injection into the agent — requires access to
        prompt module and deeper agent inspection.
- TODO: Test async behaviour of AsyncRedisSaver setup if exposed as public API.
"""

import importlib
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_module_cache():
    """
    Ensure graph module is re-imported fresh for tests that need to inspect
    module-level side effects.  For most tests we just patch at function level.
    """
    yield
    # Remove cached module so next test gets a clean slate if needed.
    for key in list(sys.modules.keys()):
        if "backend.agent.graph" in key or key == "agent.graph":
            del sys.modules[key]


def _make_graph_module_patches():
    """
    Return a dict of patch targets that must be active when the module is
    first imported (module-level side effects: Redis, AsyncRedisSaver).
    """
    return {
        "redis.asyncio.Redis": MagicMock(return_value=MagicMock()),
        "langgraph.checkpoint.redis.aio.AsyncRedisSaver": MagicMock(return_value=MagicMock()),
    }


# ---------------------------------------------------------------------------
# Unit tests for build_agent()
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:
    """Happy-path tests for build_agent()."""

    def _call_build_agent(self, model_name, temperature, mode=None):
        """Helper: patch all external deps and call build_agent."""
        mock_model = MagicMock(name="mock_model")
        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.return_value = mock_model

        mock_assessment_tool = MagicMock(name="assessment_tool")
        mock_agent = MagicMock(name="created_agent")

        with patch("backend.agent.graph.LLMS", return_value=mock_llms_instance) as mock_llms_cls, \
             patch("backend.agent.graph.create_agent", return_value=mock_agent) as mock_create, \
             patch("backend.agent.graph._run_underwriting_assessment",
                   return_value=mock_assessment_tool) as mock_assessment, \
             patch("backend.agent.graph.get_customer_profile",
                   new=MagicMock(name="get_customer_profile")) as mock_profile, \
             patch("backend.agent.graph.customer_lookalike",
                   new=MagicMock(name="customer_lookalike")) as mock_lookalike, \
             patch("backend.agent.graph._checkpointer", new=MagicMock()):

            from backend.agent.graph import build_agent, SYSTEM_PROMPT

            kwargs = {"model_name": model_name, "temperature": temperature}
            if mode is not None:
                kwargs["mode"] = mode

            result = build_agent(**kwargs)

            return {
                "result": result,
                "mock_llms_cls": mock_llms_cls,
                "mock_llms_instance": mock_llms_instance,
                "mock_create": mock_create,
                "mock_assessment": mock_assessment,
                "mock_profile": mock_profile,
                "mock_lookalike": mock_lookalike,
                "mock_agent": mock_agent,
                "mock_assessment_tool": mock_assessment_tool,
                "system_prompt": SYSTEM_PROMPT,
            }

    def test_returns_agent_object(self):
        ctx = self._call_build_agent("gpt-4o", 0.7)
        assert ctx["result"] is ctx["mock_agent"]

    def test_llms_instantiated_with_correct_temperature(self):
        ctx = self._call_build_agent("gpt-4o", 0.5)
        ctx["mock_llms_cls"].assert_called_once_with(temperature=0.5, streaming=True)

    def test_llms_get_model_called_with_model_name(self):
        ctx = self._call_build_agent("gpt-4o", 0.7)
        ctx["mock_llms_instance"].get_model.assert_called_once_with("gpt-4o")

    def test_create_agent_called_with_model(self):
        ctx = self._call_build_agent("gpt-4o", 0.7)
        call_kwargs = ctx["mock_create"].call_args.kwargs
        assert call_kwargs["model"] is ctx["mock_llms_instance"].get_model.return_value

    def test_create_agent_called_with_system_prompt(self):
        ctx = self._call_build_agent("gpt-4o", 0.7)
        call_kwargs = ctx["mock_create"].call_args.kwargs
        assert call_kwargs["system_prompt"] == ctx["system_prompt"]

    def test_create_agent_called_with_checkpointer(self):
        ctx = self._call_build_agent("gpt-4o", 0.7)
        call_kwargs = ctx["mock_create"].call_args.kwargs
        # checkpointer must be present and truthy (not None)
        assert "checkpointer" in call_kwargs
        assert call_kwargs["checkpointer"] is not None

    def test_create_agent_receives_three_tools(self):
        ctx = self._call_build_agent("gpt-4o", 0.7)
        call_kwargs = ctx["mock_create"].call_args.kwargs
        assert len(call_kwargs["tools"]) == 3

    def test_tools_list_contains_assessment_tool(self):
        ctx = self._call_build_agent("gpt-4o", 0.7)
        call_kwargs = ctx["mock_create"].call_args.kwargs
        assert ctx["mock_assessment_tool"] in call_kwargs["tools"]

    def test_default_mode_is_fast(self):
        """When mode is not supplied, _run_underwriting_assessment should be called with 'fast'."""
        ctx = self._call_build_agent("gpt-4o", 0.7)
        ctx["mock_assessment"].assert_called_once_with("fast")

    def test_mode_fast_passed_to_assessment(self):
        ctx = self._call_build_agent("gpt-4o", 0.7, mode="fast")
        ctx["mock_assessment"].assert_called_once_with("fast")

    def test_mode_deep_passed_to_assessment(self):
        ctx = self._call_build_agent("gpt-4o", 0.7, mode="deep")
        ctx["mock_assessment"].assert_called_once_with("deep")

    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-3.5-turbo",
        "claude-3-opus",
        "gemini-pro",
    ])
    def test_various_model_names(self, model_name):
        ctx = self._call_build_agent(model_name, 0.5)
        ctx["mock_llms_instance"].get_model.assert_called_once_with(model_name)

    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_boundary_temperatures(self, temperature):
        ctx = self._call_build_agent("gpt-4o", temperature)
        ctx["mock_llms_cls"].assert_called_once_with(temperature=temperature, streaming=True)


class TestBuildAgentEdgeCases:
    """Edge-case and boundary-value tests for build_agent()."""

    def _patched_build_agent(self, model_name, temperature, mode="fast",
                              llms_side_effect=None,
                              create_agent_side_effect=None,
                              assessment_side_effect=None):
        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.return_value = MagicMock(name="model")
        mock_assessment_tool = MagicMock(name="assessment_tool")

        llms_mock = MagicMock(return_value=mock_llms_instance)
        if llms_side_effect:
            llms_mock.side_effect = llms_side_effect

        assessment_mock = MagicMock(return_value=mock_assessment_tool)
        if assessment_side_effect:
            assessment_mock.side_effect = assessment_side_effect

        create_mock = MagicMock(return_value=MagicMock(name="agent"))
        if create_agent_side_effect:
            create_mock.side_effect = create_agent_side_effect

        with patch("backend.agent.graph.LLMS", llms_mock), \
             patch("backend.agent.graph.create_agent", create_mock), \
             patch("backend.agent.graph._run_underwriting_assessment", assessment_mock), \
             patch("backend.agent.graph.get_customer_profile", MagicMock()), \
             patch("backend.agent.graph.customer_lookalike", MagicMock()), \
             patch("backend.agent.graph._checkpointer", MagicMock()):
            from backend.agent.graph import build_agent
            return build_agent(model_name=model_name, temperature=temperature, mode=mode)

    def test_temperature_zero(self):
        """Temperature of 0.0 (deterministic) should not raise."""
        result = self._patched_build_agent("gpt-4o", 0.0)
        assert result is not None

    def test_temperature_one(self):
        """Temperature of 1.0 (maximum standard) should not raise."""
        result = self._patched_build_agent("gpt-4o", 1.0)
        assert result is not None

    def test_temperature_negative_passed_through(self):
        """
        build_agent itself does not validate temperature — it delegates to LLMS.
        Ensure the value is forwarded as-is.
        """
        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.return_value = MagicMock()
        llms_cls = MagicMock(return_value=mock_llms_instance)

        with patch("backend.agent.graph.LLMS", llms_cls), \
             patch("backend.agent.graph.create_agent", MagicMock(return_value=MagicMock())), \
             patch("backend.agent.graph._run_underwriting_assessment", MagicMock(return_value=MagicMock())), \
             patch("backend.agent.graph.get_customer_profile", MagicMock()), \
             patch("backend.agent.graph.customer_lookalike", MagicMock()), \
             patch("backend.agent.graph._checkpointer", MagicMock()):
            from backend.agent.graph import build_agent
            build_agent(model_name="gpt-4o", temperature=-0.1)
            llms_cls.assert_called_once_with(temperature=-0.1, streaming=True)

    def test_empty_string_model_name_passed_through(self):
        """Empty model name should be forwarded to get_model without mutation."""
        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.return_value = MagicMock()
        llms_cls = MagicMock(return_value=mock_llms_instance)

        with patch("backend.agent.graph.LLMS", llms_cls), \
             patch("backend.agent.graph.create_agent", MagicMock(return_value=MagicMock())), \
             patch("backend.agent.graph._run_underwriting_assessment", MagicMock(return_value=MagicMock())), \
             patch("backend.agent.graph.get_customer_profile", MagicMock()), \
             patch("backend.agent.graph.customer_lookalike", MagicMock()), \
             patch("backend.agent.graph._checkpointer", MagicMock()):
            from backend.agent.graph import build_agent
            build_agent(model_name="", temperature=0.5)
            mock_llms_instance.get_model.assert_called_once_with("")

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_supported_modes(self, mode):
        result = self._patched_build_agent("gpt-4o", 0.7, mode=mode)
        assert result is not None

    def test_unknown_mode_forwarded_to_assessment(self):
        """
        build_agent does not guard unknown mode values — they are forwarded.
        Validation (if any) is the responsibility of _run_underwriting_assessment.
        """
        assessment_mock = MagicMock(return_value=MagicMock())
        mock_llms_instance = MagicMock()
        mock_llms_instance.get_model.return_value = MagicMock()

        with patch("backend.agent.graph.LLMS", MagicMock(return_value=mock_llms_instance)), \
             patch("backend.agent.graph.create_agent", MagicMock(return_value=MagicMock())), \
             patch("backend.agent.graph._run_underwriting_assessment", assessment_mock), \
             patch("backend.agent.graph.get_customer_profile", MagicMock()), \
             patch("backend.agent.graph.customer_lookalike", MagicMock()), \
             patch("backend.agent.graph._checkpointer", MagicMock()):
            from backend.agent.graph import build_agent
            build_agent(model_name="gpt-4o", temperature=0.7, mode="unknown_mode")
            assessment_mock.assert_called_once_with("unknown_mode")


class TestBuildAgentErrorConditions:
    """Test error propagation when dependencies raise exceptions."""

    def test_llms_instantiation_raises_propagates(self):
        with patch("backend.agent.graph.LLMS", side_effect=ValueError("Invalid model config")), \
             patch("backend.agent.graph.create_agent", MagicMock()), \
             patch("backend.agent.graph._run_underwriting_assessment", MagicMock(return_value=MagicMock())), \
             patch("backend.agent.graph.get_customer_profile", MagicMock()), \
             patch("backend.agent.graph.customer_lookalike", MagicMock()), \
             patch("backend.agent.graph._checkpointer", MagicMock()):
            from backend.agent.graph import build_agent
            with pytest.raises(ValueError, match="Invalid model config"):
                build_agent(model_name="bad-model", temperature=0.7)

    def test_get_model_raises_propagates(self):
        mock_llms_instance =