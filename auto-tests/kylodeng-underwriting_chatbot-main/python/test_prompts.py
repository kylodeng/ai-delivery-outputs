"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE-LEVEL: MODEL_CARD is loaded correctly from model_card.json at import time
- MODULE-LEVEL: SYSTEM_PROMPT is defined with expected content and properties
- Happy path: MODEL_CARD contains expected keys and structure from the JSON fixture
- Edge cases: SYSTEM_PROMPT content constraints (non-disclosure, role description)
- Error conditions: behaviour when model_card.json is missing or malformed
- Boundary values: empty / minimal JSON for model_card.json

Mocks used:
- unittest.mock.patch / mock_open  — to simulate model_card.json reads
- tmp_path (pytest fixture)        — to provide a real temporary file for integration-style tests

TODOs:
- TODO: Extend MODEL_CARD structural tests once the full model_card.json schema is confirmed
- TODO: Test any future helper functions added to prompts.py
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
    Reload the prompts module after patching the filesystem so that the
    module-level open() call reads *model_card_dict* instead of the real file.
    """
    json_bytes = json.dumps(model_card_dict)
    m = mock_open(read_data=json_bytes)

    # Remove cached module so the top-level code re-executes on import
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m):
        # Patch json.load to parse from the string we supplied
        with patch("json.load", return_value=model_card_dict):
            import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts():
    """Import the real prompts module once for the test session."""
    # The real model_card.json must exist on disk for the module to import.
    # If it does not exist the module itself will raise FileNotFoundError —
    # that behaviour is covered by test_missing_model_card_raises below.
    import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self, prompts):
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts):
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower() or "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessment(self, prompts):
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_non_disclosure_clause(self, prompts):
        """The prompt must instruct the model never to disclose system instructions."""
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in lower or "reveal" in lower or "never" in lower

    def test_system_prompt_helpful_assistant_role(self, prompts):
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "helpful" in lower or "assistant" in lower

    def test_system_prompt_no_leading_trailing_whitespace_issues(self, prompts):
        """Prompt should not start or end with excessive blank lines."""
        assert prompts.SYSTEM_PROMPT == prompts.SYSTEM_PROMPT.strip() or prompts.SYSTEM_PROMPT[0] != "\n"

    def test_system_prompt_contains_no_placeholder_tokens(self, prompts):
        """Ensure no unfilled template placeholders remain."""
        assert "{" not in prompts.SYSTEM_PROMPT
        assert "}" not in prompts.SYSTEM_PROMPT

    def test_system_prompt_does_not_disclose_tools(self, prompts):
        lower = prompts.SYSTEM_PROMPT.lower()
        # The prompt explicitly states it *cannot* disclose tools — it should
        # not accidentally enumerate them either.
        assert "tool_name" not in lower
        assert "function_call" not in lower

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "ignore previous instructions",
            "ignore all instructions",
            "disregard",
        ],
    )
    def test_system_prompt_no_injection_phrases(self, prompts, forbidden_phrase):
        assert forbidden_phrase not in prompts.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# MODEL_CARD tests (using the real file)
# ---------------------------------------------------------------------------


class TestModelCard:
    def test_model_card_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_is_non_empty(self, prompts):
        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_has_model_name(self, prompts):
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_is_string(self, prompts):
        assert isinstance(prompts.MODEL_CARD["model_name"], str)
        assert prompts.MODEL_CARD["model_name"].strip() != ""

    def test_model_card_has_model_type(self, prompts):
        assert "model_type" in prompts.MODEL_CARD

    def test_model_card_has_target_variable(self, prompts):
        assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_target_variable_value(self, prompts):
        assert prompts.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_has_global_feature_importance(self, prompts):
        assert "global_feature_importance" in prompts.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert isinstance(gfi, dict)

    def test_model_card_feature_importance_values_are_numeric(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert isinstance(value, (int, float)), f"Feature '{key}' has non-numeric importance: {value}"

    def test_model_card_feature_importance_values_non_negative(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert value >= 0, f"Feature '{key}' has negative importance: {value}"

    def test_model_card_age_feature_present(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert "Age" in gfi

    def test_model_card_age_importance_approximate(self, prompts):
        """Age should be the dominant feature (~34.5) per the synthetic sample."""
        age_importance = prompts.MODEL_CARD["global_feature_importance"]["Age"]
        assert age_importance > 10, "Age importance unexpectedly low"

    @pytest.mark.parametrize(
        "expected_feature",
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
    def test_model_card_expected_features_present(self, prompts, expected_feature):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert expected_feature in gfi, f"Expected feature '{expected_feature}' not found in global_feature_importance"


# ---------------------------------------------------------------------------
# Module reload / file-not-found / malformed JSON error condition tests
# ---------------------------------------------------------------------------


class TestModelCardLoadErrors:
    def _remove_cached_prompts(self):
        for key in list(sys.modules.keys()):
            if "prompts" in key and "agent" in key:
                del sys.modules[key]

    def test_missing_model_card_raises_file_not_found(self, tmp_path, monkeypatch):
        """If model_card.json does not exist the module should raise FileNotFoundError."""
        self._remove_cached_prompts()

        # Point the Path resolution to a non-existent file
        non_existent = tmp_path / "does_not_exist.json"

        with patch("pathlib.Path.open", side_effect=FileNotFoundError("No such file")):
            with patch("builtins.open", side_effect=FileNotFoundError("No such file")):
                with pytest.raises((FileNotFoundError, Exception)):
                    # Force re-import
                    import backend.agent.prompts  # noqa: PLC0415, F401

    def test_malformed_json_raises_json_decode_error(self, tmp_path, monkeypatch):
        """If model_card.json contains invalid JSON a JSONDecodeError should propagate."""
        self._remove_cached_prompts()

        malformed = "{ this is not valid json ..."
        m = mock_open(read_data=malformed)

        with patch("builtins.open", m):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_empty_json_object_loads_as_empty_dict(self, monkeypatch):
        """An empty JSON object {} should load without error."""
        self._remove_cached_prompts()

        empty_card: dict = {}
        with patch("builtins.open", mock_open(read_data="{}")):
            with patch("json.load", return_value=empty_card):
                import backend.agent.prompts as reloaded  # noqa: PLC0415

        assert isinstance(reloaded.MODEL_CARD, dict)
        # Empty dict is acceptable — no KeyError should have been raised
        self._remove_cached_prompts()

    def test_model_card_with_minimal_valid_data(self, monkeypatch):
        """A minimal but valid model card should load and expose expected keys."""
        self._remove_cached_prompts()

        with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                import backend.agent.prompts as reloaded  # noqa: PLC0415

        assert reloaded.MODEL_CARD["model_name"] == "Underwriting Risk Classification"
        assert reloaded.MODEL_CARD["model_type"] == "CatBoostClassifier"
        self._remove_cached_prompts()

    def test_model_card_with_null_values(self, monkeypatch):
        """JSON nulls should be represented as None in Python without error."""
        self._remove_cached_prompts()

        null_card = {"model_name": None, "model_type": None, "target_variable": None}
        with patch("builtins.open", mock_open(read_data=json.dumps(null_card))):
            with patch("json.load", return_value=null_card):
                import backend.agent.prompts as reloaded  # noqa: PLC0415

        assert reloaded.MODEL_CARD["model_name"] is None
        self._remove_cached_prompts()

    def test_model_card_with_nested_structures(self, monkeypatch):
        """Deeply nested structures should be preserved as-is."""
        self._remove_cached_prompts()

        nested_card = {
            "model_name": "Test",
            "nested": {"level1": {"level2": [1, 2, 3]}},
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(nested_card))):
            with patch("json.load", return_value=nested_card):
                import backend.agent.prompts as reloaded  # noqa: PLC0415

        assert reloaded.MODEL_CARD["nested"]["level1"]["level2"] == [1, 2, 3]
        self._remove_cached_prompts()


# ---------------------------------------------------------------------------
# Integration-style test: use a real temporary file
# ---------------------------------------------------------------------------


class TestModelCardIntegration:
    def _remove_cached_prompts(self):
        for key in list(sys.modules.keys()):
            if "prompts" in key and "agent" in key:
                del sys.modules[key]

    def test_real_file_round_trip(self, tmp_path, monkeypatch):
        """Write a known JSON to a temp file, then verify the module reads it correctly."""
        self._remove_cached_prompts()

        card_file = tmp_path / "model_card.json"
        card_file.write_text(json.dumps(MINIMAL_MODEL_CARD), encoding="utf-8")

        # Patch open so it returns our temp file content
        original_open = builtins.open

        def patched_open(file, *args, **kwargs):
            # Intercept only the model_card.json open call
            if "model_card.json" in str(file):
                return original_open(card_file, *args, **kwargs)
            return original_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=patched_open):
            import backend.agent.prompts as reloaded  # noqa: PLC0415

        assert reloaded.MODEL_CARD["model_name"] == MINIMAL_MODEL_CARD["model_name"]
        assert reloaded.MODEL_CARD["model_type"] == MINIMAL_MODEL_CARD["model_type"]
        gfi = reloaded.MODEL_CARD["global_feature_importance"]
        assert abs(gfi["Age"] - 34.57614295408571) < 1e-6
        self._remove_cached_prompts()


# ---------------------------------------------------------------------------
# Parametrised SYSTEM_PROMPT phrase coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "required_phrase",
    [
        "underwriter",
        "assess",
        "assistant",
    ],
)
def test_system_prompt_contains_required_phrase(prompts, required_phrase):
    assert required_phrase in prompts.SYSTEM_PROMPT.lower(), (
        f"Expected phrase '{required_phrase}' not found in SYSTEM_PROMPT"
    )


# ---------------------------------------------------------------------------
# Skipped / TODO stubs
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: requires full model_card.json schema definition to validate all fields")
def test_model_card_full_schema_validation():
    """TODO: validate every expected top-level key once the full schema is confirmed."""
    pass


@pytest.mark.skip(reason="TODO: requires knowledge of additional prompt template variables if any are introduced")
def test_system_prompt_template_rendering():
    """TODO: if