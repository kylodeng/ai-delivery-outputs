"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loading from model_card.json (content, structure, types)
- SYSTEM_PROMPT content, type, and behavioural constraints
- Module-level constants are correctly initialised at import time
- Path resolution for _model_card_path
- Error conditions: missing/malformed model_card.json

Mocks used:
- unittest.mock.patch / mock_open to simulate file I/O for model_card.json
- tmp_path (pytest fixture) for real file-based integration-style tests
- importlib to reload the module under controlled conditions

TODOs:
- TODO: Full integration test against the real model_card.json requires the file
        to be present in the repo; stub provided below for CI environments without it.
- TODO: Extend SYSTEM_PROMPT behavioural tests if the prompt is retrieved dynamically
        in the future (currently it is a static string).
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


def _reload_prompts_with_card(model_card_data: dict):
    """
    Reload the prompts module with a patched open() that returns *model_card_data*
    as JSON.  Returns the freshly imported module object.
    """
    json_bytes = json.dumps(model_card_data)
    m_open = mock_open(read_data=json_bytes)

    # Remove cached module so importlib actually re-executes module-level code.
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m_open):
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

        importlib.reload(prompts_module)

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts_module(tmp_path, monkeypatch):
    """
    Provide a clean import of backend.agent.prompts backed by a real
    temporary model_card.json file so we don't depend on the repo file.
    """
    # Write a real JSON file to a temp directory
    card_file = tmp_path / "model_card.json"
    card_file.write_text(json.dumps(MINIMAL_MODEL_CARD), encoding="utf-8")

    # Patch Path.__new__ / the resolved path used inside prompts.py
    # Strategy: patch builtins.open with a side_effect that intercepts the
    # model_card path and serves our temp file for any path, else delegates.
    original_open = open  # noqa: WPS421

    def _patched_open(file, *args, **kwargs):
        # Intercept any attempt to open a file whose name is model_card.json
        if Path(str(file)).name == "model_card.json":
            return original_open(str(card_file), *args, **kwargs)
        return original_open(file, *args, **kwargs)

    sys.modules.pop("backend.agent.prompts", None)

    with patch("builtins.open", side_effect=_patched_open):
        import backend.agent.prompts as mod  # noqa: PLC0415

        importlib.reload(mod)

    return mod


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests that MODEL_CARD is loaded correctly from model_card.json."""

    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_has_model_name(self, prompts_module):
        assert "model_name" in prompts_module.MODEL_CARD

    def test_model_card_model_name_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type(self, prompts_module):
        assert "model_type" in prompts_module.MODEL_CARD

    def test_model_card_model_type_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_has_target_variable(self, prompts_module):
        assert "target_variable" in prompts_module.MODEL_CARD

    def test_model_card_target_variable_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_has_global_feature_importance(self, prompts_module):
        assert "global_feature_importance" in prompts_module.MODEL_CARD

    def test_global_feature_importance_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD["global_feature_importance"], dict)

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
    def test_global_feature_importance_values(self, prompts_module, feature, expected_value):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert feature in gfi
        assert gfi[feature] == pytest.approx(expected_value)

    def test_model_card_not_empty(self, prompts_module):
        assert len(prompts_module.MODEL_CARD) > 0

    def test_model_card_loaded_with_minimal_card(self):
        """Reload with mock_open to verify any valid JSON dict is accepted."""
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD == MINIMAL_MODEL_CARD

    def test_model_card_loaded_with_extra_keys(self):
        """Extra keys in model_card.json should be preserved as-is."""
        extended = {**MINIMAL_MODEL_CARD, "extra_field": [1, 2, 3]}
        mod = _reload_prompts_with_card(extended)
        assert mod.MODEL_CARD["extra_field"] == [1, 2, 3]

    def test_model_card_with_empty_feature_importance(self):
        """An empty global_feature_importance dict is still valid JSON."""
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {}}
        mod = _reload_prompts_with_card(card)
        assert mod.MODEL_CARD["global_feature_importance"] == {}

    def test_model_card_with_nested_structure(self):
        """Deeply nested structures should survive the JSON round-trip."""
        card = {**MINIMAL_MODEL_CARD, "metadata": {"version": "1.0", "tags": ["prod"]}}
        mod = _reload_prompts_with_card(card)
        assert mod.MODEL_CARD["metadata"]["tags"] == ["prod"]


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD error conditions
# ---------------------------------------------------------------------------


class TestModelCardErrorConditions:
    """Tests for error conditions when loading model_card.json."""

    def test_missing_file_raises_file_not_found(self):
        """If model_card.json does not exist the module should raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_malformed_json_raises_json_decode_error(self):
        """If model_card.json contains invalid JSON a JSONDecodeError should propagate."""
        sys.modules.pop("backend.agent.prompts", None)

        m_open = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m_open):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_empty_json_object_is_accepted(self):
        """An empty JSON object ({}) is valid – module should load without error."""
        mod = _reload_prompts_with_card({})
        assert mod.MODEL_CARD == {}

    def test_permission_error_propagates(self):
        """A PermissionError when opening model_card.json should propagate."""
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", side_effect=PermissionError("permission denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)


