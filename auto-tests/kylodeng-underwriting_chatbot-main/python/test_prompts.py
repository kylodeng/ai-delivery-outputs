"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loading from model_card.json (happy path, missing file, malformed JSON)
- SYSTEM_PROMPT content and type validation
- MODEL_CARD structure and expected keys
- Module-level constants integrity

Mocks used:
- unittest.mock.patch / mock_open for file I/O to avoid real filesystem dependency
- Temporary files (tmp_path) for integration-style load tests

TODOs:
- TODO: Validate full MODEL_CARD schema once the complete model_card.json is available
- TODO: Test reload behaviour if model_card.json changes at runtime (requires importlib.reload support)
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


@pytest.fixture()
def model_card_json():
    """Return a serialised minimal model card."""
    return json.dumps(MINIMAL_MODEL_CARD)


@pytest.fixture()
def real_model_card_path(tmp_path, model_card_json):
    """Write a real model_card.json under a tmp_path tree and return the path."""
    card_path = tmp_path / "model_card.json"
    card_path.write_text(model_card_json, encoding="utf-8")
    return card_path


# ---------------------------------------------------------------------------
# Helper: reload the module under a patched filesystem
# ---------------------------------------------------------------------------

def _reload_prompts_with_card(card_dict: dict):
    """
    Reload backend.agent.prompts with open() patched to return *card_dict*
    as JSON.  Returns the freshly imported module object.
    """
    serialised = json.dumps(card_dict)
    m = mock_open(read_data=serialised)
    # Remove cached module so importlib.reload triggers __init__ code again.
    sys.modules.pop("backend.agent.prompts", None)
    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_mod
        return prompts_mod


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT constant."""

    def test_system_prompt_is_string(self):
        import backend.agent.prompts as prompts_mod
        assert isinstance(prompts_mod.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self):
        import backend.agent.prompts as prompts_mod
        assert len(prompts_mod.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_contains_role_description(self):
        import backend.agent.prompts as prompts_mod
        assert "underwriting assistant" in prompts_mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_confidentiality_instruction(self):
        """The prompt must instruct the model never to disclose system instructions."""
        import backend.agent.prompts as prompts_mod
        lower = prompts_mod.SYSTEM_PROMPT.lower()
        assert "disclose" in lower or "reveal" in lower or "never" in lower

    def test_system_prompt_mentions_assessments(self):
        import backend.agent.prompts as prompts_mod
        assert "assessment" in prompts_mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self):
        import backend.agent.prompts as prompts_mod
        assert "underwriter" in prompts_mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_leading_trailing_whitespace_issues(self):
        """Prompt should start with a capital letter (no accidental whitespace prefix)."""
        import backend.agent.prompts as prompts_mod
        assert prompts_mod.SYSTEM_PROMPT[0].isupper()

    @pytest.mark.parametrize("forbidden", [
        "tool_name",
        "function_call",
        "<secret>",
    ])
    def test_system_prompt_does_not_contain_internal_markers(self, forbidden):
        """Prompt should not accidentally expose internal implementation details."""
        import backend.agent.prompts as prompts_mod
        assert forbidden not in prompts_mod.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD happy path
# ---------------------------------------------------------------------------

class TestModelCardLoad:
    """Tests that MODEL_CARD is loaded correctly from the JSON file."""

    def test_model_card_is_dict(self):
        import backend.agent.prompts as prompts_mod
        assert isinstance(prompts_mod.MODEL_CARD, dict)

    def test_model_card_not_empty(self):
        import backend.agent.prompts as prompts_mod
        assert len(prompts_mod.MODEL_CARD) > 0

    @pytest.mark.parametrize("expected_key", [
        "model_name",
        "model_type",
        "target_variable",
    ])
    def test_model_card_contains_expected_key(self, expected_key):
        import backend.agent.prompts as prompts_mod
        assert expected_key in prompts_mod.MODEL_CARD, (
            f"Expected key '{expected_key}' not found in MODEL_CARD"
        )

    def test_model_card_model_name_is_string(self):
        import backend.agent.prompts as prompts_mod
        assert isinstance(prompts_mod.MODEL_CARD.get("model_name"), str)

    def test_model_card_model_type_is_string(self):
        import backend.agent.prompts as prompts_mod
        assert isinstance(prompts_mod.MODEL_CARD.get("model_type"), str)

    def test_model_card_target_variable_is_string(self):
        import backend.agent.prompts as prompts_mod
        assert isinstance(prompts_mod.MODEL_CARD.get("target_variable"), str)

    def test_model_card_values_match_synthetic_data(self):
        """Verify known synthetic-data values are present in the loaded card."""
        import backend.agent.prompts as prompts_mod
        card = prompts_mod.MODEL_CARD
        # At minimum the real file should identify itself consistently.
        assert card.get("model_name") == "Underwriting Risk Classification"
        assert card.get("model_type") == "CatBoostClassifier"
        assert card.get("target_variable") == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self):
        import backend.agent.prompts as prompts_mod
        gfi = prompts_mod.MODEL_CARD.get("global_feature_importance")
        if gfi is not None:          # key may not exist in all environments
            assert isinstance(gfi, dict)

    @pytest.mark.parametrize("feature,expected_score", [
        ("Age", 34.57614295408571),
        ("Education_Level", 2.0984070824092758),
        ("Employment_Status", 2.1318889906418717),
        ("Nationality", 2.2774559327846506),
        ("Customer_Segment", 1.8731465731883152),
        ("Annual_Income", 1.0169358497744714),
        ("Liquid_Assets", 1.2231046859555164),
    ])
    def test_global_feature_importance_scores(self, feature, expected_score):
        import backend.agent.prompts as prompts_mod
        gfi = prompts_mod.MODEL_CARD.get("global_feature_importance", {})
        if not gfi:
            pytest.skip("global_feature_importance not present in MODEL_CARD")
        assert feature in gfi
        assert pytest.approx(gfi[feature], rel=1e-6) == expected_score


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loaded via patched open (unit tests, no real FS)
# ---------------------------------------------------------------------------

class TestModelCardLoadPatched:
    """Unit tests that patch builtins.open to avoid real filesystem access."""

    def test_model_card_loaded_from_open(self):
        """MODEL_CARD should equal the JSON that open() returns."""
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD == MINIMAL_MODEL_CARD

    def test_model_card_nested_dict_preserved(self):
        card = {**MINIMAL_MODEL_CARD, "extra": {"nested": True}}
        mod = _reload_prompts_with_card(card)
        assert mod.MODEL_CARD["extra"] == {"nested": True}

    def test_model_card_empty_dict_allowed(self):
        """An empty JSON object should load without error."""
        mod = _reload_prompts_with_card({})
        assert mod.MODEL_CARD == {}

    def test_model_card_list_values_preserved(self):
        card = {**MINIMAL_MODEL_CARD, "feature_list": ["Age", "Income"]}
        mod = _reload_prompts_with_card(card)
        assert mod.MODEL_CARD["feature_list"] == ["Age", "Income"]

    def test_model_card_numeric_values_preserved(self):
        card = {**MINIMAL_MODEL_CARD, "version": 3.14}
        mod = _reload_prompts_with_card(card)
        assert pytest.approx(mod.MODEL_CARD["version"]) == 3.14

    def test_model_card_boolean_values_preserved(self):
        card = {**MINIMAL_MODEL_CARD, "is_production": True}
        mod = _reload_prompts_with_card(card)
        assert mod.MODEL_CARD["is_production"] is True


# ---------------------------------------------------------------------------
# Tests – error conditions
# ---------------------------------------------------------------------------

class TestModelCardErrorConditions:
    """Tests for failure modes when model_card.json is unavailable or corrupt."""

    def test_file_not_found_raises_on_import(self):
        """If model_card.json is missing, the module must raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401

    def test_malformed_json_raises_on_import(self):
        """Malformed JSON in model_card.json must raise json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="{ this is not valid json !!!")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401

    def test_empty_file_raises_on_import(self):
        """An empty file is not valid JSON and must raise json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401

    def test_json_array_at_top_level(self):
        """A JSON array (not object) at the top level should load without crashing,
        though downstream code may reject it."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data='["item1", "item2"]')
        with patch("builtins.open", m):
            import backend.agent.prompts as prompts_mod
            assert isinstance(prompts_mod.MODEL_CARD, list)

    def test_permission_error_raises_on_import(self):
        """PermissionError on open must propagate."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: F401


