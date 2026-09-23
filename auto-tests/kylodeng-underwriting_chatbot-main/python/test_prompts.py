"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD: successful loading and structure of MODEL_CARD from model_card.json
- SYSTEM_PROMPT: content, type, and key behavioural constraints encoded in the prompt string
- Module-level side effects: file I/O on import, JSON parsing

Mocks used:
- unittest.mock.mock_open / patch('builtins.open') to avoid real filesystem reads
- patch('json.load') to control MODEL_CARD content without touching disk
- tmp_path (pytest fixture) for integration-style tests that write a real model_card.json

TODOs:
- TODO: test behaviour when model_card.json contains unexpected schema (no clear contract yet)
- TODO: end-to-end test that MODEL_CARD values are actually consumed by downstream agents
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

EMPTY_MODEL_CARD: dict = {}

EXTRA_FIELDS_MODEL_CARD = {
    **MINIMAL_MODEL_CARD,
    "version": "1.0.0",
    "author": "QA Team",
    "extra_nested": {"a": 1, "b": [1, 2, 3]},
}


def _reload_prompts_with_card(model_card_data: dict, monkeypatch):
    """
    Reload backend.agent.prompts with a patched model_card.json containing
    `model_card_data`.  Returns the freshly-imported module.
    """
    # Remove cached module so reload picks up patches
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    encoded = json.dumps(model_card_data).encode()

    m = mock_open(read_data=encoded.decode())
    with patch("builtins.open", m):
        with patch("json.load", return_value=model_card_data):
            import backend.agent.prompts as prompts_mod

            importlib.reload(prompts_mod)
            return prompts_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts_module():
    """Return the prompts module, reloaded with a minimal valid model card."""
    sys.modules.pop("backend.agent.prompts", None)
    with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
        with patch("json.load", return_value=MINIMAL_MODEL_CARD):
            import backend.agent.prompts as mod

            importlib.reload(mod)
            yield mod
    # cleanup
    sys.modules.pop("backend.agent.prompts", None)


@pytest.fixture()
def real_model_card_file(tmp_path, monkeypatch):
    """
    Write a real model_card.json into a temp directory and monkey-patch the
    path resolution inside the module so it points to the temp file.
    """
    card_path = tmp_path / "model_card.json"
    card_path.write_text(json.dumps(MINIMAL_MODEL_CARD))

    # We patch Path inside the prompts module namespace
    return card_path


# ---------------------------------------------------------------------------
# MODEL_CARD loading — happy path
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_contains_model_name(self, prompts_module):
        assert prompts_module.MODEL_CARD.get("model_name") == "Underwriting Risk Classification"

    def test_model_card_contains_model_type(self, prompts_module):
        assert prompts_module.MODEL_CARD.get("model_type") == "CatBoostClassifier"

    def test_model_card_contains_target_variable(self, prompts_module):
        assert prompts_module.MODEL_CARD.get("target_variable") == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self, prompts_module):
        gfi = prompts_module.MODEL_CARD.get("global_feature_importance")
        assert isinstance(gfi, dict)

    def test_model_card_age_feature_importance_value(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(gfi["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_all_expected_features_present(self, prompts_module):
        expected_features = {
            "Age",
            "Education_Level",
            "Employment_Status",
            "Nationality",
            "Customer_Segment",
            "Annual_Income",
            "Liquid_Assets",
        }
        actual_features = set(prompts_module.MODEL_CARD["global_feature_importance"].keys())
        assert expected_features.issubset(actual_features)


# ---------------------------------------------------------------------------
# MODEL_CARD loading — parametrised variations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "card_data",
    [
        pytest.param(MINIMAL_MODEL_CARD, id="minimal_card"),
        pytest.param(EMPTY_MODEL_CARD, id="empty_card"),
        pytest.param(EXTRA_FIELDS_MODEL_CARD, id="extra_fields_card"),
    ],
)
def test_model_card_accepts_various_shapes(card_data, monkeypatch):
    """MODULE should load whatever JSON object is in the file without raising."""
    sys.modules.pop("backend.agent.prompts", None)
    with patch("builtins.open", mock_open(read_data=json.dumps(card_data))):
        with patch("json.load", return_value=card_data):
            import backend.agent.prompts as mod

            importlib.reload(mod)
            assert mod.MODEL_CARD == card_data
    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# MODEL_CARD loading — error conditions
# ---------------------------------------------------------------------------


class TestModelCardLoadingErrors:
    def test_file_not_found_raises_on_import(self, monkeypatch):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("model_card.json not found")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401

                importlib.reload(backend.agent.prompts)
        sys.modules.pop("backend.agent.prompts", None)

    def test_invalid_json_raises_on_import(self, monkeypatch):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data="NOT VALID JSON {{")):
            with patch("json.load", side_effect=json.JSONDecodeError("err", "doc", 0)):
                with pytest.raises(json.JSONDecodeError):
                    import backend.agent.prompts  # noqa: F401

                    importlib.reload(backend.agent.prompts)
        sys.modules.pop("backend.agent.prompts", None)

    def test_permission_error_raises_on_import(self, monkeypatch):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: F401

                importlib.reload(backend.agent.prompts)
        sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# MODEL_CARD path resolution
