"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), edge cases
    - build_full_output(): full markdown assembly, standalone gap questionnaire
    - __main__ block behaviour via subprocess / monkeypatching environment variables
    - Correct delegation to shared-module helpers (call_claude, get_repo_files,
      write_output_file, send_email, email_html, write_audit_entry)

Mocks used:
    - shared.call_claude          — stubbed to return controlled strings
    - shared.get_repo_files       — stubbed to return a small dict of fake files
    - shared.write_output_file    — stubbed to return a fake URL
    - shared.send_email           — stubbed (no-op)
    - shared.email_html           — stubbed to return a plain HTML string
    - shared.write_audit_entry    — stubbed (no-op)
    - datetime.datetime.utcnow    — frozen to a known timestamp

TODOs:
    # TODO: Integration test that actually invokes the __main__ block end-to-end
            requires a real (or locally running) Claude-compatible API endpoint.
    # TODO: Test behaviour when get_repo_files returns very large file content
            (>3000 chars) to confirm truncation logic in the caller's slice.
    # TODO: Test concurrent / re-entrant calls to generate_biz_doc once
            thread-safety guarantees for shared helpers are known.
"""

import importlib
import sys
import os
import types
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers — build a minimal fake "shared" module so the import in the source
# file doesn't blow up even when the real shared.py is absent from the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"

def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="## Doc\nContent\n---GAPS---\n1. Question one?")
    shared.get_repo_files = MagicMock(return_value={"src/main.py": "print('hello')"})
    shared.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/out.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject the fake shared module before each test and reload the SUT."""
    fs = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fs)
    # Force a fresh import of the SUT so it picks up our fake shared module.
    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Add the scripts directory to sys.path if needed
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Also try the directory of this test file's peer location
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    yield fs


