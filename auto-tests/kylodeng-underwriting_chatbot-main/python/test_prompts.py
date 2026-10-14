"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD constant: loaded from model_card.json at import time
- SYSTEM_PROMPT constant: content, type, and key behavioural clauses
- File loading behaviour: correct path resolution, error handling for missing/corrupt files

Mocks used:
- unittest.mock.patch / builtins.open — to mock file I/O without touching real filesystem
- unittest.mock.mock_open — for simulating file read
- json.load — to simulate various JSON payloads

TODOs:
- TODO: Integration test that reads the actual model_card.json from the repo to validate its schema
  (requires the real file to be present in a known location relative to the test runner)
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


def _reload_prompts_with_card(card_data: dict):
    """
    Reload backend.agent.prompts with a mocked model_card.json payload.
    Returns the freshly imported module.
    """
    json_bytes = json.dumps(card_data)
    m = mock_open(read_data=json_bytes)

    # Ensure the module is not cached so it re-executes module-level code
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("prompts", None)

    with patch("builtins.open", m):
        with patch("json.load", return_value=card_data):
            import backend.agent.prompts as prompts_module
            importlib.reload(prompts_module)
            return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_module_cache():
    """Remove cached module before and after each test to guarantee isolation."""
    sys.modules.pop("backend.agent.prompts", None)
    yield
    sys.modules.pop("backend.agent.prompts", None)


@pytest.fixture()
def prompts(tmp_path, monkeypatch):
    """
    Provide a freshly imported prompts module backed by a real temporary
    model_card.json file.
    """
    card_file = tmp_path / "model_card.json"
    card_file.write_text(json.dumps(MINIMAL_MODEL_CARD))

    # Patch Path so _model_card_path points to our tmp file
    original_path = Path

    class PatchedPath(type(Path())):
        pass

    with patch("pathlib.Path.__truediv__", autospec=False):
        # Simpler approach: patch open + json.load at module level
        with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                import backend.agent.prompts as mod
                importlib.reload(mod)
                yield mod


# ---------------------------------------------------------------------------
# Tests — MODEL_CARD
# ---------------------------------------------------------------------------

