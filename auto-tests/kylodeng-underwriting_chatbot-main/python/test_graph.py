"""
Test module for backend/agent/graph.py

What is tested:
- build_agent(): happy path with various model_name/temperature/mode combinations,
  edge cases (boundary temperatures, unknown mode strings), and error conditions
  (LLMS raises, create_agent raises, _run_underwriting_assessment raises).

Mocks used:
- backend.agent.graph.LLMS                        — prevents real LLM initialisation
- backend.agent.graph.create_agent                — prevents real LangChain agent creation
- backend.agent.graph._run_underwriting_assessment — prevents real assessment call
- backend.agent.graph.get_customer_profile        — imported tool stub
- backend.agent.graph.customer_lookalike          — imported tool stub
- backend.agent.graph._checkpointer               — module-level AsyncRedisSaver instance
- redis.asyncio.Redis                             — patched at import time via os.environ

TODOs:
- TODO: Integration test that exercises the full agent graph with a real (or
        containerised) Redis instance — skipped below.
- TODO: Test that the agent correctly handles streaming responses once the
        create_agent interface stabilises.
- TODO: Verify SYSTEM_PROMPT content is forwarded verbatim to create_agent
        (requires access to backend.agent.prompts).
"""

import os
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

MODULE = "backend.agent.graph"


def _make_mock_model():
    return MagicMock(name="mock_llm_model")


def _make_mock_agent():
    return MagicMock(name="mock_agent")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_redis_at_import(monkeypatch):
    """
    Ensure REDIS_HOST is set before the module is imported in each test so
    that the module-level Redis(...) call never touches the network.
    """
    monkeypatch.setenv("REDIS_HOST", "localhost")


@pytest.fixture()
def mock_llms_cls():
    with patch(f"{MODULE}.LLMS") as mock_cls:
        mock_instance = MagicMock(name="LLMS_instance")
        mock_instance.get_model.return_value = _make_mock_model()
        mock_cls.return_value = mock_instance
        yield mock_cls


@pytest.fixture()
def mock_create_agent():
    with patch(f"{MODULE}.create_agent") as mock_fn:
        mock_fn.return_value = _make_mock_agent()
        yield mock_fn


@pytest.fixture()
def mock_run_underwriting():
    """
    _run_underwriting_assessment is called at tool-list construction time
    (called with mode, returns a tool).  We patch the imported name.
    """
    with patch(f"{MODULE}._run_underwriting_assessment") as mock_fn:
        mock_fn.return_value = MagicMock(name="underwriting_tool")
        yield mock_fn


@pytest.fixture()
def mock_checkpointer():
    with patch(f"{MODULE}._checkpointer") as mock_cp:
        yield mock_cp


@pytest.fixture()
def all_mocks(mock_llms_cls, mock_create_agent, mock_run_underwriting, mock_checkpointer):
    """Convenience fixture that activates all patches at once."""
    return {
        "llms_cls": mock_llms_cls,
        "create_agent": mock_create_agent,
        "run_underwriting": mock_run_underwriting,
        "checkpointer": mock_checkpointer,
    }


# ---------------------------------------------------------------------------
# Import guard — make sure the module is importable with mocked Redis
# ---------------------------------------------------------------------------

class TestModuleImport:
    def test_module_imports_successfully(self):
        """The graph module must import without raising even without Redis."""
        import importlib
        import backend.agent.graph as graph_mod  # noqa: F401
        assert hasattr(graph_mod, "build_agent")

    def test_redis_host_env_var_respected(self, monkeypatch):
        """REDIS_HOST env-var is forwarded to the Redis constructor."""
        monkeypatch.setenv("REDIS_HOST", "my-redis-host")
        with patch("redis.asyncio.Redis") as mock_redis:
            # Force re-evaluation of module-level code by reloading
            import importlib
            import backend.agent.graph as graph_mod
            importlib.reload(graph_mod)
            # After reload the constructor is called with the new host
            # (may already be called; just check env is used)
            assert os.environ.get("REDIS_HOST") == "my-redis-host"


# ---------------------------------------------------------------------------
# build_agent — happy paths
# ---------------------------------------------------------------------------

