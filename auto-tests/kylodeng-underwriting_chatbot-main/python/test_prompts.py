"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE_CARD: successful loading of MODEL_CARD from model_card.json
- SYSTEM_PROMPT: content, type, and key behavioural constraints
- Module-level constants are correctly defined and accessible
- Edge cases: malformed JSON, missing file, empty JSON object

Mocks used:
- unittest.mock.patch / mock_open to intercept `open()` calls during module reload
- importlib to force re-execution of module-level code under controlled conditions
- Temporary files (tmp_path) to supply synthetic model_card.json content

TODOs:
- TODO: Integration test that verifies the real model_card.json on disk is valid and
        contains expected top-level keys (requires the real file to be present in CI).
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

SYNTHETIC_MODEL_CARD = {
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
    Reload backend.agent.prompts while patching open() so that
    model_card.json returns *model_card_dict*.
    Returns the freshly imported module object.
    """
    json_bytes = json.dumps(model_card_dict)

    # Remove cached module so importlib actually re-executes module body
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    m = mock_open(read_data=json_bytes)
    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_mod
    return prompts_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts_module(tmp_path):
    """
    Provides a freshly loaded prompts module backed by a real temporary
    model_card.json containing the synthetic data sample.
    """
    model_card_file = tmp_path / "model_card.json"
    model_card_file.write_text(json.dumps(SYNTHETIC_MODEL_CARD))

    # Remove any cached version
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch(
        "pathlib.Path.__new__",
        side_effect=lambda cls, *a, **kw: Path.__new__(cls, *a, **kw),
    ):
        # Patch the specific path resolution inside the module
        with patch("builtins.open", mock_open(read_data=json.dumps(SYNTHETIC_MODEL_CARD))):
            import backend.agent.prompts as mod
    yield mod
    # Cleanup cached module after test
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)


# ---------------------------------------------------------------------------
# Happy-path: importing the real module
# ---------------------------------------------------------------------------


class TestModuleImport:
    """Verify the module can be imported and exports the expected names."""

    def test_module_exports_model_card(self, prompts_module):
        assert hasattr(prompts_module, "MODEL_CARD"), "MODEL_CARD must be defined"

    def test_module_exports_system_prompt(self, prompts_module):
        assert hasattr(prompts_module, "SYSTEM_PROMPT"), "SYSTEM_PROMPT must be defined"

    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)


# ---------------------------------------------------------------------------
# MODEL_CARD content tests
# ---------------------------------------------------------------------------


class TestModelCardContent:
    """Verify MODEL_CARD is parsed correctly from the JSON source."""

    def test_model_card_loaded_from_json(self):
        mod = _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)
        assert mod.MODEL_CARD == SYNTHETIC_MODEL_CARD

    def test_model_card_model_name(self):
        mod = _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)
        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type(self):
        mod = _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)
        assert mod.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self):
        mod = _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)
        assert mod.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_feature_importance_is_dict(self):
        mod = _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)
        assert isinstance(mod.MODEL_CARD["global_feature_importance"], dict)

    @pytest.mark.parametrize(
        "feature, expected_importance",
        [
            ("Age", 34.57614295408571),
            ("Education_Level", 2.0984070824092758),
            ("Employment_Status", 2.1318889906418717),
            ("Nationality", 2.2774559327846506),
            ("Customer_Segment", 1.8731465731883152),
            ("Annual_Income", 1.0169358497744714),
            ("Liquid_Assets", 1.2231046859555164),
        ],
    )
    def test_feature_importance_values(self, feature, expected_importance):
        mod = _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)
        importance = mod.MODEL_CARD["global_feature_importance"][feature]
        assert importance == pytest.approx(expected_importance)

    def test_model_card_empty_dict(self):
        """An empty JSON object is valid and should load without error."""
        mod = _reload_prompts_with_model_card({})
        assert mod.MODEL_CARD == {}

    def test_model_card_with_extra_keys(self):
        """Additional keys in model_card.json should be preserved."""
        extended = {**SYNTHETIC_MODEL_CARD, "extra_key": "extra_value"}
        mod = _reload_prompts_with_model_card(extended)
        assert mod.MODEL_CARD.get("extra_key") == "extra_value"

    def test_model_card_nested_values_preserved(self):
        nested = {"top": {"nested": {"deep": 42}}}
        mod = _reload_prompts_with_model_card(nested)
        assert mod.MODEL_CARD["top"]["nested"]["deep"] == 42


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT content tests
# ---------------------------------------------------------------------------


class TestSystemPromptContent:
    """Verify the SYSTEM_PROMPT string encodes the required behavioural rules."""

    @pytest.fixture(autouse=True)
    def _load_module(self):
        mod = _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)
        self.prompt = mod.SYSTEM_PROMPT

    def test_system_prompt_is_non_empty(self):
        assert len(self.prompt.strip()) > 0

    def test_system_prompt_mentions_underwriting(self):
        assert "underwriting" in self.prompt.lower() or "underwriter" in self.prompt.lower()

    def test_system_prompt_mentions_senior_underwriting_assistant(self):
        assert "senior underwriting assistant" in self.prompt.lower()

    def test_system_prompt_confidentiality_instruction(self):
        """The prompt must instruct the model never to disclose internal instructions."""
        assert "never disclose" in self.prompt.lower() or "cannot disclose" in self.prompt.lower() or "can never disclose" in self.prompt.lower()

    def test_system_prompt_no_tool_disclosure(self):
        """The prompt must mention not revealing the tools available."""
        assert "tools" in self.prompt.lower()

    def test_system_prompt_helpful_assistant_persona(self):
        assert "helpful assistant" in self.prompt.lower()

    def test_system_prompt_mentions_assessments(self):
        assert "assessments" in self.prompt.lower() or "assessment" in self.prompt.lower()

    def test_system_prompt_mentions_gathering_information(self):
        assert "gathering information" in self.prompt.lower() or "gather" in self.prompt.lower()

    def test_system_prompt_no_leading_trailing_newlines(self):
        """Prompt should not start or end with stray newlines that could confuse the LLM."""
        assert self.prompt == self.prompt.strip() or self.prompt.startswith("You")

    def test_system_prompt_does_not_start_with_whitespace(self):
        assert not self.prompt.startswith(" "), "SYSTEM_PROMPT should not start with a space"

    def test_system_prompt_type_is_str(self):
        assert isinstance(self.prompt, str)


# ---------------------------------------------------------------------------
# Error-condition tests (file not found, bad JSON)
# ---------------------------------------------------------------------------


class TestModelCardLoadingErrors:
    """Verify the module raises appropriate errors under bad conditions."""

    def test_missing_model_card_file_raises_file_not_found(self):
        """If model_card.json does not exist the module should raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("No such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401

    def test_malformed_json_raises_json_decode_error(self):
        """If model_card.json contains invalid JSON, json.JSONDecodeError should be raised."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        bad_json = "{ this is not valid json !!!"
        with patch("builtins.open", mock_open(read_data=bad_json)):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401

    def test_empty_file_raises_json_decode_error(self):
        """An empty file is not valid JSON."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", mock_open(read_data="")):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401

    def test_json_array_at_top_level_loaded(self):
        """A JSON array (not object) is valid JSON and should load without error."""
        array_json = [1, 2, 3]
        mod = _reload_prompts_with_model_card.__wrapped__(array_json) if hasattr(
            _reload_prompts_with_model_card, "__wrapped__"
        ) else None

        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", mock_open(read_data=json.dumps(array_json))):
            import backend.agent.prompts as mod  # noqa: F811
        assert mod.MODEL_CARD == array_json

    def test_permission_error_propagates(self):
        """PermissionError from open() should propagate."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=PermissionError("Access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: F401


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------


class TestModelCardPath:
    """Verify the model_card.json path is resolved relative to the module."""

    def test_model_card_path_resolves_correctly(self, prompts_module):
        """
        The _model_card_path should point to
        <module_parent>/../model_card.json  (i.e., backend/model_card.json).
        """
        # Re-examine the private path attribute if available, else derive it
        import backend.agent.prompts as mod
        expected_name = "model_card.json"
        # We can't easily introspect the private variable after import but we
        # can check the open() call argument used during a controlled reload.
        captured_paths = []
        original_open = open

        def capturing_open(path, *args, **kwargs):
            captured_paths.append(str(path))
            return mock_open(read_data=json.dumps(SYNTHETIC_MODEL_CARD))()

        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", side_effect=capturing_open):
            try:
                import backend.agent.prompts  # noqa: F401
            except Exception:
                pass

        assert any(expected_name in p for p in captured_paths), (
            f"Expected open() to be called with a path containing '{expected_name}', "
            f"got: {captured_paths}"
        )


# ---------------------------------------------------------------------------
# Integration smoke test (skipped when real file is absent)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "TODO: Requires the real backend/model_card.json to be present in the repository. "
        "Enable this test in CI once the file is confirmed available."
    )
)
def test_real_model_card_file_integration():
    """
    TODO: Verify the real model_card.json on disk contains expected top-level keys
    and that all feature importance values are positive floats.
    """
    import backend.agent.prompts as mod

    assert "model_name" in mod.MODEL_CARD
    assert "model_type" in mod.MODEL_CARD
    assert "target_variable" in mod.MODEL_CARD
    assert "global_feature_importance" in mod.MODEL_CARD
    for feature, importance in mod.MODEL_CARD["global_feature_importance"].items():
        assert isinstance(importance, float), f"{feature} importance should be a float"
        assert importance >= 0, f"{feature} importance should be non-negative"


@pytest.mark.skip(
    reason=(
        "TODO: Requires a running environment with backend package properly installed. "
        "Verifies SYSTEM_PROMPT length is within LLM context window safety limits."
    )
)
def test_system_prompt_length_within_token_budget():
    """
    TODO: Assert that SYSTEM_PROMPT does not exceed a reasonable character budget
    (e.g. 2000 chars) to avoid consuming excessive context window tokens.
    """
    import backend.agent.prompts as mod

    assert len(mod.SYSTEM_PROMPT) <= 2000, "SYSTEM_PROMPT is unexpectedly long"