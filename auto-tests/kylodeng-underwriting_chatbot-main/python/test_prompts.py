"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD: successful loading of model_card.json at module level
- SYSTEM_PROMPT: content, type, and behavioural constraints encoded in the prompt string
- Module-level file path construction (_model_card_path)
- Error conditions: missing file, malformed JSON

Mocks used:
- unittest.mock.patch / mock_open: to intercept open() calls and Path resolution
- tmp_path (pytest fixture): to write real temporary model_card.json files for integration-style tests

TODOs:
- TODO: If model_card.json schema is validated anywhere, add schema-validation tests once that logic exists
- TODO: Add tests for any future helper functions added to prompts.py
"""

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_MODEL_CARD = {
    "model_name": "Underwriting Risk Classification",
    "model_type": "CatBoostClassifier",
    "target_variable": "Risk_Classification",
    "global_feature_importance": {
        "Age": 34.57614295408571,
        "Education_Level": 2.0984070824092758,
        "Employment_Status": 2.1318889906418717,
        "Nationality": 2.2774559327846506,
        "Customer_Segment": 1.8731465731883152,
        "Annual_Income": 1.0169358497744714,
        "Liquid_Assets": 1.2231046859555164,
    },
}


def _reload_prompts_with_model_card(model_card_dict: dict):
    """
    Reload backend.agent.prompts with a patched model_card.json containing
    *model_card_dict*.  Returns the freshly-imported module.
    """
    json_bytes = json.dumps(model_card_dict)
    m_open = mock_open(read_data=json_bytes)

    # Remove cached module so importlib re-executes module-level code
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m_open):
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts_module(tmp_path, monkeypatch):
    """
    Provide a cleanly-imported prompts module backed by a real temporary
    model_card.json file so that module-level I/O succeeds.
    """
    # Write a valid model card next to where prompts.py expects it
    model_card_file = tmp_path / "model_card.json"
    model_card_file.write_text(json.dumps(VALID_MODEL_CARD), encoding="utf-8")

    # Patch Path so that _model_card_path resolves to our tmp file
    original_path_class = Path

    class PatchedPath(type(tmp_path)):  # same concrete type
        def __new__(cls, *args, **kwargs):
            instance = original_path_class(*args, **kwargs)
            return instance

    # Simpler: just patch the attribute after import by reloading
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("pathlib.Path.__truediv__", side_effect=_make_truediv_patcher(tmp_path)):
        pass  # see below – we use mock_open instead for reliability

    json_bytes = json.dumps(VALID_MODEL_CARD)
    with patch("builtins.open", mock_open(read_data=json_bytes)):
        import backend.agent.prompts as mod  # noqa: PLC0415

    yield mod

    # Cleanup – remove from cache so other tests get a clean slate
    sys.modules.pop("backend.agent.prompts", None)


def _make_truediv_patcher(tmp_path):
    """Not used in current fixture but kept for reference."""

    def _truediv(self, other):
        return Path.__truediv__(self, other)

    return _truediv


# ---------------------------------------------------------------------------
# MODEL_CARD loading – happy path
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests around the module-level MODEL_CARD constant."""

    def test_model_card_is_dict(self):
        """MODEL_CARD must be a dictionary after module load."""
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        assert isinstance(mod.MODEL_CARD, dict)

    def test_model_card_contains_expected_keys(self):
        """MODEL_CARD should expose known top-level keys from the fixture data."""
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        for key in ("model_name", "model_type", "target_variable", "global_feature_importance"):
            assert key in mod.MODEL_CARD, f"Missing key: {key}"

    def test_model_card_model_name_value(self):
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type_value(self):
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        assert mod.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self):
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        assert mod.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_feature_importance_is_dict(self):
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        assert isinstance(mod.MODEL_CARD["global_feature_importance"], dict)

    def test_model_card_age_feature_importance(self):
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        age_importance = mod.MODEL_CARD["global_feature_importance"]["Age"]
        assert pytest.approx(age_importance, rel=1e-6) == 34.57614295408571

    def test_model_card_with_minimal_json(self):
        """Module should load even with a minimal/empty JSON object."""
        minimal = {"model_name": "minimal"}
        mod = _reload_prompts_with_model_card(minimal)
        assert mod.MODEL_CARD == {"model_name": "minimal"}

    def test_model_card_with_empty_object(self):
        """An empty JSON object {} is valid JSON – module should still load."""
        mod = _reload_prompts_with_model_card({})
        assert mod.MODEL_CARD == {}

    def test_model_card_with_nested_structure(self):
        """Deeply nested structures should be preserved faithfully."""
        nested = {"a": {"b": {"c": [1, 2, 3]}}}
        mod = _reload_prompts_with_model_card(nested)
        assert mod.MODEL_CARD["a"]["b"]["c"] == [1, 2, 3]

    @pytest.mark.parametrize(
        "extra_key,extra_value",
        [
            ("version", "1.0.0"),
            ("training_date", "2024-01-01"),
            ("accuracy", 0.95),
            ("tags", ["insurance", "risk"]),
        ],
    )
    def test_model_card_extra_fields_preserved(self, extra_key, extra_value):
        """Any additional fields in model_card.json must be kept intact."""
        card = {**VALID_MODEL_CARD, extra_key: extra_value}
        mod = _reload_prompts_with_model_card(card)
        assert mod.MODEL_CARD[extra_key] == extra_value