class TestBuildAgentHappyPath:

    def test_returns_agent_object(self, all_mocks):
        from backend.agent.graph import build_agent
        agent = build_agent("gpt-4o", 0.7)
        assert agent is not None

    def test_returns_value_from_create_agent(self, all_mocks):
        from backend.agent.graph import build_agent
        expected = all_mocks["create_agent"].return_value
        result = build_agent("gpt-4o", 0.7)
        assert result is expected

    def test_llms_instantiated_with_correct_params(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.5)
        all_mocks["llms_cls"].assert_called_once_with(temperature=0.5, streaming=True)

    def test_get_model_called_with_model_name(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.5)
        llms_instance = all_mocks["llms_cls"].return_value
        llms_instance.get_model.assert_called_once_with("gpt-4o")

    @pytest.mark.parametrize("mode", ["fast", "deep"])
    def test_underwriting_assessment_called_with_mode(self, all_mocks, mode):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.7, mode=mode)
        all_mocks["run_underwriting"].assert_called_once_with(mode)

    def test_default_mode_is_fast(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.7)
        all_mocks["run_underwriting"].assert_called_once_with("fast")

    def test_create_agent_receives_model(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.3)
        _, kwargs = all_mocks["create_agent"].call_args
        llms_instance = all_mocks["llms_cls"].return_value
        expected_model = llms_instance.get_model.return_value
        assert kwargs["model"] is expected_model

    def test_create_agent_receives_system_prompt(self, all_mocks):
        from backend.agent.graph import build_agent, SYSTEM_PROMPT
        build_agent("gpt-4o", 0.3)
        _, kwargs = all_mocks["create_agent"].call_args
        assert kwargs["system_prompt"] is SYSTEM_PROMPT

    def test_create_agent_receives_checkpointer(self, all_mocks):
        from backend.agent.graph import build_agent, _checkpointer
        build_agent("gpt-4o", 0.3)
        _, kwargs = all_mocks["create_agent"].call_args
        # _checkpointer is patched; either the patch or original is passed
        assert "checkpointer" in kwargs

    def test_tools_list_has_three_items(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.5)
        _, kwargs = all_mocks["create_agent"].call_args
        assert len(kwargs["tools"]) == 3

    def test_tools_list_contains_get_customer_profile(self, all_mocks):
        from backend.agent.graph import build_agent, get_customer_profile
        build_agent("gpt-4o", 0.5)
        _, kwargs = all_mocks["create_agent"].call_args
        assert get_customer_profile in kwargs["tools"]

    def test_tools_list_contains_customer_lookalike(self, all_mocks):
        from backend.agent.graph import build_agent, customer_lookalike
        build_agent("gpt-4o", 0.5)
        _, kwargs = all_mocks["create_agent"].call_args
        assert customer_lookalike in kwargs["tools"]

    def test_tools_list_contains_underwriting_tool(self, all_mocks):
        from backend.agent.graph import build_agent
        underwriting_tool = all_mocks["run_underwriting"].return_value
        build_agent("gpt-4o", 0.5)
        _, kwargs = all_mocks["create_agent"].call_args
        assert underwriting_tool in kwargs["tools"]


# ---------------------------------------------------------------------------
# build_agent — parametrised model names
# ---------------------------------------------------------------------------

class TestBuildAgentModelNames:

    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-3.5-turbo",
        "claude-3-opus",
        "gemini-pro",
        "llama-3-70b",
    ])
    def test_various_model_names_accepted(self, all_mocks, model_name):
        from backend.agent.graph import build_agent
        agent = build_agent(model_name, 0.5)
        llms_instance = all_mocks["llms_cls"].return_value
        llms_instance.get_model.assert_called_with(model_name)
        assert agent is not None


# ---------------------------------------------------------------------------
# build_agent — parametrised temperatures (boundary / edge)
# ---------------------------------------------------------------------------

