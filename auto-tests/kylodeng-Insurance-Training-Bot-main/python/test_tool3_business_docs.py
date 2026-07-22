"""
Test module for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, missing ---GAPS--- delimiter, Claude errors
- build_full_output(): full markdown assembly, gap-only markdown assembly, content integrity
- __main__ block logic (via subprocess or direct invocation patterns)
- Boundary values: empty strings, very long content, missing env vars

Mocks used:
- shared.call_claude (prevents real API calls)
- shared.get_repo_files (prevents real GitHub API calls)
- shared.write_output_file (prevents real file writes)
- shared.send_email (prevents real email sending)
- shared.email_html (prevents template rendering side-effects)
- shared.write_audit_entry (prevents real audit log writes)
- datetime.datetime.utcnow (for deterministic timestamps)

TODOs:
- TODO: Test actual __main__ entry-point execution end-to-end via subprocess once CI env vars are stable
- TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO env vars are missing (needs shared module constants exposed)
- TODO: Test gap_count calculation for multi-line gaps edge cases once format is finalised
"""

import sys
import os
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Path setup – mirror what the source file does
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# ---------------------------------------------------------------------------
# We import AFTER patching shared so that import-time side effects are safe.
# We patch 'shared' as a module in sys.modules before importing the target.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"

# Build a fake shared module
shared_mock = MagicMock()
shared_mock.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
shared_mock.OUTPUT_REPO = FAKE_OUTPUT_REPO

with patch.dict(sys.modules, {"shared": shared_mock}):
    import importlib
    import tool3_business_docs as biz_docs  # noqa: E402  (import inside patch context)


# ---------------------------------------------------------------------------
# Constants / fixtures
# ---------------------------------------------------------------------------

FAKE_NOW_DATE = "2024-01-15"
FAKE_NOW_DATETIME = "2024-01-15 10:30 UTC"

SAMPLE_DOC = "# Solution overview: MyProject\n\n## Executive summary\nThis solves a problem."
SAMPLE_GAPS = "1. What is the target go-live date?\n2. Who is the business sponsor?"
SAMPLE_RAW_WITH_GAPS = f"{SAMPLE_DOC}\n---GAPS---\n{SAMPLE_GAPS}"
SAMPLE_RAW_WITHOUT_GAPS = f"{SAMPLE_DOC}\n\nSome additional content without delimiter."

SAMPLE_FILES = {
    "main.py": "def main(): pass",
    "config.yaml": "env: production",
    "README.md": "# MyProject\n\nA great project.",
}

OWNER = "acme-org"
REPO = "my-service"
PROJECT_NAME = "My Service"
VERSION = "1.2.3"
RUN_URL = "https://github.com/actions/runs/99999"


@pytest.fixture(autouse=True)
def reset_shared_mock():
    """Reset all shared mock calls between tests."""
    shared_mock.reset_mock()
    # Re-apply constants that reset_mock clears
    shared_mock.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared_mock.OUTPUT_REPO = FAKE_OUTPUT_REPO
    yield


