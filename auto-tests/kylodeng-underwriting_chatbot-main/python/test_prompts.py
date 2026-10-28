"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loading from model_card.json at module import time
- SYSTEM_PROMPT content, type, and key behavioural constraints
- Edge cases around the model card JSON structure
- File path resolution logic

Mocks used:
- unittest.mock.patch / mock_open — used to mock file I/O so no real
  model_card.json is required on disk during the test run
- importlib — used to reload the module under controlled conditions

TODOs:
- TODO: Integration test that verifies the real model_card.json on disk
  parses successfully against a schema (needs schema definition)
- TODO: Test behaviour when model_card.json contains unexpected/extra fields
  once the consuming code that uses MODEL_CARD is implemented
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

UNICODE_MODEL_CARD = {
    "model_name": "Underwriting — Risqué Ünïcödé",
    "model_type": "CatBoostClassifier",
    "target_variable": "Risk_Ünïcödé",
}


def _reload_prompts_with_card(card_dict: dict) -> ModuleType:
    """
    Remove any cached version of the module and reload it with a mocked
    model_card.json whose content is *card_dict*.
    """
    module_name = "backend.agent.prompts"
    # Remove from cache so the module-level code re-executes on import
    sys.modules.pop(module_name, None)

    json_bytes = json.dumps(card_dict)
    m = mock_open(read_data=json_bytes)

    with patch("builtins.open", m):
        module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts_module():
    """
    Load the prompts module once for the session using the minimal synthetic
    model card so tests do not depend on a real file on disk.
    """
    return _reload_prompts_with_card(MINIMAL_MODEL_CARD)


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests that verify the MODEL_CARD constant is populated correctly."""

    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_model_name(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self, prompts_module):
        assert prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_global_feature_importance_present(self, prompts_module):
        assert "global_feature_importance" in prompts_module.MODEL_CARD

    def test_model_card_feature_importance_age(self, prompts_module):
        importance = prompts_module.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(importance["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_feature_importance_all_keys(self, prompts_module):
        expected_keys = {
            "Age",
            "Education_Level",
            "Employment_Status",
            "Nationality",
            "Customer_Segment",
            "Annual_Income",
            "Liquid_Assets",
        }
        importance = prompts_module.MODEL_CARD["global_feature_importance"]
        assert expected_keys.issubset(set(importance.keys()))

    def test_model_card_feature_importance_values_are_floats(self, prompts_module):
        importance = prompts_module.MODEL_CARD["global_feature_importance"]
        for key, value in importance.items():
            assert isinstance(value, float), f"Expected float for key {key}, got {type(value)}"

    def test_model_card_with_empty_json(self):
        """Edge case: model card JSON is an empty object {}."""
        module = _reload_prompts_with_card(EMPTY_MODEL_CARD)
        assert module.MODEL_CARD == {}

    def test_model_card_with_unicode_values(self):
        """Edge case: model card contains unicode characters."""
        module = _reload_prompts_with_card(UNICODE_MODEL_CARD)
        assert module.MODEL_CARD["model_name"] == "Underwriting — Risqué Ünïcödé"

    @pytest.mark.parametrize(
        "card",
        [
            MINIMAL_MODEL_CARD,
            UNICODE_MODEL_CARD,
            {"single_key": "single_value"},
            {"nested": {"a": {"b": {"c": 42}}}},
        ],
        ids=["minimal", "unicode", "single_key", "deeply_nested"],
    )
    def test_model_card_parametrised_variants(self, card):
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD == card


# ---------------------------------------------------------------------------
# Tests – File path resolution
# ---------------------------------------------------------------------------


class TestModelCardPathResolution:
    """Verify the module computes the expected path to model_card.json."""

    def test_open_called_with_model_card_path(self):
        """
        The module must open a file whose name is model_card.json located two
        directories above the prompts.py file (i.e. backend/model_card.json).
        """
        json_bytes = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=json_bytes)

        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", m) as mocked_open:
            importlib.import_module(module_name)

        # Retrieve the path that open() was called with
        call_args = mocked_open.call_args
        opened_path = call_args[0][0]  # first positional argument

        # Accept both str and Path objects
        opened_path_obj = Path(opened_path)
        assert opened_path_obj.name == "model_card.json"

    def test_model_card_path_is_two_levels_up_from_prompts(self):
        """
        _model_card_path should equal <backend_dir>/model_card.json, i.e.
        Path(__file__).parent.parent / 'model_card.json'.
        """
        json_bytes = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=json_bytes)

        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", m):
            module = importlib.import_module(module_name)

        prompts_file = Path(module.__file__)
        expected_path = prompts_file.parent.parent / "model_card.json"
        assert module._model_card_path == expected_path


# ---------------------------------------------------------------------------
# Tests – File I/O error conditions
# ---------------------------------------------------------------------------


class TestModelCardFileErrors:
    """Tests that verify behaviour when the model_card.json cannot be read."""

    def test_file_not_found_raises_on_import(self):
        """If model_card.json is missing the module should raise FileNotFoundError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                importlib.import_module(module_name)

    def test_invalid_json_raises_json_decode_error(self):
        """Malformed JSON in model_card.json must raise json.JSONDecodeError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module(module_name)

    def test_permission_error_propagates(self):
        """PermissionError on open() must propagate out of the module."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=PermissionError("access denied")):
            with pytest.raises(PermissionError):
                importlib.import_module(module_name)

    def test_io_error_propagates(self):
        """Generic IOError must propagate."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=IOError("disk error")):
            with pytest.raises(IOError):
                importlib.import_module(module_name)

    def test_empty_file_raises_json_decode_error(self):
        """An empty file is not valid JSON and must raise json.JSONDecodeError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT constant
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests that verify the SYSTEM_PROMPT string meets behavioural requirements."""

    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self, prompts_module):
        assert len(prompts_module.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts_module):
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, prompts_module):
        assert "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_senior_underwriting_assistant(self, prompts_module):
        assert "senior underwriting assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_prohibits_disclosing_instructions(self, prompts_module):
        """The prompt must instruct the model never to reveal system instructions."""
        lowered = prompts_module.SYSTEM_PROMPT.lower()
        assert "never" in lowered or "cannot" in lowered or "must not" in lowered

    def test_system_prompt_prohibits_revealing_tools(self, prompts_module):
        """The prompt must mention not revealing tools."""
        assert "tools" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_disclose_or_reveal(self, prompts_module):
        lowered = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in lowered or "reveal" in lowered

    def test_system_prompt_mentions_assessments(self, prompts_module):
        assert "assessment" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_helpful_assistant(self, prompts_module):
        assert "helpful assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_contain_placeholder_tokens(self, prompts_module):
        """Ensure there are no un-substituted template placeholders like {var} or <var>."""
        import re

        curly_placeholders = re.findall(r"\{[^}]+\}", prompts_module.SYSTEM_PROMPT)
        assert curly_placeholders == [], f"Found unreplaced placeholders: {curly_placeholders}"

    def test_system_prompt_minimum_length(self, prompts_module):
        """A meaningful system prompt should be at least 100 characters long."""
        assert len(prompts_module.SYSTEM_PROMPT) >= 100

    def test_system_prompt_is_immutable_string_constant(self, prompts_module):
        """SYSTEM_PROMPT must be a str (immutable), not a list or other mutable type."""
        assert not isinstance(prompts_module.SYSTEM_PROMPT, (list, dict, bytearray))

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "internal system instructions",
            "tools you have access to",
        ],
    )
    def test_system_prompt_references_forbidden_disclosure_topics(
        self, prompts_module, forbidden_phrase
    ):
        """
        The prompt explicitly names the things the assistant must not disclose,
        so that the language model understands the boundary.
        """
        assert forbidden_phrase in prompts_module.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests – Module-level attributes existence
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    """Smoke tests verifying the public API of the module."""

    def test_model_card_attribute_exists(self, prompts_module):
        assert hasattr(prompts_module, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self, prompts_module):
        assert hasattr(prompts_module, "SYSTEM_PROMPT")

    def test_private_model_card_path_attribute_exists(self, prompts_module):
        assert hasattr(prompts_module, "_model_card_path")

    def test_model_card_path_is_path_instance(self, prompts_module):
        assert isinstance(prompts_module._model_card_path, Path)

    def test_no_unexpected_public_callables(self, prompts_module):
        """
        The module is expected to expose only constants, not functions.
        If new functions are added this test will remind the author to add tests.
        """
        import inspect

        public_callables = [
            name
            for name, obj in vars(prompts_module).items()
            if not name.startswith("_") and callable(obj) and not isinstance(obj, type)
        ]
        # Currently there should be no public functions
        assert public_callables == [], (
            f"Unexpected public callables found: {public_callables}. "
            "Please add tests for them."
        )


# ---------------------------------------------------------------------------
# Skipped / stub tests for future work
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: schema definition for model_card.json not yet available")
def test_model_card_conforms_to_schema():
    """
    TODO: Once a JSON schema for model_card.json is defined, validate that the
    loaded MODEL_CARD dict conforms to it (e.g. via jsonschema.validate).
    """
    pass


@pytest.mark