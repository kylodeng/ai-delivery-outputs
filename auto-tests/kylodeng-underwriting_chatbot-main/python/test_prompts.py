"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json
- SYSTEM_PROMPT is defined, is a string, and contains expected substrings
- Security constraints embedded in SYSTEM_PROMPT (no disclosure of internals)
- Edge cases around file loading and JSON parsing

Mocks used:
- unittest.mock.mock_open / patch: used to simulate model_card.json file reads
  without relying on the real file being present in all environments
- patch("builtins.open"): intercepts file I/O at the module level
- patch("json.load"): controls the parsed JSON content

TODOs:
- TODO: Integration test that validates the full model_card.json schema once the
  complete schema is finalised (currently truncated in synthetic data).
- TODO: Test that MODEL_CARD contains all expected top-level keys once the full
  model_card.json schema is known.
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


def _reload_prompts_with_card(model_card_dict: dict) -> ModuleType:
    """
    Helper: reload backend.agent.prompts with a patched model_card.json.

    Patches both builtins.open and json.load so no real filesystem access
    occurs during the reload.
    """
    module_name = "backend.agent.prompts"
    # Remove cached module so importlib.import_module re-executes module-level code
    sys.modules.pop(module_name, None)

    mock_file_handle = mock_open(read_data=json.dumps(model_card_dict))()
    mock_json_load = MagicMock(return_value=model_card_dict)

    with patch("builtins.open", return_value=mock_file_handle), patch(
        "json.load", mock_json_load
    ):
        module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts_module():
    """Return a freshly loaded prompts module backed by MINIMAL_MODEL_CARD."""
    module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
    yield module
    # Cleanup so other tests start fresh
    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts_module):
        """MODEL_CARD must be a dict after JSON parsing."""
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_has_model_name(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_has_target_variable(self, prompts_module):
        assert prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self, prompts_module):
        gfi = prompts_module.MODEL_CARD.get("global_feature_importance")
        assert isinstance(gfi, dict)

    def test_model_card_age_feature_importance(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(gfi["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_feature_importance_values_are_floats(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert isinstance(value, float), f"Feature {key!r} importance is not a float"

    @pytest.mark.parametrize(
        "card",
        [
            {},
            {"model_name": "Minimal"},
            {"model_name": "X", "model_type": "Y", "target_variable": "Z"},
        ],
    )
    def test_model_card_accepts_arbitrary_dict(self, card):
        """MODULE_CARD simply stores whatever json.load returns."""
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD == card

    def test_model_card_file_not_found_raises(self):
        """If the model_card.json file is missing, the module should raise FileNotFoundError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)
        try:
            with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
                with pytest.raises(FileNotFoundError):
                    importlib.import_module(module_name)
        finally:
            sys.modules.pop(module_name, None)

    def test_model_card_invalid_json_raises(self):
        """If model_card.json contains invalid JSON, the module should propagate the error."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)
        try:
            bad_handle = mock_open(read_data="{ not valid json }")()
            with patch("builtins.open", return_value=bad_handle):
                # json.load uses the real implementation here, so invalid JSON raises
                with pytest.raises(json.JSONDecodeError):
                    importlib.import_module(module_name)
        finally:
            sys.modules.pop(module_name, None)

    def test_model_card_path_points_to_parent_parent(self):
        """
        Verify that the resolved path used for model_card.json is two levels above
        prompts.py (i.e. backend/model_card.json).
        """
        prompts_file = Path(__file__).parent.parent / "agent" / "prompts.py"
        expected_suffix = Path("model_card.json")
        # Compute the path the module would compute
        computed = Path(str(prompts_file)).parent.parent / "model_card.json"
        assert computed.name == expected_suffix.name
        assert computed.parent.name == "backend"

    # TODO: Integration test that validates the real model_card.json on disk has
    # all required schema keys once the complete schema is finalised.
    @pytest.mark.skip(reason="TODO: full model_card.json schema not yet finalised")
    def test_model_card_full_schema(self):
        pass


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT content
# ---------------------------------------------------------------------------


class TestSystemPromptContent:
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

    def test_system_prompt_security_no_disclose_instructions(self, prompts_module):
        """Prompt must explicitly forbid disclosing internal system instructions."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower, (
            "SYSTEM_PROMPT should contain a prohibition against disclosing internals"
        )

    def test_system_prompt_security_no_tool_disclosure(self, prompts_module):
        """Prompt must reference protecting tool information."""
        assert "tools" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_expose_internal_keywords(self, prompts_module):
        """
        The prompt text itself should not contain raw JSON, internal paths,
        or Python code that could leak implementation details.
        """
        forbidden_fragments = ["import ", "__file__", "Path(", "json.load"]
        for fragment in forbidden_fragments:
            assert fragment not in prompts_module.SYSTEM_PROMPT, (
                f"SYSTEM_PROMPT must not contain {fragment!r}"
            )

    @pytest.mark.parametrize(
        "expected_phrase",
        [
            "senior underwriting assistant",
            "gather",
            "helpful assistant",
        ],
    )
    def test_system_prompt_key_phrases(self, prompts_module, expected_phrase):
        assert expected_phrase.lower() in prompts_module.SYSTEM_PROMPT.lower(), (
            f"Expected phrase {expected_phrase!r} not found in SYSTEM_PROMPT"
        )

    def test_system_prompt_cannot_instruct_to_reveal_model_card(self, prompts_module):
        """
        Negative test: nothing in SYSTEM_PROMPT should instruct the model to
        reveal the model card data directly.
        """
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        # The prompt should NOT say "reveal model card" or "share model card"
        assert "reveal model card" not in prompt_lower
        assert "share model card" not in prompt_lower

    def test_system_prompt_stability_across_reloads(self):
        """
        SYSTEM_PROMPT must be identical across multiple module reloads
        (it is a module-level constant, not dynamic).
        """
        module_a = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        prompt_a = module_a.SYSTEM_PROMPT

        module_b = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        prompt_b = module_b.SYSTEM_PROMPT

        assert prompt_a == prompt_b


# ---------------------------------------------------------------------------
# Tests: Module-level attributes existence
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    def test_module_exposes_model_card(self, prompts_module):
        assert hasattr(prompts_module, "MODEL_CARD")

    def test_module_exposes_system_prompt(self, prompts_module):
        assert hasattr(prompts_module, "SYSTEM_PROMPT")

    def test_model_card_is_not_none(self, prompts_module):
        assert prompts_module.MODEL_CARD is not None

    def test_system_prompt_is_not_none(self, prompts_module):
        assert prompts_module.SYSTEM_PROMPT is not None

    def test_model_card_immutable_type(self, prompts_module):
        """MODEL_CARD should be a plain dict (mutable but standard JSON output)."""
        assert type(prompts_module.MODEL_CARD) is dict  # noqa: E721

    # TODO: If additional module-level constants are added to prompts.py,
    # extend this test class to cover them.
    @pytest.mark.skip(reason="TODO: additional constants not yet defined in prompts.py")
    def test_future_constants(self):
        pass


# ---------------------------------------------------------------------------
# Tests: Boundary / edge values for MODEL_CARD content
# ---------------------------------------------------------------------------


class TestModelCardEdgeCases:
    def test_empty_global_feature_importance(self):
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {}}
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["global_feature_importance"] == {}

    def test_model_card_with_null_values(self):
        card = {"model_name": None, "model_type": None}
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["model_name"] is None

    def test_model_card_with_nested_structures(self):
        card = {
            "model_name": "Test",
            "nested": {"a": {"b": {"c": 42}}},
        }
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["nested"]["a"]["b"]["c"] == 42

    def test_model_card_with_list_values(self):
        card = {"features": ["Age", "Income", "Employment_Status"]}
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["features"] == ["Age", "Income", "Employment_Status"]

    def test_model_card_with_zero_feature_importance(self):
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {"SomeFeature": 0.0}}
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["global_feature_importance"]["SomeFeature"] == 0.0

    def test_model_card_with_very_large_importance(self):
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {"BigFeature": 1e10}}
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["global_feature_importance"]["BigFeature"] == 1e10

    def test_model_card_with_boolean_values(self):
        card = {"is_production": True, "is_draft": False}
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["is_production"] is True
        assert module.MODEL_CARD["is_draft"] is False

    def test_model_card_with_unicode_strings(self):
        card = {"model_name": "模型卡片", "description": "النموذج"}
        module = _reload_prompts_with_card(card)
        assert module.MODEL_CARD["model_name"] == "模型卡片"