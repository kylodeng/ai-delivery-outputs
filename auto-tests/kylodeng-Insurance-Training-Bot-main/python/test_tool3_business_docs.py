"""
Tests for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path with ---GAPS--- delimiter, missing delimiter fallback,
    empty files dict, Claude returning empty string.
  - build_full_output(): happy path structure, content inclusion, date stamping,
    gap count lines, edge cases (empty doc, empty gaps, whitespace-only gaps).
  - __main__ block execution: environment variable handling, success path, exception/failure path.

Mocks used:
  - shared.call_claude              → unittest.mock.patch
  - shared.get_repo_files           → unittest.mock.patch
  - shared.write_output_file        → unittest.mock.patch
  - shared.send_email               → unittest.mock.patch
  - shared.email_html               → unittest.mock.patch
  - shared.write_audit_entry        → unittest.mock.patch
  - shared.OUTPUT_REPO_OWNER        → patched as module-level constant
  - shared.OUTPUT_REPO              → patched as module-level constant
  - datetime.datetime.utcnow        → unittest.mock.patch for deterministic timestamps

TODOs:
  - TODO: Integration test against a real Claude API response shape (requires API key)
  - TODO: Test __main__ block with a real subprocess or importlib.reload once env vars
          are injectable without side-effects to os.environ at import time
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Build a minimal 'shared' stub so the module can be imported in isolation
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/repo/file.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    return shared


@pytest.fixture(autouse=True)
def stub_shared(monkeypatch):
    """Insert a fresh shared stub before every test and reload the module under test."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)

    # Remove cached module so each test gets a clean import
    sys.modules.pop("tool3_business_docs", None)

    # Make sure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    sys.path.insert(0, os.path.abspath(script_dir))

    yield stub

    sys.modules.pop("tool3_business_docs", None)


def import_module():
    """Helper: import the module under test (shared stub already in sys.modules)."""
    import importlib
    return importlib.import_module("tool3_business_docs")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_DATE = "2024-06-15"
FIXED_DATETIME = "2024-06-15 12:00 UTC"

FAKE_FILES = {
    "main.py": "def main(): pass",
    "infra/main.tf": 'resource "aws_s3_bucket" "bucket" {}',
}


@pytest.fixture()
def patched_utcnow():
    """Patch datetime.datetime.utcnow to return a fixed value."""
    import datetime as dt_mod

    fixed_dt = MagicMock()
    fixed_dt.strftime = lambda fmt: FIXED_DATE if "%Y-%m-%d" == fmt else FIXED_DATETIME

    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = fixed_dt
        # Also allow strptime / other datetime usage to pass through
        mock_dt.side_effect = lambda *a, **kw: dt_mod.datetime(*a, **kw)
        yield mock_dt