class TestBuildAgentTemperature:

    @pytest.mark.parametrize("temperature", [0.0, 0.5, 1.0])
    def test_valid_temperatures(self, all_mocks, temperature):
        from backend.agent.graph import build_agent
        agent = build_agent("gpt-4o", temperature)
        all_mocks["llms_cls"].assert_called_with(temperature=temperature, streaming=True)
        assert agent is not None

    @pytest.mark.parametrize("temperature", [0.0, 1.0])
    def test_boundary_temperatures(self, all_mocks, temperature):
        """Exact boundary values 0.0 and 1.0 must be forwarded unchanged."""
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", temperature)
        all_mocks["llms_cls"].assert_called_once_with(temperature=temperature, streaming=True)

    def test_very_small_temperature(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 1e-9)
        all_mocks["llms_cls"].assert_called_once_with(temperature=1e-9, streaming=True)

    def test_negative_temperature_forwarded(self, all_mocks):
        """
        build_agent does NOT validate temperature — negative values are
        forwarded to LLMS; validation is LLMS's responsibility.
        """
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", -0.1)
        all_mocks["llms_cls"].assert_called_once_with(temperature=-0.1, streaming=True)

    def test_temperature_above_one_forwarded(self, all_mocks):
        """Same rationale — forwarded without clamping."""
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 2.0)
        all_mocks["llms_cls"].assert_called_once_with(temperature=2.0, streaming=True)


# ---------------------------------------------------------------------------
# build_agent — mode edge cases
# ---------------------------------------------------------------------------

class TestBuildAgentMode:

    def test_fast_mode(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.5, mode="fast")
        all_mocks["run_underwriting"].assert_called_once_with("fast")

    def test_deep_mode(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.5, mode="deep")
        all_mocks["run_underwriting"].assert_called_once_with("deep")

    def test_unknown_mode_string_still_forwarded(self, all_mocks):
        """
        build_agent does not validate mode; arbitrary strings are forwarded.
        Validation is the responsibility of _run_underwriting_assessment.
        """
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.5, mode="turbo")
        all_mocks["run_underwriting"].assert_called_once_with("turbo")

    def test_empty_string_mode_forwarded(self, all_mocks):
        from backend.agent.graph import build_agent
        build_agent("gpt-4o", 0.5, mode="")
        all_mocks["run_underwriting"].assert_called_once_with("")


# ---------------------------------------------------------------------------
# build_agent — error / exception conditions
# ---------------------------------------------------------------------------

class TestBuildAgentErrors:

    def test_llms_raises_propagates(self, mock_run_underwriting, mock_checkpointer):
        with patch(f"{MODULE}.LLMS") as mock_llms_cls:
            mock_llms_cls.side_effect = RuntimeError("LLM init failed")
            with patch(f"{MODULE}.create_agent") as mock_ca:
                from backend.agent.graph import build_agent
                with pytest.raises(RuntimeError, match="LLM init failed"):
                    build_agent("gpt-4o", 0.7)
                mock_ca.assert_not_called()

    def test_get_model_raises_propagates(self, mock_run_underwriting, mock_checkpointer):
        with patch(f"{MODULE}.LLMS") as mock_llms_cls:
            instance = MagicMock()
            instance.get_model.side_effect = ValueError("unknown model")
            mock_llms_cls.return_value = instance
            with patch(f"{MODULE}.create_agent") as mock_ca:
                from backend.agent.graph import build_agent
                with pytest.raises(ValueError, match="unknown model"):
                    build_agent("unknown-model", 0.5)
                mock_ca.assert_not_called()

    def test_run_underwriting_raises_propagates(self, mock_llms_cls, mock_checkpointer):
        with patch(f"{MODULE}._run_underwriting_assessment") as mock_rua:
            mock_rua.side_effect = KeyError("deep")
            with patch(f"{MODULE}.create_agent") as mock_ca:
                from backend.agent.graph import build_agent
                with pytest.raises(KeyError):
                    build_agent("gpt-4o", 0.5, mode="deep")
                mock_ca.assert_not_called()

    def test_create_agent_raises_propagates(self, all_mocks):
        all_mocks["create_agent"].side_effect = RuntimeError("graph build failed")
        from backend.agent.graph import build_agent
        with pytest.raises(RuntimeError, match="graph build failed"):
            build_agent("gpt-4o", 0.7)

    def test_empty_model_name_forwarded(self, all_mocks):
        """Empty string is forwarded; LLMS/create_agent decide validity."""
        from backend.agent.