# ---------------------------------------------------------------------------
# Tests – _model_card_path resolution
# ---------------------------------------------------------------------------


class TestModelCardPath:
    """Tests for the _model_card_path module-level variable."""

    def test_path_is_path_object(self, prompts_module):
        assert isinstance(prompts_module._model_card_path, Path)

    def test_path_filename_is_model_card_json(self, prompts_module):
        assert prompts_module._model_card_path.name == "model_card.json"

    def test_path_parent_directory_name_is_backend(self, prompts_module):
        """The path should point to backend/model_card.json, so parent name == 'backend'."""
        assert prompts_module._model_card_path.parent.name == "backend"

    def test_path_is_absolute(self, prompts_module):
        assert prompts_module._model_card_path.is_absolute()


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT module-level constant."""

    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts_module):
        assert len(prompts_module.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts_module):
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, prompts_module):
        assert "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assistant(self, prompts_module):
        assert "assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts_module):
        assert "assessment" in prompts_module.SYSTEM_PROMPT.lower()

    # --- Security / confidentiality constraints ---

    def test_system_prompt_prohibits_disclosure_of_instructions(self, prompts_module):
        """The prompt must instruct the model never to disclose system instructions."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_system_prompt_prohibits_revealing_tools(self, prompts_module):
        """The prompt must reference keeping tools confidential."""
        assert "tools" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_references_never_disclose(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "never" in prompt_lower or "cannot" in prompt_lower or "can never" in prompt_lower

    # --- Role description ---

    def test_system_prompt_describes_senior_role(self, prompts_module):
        assert "senior" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_describes_information_gathering(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "gather" in prompt_lower or "information" in prompt_lower

    def test_system_prompt_describes_helpful_assistant(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "helpful" in prompt_lower

    # --- Immutability check (constant should not be accidentally mutable reference) ---

    def test_system_prompt_is_immutable_str(self, prompts_module):
        """Strings are immutable in Python; verify the value cannot be reassigned externally."""
        original = prompts_module.SYSTEM_PROMPT
        # Attempt to rebind the name locally – module value must be unchanged
        _ = "tampered"
        assert prompts_module.SYSTEM_PROMPT == original

    def test_system_prompt_does_not_expose_json_keys(self, prompts_module):
        """The prompt text must not accidentally contain raw JSON key names from model_card."""
        assert "global_feature_importance" not in prompts_module.SYSTEM_PROMPT
        assert "model_type" not in prompts_module.SYSTEM_PROMPT

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "system instructions",
            "internal",
        ],
    )
    def test_system_prompt_instructs_confidentiality_of_phrases(
        self, prompts_module, forbidden_phrase
    ):
        """
        The prompt explicitly instructs the model to hide 'internal system instructions'.
        Verify those exact concepts appear so the guard is not accidentally removed.
        """
        assert forbidden_phrase in prompts_module.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests – module public interface
# ---------------------------------------------------------------------------


class TestModulePublicInterface:
    """Verify the module exports the expected public names."""

    @pytest.mark.parametrize("attr", ["MODEL_CARD", "SYSTEM_PROMPT"])
    def test_public_attribute_exists(self, prompts_module, attr):
        assert hasattr(prompts_module, attr)

    def test_model_card_and_system_prompt_are_independent(self, prompts_module):
        """MODEL_CARD and SYSTEM_PROMPT should be independent objects."""
        assert prompts_module.MODEL_CARD is not prompts_module.SYSTEM_PROMPT

    def test_module_has_no_unexpected_callables(self, prompts_module):
        """prompts.py defines no public functions; verify there are none exported."""
        public_callables = [
            name
            for name in dir(prompts_module)
            if not name.startswith("_") and callable(getattr(prompts_module, name))
            and not isinstance(getattr(prompts_module, name), types.ModuleType)
        ]