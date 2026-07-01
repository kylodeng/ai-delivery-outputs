"""
Test suite for .github/scripts/tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude error propagation
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content correctness
    - __main__ block behaviour: env-var reading, file writes, email, audit entry, exception handling

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (fixed timestamp)

TODOs:
    - TODO: Integration test against a real Claude response shape once API contract is stable
    - TODO: Test __main__ block fully via subprocess when shared module is importable in CI
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Ensure the scripts directory is on the path and stub the 'shared' module
# before importing the module under test.
# ---------------------------------------------------------------------------

SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")

# Build a minimal fake 'shared' module so we never import the real one
_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude = MagicMock()
_shared_stub.get_repo_files = MagicMock()
_shared_stub.write_output_file = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock()
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"

sys.modules.setdefault("shared", _shared_stub)

# Now we can safely import the module under test
sys.path.insert(0, SCRIPTS_DIR)

import tool3_business_docs as biz  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"

SAMPLE_DOC = """# Solution overview: MyProject
**Version:** 1.2.3 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates underwriting risk classification for insurance teams.

## Business context
**Problem statement:** Manual underwriting is slow and error-prone.
"""

SAMPLE_GAPS = """1. Who is the primary business sponsor?
2. What is the target go-live date?
3. Which data retention policy applies to customer records?"""

SAMPLE_RAW_WITH_DELIMITER = f"{SAMPLE_DOC}\n---GAPS---\n{SAMPLE_GAPS}"
SAMPLE_RAW_WITHOUT_DELIMITER = SAMPLE_DOC  # no gaps section

SAMPLE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent"}}',
    "frontend/.chainlit/translations/ar-SA.json": '{"common": {"actions": {"cancel": "إلغاء"}}}',
}

OWNER = "acme-corp"
REPO = "underwriting-engine"
PROJECT = "Underwriting Risk Classification"
VERSION = "1.2.3"
RUN_URL = "https://github.com/acme-corp/underwriting-engine/actions/runs/99"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mocks between tests."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.email_html.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


@pytest.fixture
def fixed_utcnow():
    """Patch datetime.datetime.utcnow to return a fixed value."""
    with patch("tool3_business_docs.datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        # Make strftime work on the returned mock
        mock_dt.utcnow.side_effect = lambda: FIXED_DT
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self):
        """Claude returns response containing ---GAPS--- delimiter."""
        _shared_stub.get_repo_files.return_value = SAMPLE_FILES
        _shared_stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        doc, gaps = biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "Executive summary" in doc
        assert "1. Who is the primary business sponsor?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_gaps_delimiter(self):
        """Claude returns response with no ---GAPS--- delimiter — fallback message used."""
        _shared_stub.get_repo_files.return_value = SAMPLE_FILES
        _shared_stub.call_claude.return_value = SAMPLE_RAW_WITHOUT_DELIMITER

        doc, gaps = biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert gaps == "_Claude could not extract gap questions — review the document manually._"

    def test_calls_get_repo_files_with_correct_extensions(self):
        """get_repo_files must be called with the expected file extensions and limit."""
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "doc---GAPS---gaps"

        biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        _shared_stub.get_repo_files.assert_called_once_with(
            OWNER, REPO,
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_calls_call_claude_with_project_name_in_prompt(self):
        """The system prompt must be formatted with the project name."""
        _shared_stub.get_repo_files.return_value = SAMPLE_FILES
        _shared_stub.call_claude.return_value = "---GAPS---"

        biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert PROJECT in prompt_arg

    def test_calls_call_claude_with_version_in_prompt(self):
        """The system prompt must be formatted with the version string."""
        _shared_stub.get_repo_files.return_value = SAMPLE_FILES
        _shared_stub.call_claude.return_value = "---GAPS---"

        biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert VERSION in prompt_arg

    def test_user_message_contains_repo_path(self):
        """The user message passed to call_claude must identify the repo."""
        _shared_stub.get_repo_files.return_value = SAMPLE_FILES
        _shared_stub.call_claude.return_value = "---GAPS---"

        biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        user_msg = _shared_stub.call_claude.call_args[0][1]
        assert f"{OWNER}/{REPO}" in user_msg

    def test_file_content_truncated_to_3000_chars(self):
        """Files longer than 3000 chars must be truncated in the prompt."""
        long_content = "x" * 5000
        _shared_stub.get_repo_files.return_value = {"bigfile.py": long_content}
        _shared_stub.call_claude.return_value = "---GAPS---"

        biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        user_msg = _shared_stub.call_claude.call_args[0][1]
        # The snippet in the message must be at most 3000 x's
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_empty_repo_files(self):
        """Empty file dict should still call Claude with an empty files block."""
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        doc, gaps = biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert doc  # something returned
        _shared_stub.call_claude.assert_called_once()

    def test_multiple_gaps_delimiters_only_splits_on_first(self):
        """If ---GAPS--- appears more than once, split only on the first occurrence."""
        raw = "doc part---GAPS---gaps part---GAPS---extra"
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = raw

        doc, gaps = biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert doc == "doc part"
        assert "gaps part---GAPS---extra" in gaps

    def test_claude_raises_exception_propagates(self):
        """Exceptions from call_claude should propagate out of generate_biz_doc."""
        _shared_stub.get_repo_files.return_value = SAMPLE_FILES
        _shared_stub.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

    def test_get_repo_files_raises_exception_propagates(self):
        """Exceptions from get_repo_files should propagate out."""
        _shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

    def test_doc_and_gaps_are_stripped(self):
        """Leading/trailing whitespace must be stripped from both parts."""
        raw = "   \n  " + SAMPLE_DOC + "   \n---GAPS---\n  " + SAMPLE_GAPS + "   \n"
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = raw

        doc, gaps = biz.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    @pytest.mark.parametrize("project_name,version", [
        ("MyProject", "0.0.1"),
        ("Underwriting Risk Classification", "1.2.3"),
        ("A B C Special-Chars_Project", "99.0.0-rc.1"),
        ("", "0.1.0"),  # empty project name edge case
    ])
    def test_various_project_names_and_versions(self, project_name, version):
        """generate_biz_doc should not crash for varied project_name / version strings."""
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = f"doc {project_name}---GAPS---gap {version}"

        doc, gaps = biz.generate_biz_doc(OWNER, REPO, project_name, version, RUN_URL)

        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, doc=SAMPLE_DOC, gaps=SAMPLE_GAPS):
        return biz.build_full_output(doc, gaps, OWNER, REPO, PROJECT, VERSION)

    def test_returns_tuple_of_two_strings(self):
        full_md, gap_only_md = self._call()
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self):
        full_md, _ = self._call()
        assert "Solution overview" in full_md
        assert "Executive summary" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self):
        full_md, _ = self._call()
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self):
        full_md, _ = self._call()
        assert "Who is the primary business sponsor?" in full_md

    def test_full_md_contains_source_attribution(self):
        full_md, _ = self._call()
        assert f"{OWNER}/{REPO}" in full_md
        assert VERSION in full_md

    def test_gap_only_md_contains_project_name_and_version(self):
        _, gap_only_md = self._call()
        assert PROJECT in gap_only_md
        assert VERSION in gap_only_md

    def test_gap_only_md_contains_gaps_content(self):
        _, gap_only_md = self._call()
        assert "Who is the primary business sponsor?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self):
        _, gap_only_md = self._call()
        assert _shared_stub.OUTPUT_REPO_OWNER in gap_only_md
        assert _shared_stub.OUTPUT_REPO in gap_only_md

    def test_full_md_mentions_ai_delivery_bot(self):
        full_md, _ = self._call()
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_estimated_time(self):
        """The gap-only template should mention estimated completion time."""
        _, gap_only_md = self._call()
        assert "10-15 minutes" in gap_only_md

    def test_empty_gaps_string(self):
        """Empty gaps string should not cause a crash."""
        full_md, gap_only_md = self._call(gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_empty_doc_string(self):
        """Empty doc string should not cause a crash."""
        full_md, gap_only_md = self._call(doc="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_timestamp_format_in_full_md(self):
        """Timestamp in full output should follow