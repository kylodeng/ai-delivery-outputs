"""
Test module for backend/agent/prompts.py

What is tested:
- MODULE_CARD loaded correctly from model_card.json at module load time
- SYSTEM_PROMPT string content, structure, and key behavioural constraints
- File path resolution for _model_card_path
- Edge cases: malformed JSON, missing file, empty JSON object

Mocks used:
- unittest.mock.patch / mock_open to intercept file I/O
- tmp_path (pytest fixture) to create real temporary model_card.json files
- importlib to force module re-import with different file states

TODOs:
- TODO: Full integration test against real model_card.json once CI has access to the file
- TODO: Test behaviour when model_card.json schema changes (requires agreed schema contract)
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

NESTED_MODEL_CARD = {
    "model_name": "Deep Risk Model",
    "model_type": "XGBoostClassifier",
    "metadata": {"version": "2.0", "author": "QA Team"},
    "features": ["Age", "Income"],
}


def _reload_prompts_with_model_card(model_card_data: dict):
    """
    Re-import backend.agent.prompts after patching the file open call so that
    the module-level code runs again with the supplied model_card_data.

    Returns the freshly imported module.
    """
    json_content = json.dumps(model_card_data)

    # Remove cached module so module-level code re-executes
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)

    m = mock_open(read_data=json_content)
    with patch("builtins.open", m):
        import backend.agent.prompts as prompts_module  # noqa: PLC0415

    return prompts_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_module():
    """Ensure the real module is restored after each test that mutates sys.modules."""
    yield
    sys.modules.pop("backend.agent.prompts", None)
    sys.modules.pop("agent.prompts", None)


# ---------------------------------------------------------------------------
# Tests: MODEL_CARD loading
# ---------------------------------------------------------------------------


class TestModelCardLoading:
    def test_model_card_is_dict(self):
        """MODEL_CARD must be a dictionary after successful load."""
        mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert isinstance(mod.MODEL_CARD, dict)

    def test_model_card_minimal_content(self):
        """MODEL_CARD reflects the data in the mocked JSON file."""
        mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert mod.MODEL_CARD["model_name"] == "Underwriting Risk Classification"
        assert mod.MODEL_CARD["model_type"] == "CatBoostClassifier"
        assert mod.MODEL_CARD["target_variable"] == "Risk_Classification"

    def test_model_card_feature_importance_values(self):
        """Global feature importance values are loaded as floats."""
        mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        gfi = mod.MODEL_CARD.get("global_feature_importance", {})
        assert isinstance(gfi, dict)
        assert pytest.approx(gfi["Age"], rel=1e-6) == 34.57614295408571
        assert pytest.approx(gfi["Annual_Income"], rel=1e-6) == 1.0169358497744714

    def test_model_card_empty_json(self):
        """An empty JSON object {} is valid — MODEL_CARD should be an empty dict."""
        mod = _reload_prompts_with_model_card(EMPTY_MODEL_CARD)
        assert mod.MODEL_CARD == {}

    def test_model_card_nested_structure(self):
        """Nested JSON structures are preserved intact."""
        mod = _reload_prompts_with_model_card(NESTED_MODEL_CARD)
        assert mod.MODEL_CARD["metadata"]["version"] == "2.0"
        assert mod.MODEL_CARD["features"] == ["Age", "Income"]

    @pytest.mark.parametrize(
        "model_card_data",
        [
            MINIMAL_MODEL_CARD,
            EMPTY_MODEL_CARD,
            NESTED_MODEL_CARD,
            {"single_key": 42},
            {"list_value": [1, 2, 3]},
        ],
        ids=[
            "minimal_card",
            "empty_card",
            "nested_card",
            "single_key_card",
            "list_value_card",
        ],
    )
    def test_model_card_parametrized_load(self, model_card_data):
        """MODEL_CARD is always equal to whatever the JSON file contains."""
        mod = _reload_prompts_with_model_card(model_card_data)
        assert mod.MODEL_CARD == model_card_data

    def test_model_card_file_not_found_raises(self):
        """FileNotFoundError propagates when model_card.json does not exist."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", side_effect=FileNotFoundError("no such file")):
            with pytest.raises(FileNotFoundError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_model_card_malformed_json_raises(self):
        """json.JSONDecodeError propagates when model_card.json is malformed."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        m = mock_open(read_data="{ this is not valid json }")
        with patch("builtins.open", m):
            with pytest.raises(json.JSONDecodeError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_model_card_permission_error_raises(self):
        """PermissionError propagates when the file cannot be opened."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        with patch("builtins.open", side_effect=PermissionError("permission denied")):
            with pytest.raises(PermissionError):
                import backend.agent.prompts  # noqa: F401, PLC0415

    def test_model_card_json_array_raises_or_loads(self):
        """
        If the JSON root is an array, json.load succeeds but MODEL_CARD won't be a dict.
        Verify the module does NOT silently convert it.
        """
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        m = mock_open(read_data="[1, 2, 3]")
        with patch("builtins.open", m):
            import backend.agent.prompts as mod  # noqa: PLC0415

        # json.load on a list produces a list — module should surface that as-is
        assert mod.MODEL_CARD == [1, 2, 3]

    def test_open_called_exactly_once(self):
        """The file is opened exactly once at module import time."""
        sys.modules.pop("backend.agent.prompts", None)
        sys.modules.pop("agent.prompts", None)
        m = mock_open(read_data=json.dumps(MINIMAL_MODEL_CARD))
        with patch("builtins.open", m):
            import backend.agent.prompts  # noqa: F401, PLC0415

        assert m.call_count == 1


# ---------------------------------------------------------------------------
# Tests: _model_card_path
# ---------------------------------------------------------------------------


class TestModelCardPath:
    def test_path_is_pathlib_path(self):
        """_model_card_path is a pathlib.Path instance."""
        mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert isinstance(mod._model_card_path, Path)

    def test_path_filename(self):
        """The resolved path ends with model_card.json."""
        mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert mod._model_card_path.name == "model_card.json"

    def test_path_parent_is_backend(self):
        """
        The path is two levels up from prompts.py, pointing to the backend directory.
        Structure: backend/agent/prompts.py → backend/model_card.json
        """
        mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        # parent of prompts.py → agent/, parent of agent/ → backend/
        assert mod._model_card_path.parent.name == "backend"

    def test_path_is_absolute(self):
        """_model_card_path must be an absolute path (resolved via __file__)."""
        mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert mod._model_card_path.is_absolute()


# ---------------------------------------------------------------------------
# Tests: SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    @pytest.fixture(autouse=True)
    def _load_module(self):
        self.mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)

    def test_system_prompt_is_string(self):
        """SYSTEM_PROMPT must be a non-empty string."""
        assert isinstance(self.mod.SYSTEM_PROMPT, str)
        assert len(self.mod.SYSTEM_PROMPT) > 0

    def test_system_prompt_mentions_underwriting(self):
        """SYSTEM_PROMPT must reference underwriting to establish context."""
        assert "underwriting" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_mentions_underwriter(self):
        """SYSTEM_PROMPT must address the target user role (underwriter)."""
        assert "underwriter" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_confidentiality_instruction(self):
        """SYSTEM_PROMPT must instruct the model never to disclose internal instructions."""
        prompt_lower = self.mod.SYSTEM_PROMPT.lower()
        # Key confidentiality phrases
        assert "never" in prompt_lower or "cannot" in prompt_lower or "can never" in prompt_lower
        assert "disclose" in prompt_lower or "reveal" in prompt_lower

    def test_system_prompt_no_tool_disclosure(self):
        """The prompt explicitly forbids revealing tools access."""
        assert "tools" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_helpful_assistant_persona(self):
        """The prompt instructs the model to present itself as a helpful assistant."""
        assert "helpful assistant" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_assessment_capability(self):
        """The prompt must mention running assessments as a capability."""
        assert "assessment" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_gathering_information(self):
        """The prompt must reference information gathering."""
        assert "gathering" in self.mod.SYSTEM_PROMPT.lower() or "gather" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_no_newline_only_content(self):
        """SYSTEM_PROMPT is not composed entirely of whitespace."""
        assert self.mod.SYSTEM_PROMPT.strip() != ""

    def test_system_prompt_immutable_at_module_level(self):
        """Re-importing the module with same data produces the same SYSTEM_PROMPT."""
        mod2 = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)
        assert mod2.SYSTEM_PROMPT == self.mod.SYSTEM_PROMPT

    @pytest.mark.parametrize(
        "forbidden_phrase",
        [
            "internal system instructions",
            "tools you have access to",
        ],
    )
    def test_system_prompt_forbidden_disclosure_phrases_referenced(self, forbidden_phrase):
        """
        The prompt explicitly names what must never be disclosed —
        these phrases appear in the prohibition clauses.
        """
        assert forbidden_phrase in self.mod.SYSTEM_PROMPT

    def test_system_prompt_senior_role_mentioned(self):
        """The prompt establishes the senior underwriting assistant role."""
        assert "senior" in self.mod.SYSTEM_PROMPT.lower()

    def test_system_prompt_does_not_start_with_whitespace(self):
        """SYSTEM_PROMPT should not begin with leading whitespace."""
        assert self.mod.SYSTEM_PROMPT == self.mod.SYSTEM_PROMPT.lstrip() or \
               self.mod.SYSTEM_PROMPT[0] not in ("\n", "\r", "\t")

    def test_system_prompt_assess_customers_intent(self):
        """The prompt's stated purpose is to help assess customers."""
        assert "assess" in self.mod.SYSTEM_PROMPT.lower()
        assert "customer" in self.mod.SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Tests: Module-level public API
