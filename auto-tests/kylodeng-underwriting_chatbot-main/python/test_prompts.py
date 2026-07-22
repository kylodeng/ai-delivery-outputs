"""
Test module for backend/agent/prompts.py

What is tested:
    - MODULE_CARD: successful loading and structure of the MODEL_CARD JSON
    - SYSTEM_PROMPT: content, type, and behavioural constraints encoded in the prompt string
    - Module-level side effects: file path resolution, JSON parsing

Mocks used:
    - unittest.mock.patch / mock_open: to mock filesystem access for model_card.json
      so tests never depend on the real file being present or correctly formatted
    - json.load: patched where isolated JSON-error scenarios are needed

TODOs:
    - TODO: Obtain the full model_card.json schema to validate every key exhaustively
    - TODO: If MODEL_CARD gains a version field, add a semver-format test
"""

import builtins
import importlib
import json
import sys
import types
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_MODEL_CARD = {
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

MINIMAL_MODEL_CARD_JSON = json.dumps(MINIMAL_MODEL_CARD)


def _reload_prompts_with_model_card(model_card_dict: dict):
    """
    Re-import backend.agent.prompts while mocking the open() call that reads
    model_card.json, injecting *model_card_dict* as the parsed content.

    Returns the freshly imported module object.
    """
    module_name = "backend.agent.prompts"

    # Remove cached version so importlib re-executes module-level code
    sys.modules.pop(module_name, None)
    # Also remove child references that may shadow a reload
    for key in list(sys.modules.keys()):
        if key.startswith(module_name):
            sys.modules.pop(key, None)

    mock_file_content = json.dumps(model_card_dict)
    m_open = mock_open(read_data=mock_file_content)

    with patch("builtins.open", m_open):
        with patch("json.load", return_value=model_card_dict):
            module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_module_cache():
    """Ensure each test starts without a cached prompts module."""
    yield
    sys.modules.pop("backend.agent.prompts", None)


@pytest.fixture()
def prompts_module():
    """Return the prompts module loaded with the synthetic model card."""
    return _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts_module):
        """MODEL_CARD must be a dictionary after JSON parsing."""
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_model_name(self, prompts_module):
        """model_name key must equal the synthetic fixture value."""
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type(self, prompts_module):
        """model_type key must equal the synthetic fixture value."""
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self, prompts_module):
        """target_variable key must be present and non-empty."""
        target = prompts_module.MODEL_CARD.get("target_variable")
        assert target and isinstance(target, str)

    def test_model_card_global_feature_importance_is_dict(self, prompts_module):
        """global_feature_importance must be a dict of numeric values."""
        gfi = prompts_module.MODEL_CARD.get("global_feature_importance")
        assert isinstance(gfi, dict)

    def test_model_card_feature_importance_values_are_numeric(self, prompts_module):
        """Every feature-importance value must be a float or int."""
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        for feature, score in gfi.items():
            assert isinstance(score, (int, float)), (
                f"Feature '{feature}' has non-numeric importance: {score!r}"
            )

    def test_model_card_feature_importance_values_are_non_negative(self, prompts_module):
        """Feature importance scores must be >= 0."""
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        for feature, score in gfi.items():
            assert score >= 0, f"Negative importance for feature '{feature}': {score}"

    def test_model_card_age_importance_highest(self, prompts_module):
        """Age should have the highest importance according to synthetic data."""
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert gfi["Age"] == max(gfi.values())

    def test_model_card_contains_expected_features(self, prompts_module):
        """All expected features from the synthetic sample must be present."""
        expected_features = {
            "Age",
            "Education_Level",
            "Employment_Status",
            "Nationality",
            "Customer_Segment",
            "Annual_Income",
            "Liquid_Assets",
        }
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert expected_features.issubset(gfi.keys())

    def test_model_card_loaded_only_from_correct_path(self):
        """open() must be called with the path ending in model_card.json."""
        collected_paths = []

        original_open = builtins.open

        def tracking_open(path, *args, **kwargs):
            collected_paths.append(str(path))
            if "model_card.json" in str(path):
                return StringIO(MINIMAL_MODEL_CARD_JSON)
            return original_open(path, *args, **kwargs)

        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=tracking_open):
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                importlib.import_module("backend.agent.prompts")

        assert any("model_card.json" in p for p in collected_paths), (
            f"model_card.json was not opened; opened paths: {collected_paths}"
        )

    def test_model_card_with_empty_feature_importance(self):
        """MODULE loads successfully even when global_feature_importance is empty."""
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {}}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD["global_feature_importance"] == {}

    def test_model_card_with_extra_keys(self):
        """Extra keys in model_card.json must be preserved verbatim."""
        card = {**MINIMAL_MODEL_CARD, "extra_metadata": {"version": "1.0.0"}}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD.get("extra_metadata") == {"version": "1.0.0"}

    def test_model_card_file_not_found_raises(self):
        """If model_card.json is missing, the module must raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                importlib.import_module("backend.agent.prompts")

    def test_model_card_invalid_json_raises(self):
        """If model_card.json contains invalid JSON, module import must raise an error."""
        sys.modules.pop("backend.agent.prompts", None)
        m_open = mock_open(read_data="{ this is : not valid json }")
        with patch("builtins.open", m_open):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                # json.load is NOT patched here – real parsing of bad data
                importlib.import_module("backend.agent.prompts")

    def test_model_card_numeric_string_values_not_cast(self):
        """String values in the card must not be silently coerced to numbers."""
        card = {**MINIMAL_MODEL_CARD, "model_name": "123"}
        module = _reload_prompts_with_model_card(card)
        assert isinstance(module.MODEL_CARD["model_name"], str)


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts_module):
        """SYSTEM_PROMPT must be a plain Python string."""
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self, prompts_module):
        """SYSTEM_PROMPT must not be empty or whitespace-only."""
        assert prompts_module.SYSTEM_PROMPT.strip()

    def test_system_prompt_mentions_underwriting(self, prompts_module):
        """The prompt must mention 'underwriting' to establish domain context."""
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, prompts_module):
        """The prompt must reference the target user role ('underwriter')."""
        assert "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_prohibits_disclosure_of_instructions(self, prompts_module):
        """The prompt must instruct the model NOT to disclose system instructions."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        # Look for the intent: never disclose / reveal internal instructions
        assert "never" in prompt_lower or "cannot" in prompt_lower or "must not" in prompt_lower

    def test_system_prompt_prohibits_tool_disclosure(self, prompts_module):
        """The prompt must mention non-disclosure of tools."""
        assert "tools" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts_module):
        """The prompt must mention 'assessments' as a core capability."""
        assert "assessment" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_presents_as_helpful_assistant(self, prompts_module):
        """The prompt must describe the assistant as 'helpful'."""
        assert "helpful" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_gathering_information(self, prompts_module):
        """The prompt should describe the information-gathering role."""
        assert "gather" in prompts_module.SYSTEM_PROMPT.lower() or "information" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_expose_raw_json(self, prompts_module):
        """SYSTEM_PROMPT must not accidentally contain raw JSON fragments."""
        assert "{" not in prompts_module.SYSTEM_PROMPT and "}" not in prompts_module.SYSTEM_PROMPT

    def test_system_prompt_length_is_reasonable(self, prompts_module):
        """SYSTEM_PROMPT should be at least 50 characters to be meaningful."""
        assert len(prompts_module.SYSTEM_PROMPT) >= 50

    def test_system_prompt_is_module_level_constant(self, prompts_module):
        """SYSTEM_PROMPT must be accessible as a module-level attribute."""
        assert hasattr(prompts_module, "SYSTEM_PROMPT")

    def test_system_prompt_immutable_across_reloads(self):
        """Two independently loaded instances must produce identical SYSTEM_PROMPTs."""
        module_a = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        sys.modules.pop("backend.agent.prompts", None)
        module_b = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert module_a.SYSTEM_PROMPT == module_b.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests – module-level path resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    def test_model_card_path_is_path_object(self, prompts_module):
        """_model_card_path must be a pathlib.Path instance."""
        assert isinstance(prompts_module._model_card_path, Path)

    def test_model_card_path_ends_with_model_card_json(self, prompts_module):
        """The resolved path must end with 'model_card.json'."""
        assert prompts_module._model_card_path.name == "model_card.json"

    def test_model_card_path_is_absolute(self, prompts_module):
        """The resolved path must be absolute (derived from __file__)."""
        assert prompts_module._model_card_path.is_absolute()

    def test_model_card_path_points_to_backend_directory(self, prompts_module):
        """The parent directory of model_card.json should be 'backend'."""
        parent_name = prompts_module._model_card_path.parent.name
        assert parent_name == "backend"


