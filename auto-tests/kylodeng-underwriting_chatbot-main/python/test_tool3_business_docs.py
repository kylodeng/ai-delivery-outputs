"""
Test module for .github/scripts/tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, file truncation behaviour
    - build_full_output(): full markdown structure, gap-only markdown structure,
      timestamp injection, OUTPUT_REPO_OWNER/OUTPUT_REPO references
    - __main__ block: environment variable handling, success flow, exception/failure flow

Mocks used:
    - shared.call_claude          — patched to return controlled strings
    - shared.get_repo_files       — patched to return synthetic file dicts
    - shared.write_output_file    — patched to avoid real GitHub API calls
    - shared.send_email           — patched to avoid real SMTP calls
    - shared.email_html           — patched to return a simple string
    - shared.write_audit_entry    — patched to avoid real file/API writes
    - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
    - TODO: Integration test that verifies the Claude prompt template substitution
      produces valid markdown when call_claude returns a realistic multi-section response
    - TODO: Test __main__ failure branch fully once the truncated source (email_html call
      in the except block) is confirmed complete
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with its `shared` dependency mocked
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"
SCRIPT_MODULE_NAME = "tool3_business_docs"

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module so the import does not crash."""
    fake = types.ModuleType("shared")
    fake.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?")
    fake.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    fake.write_output_file = MagicMock(return_value="https://github.com/output/file")
    fake.send_email = MagicMock()
    fake.email_html = MagicMock(return_value="<html>body</html>")
    fake.write_audit_entry = MagicMock()
    fake.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    fake.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return fake


def _import_module(fake_shared=None):
    """
    Import (or re-import) tool3_business_docs with `shared` injected.
    Returns (module, fake_shared).
    """
    if fake_shared is None:
        fake_shared = _make_fake_shared()

    # Ensure the scripts directory is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also support running from repo root or the scripts dir directly
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in [
        os.path.join(here, ".github", "scripts"),
        here,
    ]:
        if candidate not in sys.path:
            sys.path.insert(0, candidate)

    # Inject the fake shared module before importing the target
    sys.modules["shared"] = fake_shared

    # Remove cached version so we get a fresh import each time
    sys.modules.pop(SCRIPT_MODULE_NAME, None)

    module = importlib.import_module(SCRIPT_MODULE_NAME)
    return module, fake_shared


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def mod_and_shared():
    """Provides (module, fake_shared) with a fresh import."""
    return _import_module()


@pytest.fixture()
def mod(mod_and_shared):
    return mod_and_shared[0]


@pytest.fixture()
def shared(mod_and_shared):
    return mod_and_shared[1]


