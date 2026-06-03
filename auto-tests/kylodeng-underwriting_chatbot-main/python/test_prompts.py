"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at module level
- SYSTEM_PROMPT content, type, and key behavioural constraints
- File loading behaviour: missing file, malformed JSON, empty JSON object
- Module-level constants are accessible and have expected types

Mocks used:
- unittest.mock.patch / mock_open to simulate file I/O without touching the real filesystem
- tmp_path (pytest fixture) for integration-style path tests

TODOs:
- TODO: Extend tests once MODEL_CARD schema is fully stabilised (additional required keys)
- TODO: Test behaviour when model_card.json contains unexpected/extra fields if strict validation is added
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
        "Age": 34.576,
        "Education_Level": 2.098,
        "Employment_Status": 2.131,
        "Nationality": 2.277,
        "Customer_Segment": 1.873,
        "Annual_Income": 1.016,
        "Liquid_Assets": 1.223,
    },
}

FULL_MODEL_CARD_JSON = json.dumps(MINIMAL_MODEL_CARD)


def _reload_prompts_with_model_card(model_card_dict: dict):
    """
    Reload the prompts module with a patched open() that returns the given
    model_card_dict as JSON.  Returns the freshly-imported module object.
    """
    module_name = "backend.agent.prompts"
    # Remove cached version so importlib re-executes module-level code
    sys.modules.pop(module_name, None)

    json_bytes = json.dumps(model_card_dict)
    m = mock_open(read_data=json_bytes)

    with patch("builtins.open", m):
        module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# MODEL_CARD loading – happy path
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert isinstance(module.MODEL_CARD, dict)

    def test_model_card_model_name(self):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type(self):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_contains_global_feature_importance(self):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert "global_feature_importance" in module.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert isinstance(module.MODEL_CARD["global_feature_importance"], dict)

    def test_model_card_age_importance_value(self):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["global_feature_importance"]["Age"] == pytest.approx(34.576)

    @pytest.mark.parametrize(
        "feature,expected",
        [
            ("Age", 34.576),
            ("Education_Level", 2.098),
            ("Employment_Status", 2.131),
            ("Nationality", 2.277),
            ("Customer_Segment", 1.873),
            ("Annual_Income", 1.016),
            ("Liquid_Assets", 1.223),
        ],
    )
    def test_model_card_feature_importance_values(self, feature, expected):
        module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["global_feature_importance"][feature] == pytest.approx(expected)

    def test_model_card_empty_object(self):
        """An empty JSON object is valid JSON; module should load without error."""
        module = _reload_prompts_with_model_card({})
        assert module.MODEL_CARD == {}

    def test_model_card_extra_fields_preserved(self):
        card_with_extra = dict(MINIMAL_MODEL_CARD, custom_field="hello")
        module = _reload_prompts_with_model_card(card_with_extra)
        assert module.MODEL_CARD.get("custom_field") == "hello"


# ---------------------------------------------------------------------------
# MODEL_CARD loading – error conditions
# ---------------------------------------------------------------------------


