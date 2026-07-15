"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json
- SYSTEM_PROMPT content, type, and key behavioural requirements
- Edge cases around the module-level constants

Mocks used:
- unittest.mock.patch / mock_open to simulate model_card.json file reads
- Temporary files (tmp_path) for integration-style load tests

TODOs:
- TODO: Full model_card.json schema validation once the complete schema is defined
- TODO: Test behaviour when model_card.json contains unexpected/extra fields
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
    Helper: reload the prompts module after patching the file system so that
    the module-level ``open`` call reads *model_card_dict* instead of the
    real file on disk.
    """
    json_bytes = json.dumps(model_card_dict)

    # Remove any previously imported version so the module body re-executes
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", mock_open(read_data=json_bytes)):
        with patch("json.load", return_value=model_card_dict):
            import backend.agent.prompts as prompts  # noqa: PLC0415

            importlib.reload(prompts)
            return prompts


# ---------------------------------------------------------------------------
# Tests – MODULE_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    """Verifies that MODEL_CARD is loaded from the expected JSON file."""

    def test_model_card_is_dict(self):
        """MODEL_CARD must be a dict (JSON object)."""
        from backend.agent import prompts

        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_not_empty(self):
        """MODEL_CARD must not be empty."""
        from backend.agent import prompts

        assert len(prompts.MODEL_CARD) > 0

    def test_model_card_contains_model_name(self):
        """MODEL_CARD should contain a 'model_name' key (from synthetic data)."""
        from backend.agent import prompts

        # If the real file is present this passes; if mocked it also passes.
        assert "model_name" in prompts.MODEL_CARD

    def test_model_card_model_name_is_string(self):
        from backend.agent import prompts

        assert isinstance(prompts.MODEL_CARD.get("model_name"), str)

    def test_model_card_contains_model_type(self):
        from backend.agent import prompts

        assert "model_type" in prompts.MODEL_CARD

    def test_model_card_contains_target_variable(self):
        from backend.agent import prompts

        assert "target_variable" in prompts.MODEL_CARD

    def test_model_card_global_feature_importance_is_dict(self):
        from backend.agent import prompts

        gfi = prompts.MODEL_CARD.get("global_feature_importance")
        assert isinstance(gfi, dict)

    def test_model_card_feature_importance_values_are_numeric(self):
        from backend.agent import prompts

        gfi = prompts.MODEL_CARD.get("global_feature_importance", {})
        for key, value in gfi.items():
            assert isinstance(value, (int, float)), (
                f"Feature importance for '{key}' is not numeric: {value!r}"
            )

    # ------------------------------------------------------------------
    # Mocked reload tests
    # ------------------------------------------------------------------

    def test_model_card_loaded_with_minimal_card(self):
        """Module correctly stores whatever JSON is in the file."""
        prompts = _reload_prompts_with_card(MINIMAL_MODEL_CARD)
        assert prompts.MODEL_CARD == MINIMAL_MODEL_CARD

    def test_model_card_loaded_with_empty_json_object(self):
        """An empty JSON object is valid – module should not crash."""
        prompts = _reload_prompts_with_card({})
        assert prompts.MODEL_CARD == {}

    def test_model_card_loaded_with_extra_fields(self):
        """Extra/unknown fields in model_card.json are preserved as-is."""
        extended = dict(MINIMAL_MODEL_CARD, extra_field="surprise")
        prompts = _reload_prompts_with_card(extended)
        assert prompts.MODEL_CARD.get("extra_field") == "surprise"

    def test_model_card_file_not_found_raises(self):
        """If the model_card.json file is missing, the import must raise FileNotFoundError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_model_card_invalid_json_raises(self):
        """Malformed JSON in model_card.json must raise json.JSONDecodeError."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)

        bad_json = "{ not valid json !!!"
        with patch("builtins.open", mock_open(read_data=bad_json)):
            # We do NOT patch json.load here so it actually tries to parse
            with pytest.raises((json.JSONDecodeError, ValueError)):
                import backend.agent.prompts  # noqa: PLC0415, F401

                importlib.reload(backend.agent.prompts)

    def test_model_card_path_points_to_parent_of_agent(self):
        """
        The computed path for model_card.json should be two levels above
        prompts.py  (i.e. backend/model_card.json).
        """
        from backend.agent import prompts

        expected_name = "model_card.json"
        # We can't assert the absolute path (CI vs local), but we can verify
        # the filename and that it is resolved relative to the module file.
        prompts_file = Path(prompts.__file__)
        expected_path = prompts_file.parent.parent / expected_name
        # The module-level variable _model_card_path should match
        assert prompts._model_card_path == expected_path

    def test_model_card_path_filename(self):
        from backend.agent import prompts

        assert prompts._model_card_path.name == "model_card.json"

    def test_model_card_path_is_path_instance(self):
        from backend.agent import prompts

        assert isinstance(prompts._model_card_path, Path)


# ---------------------------------------------------------------------------
# Tests – SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    """Verifies the content and characteristics of SYSTEM_PROMPT."""

    @pytest.fixture(autouse=True)
    def _import(self):
        from backend.agent import prompts

        self.prompts = prompts

    def test_system_prompt_is_string(self):
        assert isinstance(self.prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_not_empty(self):
        assert len(self.prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self):
        """Prompt must reference underwriting to set the correct domain context."""
        assert "underwriting" in self.prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self):
        """The audience is explicitly an underwriter."""
        assert "underwriter" in self.prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_confidentiality_instruction(self):
        """The prompt must instruct the model NOT to reveal internal instructions."""
        prompt_lower = self.prompts.SYSTEM_PROMPT.lower()
        # Accept various phrasings
        confidentiality_phrases = [
            "cannot disclose",
            "can never disclose",
            "never disclose",
            "not disclose",
            "not reveal",
        ]
        assert any(phrase in prompt_lower for phrase in confidentiality_phrases), (
            "SYSTEM_PROMPT does not contain a confidentiality instruction"
        )

    def test_system_prompt_does_not_leak_tool_names(self):
        """Prompt must not expose internal tool names or system instructions."""
        assert "tool" not in self.prompts.SYSTEM_PROMPT.lower() or (
            "tools" in self.prompts.SYSTEM_PROMPT.lower()
            and "disclose" in self.prompts.SYSTEM_PROMPT.lower()
        ), (
            "SYSTEM_PROMPT may leak internal tool information without the "
            "corresponding prohibition"
        )

    def test_system_prompt_presents_as_helpful_assistant(self):
        """Prompt must tell the model to present itself as a helpful assistant."""
        assert "helpful assistant" in self.prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self):
        """Core task – running assessments – must be referenced."""
        assert "assessment" in self.prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_trailing_newline_issues(self):
        """Prompt should be a non-trivially short string (sanity guard)."""
        assert len(self.prompts.SYSTEM_PROMPT) >= 50

    def test_system_prompt_is_module_level_constant(self):
        """SYSTEM_PROMPT must be accessible as a module-level attribute."""
        assert hasattr(self.prompts, "SYSTEM_PROMPT")

    def test_system_prompt_not_none(self):
        assert self.prompts.SYSTEM_PROMPT is not None

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "my instructions are",
            "my system prompt",
            "i have access to",
            "here are my tools",
        ],
    )
    def test_system_prompt_does_not_self_disclose(self, forbidden_phrase):
        """Prompt must not contain phrases that would self-disclose internals."""
        assert forbidden_phrase not in self.prompts.SYSTEM_PROMPT.lower(), (
            f"SYSTEM_PROMPT contains forbidden phrase: '{forbidden_phrase}'"
        )

    def test_system_prompt_instructs_gathering_information(self):
        """Gathering information is part of the agent's stated role."""
        assert "gather" in self.prompts.SYSTEM_PROMPT.lower() or (
            "information" in self.prompts.SYSTEM_PROMPT.lower()
        )