@pytest.fixture()
def frozen_utcnow():
    """Patch datetime.datetime.utcnow inside the module to return FIXED_DATE."""
    with patch(f"{SCRIPT_MODULE_NAME}.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FIXED_DATE
        mock_dt.datetime.utcnow.return_value.strftime = FIXED_DATE.strftime
        # Make strftime work on the mock return value
        mock_dt.datetime.utcnow.return_value = FIXED_DATE
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, mod, shared):
        """Claude returns both sections separated by ---GAPS---."""
        shared.get_repo_files.return_value = {"src/main.py": "print('hello')"}
        shared.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content.\n"
            "---GAPS---\n"
            "1. Who are the end users?"
        )

        doc, gaps = mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        assert "# Solution overview: MyProject" in doc
        assert "Some content." in doc
        assert "1. Who are the end users?" in gaps
        # The delimiter itself should not appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_delimiter(self, mod, shared):
        """Claude returns response without the expected delimiter."""
        shared.get_repo_files.return_value = {"src/main.py": "x = 1"}
        shared.call_claude.return_value = "# Solution overview\nNo gap section here."

        doc, gaps = mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        assert "# Solution overview" in doc
        assert gaps == "_Claude could not extract gap questions — review the document manually._"

    def test_calls_get_repo_files_with_correct_extensions(self, mod, shared):
        """Verify that get_repo_files is called with the right file extensions."""
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("myowner", "myrepo", "Proj", "2.0.0", "https://run")

        call_args = shared.get_repo_files.call_args
        assert call_args[0][0] == "myowner"
        assert call_args[0][1] == "myrepo"
        extensions = call_args[0][2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions
        assert call_args[1].get("max_files", call_args[0][3] if len(call_args[0]) > 3 else 20) == 20

    def test_calls_call_claude_with_project_name_in_prompt(self, mod, shared):
        """The prompt passed to Claude must contain the project name."""
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("owner", "repo", "UnderwritingRisk", "1.2.3", "https://run")

        prompt_arg = shared.call_claude.call_args[0][0]
        assert "UnderwritingRisk" in prompt_arg
        assert "1.2.3" in prompt_arg

    def test_file_content_truncated_to_3000_chars(self, mod, shared):
        """Content longer than 3000 chars must be truncated in the prompt."""
        long_content = "x" * 5000
        shared.get_repo_files.return_value = {"big_file.py": long_content}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        user_message = shared.call_claude.call_args[0][1]
        # The truncated content should appear, not the full 5000 chars worth
        assert "x" * 3000 in user_message
        assert "x" * 3001 not in user_message

    def test_multiple_files_joined_in_prompt(self, mod, shared):
        """All returned files must appear in the prompt sent to Claude."""
        shared.get_repo_files.return_value = {
            "README.md": "# Readme",
            "main.py": "import os",
            "infra.tf": 'resource "aws_s3_bucket" {}',
        }
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        user_message = shared.call_claude.call_args[0][1]
        assert "README.md" in user_message
        assert "main.py" in user_message
        assert "infra.tf" in user_message

    def test_multiple_gap_delimiters_only_first_split(self, mod, shared):
        """If ---GAPS--- appears more than once, only split on the first occurrence."""
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = (
            "doc part\n---GAPS---\ngap part\n---GAPS---\nextra"
        )

        doc, gaps = mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert doc == "doc part"
        assert "gap part" in gaps
        assert "extra" in gaps

    def test_empty_repo_files(self, mod, shared):
        """generate_biz_doc should handle an empty file dict gracefully."""
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        doc, gaps = mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert doc == "doc"
        assert gaps == "gap"

    def test_doc_and_gaps_are_stripped(self, mod, shared):
        """Leading/trailing whitespace must be stripped from both outputs."""
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "  \n doc content \n  ---GAPS---  \n  gap content  \n"

        doc, gaps = mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_date_injected_into_prompt(self, mod, shared):
        """The current UTC date must appear in the prompt."""
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        with patch(f"{SCRIPT_MODULE_NAME}.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_DATE
            # We need strftime to work
            mock_date_instance = MagicMock()
            mock_date_instance.strftime.return_value = FIXED_DATE_STR
            mock_dt.datetime.utcnow.return_value = mock_date_instance

            mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        prompt_arg = shared.call_claude.call_args[0][0]
        assert FIXED_DATE_STR in prompt_arg

    def test_synthetic_model_card_project(self, mod, shared):
        """Use synthetic data: insurance underwriting project."""
        shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
            "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "Extract gross annual income"}}',
        }
        shared.call_claude.return_value = (
            "# Solution overview: UnderwritingRisk\n## Executive summary\nThis solves risk.\n"
            "---GAPS---\n"
            "1. Who are the primary underwriters?\n2. What is the go-live date?"
        )

        doc, gaps = mod.generate_biz_doc(
            "insurance-org", "underwriting-repo",
            "UnderwritingRisk", "1.0.0", "https://run"
        )

        assert "Solution overview" in doc
        assert "1. Who are the primary underwriters?" in gaps
        assert "2. What is the go-live date?" in gaps


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    @pytest.fixture()
    def basic_inputs(self):
        return {
            "doc": "# Solution overview: TestProj\nSome content.",
            "gaps": "1. What is the go-live date?\n2. Who owns the budget?",
            "owner": "acme",
            "repo": "my-repo",
            "project_name": "TestProj",
            "version": "1.0.0",
        }

    def test_full_md_contains_doc(self, mod, basic_inputs):
        full_md, _ = mod.build_full_output(**basic_inputs)
        assert basic_inputs["doc"] in full_md

    def test_full_md_contains_gaps(self, mod, basic_inputs):
        full_md, _ = mod.build_full_output(**basic_inputs)
        assert basic_inputs["gaps"] in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, mod, basic_inputs):
        full_md, _ = mod.build_full_output(**basic_inputs)
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, mod, basic_inputs):
        full_md, _ = mod.build_full_output(**basic_inputs)
        assert "acme/my-repo" in full_md
        assert "v1.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, mod, basic_inputs):
        _, gap_only_md = mod.build_full_output(**basic_inputs)
        assert "TestProj" in gap_only_md
        assert "1.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, mod, basic_inputs):
        _, gap_only_md = mod.build_full_output(**basic_inputs)
        assert basic_inputs["gaps"] in gap_only_md

    def test_gap_only_md_references_output_repo(self, mod, basic_inputs):
        _, gap_only_md = mod.build_full_output(**basic_inputs)
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_full_md_contains_ai_delivery_bot_