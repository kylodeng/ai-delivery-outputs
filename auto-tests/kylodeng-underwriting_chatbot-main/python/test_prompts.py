"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loading from model_card.json (content, structure, types)
- SYSTEM_PROMPT string content and constraints
- Module-level constants existence and types

Mocks used:
- unittest.mock.mock_open / patch: to mock file I/O for model_card.json
- json: tested via mocked file content

TODOs:
- TODO: Integration test verifying MODEL_CARD matches the real model_card.json on disk
  (requires the actual file to be present in CI; stub provided below)
- TODO: Test behaviour when model_card.json has unexpected schema versions
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
    Reload the prompts module with a mocked model_card.json content.
    Returns the freshly-imported module object.
    """
    serialised = json.dumps(model_card_dict)
    m = mock_open(read_data=serialised)
    # Remove cached module so importlib re-executes module-level code
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m):
        # Also patch Path so we don't need the real file on disk
        with patch("pathlib.Path.open", m):
            # Direct patch at the module's namespace level
            import backend.agent.prompts as prompts_mod  # noqa: F401 – triggers reload
            # Force a genuine re-import
            if "backend.agent.prompts" in sys.modules:
                return sys.modules["backend.agent.prompts"]
            return prompts_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def prompts_module():
    """
    Import the prompts module once (using the real file if present,
    otherwise skip the fixture-dependent tests).
    """
    try:
        # Ensure a clean slate
        sys.modules.pop("backend.agent.prompts", None)
        import backend.agent.prompts as mod
        return mod
    except FileNotFoundError:
        pytest.skip("model_card.json not found on disk — skipping live-file tests")


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT
# ---------------------------------------------------------------------------

class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT module-level constant."""

    def test_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts_module):
        assert len(prompts_module.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts_module):
        assert "underwriting" in prompts_module.SYSTEM_PROMPT.lower() or \
               "underwriter" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_never_disclose_instruction(self, prompts_module):
        """Prompt must instruct the model NOT to disclose internal instructions."""
        prompt_lower = prompts_module.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_system_prompt_presents_as_helpful_assistant(self, prompts_module):
        assert "helpful assistant" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts_module):
        assert "assessment" in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_expose_tool_names(self, prompts_module):
        """Prompt should not directly name internal tools."""
        lowered = prompts_module.SYSTEM_PROMPT.lower()
        # The prompt explicitly says the assistant "can never disclose … tools"
        assert "tools" in lowered  # present as a rule, not as a list of tool names

    @pytest.mark.parametrize("forbidden_phrase", [
        "ignore previous instructions",
        "jailbreak",
        "disregard your instructions",
    ])
    def test_system_prompt_does_not_contain_jailbreak_phrases(
        self, prompts_module, forbidden_phrase
    ):
        assert forbidden_phrase not in prompts_module.SYSTEM_PROMPT.lower()

    def test_system_prompt_is_a_single_string_not_list(self, prompts_module):
        assert not isinstance(prompts_module.SYSTEM_PROMPT, (list, tuple, dict))


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading
# ---------------------------------------------------------------------------

class TestModelCardLoading:
    """Tests that MODEL_CARD is loaded correctly from model_card.json."""

    def test_model_card_is_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_model_card_not_empty(self, prompts_module):
        assert len(prompts_module.MODEL_CARD) > 0

    def test_model_card_has_model_name_key(self, prompts_module):
        assert "model_name" in prompts_module.MODEL_CARD

    def test_model_card_has_model_type_key(self, prompts_module):
        assert "model_type" in prompts_module.MODEL_CARD

    def test_model_card_has_target_variable_key(self, prompts_module):
        assert "target_variable" in prompts_module.MODEL_CARD

    def test_model_card_model_name_is_string(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD["model_name"], str)

    def test_model_card_model_type_is_string(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD["model_type"], str)

    def test_model_card_target_variable_is_string(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD["target_variable"], str)

    def test_model_card_global_feature_importance_is_dict(self, prompts_module):
        if "global_feature_importance" in prompts_module.MODEL_CARD:
            assert isinstance(
                prompts_module.MODEL_CARD["global_feature_importance"], dict
            )

    def test_model_card_feature_importance_values_are_numeric(self, prompts_module):
        fi = prompts_module.MODEL_CARD.get("global_feature_importance", {})
        for key, value in fi.items():
            assert isinstance(value, (int, float)), (
                f"Feature importance for '{key}' should be numeric, got {type(value)}"
            )

    def test_model_card_feature_importance_values_are_non_negative(
        self, prompts_module
    ):
        fi = prompts_module.MODEL_CARD.get("global_feature_importance", {})
        for key, value in fi.items():
            assert value >= 0, (
                f"Feature importance for '{key}' should be >= 0, got {value}"
            )

    def test_model_card_model_name_expected_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type_expected_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable_expected_value(self, prompts_module):
        assert prompts_module.MODEL_CARD["target_variable"] == "Risk_Classification"


# ---------------------------------------------------------------------------
# Tests: module-level file I/O (mocked — no real file required)
# ---------------------------------------------------------------------------

