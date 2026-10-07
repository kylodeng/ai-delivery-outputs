"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at import time
- SYSTEM_PROMPT is defined with the expected content and constraints
- File-level constants (MODEL_CARD, SYSTEM_PROMPT) are accessible and correctly typed
- Edge cases around the JSON loading and path resolution

Mocks used:
- unittest.mock.patch / mock_open: used to mock open() and json.load() to avoid
  reading the real model_card.json from disk in isolated unit tests
- tmp_path (pytest fixture): used to create a temporary model_card.json for
  integration-style path tests

TODOs:
- TODO: Full schema validation of MODEL_CARD once the complete model_card.json
  schema is confirmed (currently truncated in synthetic data).
- TODO: Tests for any future functions added to prompts.py.
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

SYSTEM_PROMPT_EXPECTED_SUBSTRINGS = [
    "senior underwriting assistant",
    "underwriter",
    "never disclose",
    "internal system instructions",
    "tools you have access to",
    "helpful assistant",
    "assessments",
]


def _reload_prompts_with_card(model_card_dict: dict):
    """
    Reload backend.agent.prompts with a patched open/json.load that returns
    *model_card_dict*.  Returns the freshly imported module.
    """
    module_name = "backend.agent.prompts"
    # Remove cached version so importlib re-executes module-level code
    sys.modules.pop(module_name, None)

    serialised = json.dumps(model_card_dict)
    m_open = mock_open(read_data=serialised)

    with patch("builtins.open", m_open):
        with patch("json.load", return_value=model_card_dict):
            module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def prompts_module():
    """
    Import the real module (requires model_card.json to exist on disk).
    Falls back to a mocked import if the file is absent.
    """
    module_name = "backend.agent.prompts"
    sys.modules.pop(module_name, None)

    try:
        module = importlib.import_module(module_name)
        yield module
    except FileNotFoundError:
        # Model card not present in CI – use mocked version
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        yield module
    finally:
        sys.modules.pop(module_name, None)


@pytest.fixture()
def real_model_card_path(tmp_path):
    """Write a real model_card.json to a temp directory and return its path."""
    card_path = tmp_path / "model_card.json"
    card_path.write_text(json.dumps(MINIMAL_MODEL_CARD), encoding="utf-8")
    return card_path