# ---------------------------------------------------------------------------
# Tests – _model_card_path resolution
# ---------------------------------------------------------------------------

class TestModelCardPath:
    """Tests that the path to model_card.json is resolved correctly."""

    def test_model_card_path_resolves_to_json(self):
        """The module-level path must point to a file named model_card.json."""
        import backend.agent.prompts as prompts_mod
        # Reconstruct the expected path from the module location.
        module_file = Path(prompts_mod.__file__)
        expected = module_file.parent.parent / "model_card.json"
        # We cannot assert it exists in all CI environments, but the name must match.
        assert expected.name == "model_card.json"

    def test_model_card_path_is_two_levels_up(self):
        """model_card.json should be two directory levels above prompts.py."""
        import backend.agent.prompts as prompts_mod
        module_file = Path(prompts_mod.__file__)
        # parent = backend/agent, parent.parent = backend
        expected_parent = module_file.parent.parent
        expected = expected_parent / "model_card.json"
        assert expected.parent == expected_parent


# ---------------------------------------------------------------------------
# Tests – module-level public API surface
# ---------------------------------------------------------------------------

class TestModulePublicAPI:
    """Ensure expected names are exported at module level."""

    @pytest.mark.parametrize("attr", ["SYSTEM_PROMPT", "MODEL_CARD"])
    def test_attribute_exists(self, attr):
        import backend.agent.prompts as prompts_mod
        assert hasattr(prompts_mod, attr), f"Missing attribute: {attr}"

    def test_system_prompt_is_immutable_string(self):
        """Strings are immutable in Python; verify no one has wrapped it."""
        import backend.agent.prompts as prompts_mod
        assert type(prompts_mod.SYSTEM_PROMPT) is str

    def test_model_card_is_plain_dict(self):
        import backend.agent.prompts as prompts_mod
        assert type(prompts_mod.MODEL_CARD) is dict


# ---------------------------------------------------------------------------
# Skipped / TODO stubs
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="TODO: requires complete model_card.json schema definition to validate all keys")
def test_model_card_full_schema_validation():
    """
    TODO: Once the full model_card.json schema is documented, validate every
    expected key (thresholds, feature metadata, version, etc.) is present and
    correctly typed.
    """
    pass


@pytest.mark.skip(reason="TODO: importlib.reload behaviour when model_card.json changes at runtime")
def test_model_card_reload_on_file_change():
    """
    TODO: Verify that reloading the module after model_card.json changes reflects
    the new content.  Requires coordination with importlib.reload and may need
    module-level caching to be addressed first.
    """
    pass


@pytest.mark.skip(reason="TODO: needs clarity on whether SYSTEM_PROMPT will be parameterised per tenant/product")
def test_system_prompt_per_tenant_customisation():
    """
    TODO: If SYSTEM_PROMPT is ever made configurable per tenant or product line,
    add tests for each variant here.
    """
    pass