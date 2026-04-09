"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD: successful loading and structure of MODEL_CARD from model_card.json
- SYSTEM_PROMPT: content, type, and key behavioural constraints present in the prompt string
- Module-level side effects: file loading at import time

Mocks used:
- unittest.mock.mock_open / patch('builtins.open') to simulate model_card.json reads
- unittest.mock.patch('pathlib.Path.open') where needed
- json.load patched to return controlled data

TODOs:
- TODO: Integration test that reads the real model_card.json once a stable fixture path is agreed upon
- TODO: Test behaviour when model_card.json has unexpected schema (missing keys) — needs schema validation logic to exist first
"""

import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, mock_open, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_MODEL_CARD = {
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


def _reload_prompts_with_card(card_data: dict) -> ModuleType:
    """
    Remove the cached module and re-import it with a mocked open that returns
    *card_data* as the JSON content.  Returns the freshly imported module.
    """
    serialised = json.dumps(card_data)
    mocked_file = mock_open(read_data=serialised)

    # Patch builtins.open and json.load together so the module-level code sees
    # the fake file content.
    with patch("builtins.open", mocked_file), patch(
        "json.load", return_value=card_data
    ):
        # Remove cached version so Python actually re-executes the module body.
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        import backend.agent.prompts as prompts_mod  # noqa: PLC0415

        return prompts_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts():
    """Return the already-imported prompts module (uses real model_card.json)."""
    import backend.agent.prompts as prompts_mod  # noqa: PLC0415

    return prompts_mod


# ---------------------------------------------------------------------------
# MODEL_CARD tests
# ---------------------------------------------------------------------------


class TestModelCard:
    def test_model_card_is_dict(self, prompts):
        """MODEL_CARD must be a dictionary."""
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_not_empty(self, prompts):
        """MODEL_CARD must not be an empty dict."""
        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_has_model_name(self, prompts):
        """MODEL_CARD should contain a 'model_name' key."""
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_is_string(self, prompts):
        assert isinstance(prompts.MODEL_CARD["model_name"], str)

    def test_model_card_has_model_type(self, prompts):
        assert "model_type" in prompts.MODEL_CARD

    def test_model_card_has_target_variable(self, prompts):
        assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_has_global_feature_importance(self, prompts):
        assert "global_feature_importance" in prompts.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert isinstance(gfi, dict)

    @pytest.mark.parametrize(
        "feature",
        [
            "Age",
            "Education_Level",
            "Employment_Status",
            "Nationality",
            "Customer_Segment",
            "Annual_Income",
            "Liquid_Assets",
        ],
    )
    def test_model_card_expected_features_present(self, prompts, feature):
        """Each expected feature must appear in global_feature_importance."""
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert feature in gfi, f"Feature '{feature}' missing from global_feature_importance"

    @pytest.mark.parametrize(
        "feature, expected_value",
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
    def test_model_card_feature_importance_values(self, prompts, feature, expected_value):
        """Feature importance values must match the expected floats."""
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert gfi[feature] == pytest.approx(expected_value, rel=1e-6)

    def test_model_card_feature_importance_values_are_positive(self, prompts):
        """All feature importance values should be positive floats."""
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for name, val in gfi.items():
            assert val > 0, f"Feature '{name}' has non-positive importance {val}"

    # ------------------------------------------------------------------
    # Reload-based tests (controlled JSON content)
    # ------------------------------------------------------------------

    def test_model_card_loaded_from_json(self):
        """MODEL_CARD content must reflect exactly what json.load returns."""
        reloaded = _reload_prompts_with_card(FAKE_MODEL_CARD)
        assert reloaded.MODEL_CARD == FAKE_MODEL_CARD

    def test_model_card_minimal_valid_card(self):
        """A minimal dict should be accepted without errors."""
        minimal = {"model_name": "TestModel"}
        reloaded = _reload_prompts_with_card(minimal)
        assert reloaded.MODEL_CARD == minimal

    def test_model_card_empty_dict(self):
        """An empty JSON object should be stored as-is (no validation in module)."""
        reloaded = _reload_prompts_with_card({})
        assert reloaded.MODEL_CARD == {}

    def test_model_card_nested_structures_preserved(self):
        """Nested dicts/lists inside the card should be preserved exactly."""
        complex_card = {
            "model_name": "ComplexModel",
            "features": ["f1", "f2"],
            "metadata": {"version": 2, "tags": ["prod"]},
        }
        reloaded = _reload_prompts_with_card(complex_card)
        assert reloaded.MODEL_CARD == complex_card


# ---------------------------------------------------------------------------
# File-not-found / IO error scenarios
# ---------------------------------------------------------------------------


class TestModelCardFileErrors:
    def _force_reimport(self):
        """Ensure the module is re-executed."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

    def test_missing_file_raises_file_not_found(self):
        """ImportError / FileNotFoundError when model_card.json is absent."""
        self._force_reimport()
        with patch("builtins.open", side_effect=FileNotFoundError("no file")):
            with pytest.raises((FileNotFoundError, Exception)):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_invalid_json_raises_value_error(self):
        """json.JSONDecodeError when the file contains invalid JSON."""
        self._force_reimport()
        bad_json = mock_open(read_data="{not valid json}")
        with patch("builtins.open", bad_json):
            with pytest.raises((json.JSONDecodeError, ValueError, Exception)):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_permission_error_propagates(self):
        """PermissionError reading the file should propagate."""
        self._force_reimport()
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with pytest.raises((PermissionError, Exception)):
                import backend.agent.prompts  # noqa: PLC0415, F401


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts):
        """SYSTEM_PROMPT must be a str."""
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self, prompts):
        """SYSTEM_PROMPT must not be empty."""
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts):
        """SYSTEM_PROMPT should mention underwriting."""
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower() or "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assistant_role(self, prompts):
        """The prompt must identify itself as an assistant."""
        assert "assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_disclosure_instruction(self, prompts):
        """The prompt must instruct the model NOT to disclose internal instructions."""
        lowered = prompts.SYSTEM_PROMPT.lower()
        assert "never disclose" in lowered or "cannot disclose" in lowered or "never" in lowered

    def test_system_prompt_confidentiality_of_tools(self, prompts):
        """The prompt must protect information about available tools."""
        assert "tools" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts):
        """The prompt must reference the ability to run assessments."""
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_contain_secret_keys(self, prompts):
        """SYSTEM_PROMPT must not contain any obvious secret/token patterns."""
        import re

        # Simple heuristic: no sequences that look like API keys
        secret_pattern = re.compile(r"(sk-[A-Za-z0-9]{20,}|Bearer\s+\S{20,})")
        assert not secret_pattern.search(prompts.SYSTEM_PROMPT)

    def test_system_prompt_is_module_level_constant(self, prompts):
        """SYSTEM_PROMPT should be accessible as a module-level attribute."""
        assert hasattr(prompts, "SYSTEM_PROMPT")

    @pytest.mark.parametrize(
        "required_phrase",
        [
            "underwriter",
            "assistant",
        ],
    )
    def test_system_prompt_required_phrases(self, prompts, required_phrase):
        """Parametrised check for required phrases in SYSTEM_PROMPT."""
        assert required_phrase.lower() in prompts.SYSTEM_PROMPT.lower(), (
            f"Expected phrase '{required_phrase}' not found in SYSTEM_PROMPT"
        )

    def test_system_prompt_does_not_reveal_system_instructions(self, prompts):
        """The prompt explicitly forbids revealing 'internal system instructions'."""
        assert "system" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_length_reasonable(self, prompts):
        """SYSTEM_PROMPT should have a reasonable length (>50 chars, <10 000 chars)."""
        length = len(prompts.SYSTEM_PROMPT)
        assert length > 50, f"SYSTEM_PROMPT suspiciously short: {length} chars"
        assert length < 10_000, f"SYSTEM_PROMPT suspiciously long: {length} chars"


