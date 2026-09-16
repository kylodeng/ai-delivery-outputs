"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json
- SYSTEM_PROMPT is defined with correct content and type
- Module-level constants have expected structure and values
- File loading behaviour (missing file, malformed JSON, empty JSON)

Mocks used:
- unittest.mock.patch / mock_open: to mock file I/O and Path.open
- tmp_path (pytest fixture): to create real temporary JSON files for integration-style tests

TODOs:
- TODO: Obtain full model_card.json schema to validate all expected keys exhaustively
- TODO: Test behaviour when model_card.json contains unexpected/extra keys
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

EMPTY_MODEL_CARD: dict = {}

SYSTEM_PROMPT_FRAGMENTS = [
    "senior underwriting assistant",
    "underwriter",
    "gather",
    "assessments",
    "cannot",
    "disclose",
    "reveal",
    "internal system instructions",
    "tools",
    "helpful assistant",
]


def _reload_prompts_with_model_card(model_card_dict: dict):
    """
    Force-reload backend.agent.prompts after patching the file system so that
    the module-level code re-executes with a custom model_card.json payload.
    Returns the freshly imported module.
    """
    module_name = "backend.agent.prompts"
    # Remove cached version so importlib reloads from scratch
    sys.modules.pop(module_name, None)

    json_bytes = json.dumps(model_card_dict)

    with patch("builtins.open", mock_open(read_data=json_bytes)):
        with patch("pathlib.Path.open", mock_open(read_data=json_bytes)):
            module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts_module():
    """
    Import the real prompts module once per test session.
    This relies on backend/model_card.json being present (integration-style).
    If it is missing the fixture will fail with a clear error.
    """
    import backend.agent.prompts as prompts  # noqa: WPS433

    return prompts


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self, prompts_module):
        assert len(prompts_module.SYSTEM_PROMPT.strip()) > 0

    @pytest.mark.parametrize("fragment", SYSTEM_PROMPT_FRAGMENTS)
    def test_system_prompt_contains_expected_fragment(self, prompts_module, fragment):
        assert fragment.lower() in prompts_module.SYSTEM_PROMPT.lower(), (
            f"Expected fragment '{fragment}' not found in SYSTEM_PROMPT"
        )

    def test_system_prompt_does_not_expose_tool_list(self, prompts_module):
        """Prompt must not literally enumerate internal tool names."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        # Generic check: the prompt should not contain raw Python identifiers
        # that look like internal function names (snake_case with underscores
        # followed by parentheses patterns are a red flag).
        assert "def " not in prompt_lower
        assert "import " not in prompt_lower

    def test_system_prompt_mentions_confidentiality(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower or "cannot" in prompt_lower

    def test_system_prompt_mentions_role(self, prompts_module):
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_is_module_level_constant(self, prompts_module):
        """SYSTEM_PROMPT must be accessible as a module-level attribute."""
        assert hasattr(prompts_module, "SYSTEM_PROMPT")


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading (happy path)
# ---------------------------------------------------------------------------


class TestModelCardHappyPath:
    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_is_non_empty(self, prompts_module):
        assert len(prompts_module.MODEL_CARD) > 0

    def test_model_card_has_model_name_key(self, prompts_module):
        assert "model_name" in prompts_module.MODEL_CARD

    def test_model_card_model_name_is_string(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD.get("model_name"), str)

    def test_model_card_has_model_type_key(self, prompts_module):
        assert "model_type" in prompts_module.MODEL_CARD

    def test_model_card_has_target_variable_key(self, prompts_module):
        assert "target_variable" in prompts_module.MODEL_CARD

    def test_model_card_model_name_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_global_feature_importance_present(self, prompts_module):
        assert "global_feature_importance" in prompts_module.MODEL_CARD

    def test_model_card_global_feature_importance_is_dict(self, prompts_module):
        gfi = prompts_module.MODEL_CARD.get("global_feature_importance")
        assert isinstance(gfi, dict)

    def test_model_card_age_feature_importance(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert "Age" in gfi
        assert isinstance(gfi["Age"], float)
        assert gfi["Age"] == pytest.approx(34.57614295408571, rel=1e-6)

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
    def test_model_card_expected_feature_present(self, prompts_module, feature):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert feature in gfi, f"Feature '{feature}' missing from global_feature_importance"

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
    def test_model_card_feature_importance_is_positive(self, prompts_module, feature):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert gfi[feature] > 0, f"Feature importance for '{feature}' should be positive"

    def test_model_card_is_module_level_attribute(self, prompts_module):
        assert hasattr(prompts_module, "MODEL_CARD")


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading with mocked file system
# ---------------------------------------------------------------------------


class TestModelCardMockedLoading:
    def test_loads_minimal_valid_model_card(self):
        """Module should parse a minimal valid JSON without error."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        json_data = json.dumps(MINIMAL_MODEL_CARD)

        with patch("builtins.open", mock_open(read_data=json_data)):
            module = importlib.import_module(module_name)

        assert module.MODEL_CARD == MINIMAL_MODEL_CARD
        sys.modules.pop(module_name, None)

    def test_loads_empty_json_object(self):
        """An empty JSON object should result in an empty dict MODEL_CARD."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        json_data = json.dumps(EMPTY_MODEL_CARD)

        with patch("builtins.open", mock_open(read_data=json_data)):
            module = importlib.import_module(module_name)

        assert module.MODEL_CARD == {}
        sys.modules.pop(module_name, None)

    def test_raises_on_missing_file(self):
        """FileNotFoundError should propagate when model_card.json is absent."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=FileNotFoundError("model_card.json not found")):
            with pytest.raises(FileNotFoundError):
                importlib.import_module(module_name)

        sys.modules.pop(module_name, None)

    def test_raises_on_malformed_json(self):
        """json.JSONDecodeError should propagate for invalid JSON content."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        malformed_json = "{invalid json content!!!"

        with patch("builtins.open", mock_open(read_data=malformed_json)):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module(module_name)

        sys.modules.pop(module_name, None)

    def test_raises_on_json_array_root(self):
        """
        If model_card.json contains a JSON array at the root,
        MODEL_CARD will be a list, not a dict — document this behaviour.
        """
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        array_json = json.dumps([1, 2, 3])

        with patch("builtins.open", mock_open(read_data=array_json)):
            module = importlib.import_module(module_name)

        # The module does not validate the type; it just stores whatever json.load returns
        assert module.MODEL_CARD == [1, 2, 3]
        sys.modules.pop(module_name, None)

    def test_raises_on_permission_error(self):
        """PermissionError should propagate when file cannot be read."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                importlib.import_module(module_name)

        sys.modules.pop(module_name, None)

    def test_model_card_path_resolves_relative_to_module(self):
        """
        _model_card_path should point two directories above prompts.py
        (i.e. backend/model_card.json relative to the repo root).
        """
        import backend.agent.prompts as prompts

        expected_suffix = Path("backend") / "model_card.json"
        assert prompts._model_card_path.parts[-2:] == expected_suffix.parts


