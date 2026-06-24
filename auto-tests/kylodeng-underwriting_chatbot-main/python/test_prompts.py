"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at module level
- SYSTEM_PROMPT is defined as a non-empty string with expected content
- Module-level file loading behaviour (happy path, missing file, malformed JSON)

Mocks used:
- unittest.mock.patch / mock_open to intercept file I/O so no real model_card.json is required
- importlib to force re-import of the module under controlled conditions

TODOs:
- TODO: Validate full schema of MODEL_CARD once model_card.json schema is finalised
- TODO: Test behaviour when model_card.json contains unexpected/missing keys once
        downstream consumers of MODEL_CARD are implemented
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


def _reload_prompts_with_model_card(model_card_dict: dict):
    """Re-import backend.agent.prompts with a mocked model_card.json."""
    serialised = json.dumps(model_card_dict)
    m = mock_open(read_data=serialised)
    # Remove cached module so it is re-executed from scratch
    sys.modules.pop("backend.agent.prompts", None)
    with patch("builtins.open", m):
        module = importlib.import_module("backend.agent.prompts")
    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts_module():
    """Return a freshly imported prompts module backed by the minimal model card."""
    module = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
    yield module
    # Clean up so other tests get a fresh import if needed
    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# MODEL_CARD loading – happy path
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_has_model_name(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_has_target_variable(self, prompts_module):
        assert prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_has_global_feature_importance(self, prompts_module):
        assert "global_feature_importance" in prompts_module.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert isinstance(gfi, dict)

    def test_model_card_age_importance_value(self, prompts_module):
        age_importance = prompts_module.MODEL_CARD["global_feature_importance"]["Age"]
        assert pytest.approx(age_importance, rel=1e-6) == 34.57614295408571

    def test_model_card_feature_importance_values_are_floats(self, prompts_module):
        for key, value in prompts_module.MODEL_CARD["global_feature_importance"].items():
            assert isinstance(value, float), f"Expected float for key {key}, got {type(value)}"

    def test_model_card_not_empty(self, prompts_module):
        assert len(prompts_module.MODEL_CARD) > 0

    def test_model_card_all_expected_keys_present(self, prompts_module):
        expected_keys = {
            "model_name",
            "model_type",
            "target_variable",
            "global_feature_importance",
        }
        assert expected_keys.issubset(set(prompts_module.MODEL_CARD.keys()))

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
    def test_model_card_feature_present(self, prompts_module, feature):
        assert feature in prompts_module.MODEL_CARD["global_feature_importance"]


# ---------------------------------------------------------------------------
# MODEL_CARD loading – error conditions
# ---------------------------------------------------------------------------


class TestModelCardLoadingErrors:
    def test_missing_file_raises_file_not_found(self):
        """If model_card.json does not exist the module should propagate the error."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                importlib.import_module("backend.agent.prompts")
        sys.modules.pop("backend.agent.prompts", None)

    def test_malformed_json_raises_json_decode_error(self):
        """If model_card.json contains invalid JSON the module should propagate the error."""
        sys.modules.pop("backend.agent.prompts", None)
        bad_json = "{ not valid json ..."
        m = mock_open(read_data=bad_json)
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module("backend.agent.prompts")
        sys.modules.pop("backend.agent.prompts", None)

    def test_empty_json_object_loads_without_error(self):
        """An empty JSON object {} is valid; MODULE_CARD should be an empty dict."""
        sys.modules.pop("backend.agent.prompts", None)
        module = _reload_prompts_with_model_card({})
        assert module.MODEL_CARD == {}
        sys.modules.pop("backend.agent.prompts", None)

    def test_empty_file_raises_json_decode_error(self):
        """An empty file is not valid JSON."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module("backend.agent.prompts")
        sys.modules.pop("backend.agent.prompts", None)

    def test_permission_error_propagates(self):
        """A PermissionError on open should propagate."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with pytest.raises(PermissionError):
                importlib.import_module("backend.agent.prompts")
        sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# MODEL_CARD loading – edge / boundary values
# ---------------------------------------------------------------------------


class TestModelCardEdgeCases:
    def test_model_card_with_zero_feature_importance(self):
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {"Age": 0.0}}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD["global_feature_importance"]["Age"] == 0.0
        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_with_large_feature_importance(self):
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {"Age": 1e18}}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD["global_feature_importance"]["Age"] == 1e18
        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_with_extra_keys(self):
        card = {**MINIMAL_MODEL_CARD, "extra_key": "extra_value"}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD["extra_key"] == "extra_value"
        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_with_nested_structure(self):
        card = {**MINIMAL_MODEL_CARD, "nested": {"a": {"b": 1}}}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD["nested"]["a"]["b"] == 1
        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_with_list_values(self):
        card = {**MINIMAL_MODEL_CARD, "features": ["Age", "Income"]}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD["features"] == ["Age", "Income"]
        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_unicode_values(self):
        card = {**MINIMAL_MODEL_CARD, "model_name": "الاكتتاب"}
        module = _reload_prompts_with_model_card(card)
        assert module.MODEL_CARD["model_name"] == "الاكتتاب"
        sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT – happy path
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts_module):
        assert len(prompts_module.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts_module):
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, prompts_module):
        assert "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts_module):
        assert "assessment" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_confidentiality_clause(self, prompts_module):
        """Must instruct the model never to reveal internal instructions."""
        lowered = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in lowered or "reveal" in lowered or "never" in lowered

    def test_system_prompt_mentions_helpful_assistant(self, prompts_module):
        assert "helpful assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_contain_placeholder(self, prompts_module):
        """No un-substituted template placeholders should remain."""
        assert "{{" not in prompts_module.SYSTEM_PROMPT
        assert "}}" not in prompts_module.SYSTEM_PROMPT

    def test_system_prompt_does_not_expose_tools(self, prompts_module):
        """Should explicitly state tools are not to be disclosed."""
        lowered = prompts_module.SYSTEM_PROMPT.lower()
        # The prompt says it cannot disclose the tools it has access to
        assert "tools" in lowered

    def test_system_prompt_minimum_length(self, prompts_module):
        """A meaningful system prompt should be at least 100 characters."""
        assert len(prompts_module.SYSTEM_PROMPT) >= 100

    @pytest.mark.parametrize(
        "forbidden_fragment",
        [
            "TODO",
            "FIXME",
            "PLACEHOLDER",
            "<insert>",
        ],
    )
    def test_system_prompt_no_development_artefacts(self, prompts_module, forbidden_fragment):
        assert forbidden_fragment not in prompts_module.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Module-level path construction
# ---------------------------------------------------------------------------


class TestModelCardPath:
    def test_model_card_path_resolves_to_json(self, prompts_module):
        """Verify the expected resolved path ends with model_card.json."""
        # Re-derive the path the same way the module does
        expected_suffix = Path("model_card.json")
        # The module is at backend/agent/prompts.py so parent.parent == backend/
        module_file = Path(prompts_module.__file__)
        derived_path = module_file.parent.parent / "model_card.json"
        assert derived_path.name == expected_suffix.name

    def test_model_card_path_is_absolute(self, prompts_module):
        module_file = Path(prompts_module.__file__)
        derived_path = module_file.parent.parent / "model_card.json"
        assert derived_path.is_absolute()


# ---------------------------------------------------------------------------
# Skipped / stub tests requiring additional context
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: full MODEL_CARD schema validation requires finalised schema spec")
def test_model_card_full_schema_validation():
    """TODO: validate every expected field and type once the schema is locked."""
    pass


@pytest.mark.skip(
    reason="TODO: test downstream consumers of MODEL_CARD once they are implemented"
)
def test_model_card_consumed_by_downstream_modules():
    """TODO: ensure changes to MODEL_CARD keys do not silently break downstream usage."""
    pass


@pytest.mark.skip(
    reason="TODO: integration test — requires real model_card.json on disk at backend/model_card.json"
)
def test_model_card_loads_from_real_file():
    """TODO: run against the actual file to confirm production path resolves correctly."""
    pass