# ---------------------------------------------------------------------------
# Module structure / public API tests
# ---------------------------------------------------------------------------


class TestModulePublicApi:
    def test_module_exposes_model_card(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")

    def test_module_exposes_system_prompt(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_model_card_path_resolution(self, prompts):
        """_model_card_path should be a Path pointing to model_card.json."""
        assert hasattr(prompts, "_model_card_path")
        path: Path = prompts._model_card_path
        assert isinstance(path, Path)
        assert path.name == "model_card.json"

    def test_model_card_path_is_absolute(self, prompts):
        """The resolved path should be absolute so it works from any CWD."""
        assert prompts._model_card_path.is_absolute()

    def test_model_card_path_parent_is_backend(self, prompts):
        """The model_card.json should live in the 'backend' directory."""
        assert prompts._model_card_path.parent.name == "backend"


# ---------------------------------------------------------------------------
# Skipped / stub tests requiring additional context
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="TODO: needs schema validation logic in prompts.py — "
    "currently no enforcement of required keys in MODEL_CARD"
)
def test_model_card_schema_validation_missing_required_key():
    """
    TODO: Once schema validation is added to prompts.py, verify that a
    MODEL_CARD missing 'model_name' raises a descriptive ValidationError.
    """
    pass


@pytest.mark.skip(
    reason="TODO: integration test — requires the real backend/model_card.json "
    "to be present in the test environment CI path"
)
def test_model_card_real_file_integration():
    """
    TODO: Read the actual model_card.json from disk and assert its contents
    match the expected schema/values for the production model.
    """
    pass


@pytest.mark.skip(
    reason="TODO: concurrency test — module-level globals are not thread-safe; "
    "needs threading fixtures to verify MODEL_CARD is not mutated across threads"
)
def test_model_card_thread_safety():
    """
    TODO: Verify MODEL_CARD is not mutated when multiple threads import the
    module simultaneously.
    """
    pass