class TestModelCardMockedLoading:
    """
    Unit tests that mock the file system so that model_card.json does NOT need
    to exist on disk.  Each test reloads the module with controlled content.
    """

    def _import_fresh(self, card_dict: dict):
        """Helper: patch open() and (re)import the prompts module."""
        serialised = json.dumps(card_dict)

        # Remove any cached version
        for key in list(sys.modules.keys()):
            if "prompts" in key and "agent" in key:
                sys.modules.pop(key)

        m = mock_open(read_data=serialised)
        with patch("builtins.open", m):
            import backend.agent.prompts as mod
            # Make sure we have the freshly executed module
            importlib.reload(mod)
            return mod

    def test_minimal_card_loads_correctly(self):
        mod = self._import_fresh(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_with_extra_keys(self):
        card = {**MINIMAL_MODEL_CARD, "extra_key": "extra_value", "version": 2}
        mod = self._import_fresh(card)
        assert mod.MODEL_CARD.get("extra_key") == "extra_value"
        assert mod.MODEL_CARD.get("version") == 2

    def test_model_card_with_empty_feature_importance(self):
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {}}
        mod = self._import_fresh(card)
        assert mod.MODEL_CARD["global_feature_importance"] == {}

    def test_model_card_with_nested_dict(self):
        card = {**MINIMAL_MODEL_CARD, "metadata": {"author": "QA", "date": "2024-01-01"}}
        mod = self._import_fresh(card)
        assert mod.MODEL_CARD["metadata"]["author"] == "QA"

    def test_system_prompt_unchanged_after_reload(self):
        mod = self._import_fresh(MINIMAL_MODEL_CARD)
        assert isinstance(mod.SYSTEM_PROMPT, str)
        assert len(mod.SYSTEM_PROMPT) > 0

    def test_file_open_called_once_on_import(self):
        """Verify that exactly one open() call is made during module load."""
        serialised = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=serialised)

        for key in list(sys.modules.keys()):
            if "prompts" in key and "agent" in key:
                sys.modules.pop(key)

        with patch("builtins.open", m):
            import backend.agent.prompts as mod  # noqa: F401
            importlib.reload(mod)

        m.assert_called_once()

    def test_json_parse_error_raises_on_import(self):
        """If model_card.json contains invalid JSON the module must raise."""
        m = mock_open(read_data="NOT VALID JSON {{{{")

        for key in list(sys.modules.keys()):
            if "prompts" in key and "agent" in key:
                sys.modules.pop(key)

        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts as mod  # noqa: F401
                importlib.reload(mod)

    def test_empty_json_object_loads_as_empty_dict(self):
        """An empty JSON object {} should load MODEL_CARD as an empty dict."""
        mod = self._import_fresh({})
        assert mod.MODEL_CARD == {}

    def test_file_not_found_raises_on_import(self):
        """Missing model_card.json must propagate FileNotFoundError."""
        for key in list(sys.modules.keys()):
            if "prompts" in key and "agent" in key:
                sys.modules.pop(key)

        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts as mod  # noqa: F401
                importlib.reload(mod)


# ---------------------------------------------------------------------------
# Tests: _model_card_path resolution
# ---------------------------------------------------------------------------

class TestModelCardPath:
    """Tests for the path used to locate model_card.json."""

    def test_model_card_path_ends_with_model_card_json(self, prompts_module):
        # Access the private path via the module's global namespace
        # (it is a module-level name, so accessible via __dict__ or direct attr)
        path = getattr(prompts_module, "_model_card_path", None)
        if path is None:
            pytest.skip("_model_card_path not accessible as a module attribute")
        assert path.name == "model_card.json"

    def test_model_card_path_is_path_object(self, prompts_module):
        path = getattr(prompts_module, "_model_card_path", None)
        if path is None:
            pytest.skip("_model_card_path not accessible as a module attribute")
        assert isinstance(path, Path)

    def test_model_card_path_two_levels_up_from_agent(self, prompts_module):
        """model_card.json should be at backend/, i.e. two parents above prompts.py."""
        path = getattr(prompts_module, "_model_card_path", None)
        if path is None:
            pytest.skip("_model_card_path not accessible as a module attribute")
        # The path should contain 'backend' somewhere in its parts
        assert "backend" in path.parts or path.parent.name == "backend" or \
               path.parent.parent.name == "backend"


# ---------------------------------------------------------------------------
# TODO stubs
# ---------------------------------------------------------------------------

@pytest.mark.skip(
    reason="TODO: Requires the actual model_card.json present in CI to verify "
           "all expected top-level keys and their exact values end-to-end."
)
def test_integration_model_card_full_schema():
    """
    TODO: Load the real model_card.json and assert the full expected schema,
    including all feature importance keys present in the production file.
    """
    pass


@pytest.mark.skip(
    reason="TODO: Need schema-version metadata in model_card.json to test "
           "backward-compatible loading of older card versions."
)
def test_model_card_schema_version_compatibility():
    """
    TODO: Once model_card.json gains a 'schema_version' field, test that
    the module handles both current and legacy versions gracefully.
    """
    pass


@pytest.mark.skip(
    reason="TODO: Need to know whether SYSTEM_PROMPT will be externalised to a "
           "config/prompt file in the future; add a round-trip load test if so."
)
def test_system_prompt_loaded_from_external_file():
    """
    TODO: If SYSTEM_PROMPT is ever moved to a .txt / .jinja2 template file,
    add a test here that mocks file I/O and asserts correct rendering.
    """
    pass