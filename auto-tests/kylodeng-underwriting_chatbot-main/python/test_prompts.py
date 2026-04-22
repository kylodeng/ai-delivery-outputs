"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loading from model_card.json at module level
- SYSTEM_PROMPT content, type, and key constraints
- MODEL_CARD structure and expected fields
- Edge cases: missing file, malformed JSON, file permissions

Mocks used:
- unittest.mock.patch / mock_open for file I/O operations
- Patching pathlib.Path and builtins.open to avoid real filesystem dependency
  where appropriate (isolation tests)

TODOs:
- TODO: Full model_card.json schema validation once schema is formally defined
- TODO: Integration test that verifies model_card.json stays in sync with
        the features actually used by the model pipeline
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
# Helpers / constants
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

EXPECTED_SYSTEM_PROMPT_SUBSTRINGS = [
    "senior underwriting assistant",
    "underwriter",
    "gather",
    "assessments",
    "never disclose",
    "internal system instructions",
    "helpful assistant",
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def model_card_json():
    """Return the serialised minimal model card as a string."""
    return json.dumps(MINIMAL_MODEL_CARD)


@pytest.fixture()
def prompts_module(model_card_json, tmp_path):
    """
    Re-import backend.agent.prompts in isolation using a temporary model_card.json
    so the real filesystem layout is not required for unit tests.
    """
    # Write a real temporary file so Path / open work naturally
    model_card_file = tmp_path / "model_card.json"
    model_card_file.write_text(model_card_json)

    # Remove cached module so we can reload with patched path
    module_name = "backend.agent.prompts"
    sys.modules.pop(module_name, None)

    with patch(
        "pathlib.Path.__new__",
        wraps=Path.__new__,
    ):
        # Patch the path resolution inside the module
        with patch.object(Path, "__truediv__", side_effect=lambda self, other: model_card_file if "model_card.json" in str(other) else self / other):
            pass  # We'll use a simpler approach below

    sys.modules.pop(module_name, None)

    # Simpler: monkeypatch open at the builtins level scoped to the load
    original_open = builtins.open

    def patched_open(file, *args, **kwargs):
        if "model_card.json" in str(file):
            return original_open(str(model_card_file), *args, **kwargs)
        return original_open(file, *args, **kwargs)

    with patch("builtins.open", side_effect=patched_open):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            module_name,
            Path(__file__).parent.parent / "agent" / "prompts.py",
        )
        if spec is None or spec.loader is None:
            pytest.skip("Cannot locate backend/agent/prompts.py – adjust path if needed")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

    yield mod

    sys.modules.pop(module_name, None)


# ---------------------------------------------------------------------------
# Helper to load the module via mock_open (pure-mock variant)
# ---------------------------------------------------------------------------