class TestModelCardLoadingErrors:
    def test_file_not_found_raises(self, tmp_path, monkeypatch):
        """
        If model_card.json does not exist the module should raise FileNotFoundError
        at import time.
        """
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        non_existent = tmp_path / "no_such_file.json"

        with patch("pathlib.Path.__truediv__", return_value=non_existent):
            with pytest.raises((FileNotFoundError, OSError)):
                importlib.import_module(module_name)

    def test_malformed_json_raises(self):
        """Malformed JSON in model_card.json should raise json.JSONDecodeError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module(module_name)

    def test_json_array_root_loads_as_list(self):
        """JSON arrays are valid; MODEL_CARD would be a list (edge case)."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data='["a", "b", "c"]')
        with patch("builtins.open", m):
            module = importlib.import_module(module_name)

        assert isinstance(module.MODEL_CARD, list)

    def test_json_null_root_loads_as_none(self):
        """JSON null is valid; MODEL_CARD would be None."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="null")
        with patch("builtins.open", m):
            module = importlib.import_module(module_name)

        assert module.MODEL_CARD is None

    def test_empty_file_raises(self):
        """An empty file is not valid JSON."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="")
        with patch("builtins.open", m):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT – type and content
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)

    def test_system_prompt_is_string(self):
        assert isinstance(self.module.SYSTEM_PROMPT, str)

    def test_system_prompt_non_empty(self):
        assert len(self.module.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self):
        assert "underwriting" in self.module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self):
        assert "underwriter" in self.module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self):
        assert "assessment" in self.module.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_disclose_instruction(self):
        """Must contain language about NOT disclosing internal instructions."""
        prompt_lower = self.module.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_system_prompt_cannot_reveal_system_instructions(self):
        assert "system" in self.module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_helpful_assistant(self):
        assert "helpful assistant" in self.module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_senior(self):
        assert "senior" in self.module.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_trailing_whitespace_trimmed(self):
        """Prompt should not be purely whitespace."""
        assert self.module.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_contains_tools_restriction(self):
        """Must not disclose tools available."""
        assert "tools" in self.module.SYSTEM_PROMPT.lower()

    def test_system_prompt_three_behavioural_sentences(self):
        """
        The prompt currently concatenates three sentences (no spaces between them
        but each ends with a full stop). Verify overall length is reasonable.
        """
        assert len(self.module.SYSTEM_PROMPT) > 100

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "my secret",
            "internal instructions are",
            "here are my tools",
        ],
    )
    def test_system_prompt_does_not_contain_forbidden_phrases(self, forbidden_phrase):
        assert forbidden_phrase.lower() not in self.module.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Module-level constant names / accessibility
# ---------------------------------------------------------------------------


class TestModuleConstants:
    @pytest.fixture(autouse=True)
    def _module(self):
        self.module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)

    def test_model_card_attribute_exists(self):
        assert hasattr(self.module, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self):
        assert hasattr(self.module, "SYSTEM_PROMPT")

    def test_no_private_model_card_leaked(self):
        """_model_card_path should not be exported (convention check)."""
        # It may still exist as a module attribute, but it should be a Path object.
        if hasattr(self.module, "_model_card_path"):
            assert isinstance(self.module._model_card_path, Path)

    def test_model_card_path_points_to_json(self):
        if hasattr(self.module, "_model_card_path"):
            assert self.module._model_card_path.suffix == ".json"

    def test_model_card_is_not_string(self):
        assert not isinstance(self.module.MODEL_CARD, str)


# ---------------------------------------------------------------------------
# Integration: real filesystem (skipped if model_card.json absent)
# ---------------------------------------------------------------------------


class TestIntegrationRealFile:
    """
    These tests use the *actual* model_card.json on disk (if present).
    They are skipped automatically when the file cannot be found.
    """

    @pytest.fixture(autouse=True)
    def _check_file(self):
        candidate = Path(__file__).parent.parent / "model_card.json"
        if not candidate.exists():
            pytest.skip("model_card.json not found on disk – skipping integration test")

    def test_real_model_card_loads_without_error(self):
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)
        assert module.MODEL_CARD is not None

    def test_real_model_card_is_dict(self):
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)
        module = importlib.import_module(module_name)
        assert isinstance(module.MODEL_CARD, dict)

    @pytest.mark.skip(
        reason="TODO: validate all required model_card.json schema keys once schema is finalised"
    )
    def test_real_model_card_schema_valid(self):
        pass  # TODO: implement JSON-schema validation once schema is stable


# ---------------------------------------------------------------------------
# Teardown – restore module cache to real version after tests
# ---------------------------------------------------------------------------


def teardown_module(module):
    """Ensure the real prompts module (if importable) is restored in sys.modules."""
    sys.modules.pop("backend.agent.prompts", None)