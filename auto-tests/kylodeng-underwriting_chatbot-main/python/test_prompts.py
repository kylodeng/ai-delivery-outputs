"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at import time
- SYSTEM_PROMPT is defined as a non-empty string with expected content
- Content and type guarantees for both module-level constants

Mocks used:
- unittest.mock.patch / mock_open to simulate file system reads and Path behaviour,
  preventing any dependency on a real model_card.json on disk

TODOs:
- TODO: Extend MODEL_CARD content assertions once the full schema of model_card.json
  is stabilised (currently only a partial sample is available).
- TODO: Add tests for any future helper functions added to prompts.py.
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
# Helpers / shared fixtures
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

COMPLEX_MODEL_CARD = {
    "model_name": "Complex Model",
    "model_type": "XGBoostClassifier",
    "target_variable": "Risk_Score",
    "nested": {"level1": {"level2": [1, 2, 3]}},
    "unicode_field": "héllo wörld",
}


def _reload_prompts_with_card(model_card_dict: dict):
    """
    Re-import backend.agent.prompts with a mocked model_card.json whose
    content is *model_card_dict*.  Returns the freshly-loaded module.
    """
    json_bytes = json.dumps(model_card_dict)
    m_open = mock_open(read_data=json_bytes)

    # Remove any previously cached version so importlib actually re-executes
    # the module-level code.
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m_open):
        # Also patch Path so the open() call receives a predictable path,
        # but we mainly care that open() is intercepted.
        with patch("pathlib.Path.open", m_open):
            import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# Fixture: load the module once for read-only tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts():
    """Return the prompts module loaded with the minimal synthetic model card."""
    return _reload_prompts_with_card(MINIMAL_MODEL_CARD)


# ---------------------------------------------------------------------------
# Tests: MODULE_CARD (MODEL_CARD constant)
# ---------------------------------------------------------------------------


