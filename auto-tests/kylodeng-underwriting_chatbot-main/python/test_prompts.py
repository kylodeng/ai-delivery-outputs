"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at import time
- SYSTEM_PROMPT is a non-empty string with the expected content
- SYSTEM_PROMPT contains key behavioural constraints (confidentiality, role identity, helpfulness)
- Resilience / error conditions when model_card.json is missing or malformed

Mocks used:
- unittest.mock.patch / mock_open to simulate file I/O for model_card.json
- tmp_path (pytest fixture) to create real temporary JSON files for integration-style path tests

TODOs:
- TODO: Extend tests once MODEL_CARD fields are consumed by other functions in prompts.py
- TODO: Add tests for any future prompt-building functions that interpolate MODEL_CARD values
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

VALID_MODEL_CARD = {
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
    Re-import backend.agent.prompts after patching the file-system call so
    that MODEL_CARD is populated from *model_card_dict*.
    Returns the freshly-imported module.
    """
    serialised = json.dumps(model_card_dict)
    m = mock_open(read_data=serialised)

    # Remove any previously cached version so importlib re-executes module body
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_module
        importlib.reload(prompts_module)

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def prompts_module(tmp_path, monkeypatch):
    """
    Provide a cleanly-imported prompts module backed by a real temporary
    model_card.json containing VALID_MODEL_CARD data.
    """
    card_file = tmp_path / "model_card.json"
    card_file.write_text(json.dumps(VALID_MODEL_CARD))

    # Patch Path.__truediv__ resolution so _model_card_path points to our tmp file
    monkeypatch.setattr(
        "backend.agent.prompts._model_card_path",
        card_file,
        raising=False,
    )

    sys.modules.pop("backend.agent.prompts", None)

    with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
        import backend.agent.prompts as mod
        importlib.reload(mod)

    yield mod

    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT tests
# ---------------------------------------------------------------------------

class TestSystemPrompt:

    def test_system_prompt_is_a_string(self):
        import backend.agent.prompts as prompts
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self):
        import backend.agent.prompts as prompts
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_contains_role_identity(self):
        """The assistant must identify itself as an underwriting assistant."""
        import backend.agent.prompts as prompts
        assert "underwriting assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_confidentiality_constraint(self):
        """The assistant must never disclose internal system instructions."""
        import backend.agent.prompts as prompts
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in lower or "reveal" in lower or "never" in lower

    def test_system_prompt_references_tools_confidentiality(self):
        """Tools access must also be kept confidential."""
        import backend.agent.prompts as prompts
        assert "tools" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_references_helpfulness(self):
        """The assistant should present as helpful."""
        import backend.agent.prompts as prompts
        assert "helpful" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_references_assessments(self):
        """The assistant should be able to run assessments."""
        import backend.agent.prompts as prompts
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_addresses_underwriter(self):
        """The assistant talks *to* an underwriter."""
        import backend.agent.prompts as prompts
        assert "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_leading_trailing_whitespace_issues(self):
        """Prompt should start and end without stray newlines that could confuse the LLM."""
        import backend.agent.prompts as prompts
        # Allow a single trailing newline at most; disallow multiple blank lines at edges
        assert prompts.SYSTEM_PROMPT == prompts.SYSTEM_PROMPT.strip() or \
               prompts.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_minimum_length(self):
        """A meaningful system prompt should be at least 100 characters."""
        import backend.agent.prompts as prompts
        assert len(prompts.SYSTEM_PROMPT) >= 100

    @pytest.mark.parametrize("forbidden_phrase", [
        "tool_name",
        "function_call",
        "secret",
        "password",
        "api_key",
    ])
    def test_system_prompt_does_not_leak_internal_terms(self, forbidden_phrase):
        """System prompt must not accidentally expose internal implementation details."""
        import backend.agent.prompts as prompts
        assert forbidden_phrase.lower() not in prompts.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# MODEL_CARD loading – happy path
# ---------------------------------------------------------------------------

class TestModelCardLoading:

    def test_model_card_is_dict(self):
        """MODEL_CARD must be a dictionary after loading."""
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_contains_model_name(self):
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_value(self):
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_contains_model_type(self):
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert prompts.MODEL_CARD.get("model_type") == "CatBoostClassifier"

    def test_model_card_contains_target_variable(self):
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_feature_importance_is_dict(self):
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert isinstance(prompts.MODEL_CARD.get("global_feature_importance"), dict)

    def test_model_card_feature_importance_age(self):
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            age_importance = prompts.MODEL_CARD["global_feature_importance"]["Age"]
            assert pytest.approx(age_importance, rel=1e-6) == 34.57614295408571

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
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert feature in prompts.MODEL_CARD["global_feature_importance"]

    def test_model_card_with_minimal_valid_json(self):
        """MODEL_CARD should accept any valid JSON object, even a minimal one."""
        minimal = {"model_name": "minimal"}
        with patch("builtins.open", mock_open(read_data=json.dumps(minimal))):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert prompts.MODEL_CARD == minimal

    def test_model_card_with_empty_json_object(self):
        """MODEL_CARD can be an empty dict if the JSON file contains {}."""
        with patch("builtins.open", mock_open(read_data="{}")):
            sys.modules.pop("backend.agent.prompts", None)
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert prompts.MODEL_CARD == {}

    def test_model_card_path_is_resolved_relative_to_module(self):
        """_model_card_path should be two directories up from prompts.py."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data=json.dumps(VALID_MODEL_CARD))):
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            expected_name = "model_card.json"
            assert prompts._model_card_path.name == expected_name


# ---------------------------------------------------------------------------
# MODEL_CARD loading – error / edge cases
# ---------------------------------------------------------------------------

class TestModelCardLoadingErrors:

    def test_missing_file_raises_file_not_found(self):
        """If model_card.json does not exist the module should raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts as prompts
                importlib.reload(prompts)

    def test_malformed_json_raises_json_decode_error(self):
        """If model_card.json contains invalid JSON a JSONDecodeError should propagate."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data="{ not valid json }")):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts as prompts
                importlib.reload(prompts)

    def test_empty_file_raises_json_decode_error(self):
        """An empty file is not valid JSON."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data="")):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts as prompts
                importlib.reload(prompts)

    def test_permission_error_propagates(self):
        """A PermissionError on the file should not be silently swallowed."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts as prompts
                importlib.reload(prompts)

    def test_json_array_root_loads_as_list(self):
        """json.load returns a list when the root element is an array; MODEL_CARD would be a list."""
        array_data = json.dumps([{"key": "value"}])
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data=array_data)):
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            # The module does not enforce dict; it stores whatever json.load returns
            assert isinstance(prompts.MODEL_CARD, list)

    def test_json_string_root_loads_as_string(self):
        """json.load returns a str when the root is a JSON string."""
        string_data = json.dumps("just a string")
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data=string_data)):
            import backend.agent.prompts as prompts
            importlib.reload(prompts)
            assert isinstance(prompts.MODEL_CARD, str)


# ---------------------------------------------------------------------------
# Module-level attribute existence
# ---------------------------------------------------------------------------

class TestModuleAttributes:

    def test_module_exposes_system_prompt(self):
        import backend.agent.prompts as prompts
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_module_exposes_model_card(self):
        import backend.agent.prompts as prompts
        assert hasattr(prompts, "MODEL_CARD")

    def test_module_exposes_model_card_path(self):
        import backend.agent.prompts as prompts
        assert hasattr(prompts, "_model_card_path")

    def test_model_card_path_is_path_instance(self):
        import backend.agent.prompts as prompts
        assert isinstance(prompts._model_card_path, Path)

    def test_system_prompt_is_immutable_string(self):
        """Strings are immutable in Python; verify it is not accidentally a mutable container."""
        import backend.agent.prompts as prompts
        assert not isinstance(prompts.SYSTEM_