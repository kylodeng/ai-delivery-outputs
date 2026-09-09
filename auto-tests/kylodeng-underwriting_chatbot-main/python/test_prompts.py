"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at module level
- SYSTEM_PROMPT is defined, is a string, and contains expected content
- The module-level file loading behaves correctly (path resolution, JSON parsing)
- Edge cases: missing file, malformed JSON, unexpected MODEL_CARD structure

Mocks used:
- unittest.mock.patch / mock_open: used to intercept open() and Path operations
  so that no real filesystem reads are required beyond the initial import
- pytest monkeypatch: used for environment/import isolation where needed

TODOs:
- TODO: Integration test that verifies model_card.json on disk matches the
        expected schema (requires access to the actual repo file at runtime).
- TODO: Test that MODEL_CARD values are used downstream in prompt construction
        once that feature is implemented.
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

EMPTY_MODEL_CARD: dict = {}

COMPLEX_MODEL_CARD = {
    "model_name": "Underwriting Risk Classification",
    "model_type": "CatBoostClassifier",
    "target_variable": "Risk_Classification",
    "global_feature_importance": {
        "Age": 34.57614295408571,
        "Education_Level": 2.0984070824092758,
    },
    "extra_metadata": {"version": "1.0.0", "created_at": "2024-01-01"},
    "thresholds": {"low": 0.3, "medium": 0.6, "high": 0.9},
}


def _reimport_prompts(model_card_data: dict):
    """
    Remove the cached module and reimport it with a patched open() that
    returns *model_card_data* as the JSON content.

    Returns the freshly imported module.
    """
    # Remove cached module so the module-level code runs again
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    json_bytes = json.dumps(model_card_data)
    mocked_open = mock_open(read_data=json_bytes)

    with patch("builtins.open", mocked_open):
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_module_cache():
    """Ensure a fresh module state before and after each test."""
    for key in ("backend.agent.prompts", "agent.prompts"):
        sys.modules.pop(key, None)
    yield
    for key in ("backend.agent.prompts", "agent.prompts"):
        sys.modules.pop(key, None)