# ===========================================================================
# generate_biz_doc tests
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = FAKE_FILES
        stub_shared.call_claude.return_value = (
            "# Solution overview\nSome doc content.\n"
            "---GAPS---\n"
            "1. Who owns this?\n2. What is the go-live date?\n"
        )
        mod = import_module()

        doc, gaps = mod.generate_biz_doc("acme", "myrepo", "MyProject", "1.2.3", "https://run")

        assert "# Solution overview" in doc
        assert "Some doc content." in doc
        assert "1. Who owns this?" in gaps
        assert "2. What is the go-live date?" in gaps

    def test_missing_gaps_delimiter_uses_fallback(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = FAKE_FILES
        stub_shared.call_claude.return_value = "Just a document with no delimiter"
        mod = import_module()

        doc, gaps = mod.generate_biz_doc("acme", "myrepo", "MyProject", "1.0.0", "https://run")

        assert doc == "Just a document with no delimiter"
        assert "manually" in gaps.lower() or "could not extract" in gaps.lower()

    def test_empty_files_dict(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "empty doc---GAPS---no gaps"
        mod = import_module()

        doc, gaps = mod.generate_biz_doc("acme", "myrepo", "MyProject", "0.1.0", "https://run")

        assert doc == "empty doc"
        assert gaps == "no gaps"

    def test_claude_receives_correct_project_info(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "doc---GAPS---q"
        mod = import_module()

        mod.generate_biz_doc("org", "repo", "InsurancePortal", "2.0.0", "https://run")

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg
        assert "2.0.0" in prompt_arg

    def test_claude_receives_repo_owner_and_name(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = {"README.md": "hello"}
        stub_shared.call_claude.return_value = "d---GAPS---g"
        mod = import_module()

        mod.generate_biz_doc("sun-life", "generations-ii", "Generations II", "1.0.0", "url")

        user_msg = stub_shared.call_claude.call_args[0][1]
        assert "sun-life/generations-ii" in user_msg

    def test_file_contents_truncated_to_3000_chars(self, stub_shared, patched_utcnow):
        long_content = "x" * 5000
        stub_shared.get_repo_files.return_value = {"big_file.py": long_content}
        stub_shared.call_claude.return_value = "d---GAPS---g"
        mod = import_module()

        mod.generate_biz_doc("acme", "repo", "Proj", "1.0.0", "url")

        user_msg = stub_shared.call_claude.call_args[0][1]
        # The truncated content (3000 x's) should appear, not 5000 x's
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_get_repo_files_called_with_correct_extensions(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "d---GAPS---g"
        mod = import_module()

        mod.generate_biz_doc("acme", "repo", "Proj", "1.0.0", "url")

        call_kwargs = stub_shared.get_repo_files.call_args
        extensions_arg = call_kwargs[0][2] if len(call_kwargs[0]) > 2 else call_kwargs[1].get("extensions", [])
        # Alternatively the second positional list
        args = call_kwargs[0]
        assert ".py" in args[2]
        assert ".tf" in args[2]
        assert ".md" in args[2]

    def test_gaps_delimiter_split_on_first_occurrence_only(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = (
            "doc section\n---GAPS---\nquestion 1\n---GAPS---\nquestion 2"
        )
        mod = import_module()

        doc, gaps = mod.generate_biz_doc("a", "b", "P", "1", "url")

        assert "doc section" in doc
        assert "question 1" in gaps
        # second delimiter and content after it are part of gaps
        assert "question 2" in gaps

    def test_doc_and_gaps_are_stripped(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "  doc content  \n---GAPS---\n  gap content  \n"
        mod = import_module()

        doc, gaps = mod.generate_biz_doc("a", "b", "P", "1", "url")

        assert doc == "doc content"
        assert gaps == "gap content"

    def test_claude_returns_empty_string_no_delimiter(self, stub_shared, patched_utcnow):
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = ""
        mod = import_module()

        doc, gaps = mod.generate_biz_doc("a", "b", "P", "1", "url")

        assert doc == ""
        assert "could not extract" in gaps.lower() or "manually" in gaps.lower()

    def test_date_is_injected_into_prompt(self, stub_shared):
        """Date string formatted YYYY-MM-DD should appear in the prompt sent to Claude."""
        import datetime as dt_real
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "d---GAPS---g"
        mod = import_module()

        mod.generate_biz_doc("a", "b", "P", "1", "url")

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        today = dt_real.datetime.utcnow().strftime("%Y-%m-%d")
        assert today in prompt_arg


# ===========================================================================
# build_full_output tests
# ===========================================================================

class TestBuildFullOutput:

    def _call(self, mod, doc="# Doc", gaps="1. Gap question?",
              owner="acme", repo="myrepo", project_name="MyProject", version="1.0.0"):
        return mod.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_happy_path_returns_tuple_of_two_strings(self, stub_shared):
        mod = import_module()
        result = self._call(mod)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_full_md_contains_doc_content(self, stub_shared):
        mod = import_module()
        full_md, _ = self._call(mod, doc="# My Solution Overview")
        assert "# My Solution Overview" in full_md

    def test_full_md_contains_gaps_content(self, stub_shared):
        mod = import_module()
        full_md, _ = self._call(mod, gaps="1. What is the deadline?")
        assert "1. What is the deadline?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, stub_shared):
        mod = import_module()
        full_md, _ = self._call(mod)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, stub_shared):
        mod = import_module()
        full_md, _ = self._call(mod, owner="acme", repo="myrepo", version="1.0.0")
        assert "acme/myrepo" in full_md
        assert "1.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, stub_shared):
        mod = import_module()
        _, gap_only = self._call(mod, project_name="InsurancePortal", version="2.5.0")
        assert "InsurancePortal" in gap_only
        assert "2.5.0" in gap_only

    def test_gap_only_md_contains_gaps_content(self, stub_shared):
        mod = import_module()
        _, gap_only = self._call(mod, gaps="2. Who are the key users?")
        assert "2. Who are the key users?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, stub_shared):
        stub_shared.OUTPUT_REPO_OWNER = "bot-owner"
        stub_shared.OUTPUT_REPO = "bot-repo"
        mod = import_module()
        _, gap_only = self._call(mod)
        assert "bot-owner" in gap_only or "bot-repo" in gap_only

    def test_full_md_contains_ai_delivery_bot_attribution(self, stub_shared):
        mod = import_module()
        full_md, _ = self._call(mod)
        assert "AI Delivery Bot" in full_md

    def test_empty_doc_produces_valid_output(self, stub_shared):
        mod = import_module()
        full_md, gap_only = self._call(mod, doc="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_empty_gaps_produces_valid_output(self, stub_shared):
        mod = import_module()
        full_md, gap_only = self._call(mod, gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_multiline_gaps_preserved(self, stub_shared):
        gaps = "1. First question?\n2. Second question?\n3. Third question?"
        mod = import_module()
        full_md, gap_only = self._call(mod, gaps=gaps)
        assert "1. First question?" in full_md
        assert "3. Third question?" in gap_only

    def test_special_characters_in_project_name(self, stub_shared):
        mod = import_module()
        full_md, gap_only = self._call(mod, project_name="Acme & Co. <Test> 'Proj'")
        assert "Acme & Co. <Test> 'Proj'" in gap_