@pytest.fixture()
def frozen_datetime_date():
    """Patch datetime inside biz_docs to return deterministic values."""
    fake_dt = MagicMock()
    fake_dt.utcnow.return_value.strftime.side_effect = lambda fmt: (
        FAKE_NOW_DATE if fmt == "%Y-%m-%d" else FAKE_NOW_DATETIME
    )
    with patch.object(biz_docs.datetime, "datetime", fake_dt):
        yield fake_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:
    """Tests for the generate_biz_doc function."""

    def _setup_mocks(self, raw_response=SAMPLE_RAW_WITH_GAPS, files=None):
        shared_mock.get_repo_files.return_value = files or SAMPLE_FILES
        shared_mock.call_claude.return_value = raw_response

    def test_happy_path_splits_on_delimiter(self, frozen_datetime_date):
        """generate_biz_doc returns (doc_part, gaps_part) when delimiter present."""
        self._setup_mocks(raw_response=SAMPLE_RAW_WITH_GAPS)

        doc, gaps = biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == SAMPLE_DOC.strip()
        assert gaps == SAMPLE_GAPS.strip()

    def test_calls_get_repo_files_with_correct_extensions(self, frozen_datetime_date):
        """get_repo_files is called with expected file extensions and max_files."""
        self._setup_mocks()

        biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        shared_mock.get_repo_files.assert_called_once_with(
            OWNER,
            REPO,
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_calls_call_claude_with_formatted_prompt(self, frozen_datetime_date):
        """call_claude receives a prompt containing the project name, version, and date."""
        self._setup_mocks()

        biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert shared_mock.call_claude.called
        prompt_arg = shared_mock.call_claude.call_args[0][0]
        assert PROJECT_NAME in prompt_arg
        assert VERSION in prompt_arg
        assert FAKE_NOW_DATE in prompt_arg

    def test_calls_call_claude_with_repo_context_in_user_message(self, frozen_datetime_date):
        """call_claude user message contains repo owner/name and file contents."""
        self._setup_mocks()

        biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_msg_arg = shared_mock.call_claude.call_args[0][1]
        assert f"{OWNER}/{REPO}" in user_msg_arg
        assert "main.py" in user_msg_arg

    def test_missing_delimiter_returns_fallback_gaps(self, frozen_datetime_date):
        """When Claude response lacks ---GAPS--- delimiter, gaps_part is a fallback message."""
        self._setup_mocks(raw_response=SAMPLE_RAW_WITHOUT_GAPS)

        doc, gaps = biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == SAMPLE_RAW_WITHOUT_GAPS.strip()
        assert "Claude could not extract gap questions" in gaps

    def test_delimiter_splits_only_on_first_occurrence(self, frozen_datetime_date):
        """Only the first ---GAPS--- delimiter is used for splitting."""
        raw = f"Doc part\n---GAPS---\nFirst gaps\n---GAPS---\nExtra content"
        self._setup_mocks(raw_response=raw)

        doc, gaps = biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == "Doc part"
        assert "First gaps" in gaps
        assert "Extra content" in gaps  # second occurrence is part of gaps_part

    def test_empty_files_dict_produces_empty_files_str(self, frozen_datetime_date):
        """Empty repo files still calls Claude (with empty file section)."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc is not None
        user_msg_arg = shared_mock.call_claude.call_args[0][1]
        # files section should be essentially empty
        assert "Files:\n" in user_msg_arg

    def test_file_content_truncated_to_3000_chars(self, frozen_datetime_date):
        """File contents longer than 3000 chars are truncated in the prompt."""
        long_content = "x" * 5000
        shared_mock.get_repo_files.return_value = {"bigfile.py": long_content}
        shared_mock.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_msg_arg = shared_mock.call_claude.call_args[0][1]
        # The truncated string should appear (3000 x's), not 5000
        assert "x" * 3000 in user_msg_arg
        assert "x" * 3001 not in user_msg_arg

    def test_claude_raises_exception_propagates(self, frozen_datetime_date):
        """Exceptions from call_claude propagate to the caller."""
        shared_mock.get_repo_files.return_value = SAMPLE_FILES
        shared_mock.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    def test_get_repo_files_raises_exception_propagates(self, frozen_datetime_date):
        """Exceptions from get_repo_files propagate to the caller."""
        shared_mock.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    def test_delimiter_with_surrounding_whitespace_handled(self, frozen_datetime_date):
        """doc and gaps are stripped of surrounding whitespace."""
        raw = f"  {SAMPLE_DOC}  \n---GAPS---\n  {SAMPLE_GAPS}  "
        self._setup_mocks(raw_response=raw)

        doc, gaps = biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert not doc.startswith(" ")
        assert not doc.endswith(" ")
        assert not gaps.startswith(" ")
        assert not gaps.endswith(" ")

    def test_response_with_only_delimiter_gives_empty_parts(self, frozen_datetime_date):
        """Edge case: response is just the delimiter."""
        self._setup_mocks(raw_response="---GAPS---")

        doc, gaps = biz_docs.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == ""
        assert gaps == ""

    @pytest.mark.parametrize("project_name,version", [
        ("MyProject", "1.0.0"),
        ("insurance-tool", "2.5.0-rc1"),
        ("Generations II", "0.1.0"),
        ("Global Network Hospital List", "3.0.0"),
    ])
    def test_various_project_names_and_versions_in_prompt(self, project_name, version, frozen_datetime_date):
        """Parameterised: project_name and version are always embedded in the Claude prompt."""
        shared_mock.get_repo_files.return_value = SAMPLE_FILES
        shared_mock.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        biz_docs.generate_biz_doc(OWNER, REPO, project_name, version, RUN_URL)

        prompt_arg = shared_mock.call_claude.call_args[0][0]
        assert project_name in prompt_arg
        assert version in prompt_arg


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================


class TestBuildFullOutput:
    """Tests for the build_full_output function."""

    def test_returns_tuple_of_two_strings(self, frozen_datetime_date):
        full_md, gap_only_md = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, frozen_datetime_date):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert SAMPLE_DOC in full_md

    def test_full_md_contains_gaps_content(self, frozen_datetime_date):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert SAMPLE_GAPS in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, frozen_datetime_date):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, frozen_datetime_date):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert f"{OWNER}/{REPO}" in full_md
        assert VERSION in full_md
        assert "AI Delivery Bot" in full_md

    def test_full_md_contains_timestamp(self, frozen_datetime_date):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        # The strftime with "%Y-%m-%d %H:%M UTC" format
        assert "UTC" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, frozen_datetime_date):
        _, gap_only_md = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert PROJECT_NAME in gap_only_md
        assert VERSION in gap_only_md

    def test_gap_only_md_contains_gaps_content(self, frozen_datetime_date):
        _, gap_only_md = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert SAMPLE_GAPS in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, frozen_datetime_date):
        _, gap_only_md = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_has_heading(self