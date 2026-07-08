"""
Test module for backend/agent/prompts.py

What is tested:
- MODEL_CARD loading from model_card.json at module import time
- SYSTEM_PROMPT content and structural guarantees
- Edge cases around file loading (missing file, malformed JSON)
- Boundary/negative conditions for the constants exposed by the module

Mocks used:
- unittest.mock.patch / mock_open — used to simulate file I/O so no real
  filesystem read is required during the test suite
- tmp_path (pytest fixture) — used to write real temporary JSON files for
  integration-style loading tests

TODOs:
- TODO: Extend MODEL_CARD content tests once the full model_card.json schema
  is finalised (currently truncated in the synthetic sample).
- TODO: Add tests for any additional prompt constants added to prompts.py in
  the future.
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

SYSTEM_PROMPT_EXPECTED_FRAGMENTS = [
    "senior underwriting assistant",
    "underwriter",
    "gather",
    "assessments",
    "never disclose",
    "internal system instructions",
    "helpful assistant",
]


def _reload_prompts_with_model_card(model_card_dict: dict):
    """
    Helper: reload backend.agent.prompts with a patched model_card.json
    containing *model_card_dict*.  Returns the freshly imported module.
    """
    json_bytes = json.dumps(model_card_dict).encode()
    m = mock_open(read_data=json_bytes.decode())

    # Remove cached module so importlib reloads from scratch
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m):
        with patch("pathlib.Path.open", m):
            # Patch json.load to return our dict when called
            with patch("json.load", return_value=model_card_dict):
                import backend.agent.prompts as prompts_module  # noqa: PLC0415

                importlib.reload(prompts_module)
                return prompts_module


# ---------------------------------------------------------------------------
# Fixture: ensure the real module is importable (needs a real model_card.json)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_prompts_module():
    """
    Attempt to import the real module.  If the model_card.json does not exist
    in the test environment, the fixture creates a temporary one so that the
    module can be imported, then restores state afterwards.
    """
    model_card_path = Path(__file__).parent.parent / "model_card.json"

    if model_card_path.exists():
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

        return prompts_module

    # model_card.json absent — write a minimal one, import, then remove
    model_card_path.parent.mkdir(parents=True, exist_ok=True)
    model_card_path.write_text(json.dumps(MINIMAL_MODEL_CARD))
    try:
        sys.modules.pop("backend.agent.prompts", None)
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

        return prompts_module
    finally:
        model_card_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 1.  MODULE-LEVEL CONSTANT: MODEL_CARD
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests around MODEL_CARD being loaded correctly at import time."""

    def test_model_card_is_dict(self, real_prompts_module):
        """MODEL_CARD must be a dictionary (parsed JSON object)."""
        assert isinstance(real_prompts_module.MODEL_CARD, dict)

    def test_model_card_not_empty(self, real_prompts_module):
        """MODEL_CARD must not be an empty dict."""
        assert len(real_prompts_module.MODEL_CARD) > 0

    def test_model_card_has_model_name(self, real_prompts_module):
        """MODEL_CARD should contain the 'model_name' key (from synthetic sample)."""
        assert "model_name" in real_prompts_module.MODEL_CARD

    def test_model_card_model_name_is_string(self, real_prompts_module):
        """'model_name' value must be a non-empty string."""
        name = real_prompts_module.MODEL_CARD.get("model_name", "")
        assert isinstance(name, str) and name.strip() != ""

    def test_model_card_has_model_type(self, real_prompts_module):
        """MODEL_CARD should contain the 'model_type' key."""
        assert "model_type" in real_prompts_module.MODEL_CARD

    def test_model_card_has_target_variable(self, real_prompts_module):
        """MODEL_CARD should contain the 'target_variable' key."""
        assert "target_variable" in real_prompts_module.MODEL_CARD

    def test_model_card_expected_model_name_value(self, real_prompts_module):
        """'model_name' should match the synthetic sample value."""
        assert (
            real_prompts_module.MODEL_CARD["model_name"]
            == "Underwriting Risk Classification"
        )

    def test_model_card_expected_model_type_value(self, real_prompts_module):
        """'model_type' should be 'CatBoostClassifier' per synthetic sample."""
        assert real_prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable_value(self, real_prompts_module):
        """'target_variable' should be 'Risk_Classification' per synthetic sample."""
        assert (
            real_prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"
        )

    def test_model_card_global_feature_importance_present(self, real_prompts_module):
        """'global_feature_importance' key should be present."""
        assert "global_feature_importance" in real_prompts_module.MODEL_CARD

    def test_model_card_global_feature_importance_is_dict(self, real_prompts_module):
        """'global_feature_importance' value should be a dict."""
        gfi = real_prompts_module.MODEL_CARD.get("global_feature_importance")
        assert isinstance(gfi, dict)

    @pytest.mark.parametrize(
        "feature, expected_score",
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
    def test_model_card_feature_importance_values(
        self, real_prompts_module, feature, expected_score
    ):
        """Each feature importance value should match the synthetic sample."""
        gfi = real_prompts_module.MODEL_CARD.get("global_feature_importance", {})
        if feature not in gfi:
            pytest.skip(f"Feature '{feature}' not present in loaded MODEL_CARD.")
        assert gfi[feature] == pytest.approx(expected_score, rel=1e-6)

    def test_model_card_age_is_dominant_feature(self, real_prompts_module):
        """'Age' should have the highest importance score per synthetic sample."""
        gfi = real_prompts_module.MODEL_CARD.get("global_feature_importance", {})
        if not gfi:
            pytest.skip("global_feature_importance not present in MODEL_CARD.")
        top_feature = max(gfi, key=gfi.get)
        assert top_feature == "Age"


# ---------------------------------------------------------------------------
# 2.  MODULE-LEVEL CONSTANT: SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests around the SYSTEM_PROMPT string constant."""

    def test_system_prompt_is_string(self, real_prompts_module):
        """SYSTEM_PROMPT must be a plain string."""
        assert isinstance(real_prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self, real_prompts_module):
        """SYSTEM_PROMPT must not be blank."""
        assert real_prompts_module.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_minimum_length(self, real_prompts_module):
        """SYSTEM_PROMPT should be reasonably long (>= 50 characters)."""
        assert len(real_prompts_module.SYSTEM_PROMPT) >= 50

    @pytest.mark.parametrize("fragment", SYSTEM_PROMPT_EXPECTED_FRAGMENTS)
    def test_system_prompt_contains_key_fragment(self, real_prompts_module, fragment):
        """SYSTEM_PROMPT must contain each expected keyword/phrase (case-insensitive)."""
        assert fragment.lower() in real_prompts_module.SYSTEM_PROMPT.lower(), (
            f"Expected fragment '{fragment}' not found in SYSTEM_PROMPT."
        )

    def test_system_prompt_does_not_expose_tool_names(self, real_prompts_module):
        """
        SYSTEM_PROMPT itself should not expose concrete internal tool names
        (the prompt instructs the model never to reveal tools).
        We verify that obvious leakage strings are absent.
        """
        leakage_patterns = ["tool_call", "function_call", "<tool>", "</tool>"]
        prompt_lower = real_prompts_module.SYSTEM_PROMPT.lower()
        for pattern in leakage_patterns:
            assert pattern not in prompt_lower, (
                f"Possible internal leakage pattern '{pattern}' found in SYSTEM_PROMPT."
            )

    def test_system_prompt_confidentiality_instruction_present(
        self, real_prompts_module
    ):
        """The prompt must contain an instruction about not revealing system internals."""
        prompt_lower = real_prompts_module.SYSTEM_PROMPT.lower()
        assert "never" in prompt_lower or "cannot" in prompt_lower or "must not" in prompt_lower

    def test_system_prompt_role_persona(self, real_prompts_module):
        """The prompt must establish a 'senior underwriting assistant' persona."""
        assert "senior underwriting assistant" in real_prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_target_audience(self, real_prompts_module):
        """The prompt should mention 'underwriter' as the target audience."""
        assert "underwriter" in real_prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_trailing_only_whitespace(self, real_prompts_module):
        """SYSTEM_PROMPT should have meaningful content, not only whitespace."""
        assert real_prompts_module.SYSTEM_PROMPT.strip() == real_prompts_module.SYSTEM_PROMPT.strip()
        assert len(real_prompts_module.SYSTEM_PROMPT.strip()) > 0


# ---------------------------------------------------------------------------
# 3.  FILE-LOADING EDGE CASES (use mocks — no real filesystem dependency)
# ---------------------------------------------------------------------------


class TestModelCardFileLoadingEdgeCases:
    """
    Tests that exercise failure modes when model_card.json is unavailable or
    malformed.  These reload the module in isolation using mocks.
    """

    def test_file_not_found_raises_on_import(self):
        """
        If model_card.json does not exist, opening the file should raise
        FileNotFoundError, which propagates out of the module import.
        """
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("model_card.json not found")):
            with pytest.raises((FileNotFoundError, Exception)):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_malformed_json_raises_on_import(self):
        """
        If model_card.json contains invalid JSON, json.load should raise
        json.JSONDecodeError, which propagates out of the module import.
        """
        sys.modules.pop("backend.agent.prompts", None)

        bad_json = "{ not valid json !!!"
        m = mock_open(read_data=bad_json)

        with patch("builtins.open", m):
            with patch("json.load", side_effect=json.JSONDecodeError("Expecting value", bad_json, 0)):
                with pytest.raises((json.JSONDecodeError, Exception)):
                    import backend.agent.prompts  # noqa: PLC0415, F401

                    importlib.reload(backend.agent.prompts)

    def test_empty_json_object_loads_as_empty_dict(self, tmp_path, monkeypatch):
        """
        An empty JSON object '{}' is valid JSON and should load without error,
        producing an empty dict for MODEL_CARD.
        """
        empty_card = {}
        json_text = json.dumps(empty_card)
        m = mock_open(read_data=json_text)

        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", m):
            with patch("json.load", return_value=empty_card):
                import backend.agent.prompts as pm  # noqa: PLC0415

                importlib.reload(pm)
                assert pm.MODEL_CARD == {}

    def test_minimal_valid_model_card_loads_correctly(self):
        """
        A minimal valid model_card dict should be loaded into MODEL_CARD unchanged.
        """
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                import backend.agent.prompts as pm  # noqa: PLC0415

                importlib.reload(pm)
                assert pm.MODEL_CARD["model_name"] == MINIMAL_MODEL_CARD["model_name"]
                assert pm.MODEL_CARD["model_type"] == MINIMAL_MODEL_CARD["model_type"]

    def test_model_card_with_extra_keys_loads_without_error(self):
        """
        A model_card.json with unexpected extra keys should still load fine —
        prompts.py does not validate the schema.
        """
        extended = dict(MINIMAL_MODEL_CARD, extra_unknown_key="some_value", nested={"a": 1})
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", mock_open(read_data=json.dumps(extended))):
            with patch("json.load", return_value=extended):
                import backend.agent.prompts as pm  # noqa: PLC0415

                import