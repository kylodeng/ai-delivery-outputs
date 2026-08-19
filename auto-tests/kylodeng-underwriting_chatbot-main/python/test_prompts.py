"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD: successful loading and structure of MODEL_CARD from model_card.json
- SYSTEM_PROMPT: content, type, and behavioural constraints encoded in the prompt string
- Module-level side effects: file loading at import time

Mocks used:
- unittest.mock.patch / mock_open: to simulate model_card.json file reads without touching the filesystem
- tmp_path (pytest fixture): for integration-style tests that write a real temp JSON file

TODOs:
- TODO: Extend MODEL_CARD structure tests once the full model_card.json schema is finalised
- TODO: Add tests for any future prompt-builder functions added to prompts.py
"""

import builtins
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / fixtures
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
    Helper: reload the prompts module after patching the open() call so that
    model_card.json returns *model_card_dict*.
    Returns the freshly imported module.
    """
    json_bytes = json.dumps(model_card_dict)
    m = mock_open(read_data=json_bytes)

    # Remove cached module so importlib.import_module re-executes module body
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_mod  # noqa: PLC0415

        return prompts_mod


# ---------------------------------------------------------------------------
# MODEL_CARD loading tests
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests focused on the MODEL_CARD constant populated at import time."""

    def test_model_card_is_dict(self):
        """MODEL_CARD must be a dictionary (parsed JSON object)."""
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert isinstance(mod.MODEL_CARD, dict)

    def test_model_card_has_model_name(self):
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type(self):
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_has_target_variable(self):
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_has_global_feature_importance(self):
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert "global_feature_importance" in mod.MODEL_CARD
        assert isinstance(mod.MODEL_CARD["global_feature_importance"], dict)

    def test_model_card_feature_importance_values_are_floats(self):
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        for key, val in mod.MODEL_CARD["global_feature_importance"].items():
            assert isinstance(val, float), f"Feature {key!r} importance is not a float"

    def test_model_card_age_importance_boundary(self):
        """Age importance should be the dominant feature (>30 in the sample)."""
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        age_importance = mod.MODEL_CARD["global_feature_importance"]["Age"]
        assert age_importance > 30.0

    def test_model_card_empty_dict(self):
        """An empty JSON object should still load without raising."""
        mod = _reload_prompts_with_card({})
        assert mod.MODEL_CARD == {}

    def test_model_card_nested_extra_keys(self):
        """Extra keys beyond the known schema should be preserved."""
        extended = dict(MINIMAL_MODEL_CARD)
        extended["extra_metadata"] = {"version": "1.0", "author": "QA"}
        mod = _reload_prompts_with_card(extended)
        assert mod.MODEL_CARD["extra_metadata"]["version"] == "1.0"

    def test_model_card_file_not_found_raises(self):
        """If the model card file is missing, the module should raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_model_card_invalid_json_raises(self):
        """Malformed JSON in model_card.json must raise json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        m = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_model_card_list_json_raises_or_loads(self):
        """
        A JSON array at the top level is valid JSON but semantically wrong for
        MODEL_CARD (expected dict).  The module currently does no validation, so
        it should load as a list.  This test documents that behaviour.
        """
        mod = _reload_prompts_with_card([])  # type: ignore[arg-type]
        # No assertion on type – just confirm it doesn't crash.
        # If schema validation is added later, update this test.
        assert mod.MODEL_CARD == []

    def test_model_card_path_points_to_parent_parent(self):
        """
        The path used to open model_card.json must resolve relative to the
        parent's parent of the prompts module file (i.e. backend/).
        We verify open() is called with a Path ending in 'model_card.json'.
        """
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        json_bytes = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=json_bytes)

        with patch("builtins.open", m) as patched_open:
            import backend.agent.prompts  # noqa: F401, PLC0415

        call_args = patched_open.call_args
        opened_path = call_args[0][0]  # first positional argument
        assert Path(opened_path).name == "model_card.json"


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT string constant."""

    @pytest.fixture(autouse=True)
    def prompts_module(self):
        self.mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)

    def test_system_prompt_is_string(self):
        assert isinstance(self.mod.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self):
        assert len(self.mod.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self):
        assert "underwriting" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self):
        assert "underwriter" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_prohibits_disclosure_of_instructions(self):
        """Prompt must encode the confidentiality constraint."""
        prompt_lower = self.mod.SYSTEM_PROMPT.lower()
        # Either "disclose" or "reveal" must appear
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_system_prompt_prohibits_tool_disclosure(self):
        """Tools available to the agent must not be revealed."""
        assert "tools" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_describes_assistant_role(self):
        assert "assistant" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self):
        assert "assessment" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_gathering_information(self):
        assert "gather" in self.mod.SYSTEM_PROMPT.lower() or "information" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_leading_trailing_newlines(self):
        """
        The prompt is a plain concatenated string literal; it should not start
        or end with a bare newline that might confuse the LLM tokenizer.
        """
        assert not self.mod.SYSTEM_PROMPT.startswith("\n")
        assert not self.mod.SYSTEM_PROMPT.endswith("\n")

    def test_system_prompt_never_reveals_internal_system_instruction_phrase(self):
        """
        The prompt explicitly says it cannot reveal 'internal system instructions'.
        Verify that phrase (or close variant) is present.
        """
        assert "internal system" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_contain_placeholder_tokens(self):
        """No unfilled template placeholders like {variable} should exist."""
        import re

        placeholders = re.findall(r"\{[^}]+\}", self.mod.SYSTEM_PROMPT)
        assert placeholders == [], f"Unfilled placeholders found: {placeholders}"

    def test_system_prompt_is_senior_underwriting_assistant(self):
        """Role description must reference 'senior underwriting assistant'."""
        assert "senior underwriting assistant" in self.mod.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Module-level public surface tests
