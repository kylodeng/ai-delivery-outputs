"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json (a real JSON file at module load time)
- SYSTEM_PROMPT string content, type, and key behavioural constraints
- Edge cases around the model card JSON structure
- File-not-found and malformed-JSON error conditions at import time

Mocks used:
- unittest.mock.patch / mock_open: used to simulate file reading so tests are
  hermetic and do not depend on the real model_card.json being present on disk
- importlib: module is re-imported inside individual tests that need to control
  the file-system layer

TODOs:
- TODO: If MODEL_CARD gains a schema/validator, add schema-validation tests.
- TODO: Integration test that reads the real model_card.json and asserts all
  expected top-level keys are present (requires the real file to be available
  in CI).
"""

import builtins
import importlib
import json
import sys
import types
from io import StringIO
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

MINIMAL_MODEL_CARD_JSON = json.dumps(MINIMAL_MODEL_CARD)


def _reimport_prompts(fake_json: str = MINIMAL_MODEL_CARD_JSON):
    """
    Remove any cached version of the module and re-import it, patching the
    open() call so no real file is touched.
    """
    # Drop cached module so module-level code re-executes
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    m = mock_open(read_data=fake_json)
    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts():
    """
    Return the prompts module, isolating file I/O with a mock so the test
    suite is hermetic.  scope=module means it is loaded once per test session.
    """
    return _reimport_prompts()


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts):
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_describes_role(self, prompts):
        """The assistant must identify itself as an underwriting assistant."""
        assert "underwriting assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter_audience(self, prompts):
        """The prompt should be directed at an underwriter."""
        assert "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_no_disclosure_instruction(self, prompts):
        """
        The prompt must instruct the model never to disclose internal system
        instructions.
        """
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in lower or "reveal" in lower or "never" in lower

    def test_system_prompt_contains_assessment_capability(self, prompts):
        """The prompt should mention the ability to run assessments."""
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_helpful_assistant_persona(self, prompts):
        assert "helpful assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_expose_tool_names(self, prompts):
        """
        The system prompt itself should not leak concrete internal tool names.
        (Guards against accidental copy-paste of tool lists into the prompt.)
        """
        forbidden_fragments = ["<tool>", "tool_name", "function_call"]
        for fragment in forbidden_fragments:
            assert fragment not in prompts.SYSTEM_PROMPT

    @pytest.mark.parametrize(
        "required_phrase",
        [
            "underwriting assistant",
            "underwriter",
            "assessments",
        ],
    )
    def test_system_prompt_required_phrases(self, prompts, required_phrase):
        assert required_phrase.lower() in prompts.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading – happy path
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_not_empty(self, prompts):
        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_has_model_name(self, prompts):
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_has_model_type(self, prompts):
        assert "model_type" in prompts.MODEL_CARD

    def test_model_card_has_target_variable(self, prompts):
        assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_has_global_feature_importance(self, prompts):
        assert "global_feature_importance" in prompts.MODEL_CARD

    def test_model_card_model_name_value(self, prompts):
        assert prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type_value(self, prompts):
        assert prompts.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable_value(self, prompts):
        assert prompts.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_feature_importance_is_dict(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        assert isinstance(fi, dict)

    def test_model_card_feature_importance_age(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        assert "Age" in fi
        assert pytest.approx(fi["Age"], rel=1e-6) == 34.57614295408571

    @pytest.mark.parametrize(
        "feature,expected",
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
    def test_feature_importance_values(self, prompts, feature, expected):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        assert feature in fi
        assert pytest.approx(fi[feature], rel=1e-6) == expected

    def test_model_card_all_importance_values_are_floats(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in fi.items():
            assert isinstance(value, float), f"{key} should be float, got {type(value)}"

    def test_model_card_all_importance_values_positive(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in fi.items():
            assert value > 0, f"{key} importance should be positive"


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading – alternative / minimal JSON payloads
# ---------------------------------------------------------------------------


class TestModelCardVariants:
    def test_minimal_json_loads_correctly(self):
        minimal = json.dumps({"model_name": "Test"})
        mod = _reimport_prompts(fake_json=minimal)
        assert mod.MODEL_CARD == {"model_name": "Test"}

    def test_empty_json_object_loads(self):
        mod = _reimport_prompts(fake_json="{}")
        assert mod.MODEL_CARD == {}

    def test_nested_json_loads(self):
        nested = json.dumps({"level1": {"level2": {"level3": "value"}}})
        mod = _reimport_prompts(fake_json=nested)
        assert mod.MODEL_CARD["level1"]["level2"]["level3"] == "value"

    def test_json_with_list_values(self):
        data = json.dumps({"features": ["Age", "Income", "Assets"]})
        mod = _reimport_prompts(fake_json=data)
        assert mod.MODEL_CARD["features"] == ["Age", "Income", "Assets"]

    def test_json_with_numeric_values(self):
        data = json.dumps({"threshold": 0.75, "version": 3})
        mod = _reimport_prompts(fake_json=data)
        assert mod.MODEL_CARD["threshold"] == 0.75
        assert mod.MODEL_CARD["version"] == 3


# ---------------------------------------------------------------------------
# Tests: Error conditions at import / module load time
# ---------------------------------------------------------------------------


class TestErrorConditions:
    def test_file_not_found_raises_on_import(self):
        """If model_card.json does not exist, opening it must raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_malformed_json_raises_on_import(self):
        """Malformed JSON in model_card.json must surface as json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        m = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_empty_file_raises_on_import(self):
        """An empty model_card.json must surface as json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        m = mock_open(read_data="")
        with patch("builtins.open", m):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_permission_error_propagates(self):
        """A PermissionError on the JSON file must propagate unchanged."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=PermissionError("access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: F401, PLC0415


# ---------------------------------------------------------------------------
# Tests: _model_card_path resolution
# ---------------------------------------------------------------------------


class TestModelCardPath:
    def test_model_card_path_attribute_exists(self, prompts):
        """The module should expose _model_card_path (Path object)."""
        assert hasattr(prompts, "_model_card_path")

    def test_model_card_path_is_path_instance(self, prompts):
        assert isinstance(prompts._model_card_path, Path)

    def test_model_card_path_ends_with_model_card_json(self, prompts):
        assert prompts._model_card_path.name == "model_card.json"

    def test_model_card_path_parent_is_backend_directory(self, prompts):
        """
        The JSON file should live two levels above the prompts.py file,
        i.e. in the 'backend' directory.
        """
        path = prompts._model_card_path
        # Path: backend/model_card.json → parent should be named 'backend'
        assert path.parent.name == "backend"


# ---------------------------------------------------------------------------
# Skipped / stub tests requiring additional context
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="TODO: requires real model_card.json on disk in CI environment "
    "to validate full schema including all expected top-level keys."
)
def test_real_model_card_schema_integration():
    """
    TODO: Read the actual model_card.json from disk and assert all expected
    top-level keys (model_name, model_type, target_variable,
    global_feature_importance, …) are present and of correct types.
    Requires the real backend/model_card.json to be present during test run.
    """
    pass


@pytest.mark.skip(
    reason="TODO: If a SYSTEM_PROMPT version/hash is introduced for auditing, "
    "add a test that asserts the expected hash value."
)
def test_system_prompt_version_hash():
    """
    TODO: Add a deterministic hash check for SYSTEM_PROMPT to detect
    accidental edits to safety-critical instructions.
    """
    pass


@pytest.mark.skip(
    reason="TODO: Requires knowledge of additional public constants or "
    "functions that may be added to prompts.py in the future."
)
def test_additional_public_api():
    """
    TODO: If prompts.py exposes additional constants or helper functions
    (e.g. build_prompt(), ASSESSMENT_PROMPT), add corresponding tests here.
    """
    pass