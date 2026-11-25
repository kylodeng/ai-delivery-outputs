"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json
- SYSTEM_PROMPT content and structure
- Module-level constants are accessible and have correct types
- Edge cases: file not found, malformed JSON

Mocks used:
- unittest.mock.patch / mock_open to intercept file I/O for MODEL_CARD loading
- tmp_path (pytest fixture) for real temporary JSON files

TODOs:
- TODO: Extend MODEL_CARD content tests once full model_card.json schema is confirmed
- TODO: Test behaviour when model_card.json has unexpected/missing keys if downstream code depends on specific keys
"""

import builtins
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


def _reload_prompts_with_card(model_card_dict: dict):
    """
    Reload the prompts module with a patched model_card.json content.
    Returns the freshly imported module.
    """
    module_name = "backend.agent.prompts"
    # Remove cached module so importlib re-executes module-level code
    sys.modules.pop(module_name, None)

    json_bytes = json.dumps(model_card_dict)
    m = mock_open(read_data=json_bytes)

    with patch("builtins.open", m):
        with patch("pathlib.Path.open", m):
            # We also patch the top-level open used in the module
            module = importlib.import_module(module_name)

    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_module():
    """Ensure the original module is restored after each test."""
    original = sys.modules.get("backend.agent.prompts")
    yield
    # Restore whatever was there before (may be None)
    if original is not None:
        sys.modules["backend.agent.prompts"] = original
    else:
        sys.modules.pop("backend.agent.prompts", None)


@pytest.fixture()
def prompts_module():
    """
    Import the real prompts module (requires model_card.json to exist on disk).
    If the file is missing the test is skipped.
    """
    model_card_path = Path(__file__).parent.parent / "model_card.json"
    if not model_card_path.exists():
        pytest.skip("model_card.json not present on disk – skipping integration fixture")

    sys.modules.pop("backend.agent.prompts", None)
    import backend.agent.prompts as mod  # noqa: PLC0415

    return mod


@pytest.fixture()
def patched_prompts():
    """Return prompts module loaded with the MINIMAL_MODEL_CARD stub."""
    module_name = "backend.agent.prompts"
    sys.modules.pop(module_name, None)

    json_data = json.dumps(MINIMAL_MODEL_CARD)
    m = mock_open(read_data=json_data)

    with patch("builtins.open", m):
        import backend.agent.prompts as mod  # noqa: PLC0415

    return mod


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Tests around the module-level MODEL_CARD constant."""

    def test_model_card_is_dict(self, patched_prompts):
        assert isinstance(patched_prompts.MODEL_CARD, dict)

    def test_model_card_contains_model_name(self, patched_prompts):
        assert patched_prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_contains_model_type(self, patched_prompts):
        assert patched_prompts.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_contains_target_variable(self, patched_prompts):
        assert patched_prompts.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self, patched_prompts):
        gfi = patched_prompts.MODEL_CARD["global_feature_importance"]
        assert isinstance(gfi, dict)

    def test_model_card_feature_importance_values_are_floats(self, patched_prompts):
        gfi = patched_prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert isinstance(value, float), f"Feature '{key}' importance is not a float"

    def test_model_card_age_importance_approximate(self, patched_prompts):
        age_importance = patched_prompts.MODEL_CARD["global_feature_importance"]["Age"]
        assert abs(age_importance - 34.576) < 0.01

    def test_model_card_not_empty(self, patched_prompts):
        assert len(patched_prompts.MODEL_CARD) > 0

    def test_model_card_loaded_once_per_import(self):
        """open() should be called exactly once during module import."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        json_data = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=json_data)

        with patch("builtins.open", m):
            import backend.agent.prompts  # noqa: PLC0415, F401

        assert m.call_count == 1


class TestModelCardLoadingErrors:
    """Tests that verify error handling when model_card.json is problematic."""

    def test_file_not_found_raises_on_import(self):
        """Module import must raise FileNotFoundError when model_card.json is absent."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=FileNotFoundError("No such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_malformed_json_raises_on_import(self):
        """Module import must raise json.JSONDecodeError when JSON is invalid."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_empty_json_object_loads_as_empty_dict(self):
        """An empty JSON object '{}' should load without error."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="{}")
        with patch("builtins.open", m):
            import backend.agent.prompts as mod  # noqa: PLC0415

        assert mod.MODEL_CARD == {}

    def test_json_array_at_root_loads(self):
        """model_card.json containing a JSON array should still load (no schema enforcement)."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="[1, 2, 3]")
        with patch("builtins.open", m):
            import backend.agent.prompts as mod  # noqa: PLC0415

        assert mod.MODEL_CARD == [1, 2, 3]

    def test_permission_error_raises_on_import(self):
        """Module import must propagate PermissionError when file is unreadable."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=PermissionError("Access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: PLC0415, F401


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT constant
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests around the module-level SYSTEM_PROMPT constant."""

    def test_system_prompt_is_string(self, patched_prompts):
        assert isinstance(patched_prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, patched_prompts):
        assert len(patched_prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, patched_prompts):
        assert "underwriting" in patched_prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, patched_prompts):
        assert "underwriter" in patched_prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, patched_prompts):
        assert "assessment" in patched_prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_confidentiality_instruction(self, patched_prompts):
        """Must instruct the model never to reveal internal instructions."""
        prompt_lower = patched_prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in prompt_lower or "reveal" in prompt_lower or "never" in prompt_lower

    def test_system_prompt_mentions_helpful_assistant(self, patched_prompts):
        assert "helpful assistant" in patched_prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_leading_trailing_newlines(self, patched_prompts):
        """The prompt should not start or end with stray newline characters."""
        assert not patched_prompts.SYSTEM_PROMPT.startswith("\n")
        assert not patched_prompts.SYSTEM_PROMPT.endswith("\n")

    def test_system_prompt_minimum_length(self, patched_prompts):
        """Prompt should be reasonably long (more than 50 chars) to be useful."""
        assert len(patched_prompts.SYSTEM_PROMPT) > 50

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "internal system instructions",
            "tools you have access to",
        ],
    )
    def test_system_prompt_instructs_to_hide_forbidden_info(self, patched_prompts, forbidden_phrase):
        """
        The prompt references these phrases in the context of hiding them —
        verify they appear so the instruction is present.
        """
        assert forbidden_phrase in patched_prompts.SYSTEM_PROMPT

    def test_system_prompt_is_immutable_across_imports(self, patched_prompts):
        """SYSTEM_PROMPT should be the same string object (module-level constant)."""
        first_ref = patched_prompts.SYSTEM_PROMPT
        second_ref = patched_prompts.SYSTEM_PROMPT
        assert first_ref is second_ref


# ---------------------------------------------------------------------------
# Tests – Module public API
# ---------------------------------------------------------------------------


class TestModulePublicAPI:
    """Verify the public names exported by the module."""

    def test_model_card_attribute_exists(self, patched_prompts):
        assert hasattr(patched_prompts, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self, patched_prompts):
        assert hasattr(patched_prompts, "SYSTEM_PROMPT")

    def test_no_unintended_public_callables(self, patched_prompts):
        """
        The module is expected to expose only constants, not functions.
        Any callable in the public namespace (excluding imported built-ins) is unexpected.
        """
        public_callables = [
            name
            for name in dir(patched_prompts)
            if not name.startswith("_") and callable(getattr(patched_prompts, name))
        ]
        # json and Path may be importable as module attributes; that's fine.
        # We just want to ensure no surprise user-defined functions are present.
        unexpected = [
            c for c in public_callables if c not in {"json", "Path", "MODEL_CARD", "SYSTEM_PROMPT"}
        ]
        assert unexpected == [], f"Unexpected callables in public API: {unexpected}"


# ---------------------------------------------------------------------------
# Integration test (requires real model_card.json on disk)
# ---------------------------------------------------------------------------


class TestIntegration:
    """
    Integration tests that read the real model_card.json from disk.
    Skipped automatically if the file is absent.
    """

    def test_real_model_card_loads_as_dict(self, prompts_module):
        assert isinstance(prompts_module.MODEL_CARD, dict)

    def test_real_model_card_has_model_name(self, prompts_module):
        assert "model_name" in prompts_module.MODEL_CARD

    def test_real_system_prompt_is_string(self, prompts_module):
        assert isinstance(prompts_module.SYSTEM_PROMPT, str)

    def test_real_model_card_global_feature_importance_present(self, prompts_module):
        assert "global_feature_importance" in prompts_module.MODEL_CARD

    @pytest.mark.skip(reason="TODO: Confirm full model_card.json schema before asserting all required keys")
    def test_real_model_card_all_required_keys(self, prompts_module):
        # TODO: enumerate every required key once the schema is finalised
        required_keys = [
            "model_name",
            "model_type",
            "target_variable",
            "global_feature_importance",
            # add more as schema is confirmed
        ]
        for key in required_keys:
            assert key in prompts_module.MODEL_CARD, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# Parametrised edge-case tests for MODEL_CARD content
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "card_input,expected",
    [
        ({"model_name": "Test"}, {"model_name": "Test"}),
        ({"key": 123, "nested": {"a": 1.0}}, {"key": 123, "nested": {"a": 1.0}}),
        ({}, {}),
        ({"unicode": "日本語テスト"}, {"unicode": "日本語テスト"}),
    ],
)
def test_model_card_various_valid_json_inputs(card_input, expected):
    """MODEL_CARD should faithfully reflect whatever valid JSON is in the file."""
    module_name = "backend.agent.prompts"
    sys.modules.pop(module_name, None)

    m = mock_open(read_data=json.dumps(card_input))
    with patch("builtins.open", m):
        import backend.agent.prompts as mod  # noqa: PLC0415

    assert mod.MODEL_CARD == expected
    # cleanup
    sys.modules.pop(module_name, None)