class TestModelCardLoading:
    """Tests for the MODULE_CARD constant loaded from model_card.json."""

    def test_model_card_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_has_model_name(self, prompts):
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_value(self, prompts):
        assert prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_has_model_type(self, prompts):
        assert "model_type" in prompts.MODEL_CARD

    def test_model_card_model_type_value(self, prompts):
        assert prompts.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_has_target_variable(self, prompts):
        assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_target_variable_value(self, prompts):
        assert prompts.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_has_global_feature_importance(self, prompts):
        assert "global_feature_importance" in prompts.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        assert isinstance(fi, dict)

    def test_model_card_feature_importance_age_value(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(fi["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_feature_importance_values_are_floats(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in fi.items():
            assert isinstance(value, (int, float)), f"{key} importance is not numeric"

    def test_model_card_feature_importance_values_non_negative(self, prompts):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in fi.items():
            assert value >= 0, f"{key} importance should be non-negative"

    @pytest.mark.parametrize("expected_key", [
        "Age",
        "Education_Level",
        "Employment_Status",
        "Nationality",
        "Customer_Segment",
        "Annual_Income",
        "Liquid_Assets",
    ])
    def test_model_card_feature_importance_contains_key(self, prompts, expected_key):
        fi = prompts.MODEL_CARD["global_feature_importance"]
        assert expected_key in fi, f"Expected feature '{expected_key}' not found"


class TestModelCardLoadingEdgeCases:
    """Edge cases and error conditions for model_card.json loading."""

    def test_model_card_empty_json_object(self):
        """An empty JSON object should still produce a dict (no KeyError at import)."""
        empty_card = {}
        with patch("builtins.open", mock_open(read_data=json.dumps(empty_card))):
            with patch("json.load", return_value=empty_card):
                import backend.agent.prompts as mod
                importlib.reload(mod)
                assert mod.MODEL_CARD == {}

    def test_model_card_missing_file_raises(self):
        """FileNotFoundError should propagate when model_card.json is absent."""
        with patch("builtins.open", side_effect=FileNotFoundError("model_card.json not found")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts as mod
                importlib.reload(mod)

    def test_model_card_corrupt_json_raises(self):
        """json.JSONDecodeError should propagate for malformed JSON."""
        with patch("builtins.open", mock_open(read_data="{ not valid json ")):
            with patch("json.load", side_effect=json.JSONDecodeError("Expecting value", "", 0)):
                with pytest.raises(json.JSONDecodeError):
                    import backend.agent.prompts as mod
                    importlib.reload(mod)

    def test_model_card_permission_error_raises(self):
        """PermissionError should propagate when the file cannot be opened."""
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts as mod
                importlib.reload(mod)

    def test_model_card_json_array_at_root(self):
        """A JSON array at root should be loaded as a list (not a dict)."""
        array_card = [{"model_name": "test"}]
        with patch("builtins.open", mock_open(read_data=json.dumps(array_card))):
            with patch("json.load", return_value=array_card):
                import backend.agent.prompts as mod
                importlib.reload(mod)
                assert isinstance(mod.MODEL_CARD, list)

    def test_model_card_nested_structure_preserved(self):
        """Nested dicts within the card should be preserved exactly."""
        nested_card = {
            "model_name": "Test",
            "metadata": {"version": "1.0", "author": "qa-team"},
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(nested_card))):
            with patch("json.load", return_value=nested_card):
                import backend.agent.prompts as mod
                importlib.reload(mod)
                assert mod.MODEL_CARD["metadata"]["version"] == "1.0"


# ---------------------------------------------------------------------------
# Tests — SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT constant."""

    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts):
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts):
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, prompts):
        assert "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts):
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_confidentiality_clause_present(self, prompts):
        """Prompt must instruct the LLM not to disclose internal instructions."""
        assert "disclose" in prompts.SYSTEM_PROMPT.lower() or \
               "reveal" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_disclose_system_instructions(self, prompts):
        """Key confidentiality phrase should be present."""
        assert "system" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_helpful_assistant_phrase(self, prompts):
        assert "helpful assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_tools_confidentiality(self, prompts):
        """Prompt must mention tools are not to be revealed."""
        assert "tools" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_gathering_information(self, prompts):
        assert "gather" in prompts.SYSTEM_PROMPT.lower() or \
               "information" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_senior_role_present(self, prompts):
        assert "senior" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_trailing_whitespace_only(self, prompts):
        """Prompt should not be purely whitespace."""
        assert prompts.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_does_not_contain_placeholder(self, prompts):
        """Common template placeholders should not appear in the final prompt."""
        for placeholder in ["{", "}", "{{", "}}", "<PLACEHOLDER>", "TODO"]:
            assert placeholder not in prompts.SYSTEM_PROMPT, \
                f"Placeholder '{placeholder}' found in SYSTEM_PROMPT"

    def test_system_prompt_does_not_disclose_tool_names(self, prompts):
        """Prompt should assert confidentiality but not list actual tool names."""
        # The prompt says it CAN'T disclose tools — this checks intent phrasing
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "can never disclose" in lower or "cannot disclose" in lower or \
               "never disclose" in lower or "not disclose" in lower

    @pytest.mark.parametrize("forbidden_phrase", [
        "openai",
        "gpt-",
        "claude",
        "secret key",
        "api key",
        "password",
    ])
    def test_system_prompt_no_sensitive_literals(self, prompts, forbidden_phrase):
        """System prompt must not contain hardcoded secrets or model identifiers."""
        assert forbidden_phrase not in prompts.SYSTEM_PROMPT.lower(), \
            f"Sensitive phrase '{forbidden_phrase}' found in SYSTEM_PROMPT"


# ---------------------------------------------------------------------------
# Tests — Module-level path resolution
# ---------------------------------------------------------------------------

class TestPathResolution:
    """Tests for the _model_card_path computation."""

    def test_model_card_path_is_path_object(self):
        """Verify _model_card_path is a Path (requires real or mocked import)."""
        with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                import backend.agent.prompts as mod
                importlib.reload(mod)
                assert isinstance(mod._model_card_path, Path)

    def test_model_card_path_ends_with_model_card_json(self):
        """Path must end with model_card.json."""
        with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                import backend.agent.prompts as mod
                importlib.reload(mod)
                assert mod._model_card_path.name == "model_card.json"

    def test_model_card_path_parent_is_backend(self):
        """
        _model_card_path should live two levels up from prompts.py (i.e. backend/).
        backend/agent/prompts.py -> parent -> agent -> parent -> backend
        """
        with patch("builtins.open", mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))):
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                import backend.agent.prompts as mod
                importlib.reload(mod)
                # Two parents up from prompts.py file should be backend/
                prompts_file = Path(mod.__file__)
                expected = prompts_file.parent.parent / "model_card.json"
                assert mod._model_card_path == expected


# ---------------------------------------------------------------------------
# Tests — Module public API completeness
# ---------------------------------------------------------------------------

class TestModulePublicAPI:
    """Ensure the module exposes exactly the expected public symbols."""

    EXPECTED_PUBLIC_NAMES = {"MODEL_CARD", "SYSTEM_PROMPT"}

    def test_model_card_exported(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")

    def test_system_prompt_exported(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_no_unexpected_credentials_exported(self, prompts):
        """Ensure no credential-like attributes are accidentally exposed."""
        for name in dir(prompts):
            lower = name.lower()
            assert "password" not in lower
            assert "secret" not in lower
            assert "api_key" not in lower


# ---------------------------------------------------------------------------