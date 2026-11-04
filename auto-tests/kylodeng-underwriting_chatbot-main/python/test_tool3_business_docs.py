"""
Tests for tool3_business_docs.py
=================================
What is tested:
  - generate_biz_doc(): happy path (delimiter present / absent), Claude integration, file retrieval
  - build_full_output(): markdown structure, content assembly, gap questionnaire formatting
  - __main__ block: environment variable handling, success flow, failure/exception flow

Mocks used:
  - shared.call_claude          → patched to return synthetic responses
  - shared.get_repo_files       → patched to return synthetic file dictionaries
  - shared.write_output_file    → patched to return a fake URL
  - shared.send_email           → patched to no-op
  - shared.email_html           → patched to return a dummy HTML string
  - shared.write_audit_entry    → patched to no-op
  - datetime.datetime.utcnow    → patched to return a fixed timestamp

TODOs:
  - TODO: Integration test against a real Claude endpoint (requires API key + network)
  - TODO: Test write_output_file actually commits to a real output repo (needs GH token)
  - TODO: Test send_email delivers mail (requires SMTP/SES credentials)
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal `shared` stub so the import always succeeds
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = os.path.join(os.path.dirname(__file__), ".github", "scripts", "shared.py")

def _make_shared_stub():
    """Return a stub `shared` module that satisfies tool3_business_docs imports."""
    mod = types.ModuleType("shared")
    mod.call_claude        = MagicMock(return_value="Doc content\n---GAPS---\n1. Question one?")
    mod.get_repo_files     = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file  = MagicMock(return_value="https://github.com/output/repo/file.md")
    mod.send_email         = MagicMock()
    mod.email_html         = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry  = MagicMock()
    mod.OUTPUT_REPO_OWNER  = "test-owner"
    mod.OUTPUT_REPO        = "test-output-repo"
    return mod


@pytest.fixture(autouse=True)
def stub_shared(monkeypatch):
    """
    Inject a fresh shared stub before every test and load/reload the module
    under test so it picks up the stub.
    """
    shared_stub = _make_shared_stub()
    # Make sure sys.modules["shared"] points at our stub
    monkeypatch.setitem(sys.modules, "shared", shared_stub)

    # Also insert the scripts directory so the relative import in the source works
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Force a fresh import each time
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]

    return shared_stub


def import_module():
    """Import (or re-import) tool3_business_docs with the current stubs."""
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic output
# ---------------------------------------------------------------------------

FIXED_NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR       = "2024-06-15"
FIXED_DATETIME_STR   = "2024-06-15 12:00 UTC"


# ---------------------------------------------------------------------------
# Tests – generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_delimiter_present(self, stub_shared):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        stub_shared.get_repo_files.return_value = {
            "README.md": "# Project\nSome description",
            "main.py":   "print('hello')",
        }
        stub_shared.call_claude.return_value = (
            "## Solution Overview\nThis does X.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n"
            "2. Who are the key users?"
        )

        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            doc, gaps = m.generate_biz_doc("my-owner", "my-repo", "MyProject", "1.0.0", "https://run")

        assert "Solution Overview" in doc
        assert "What is the go-live date?" in gaps
        assert "Who are the key users?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_no_delimiter(self, stub_shared):
        """When Claude omits ---GAPS--- the whole response becomes the doc and a fallback message is returned."""
        stub_shared.call_claude.return_value = "## Solution Overview\nNo gap delimiter here."

        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            doc, gaps = m.generate_biz_doc("o", "r", "P", "0.1", "https://run")

        assert "Solution Overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_multiple_delimiter_occurrences(self, stub_shared):
        """Only the FIRST ---GAPS--- split is used; subsequent ones stay in gaps_part."""
        stub_shared.call_claude.return_value = (
            "Doc part\n"
            "---GAPS---\n"
            "1. Q1?\n"
            "---GAPS---\n"
            "2. Q2?"
        )

        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            doc, gaps = m.generate_biz_doc("o", "r", "P", "0.1", "https://run")

        assert "Doc part" in doc
        assert "1. Q1?" in gaps
        # Second delimiter stays inside gaps
        assert "---GAPS---" in gaps or "2. Q2?" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, stub_shared):
        """get_repo_files must be called with the expected extensions list."""
        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            m.generate_biz_doc("owner", "repo", "proj", "1.0", "https://run")

        args, kwargs = stub_shared.get_repo_files.call_args
        extensions = args[2] if len(args) >= 3 else kwargs.get("extensions", [])
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions

    def test_get_repo_files_max_files_limit(self, stub_shared):
        """max_files should be capped at 20."""
        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            m.generate_biz_doc("o", "r", "p", "1", "https://run")

        _, kwargs = stub_shared.get_repo_files.call_args
        assert kwargs.get("max_files", None) == 20

    def test_call_claude_receives_project_name_in_prompt(self, stub_shared):
        """The project_name must appear in the prompt sent to Claude."""
        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            m.generate_biz_doc("o", "r", "InsuranceUnderwriter", "2.0", "https://run")

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert "InsuranceUnderwriter" in prompt_arg

    def test_call_claude_receives_version_in_prompt(self, stub_shared):
        """The version must appear in the prompt sent to Claude."""
        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            m.generate_biz_doc("o", "r", "P", "3.1.4", "https://run")

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert "3.1.4" in prompt_arg

    def test_file_content_truncated_to_3000_chars(self, stub_shared):
        """Files longer than 3000 chars must be truncated in the prompt to Claude."""
        long_content = "x" * 5000
        stub_shared.get_repo_files.return_value = {"big_file.py": long_content}

        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            m.generate_biz_doc("o", "r", "P", "1", "https://run")

        user_msg = stub_shared.call_claude.call_args[0][1]
        # The raw 5000-char content should NOT appear verbatim; only 3000 chars used
        assert "x" * 5000 not in user_msg
        assert "x" * 3000 in user_msg

    def test_empty_repo_files(self, stub_shared):
        """An empty repo (no matching files) should still produce output."""
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "No files.\n---GAPS---\n1. Any code at all?"

        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            doc, gaps = m.generate_biz_doc("o", "r", "P", "1", "https://run")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_whitespace_stripped_from_outputs(self, stub_shared):
        """Leading/trailing whitespace should be stripped from both outputs."""
        stub_shared.call_claude.return_value = "  \n Doc \n ---GAPS---  \n  Gaps  \n "

        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            doc, gaps = m.generate_biz_doc("o", "r", "P", "1", "https://run")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_owner_and_repo_appear_in_claude_user_message(self, stub_shared):
        """The owner/repo pair should be visible in the user message sent to Claude."""
        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            m.generate_biz_doc("acme", "widget-service", "Widget", "1.0", "https://run")

        user_msg = stub_shared.call_claude.call_args[0][1]
        assert "acme" in user_msg
        assert "widget-service" in user_msg


# ---------------------------------------------------------------------------
# Tests – build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, doc="## Doc", gaps="1. A question?",
              owner="acme", repo="my-repo",
              project_name="MyProject", version="1.2.3"):
        m = import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_NOW
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_NOW.strftime
            return m.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_tuple_of_two_strings(self):
        full_md, gap_md = self._call()
        assert isinstance(full_md, str)
        assert isinstance(gap_md, str)

    def test_full_md_contains_doc_content(self):
        full_md, _ = self._call(doc="## Executive Summary\nSomething important.")
        assert "Executive Summary" in full_md
        assert "Something important." in full_md

    def test_full_md_contains_gap_section_header(self):
        full_md, _ = self._call()
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self):
        full_md, _ = self._call(gaps="1. When is go-live?")
        assert "1. When is go-live?" in full_md

    def test_full_md_contains_attribution_line(self):
        full_md, _ = self._call(owner="acme", repo="widget", version="2.0.0")
        assert "acme/widget" in full_md
        assert "v2.0.0" in full_md

    def test_full_md_contains_ai_delivery_bot_attribution(self):
        full_md, _ = self._call()
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self):
        _, gap_md = self._call(project_name="InsurancePortal", version="3.0.0")
        assert "InsurancePortal" in gap_md
        assert "v3.0.0" in gap_md

    def test_gap_only_md_contains_gaps_content(self):
        _, gap_md = self._call(gaps="1. Who are the key stakeholders?")
        assert "1. Who are the key stakeholders?" in gap_md

    def test_gap_only_md_references_output_repo(self, stub_shared):
        """gap_only_md should include a