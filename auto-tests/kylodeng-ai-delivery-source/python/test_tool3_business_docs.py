"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude response handling
    - build_full_output(): markdown construction, correct sections, metadata embedding
    - __main__ block: environment variable handling, success path, exception/failure path
    - Edge cases: missing delimiter, empty gaps, whitespace handling, version/project_name defaults

Mocks used:
    - shared.call_claude          — stubbed to return controlled strings
    - shared.get_repo_files       — stubbed to return a dict of file path → content
    - shared.write_output_file    — stubbed to return a fake URL
    - shared.send_email           — stubbed (no-op)
    - shared.email_html           — stubbed to return a plain string
    - shared.write_audit_entry    — stubbed (no-op)
    - datetime.datetime.utcnow    — frozen where deterministic output is needed
    - os.environ                  — patched via monkeypatch / unittest.mock.patch.dict

TODOs:
    - TODO: Integration test against a real GitHub repo (requires GH token + network)
    - TODO: Test actual Claude API response parsing for malformed/partial JSON
    - TODO: Test write_output_file commit collision handling (needs output-repo fixture)
"""

import sys
import os
import importlib
import datetime
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so we can import the SUT
# without the real shared.py being present (or partially present).
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a fake `shared` module with all symbols the SUT imports."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?")
    shared.get_repo_files = MagicMock(return_value={"README.md": "# hello"})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/file")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fresh fake shared module before every test and reload the SUT so
    that module-level imports pick up the fakes.
    """
    fs = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fs)

    # Remove cached SUT module so it re-imports with the fresh fake
    sys.modules.pop("tool3_business_docs", None)

    # Make sure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    yield fs