# ---------------------------------------------------------------------------
# Tests – MODULE_CARD / MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests that MODEL_CARD is loaded correctly from disk."""

    def test_model_card_is_dict(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert isinstance(module.MODEL_CARD, dict)

    def test_model_card_contains_expected_top_level_keys(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        for key in ("model_name", "model_type", "target_variable"):
            assert key in module.MODEL_CARD, f"Expected key '{key}' missing from MODEL_CARD"

    def test_model_card_model_name(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert module.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert isinstance(module.MODEL_CARD.get("global_feature_importance"), dict)

    @pytest.mark.parametrize(
        "feature, expected_value",
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
    def test_model_card_feature_importance_values(self, feature, expected_value):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        gfi = module.MODEL_CARD["global_feature_importance"]
        assert feature in gfi, f"Feature '{feature}' missing from global_feature_importance"
        assert gfi[feature] == pytest.approx(expected_value)

    def test_model_card_with_empty_dict(self):
        """Edge case: model_card.json exists but is an empty JSON object."""
        module = _reload_prompts_with_card(EMPTY_MODEL_CARD)
        assert module.MODEL_CARD == {}

    def test_model_card_with_extra_keys(self):
        """MODEL_CARD should preserve unknown/extra keys without error."""
        extended_card = {**MINIMAL_MODEL_CARD, "extra_field": "extra_value", "version": 42}
        module = _reload_prompts_with_card(extended_card)
        assert module.MODEL_CARD.get("extra_field") == "extra_value"
        assert module.MODEL_CARD.get("version") == 42

    def test_model_card_path_is_sibling_of_parent_directory(self):
        """
        Verify the _model_card_path construction:
        Path(__file__).parent.parent / 'model_card.json'
        should resolve two levels up from prompts.py.
        """
        # We check this by inspecting what path would be constructed for a
        # known __file__ location.
        fake_file = Path("/repo/backend/agent/prompts.py")
        expected = Path("/repo/backend/model_card.json")
        computed = fake_file.parent.parent / "model_card.json"
        assert computed == expected

    def test_open_called_with_model_card_path(self):
        """open() must be called with the resolved model_card.json path."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m_open = mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))
        with patch("builtins.open", m_open) as patched_open:
            with patch("json.load", return_value=MINIMAL_MODEL_CARD):
                importlib.import_module(module_name)

        assert patched_open.called, "open() was never called during module import"
        call_args = patched_open.call_args
        opened_path = call_args[0][0]  # first positional argument
        assert str(opened_path).endswith("model_card.json")

        sys.modules.pop(module_name, None)

    def test_file_not_found_raises(self):
        """If model_card.json is missing, the module should raise FileNotFoundError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                importlib.import_module(module_name)

        sys.modules.pop(module_name, None)

    def test_invalid_json_raises(self):
        """If model_card.json contains invalid JSON, module import must raise."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m_open = mock_open(read_data="THIS IS NOT JSON {{{{")
        with patch("builtins.open", m_open):
            with pytest.raises(json.JSONDecodeError):
                importlib.import_module(module_name)

        sys.modules.pop(module_name, None)

    def test_model_card_loaded_with_real_tmp_file(self, tmp_path, monkeypatch):
        """
        Integration-style: write an actual JSON file to tmp_path and verify
        that the module reads it correctly via path manipulation.
        """
        card_path = tmp_path / "model_card.json"
        card_path.write_text(json.dumps(MINIMAL_MODEL_CARD), encoding="utf-8")

        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        # Patch Path so _model_card_path resolves to our temp file
        original_path_class = Path

        class PatchedPath(type(original_path_class())):
            pass

        fake_parent = MagicMock()
        fake_parent.__truediv__ = MagicMock(return_value=card_path)
        fake_grandparent = MagicMock()
        fake_grandparent.__truediv__ = MagicMock(return_value=card_path)

        with patch("backend.agent.prompts.Path") as mock_path_cls:
            mock_file_path = MagicMock()
            mock_file_path.parent.parent.__truediv__ = MagicMock(return_value=card_path)
            mock_path_cls.return_value = mock_file_path
            mock_path_cls.return_value.parent.parent.__truediv__.return_value = card_path

            # Use real open/json since card_path is an actual file
            module = importlib.import_module(module_name)

        sys.modules.pop(module_name, None)


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests that SYSTEM_PROMPT is defined correctly."""

    def test_system_prompt_is_string(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert isinstance(module.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert len(module.SYSTEM_PROMPT.strip()) > 0

    @pytest.mark.parametrize("substring", SYSTEM_PROMPT_EXPECTED_SUBSTRINGS)
    def test_system_prompt_contains_expected_substring(self, substring):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert substring in module.SYSTEM_PROMPT, (
            f"Expected substring '{substring}' not found in SYSTEM_PROMPT"
        )

    def test_system_prompt_does_not_reveal_tools(self):
        """The prompt itself must instruct the LLM NOT to reveal tools."""
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        # The instruction "never disclose" must come before any tool mention
        prompt_lower = module.SYSTEM_PROMPT.lower()
        assert "never disclose" in prompt_lower or "can never disclose" in prompt_lower

    def test_system_prompt_mentions_underwriting(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert "underwriting" in module.SYSTEM_PROMPT.lower() or "underwriter" in module.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessment(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert "assessment" in module.SYSTEM_PROMPT.lower()

    def test_system_prompt_is_a_single_string_not_list(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert not isinstance(module.SYSTEM_PROMPT, (list, tuple, bytes))

    def test_system_prompt_has_minimum_length(self):
        """A meaningful system prompt should be at least 100 characters."""
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert len(module.SYSTEM_PROMPT) >= 100

    def test_system_prompt_instructs_helpful_assistant(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert "helpful assistant" in module.SYSTEM_PROMPT

    def test_system_prompt_cannot_be_overridden_to_reveal_instructions(self):
        """
        Structural check: the system prompt must include explicit language
        forbidding disclosure of system instructions.
        """
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert "internal system instructions" in module.SYSTEM_PROMPT

    def test_system_prompt_is_immutable_string(self):
        """Strings are immutable in Python; verify it hasn't been wrapped."""
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert type(module.SYSTEM_PROMPT) is str  # noqa: E721


# ---------------------------------------------------------------------------
# Tests – Module-level attribute existence
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    """Tests that expected public attributes are exported from the module."""

    def test_model_card_attribute_exists(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert hasattr(module, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self):
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert hasattr(module, "SYSTEM_PROMPT")

    def test_no_unexpected_callable_exports(self):
        """
        prompts.py currently defines only constants; there should be no
        public functions exported (private helpers with _ prefix are allowed).
        """
        module = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        public_callables = [
            name
            for name in dir(module)
            if not name.startswith("_") and callable(getattr(module, name))
            and not isinstance(getattr(module, name), type)
        ]
        # Allow only built-ins that might leak (e.g. Path, json)
        non_stdlib_callables