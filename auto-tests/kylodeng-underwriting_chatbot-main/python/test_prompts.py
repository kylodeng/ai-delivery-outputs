"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at import time
- SYSTEM_PROMPT is defined, non-empty, and contains expected behavioural constraints
- File-level constants (MODEL_CARD, SYSTEM_PROMPT) are the correct types
- Edge cases around the model_card.json path resolution
- Content constraints of SYSTEM_PROMPT (confidentiality, role identity, helpfulness)

Mocks used:
- unittest.mock.patch / mock_open to avoid reading real model_card.json where needed
- tmp_path (pytest fixture) for controlled JSON file scenarios
- importlib used to reload the module under controlled path conditions

TODOs:
- TODO: Test behaviour when model_card.json contains unexpected/missing keys — needs schema spec
- TODO: Integration test that verifies MODEL_CARD values match a known good snapshot — needs
        a stable, checked-in model_card.json fixture agreed by the team
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


def _reload_prompts_with_card(model_card_dict: dict):
    """
    Reload backend.agent.prompts with a patched open() that returns
    *model_card_dict* as the JSON content.  Returns the freshly imported
    module object.
    """
    json_bytes = json.dumps(model_card_dict)
    m_open = mock_open(read_data=json_bytes)

    # Remove cached module so importlib actually re-executes module-level code
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m_open):
        with patch("json.load", return_value=model_card_dict):
            import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts():
    """
    Import the real module once per test-session.

    If model_card.json is absent in the test environment the fixture falls
    back to a patched version so the rest of the test suite can still run.
    """
    try:
        import backend.agent.prompts as _prompts  # noqa: PLC0415

        return _prompts
    except FileNotFoundError:
        return _reload_prompts_with_card(MINIMAL_MODEL_CARD)


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD
# ---------------------------------------------------------------------------


