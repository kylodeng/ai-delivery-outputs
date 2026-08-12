"""
Test module for .github/scripts/tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path with/without ---GAPS--- delimiter, Claude error propagation
    - build_full_output(): correct markdown assembly, gap-only doc, metadata injection
    - __main__ block: env-var wiring, success path, failure/exception path
    - Edge cases: empty gaps, missing delimiter, whitespace handling, gap count calculation

Mocks used:
    - shared.call_claude            — prevents real Anthropic API calls
    - shared.get_repo_files         — prevents real GitHub API calls
    - shared.write_output_file      — prevents real git pushes
    - shared.send_email             — prevents real SES/SMTP calls
    - shared.email_html             — prevents template rendering side-effects
    - shared.write_audit_entry      — prevents real audit writes
    - datetime.datetime.utcnow      — frozen for deterministic output

TODOs:
    - TODO: Integration test against a real (sandboxed) Claude endpoint once credentials available
    - TODO: Test truncation behaviour when a single file exceeds 3 000 chars (needs >3 000 char fixture)
    - TODO: Test write_output_file path-construction when owner/repo contain special characters
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch, call
import datetime

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal 'shared' stub so the import succeeds without the
# real module being on sys.path.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude        = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    stub.get_repo_files     = MagicMock(return_value={"README.md": "# Hello"})
    stub.write_output_file  = MagicMock(return_value="https://github.com/output/file")
    stub.send_email         = MagicMock()
    stub.email_html         = MagicMock(return_value="<html>ok</html>")
    stub.write_audit_entry  = MagicMock()
    stub.OUTPUT_REPO_OWNER  = "test-owner"
    stub.OUTPUT_REPO        = "test-repo"
    return stub


# Insert stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test (must come after stub registration)
import importlib.util, pathlib

_MODULE_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool3_business_docs.py"


def _load_module(shared_stub=None):
    """Re-load the module so each test can inject its own stub."""
    stub = shared_stub or _make_shared_stub()
    sys.modules["shared"] = stub

    spec = importlib.util.spec_from_file_location("tool3_business_docs", _MODULE_PATH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FROZEN_DATE     = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_TS_STR   = "2024-06-15 12:00 UTC"

OWNER        = "acme-corp"
REPO         = "underwriting-risk"
PROJECT_NAME = "Underwriting Risk Classification"
VERSION      = "1.2.3"
RUN_URL      = "https://github.com/acme-corp/underwriting-risk/actions/runs/99"

SAMPLE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent"}}',
}

CLAUDE_RESPONSE_WITH_GAPS = (
    "# Solution overview: Underwriting Risk Classification\n\n"
    "Some executive summary text.\n"
    "---GAPS---\n"
    "1. What is the target go-live date?\n"
    "2. Who is the business sponsor?\n"
    "3. What SLA applies to the classification service?"
)

CLAUDE_RESPONSE_NO_GAPS = (
    "# Solution overview: Underwriting Risk Classification\n\n"
    "Some executive summary text — no delimiter present."
)


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    @patch("datetime.datetime")
    def test_happy_path_with_gaps_delimiter(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value    = CLAUDE_RESPONSE_WITH_GAPS

        doc, gaps = mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "What is the target go-live date?" in gaps
        assert "Who is the business sponsor?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    @patch("datetime.datetime")
    def test_no_gaps_delimiter_falls_back_gracefully(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value    = CLAUDE_RESPONSE_NO_GAPS

        doc, gaps = mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "Claude could not extract" in gaps

    @patch("datetime.datetime")
    def test_get_repo_files_called_with_correct_extensions(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value    = CLAUDE_RESPONSE_WITH_GAPS

        mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        called_exts = stub.get_repo_files.call_args[0][2]
        assert ".py"    in called_exts
        assert ".tf"    in called_exts
        assert ".yaml"  in called_exts
        assert ".md"    in called_exts

    @patch("datetime.datetime")
    def test_get_repo_files_max_files_is_20(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value    = CLAUDE_RESPONSE_WITH_GAPS

        mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        kwargs = stub.get_repo_files.call_args[1]
        assert kwargs.get("max_files", None) == 20 or stub.get_repo_files.call_args[0][3] == 20

    @patch("datetime.datetime")
    def test_prompt_contains_project_name_version_date(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE
        mock_dt.utcnow.return_value.strftime = lambda fmt: (
            FROZEN_DATE.strftime(fmt)
        )

        mod, stub = _load_module()
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value    = CLAUDE_RESPONSE_WITH_GAPS

        mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        prompt_arg = stub.call_claude.call_args[0][0]
        assert PROJECT_NAME in prompt_arg
        assert VERSION      in prompt_arg
        assert FROZEN_DATE_STR in prompt_arg

    @patch("datetime.datetime")
    def test_user_content_contains_owner_repo(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value    = CLAUDE_RESPONSE_WITH_GAPS

        mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_content = stub.call_claude.call_args[0][1]
        assert f"{OWNER}/{REPO}" in user_content

    @patch("datetime.datetime")
    def test_file_content_truncated_to_3000_chars(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        long_content = "x" * 5000
        mod, stub    = _load_module()
        stub.get_repo_files.return_value = {"big_file.py": long_content}
        stub.call_claude.return_value    = CLAUDE_RESPONSE_WITH_GAPS

        mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_content = stub.call_claude.call_args[0][1]
        # The truncated slice should appear; 5000 x's should NOT appear in full
        assert "x" * 3000 in user_content
        assert "x" * 3001 not in user_content

    @patch("datetime.datetime")
    def test_claude_exception_propagates(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.side_effect     = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    @patch("datetime.datetime")
    def test_get_repo_files_exception_propagates(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    @patch("datetime.datetime")
    def test_gaps_are_stripped(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        raw = "  doc body  \n---GAPS---\n  gaps body  \n"
        mod, stub = _load_module()
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value    = raw

        doc, gaps = mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc  == "doc body"
        assert gaps == "gaps body"

    @patch("datetime.datetime")
    def test_multiple_gap_delimiters_only_first_split(self, mock_dt):
        """Only the first ---GAPS--- should act as delimiter."""
        mock_dt.utcnow.return_value = FROZEN_DATE

        raw = "doc\n---GAPS---\nq1\n---GAPS---\nq2"
        mod, stub = _load_module()
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value    = raw

        doc, gaps = mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc  == "doc"
        assert "q1" in gaps
        assert "q2" in gaps   # second delimiter remains in gaps section

    @patch("datetime.datetime")
    def test_empty_repo_files(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, stub = _load_module()
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value    = CLAUDE_RESPONSE_WITH_GAPS

        doc, gaps = mod.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc
        assert gaps


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    DOC  = "# Solution overview\n\nSome content."
    GAPS = "1. What is the go-live date?\n2. Who owns the budget?"

    @patch("datetime.datetime")
    def test_full_md_contains_doc_section(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, _ = _load_module()
        full_md, _ = mod.build_full_output(
            self.DOC, self.GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )

        assert "# Solution overview" in full_md
        assert "Some content." in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_gaps_section(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, _ = _load_module()
        full_md, _ = mod.build_full_output(
            self.DOC, self.GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )

        assert "Gap Questionnaire" in full_md
        assert "What is the go-live date?" in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_source_metadata(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, _ = _load_module()
        full_md, _ = mod.build_full_output(
            self.DOC, self.GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )

        assert OWNER   in full_md
        assert REPO    in full_md
        assert VERSION in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_timestamp(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, _ = _load_module()
        full_md, _ = mod.build_full_output(
            self.DOC, self.GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )

        assert FROZEN_TS_STR in full_md

    @patch("datetime.datetime")
    def test_gap_only_md_contains_project_and_version(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, _ = _load_module()
        _, gap_only_md = mod.build_full_output(
            self.DOC, self.GAPS, OWNER, REPO, PROJECT_NAME, VERSION
        )

        assert PROJECT_NAME in gap_only_md
        assert VERSION      in gap_only_md

    @patch("datetime.datetime")
    def test_gap_only_md_contains_gap_questions(self, mock_dt):
        mock_dt.utcnow.return_value = FROZEN_DATE

        mod, _ = _load_module()
        _, gap_only_md =