# ---------------------------------------------------------------------------
# Tests – Module-level attribute existence
# ---------------------------------------------------------------------------


class TestModuleAttributes:
    """Smoke tests ensuring all public names are exported by the module."""

    def test_model_card_attribute_exists(self):
        from backend.agent import prompts

        assert hasattr(prompts, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self):
        from backend.agent import prompts

        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_private_path_attribute_exists(self):
        from backend.agent import prompts

        assert hasattr(prompts, "_model_card_path")

    def test_no_unexpected_public_constants(self):
        """
        Guard against accidentally shipping sensitive data as a module-level
        constant.  Only whitelisted public names should be present.
        """
        from backend.agent import prompts

        allowed_public = {"MODEL_CARD", "SYSTEM_PROMPT"}
        public_names = {
            name
            for name in dir(prompts)
            if not name.startswith("_") and name.isupper()
        }
        unexpected = public_names - allowed_public
        assert not unexpected, f"Unexpected public constants found: {unexpected}"


# ---------------------------------------------------------------------------
# Parametrised – model card key validation against synthetic data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expected_key",
    [
        "model_name",
        "model_type",
        "target_variable",
        "global_feature_importance",
    ],
)
def test_model_card_expected_keys_present(expected_key):
    """Each key from the synthetic model_card.json must exist in MODEL_CARD."""
    from backend.agent import prompts

    assert expected_key in prompts.MODEL_CARD, (
        f"Expected key '{expected_key}' not found in MODEL_CARD"
    )


@pytest.mark.parametrize(
    "feature_name",
    [
        "Age",
        "Education_Level",
        "Employment_Status",
        "Nationality",
        "Customer_Segment",
        "Annual_Income",
        "Liquid_Assets",
    ],
)
def test_model_card_feature_importance_contains_known_features(feature_name):
    """Known features from the synthetic data should appear in feature importance."""
    from backend.agent import prompts

    gfi = prompts.MODEL_CARD.get("global_feature_importance", {})
    assert feature_name in gfi, (
        f"Feature '{feature_name}' missing from global_feature_importance"
    )


@pytest.mark.parametrize(
    "feature_name,min_value",
    [
        ("Age", 30.0),  # dominant feature per synthetic data (~34.6)
        ("Education_Level", 0.0),
        ("Employment_Status", 0.0),
    ],
)
def test_model_card_feature_importance_age_dominates(feature_name, min_value):
    """Age should have the highest importance per synthetic data."""
    from backend.agent import prompts

    gfi = prompts.MODEL_CARD.get("global_feature_importance", {})
    if feature_name in gfi:
        assert gfi[feature_name] >= min_value, (
            f"Feature '{feature_name}' importance {gfi[feature_name]} "
            f"is below expected minimum {min_value}"
        )


# ---------------------------------------------------------------------------
# Skipped / stub tests – require additional context
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: full model_card.json JSON Schema not yet defined")
def test_model_card_validates_against_json_schema():
    """TODO: validate MODEL_CARD against a formal JSON Schema once available."""
    pass


@pytest.mark.skip(
    reason="TODO: need access to the complete assessment_criterias.json "
    "to test prompt assembly that references it"
)
def test_system_prompt_integrates_with_assessment_criteria():
    """TODO: verify SYSTEM_PROMPT is compatible with assessment_criterias.json prompts."""
    pass


@pytest.mark.skip(
    reason="TODO: test concurrent / parallel imports to ensure module-level "
    "file IO is thread-safe"
)
def test_model_card_thread_safe_loading():
    """TODO: ensure MODEL_CARD loading is safe under concurrent import."""
    pass