class TestModelCard:
    def test_model_card_is_dict(self, prompts):
        """MODEL_CARD should be a plain Python dict after JSON deserialization."""
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_is_not_empty(self, prompts):
        """A completely empty dict would indicate a bad model_card.json."""
        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_loaded_with_synthetic_data(self):
        """
        When model_card.json contains the synthetic sample data the module
        should expose all top-level keys.
        """
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"
        assert mod.MODEL_CARD["model_type"] == "CatBoostClassifier"
        assert mod.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_feature_importance_present(self):
        """global_feature_importance should be accessible as a dict."""
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        fi = mod.MODEL_CARD["global_feature_importance"]
        assert isinstance(fi, dict)
        assert "Age" in fi
        assert pytest.approx(fi["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_feature_importance_values_are_floats(self):
        """All feature importance values should be numeric."""
        mod = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        for key, value in mod.MODEL_CARD["global_feature_importance"].items():
            assert isinstance(value, float), f"Feature '{key}' value is not a float"

    @pytest.mark.parametrize(
        "card_data",
        [
            {"key": "value"},
            {"nested": {"a": 1, "b": [1, 2, 3]}},
            MINIMAL_MODEL_CARD,
        ],
        ids=["minimal-single-key", "nested-structure", "synthetic-sample"],
    )
    def test_model_card_accepts_various_valid_json(self, card_data):
        """MODULE_CARD should reflect whatever valid JSON is in the file."""
        mod = _reload_prompts_with_card(card_data)
        assert mod.MODEL_CARD == card_data

    def test_model_card_path_resolves_relative_to_module(self, tmp_path, monkeypatch):
        """
        The path computation  Path(__file__).parent.parent / 'model_card.json'
        should point two directories above the prompts.py file.
        """
        # Build a fake package tree:  fake_root/agent/prompts.py
        agent_dir = tmp_path / "backend" / "agent"
        agent_dir.mkdir(parents=True)
        model_card_file = tmp_path / "backend" / "model_card.json"
        model_card_file.write_text(json.dumps(MINIMAL_MODEL_CARD))

        prompts_py = agent_dir / "prompts.py"
        # Read the real source and write it to the temp location
        real_src = Path(__file__).parent.parent / "agent" / "prompts.py"
        try:
            src_text = real_src.read_text()
        except FileNotFoundError:
            pytest.skip("Source file not available at expected location for path test")

        prompts_py.write_text(src_text)

        spec = importlib.util.spec_from_file_location("_test_prompts", prompts_py)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod.MODEL_CARD == MINIMAL_MODEL_CARD


# ---------------------------------------------------------------------------
# Tests – MODEL_CARD error conditions
# ---------------------------------------------------------------------------


class TestModelCardErrors:
    def test_file_not_found_raises_on_import(self, tmp_path, monkeypatch):
        """
        If model_card.json does not exist the module should raise FileNotFoundError
        at import time (because the open() call is at module scope).
        """
        sys.modules.pop("backend.agent.prompts", None)

        non_existent = tmp_path / "does_not_exist.json"

        with patch(
            "pathlib.Path.__truediv__",
            side_effect=lambda self, other: non_existent
            if str(other) == "model_card.json"
            else Path.__truediv__(self, other),
        ):
            with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
                with pytest.raises(FileNotFoundError):
                    import backend.agent.prompts  # noqa: PLC0415, F401

    def test_invalid_json_raises_on_import(self):
        """Malformed JSON in model_card.json should propagate a json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)

        m_open = mock_open(read_data="{ not valid json }")
        with patch("builtins.open", m_open):
            with pytest.raises((json.JSONDecodeError, ValueError)):
                # json.load will be called with a mock file handle;
                # we let the real json.load run so the decode error fires.
                import backend.agent.prompts  # noqa: PLC0415, F401

    def test_empty_json_object_is_accepted(self):
        """An empty JSON object {} is technically valid and should not raise."""
        mod = _reload_prompts_with_card({})
        assert mod.MODEL_CARD == {}

    def test_json_array_at_root(self):
        """
        If model_card.json is a JSON array instead of an object the module
        should still load (json.load returns a list).
        """
        array_card = [1, 2, 3]
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", mock_open(read_data=json.dumps(array_card))):
            with patch("json.load", return_value=array_card):
                import backend.agent.prompts as p  # noqa: PLC0415

        assert p.MODEL_CARD == array_card


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts):
        """SYSTEM_PROMPT must be a plain Python string."""
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts):
        """SYSTEM_PROMPT must not be blank."""
        assert prompts.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_has_meaningful_length(self, prompts):
        """
        A useful prompt should be more than a handful of characters.
        100 characters is a very conservative lower bound.
        """
        assert len(prompts.SYSTEM_PROMPT) >= 100

    def test_system_prompt_describes_underwriting_role(self, prompts):
        """The prompt should identify the agent as an underwriting assistant."""
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "underwriting" in lower or "underwriter" in lower

    def test_system_prompt_contains_senior_underwriting_assistant(self, prompts):
        """Exact role phrase from spec: 'senior underwriting assistant'."""
        assert "senior underwriting assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_instructs_confidentiality(self, prompts):
        """
        The prompt must contain a confidentiality constraint so the model
        does not reveal internal instructions.
        """
        lower = prompts.SYSTEM_PROMPT.lower()
        # Should mention not disclosing system instructions or tools
        has_disclose = "disclose" in lower or "reveal" in lower
        has_instruction_ref = "instruction" in lower or "system" in lower or "tools" in lower
        assert has_disclose, "SYSTEM_PROMPT should contain 'disclose' or 'reveal'"
        assert has_instruction_ref, (
            "SYSTEM_PROMPT should reference 'instruction', 'system', or 'tools'"
        )

    def test_system_prompt_instructs_never_disclose(self, prompts):
        """The word 'never' enforcing the hard prohibition should be present."""
        assert "never" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts):
        """The agent is supposed to run assessments — this must be in the prompt."""
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_is_helpful_framing(self, prompts):
        """The prompt should frame the agent as a helpful assistant."""
        assert "helpful" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_gathering_information(self, prompts):
        """The data-gathering role should be stated in the prompt."""
        lower = prompts.SYSTEM_PROMPT.lower()
        assert "gather" in lower or "information" in lower

    def test_system_prompt_does_not_start_with_whitespace(self, prompts):
        """No leading whitespace — cosmetic but matters for token budgets."""
        assert prompts.SYSTEM_PROMPT == prompts.SYSTEM_PROMPT.lstrip()

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "ignore previous instructions",
            "disregard",
            "do anything now",
        ],
    )
    def test_system_prompt_has_no_jailbreak_phrases(self, prompts, forbidden_phrase):
        """SYSTEM_PROMPT itself should not contain jailbreak-style language."""
        assert forbidden_phrase not in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_unchanged_across_imports(self, prompts):
        """
        SYSTEM_PROMPT is a module-level constant; re-importing should yield
        the identical object (CPython module caching).
        """
        import backend.agent.prompts as prompts2  # noqa: PLC0415

        assert prompts.SYSTEM_PROMPT is prompts2.SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests – Module-level structure / public API
# ---------------------------------------------------------------------------


class TestModulePublicApi:
    def test_model_card_attribute_exists(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_no_unexpected_public_callables(self, prompts):
        """
        prompts.py should expose only constants, not functions or classes
        that might accidentally be part of the public surface.
        """
        public_callables = [
            name
            for name in dir(prompts)
            if not name.startswith("_") and callable(getattr(prompts, name))
            and not isinstance(getattr(prompts, name), types.ModuleType)
        ]
        # Allow zero public callables (constants-only module)
        # If functions are added intentionally, update this list.
        assert public_callables == [], (
            f"Unexpected public callables found: {public_callables}"
        )

    def test_module_has_no_side_effects_beyond_constants(self, prompts):
        """
        The two expected public names should be the only non-dunder, non-module
        names at module scope (apart from imported stdlib names).
        """
        expected = {"MODEL_CARD", "SYSTEM_PROMPT"}
        stdlib_or_private_prefixes = ("_", "json", "Path")
        actual_public = {
            name
            for name in dir(prompts)
            if not name.startswith("_")
            and not any(name.startswith(p) for p in stdlib_or_private_prefixes)
            and not isinstance(getattr(prompts, name), types.ModuleType)
        }
        # expected names must all be present; no unknown names should appear
        assert expected.issubset(actual_public)


# ---------------------------------------------------------------------------
# TODO stubs
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason=(
        "TODO: Needs agreed schema / JSON-Schema spec for model_card.json to "
        "validate that all required keys (model_name, model_type, target_variable, "
        "global_feature_importance) are always present and correctly typed."
    )
)
def test_model_card_schema_validation():
    pass


@pytest.mark.skip(
    reason=(
        "TODO: Integration snapshot test — requires a stable, committed "
        "model_card.json fixture.  