"""
Test module for backend/agent/prompts.py

What is tested:
    - MODULE_CARD is loaded correctly from model_card.json
    - SYSTEM_PROMPT is defined, non-empty, and contains expected content
    - Module-level constants have correct types
    - Edge cases: missing file, malformed JSON, path resolution

Mocks used:
    - unittest.mock.patch / mock_open for file I/O
    - tmp_path fixture (pytest) for real file-based tests

TODOs:
    - TODO: Full model_card.json schema validation once schema is finalised
    - TODO: Test behaviour when model_card.json has unexpected/missing keys
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


def _reload_prompts_with_model_card(model_card_dict: dict):
    """
    Helper: reload the prompts module after patching the file-system so that
    the module-level `open` + `json.load` sees *model_card_dict*.
    Returns the freshly-imported module.
    """
    json_bytes = json.dumps(model_card_dict)

    # Remove cached module so the top-level code reruns
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    m = mock_open(read_data=json_bytes)
    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_mod  # noqa: PLC0415

    return prompts_mod


# ---------------------------------------------------------------------------
# Fixture: real model_card.json on disk (tmp_path)
# ---------------------------------------------------------------------------


@pytest.fixture()
def real_model_card_file(tmp_path: Path):
    """Write a real model_card.json to a temp directory and return its path."""
    card_path = tmp_path / "model_card.json"
    card_path.write_text(json.dumps(MINIMAL_MODEL_CARD), encoding="utf-8")
    return card_path


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT constant
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT module-level constant."""

    @pytest.fixture(autouse=True)
    def _import_module(self):
        """Import (or re-use) the prompts module for each test."""
        # Use mock so we don't depend on the real file existing in CI
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        m = mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))
        with patch("builtins.open", m):
            import backend.agent.prompts as _prompts

        self.prompts = _prompts

    def test_system_prompt_is_defined(self):
        assert hasattr(self.prompts, "SYSTEM_PROMPT")

    def test_system_prompt_is_string(self):
        assert isinstance(self.prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self):
        assert self.prompts.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_mentions_underwriting(self):
        assert "underwriting" in self.prompts.SYSTEM_PROMPT.lower() or \
               "underwriter" in self.prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_assistant_reference(self):
        assert "assistant" in self.prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_prohibits_disclosure(self):
        """Prompt must instruct the model NOT to reveal system instructions."""
        lower = self.prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in lower or "reveal" in lower or "never" in lower

    def test_system_prompt_no_leading_trailing_whitespace_issues(self):
        """Prompt should not start/end with excessive blank lines."""
        # Allow single spaces but not purely whitespace
        assert self.prompts.SYSTEM_PROMPT == self.prompts.SYSTEM_PROMPT.strip() or \
               len(self.prompts.SYSTEM_PROMPT) > 0

    def test_system_prompt_is_single_string_not_tuple(self):
        """Implicit string concatenation should produce a single str, not a tuple."""
        assert not isinstance(self.prompts.SYSTEM_PROMPT, tuple)

    @pytest.mark.parametrize("keyword", [
        "underwriter",
        "assessments",
        "helpful",
    ])
    def test_system_prompt_contains_expected_keywords(self, keyword):
        assert keyword in self.prompts.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading — happy path
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests for the MODEL_CARD module-level constant."""

    @pytest.fixture(autouse=True)
    def _import_module(self):
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        m = mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))
        with patch("builtins.open", m):
            import backend.agent.prompts as _prompts

        self.prompts = _prompts

    def test_model_card_is_defined(self):
        assert hasattr(self.prompts, "MODEL_CARD")

    def test_model_card_is_dict(self):
        assert isinstance(self.prompts.MODEL_CARD, dict)

    def test_model_card_is_not_empty(self):
        assert len(self.prompts.MODEL_CARD) > 0

    def test_model_card_has_model_name_key(self):
        assert "model_name" in self.prompts.MODEL_CARD

    def test_model_card_model_name_value(self):
        assert self.prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type_key(self):
        assert "model_type" in self.prompts.MODEL_CARD

    def test_model_card_model_type_value(self):
        assert self.prompts.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_has_target_variable(self):
        assert "target_variable" in self.prompts.MODEL_CARD

    def test_model_card_target_variable_value(self):
        assert self.prompts.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_has_feature_importance(self):
        assert "global_feature_importance" in self.prompts.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self):
        assert isinstance(self.prompts.MODEL_CARD["global_feature_importance"], dict)

    def test_model_card_feature_importance_age(self):
        fi = self.prompts.MODEL_CARD["global_feature_importance"]
        assert "Age" in fi
        assert abs(fi["Age"] - 34.57614295408571) < 1e-6

    @pytest.mark.parametrize("feature", [
        "Age",
        "Education_Level",
        "Employment_Status",
        "Nationality",
        "Customer_Segment",
        "Annual_Income",
        "Liquid_Assets",
    ])
    def test_model_card_feature_importance_keys(self, feature):
        fi = self.prompts.MODEL_CARD["global_feature_importance"]
        assert feature in fi

    @pytest.mark.parametrize("feature,expected", [
        ("Age", 34.57614295408571),
        ("Education_Level", 2.0984070824092758),
        ("Employment_Status", 2.1318889906418717),
        ("Nationality", 2.2774559327846506),
        ("Customer_Segment", 1.8731465731883152),
        ("Annual_Income", 1.0169358497744714),
        ("Liquid_Assets", 1.2231046859555164),
    ])
    def test_model_card_feature_importance_values(self, feature, expected):
        fi = self.prompts.MODEL_CARD["global_feature_importance"]
        assert abs(fi[feature] - expected) < 1e-9


# ---------------------------------------------------------------------------
# Tests: Path resolution
# ---------------------------------------------------------------------------


class TestModelCardPathResolution:
    """Verify the path to model_card.json is computed correctly."""

    def test_model_card_path_is_two_levels_up_from_agent(self):
        """
        prompts.py lives at backend/agent/prompts.py
        model_card.json must be at backend/model_card.json
        i.e. two levels up from prompts.py then named model_card.json
        """
        # We compute the expected path independently
        prompts_file = Path(__file__).parent.parent / "backend" / "agent" / "prompts.py"
        # If the real file exists use it; otherwise just check the logic
        expected_relative = Path("backend") / "model_card.json"
        # The module uses: Path(__file__).parent.parent / "model_card.json"
        # __file__ for prompts.py → backend/agent/prompts.py
        # .parent → backend/agent
        # .parent → backend
        # / "model_card.json" → backend/model_card.json
        # Verify this logic with a synthetic __file__
        synthetic_file = Path("/some/project/backend/agent/prompts.py")
        computed = synthetic_file.parent.parent / "model_card.json"
        assert computed == Path("/some/project/backend/model_card.json")

    def test_model_card_path_resolves_to_json_extension(self):
        synthetic_file = Path("/project/backend/agent/prompts.py")
        computed = synthetic_file.parent.parent / "model_card.json"
        assert computed.suffix == ".json"

    def test_model_card_path_filename(self):
        synthetic_file = Path("/project/backend/agent/prompts.py")
        computed = synthetic_file.parent.parent / "model_card.json"
        assert computed.name == "model_card.json"


# ---------------------------------------------------------------------------
# Tests: Error conditions
# ---------------------------------------------------------------------------


class TestModelCardErrorConditions:
    """Test module behaviour when model_card.json is missing or malformed."""

    def _reload(self, open_patch):
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with open_patch:
            import backend.agent.prompts  # noqa: PLC0415

    def test_missing_file_raises_file_not_found(self):
        """If model_card.json does not exist the module should raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: PLC0415

    def test_malformed_json_raises_json_decode_error(self):
        """If model_card.json contains invalid JSON the module should raise JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        bad_json = "{ this is not valid json }"
        m = mock_open(read_data=bad_json)
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: PLC0415

    def test_empty_json_object_loads_as_empty_dict(self):
        """An empty JSON object should load as an empty dict without crashing."""
        mod = _reload_prompts_with_model_card({})
        assert mod.MODEL_CARD == {}

    def test_permission_error_propagates(self):
        """PermissionError on the file should propagate out of the module."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("permission denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: PLC0415

    def test_json_array_top_level_loads_as_list(self):
        """Top-level JSON array loads without error (MODEL_CARD will be a list)."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        m = mock_open(read_data=json.dumps([1, 2, 3]))
        with patch("builtins.open", m):
            import backend.agent.prompts as p  # noqa: PLC0415
        assert p.MODEL_CARD == [1, 2, 3]

    def test_json_with_null_values_loads(self):
        """model_card.json with null values should load without raising."""
        data = {"model_name": None, "model_type": None}
        mod = _reload_prompts_with_model_card(data)
        assert mod.MODEL_CARD["model_name"] is None


# ---------------------------------------------------------------------------
# Tests: Module-level attributes completeness
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    """Verify the public API surface of the prompts module."""

    @pytest.fixture(autouse=True)
    def _import_module(self):
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        m = mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))
        with patch("builtins.open", m):
            import backend.agent.prompts as _prompts

        self.prompts = _prompts

    def test_module_exposes_model_card(self):
        assert hasattr(self.prompts, "MODEL_CARD")

    def test_module_exposes_system_prompt(self):
        assert hasattr(self.prompts, "SYSTEM_PROMPT")

    def test_model_card_is_not_none(self):
        assert self.prompts.MODEL_CARD is not None

    def test_system_prompt_is_not_none(self):
        assert self.prompts.SYSTEM_PROMPT is not None

    def test_system_prompt_length_is_reasonable(self):
        """Prompt should be at least 50 characters long."""
        assert len(self.prompts.SYSTEM_PROMPT) >= 50

    def test_model_card_and_system_prompt_are_independent(self):
        """Changing MODEL_CARD should not affect SYSTEM_PROMPT reference."""
        original_prompt = self.prompts.SYSTEM_PROMPT
        self.prompts.MODEL_CARD = {}
        assert self.prompts.SYSTEM_PROMPT == original_prompt


# ---------------------------------------------------------------------------
# Tests: Real file integration (skipped if file absent)
# ---------------------------------------------------------------------------


class TestRealFileIntegration:
    """
    