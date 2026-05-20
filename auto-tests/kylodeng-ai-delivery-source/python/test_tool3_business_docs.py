"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, Claude response handling
    - build_full_output(): output structure, content formatting, edge cases (empty strings,
      long inputs, special characters)
    - __main__ block logic (via subprocess or direct env-var injection) — stubbed
    - Gap count calculation logic mirrored from __main__

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (for deterministic timestamps)

TODOs:
    # TODO: Integration test requiring a real GitHub token and Claude API key
    # TODO: Test __main__ block end-to-end via subprocess with env injection
    # TODO: Verify email HTML body content in detail once email_html signature is confirmed
"""

import sys
import os
import importlib
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared deps stubbed out
# ---------------------------------------------------------------------------

SHARED_STUB_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. What is the go-live date?"),
    "get_repo_files": MagicMock(return_value={"main.py": "print('hello')"}),
    "write_output_file": MagicMock(return_value="https://github.com/out/repo/blob/main/file.md"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>stub</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_module():
    """Create a fake 'shared' module with stub callables."""
    mod = types.ModuleType("shared")
    for k, v in SHARED_STUB_ATTRS.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def stub_shared(monkeypatch):
    """
    Insert a fresh stub 'shared' module before every test so that
    `from shared import ...` inside the module under test resolves to stubs.
    Resets all MagicMock call counts between tests.
    """
    shared_mod = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)

    # Remove cached module so re-import picks up the fresh stub
    sys.modules.pop("tool3_business_docs", None)
    # Ensure script directory is on path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        monkeypatch.syspath_prepend(script_dir)

    yield shared_mod

    # Cleanup
    sys.modules.pop("tool3_business_docs", None)


def _import_module():
    """Import (or re-import) the module under test."""
    # Make sure we look in the right place
    target = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if target not in sys.path:
        sys.path.insert(0, target)
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic tests
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 30, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:30 UTC"


@pytest.fixture()
def fixed_datetime(monkeypatch):
    """Patch datetime.datetime inside tool3_business_docs to return FIXED_DT."""
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        # Make strftime work on the mock return value
        mock_dt.utcnow.return_value = FIXED_DT
        yield mock_dt


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, stub_shared, fixed_datetime):
        """Claude returns properly delimited output → both parts extracted correctly."""
        stub_shared.get_repo_files.return_value = {
            "main.py": "def main(): pass",
            "infra.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        stub_shared.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who are the key users?"
        )

        m = _import_module()
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            doc, gaps = m.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run")

        assert "# Solution overview: MyProject" in doc
        assert "Some content" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who are the key users?" in gaps

    def test_happy_path_delimiter_stripped_from_doc(self, stub_shared):
        """Delimiter must not appear in the returned doc_part."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"
        m = _import_module()
        doc, gaps = m.generate_biz_doc("o", "r", "p", "v", "url")
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_delimiter_fallback(self, stub_shared):
        """When Claude omits delimiter, gaps fallback message is returned."""
        stub_shared.call_claude.return_value = "Only document content, no delimiter here."
        m = _import_module()
        doc, gaps = m.generate_biz_doc("o", "r", "p", "v", "url")
        assert doc == "Only document content, no delimiter here."
        assert "Claude could not extract gap questions" in gaps

    def test_multiple_delimiter_occurrences_splits_on_first(self, stub_shared):
        """Only the first ---GAPS--- should act as the split point."""
        stub_shared.call_claude.return_value = (
            "doc part\n---GAPS---\ngap1\n---GAPS---\ngap2"
        )
        m = _import_module()
        doc, gaps = m.generate_biz_doc("o", "r", "p", "v", "url")
        assert doc == "doc part"
        assert "gap1" in gaps
        assert "gap2" in gaps  # second occurrence stays in gaps section

    def test_get_repo_files_called_with_correct_extensions(self, stub_shared):
        """Correct file extensions and max_files should be passed to get_repo_files."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"
        m = _import_module()
        m.generate_biz_doc("owner123", "repo456", "proj", "0.2.0", "url")

        stub_shared.get_repo_files.assert_called_once()
        args, kwargs = stub_shared.get_repo_files.call_args
        assert "owner123" in args
        assert "repo456" in args
        assert ".py" in args[2]
        assert ".tf" in args[2]
        assert ".md" in args[2]
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_project_name_in_prompt(self, stub_shared):
        """Project name must appear in the prompt passed to Claude."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"
        m = _import_module()
        m.generate_biz_doc("o", "r", "SpecialProjectName", "v", "url")

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert "SpecialProjectName" in prompt_arg

    def test_call_claude_receives_version_in_prompt(self, stub_shared):
        """Version string must appear in the prompt passed to Claude."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"
        m = _import_module()
        m.generate_biz_doc("o", "r", "proj", "3.14.159", "url")

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert "3.14.159" in prompt_arg

    def test_call_claude_user_message_contains_owner_repo(self, stub_shared):
        """User message to Claude must reference owner/repo."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"
        m = _import_module()
        m.generate_biz_doc("myowner", "myrepo", "proj", "v", "url")

        user_msg = stub_shared.call_claude.call_args[0][1]
        assert "myowner/myrepo" in user_msg

    def test_empty_repo_files(self, stub_shared):
        """Empty file dict should still produce a valid (possibly minimal) output."""
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "minimal doc\n---GAPS---\n1. Question?"
        m = _import_module()
        doc, gaps = m.generate_biz_doc("o", "r", "p", "v", "url")
        assert doc == "minimal doc"
        assert "1. Question?" in gaps

    def test_large_file_content_truncated_in_user_message(self, stub_shared):
        """File content should be truncated to 3000 chars in the user message."""
        large_content = "x" * 10_000
        stub_shared.get_repo_files.return_value = {"big.py": large_content}
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"
        m = _import_module()
        m.generate_biz_doc("o", "r", "p", "v", "url")

        user_msg = stub_shared.call_claude.call_args[0][1]
        # The truncated content block should not exceed 3000 x's
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_whitespace_stripped_from_parts(self, stub_shared):
        """Leading/trailing whitespace should be stripped from doc and gaps."""
        stub_shared.call_claude.return_value = "  \n\n doc content \n\n---GAPS---\n\n  gap content  \n"
        m = _import_module()
        doc, gaps = m.generate_biz_doc("o", "r", "p", "v", "url")
        assert doc == "doc content"
        assert gaps == "gap content"

    def test_delimiter_only_response(self, stub_shared):
        """Edge case: response is just the delimiter."""
        stub_shared.call_claude.return_value = "---GAPS---"
        m = _import_module()
        doc, gaps = m.generate_biz_doc("o", "r", "p", "v", "url")
        assert doc == ""
        assert gaps == ""

    def test_call_claude_propagates_exception(self, stub_shared):
        """If call_claude raises, generate_biz_doc should propagate the exception."""
        stub_shared.call_claude.side_effect = RuntimeError("Claude API timeout")
        m = _import_module()
        with pytest.raises(RuntimeError, match="Claude API timeout"):
            m.generate_biz_doc("o", "r", "p", "v", "url")


