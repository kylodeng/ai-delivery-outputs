"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD: successful loading and structure of the MODEL_CARD JSON constant
- SYSTEM_PROMPT: content, type, and behavioural constraints of the system prompt string
- Module-level file path construction (_model_card_path)
- Error conditions when model_card.json is missing or malformed

Mocks used:
- unittest.mock.patch / mock_open: to mock file I/O for model_card.json loading
- tmp_path (pytest fixture): to create temporary model_card.json files for integration-style tests

TODOs:
- TODO: Full model_card.json schema validation requires the complete file — stub tests provided for unknown fields
"""

import importlib
import json
import sys
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
    "system instructions",
    "tools",
    "helpful assistant",
]


def _reload_prompts_with_card(model_card_dict: dict):
    """
    Reload backend.agent.prompts with a mocked model_card.json.
    Returns the freshly-imported module.
    """
    serialised = json.dumps(model_card_dict)
    # Remove any previously cached version of the module so the module-level
    # code (open / json.load) is executed again.
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", mock_open(read_data=serialised)):
        with patch("pathlib.Path.open", mock_open(read_data=serialised)):
            # The module uses the plain built-in open, so patching builtins.open
            # inside the module's own namespace is the most reliable approach.
            import importlib

            with patch("backend.agent.prompts.__file__", __file__, create=True):
                mod = importlib.import_module("backend.agent.prompts")
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts_module():
    """
    Return the already-imported prompts module.  Because the module is loaded
    at collection time (it's imported at the top of the test run) we simply
    import it here; the file must exist on disk for this fixture to work.
    """
    import importlib

    import backend.agent.prompts as prompts_mod

    return prompts_mod


# ---------------------------------------------------------------------------
# Tests – _model_card_path
# ---------------------------------------------------------------------------


class TestModelCardPath:
    def test_path_is_path_object(self, prompts_module):
        path = prompts_module._model_card_path
        assert isinstance(path, Path)

    def test_path_ends_with_model_card_json(self, prompts_module):
        assert prompts_module._model_card_path.name == "model_card.json"

    def test_path_points_to_backend_directory(self, prompts_module):
        """
        The path should resolve to <repo_root>/backend/model_card.json, i.e.
        the grandparent of the prompts.py file is 'backend'.
        """
        path = prompts_module._model_card_path
        # grandparent of backend/agent/prompts.py is backend/
        assert path.parent.name == "backend" or path.parts[-2] == "backend"

    def test_path_sibling_of_agent_directory(self, prompts_module):
        """model_card.json lives one level above the agent package."""
        agent_dir = Path(prompts_module.__file__).parent
        expected = agent_dir.parent / "model_card.json"
        assert prompts_module._model_card_path == expected


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading (happy path)
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_not_empty(self, prompts_module):
        assert len(prompts_module.MODEL_CARD) > 0

    def test_model_card_has_model_name(self, prompts_module):
        assert "model_name" in prompts_module.MODEL_CARD

    def test_model_card_model_name_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type(self, prompts_module):
        assert "model_type" in prompts_module.MODEL_CARD

    def test_model_card_model_type_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_has_target_variable(self, prompts_module):
        assert "target_variable" in prompts_module.MODEL_CARD

    def test_model_card_target_variable_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_has_global_feature_importance(self, prompts_module):
        assert "global_feature_importance" in prompts_module.MODEL_CARD

    def test_global_feature_importance_is_dict(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert isinstance(gfi, dict)

    def test_global_feature_importance_age_key(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert "Age" in gfi

    def test_global_feature_importance_age_is_numeric(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert isinstance(gfi["Age"], (int, float))

    def test_global_feature_importance_age_value(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(gfi["Age"], rel=1e-3) == 34.576

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
    def test_global_feature_importance_known_features_present(self, prompts_module, feature):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert feature in gfi

    @pytest.mark.parametrize(
        "feature, expected",
        [
            ("Education_Level", 2.0984070824092758),
            ("Employment_Status", 2.1318889906418717),
            ("Nationality", 2.2774559327846506),
            ("Customer_Segment", 1.8731465731883152),
            ("Annual_Income", 1.0169358497744714),
            ("Liquid_Assets", 1.2231046859555164),
        ],
    )
    def test_global_feature_importance_values(self, prompts_module, feature, expected):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(gfi[feature], rel=1e-4) == expected

    def test_all_feature_importance_values_positive(self, prompts_module):
        gfi = prompts_module.MODEL_CARD["global_feature_importance"]
        for key, val in gfi.items():
            assert val >= 0, f"Feature importance for '{key}' must be non-negative"

    @pytest.mark.skip(reason="TODO: full schema unknown — add tests once complete model_card.json is available")
    def test_model_card_full_schema(self, prompts_module):
        """TODO: validate every field in the complete model_card.json schema."""
        pass


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD reloading with controlled JSON (using tmp_path)
# ---------------------------------------------------------------------------


class TestModelCardReloadWithTmpPath:
    """
    These tests write a temporary model_card.json to disk and patch Path so
    the module reads from that file, ensuring the loading logic is exercised
    end-to-end without depending on the real file.
    """

    def _reload(self, tmp_path: Path, card: dict):
        """Write card to a tmp file and reload the module pointing at it."""
        model_card_file = tmp_path / "model_card.json"
        model_card_file.write_text(json.dumps(card))

        sys.modules.pop("backend.agent.prompts", None)

        with patch("pathlib.Path.__truediv__", side_effect=lambda self, other: model_card_file if str(other) == "model_card.json" else Path.__truediv__(self, other)):
            # Simpler approach: patch the constant path directly after import
            pass

        # Directly patch the open call to return our tmp file content
        with patch("builtins.open", mock_open(read_data=model_card_file.read_text())):
            mod = importlib.import_module("backend.agent.prompts")
        return mod

    def test_minimal_model_card_loads(self, tmp_path):
        mod = self._reload(tmp_path, MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_empty_dict_model_card(self, tmp_path):
        mod = self._reload(tmp_path, {})
        assert mod.MODEL_CARD == {}

    def test_extra_fields_in_model_card(self, tmp_path):
        card = {**MINIMAL_MODEL_CARD, "extra_field": "extra_value"}
        mod = self._reload(tmp_path, card)
        assert mod.MODEL_CARD.get("extra_field") == "extra_value"

    def test_nested_model_card(self, tmp_path):
        card = {**MINIMAL_MODEL_CARD, "nested": {"a": {"b": 1}}}
        mod = self._reload(tmp_path, card)
        assert mod.MODEL_CARD["nested"]["a"]["b"] == 1


# ---------------------------------------------------------------------------
# Tests – MODULE_CARD error conditions
# ---------------------------------------------------------------------------


class TestModelCardErrorConditions:
    def test_file_not_found_raises(self):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("model_card.json not found")):
            with pytest.raises(FileNotFoundError):
                importlib.import_module("backend.agent.prompts")

    def test_invalid_json_raises(self):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data="{invalid json!!!")):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module("backend.agent.prompts")

    def test_empty_file_raises(self):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data="")):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                importlib.import_module("backend.agent.prompts")

    def test_permission_error_raises(self):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("permission denied")):
            with pytest.raises(PermissionError):
                importlib.import_module("backend.agent.prompts")

    def test_json_array_instead_of_object(self):
        """MODEL_CARD is expected to be a dict; a JSON array should load but be a list."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data="[1, 2, 3]")):
            mod = importlib.import_module("backend.agent.prompts")
            assert isinstance(mod.MODEL_CARD, list)

    def teardown_method(self, method):
        # Ensure the real module is restored after each error test
        sys.modules.pop("backend.agent.prompts", None)
        try:
            importlib.import_module("backend.agent.prompts")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT type and basic properties
# ---------------------------------------------------------------------------


class TestSystemPromptType:
    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self, prompts_module):
        assert len(prompts_module.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_is_non_trivially_long(self, prompts_module):
        """The prompt should be substantive, not just a few characters."""
        assert len(prompts_module.SYSTEM_PROMPT) > 50


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT content / behavioural constraints
# ---------------------------------------------------------------------------


class TestSystemPromptContent:
    @pytest.mark.parametrize("fragment", SYSTEM_PROMPT_EXPECTED_FRAGMENTS)
    def test_system_prompt_contains_fragment(self, prompts_module, fragment):
        assert fragment.lower() in prompts_module.SYSTEM_PROMPT.lower(), (
            f"SYSTEM_PROMPT should contain '{fragment}'"
        )

    def test_system_prompt_mentions_underwriting(self, prompts_module):
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower() or \
               "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_disclosure_instruction(self, prompts_module):
        """Must instruct the model NOT to disclose internal instructions."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "never disclose" in prompt_lower or "cannot disclose" in prompt_lower or \
               "can never disclose" in prompt_lower

    def test_system_prompt_no_tool_revelation_instruction(self, prompts_module):
        """The prompt should restrict revealing the tools."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "tools" in prompt_lower

    def test_system_prompt_mentions_helpful_assistant(self, prompts_module):
        assert "helpful assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts_module):
        assert "assessment" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_start_with_whitespace(self, prompts_module):
        assert prompts_module.SYSTEM_PROMPT == prompts_module.SYSTEM_PROMPT.lstrip() or \
               prompts_module.SYSTEM_PROMPT[0] != "\n"

    def test_