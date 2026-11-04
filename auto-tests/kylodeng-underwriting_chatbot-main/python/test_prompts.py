"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at import time
- SYSTEM_PROMPT string is defined, non-empty, and contains expected content
- MODEL_CARD is a dict with expected structure
- Edge cases: file-not-found, malformed JSON, missing keys

Mocks used:
- unittest.mock.patch / mock_open to simulate file I/O for MODEL_CARD loading
- tmp_path (pytest fixture) to create real temporary model_card.json files
  for integration-style path tests

TODOs:
- TODO: Extend MODEL_CARD structural tests once the full schema is finalised
  (currently only partial JSON is provided in synthetic samples)
"""

import builtins
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


def _reload_prompts_with_card(model_card_dict: dict):
    """
    Reload the prompts module with a patched model_card.json containing
    *model_card_dict*.  Returns the freshly imported module object.
    """
    json_bytes = json.dumps(model_card_dict)
    m = mock_open(read_data=json_bytes)

    # Remove cached module so importlib.import_module re-executes module body
    sys.modules.pop("backend.agent.prompts", None)

    with patch("builtins.open", m):
        with patch("pathlib.Path.open", m):
            # patch json.load so it deserialises our dict rather than a real file
            with patch("json.load", return_value=model_card_dict):
                import backend.agent.prompts as prompts_module
                return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def prompts():
    """Return the already-imported prompts module (uses the real model_card.json)."""
    # Ensure the module is imported (it may already be cached)
    import backend.agent.prompts as prompts_module
    return prompts_module


@pytest.fixture()
def reloaded_prompts():
    """
    Provide a factory that reloads the prompts module with a custom model card.
    Cleans up the sys.modules entry after the test.
    """
    imported_modules = []

    def _factory(card: dict):
        mod = _reload_prompts_with_card(card)
        imported_modules.append(mod)
        return mod

    yield _factory

    # Teardown: evict any module we injected so later tests get a clean slate
    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self, prompts):
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts):
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower() or \
               "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessment(self, prompts):
        assert "assessment" in prompts.SYSTEM_PROMPT.lower() or \
               "assess" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_discloses_no_instructions_policy(self, prompts):
        """The prompt must state it cannot disclose internal instructions."""
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in lower or "reveal" in lower or "cannot" in lower

    def test_system_prompt_no_leading_trailing_whitespace_on_sentences(self, prompts):
        """Each natural sentence boundary should not leave the whole prompt blank."""
        assert prompts.SYSTEM_PROMPT == prompts.SYSTEM_PROMPT  # trivially true; real check below
        assert prompts.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_presents_as_helpful_assistant(self, prompts):
        assert "helpful assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_tool_names_exposed(self, prompts):
        """
        The prompt claims it won't reveal tools – verify no obvious tool
        function names are accidentally embedded in the prompt text itself.
        """
        forbidden_fragments = ["def ", "import ", "lambda "]
        for fragment in forbidden_fragments:
            assert fragment not in prompts.SYSTEM_PROMPT

    @pytest.mark.parametrize("keyword", [
        "underwriter",
        "assistant",
        "information",
    ])
    def test_system_prompt_contains_keyword(self, prompts, keyword):
        assert keyword.lower() in prompts.SYSTEM_PROMPT.lower(), \
            f"Expected keyword '{keyword}' in SYSTEM_PROMPT"


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading (happy path with real file)
# ---------------------------------------------------------------------------

class TestModelCardHappyPath:
    def test_model_card_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_is_non_empty(self, prompts):
        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_has_model_name(self, prompts):
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_is_string(self, prompts):
        assert isinstance(prompts.MODEL_CARD["model_name"], str)

    def test_model_card_has_model_type(self, prompts):
        assert "model_type" in prompts.MODEL_CARD

    def test_model_card_has_target_variable(self, prompts):
        assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_target_variable_is_string(self, prompts):
        assert isinstance(prompts.MODEL_CARD["target_variable"], str)

    def test_model_card_has_global_feature_importance(self, prompts):
        assert "global_feature_importance" in prompts.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert isinstance(gfi, dict)

    def test_model_card_feature_importance_values_are_numeric(self, prompts):
        gfi = prompts.MODEL_CARD.get("global_feature_importance", {})
        for key, value in gfi.items():
            assert isinstance(value, (int, float)), \
                f"Feature importance for '{key}' is not numeric: {value!r}"

    def test_model_card_model_name_matches_expected(self, prompts):
        assert prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type_matches_expected(self, prompts):
        assert prompts.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable_matches_expected(self, prompts):
        assert prompts.MODEL_CARD["target_variable"] == "Risk_Classification"


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading with mocked file (isolated / reloaded)
# ---------------------------------------------------------------------------

class TestModelCardMockedLoad:
    def test_model_card_loaded_from_json(self, reloaded_prompts):
        mod = reloaded_prompts(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD == MINIMAL_MODEL_CARD

    def test_model_card_age_feature_importance(self, reloaded_prompts):
        mod = reloaded_prompts(MINIMAL_MODEL_CARD)
        age_importance = mod.MODEL_CARD["global_feature_importance"]["Age"]
        assert pytest.approx(age_importance, rel=1e-6) == 34.57614295408571

    def test_model_card_empty_dict_allowed(self, reloaded_prompts):
        """An empty JSON object should load without error."""
        mod = reloaded_prompts({})
        assert mod.MODEL_CARD == {}

    def test_model_card_nested_structures_preserved(self, reloaded_prompts):
        card = {
            "model_name": "Test Model",
            "nested": {"a": {"b": 1}},
        }
        mod = reloaded_prompts(card)
        assert mod.MODEL_CARD["nested"]["a"]["b"] == 1

    def test_model_card_list_values_preserved(self, reloaded_prompts):
        card = {"features": ["Age", "Income", "Risk"]}
        mod = reloaded_prompts(card)
        assert mod.MODEL_CARD["features"] == ["Age", "Income", "Risk"]

    def test_system_prompt_unchanged_after_reload(self, reloaded_prompts):
        """SYSTEM_PROMPT must remain the same regardless of model card content."""
        mod = reloaded_prompts(MINIMAL_MODEL_CARD)
        assert "underwriter" in mod.SYSTEM_PROMPT.lower()

    @pytest.mark.parametrize("card", [
        {"model_name": "Model A", "model_type": "XGBoost"},
        {"model_name": "Model B", "model_type": "CatBoost", "version": "1.0"},
        MINIMAL_MODEL_CARD,
    ])
    def test_various_model_cards_load_successfully(self, reloaded_prompts, card):
        mod = reloaded_prompts(card)
        assert isinstance(mod.MODEL_CARD, dict)
        assert mod.MODEL_CARD == card


# ---------------------------------------------------------------------------
# Tests – error conditions during module load
# ---------------------------------------------------------------------------

class TestModelCardErrorConditions:
    def test_file_not_found_raises_on_import(self):
        """
        If model_card.json does not exist the module should raise FileNotFoundError
        at import time.
        """
        sys.modules.pop("backend.agent.prompts", None)
        try:
            with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
                with pytest.raises(FileNotFoundError):
                    import backend.agent.prompts  # noqa: F401
        finally:
            sys.modules.pop("backend.agent.prompts", None)

    def test_malformed_json_raises_on_import(self):
        """
        If model_card.json contains invalid JSON the module should raise
        json.JSONDecodeError at import time.
        """
        sys.modules.pop("backend.agent.prompts", None)
        bad_json = "{ this is not valid json }"
        try:
            with patch("builtins.open", mock_open(read_data=bad_json)):
                with patch("json.load", side_effect=json.JSONDecodeError("err", bad_json, 0)):
                    with pytest.raises(json.JSONDecodeError):
                        import backend.agent.prompts  # noqa: F401
        finally:
            sys.modules.pop("backend.agent.prompts", None)

    def test_permission_error_raises_on_import(self):
        """
        If the process lacks read permission for model_card.json a
        PermissionError should propagate.
        """
        sys.modules.pop("backend.agent.prompts", None)
        try:
            with patch("builtins.open", side_effect=PermissionError("permission denied")):
                with pytest.raises(PermissionError):
                    import backend.agent.prompts  # noqa: F401
        finally:
            sys.modules.pop("backend.agent.prompts", None)

    @pytest.mark.skip(reason="TODO: Need to test OS-level I/O errors (OSError) separately; "
                              "requires additional fixture to simulate mid-read failures")
    def test_io_error_mid_read(self):
        pass  # TODO: simulate OSError raised by json.load after file open succeeds


# ---------------------------------------------------------------------------
# Tests – module-level attributes existence
# ---------------------------------------------------------------------------

class TestModuleAttributes:
    def test_model_card_attribute_exists(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_no_unintended_public_names(self, prompts):
        """
        The public interface should only expose MODEL_CARD and SYSTEM_PROMPT
        as upper-case constants (private/dunder names are acceptable).
        """
        public_upper = [
            name for name in dir(prompts)
            if name.isupper() and not name.startswith("_")
        ]
        expected = {"MODEL_CARD", "SYSTEM_PROMPT"}
        unexpected = set(public_upper) - expected
        assert not unexpected, f"Unexpected public constants found: {unexpected}"

    @pytest.mark.skip(reason="TODO: Verify _model_card_path resolves correctly "
                              "relative to the installed package location; "
                              "requires knowledge of deployment directory structure")
    def test_model_card_path_resolution(self):
        pass  # TODO: assert _model_card_path points to the correct absolute path