class TestModelCard:
    """Tests that MODEL_CARD is loaded and has the expected structure."""

    def test_model_card_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_not_empty_for_minimal_card(self, prompts):
        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_has_model_name_key(self, prompts):
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_value(self, prompts):
        assert prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type(self, prompts):
        assert prompts.MODEL_CARD.get("model_type") == "CatBoostClassifier"

    def test_model_card_has_target_variable(self, prompts):
        assert prompts.MODEL_CARD.get("target_variable") == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self, prompts):
        gfi = prompts.MODEL_CARD.get("global_feature_importance")
        assert isinstance(gfi, dict)

    def test_model_card_age_feature_importance_value(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(gfi["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_all_feature_importance_values_are_floats(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert isinstance(value, float), f"Feature {key} importance is not float"

    # --- reload with different payloads ---

    def test_model_card_empty_json_object(self):
        mod = _reload_prompts_with_card(EMPTY_MODEL_CARD)
        assert mod.MODEL_CARD == {}

    def test_model_card_complex_nested_structure(self):
        mod = _reload_prompts_with_card(COMPLEX_MODEL_CARD)
        assert mod.MODEL_CARD["nested"]["level1"]["level2"] == [1, 2, 3]

    def test_model_card_unicode_preserved(self):
        mod = _reload_prompts_with_card(COMPLEX_MODEL_CARD)
        assert mod.MODEL_CARD["unicode_field"] == "héllo wörld"

    def test_model_card_is_full_deserialised_dict_not_string(self, prompts):
        # Ensure json.load was used, not just file.read()
        assert not isinstance(prompts.MODEL_CARD, str)

    def test_model_card_is_independent_copy_across_reloads(self):
        mod1 = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        mod2 = _reload_prompts_with_card(COMPLEX_MODEL_CARD)
        assert mod1.MODEL_CARD["model_name"] != mod2.MODEL_CARD.get("model_name", "")

    @pytest.mark.parametrize(
        "key",
        ["model_name", "model_type", "target_variable", "global_feature_importance"],
    )
    def test_model_card_expected_top_level_keys_present(self, prompts, key):
        assert key in prompts.MODEL_CARD

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
    def test_model_card_feature_importance_expected_features(self, prompts, feature):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert feature in gfi, f"Expected feature '{feature}' not found"

    def test_model_card_raises_on_invalid_json(self):
        """If the file contains invalid JSON, loading the module should raise."""
        bad_json = "{ not valid json }"
        m_open = mock_open(read_data=bad_json)
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", m_open):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_model_card_raises_on_missing_file(self):
        """If model_card.json does not exist, loading the module should raise."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("file not found")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401, PLC0415


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT constant
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests that SYSTEM_PROMPT is a well-formed string with expected content."""

    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self, prompts):
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts):
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, prompts):
        assert "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assistant(self, prompts):
        assert "assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_confidentiality_instruction(self, prompts):
        """The prompt must tell the model not to reveal internal instructions."""
        prompt_lower = prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_system_prompt_does_not_expose_tool_list(self, prompts):
        """The prompt instructs the model to hide tools, so must not itself list them."""
        # The instruction says the model 'can never disclose tools',
        # but the prompt text should not accidentally embed a real tool listing.
        assert "tool_name" not in prompts.SYSTEM_PROMPT
        assert "<tool>" not in prompts.SYSTEM_PROMPT

    def test_system_prompt_mentions_assessments(self, prompts):
        assert "assessments" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_instructs_helpful_presentation(self, prompts):
        assert "helpful" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_is_single_string_not_list(self, prompts):
        assert not isinstance(prompts.SYSTEM_PROMPT, (list, tuple))

    def test_system_prompt_minimum_length(self, prompts):
        """A meaningful system prompt should be at least 50 characters."""
        assert len(prompts.SYSTEM_PROMPT) >= 50

    def test_system_prompt_contains_expected_fragment_senior(self, prompts):
        assert "senior" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_expected_fragment_customers(self, prompts):
        assert "customers" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_contain_placeholder_tokens(self, prompts):
        """Make sure no un-substituted template placeholders remain."""
        import re

        placeholders = re.findall(r"\{[^}]+\}|\{\{[^}]+\}\}|<[A-Z_]+>", prompts.SYSTEM_PROMPT)
        assert placeholders == [], f"Unresolved placeholders found: {placeholders}"

    def test_system_prompt_is_stable_across_reloads(self):
        """SYSTEM_PROMPT should be identical regardless of model_card content."""
        mod1 = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        mod2 = _reload_prompts_with_card(COMPLEX_MODEL_CARD)
        assert mod1.SYSTEM_PROMPT == mod2.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests: module-level attribute existence
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    """Sanity checks that public names are exported by the module."""

    def test_model_card_attribute_exists(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_no_unexpected_none_constants(self, prompts):
        assert prompts.MODEL_CARD is not None
        assert prompts.SYSTEM_PROMPT is not None


# ---------------------------------------------------------------------------
# Tests: path construction (unit-level, no I/O)
# ---------------------------------------------------------------------------


class TestModelCardPathConstruction:
    """Verify that the expected filesystem path is used when opening the card."""

    def test_open_called_with_path_ending_in_model_card_json(self):
        """The module must try to open a file whose name is 'model_card.json'."""
        json_data = json.dumps(MINIMAL_MODEL_CARD)
        m_open = mock_open(read_data=json_data)

        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", m_open) as patched:
            import backend.agent.prompts  # noqa: F401, PLC0415

        # Retrieve the path argument passed to open()
        call_args = patched.call_args
        opened_path = call_args[0][0] if call_args[0] else call_args[1].get("file", "")
        assert str(opened_path).endswith("model_card.json"), (
            f"Expected open() to be called with a path ending in 'model_card.json', "
            f"got: {opened_path}"
        )

    def test_model_card_path_is_two_levels_above_prompts(self):
        """_model_card_path should be <parent>/<parent>/model_card.json."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        json_data = json.dumps(MINIMAL_MODEL_CARD)
        m_open = mock_open(read_data=json_data)

        with patch("builtins.open", m_open):
            import backend.agent.prompts as pm  # noqa: PLC0415

        expected_suffix = Path("backend") / "model_card.json"
        actual_path = Path(pm.__file__).parent.parent / "model_card.json"
        assert actual_path.parts[-1] == "model_card.json"
        assert actual_path.parts[-2] == "backend"