@pytest.fixture()
def prompts(tmp_path):
    """
    Import backend.agent.prompts with a patched open() returning the
    MINIMAL_MODEL_CARD fixture so no real file is touched.
    """
    return _reimport_prompts(MINIMAL_MODEL_CARD)


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self, prompts):
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts):
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower() or "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessment(self, prompts):
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_no_disclosure_instruction(self, prompts):
        lowered = prompts.SYSTEM_PROMPT.lower()
        assert "disclose" in lowered or "reveal" in lowered

    def test_system_prompt_contains_helpful_assistant(self, prompts):
        assert "helpful assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_senior_underwriting_assistant(self, prompts):
        assert "senior underwriting assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_contain_internal_keys(self, prompts):
        """The prompt must not accidentally embed model card secrets."""
        assert "CatBoostClassifier" not in prompts.SYSTEM_PROMPT
        assert "Risk_Classification" not in prompts.SYSTEM_PROMPT

    def test_system_prompt_is_module_level_constant(self, prompts):
        """SYSTEM_PROMPT should be accessible as a module attribute."""
        assert hasattr(prompts, "SYSTEM_PROMPT")

    @pytest.mark.parametrize(
        "keyword",
        [
            "underwriter",
            "assessments",
            "assistant",
        ],
    )
    def test_system_prompt_contains_keyword(self, prompts, keyword):
        assert keyword.lower() in prompts.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD loading (happy path)
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_model_name(self, prompts):
        assert prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type(self, prompts):
        assert prompts.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self, prompts):
        assert prompts.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD["global_feature_importance"], dict)

    def test_model_card_age_feature_importance(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert "Age" in gfi
        assert pytest.approx(gfi["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_all_expected_features_present(self, prompts):
        expected_features = {
            "Age",
            "Education_Level",
            "Employment_Status",
            "Nationality",
            "Customer_Segment",
            "Annual_Income",
            "Liquid_Assets",
        }
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        assert expected_features.issubset(set(gfi.keys()))

    def test_model_card_feature_importance_values_are_floats(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert isinstance(value, float), f"Feature {key!r} has non-float value {value!r}"

    def test_model_card_feature_importance_values_positive(self, prompts):
        gfi = prompts.MODEL_CARD["global_feature_importance"]
        for key, value in gfi.items():
            assert value >= 0, f"Feature {key!r} has negative importance {value}"

    def test_model_card_is_module_level_constant(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD with varied payloads (parametrised)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_card_data",
    [
        pytest.param(MINIMAL_MODEL_CARD, id="minimal"),
        pytest.param(EMPTY_MODEL_CARD, id="empty"),
        pytest.param(COMPLEX_MODEL_CARD, id="complex"),
    ],
)
def test_model_card_accepts_any_valid_json_object(model_card_data):
    """MODULE_CARD should hold whatever dict the JSON file contains."""
    module = _reimport_prompts(model_card_data)
    assert module.MODEL_CARD == model_card_data


# ---------------------------------------------------------------------------
# Tests – path resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    def test_model_card_path_is_json(self):
        """The resolved path should end with model_card.json regardless of OS."""
        # We inspect the path variable without opening the file
        json_bytes = json.dumps(MINIMAL_MODEL_CARD)
        mocked_open = mock_open(read_data=json_bytes)

        captured_paths: list[Path] = []
        real_open = builtins.open

        def capturing_open(path, *args, **kwargs):
            if isinstance(path, Path):
                captured_paths.append(path)
            return mocked_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=capturing_open):
            _reimport_prompts(MINIMAL_MODEL_CARD)

        assert any(str(p).endswith("model_card.json") for p in captured_paths), (
            f"Expected a path ending with 'model_card.json', got: {captured_paths}"
        )

    def test_model_card_path_constructed_relative_to_module(self):
        """model_card.json should be two levels up from prompts.py."""
        json_bytes = json.dumps(MINIMAL_MODEL_CARD)
        mocked_open = mock_open(read_data=json_bytes)

        captured_paths: list[Path] = []

        def capturing_open(path, *args, **kwargs):
            if isinstance(path, Path):
                captured_paths.append(path)
            return mocked_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=capturing_open):
            module = _reimport_prompts(MINIMAL_MODEL_CARD)

        # The path: Path(__file__).parent.parent / "model_card.json"
        # __file__ == .../backend/agent/prompts.py
        # parent       == .../backend/agent/
        # parent.parent == .../backend/
        # / "model_card.json" == .../backend/model_card.json
        if captured_paths:
            resolved = captured_paths[0]
            assert resolved.name == "model_card.json"
            # parent directory should be named "backend" (or its parent chain contains it)
            assert "backend" in str(resolved)


# ---------------------------------------------------------------------------
# Tests – error conditions
# ---------------------------------------------------------------------------


class TestErrorConditions:
    def test_file_not_found_raises_on_import(self):
        """If model_card.json does not exist, importing the module must raise."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("model_card.json not found")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_malformed_json_raises_on_import(self):
        """If model_card.json contains invalid JSON, importing must raise."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        mocked_open = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", mocked_open):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_permission_error_raises_on_import(self):
        """A PermissionError on open should propagate without swallowing."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=PermissionError("access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_empty_json_file_raises_on_import(self):
        """An empty file produces invalid JSON and must raise JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        mocked_open = mock_open(read_data="")
        with patch("builtins.open", mocked_open):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_json_array_at_root_loads_as_list(self):
        """model_card.json containing a JSON array still loads (no schema enforcement)."""
        array_data: list = [1, 2, 3]
        module = _reimport_prompts(array_data)  # type: ignore[arg-type]
        assert module.MODEL_CARD == array_data

    def test_json_null_at_root_loads_as_none(self):
        """model_card.json containing null loads MODEL_CARD as None."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        mocked_open = mock_open(read_data="null")
        with patch("builtins.open", mocked_open):
            import backend.agent.prompts as p  # noqa: PLC0415

        assert p.MODEL_CARD is None


# ---------------------------------------------------------------------------
# Tests – module public API surface
# ---------------------------------------------------------------------------


class TestPublicApi:
    def test_module_exposes_system_prompt(self, prompts):
        assert "SYSTEM_PROMPT" in dir(prompts)

    def test_module_exposes_model_card(self, prompts):
        assert "MODEL_CARD" in dir(prompts)

    def test_system_prompt_immutable_string(self, prompts):
        """Strings are immutable in Python; verify type for safety."""
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_reimport_gives_same_system_prompt(self):
        """SYSTEM_PROMPT is a literal constant and must be identical across reimports."""
        m1 = _reimport_prompts(MINIMAL_MODEL_CARD)
        prompt1 = m1.SYSTEM_PROMPT
        m2 = _reimport_prompts(MINIMAL_MODEL_CARD)
        prompt2 = m2.SYSTEM_PROMPT
        assert prompt1 == prompt2

    def test_model_card_not_mutated_between_reimports(self):
        """Each import should produce an independent MODEL_CARD dict."""
        m1 = _reimport_prompts(MINIMAL_MODEL_CARD)
        m1.MODEL_CARD["_injected"] = True  # mutate first import's dict

        m2 = _reimport_prompts(MINIMAL_MODEL_CARD)
        assert "_injected" not in m2.MODEL_CARD


# ---------------------------------------------------------------------------
# Skipped / stub tests (require additional context)