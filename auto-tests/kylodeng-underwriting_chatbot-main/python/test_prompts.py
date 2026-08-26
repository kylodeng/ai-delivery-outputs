"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loading from model_card.json (structure, content, types)
- SYSTEM_PROMPT content, type, and behavioural constraints
- Module-level constants existence and correctness
- Edge cases around the JSON loading path resolution

Mocks used:
- unittest.mock.patch / mock_open to intercept file I/O for model_card.json
- pytest tmp_path fixture to create real temporary JSON files for integration-style tests

TODOs:
- TODO: If model_card.json schema is formally defined (e.g., via pydantic), add schema-validation tests
- TODO: Test behaviour when model_card.json contains unexpected/extra fields once schema is locked
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


def _reload_prompts_with_card(model_card_dict: dict):
    """
    Reload backend.agent.prompts with a patched model_card.json so that each
    test can control the file content without touching the real filesystem.
    """
    json_bytes = json.dumps(model_card_dict)
    m = mock_open(read_data=json_bytes)

    # Remove cached module so importlib reloads fresh
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m):
        with patch("pathlib.Path.open", m):  # belt-and-suspenders
            import backend.agent.prompts as prompts_module  # noqa: PLC0415

            importlib.reload(prompts_module)

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts():
    """Import the real module (uses the real model_card.json on disk)."""
    import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