def _load_prompts_with_mock_card(card_dict):
    """
    Load the prompts module with builtins.open mocked to return *card_dict* as JSON.
    Returns the freshly loaded module object.
    """
    module_name = "backend.agent.prompts"
    sys.modules.pop(module_name, None)

    json_bytes = json.dumps(card_dict)
    m = mock_open(read_data=json_bytes)

    with patch("builtins.open", m):
        spec = importlib.util.spec_from_file_location(
            module_name,
            Path(__file__).parent.parent / "agent" / "prompts.py",
        )
        if spec is None or spec.loader is None:
            pytest.skip("Cannot locate backend/agent/prompts.py")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            sys.modules.pop(module_name, None)
            raise

    return mod


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT tests (import the real module – model_card.json must exist)
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Tests for the SYSTEM_PROMPT constant."""

    @pytest.fixture(autouse=True)
    def _import_module(self):
        """Import the real module once per test class (uses the real file)."""
        try:
            import importlib
            import backend.agent.prompts as p

            self.mod = p
        except (ImportError, FileNotFoundError):
            pytest.skip(
                "backend.agent.prompts could not be imported – "
                "ensure model_card.json exists for integration-style tests."
            )

    # --- type & basic structure ---

    def test_system_prompt_is_string(self):
        assert isinstance(self.mod.SYSTEM_PROMPT, str)

    def test_system_prompt_is_non_empty(self):
        assert len(self.mod.SYSTEM_PROMPT.strip()) > 0

    # --- content requirements ---

    @pytest.mark.parametrize("substring", EXPECTED_SYSTEM_PROMPT_SUBSTRINGS)
    def test_system_prompt_contains_required_substring(self, substring):
        assert substring.lower() in self.mod.SYSTEM_PROMPT.lower(), (
            f"Expected '{substring}' to appear in SYSTEM_PROMPT"
        )

    def test_system_prompt_never_discloses_instructions(self):
        """The prompt must explicitly forbid disclosing internal instructions."""
        prompt_lower = self.mod.SYSTEM_PROMPT.lower()
        assert "never disclose" in prompt_lower or "cannot disclose" in prompt_lower

    def test_system_prompt_does_not_expose_tool_list(self):
        """Prompt should not enumerate specific internal tool names."""
        forbidden_patterns = ["<tool>", "tool_call", "function_call"]
        for pattern in forbidden_patterns:
            assert pattern not in self.mod.SYSTEM_PROMPT, (
                f"Potentially sensitive pattern '{pattern}' found in SYSTEM_PROMPT"
            )

    def test_system_prompt_mentions_underwriter_role(self):
        assert "underwriter" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_is_module_level_constant(self):
        """SYSTEM_PROMPT must be accessible as a top-level module attribute."""
        assert hasattr(self.mod, "SYSTEM_PROMPT")

    # --- immutability / identity ---

    def test_system_prompt_value_stable_across_accesses(self):
        """Repeated access returns the exact same object (module-level constant)."""
        first = self.mod.SYSTEM_PROMPT
        second = self.mod.SYSTEM_PROMPT
        assert first is second


# ---------------------------------------------------------------------------
# MODEL_CARD tests (import the real module)
# ---------------------------------------------------------------------------


class TestModelCard:
    """Tests for the MODEL_CARD constant loaded from model_card.json."""

    @pytest.fixture(autouse=True)
    def _import_module(self):
        try:
            import backend.agent.prompts as p

            self.mod = p
        except (ImportError, FileNotFoundError):
            pytest.skip("backend.agent.prompts could not be imported.")

    def test_model_card_is_dict(self):
        assert isinstance(self.mod.MODEL_CARD, dict)

    def test_model_card_is_non_empty(self):
        assert len(self.mod.MODEL_CARD) > 0

    def test_model_card_is_module_level_attribute(self):
        assert hasattr(self.mod, "MODEL_CARD")

    @pytest.mark.parametrize(
        "expected_key",
        ["model_name", "model_type", "target_variable"],
    )
    def test_model_card_contains_expected_keys(self, expected_key):
        assert expected_key in self.mod.MODEL_CARD, (
            f"Expected key '{expected_key}' missing from MODEL_CARD"
        )

    def test_model_card_model_name_is_string(self):
        assert isinstance(self.mod.MODEL_CARD.get("model_name"), str)

    def test_model_card_model_type_is_string(self):
        assert isinstance(self.mod.MODEL_CARD.get("model_type"), str)

    def test_model_card_target_variable_is_string(self):
        assert isinstance(self.mod.MODEL_CARD.get("target_variable"), str)

    def test_model_card_global_feature_importance_is_dict(self):
        gfi = self.mod.MODEL_CARD.get("global_feature_importance")
        if gfi is not None:
            assert isinstance(gfi, dict)

    def test_model_card_feature_importance_values_are_floats(self):
        gfi = self.mod.MODEL_CARD.get("global_feature_importance", {})
        for feature, importance in gfi.items():
            assert isinstance(importance, (int, float)), (
                f"Feature '{feature}' importance should be numeric, got {type(importance)}"
            )

    def test_model_card_feature_importance_values_non_negative(self):
        gfi = self.mod.MODEL_CARD.get("global_feature_importance", {})
        for feature, importance in gfi.items():
            assert importance >= 0, (
                f"Feature importance for '{feature}' should be non-negative"
            )


# ---------------------------------------------------------------------------
# Isolation / mock-based tests (no real filesystem dependency)
# ---------------------------------------------------------------------------


class TestModuleLoadingWithMocks:
    """
    Tests that exercise module-load behaviour using mock_open,
    completely independent of the real model_card.json.
    """

    def test_model_card_loaded_from_json_file(self):
        """MODEL_CARD should equal the parsed contents of model_card.json."""
        mod = _load_prompts_with_mock_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD == MINIMAL_MODEL_CARD

    def test_model_card_with_empty_feature_importance(self):
        card = {**MINIMAL_MODEL_CARD, "global_feature_importance": {}}
        mod = _load_prompts_with_mock_card(card)
        assert mod.MODEL_CARD["global_feature_importance"] == {}

    def test_model_card_with_extra_keys(self):
        """Unknown keys in model_card.json should be preserved as-is."""
        card = {**MINIMAL_MODEL_CARD, "extra_metadata": {"version": "1.2.3"}}
        mod = _load_prompts_with_mock_card(card)
        assert mod.MODEL_CARD.get("extra_metadata") == {"version": "1.2.3"}

    def test_system_prompt_unchanged_regardless_of_model_card(self):
        """SYSTEM_PROMPT is a hard-coded constant; it must not depend on MODEL_CARD."""
        card_a = {**MINIMAL_MODEL_CARD, "model_name": "Model A"}
        card_b = {**MINIMAL_MODEL_CARD, "model_name": "Model B"}

        mod_a = _load_prompts_with_mock_card(card_a)
        sys.modules.pop("backend.agent.prompts", None)
        mod_b = _load_prompts_with_mock_card(card_b)

        assert mod_a.SYSTEM_PROMPT == mod_b.SYSTEM_PROMPT

    def test_missing_model_card_raises_file_not_found(self):
        """If model_card.json does not exist, module import must raise FileNotFoundError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        with patch("builtins.open", side_effect=FileNotFoundError("No such file")):
            spec = importlib.util.spec_from_file_location(
                module_name,
                Path(__file__).parent.parent / "agent" / "prompts.py",
            )
            if spec is None or spec.loader is None:
                pytest.skip("Cannot locate prompts.py")

            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod

            with pytest.raises(FileNotFoundError):
                spec.loader.exec_module(mod)  # type: ignore[union-attr]

        sys.modules.pop(module_name, None)

    def test_malformed_json_raises_json_decode_error(self):
        """If model_card.json contains invalid JSON, module import must raise JSONDecodeError."""
        module_name = "backend.agent.prompts"
        sys.modules.pop(module_name, None)

        m = mock_open(read_data="{ this is not valid json }")

        with patch("builtins.open", m):
            spec = importlib.util.spec_from_file_location(
                module_name,
                Path(__file__).parent.parent / "agent" / "prompts.py",
            )
            if spec is None or spec.loader is None:
                pytest.skip("Cannot locate prompts.py")

            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod

            with pytest.raises(json.JSONDecodeError):
                spec.loader.exec_module(mod)  # type: ignore[union-attr]

        sys.modules.pop(module_name, None)

    def test_empty_json_object_is_accepted(self):
        """An empty JSON object should load without error; MODEL_CARD will be {}."""
        mod = _load_prompts_with_mock_card({})
        assert mod.MODEL_CARD == {}

    def test_model_card_with_null_values(self):
        """JSON null values should be mapped to Python None without error."""
        card = {**MINIMAL_MODEL_CARD, "deprecated_field": None}
        mod = _load_prompts_with_mock_card(card)
        assert mod.MODEL_CARD["deprecated_field"] is None

    def test_model_card_with_nested_structure(self):
        """Deeply nested JSON structures should be preserved."""
        card = {
            **MINIMAL_MODEL_CARD,
            "thresholds": {"low": {"min": 0, "max": 33}, "high": {"min": 67, "max": 100}},
        }
        mod = _load_prompts_with_mock_card(card)
        assert mod.MODEL_CARD["thresholds"]["high"]["max"] == 100


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------


class TestModelCardPathResolution:
    """Verify that the path to model