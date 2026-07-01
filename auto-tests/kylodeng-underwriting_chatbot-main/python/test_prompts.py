"""
Tests for backend/agent/prompts.py

What is tested:
- MODULE_CARD is loaded correctly from model_card.json at import time
- SYSTEM_PROMPT is defined with expected content and constraints
- Module-level constants have correct types
- Content rules embedded in SYSTEM_PROMPT (confidentiality, role description)

Mocks used:
- builtins.open (patched to control model_card.json content)
- pathlib.Path (used to verify path resolution logic)
- json.load (patched where needed)

TODOs:
- TODO: Integration test that verifies the real model_card.json on disk has required keys
         — would need the actual file present in CI environment
"""

import builtins
import importlib
import io
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
    },
}


def _reload_prompts(fake_model_card: dict):
    """
    Reload backend.agent.prompts with a mocked model_card.json so each test
    is hermetic.  Returns the freshly imported module.
    """
    serialised = json.dumps(fake_model_card)

    m = mock_open(read_data=serialised)
    # Remove cached module so importlib performs a fresh exec
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module, m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prompts():
    """Fresh import of the module with a minimal but valid model card."""
    module, _ = _reload_prompts(MINIMAL_MODEL_CARD)
    yield module
    # cleanup
    sys.modules.pop("backend.agent.prompts", None)


# ---------------------------------------------------------------------------
# MODEL_CARD tests
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD, dict)

    def test_model_card_contains_expected_keys(self, prompts):
        expected_keys = {"model_name", "model_type", "target_variable"}
        assert expected_keys.issubset(prompts.MODEL_CARD.keys())

    def test_model_card_model_name_value(self, prompts):
        assert prompts.MODEL_CARD["model_name"] == "Underwriting Risk Classification"

    def test_model_card_model_type_value(self, prompts):
        assert prompts.MODEL_CARD["model_type"] == "CatBoostClassifier"

    def test_model_card_target_variable_value(self, prompts):
        assert prompts.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_global_feature_importance_is_dict(self, prompts):
        assert isinstance(prompts.MODEL_CARD["global_feature_importance"], dict)

    def test_model_card_age_importance_value(self, prompts):
        importance = prompts.MODEL_CARD["global_feature_importance"]["Age"]
        assert pytest.approx(importance, rel=1e-6) == 34.57614295408571

    @pytest.mark.parametrize(
        "card",
        [
            {},
            {"model_name": "Minimal"},
            {"model_name": "X", "model_type": "Y", "target_variable": "Z"},
        ],
    )
    def test_model_card_accepts_arbitrary_json_objects(self, card):
        """MODULE_CARD should hold whatever the JSON file contains — no schema enforcement."""
        module, _ = _reload_prompts(card)
        assert module.MODEL_CARD == card

    def test_model_card_not_none(self, prompts):
        assert prompts.MODEL_CARD is not None

    def test_model_card_is_not_empty_for_minimal_card(self, prompts):
        assert len(prompts.MODEL_CARD) > 0


# ---------------------------------------------------------------------------
# MODEL_CARD path resolution tests
# ---------------------------------------------------------------------------


class TestModelCardPathResolution:
    def test_open_called_once_on_import(self):
        serialised = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=serialised)
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", m):
            import backend.agent.prompts  # noqa: F401, PLC0415

        m.assert_called_once()

    def test_open_called_with_path_ending_in_model_card_json(self):
        serialised = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=serialised)
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", m):
            import backend.agent.prompts  # noqa: F401, PLC0415

        call_args = m.call_args
        opened_path = call_args[0][0]  # first positional argument
        assert str(opened_path).endswith("model_card.json")

    def test_model_card_path_is_two_levels_above_module(self):
        """_model_card_path should resolve to …/backend/model_card.json."""
        serialised = json.dumps(MINIMAL_MODEL_CARD)
        m = mock_open(read_data=serialised)
        sys.modules.pop("backend.agent.prompts", None)

        with patch("builtins.open", m):
            import backend.agent.prompts  # noqa: F401, PLC0415

        opened_path = Path(str(m.call_args[0][0]))
        # The parent of model_card.json should be the backend directory
        assert opened_path.name == "model_card.json"
        assert opened_path.parent.name == "backend"


# ---------------------------------------------------------------------------
# Error-condition tests for model card loading
# ---------------------------------------------------------------------------