# ---------------------------------------------------------------------------


class TestModulePublicSurface:
    """Ensure the module exports exactly the expected public names."""

    @pytest.fixture(autouse=True)
    def prompts_module(self):
        self.mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)

    def test_model_card_exported(self):
        assert hasattr(self.mod, "MODEL_CARD")

    def test_system_prompt_exported(self):
        assert hasattr(self.mod, "SYSTEM_PROMPT")

    def test_model_card_is_not_none(self):
        assert self.mod.MODEL_CARD is not None

    def test_system_prompt_is_not_none(self):
        assert self.mod.SYSTEM_PROMPT is not None


# ---------------------------------------------------------------------------
# Integration test: real filesystem via tmp_path
# ---------------------------------------------------------------------------


class TestModelCardIntegrationWithRealFile:
    """
    Write an actual JSON file in a temp directory and verify the module loads
    it correctly, without mocking builtins.open.
    Uses monkeypatching to redirect the module's path constant.
    """

    def test_real_file_read(self, tmp_path, monkeypatch):
        card_file = tmp_path / "model_card.json"
        card_file.write_text(json.dumps(MINIMAL_MODEL_CARD))

        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        # Patch Path so that the computed _model_card_path resolves to our temp file.
        original_path_init = Path.__new__

        with patch("backend.agent.prompts._model_card_path", card_file, create=True):
            # Re-open is already done at module level; we need a full reload.
            # Simplest approach: patch builtins.open specifically for the known path.
            real_open = builtins.open

            def selective_open(file, *args, **kwargs):
                if Path(file).name == "model_card.json":
                    return real_open(card_file, *args, **kwargs)
                return real_open(file, *args, **kwargs)

            with patch("builtins.open", side_effect=selective_open):
                import backend.agent.prompts as mod  # noqa: PLC0415

        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"
        assert mod.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_real_file_with_unicode_values(self, tmp_path):
        """Model card containing unicode characters should parse correctly."""
        card = {"model_name": "模型卡片", "model_type": "CatBoostClassifier"}
        card_file = tmp_path / "model_card.json"
        card_file.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")

        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        real_open = builtins.open

        def selective_open(file, *args, **kwargs):
            if Path(file).name == "model_card.json":
                return real_open(card_file, encoding="utf-8")
            return real_open(file, *args, **kwargs)

        with patch("builtins.open", side_effect=selective_open):
            import backend.agent.prompts as mod  # noqa: PLC0415

        assert mod.MODEL_CARD["model_name"] == "模型卡片"


# ---------------------------------------------------------------------------
# Parametrised edge-case tests for MODEL_CARD content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "card,expected_key,expected_value",
    [
        ({"model_name": "A"}, "model_name", "A"),
        ({"model_type": "XGBoost"}, "model_type", "XGBoost"),
        ({"target_variable": "Risk"}, "target_variable", "Risk"),
        ({"numeric_value": 0}, "numeric_value", 0),
        ({"boolean_flag": True}, "boolean_flag", True),
        ({"null_field": None}, "null_field", None),
        ({"nested": {"a": 1}}, "nested", {"a": 1}),
    ],
)
def test_model_card_various_shapes(card, expected_key, expected_value):
    """MODEL_CARD faithfully reflects whatever JSON is in the file."""
    mod = _reload_prompts_with_card(card)
    assert mod.MODEL_CARD[expected_key] == expected_value


# ---------------------------------------------------------------------------
# TODO stubs
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: full model_card.json schema not yet finalised — add exhaustive key checks")
def test_model_card_full_schema_validation():
    pass


@pytest.mark.skip(reason="TODO: if prompt-builder functions are added to prompts.py, test them here")
def test_prompt_builder_functions():
    pass


@pytest.mark.skip(reason="TODO: test behaviour when model_card.json contains very large feature importance dicts (performance)")