"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), error propagation
    - build_full_output(): structure, content inclusion, edge cases (empty gaps, long strings)
    - __main__ block behaviour via subprocess / monkeypatching (env vars, success, failure)

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch

TODOs:
    - TODO: Integration test that validates full Claude response parsing with a real prompt
    - TODO: Test __main__ block failure branch once source file is syntactically complete
            (the source has a truncated string literal at the end)
    - TODO: Validate email HTML content structure against a schema/template
"""

import importlib
import sys
import os
import datetime
import types
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with its `shared` dependency mocked
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_mock():
    """Return a MagicMock that stands in for the `shared` module."""
    shared = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module(shared_mock):
    """
    Import tool3_business_docs with a stubbed `shared` module.
    Re-imports each time so patches are isolated.
    """
    # Inject our fake `shared` before the real import resolves it
    sys.modules["shared"] = shared_mock

    module_path = os.path.join(
        os.path.dirname(__file__),
        ".github",
        "scripts",
        "tool3_business_docs.py",
    )

    spec = importlib.util.spec_from_file_location("tool3_business_docs", module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def shared_mock():
    mock = _make_shared_mock()
    yield mock
    # Clean up sys.modules so other tests get a fresh import
    sys.modules.pop("shared", None)
    sys.modules.pop("tool3_business_docs", None)


@pytest.fixture()
def module(shared_mock):
    return _import_module(shared_mock)


@pytest.fixture()
def frozen_utcnow(monkeypatch, module):
    """Freeze datetime.datetime.utcnow inside the module under test."""
    fake_dt = MagicMock(wraps=datetime.datetime)
    fake_dt.utcnow.return_value = FIXED_DATE
    monkeypatch.setattr(module.datetime, "datetime", fake_dt)
    return fake_dt


# ---------------------------------------------------------------------------
# Sample repo files (drawn from synthetic data context)
# ---------------------------------------------------------------------------

SAMPLE_FILES = {
    "README.md": "# Generations II Insurance platform\nProvides lifelong protection.",
    "main.py": "import boto3\n\ndef handler(event, context):\n    return {'statusCode': 200}",
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" { bucket = "insurance-docs" }',
}

SAMPLE_DOC = """# Solution overview: MyProject

**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This tool automates business documentation.