class TestModelCardLoadingErrors:
    def test_file_not_found_raises_on_import(self):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_invalid_json_raises_on_import(self):
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="THIS IS NOT JSON {{{{")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_permission_error_raises_on_import(self):
        sys.modules.pop("backend.agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("access denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_empty_json_object_is_valid(self):
        """An empty JSON object {} should load without error."""
        module, _ = _reload_prompts({})
        assert module.MODEL_CARD == {}

    def test_json_array_at_top_level(self):
        """A JSON array (list) is valid JSON — module should not crash."""
        sys.modules.pop("backend.agent.prompts", None)
        m = mock_open(read_data="[1, 2, 3]")
        with patch("builtins.open", m):
            import backend.agent.prompts as p  # noqa: PLC0415

        assert p.MODEL_CARD == [1, 2, 3]


# ---------------------------------------------------------------------------
# SYSTEM_PROMPT tests
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_system_prompt_is_string(self, prompts):
        assert isinstance(prompts.SYSTEM_PROMPT, str)

    def test_system_prompt_is_not_empty(self, prompts):
        assert len(prompts.SYSTEM_PROMPT.strip()) > 0

    def test_system_prompt_mentions_underwriting(self, prompts):
        assert "underwriting" in prompts.SYSTEM_PROMPT.lower() or "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_senior_underwriting_assistant(self, prompts):
        assert "senior underwriting assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_contains_confidentiality_instruction(self, prompts):
        """The prompt must instruct the model never to reveal internal instructions."""
        lowered = prompts.SYSTEM_PROMPT.lower()
        assert "never disclose" in lowered or "cannot disclose" in lowered or "never" in lowered

    def test_system_prompt_contains_tool_confidentiality(self, prompts):
        lowered = prompts.SYSTEM_PROMPT.lower()
        assert "tools" in lowered or "system instructions" in lowered

    def test_system_prompt_contains_helpful_assistant_persona(self, prompts):
        assert "helpful assistant" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_assessments(self, prompts):
        assert "assessment" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_expose_internal_tools_literally(self, prompts):
        """The prompt should not contain raw tool names or JSON function definitions."""
        assert "def " not in prompts.SYSTEM_PROMPT
        assert '"name":' not in prompts.SYSTEM_PROMPT

    def test_system_prompt_is_defined_at_module_level(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "ignore previous instructions",
            "disregard your instructions",
            "you are now",
        ],
    )
    def test_system_prompt_does_not_contain_jailbreak_phrases(self, prompts, forbidden_phrase):
        assert forbidden_phrase.lower() not in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_addresses_underwriter_audience(self, prompts):
        assert "underwriter" in prompts.SYSTEM_PROMPT.lower()

    def test_system_prompt_is_not_none(self, prompts):
        assert prompts.SYSTEM_PROMPT is not None


# ---------------------------------------------------------------------------
# Module-level constant existence tests
# ---------------------------------------------------------------------------


class TestModuleConstants:
    def test_module_exports_model_card(self, prompts):
        assert hasattr(prompts, "MODEL_CARD")

    def test_module_exports_system_prompt(self, prompts):
        assert hasattr(prompts, "SYSTEM_PROMPT")

    def test_model_card_path_attribute_exists(self, prompts):
        """_model_card_path is a private but inspectable module attribute."""
        assert hasattr(prompts, "_model_card_path")

    def test_model_card_path_is_path_instance(self, prompts):
        assert isinstance(prompts._model_card_path, Path)

    def test_no_unexpected_public_names(self, prompts):
        """Public names in the module should be limited to the expected constants."""
        public_names = {name for name in dir(prompts) if not name.startswith("_")}
        expected_public = {"MODEL_CARD", "SYSTEM_PROMPT", "json", "Path"}
        # We only assert expected names are present, not that nothing else exists
        assert expected_public.issubset(public_names)


# ---------------------------------------------------------------------------
# Parameterised edge-case data tests
# ---------------------------------------------------------------------------


class TestModelCardParameterised:
    @pytest.mark.parametrize(
        "card_data",
        [
            {"model_name": "Underwriting Risk Classification"},
            {"model_type": "CatBoostClassifier", "target_variable": "Risk_Classification"},
            MINIMAL_MODEL_CARD,
            {
                "model_name": "Full Card",
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
            },
        ],
    )
    def test_model_card_round_trips_correctly(self, card_data):
        """Whatever JSON is in the file should be faithfully stored in MODEL_CARD."""
        module, _ = _reload_prompts(card_data)
        assert module.MODEL_CARD == card_data

    @pytest.mark.parametrize("feature,expected_importance", [
        ("Age", 34.57614295408571),
        ("Education_Level", 2.0984070824092758),
    ])
    def test_feature_importance_values(self, feature, expected_importance):
        module, _ = _reload_prompts(MINIMAL_MODEL_CARD)
        actual = module.MODEL_CARD["global_feature_importance"][feature]
        assert pytest.approx(actual, rel=1e-6) == expected_importance


# ---------------------------------------------------------------------------
# TODO stubs
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="TODO: Requires the real backend/model_card.json present on disk — integration test only"
)
def test_real_model_card_json_has_required_keys():
    """
    TODO: Verify that the actual model_card.json shipped with the project contains
    at minimum: model_name, model_type, target_variable, global_feature_importance.
    Should be run as an integration test with the real file on disk.
    """
    pass


@pytest.mark.skip(
    reason="TODO: Requires knowledge of all tool names available to the agent to assert none appear in the prompt"
)
def test_system_prompt_does_not_leak_tool_names():
    """
    TODO: Once the full tool registry is available, assert that no tool function
    names are present verbatim in SYSTEM_PROMPT.
    """
    pass


@pytest.mark.skip(
    reason="TODO: Requires LLM evaluation framework to assert semantic correctness of the prompt"
)
def test_system_prompt_semantic_quality():
    """
    TODO: Use an LLM-as-judge or rubric-based evaluator to assert the prompt
    adequately constrains model behaviour for underwriting use cases.
    """
    pass