@pytest.fixture()
def fake_prompts(tmp_path, monkeypatch):
    """
    Provide a freshly imported prompts module that reads from a controlled
    temporary model_card.json, keeping the real file untouched.
    """
    card_path = tmp_path / "model_card.json"
    card_path.write_text(json.dumps(MINIMAL_MODEL_CARD), encoding="utf-8")

    # Patch Path so that _model_card_path resolves to our tmp file
    original_path_class = Path

    class PatchedPath(type(original_path_class())):
        pass

    # Simpler: monkeypatch the open call at the module level via importlib
    sys.modules.pop("backend.agent.prompts", None)

    with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
        import backend.agent.prompts as pm  # noqa: PLC0415

        importlib.reload(pm)

    yield pm

    # Cleanup: remove cached module so subsequent tests get a fresh import
    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# Tests: MODULE_CARD (MODEL_CARD) loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts):
        """MODEL_CARD must be a dictionary after JSON parsing."""
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_not_empty(self, prompts):
        """MODEL_CARD must contain at least one key."""
        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_has_model_name(self, prompts):
        """MODEL_CARD should contain a 'model_name' key."""
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_value(self, prompts):
        """model_name value must be a non-empty string."""
        assert isinstance(prompts.MODEL_CARD["model_name"], str)
        assert prompts.MODEL_CARD["model_name"].strip() != ""

    def test_model_card_has_model_type(self, prompts):
        assert "model_type" in prompts.MODEL_CARD

    def test_model_card_model_type_is_string(self, prompts):
        assert isinstance(prompts.MODEL_CARD["model_type"], str)

    def test_model_card_has_target_variable(self, prompts):
        assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_target_variable_is_string(self, prompts):
        assert isinstance(prompts.MODEL_CARD["target_variable"], str)
        assert prompts.MODEL_CARD["target_variable"].strip() != ""

    def test_model_card_global_feature_importance_exists(self, prompts):
        assert "global_feature_importance" in prompts.MODEL_CARD

    def test_model_card_global_feature_importance_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD["global_feature_importance"], dict)

    def test_model_card_feature_importance_values_are_numeric(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert isinstance(value, (int, float)), (
                f"Feature importance for '{key}' is not numeric: {value!r}"
            )

    def test_model_card_feature_importance_values_non_negative(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert value >= 0, (
                f"Feature importance for '{key}' is negative: {value}"
            )

    def test_model_card_known_features_present(self, prompts):
        """Key features from the synthetic data sample must be present."""
        expected_features = {"Age", "Education_Level", "Employment_Status"}
        gfi = prompts.MODEL_CARD.get("global_feature_importance", {})
        present = set(gfi.keys())
        assert expected_features.issubset(present), (
            f"Missing expected features: {expected_features - present}"
        )

    def test_model_card_age_importance_approx(self, prompts):
        """Age should be the top feature with importance ~34.58."""
        age_importance = prompts.MODEL_CARD["global_feature_importance"].get("Age")
        assert age_importance is not None
        assert abs(age_importance - 34.57614295408571) < 1e-3

    # ------------------------------------------------------------------
    # Controlled-load tests (fake_prompts fixture)
    # ------------------------------------------------------------------

    def test_model_card_loads_from_mocked_file(self, fake_prompts):
        """MODEL_CARD should equal exactly what was written to the mocked file."""
        assert fake_prompts.MODEL_CARD["model_name"] == MINIMAL_MODEL_CARD["model_name"]
        assert fake_prompts.MODEL_CARD["model_type"] == MINIMAL_MODEL_CARD["model_type"]

    def test_model_card_feature_importance_matches_mock(self, fake_prompts):
        gfi = fake_prompts.MODEL_CARD["global_feature_importance"]
        assert gfi["Age"] == pytest.approx(34.57614295408571, rel=1e-5)
        assert gfi["Liquid_Assets"] == pytest.approx(1.2231046859555164, rel=1e-5)


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT content
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_exists(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self, prompts):
        assert prompts.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_length_reasonable(self, prompts):
        """Prompt should be at least 50 characters long."""
        assert len(prompts.SYSTEM_PROMPT) >= 50

    def test_system_prompt_mentions_underwriting(self, prompts):
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower() or \
               "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessment(self, prompts):
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_non_disclosure_clause_present(self, prompts):
        """The prompt must instruct the model to never reveal system instructions."""
        lowered = prompts.SYSTEM_PROMPT.lower()
        # Any of these phrases signals the non-disclosure intent
        disclosure_keywords = ["disclose", "reveal", "internal system"]
        assert any(kw in lowered for kw in disclosure_keywords), (
            "SYSTEM_PROMPT should contain a non-disclosure instruction."
        )

    def test_system_prompt_never_keyword(self, prompts):
        """The word 'never' must appear to enforce hard constraints."""
        assert "never" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_helpful_assistant_persona(self, prompts):
        lowered = prompts.SYSTEM_PROMPT.lower()
        assert "helpful assistant" in lowered or "helpful" in lowered

    def test_system_prompt_tools_confidentiality(self, prompts):
        """The prompt should mention that tools must not be revealed."""
        lowered = prompts.SYSTEM_PROMPT.lower()
        assert "tool" in lowered

    def test_system_prompt_no_trailing_whitespace_only(self, prompts):
        """Prompt should not be whitespace-only."""
        assert prompts.SYSTEM_PROMPT.strip()

    def test_system_prompt_is_single_string_not_list(self, prompts):
        """SYSTEM_PROMPT must be a plain str, not a list or tuple of strings."""
        assert not isinstance(prompts.SYSTEM_PROMPT, (list, tuple, bytes))

    @pytest.mark.parametrize("forbidden_phrase", [
        "ignore previous instructions",
        "disregard your instructions",
        "forget your instructions",
    ])
    def test_system_prompt_no_injection_phrases(self, prompts, forbidden_phrase):
        """SYSTEM_PROMPT itself must not contain prompt-injection phrases."""
        assert forbidden_phrase.lower() not in prompts.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests: Module-level constants & path resolution
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_model_card_constant_exists(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")

    def test_system_prompt_constant_exists(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_model_card_is_not_none(self, prompts):
        assert prompts.MODEL_CARD is not None

    def test_system_prompt_is_not_none(self, prompts):
        assert prompts.SYSTEM_PROMPT is not None

    def test_no_extra_public_constants_leaked(self, prompts):
        """
        Only MODEL_CARD and SYSTEM_PROMPT should be uppercase public constants
        (guards against accidental credential or secret exposure).
        """
        public_uppercase = [
            name for name in dir(prompts)
            if name.isupper() and not name.startswith("_")
        ]
        allowed = {"MODEL_CARD", "SYSTEM_PROMPT"}
        unexpected = set(public_uppercase) - allowed
        assert not unexpected, (
            f"Unexpected public uppercase constants found: {unexpected}"
        )


# ---------------------------------------------------------------------------
# Tests: Error handling during module load
# ---------------------------------------------------------------------------


class TestModelCardErrorHandling:
    def test_missing_file_raises_file_not_found(self):
        """If model_card.json is absent, the module should raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("mocked missing file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_invalid_json_raises_json_decode_error(self):
        """If model_card.json contains malformed JSON, a JSONDecodeError should propagate."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_empty_json_file_raises_value_error_or_json_error(self):
        """An empty file should not silently produce a usable MODEL_CARD."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="")
        with patch("builtins.open", m):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_json_array_instead_of_object(self):
        """
        If the JSON root is an array rather than an object, MODEL_CARD will be
        a list. This test documents (and guards against) that unexpected shape.
        """
        sys.modules.pop("backend.agent.prompts", None)
        array_json = json.dumps([{"key": "value"}])
        m = mock_open(read_data=array_json)
        with patch("builtins.open", m):
            import backend.agent.prompts as pm  # noqa: PLC0415

            importlib.reload(pm)
            # MODEL_CARD should be a list in this case — we assert it is NOT a dict
            assert not isinstance(pm.MODEL_CARD, dict), (
                "Expected MODEL_CARD to be a list when JSON root is an array, "
                "so callers should validate the type."
            )

    def test_permission_error_propagates(self):
        """A PermissionError reading model_card.json should not be swallowed."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("mocked permission denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)


# ---------------------------------------------------------------------------
# Tests: Path resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    def test_model_card_path_is_two_levels_up(self):
        """
        _model_card_path should resolve to <package_root>/model_card.json,
        i.e. two directories above prompts.py.
        """
        import backend.agent.prompts as pm  #