## What it does NOT do (out of scope)
- No manual editing
- No real-time updates
- No multi-language support
"""

SAMPLE_GAPS = """1. Who is the business sponsor?
2. What is the target go-live date?
3. What SLA is expected for document generation?
"""

SAMPLE_RAW_WITH_GAPS = f"{SAMPLE_DOC}\n---GAPS---\n{SAMPLE_GAPS}"
SAMPLE_RAW_WITHOUT_GAPS = SAMPLE_DOC  # no delimiter


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:
    """Tests for the generate_biz_doc() function."""

    def test_happy_path_with_gaps_delimiter(self, module, shared_mock, frozen_utcnow):
        """Claude returns well-formed output with ---GAPS--- delimiter."""
        shared_mock.get_repo_files.return_value = SAMPLE_FILES
        shared_mock.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = module.generate_biz_doc(
            "acme", "platform", "MyProject", "1.0.0", "https://github.com/run/1"
        )

        assert "Solution overview" in doc
        assert "Who is the business sponsor" in gaps
        # Ensure the delimiter itself is NOT present in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_gaps_delimiter(self, module, shared_mock, frozen_utcnow):
        """Claude returns output without ---GAPS--- delimiter → fallback message."""
        shared_mock.get_repo_files.return_value = SAMPLE_FILES
        shared_mock.call_claude.return_value = SAMPLE_RAW_WITHOUT_GAPS

        doc, gaps = module.generate_biz_doc(
            "acme", "platform", "MyProject", "1.0.0", "https://github.com/run/1"
        )

        assert "Solution overview" in doc
        assert "review the document manually" in gaps.lower() or "could not extract" in gaps.lower()

    def test_get_repo_files_called_with_correct_extensions(self, module, shared_mock, frozen_utcnow):
        """get_repo_files is called with expected file extensions and max_files."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = "no gaps here"

        module.generate_biz_doc("owner", "repo", "Proj", "0.1.0", "")

        shared_mock.get_repo_files.assert_called_once()
        args, kwargs = shared_mock.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_formatted_prompt(self, module, shared_mock, frozen_utcnow):
        """call_claude is invoked with the project_name and version inserted."""
        shared_mock.get_repo_files.return_value = SAMPLE_FILES
        shared_mock.call_claude.return_value = "doc---GAPS---gaps"

        module.generate_biz_doc("owner", "repo", "InsuranceBot", "2.3.0", "")

        args, _ = shared_mock.call_claude.call_args
        prompt_arg = args[0]
        assert "InsuranceBot" in prompt_arg
        assert "2.3.0" in prompt_arg
        assert FIXED_DATE_STR in prompt_arg

    def test_files_content_passed_to_claude(self, module, shared_mock, frozen_utcnow):
        """File contents appear in the user message sent to Claude."""
        shared_mock.get_repo_files.return_value = SAMPLE_FILES
        shared_mock.call_claude.return_value = "doc---GAPS---gaps"

        module.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "")

        _, user_msg = shared_mock.call_claude.call_args[0]
        assert "README.md" in user_msg
        assert "Generations II" in user_msg

    def test_empty_repo_files(self, module, shared_mock, frozen_utcnow):
        """Empty repo dict still results in a call to Claude."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = module.generate_biz_doc("o", "r", "P", "0.0.1", "")

        shared_mock.call_claude.assert_called_once()
        assert doc  # not empty
        assert gaps

    def test_multiple_gaps_delimiters_splits_on_first(self, module, shared_mock, frozen_utcnow):
        """Only the first ---GAPS--- delimiter is used as the split point."""
        raw = "DOC_PART---GAPS---FIRST_GAPS---GAPS---SECOND_GAPS"
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = raw

        doc, gaps = module.generate_biz_doc("o", "r", "P", "1.0.0", "")

        assert doc.strip() == "DOC_PART"
        assert "FIRST_GAPS" in gaps
        assert "SECOND_GAPS" in gaps  # remainder stays in gaps section

    def test_call_claude_exception_propagates(self, module, shared_mock):
        """Exceptions raised by call_claude are not swallowed."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            module.generate_biz_doc("o", "r", "P", "1.0.0", "")

    def test_get_repo_files_exception_propagates(self, module, shared_mock):
        """Exceptions from get_repo_files propagate to the caller."""
        shared_mock.get_repo_files.side_effect = ConnectionError("GitHub timeout")

        with pytest.raises(ConnectionError, match="GitHub timeout"):
            module.generate_biz_doc("o", "r", "P", "1.0.0", "")

    def test_large_file_content_truncated_in_prompt(self, module, shared_mock, frozen_utcnow):
        """Files with >3000 chars are sliced to 3000 chars in the prompt."""
        long_content = "x" * 5000
        shared_mock.get_repo_files.return_value = {"big.py": long_content}
        shared_mock.call_claude.return_value = "doc---GAPS---gaps"

        module.generate_biz_doc("o", "r", "P", "1.0.0", "")

        _, user_msg = shared_mock.call_claude.call_args[0]
        # The truncated block should contain exactly 3000 x's, not 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    @pytest.mark.parametrize("owner,repo,project_name,version", [
        ("sun-life", "generations-ii", "Generations II", "2.0.0"),
        ("acme", "hospital-list", "Designated Hospitals", "1.5.3"),
        ("org", "cashless", "Cashless Network", "0.9.1"),
    ])
    def test_parametrised_inputs(
        self, module, shared_mock, frozen_utcnow,
        owner, repo, project_name, version
    ):
        """generate_biz_doc works for various owner/repo/project/version combos."""
        shared_mock.get_repo_files.return_value = SAMPLE_FILES
        shared_mock.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = module.generate_biz_doc(owner, repo, project_name, version, "")

        assert isinstance(doc, str) and doc
        assert isinstance(gaps, str) and gaps


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================


class TestBuildFullOutput:
    """Tests for the build_full_output() function."""

    def test_full_md_contains_doc(self, module, frozen_utcnow):
        full_md, _ = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert "Solution overview" in full_md

    def test_full_md_contains_gaps(self, module, frozen_utcnow):
        full_md, _ = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert "Who is the business sponsor" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, module, frozen_utcnow):
        full_md, _ = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, module, frozen_utcnow):
        full_md, _ = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert "acme/platform" in full_md
        assert "1.0.0" in full_md

    def test_gap_only_md_contains_project_name(self, module, frozen_utcnow):
        _, gap_only_md = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert "MyProject" in gap_only_md

    def test_gap_only_md_contains_version(self, module, frozen_utcnow):
        _, gap_only_md = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert "1.0.0" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, module, frozen_utcnow):
        _, gap_only_md = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_contains_gaps_content(self, module, frozen_utcnow):
        _, gap_only_md = module.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "platform", "MyProject", "1.0.0"
        )
        assert "Who is the business sponsor" in gap_only_md

    def test_frozen_timestamp_appears_in_full_md(self, module, frozen