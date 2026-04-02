"""
Test suite for tool3_business_docs.py

What is tested:
  - generate_biz_doc: happy path, Claude response with/without ---GAPS--- delimiter,
    file assembly, prompt formatting
  - build_full_output: full markdown assembly, gap-only markdown assembly,
    presence of required sections, edge cases (empty doc, empty gaps, whitespace)
  - __main__ block: environment variable handling, success path, exception/failure path

Mocks used:
  - shared.call_claude          — patched to return synthetic Claude responses
  - shared.get_repo_files       — patched to return synthetic file dicts
  - shared.write_output_file    — patched to return a fake URL string
  - shared.send_email           — patched to suppress real email calls
  - shared.email_html           — patched to return a simple HTML stub
  - shared.write_audit_entry    — patched to suppress real audit writes
  - datetime.datetime.utcnow    — patched to return a fixed timestamp
  - os.environ                  — patched via monkeypatch for __main__ tests

TODOs:
  - TODO: Integration test against a real (sandboxed) Claude API response
  - TODO: Test write_output_file path collision / overwrite behaviour
  - TODO: Test behaviour when get_repo_files returns files > 3000 chars (truncation)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 30, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_NOW_STR  = "2024-06-15 12:30 UTC"

FAKE_DOC_PART = """# Solution overview: MyApp
**Version:** 1.2.3 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solves a real problem for real people."""

FAKE_GAPS_PART = """1. What is the target go-live date?
2. Who are the primary end-users?
3. Are there any hard compliance requirements?"""

CLAUDE_RESPONSE_WITH_DELIMITER = f"{FAKE_DOC_PART}\n---GAPS---\n{FAKE_GAPS_PART}"
CLAUDE_RESPONSE_WITHOUT_DELIMITER = FAKE_DOC_PART  # no delimiter

FAKE_FILES = {
    "main.py": "def main(): pass",
    "README.md": "# MyApp\nSome description",
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}',
}


def _make_shared_stub():
    """Return a minimal stub module that can stand in for `shared`."""
    stub = types.ModuleType("shared")
    stub.call_claude        = MagicMock(return_value=CLAUDE_RESPONSE_WITH_DELIMITER)
    stub.get_repo_files     = MagicMock(return_value=FAKE_FILES)
    stub.write_output_file  = MagicMock(return_value="https://github.com/output-repo/file.md")
    stub.send_email         = MagicMock(return_value=None)
    stub.email_html         = MagicMock(return_value="<html>stub</html>")
    stub.write_audit_entry  = MagicMock(return_value=None)
    stub.OUTPUT_REPO_OWNER  = "acme-org"
    stub.OUTPUT_REPO        = "ai-delivery-output"
    return stub


@pytest.fixture()
def shared_stub():
    """Inject a fresh stub `shared` module for every test."""
    stub = _make_shared_stub()
    # Ensure clean module state for each test
    sys.modules["shared"] = stub
    yield stub
    # Remove so next test gets a fresh injection
    sys.modules.pop("shared", None)
    sys.modules.pop("tool3_business_docs", None)


@pytest.fixture()
def biz_docs(shared_stub):
    """Import (or re-import) tool3_business_docs with stubbed shared module."""
    sys.modules.pop("tool3_business_docs", None)
    # Make sure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# generate_biz_doc tests
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    @patch("datetime.datetime")
    def test_happy_path_with_delimiter(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        doc, gaps = biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.2.3", "https://gh.com/run")

        assert doc == FAKE_DOC_PART.strip()
        assert gaps == FAKE_GAPS_PART.strip()

    @patch("datetime.datetime")
    def test_response_without_delimiter_uses_fallback_gaps(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITHOUT_DELIMITER

        doc, gaps = biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.2.3", "https://gh.com/run")

        assert doc  == CLAUDE_RESPONSE_WITHOUT_DELIMITER.strip()
        assert "could not extract" in gaps.lower() or "manually" in gaps.lower()

    @patch("datetime.datetime")
    def test_get_repo_files_called_with_correct_extensions(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

        shared_stub.get_repo_files.assert_called_once()
        call_args = shared_stub.get_repo_files.call_args
        exts = call_args[0][2] if len(call_args[0]) >= 3 else call_args[1].get("extensions", [])
        for ext in [".py", ".md", ".tf"]:
            assert ext in exts

    @patch("datetime.datetime")
    def test_call_claude_receives_project_name_in_prompt(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        biz_docs.generate_biz_doc("acme", "myapp", "SpecialProject", "2.0.0", "https://gh.com/run")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "SpecialProject" in prompt_arg

    @patch("datetime.datetime")
    def test_call_claude_receives_version_in_prompt(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "3.1.4", "https://gh.com/run")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "3.1.4" in prompt_arg

    @patch("datetime.datetime")
    def test_call_claude_user_message_contains_repo_path(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "acme/myapp" in user_msg

    @patch("datetime.datetime")
    def test_files_content_included_in_claude_call(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        shared_stub.get_repo_files.return_value = {"app.py": "x = 1"}

        biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "app.py" in user_msg
        assert "x = 1" in user_msg

    @patch("datetime.datetime")
    def test_empty_repo_files_still_calls_claude(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        shared_stub.get_repo_files.return_value = {}

        doc, gaps = biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

        shared_stub.call_claude.assert_called_once()
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    @patch("datetime.datetime")
    def test_delimiter_split_only_on_first_occurrence(self, mock_dt, biz_docs, shared_stub):
        """If ---GAPS--- appears twice, only split on the first occurrence."""
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        shared_stub.call_claude.return_value = (
            "DOC CONTENT\n---GAPS---\nQUESTIONS\n---GAPS---\nEXTRA"
        )

        doc, gaps = biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

        assert doc.strip()  == "DOC CONTENT"
        assert "QUESTIONS" in gaps
        assert "EXTRA"     in gaps  # everything after first split is in gaps

    @patch("datetime.datetime")
    def test_doc_and_gaps_are_stripped(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        shared_stub.call_claude.return_value = "  DOC  \n---GAPS---\n  GAPS  "

        doc, gaps = biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

        assert doc  == "DOC"
        assert gaps == "GAPS"

    @patch("datetime.datetime")
    def test_call_claude_propagates_exception(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        shared_stub.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

    @patch("datetime.datetime")
    def test_get_repo_files_max_files_is_20(self, mock_dt, biz_docs, shared_stub):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        biz_docs.generate_biz_doc("acme", "myapp", "MyApp", "1.0.0", "https://gh.com/run")

        call_kwargs = shared_stub.get_repo_files.call_args
        # max_files can be positional or keyword
        args   = call_kwargs[0]
        kwargs = call_kwargs[1]
        max_files = kwargs.get("max_files") if "max_files" in kwargs else (args[3] if len(args) > 3 else None)
        assert max_files == 20


# ---------------------------------------------------------------------------
# build_full_output tests
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    @patch("datetime.datetime")
    def test_full_md_contains_doc_content(self, mock_dt, biz_docs):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        full_md, _ = biz_docs.build_full_output(
            FAKE_DOC_PART, FAKE_GAPS_PART, "acme", "myapp", "MyApp", "1.2.3"
        )
        assert FAKE_DOC_PART in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_gaps_content(self, mock_dt, biz_docs):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        full_md, _ = biz_docs.build_full_output(
            FAKE_DOC_PART, FAKE_GAPS_PART, "acme", "myapp", "MyApp", "1.2.3"
        )
        assert FAKE_GAPS_PART in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_gap_questionnaire_heading(self, mock_dt, biz_docs):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        full_md, _ = biz_docs.build_full_output(
            FAKE_DOC_PART, FAKE_GAPS_PART, "acme", "myapp", "MyApp", "1.2.3"
        )
        assert "Gap Questionnaire" in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_attribution_footer(self, mock_dt, biz_docs):
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        full_md, _ = biz_docs.build_full_output(
            FAKE_DOC_PART, FAKE_GAPS_PART, "ac