# ===========================================================================
# Tests for build_full_output
# ===========================================================================

class TestBuildFullOutput:

    def test_returns_two_strings(self, stub_shared):
        m = _import_module()
        result = m.build_full_output("doc", "gaps", "owner", "repo", "MyProj", "1.0.0")
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, stub_shared):
        m = _import_module()
        full_md, _ = m.build_full_output(
            "# My Document\nSome content", "1. Gap question",
            "owner", "repo", "MyProj", "2.0.0"
        )
        assert "# My Document" in full_md
        assert "Some content" in full_md

    def test_full_md_contains_gap_questionnaire_section(self, stub_shared):
        m = _import_module()
        full_md, _ = m.build_full_output(
            "doc", "1. What is the deadline?",
            "owner", "repo", "MyProj", "2.0.0"
        )
        assert "Gap Questionnaire" in full_md
        assert "1. What is the deadline?" in full_md

    def test_full_md_contains_source_attribution(self, stub_shared):
        m = _import_module()
        full_md, _ = m.build_full_output("doc", "gaps", "acme-org", "cool-repo", "CoolProj", "1.2.3")
        assert "acme-org/cool-repo" in full_md
        assert "1.2.3" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, stub_shared):
        m = _import_module()
        _, gap_only_md = m.build_full_output("doc", "gaps", "o", "r", "AwesomeProject", "3.0.0")
        assert "AwesomeProject" in gap_only_md
        assert "3.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, stub_shared):
        m = _import_module()
        _, gap_only_md = m.build_full_output(
            "doc", "1. Who is the sponsor?\n2. What is the budget?",
            "o", "r", "p", "v"
        )
        assert "1. Who is the sponsor?" in gap_only_md
        assert "2. What is the budget?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, stub_shared):
        """Gap questionnaire should link to the output repo."""
        m = _import_module()
        _, gap_only_md = m.build_full_output("doc", "gaps", "o", "r", "p", "v")
        # OUTPUT_REPO_OWNER and OUTPUT_REPO are injected from stub
        assert "test-owner