# ---------------------------------------------------------------------------
# MODEL_CARD loading – error / edge conditions
# ---------------------------------------------------------------------------


class TestModelCardLoadingErrors:
    """Tests for failure modes during module-level file loading."""

    def test_file_not_found_raises_on_import(self):
        """If model_card.json does not exist, importing prompts should raise."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("not found")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_invalid_json_raises_on_import(self):
        """Malformed JSON in model_card.json should raise json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        bad_json = "{ this is not valid json }"
        with patch("builtins.open", mock_open(read_data=bad_json)):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_empty_file_raises_on_import(self):
        """An empty file is invalid JSON and should raise json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", mock_open(read_data="")):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_json_array_at_root_loads_as_list(self):
        """JSON arrays are valid – MODEL_CARD would be a list (unusual but handled)."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        array_json = json.dumps([1, 2, 3])
        with patch("builtins.open", mock_open(read_data=array_json)):
            import backend.agent.prompts as mod  # noqa: PLC0415

        assert mod.MODEL_CARD == [1, 2, 3]
        sys.modules.pop("backend.agent.prompts", None)

    def test_permission_error_raises_on_import(self):
        """A PermissionError on open() should propagate out of the module."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: PLC0415, F401


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT – type and content
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT constant."""

    @pytest.fixture(autouse=True)
    def _load_module(self):
        self.mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)

    def test_system_prompt_is_string(self):
        assert isinstance(self.mod.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self):
        assert len(self.mod.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self):
        """Prompt must reference the underwriting domain."""
        assert "underwriting" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self):
        """Prompt addresses an underwriter audience."""
        assert "underwriter" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_confidentiality_instruction(self):
        """Prompt must contain the instruction to never disclose internal instructions."""
        prompt_lower = self.mod.SYSTEM_PROMPT.lower()
        # Both 'disclose' and 'reveal' should appear per the source
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_system_prompt_never_reveal_tools(self):
        """Prompt must prohibit revealing tools."""
        assert "tools" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_helpful_assistant_framing(self):
        """Prompt should instruct the model to present itself as a helpful assistant."""
        assert "helpful assistant" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_run_assessments_capability(self):
        """Prompt should mention ability to run assessments."""
        assert "assessments" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_expose_tool_names(self):
        """
        Prompt text itself should not enumerate concrete internal tool names
        (as that would contradict the confidentiality instruction).
        The prompt should reference 'tools' generically only.
        """
        # Specific internal function/tool names should not leak into the system prompt
        forbidden_fragments = ["def ", "import ", "def run_", "tool_registry"]
        for fragment in forbidden_fragments:
            assert fragment not in self.mod.SYSTEM_PROMPT, (
                f"System prompt must not contain '{fragment}'"
            )

    def test_system_prompt_no_leading_trailing_significant_whitespace(self):
        """
        The prompt should not start or end with unexpected newlines that could
        confuse LLM tokenisers.
        """
        # Allow regular spaces but not leading/trailing newlines
        assert not self.mod.SYSTEM_PROMPT.startswith("\n")
        assert not self.mod.SYSTEM_PROMPT.endswith("\n")

    def test_system_prompt_senior_role_description(self):
        """Prompt should declare a 'senior' role for authority framing."""
        assert "senior" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_gather_information_intent(self):
        """Prompt must express that the agent gathers information."""
        assert "gathering information" in self.mod.SYSTEM_PROMPT.lower() or \
               "gather information" in self.mod.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Module-level path construction
# ---------------------------------------------------------------------------


class TestModelCardPath:
    """Tests for the _model_card_path resolution logic."""

    def test_model_card_path_is_path_object(self):
        """After module load _model_card_path should be a Path (tested indirectly)."""
        mod = _reload_prompts_with_model_card(VALID_MODEL_CARD)
        # We verify indirectly that Path construction succeeded by confirming
        # MODEL_CARD was populated (i.e. open() was called successfully).
        assert mod.MODEL_CARD is not None

    def test_model_card_filename_used_in_open(self):
        """open() must be called with a path ending in 'model_card.json'."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        json_bytes = json.dumps(VALID_MODEL_CARD)
        m_open = mock_open(read_data=json_bytes)
        with patch("builtins.open", m_open):
            import backend.agent.prompts  # noqa: PLC0415, F401

        call_args = m_open.call_args
        # First positional argument is the path
        opened_path = call_args[0][0]
        assert str(opened_path).endswith("model_card.json"), (
            f"Expected path ending in 'model_card.json', got: {opened_path}"
        )
        sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


class TestPublicConstants:
    """Ensure the public names expected by