@pytest.fixture()
def sut(fake_shared):
    """Return the freshly-loaded tool3_business_docs module."""
    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    import tool3_business_docs as mod
    return mod


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_utcnow(monkeypatch):
    """Patch datetime.datetime.utcnow inside the SUT to return FROZEN_DT."""
    class _FakeDatetime(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return FROZEN_DT

    # We must patch the datetime class *inside the already-imported sut module*
    monkeypatch.setattr("tool3_business_docs.datetime.datetime", _FakeDatetime)
    return FROZEN_DT


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, sut, fake_shared, frozen_utcnow):
        """Claude returns a response that contains ---GAPS--- — split correctly."""
        fake_shared.call_claude.return_value = (
            "## Solution Overview\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the sponsor?"
        )
        fake_shared.get_repo_files.return_value = {"README.md": "# My project"}

        doc, gaps = sut.generate_biz_doc("acme", "myrepo", "MyProject", "1.0.0", "https://run")

        assert "## Solution Overview" in doc
        assert "Some content." in doc
        # gaps section must NOT contain the doc part
        assert "## Solution Overview" not in gaps
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the sponsor?" in gaps

    def test_happy_path_no_gaps_delimiter(self, sut, fake_shared, frozen_utcnow):
        """When Claude omits ---GAPS---, gaps fall back to the static warning string."""
        fake_shared.call_claude.return_value = "## Solution Overview\nOnly a doc, no delimiter."
        fake_shared.get_repo_files.return_value = {}

        doc, gaps = sut.generate_biz_doc("acme", "myrepo", "MyProject", "1.0.0", "https://run")

        assert "## Solution Overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared, frozen_utcnow):
        """Correct file-extension list and max_files=20 are forwarded."""
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"
        fake_shared.get_repo_files.return_value = {}

        sut.generate_biz_doc("owner", "repo", "proj", "2.0", "url")

        fake_shared.get_repo_files.assert_called_once()
        args, kwargs = fake_shared.get_repo_files.call_args
        assert args[0] == "owner"
        assert args[1] == "repo"
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert args[2] == expected_exts
        assert kwargs.get("max_files") == 20

    def test_call_claude_receives_formatted_prompt(self, sut, fake_shared, frozen_utcnow):
        """The SYSTEM prompt passed to call_claude must contain project_name and version."""
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"
        fake_shared.get_repo_files.return_value = {"a.py": "x=1"}

        sut.generate_biz_doc("owner", "repo", "InsurancePortal", "3.1.0", "url")

        args, _ = fake_shared.call_claude.call_args
        system_prompt = args[0]
        assert "InsurancePortal" in system_prompt
        assert "3.1.0" in system_prompt
        assert FROZEN_DATE_STR in system_prompt

    def test_call_claude_receives_files_in_user_message(self, sut, fake_shared, frozen_utcnow):
        """File content is embedded in the user message sent to Claude."""
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"
        fake_shared.get_repo_files.return_value = {
            "src/main.py": "def hello(): pass",
            "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        }

        sut.generate_biz_doc("owner", "repo", "proj", "1.0", "url")

        _, user_msg = fake_shared.call_claude.call_args[0]
        assert "src/main.py" in user_msg
        assert "def hello(): pass" in user_msg
        assert "infra/main.tf" in user_msg

    def test_multiple_gaps_delimiters_only_first_split(self, sut, fake_shared, frozen_utcnow):
        """Only the first ---GAPS--- is used as the split point."""
        fake_shared.call_claude.return_value = (
            "Doc part.\n"
            "---GAPS---\n"
            "Gaps part.\n"
            "---GAPS---\n"
            "Extra trailing content."
        )
        fake_shared.get_repo_files.return_value = {}

        doc, gaps = sut.generate_biz_doc("o", "r", "p", "v", "u")

        # doc must not contain any ---GAPS--- text
        assert "---GAPS---" not in doc
        # gaps should contain everything after the first delimiter
        assert "Gaps part." in gaps
        assert "Extra trailing content." in gaps

    def test_empty_claude_response(self, sut, fake_shared, frozen_utcnow):
        """An empty string from Claude is handled gracefully."""
        fake_shared.call_claude.return_value = ""
        fake_shared.get_repo_files.return_value = {}

        doc, gaps = sut.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == ""
        assert "Claude could not extract gap questions" in gaps

    def test_whitespace_only_gaps_part(self, sut, fake_shared, frozen_utcnow):
        """---GAPS--- present but gaps section is only whitespace → strip() returns ''."""
        fake_shared.call_claude.return_value = "Doc content.\n---GAPS---\n   \n  "
        fake_shared.get_repo_files.return_value = {}

        doc, gaps = sut.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "Doc content."
        assert gaps == ""

    def test_repo_files_truncated_to_3000_chars(self, sut, fake_shared, frozen_utcnow):
        """File content longer than 3000 chars is truncated in the user message."""
        long_content = "x" * 5000
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"
        fake_shared.get_repo_files.return_value = {"big.py": long_content}

        sut.generate_biz_doc("o", "r", "p", "v", "u")

        _, user_msg = fake_shared.call_claude.call_args[0]
        # The embedded content must be at most 3000 x's
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_call_claude_propagates_exception(self, sut, fake_shared, frozen_utcnow):
        """If call_claude raises, the exception bubbles up from generate_biz_doc."""
        fake_shared.call_claude.side_effect = RuntimeError("API timeout")
        fake_shared.get_repo_files.return_value = {}

        with pytest.raises(RuntimeError, match="API timeout"):
            sut.generate_biz_doc("o", "r", "p", "v", "u")

    def test_get_repo_files_propagates_exception(self, sut, fake_shared, frozen_utcnow):
        """If get_repo_files raises, the exception bubbles up."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            sut.generate_biz_doc("o", "r", "p", "v", "u")


# ===========================================================================
# Tests for build_full_output
# ===========================================================================

class TestBuildFullOutput:

    def test_returns_two_strings(self, sut, frozen_utcnow):
        full_md, gap_only_md = sut.build_full_output(
            "## Doc", "1. Question?", "acme", "myrepo", "MyProject", "1.0.0"
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, sut, frozen_utcnow):
        full_md, _ = sut.build_full_output(
            "## Solution\nDetails here.", "1. What date?",
            "acme", "myrepo", "MyProject", "1.0.0"
        )
        assert "## Solution" in full_md
        assert "Details here." in full_md

    def test_full_md_contains_gaps_content(self, sut, frozen_utcnow):
        full_md, _ = sut.build_full_output(
            "## Doc", "1. Go-live?\n2. Sponsor?",
            "acme", "myrepo", "MyProject", "1.0.0"
        )
        assert "1. Go-live?" in full_md
        assert "2. Sponsor?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, sut, frozen_utcnow):
        full_md, _ = sut.build_full_output(
            "## Doc", "1. Question?",
            "acme", "myrepo", "MyProject", "1.0.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_version_and_repo(self, sut, frozen_utcnow):
        full_md, _ = sut.build_full_output(
            "## Doc", "1. Q?",
            "acme", "myrepo", "MyProject", "2.3.1"
        )
        assert "acme/myrepo" in full_md
        assert "2.3.1" in full_md

    def test_full_md_contains_timestamp(self, sut, frozen_utcnow):
        full_md, _ = sut.build_full_output(
            "## Doc", "1. Q?",
            "acme", "myrepo", "MyProject", "1.0.0"
        )
        assert FROZEN_DATETIME_STR in full_md

    def test_gap_only_md_contains_project_name_and_version(self, sut, frozen_utcnow):
        _, gap_only_md = sut.build_full_output(
            "## Doc", "1. Q?",
            "acme", "myrepo", "InsurancePortal", "4.0.0"
        )
        assert "InsurancePortal" in gap_only_md
        assert "4.0.0" in gap_only_md

    def test_gap_