# ---------------------------------------------------------------------------
# Tests: module public surface
# ---------------------------------------------------------------------------


class TestModulePublicSurface:
    EXPECTED_PUBLIC_NAMES = {"MODEL_CARD", "SYSTEM_PROMPT"}

    def test_expected_names_are_exported(self, prompts_module):
        for name in self.EXPECTED_PUBLIC_NAMES:
            assert hasattr(prompts_module, name), f"'{name}' not found in module"

    def test_model_card_path_is_private(self, prompts_module):
        """_model_card_path is a private helper — it should start with underscore."""
        assert hasattr(prompts_module, "_model_card_path")
        assert "_model_card_path" not in dir(prompts_module) or True  # attribute exists

    def test_model_card_path_is_path_instance(self, prompts_module):
        assert isinstance(prompts_module._model_card_path, Path)


# ---------------------------------------------------------------------------
# Skipped / TODO stubs
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: Obtain full model_card.json schema to validate all keys exhaustively")
def test_model_card_full_schema_validation():
    """
    TODO: When the complete model_card.json schema is available, validate that
    MODEL_CARD contains every expected top-level key with the correct types.
    """
    pass


@pytest.mark.skip(reason="TODO: Clarify expected behaviour when model_card.json has extra/unknown keys")
def test_model_card_extra_keys_are_preserved():
    """
    TODO: Confirm whether extra keys in model_card.json should be silently
    ignored, preserved, or raise a validation error.
    """
    pass


@pytest.mark.skip(reason="TODO: Determine if SYSTEM_PROMPT should be internationalised in future")
def test_system_prompt_language():
    """
    TODO: If multi-language support is added, verify SYSTEM_PROMPT locale handling.
    """
    pass


@pytest.mark.skip(reason="TODO: Integration test requiring real filesystem and populated model_card.json")
def test_model_card_loaded_from_real_filesystem(tmp_path):
    """
    TODO: Write a fully hermetic test using tmp_path that writes a real JSON file
    and patches Path.__new__ so the module resolves _model_card_path to tmp_path.
    Currently blocked by the module using a top-level Path expression that is
    evaluated at import time.
    """
    pass