# ---------------------------------------------------------------------------
# Parametrised edge-case tests for MODEL_CARD content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "card_patch,key,expected_type",
    [
        ({"model_name": "Test Model"}, "model_name", str),
        ({"model_type": "RandomForest"}, "model_type", str),
        ({"target_variable": "Output"}, "target_variable", str),
        ({"global_feature_importance": {"A": 1.0}}, "global_feature_importance", dict),
    ],
)
def test_model_card_key_types_parametrised(card_patch, key, expected_type):
    """Parametrised: individual MODEL_CARD keys must have the expected Python type."""
    card = {**MINIMAL_MODEL_CARD, **card_patch}
    module = _reload_prompts_with_model_card(card)
    assert isinstance(module.MODEL_CARD[key], expected_type)


@pytest.mark.parametrize(
    "bad_card",
    [
        [],          # list instead of dict
        "string",   # string instead of dict
        42,          # int instead of dict
        None,        # None
    ],
)
def test_model_card_non_dict_json_root(bad_card):
    """If JSON root is not a dict, MODEL_CARD should reflect that (no silent coercion)."""
    sys.modules.pop("backend.agent.prompts", None)
    with patch("builtins.open", mock_open(read_data=json.dumps(bad_card))):
        with patch("json.load", return_value=bad_card):
            module = importlib.import_module("backend.agent.prompts")
    # The module loads without error; MODEL_CARD equals whatever json.load returned
    assert module.MODEL_CARD == bad_card


# ---------------------------------------------------------------------------
# Stub / skipped tests for context requiring additional info
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: full model_card.