@pytest.fixture()
def sut(fake_shared):
    """Import (or re-import) the SUT after fakes are installed."""
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_utcnow():
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FROZEN_DT
        mock_dt.datetime.utcnow.return_value.strftime = FROZEN_DT.strftime
        # Make strftime work on the mock
        mock_dt.datetime.utcnow = MagicMock(return_value=FROZEN_DT)
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, sut, fake_shared):
        """Claude returns correctly delimited output → both parts returned."""
        fake_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        fake_shared.call_claude.return_value = (
            "## Solution Overview\nSome content here."
            "\n---GAPS---\n"
            "1. What is the go-live date?\n2. Who owns the budget?"
        )

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run.url")

        assert "Solution Overview" in doc
        assert "Some content here" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who owns the budget?" in gaps

    def test_no_delimiter_falls_back(self, sut, fake_shared):
        """When Claude omits ---GAPS---, doc gets full response, gaps get fallback text."""
        fake_shared.call_claude.return_value = "Only a document, no gaps section here."

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run.url")

        assert "Only a document" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_delimiter_at_start_gives_empty_doc(self, sut, fake_shared):
        """Edge case: delimiter is the very first thing returned."""
        fake_shared.call_claude.return_value = "---GAPS---\n1. A question?"

        doc, gaps = sut.generate_biz_doc("acme", "repo", "Proj", "0.1.0", "")

        assert doc == ""  # split gives empty string before delimiter, then stripped
        assert "1. A question?" in gaps

    def test_delimiter_at_end_gives_empty_gaps(self, sut, fake_shared):
        """Edge case: delimiter appears at the very end, gaps section is empty."""
        fake_shared.call_claude.return_value = "Some doc\n---GAPS---"

        doc, gaps = sut.generate_biz_doc("acme", "repo", "Proj", "0.1.0", "")

        assert "Some doc" in doc
        assert gaps == ""

    def test_multiple_delimiters_splits_on_first(self, sut, fake_shared):
        """split(..., 1) ensures only the first ---GAPS--- is used."""
        fake_shared.call_claude.return_value = (
            "Doc content\n---GAPS---\nQ1?\n---GAPS---\nQ2 (should be in gaps part)?"
        )

        doc, gaps = sut.generate_biz_doc("acme", "repo", "Proj", "0.1.0", "")

        assert "---GAPS---" not in doc
        assert "Q1?" in gaps
        assert "Q2 (should be in gaps part)?" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared):
        """Verify the file filter extensions passed to get_repo_files."""
        sut.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        call_args = fake_shared.get_repo_files.call_args
        positional = call_args[0]
        assert positional[0] == "owner"
        assert positional[1] == "repo"
        extensions = positional[2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions
        assert call_args[1].get("max_files", call_args[0][3] if len(call_args[0]) > 3 else 20) == 20

    def test_call_claude_receives_owner_repo_in_user_message(self, sut, fake_shared):
        """The user message passed to Claude should contain owner/repo."""
        sut.generate_biz_doc("myowner", "myrepo", "P", "2.0.0", "")

        _, user_msg = fake_shared.call_claude.call_args[0]
        assert "myowner/myrepo" in user_msg

    def test_prompt_contains_project_name(self, sut, fake_shared):
        """System prompt passed to Claude must include the project name."""
        sut.generate_biz_doc("o", "r", "AwesomeProject", "3.0.0", "")

        system_prompt, _ = fake_shared.call_claude.call_args[0]
        assert "AwesomeProject" in system_prompt

    def test_prompt_contains_version(self, sut, fake_shared):
        """System prompt must include the version string."""
        sut.generate_biz_doc("o", "r", "P", "9.9.9", "")

        system_prompt, _ = fake_shared.call_claude.call_args[0]
        assert "9.9.9" in system_prompt

    def test_file_content_truncated_to_3000_chars(self, sut, fake_shared):
        """Files longer than 3000 chars should be truncated in the user message."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"bigfile.py": long_content}

        sut.generate_biz_doc("o", "r", "P", "1.0", "")

        _, user_msg = fake_shared.call_claude.call_args[0]
        # The truncated content should appear, not the full 10 000 chars
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files(self, sut, fake_shared):
        """No files returned from repo → Claude still called with empty files string."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\nq?"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "")

        assert doc == "doc"
        assert gaps == "q?"
        fake_shared.call_claude.assert_called_once()

    def test_returns_stripped_strings(self, sut, fake_shared):
        """Leading/trailing whitespace must be stripped from both parts."""
        fake_shared.call_claude.return_value = "   doc with spaces   \n---GAPS---\n   gaps   \n"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "")

        assert not doc.startswith(" ")
        assert not doc.endswith(" ")
        assert not gaps.startswith(" ")
        assert not gaps.endswith(" ")


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, sut, doc="# Doc", gaps="1. Q?", owner="acme",
              repo="myrepo", project_name="MyProject", version="1.2.3"):
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FROZEN_DT
            return sut.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_tuple_of_two_strings(self, sut):
        result = self._call(sut)
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, sut):
        full_md, _ = self._call(sut, doc="## Executive Summary\nGreat product.")
        assert "## Executive Summary" in full_md
        assert "Great product." in full_md

    def test_full_md_contains_gap_questionnaire_section(self, sut):
        full_md, _ = self._call(sut, gaps="1. Who is the owner?")
        assert "Gap Questionnaire" in full_md
        assert "1. Who is the owner?" in full_md

    def test_full_md_contains_source_attribution(self, sut):
        full_md, _ = self._call(sut, owner="acme", repo="widget", version="2.0.0")
        assert "acme/widget" in full_md
        assert "v2.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name(self, sut):
        _, gap_only_md = self._call(sut, project_name="SuperTool", version="3.0.0")
        assert "SuperTool" in gap_only_md

    def test_gap_only_md_contains_version(self, sut):
        _, gap_only_md = self._call(sut, version="5.5.5")
        assert "5.5.5" in gap_only_md

    def test_gap_only_md_contains_gaps_text(self, sut):
        _, gap_only_md = self._call(sut, gaps="1. First question?\n2. Second question?")
        assert "1. First question?" in gap_only_md
        assert "2. Second question?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, sut, fake_shared):
        _, gap_only_md = self._call(sut)
        # Link should reference the OUTPUT_REPO_OWNER / OUTPUT_REPO constants
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_has_estimated_time(self, sut):
        _, gap_only_md = self._call(sut)
        assert "10-15 minutes" in gap_only_md

    def test_full_md_has_ai_draft_footer(self, sut):
        full_md, _ = self._call(sut)
        assert "Draft auto-generated by AI Delivery Bot" in full_md

    def test_empty_gaps_string(self, sut):
        """build_full_output must not crash when gaps is an empty string."""
        full_md, gap_only_md = self._call(sut, gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_empty_doc_string(self, sut):
        """build_full_output must not crash when doc is an empty string."""
        full_md, gap_only_md = self._call(sut, doc="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_special_characters_in_project_name(self, sut):
        """Project names with special chars should not cause formatting issues."""
        full_md, gap_only_md = self._call(sut, project_name="Project & Co. <v2>")
        assert "Project & Co. <v2>" in gap_only_md

    @pytest.mark.parametrize("version", ["0.0.1", "1.0.0", "10.20