# ---------------------------------------------------------------------------


class TestModulePublicAttributes:
    @pytest.fixture(autouse=True)
    def _load_module(self):
        self.mod = _reload_prompts_with_model_card(MINIMAL_MODEL_CARD)

    def test_model_card_attribute_exists(self):
        assert hasattr(self.mod, "MODEL_CARD")

    def test_system_prompt_attribute_exists(self):
        assert hasattr(self.mod, "SYSTEM_PROMPT")

    def test_private_path_attribute_exists(self):
        assert hasattr(self.mod, "_model_card_path")

    def test_no_unexpected_public_callables(self):
        """
        The module exposes no public functions (only constants).
        Any callable added later should be reviewed for test coverage.
        """
        public_callables = [
            name
            for name in dir(self.mod)
            if not name.startswith("_") and callable(getattr(self.mod, name))
        ]
        # Only built-in module artefacts (like Path, json) could appear — none are expected
        # from the module's own code. Adjust if public helpers are added.
        assert public_callables == [], (
            f"Unexpected public callables found: {public_callables}. "
            "Add explicit tests for each."
        )


# ---------------------------------------------------------------------------
# Skipped / TODO stubs
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="TODO: requires real filesystem with model_card.json present in CI")
def test_integration_real_model_card_file():
    """
    TODO: Full integration test — import prompts without any mocking and
    verify MODEL_CARD matches the committed model_card.json schema.
    Needs: agreed JSON schema + CI access to backend/model_card.json.
    """
    import backend.agent.prompts as mod  # noqa: PLC0415