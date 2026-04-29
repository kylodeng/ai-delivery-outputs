"""
Test module for backend/agent/prompts.py

What is tested:
    - MODEL_CARD is loaded correctly from model_card.json
    - MODEL_CARD contains expected keys and structure
    - SYSTEM_PROMPT is defined and contains expected content
    - SYSTEM_PROMPT enforces confidentiality constraints (no disclosure of internals)
    - SYSTEM_PROMPT presents the assistant as a helpful underwriting tool
    - Module-level constants are the correct types

Mocks used:
    - unittest.mock.mock_open / patch: used to mock open() and Path resolution
      so that no real filesystem access is required during tests
    - json.load: patched to return controlled synthetic data

TODOs:
    - TODO: Integration test that validates MODEL_CARD schema against a live
      model_card.json once a JSON Schema is formalised for that file.
    - TODO: Test behaviour when model_card.json contains unexpected/extra keys
      once schema validation is added to prompts.py.
"""

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# ---------------------------------------------------------------------------
# Synthetic model card data (derived from provided samples)
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_prompts_with_model_card(model_card_data: dict):
    """
    Re-import backend.agent.prompts with a mocked file open so that
    MODEL_CARD is populated from *model_card_data* instead of the real file.
    """
    module_name = "backend.agent.prompts"

    # Remove cached module so importlib reloads it fresh
    sys.modules.pop(module_name, None)
    # Also remove any parent package stubs we may have added
    for key in list(sys.modules.keys()):
        if key.startswith("backend.agent") or key == "backend":
            sys.modules.pop(key, None)

    json_str = json.dumps(model_card_data)

    with patch("builtins.open", mock_open(read_data=json_str)):
        with patch("json.load", return_value=model_card_data):
            # Ensure parent packages exist as namespace packages
            if "backend" not in sys.modules:
                sys.modules["backend"] = types.ModuleType("backend")
            if "backend.agent" not in sys.modules:
                sys.modules["backend.agent"] = types.ModuleType("backend.agent")

            module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts_module():
    """Load the prompts module once per test-session using synthetic data."""
    return _reload_prompts_with_model_card(SYNTHETIC_MODEL_CARD)


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests that MODEL_CARD is loaded and has the expected structure."""

    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_not_empty(self, prompts_module):
        assert len(prompts_module.MODEL_CARD) > 0

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
        importance = prompts_module.MODEL_CARD["global_feature_importance"]
        assert feature in importance
        assert pytest.approx(importance[feature], rel=1e-6) == expected_value

    def test_global_feature_importance_values_are_floats(self, prompts_module):
        importance = prompts_module.MODEL_CARD["global_feature_importance"]
        for key, value in importance.items():
            assert isinstance(value, float), f"Feature {key!r} importance is not a float"

    def test_global_feature_importance_values_are_positive(self, prompts_module):
        importance = prompts_module.MODEL_CARD["global_feature_importance"]
        for key, value in importance.items():
            assert value > 0, f"Feature {key!r} importance should be positive"

    def test_age_has_highest_importance(self, prompts_module):
        importance = prompts_module.MODEL_CARD["global_feature_importance"]
        max_feature = max(importance, key=importance.__getitem__)
        assert max_feature == "Age"


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD error conditions
# ---------------------------------------------------------------------------


class TestModelCardErrorConditions:
    """Tests behaviour when model_card.json is missing or malformed."""

    def test_missing_model_card_raises_file_not_found(self):
        """
        If model_card.json does not exist the module import must raise
        FileNotFoundError (or an OSError subclass).
        """
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=FileNotFoundError("model_card.json not found")):
            with pytest.raises(FileNotFoundError):
                if "backend" not in sys.modules:
                    sys.modules["backend"] = types.ModuleType("backend")
                if "backend.agent" not in sys.modules:
                    sys.modules["backend.agent"] = types.ModuleType("backend.agent")
                importlib.import_module(module_name)

        # Cleanup so other tests are unaffected
        sys.modules.pop(module_name, None)

    def test_malformed_json_raises_json_decode_error(self):
        """
        If model_card.json contains invalid JSON a JSONDecodeError must propagate.
        """
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        bad_json = "{ not valid json !!!"

        with patch("builtins.open", mock_open(read_data=bad_json)):
            with pytest.raises(json.JSONDecodeError):
                if "backend" not in sys.modules:
                    sys.modules["backend"] = types.ModuleType("backend")
                if "backend.agent" not in sys.modules:
                    sys.modules["backend.agent"] = types.ModuleType("backend.agent")
                importlib.import_module(module_name)

        sys.modules.pop(module_name, None)

    def test_empty_json_object_loads_without_error(self):
        """An empty JSON object {} is technically valid; module should load."""
        module = _reload_prompts_with_model_card({})
        assert isinstance(module.MODEL_CARD, dict)
        assert module.MODEL_CARD == {}

    def test_model_card_with_extra_keys_does_not_raise(self):
        """Extra keys in model_card.json should not cause an error."""
        data = {**SYNTHETIC_MODEL_CARD, "extra_key": "extra_value"}
        module = _reload_prompts_with_model_card(data)
        assert "extra_key" in module.MODEL_CARD

    def test_model_card_with_nested_structure_loads_correctly(self):
        """Deeply nested structures should be preserved as-is."""
        data = {
            "model_name": "Test",
            "nested": {"level1": {"level2": [1, 2, 3]}},
        }
        module = _reload_prompts_with_model_card(data)
        assert module.MODEL_CARD["nested"]["level1"]["level2"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT content
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests that SYSTEM_PROMPT is correctly defined with required content."""

    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts_module):
        assert prompts_module.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_minimum_length(self, prompts_module):
        """Prompt should be substantive – at least 100 characters."""
        assert len(prompts_module.SYSTEM_PROMPT) >= 100

    def test_system_prompt_mentions_underwriting(self, prompts_module):
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower() or \
               "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assistant(self, prompts_module):
        assert "assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_senior_underwriting_assistant(self, prompts_module):
        assert "senior underwriting assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_references_underwriter_audience(self, prompts_module):
        """Prompt must state it is talking to an underwriter."""
        assert "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts_module):
        assert "assessments" in prompts_module.SYSTEM_PROMPT.lower() or \
               "assessment" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_gathering_information(self, prompts_module):
        assert "gathering information" in prompts_module.SYSTEM_PROMPT.lower() or \
               "gather" in prompts_module.SYSTEM_PROMPT.lower()

    # -- Confidentiality constraints --

    def test_system_prompt_prohibits_disclosure_of_system_instructions(self, prompts_module):
        """Prompt must instruct the model not to reveal system instructions."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower or "never" in prompt_lower

    def test_system_prompt_references_internal_instructions_confidentiality(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "system" in prompt_lower or "internal" in prompt_lower

    def test_system_prompt_prohibits_tool_disclosure(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "tools" in prompt_lower

    def test_system_prompt_does_not_contain_actual_tool_names(self, prompts_module):
        """
        The system prompt itself should not inadvertently list real internal
        tool names — it should only reference 'tools' generically.
        """
        # If specific internal tool names were added in future, they should not
        # appear verbatim in the prompt exposed to end users.
        # Currently just ensure no Python built-in introspection leakage.
        assert "__import__" not in prompts_module.SYSTEM_PROMPT
        assert "os.system" not in prompts_module.SYSTEM_PROMPT

    # -- Tone and presentation --

    def test_system_prompt_describes_helpful_assistant(self, prompts_module):
        assert "helpful" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_answering_questions(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "answer questions" in prompt_lower or "answer" in prompt_lower

    def test_system_prompt_no_trailing_whitespace_on_lines(self, prompts_module):
        """Each logical sentence should not have extraneous trailing whitespace."""
        lines = prompts_module.SYSTEM_PROMPT.splitlines()
        for line in lines:
            assert line == line.rstrip(), f"Line has trailing whitespace: {line!r}"

    @pytest.mark.parametrize("forbidden", ["TODO", "FIXME", "PLACEHOLDER", "XXX"])
    def test_system_prompt_has_no_placeholder_text(self, prompts_module, forbidden):
        assert forbidden not in prompts_module.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests – Module-level constant types
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Sanity checks on all public constants exported by prompts.py."""

    def test_model_card_constant_exists(self, prompts_module):
        assert hasattr(prompts_module, "MODEL_CARD")

    def test_system_prompt_constant_exists(self, prompts_module):
        assert hasattr(prompts_module, "SYSTEM_PROMPT")

    def test_model_card_type(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_system_prompt_type(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_model_card_is_not_none(self, prompts_module):
        assert prompts_module.MODEL_CARD is not None

    def test_system_prompt_is_not_none(self, prompts_module):
        assert prompts_module.SYSTEM_PROMPT is not None

    def test_constants_are_immutable_types_or_dicts(self, prompts_module):
        """
        MODEL_CARD should be a dict; SYSTEM_PROMPT should be a str.
        Neither should be mutable sequences like a list at the top level.
        """
        assert not isinstance(prompts_module.MODEL_CARD, list)
        assert not isinstance(prompts_module.SYSTEM