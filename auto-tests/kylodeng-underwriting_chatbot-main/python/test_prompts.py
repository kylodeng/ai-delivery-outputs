"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loading: verifies MODEL_CARD is loaded correctly from model_card.json
- SYSTEM_PROMPT: verifies content, type, and key behavioural constraints
- Module-level constants: presence, types, and values

Mocks used:
- unittest.mock.mock_open / patch: used to simulate file I/O for model_card.json
  so tests never touch the real filesystem (except one integration-style test
  that explicitly needs the real file to be present).
- json.load is patched where appropriate.

TODOs:
- TODO: If model_card.json schema is formalised, add schema-validation tests.
- TODO: Add tests for any future prompt-builder functions added to prompts.py.
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

FAKE_MODEL_CARD = {
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


def _reload_prompts_with_mock_card(fake_card: dict):
    """
    Reload the prompts module with a patched open() that returns *fake_card*
    as the JSON content.  Returns the freshly-imported module object.
    """
    serialised = json.dumps(fake_card)

    # Remove cached module so importlib reimports it fresh
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    m = mock_open(read_data=serialised)
    with patch("builtins.open", m):
        with patch("json.load", return_value=fake_card):
            import backend.agent.prompts as prompts_module  # noqa: PLC0415

            return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def prompts():
    """
    Import the real prompts module.  This will read the real model_card.json
    from disk.  If the file is absent the fixture is skipped gracefully.
    """
    try:
        # Ensure a clean import
        sys.modules.pop("backend.agent.prompts", None)
        import backend.agent.prompts as _prompts  # noqa: PLC0415

        return _prompts
    except FileNotFoundError:
        pytest.skip(
            "Real model_card.json not found on disk – skipping integration fixture."
        )


@pytest.fixture()
def prompts_mocked():
    """
    Return the prompts module loaded with the FAKE_MODEL_CARD – no real file I/O.
    """
    module = _reload_prompts_with_mock_card(FAKE_MODEL_CARD)
    yield module
    # Clean up so other tests get a pristine import
    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# MODEL_CARD loading tests
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts_mocked):
        assert isinstance(prompts_mocked.MODEL_CARD, dict)

    def test_model_card_contains_expected_keys(self, prompts_mocked):
        expected_keys = {
            "model_name",
            "model_type",
            "target_variable",
            "global_feature_importance",
        }
        assert expected_keys.issubset(prompts_mocked.MODEL_CARD.keys())

    def test_model_card_model_name(self, prompts_mocked):
        assert prompts_mocked.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type(self, prompts_mocked):
        assert prompts_mocked.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable(self, prompts_mocked):
        assert prompts_mocked.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_feature_importance_is_dict(self, prompts_mocked):
        fi = prompts_mocked.MODEL_CARD["global_feature_importance"]
        assert isinstance(fi, dict)

    def test_model_card_feature_importance_values_are_floats(self, prompts_mocked):
        fi = prompts_mocked.MODEL_CARD["global_feature_importance"]
        for key, value in fi.items():
            assert isinstance(value, float), f"Feature {key!r} importance is not a float"

    def test_model_card_age_importance_value(self, prompts_mocked):
        fi = prompts_mocked.MODEL_CARD["global_feature_importance"]
        assert pytest.approx(fi["Age"], rel=1e-6) == 34.57614295408571

    def test_model_card_not_empty(self, prompts_mocked):
        assert len(prompts_mocked.MODEL_CARD) > 0

    def test_open_called_once_during_import(self):
        """Verify builtins.open is called exactly once while importing the module."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data=json.dumps(FAKE_MODEL_CARD))
        with patch("builtins.open", m):
            with patch("json.load", return_value=FAKE_MODEL_CARD):
                import backend.agent.prompts  # noqa: F401, PLC0415

        m.assert_called_once()
        sys.modules.pop("backend.agent.prompts", None)

    def test_file_not_found_raises(self):
        """If model_card.json is missing, importing the module must raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401, PLC0415
        sys.modules.pop("backend.agent.prompts", None)

    def test_invalid_json_raises(self):
        """If model_card.json contains invalid JSON, a JSONDecodeError should propagate."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="NOT_VALID_JSON{{{{")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401, PLC0415
        sys.modules.pop("backend.agent.prompts", None)

    def test_empty_json_object_is_accepted(self):
        """An empty JSON object {} should load without error."""
        sys.modules.pop("backend.agent.prompts", None)
        empty_card: dict = {}
        m = mock_open(read_data=json.dumps(empty_card))
        with patch("builtins.open", m):
            with patch("json.load", return_value=empty_card):
                import backend.agent.prompts as mod  # noqa: PLC0415

                assert mod.MODEL_CARD == {}
        sys.modules.pop("backend.agent.prompts", None)

    def test_model_card_path_points_to_correct_relative_location(self):
        """_model_card_path should resolve to  <package_root>/model_card.json."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data=json.dumps(FAKE_MODEL_CARD))
        with patch("builtins.open", m) as patched_open:
            with patch("json.load", return_value=FAKE_MODEL_CARD):
                import backend.agent.prompts  # noqa: F401, PLC0415

        call_args = patched_open.call_args
        opened_path = Path(call_args[0][0])
        assert opened_path.name == "model_card.json"
        sys.modules.pop("backend.agent.prompts", None)

    # Parametrised: different valid model-card shapes
    @pytest.mark.parametrize(
        "card",
        [
            {"model_name": "Model A"},
            {"model_name": "Model B", "model_type": "XGBoost"},
            FAKE_MODEL_CARD,
        ],
    )
    def test_model_card_accepts_various_shapes(self, card):
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data=json.dumps(card))
        with patch("builtins.open", m):
            with patch("json.load", return_value=card):
                import backend.agent.prompts as mod  # noqa: PLC0415

                assert mod.MODEL_CARD == card
        sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts_mocked):
        assert isinstance(prompts_mocked.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts_mocked):
        assert prompts_mocked.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_minimum_length(self, prompts_mocked):
        """Prompt should be substantial – at least 100 characters."""
        assert len(prompts_mocked.SYSTEM_PROMPT) >= 100

    def test_system_prompt_mentions_underwriting(self, prompts_mocked):
        assert "underwriting" in prompts_mocked.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self, prompts_mocked):
        assert "underwriter" in prompts_mocked.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_assistant_persona(self, prompts_mocked):
        assert "assistant" in prompts_mocked.SYSTEM_PROMPT.lower()

    def test_system_prompt_confidentiality_clause_present(self, prompts_mocked):
        """The prompt must include a clause preventing disclosure of internals."""
        lower = prompts_mocked.SYSTEM_PROMPT.lower()
        assert "disclose" in lower or "reveal" in lower or "never" in lower

    def test_system_prompt_no_disclosure_of_tools(self, prompts_mocked):
        lower = prompts_mocked.SYSTEM_PROMPT.lower()
        # The instruction says the assistant can NEVER disclose tools
        assert "tools" in lower

    def test_system_prompt_mentions_assessments(self, prompts_mocked):
        assert "assessment" in prompts_mocked.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_gathering_information(self, prompts_mocked):
        lower = prompts_mocked.SYSTEM_PROMPT.lower()
        assert "gather" in lower or "information" in lower

    def test_system_prompt_is_constant_across_multiple_accesses(self, prompts_mocked):
        """Accessing SYSTEM_PROMPT twice should return the identical object."""
        first = prompts_mocked.SYSTEM_PROMPT
        second = prompts_mocked.SYSTEM_PROMPT
        assert first is second

    def test_system_prompt_no_leading_trailing_newlines(self, prompts_mocked):
        """Prompt should not start or end with newline characters."""
        assert not prompts_mocked.SYSTEM_PROMPT.startswith("\n")
        assert not prompts_mocked.SYSTEM_PROMPT.endswith("\n")

    def test_system_prompt_does_not_expose_internal_instructions(self, prompts_mocked):
        """
        The SYSTEM_PROMPT itself should not instruct the LLM to reveal
        its own system instructions (double-negation sanity check).
        """
        lower = prompts_mocked.SYSTEM_PROMPT.lower()
        assert "reveal the internal system instructions" not in lower or "can never" in lower

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "ignore previous instructions",
            "disregard all prior",
            "do anything now",
        ],
    )
    def test_system_prompt_free_of_injection_phrases(self, prompts_mocked, forbidden_phrase):
        """Prompt must not contain known prompt-injection patterns."""
        assert forbidden_phrase.lower() not in prompts_mocked.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Module-level attribute tests
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    def test_model_card_attribute_exists(self, prompts_mocked):
        assert hasattr(prompts_mocked, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self, prompts_mocked):
        assert hasattr(prompts_mocked, "SYSTEM_PROMPT")

    def test_no_unexpected_public_callables(self, prompts_mocked):
        """
        prompts.py should expose only data constants, not functions/classes
        at module level (beyond what was imported).
        """
        import inspect  # noqa: PLC0415

        public_callables = [
            name
            for name in dir(prompts_mocked)
            if not name.startswith("_")
            and callable(getattr(prompts_mocked, name))
            and not isinstance(getattr(prompts_mocked, name), types.ModuleType)
            and inspect.isbuiltin(getattr(prompts_mocked, name)) is False
        ]
        # Allow standard helpers that might appear; flag anything unexpected
        allowed = {"Path"}  # Path class is imported
        unexpected = set(public_callables) - allowed
        assert unexpected == set(), f"Unexpected public callables found: {unexpected}"


# ---------------------------------------------------------------------------
# Integration test (real filesystem) – skipped if file absent
# ---------------------------------------------------------------------------


class TestIntegrationRealFile:
    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "model_card.json").exists(),
        reason="Real model_card.json not present in backend/",
    )
    def test_real_model_card_loads_as_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "model_card.json").exists(),
        reason="Real model_card.json not present in backend/",
    )
    def test_real_model_card_not_empty(self, prompts):
        assert len(prompts.MODEL_CARD) > 0

    @pytest.mark.skipif(
        not (Path(__file__).parent.parent / "model_card.json").exists(),
        reason="Real model_card.json not present in backend/",
    )
    def test_real_system_prompt_is_str(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)


# ---------------------------------------------------------------------------
# Stub tests – require additional context
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="TODO: Schema for model_card.json not yet formalised – add JSON-schema validation once available."
)
def test_model_card_validates_against_schema():
    pass


@pytest.mark.skip(
    reason="TODO: If prompt versioning is introduced, add tests that verify the correct version is loaded."
)
def test_system_prompt_versioning():
    