# ---------------------------------------------------------------------------


class TestModelCardPathResolution:
    def test_model_card_path_is_two_levels_up_from_module(self, prompts_module):
        """
        _model_card_path should resolve to  <package_root>/model_card.json.
        We verify the filename at minimum.
        """
        assert prompts_module._model_card_path.name == "model_card.json"

    def test_model_card_path_parent_is_backend(self, prompts_module):
        parent_name = prompts_module._model_card_path.parent.name
        assert parent_name == "backend"

    def test_model_card_path_is_path_instance(self, prompts_module):
        assert isinstance(prompts_module._model_card_path, Path)


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT — type and existence
# ---------------------------------------------------------------------------


class TestSystemPromptType:
    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self, prompts_module):
        assert len(prompts_module.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_is_module_level_constant(self, prompts_module):
        assert hasattr(prompts_module, "SYSTEM_PROMPT")


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT — content / behavioural constraints
# ---------------------------------------------------------------------------


class TestSystemPromptContent:
    def test_prompt_identifies_role_as_underwriting_assistant(self, prompts_module):
        assert "underwriting assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_prompt_mentions_underwriter_audience(self, prompts_module):
        assert "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_prompt_includes_confidentiality_instruction(self, prompts_module):
        """Must never reveal internal system instructions."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_prompt_prohibits_revealing_tools(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "tools" in prompt_lower

    def test_prompt_instructs_to_gather_information(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "gathering information" in prompt_lower or "gather" in prompt_lower

    def test_prompt_instructs_to_run_assessments(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "assessment" in prompt_lower

    def test_prompt_presents_helpful_assistant_persona(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "helpful assistant" in prompt_lower

    def test_prompt_does_not_start_with_whitespace(self, prompts_module):
        assert prompts_module.SYSTEM_PROMPT == prompts_module.SYSTEM_PROMPT.lstrip()

    def test_prompt_contains_no_placeholder_tokens(self, prompts_module):
        """Ensure no un-substituted template placeholders like {variable} remain."""
        import re

        placeholders = re.findall(r"\{[^}]+\}", prompts_module.SYSTEM_PROMPT)
        assert placeholders == [], f"Unresolved placeholders found: {placeholders}"

    def test_prompt_does_not_expose_system_instructions_phrase(self, prompts_module):
        """The word 'never' should co-appear with disclosure-related terms."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "never" in prompt_lower

    def test_prompt_mentions_assess_customers(self, prompts_module):
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "assess" in prompt_lower

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "password",
            "secret key",
            "api_key",
            "private key",
        ],
    )
    def test_prompt_does_not_contain_sensitive_literals(self, prompts_module, forbidden_phrase):
        assert forbidden_phrase not in prompts_module.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Integration-style: real file on disk via tmp_path
# ---------------------------------------------------------------------------


class TestRealFileIntegration:
    def test_loads_from_real_json_file(self, tmp_path, monkeypatch):
        """Write a real file and patch the module path to use it."""
        card = {"model_name": "TestModel", "model_type": "TestType"}
        card_file = tmp_path / "model_card.json"
        card_file.write_text(json.dumps(card))

        sys.modules.pop("backend.agent.prompts", None)

        # Patch Path so _model_card_path resolves to our temp file
        real_path_class = Path

        class PatchedPath(type(real_path_class())):
            def __new__(cls, *args, **kwargs):
                instance = super().__new__(cls, *args, **kwargs)
                return instance

        with patch("backend.agent.prompts._model_card_path", card_file):
            # We still need open to work normally — use real open via tmp file
            with patch("builtins.open", mock_open(read_data=json.dumps(card))):
                with patch("json.load", return_value=card):
                    import backend.agent.prompts as mod

                    importlib.reload(mod)
                    assert mod.MODEL_CARD == card

        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_feature_importance_values_are_floats(self, prompts_module):
        gfi = prompts_module.MODEL_CARD.get("global_feature_importance", {})
        for feature, value in gfi.items():
            assert isinstance(value, float), (
                f"Feature '{feature}' importance should be float, got {type(value)}"
            )


# ---------------------------------------------------------------------------
# Boundary / edge cases
# ---------------------------------------------------------------------------


class TestBoundaryAndEdgeCases:
    def test_model_card_with_unicode_values(self, monkeypatch):
        card = {"model_name": "模型卡片", "notes": "données spéciales ñoño"}
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data=json.dumps(card))):
            with patch("json.load", return_value=card):
                import backend.agent.prompts as mod

                importlib.reload(mod)
                assert mod.MODEL_CARD["model_name"] == "模型卡片"
        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_with_deeply_nested_json(self, monkeypatch):
        card = {"level1": {"level2": {"level3": {"value": 42}}}}
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data=json.dumps(card))):
            with patch("json.load", return_value=card):
                import backend.agent.prompts as mod

                importlib.reload(mod)
                assert mod.MODEL_CARD["level1