"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter splitting, missing delimiter fallback
    - build_full_output(): content assembly, metadata injection, gap-only document structure
    - __main__ block execution paths: success flow, exception/failure flow
    - Environment variable handling and defaults

Mocks used:
    - shared.call_claude          — patched to return synthetic Claude responses
    - shared.get_repo_files       — patched to return synthetic file content
    - shared.write_output_file    — patched to avoid real GitHub API calls
    - shared.send_email           — patched to avoid real email dispatch
    - shared.email_html           — patched to return a stub HTML string
    - shared.write_audit_entry    — patched to avoid real audit writes
    - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
    - TODO: Integration test with a real (sandboxed) Claude API key
    - TODO: Test write_output_file path construction for non-ASCII project names
    - TODO: Test behaviour when get_repo_files returns empty dict
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to keep the shared module import from exploding during collection
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = os.path.join(os.path.dirname(__file__), ".github", "scripts")


def _make_fake_shared():
    """Return a minimal fake 'shared' module so tool3 can be imported."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/output/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>stub</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject the fake shared module before each test and reload tool3."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Ensure the scripts directory is on sys.path so the import works
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        monkeypatch.syspath_prepend(scripts_dir)
    return fake


def _import_tool3():
    """Import (or re-import) tool3_business_docs cleanly."""
    module_name = "tool3_business_docs"
    if module_name in sys.modules:
        del sys.modules[module_name]
    import tool3_business_docs as t3
    return t3


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_NOW_DATE = "2024-06-15"
FIXED_NOW_DATETIME = "2024-06-15 12:00 UTC"


@pytest.fixture()
def tool3():
    return _import_tool3()


@pytest.fixture()
def frozen_datetime():
    """Patch datetime.datetime.utcnow to return a fixed point in time."""
    fixed = datetime.datetime(2024, 6, 15, 12, 0, 0)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = fixed
        mock_dt.strftime = datetime.datetime.strftime
        # Make strftime work on the returned value
        mock_dt.utcnow.return_value.strftime = fixed.strftime
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------


class TestGenerateBizDoc:
    """Tests for the generate_biz_doc() function."""

    def test_happy_path_returns_doc_and_gaps(self, tool3, fake_shared):
        """Claude response with delimiter → both parts returned correctly."""
        fake_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        fake_shared.call_claude.return_value = (
            "# Solution Overview\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the business sponsor?"
        )

        doc, gaps = tool3.generate_biz_doc(
            "acme", "my-repo", "My Project", "1.2.3", "https://github.com/run/1"
        )

        assert "# Solution Overview" in doc
        assert "Some content." in doc
        assert "What is the go-live date?" in gaps
        assert "business sponsor" in gaps
        # Delimiter itself must not appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_falls_back_gracefully(self, tool3, fake_shared):
        """When Claude omits ---GAPS---, doc gets all content; gaps is a warning."""
        fake_shared.call_claude.return_value = "# Full doc with no delimiter"

        doc, gaps = tool3.generate_biz_doc(
            "acme", "my-repo", "My Project", "1.0.0", "https://github.com/run/2"
        )

        assert "# Full doc with no delimiter" in doc
        assert "manually" in gaps.lower() or "could not" in gaps.lower()

    def test_calls_get_repo_files_with_correct_extensions(self, tool3, fake_shared):
        """get_repo_files must be called with expected extension list."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("acme", "repo", "Proj", "0.1", "url")

        args, kwargs = fake_shared.get_repo_files.call_args
        extensions_arg = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions_arg
        assert ".tf" in extensions_arg
        assert ".md" in extensions_arg

    def test_calls_get_repo_files_with_owner_and_repo(self, tool3, fake_shared):
        """Owner and repo are forwarded correctly to get_repo_files."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "d\n---GAPS---\ng"

        tool3.generate_biz_doc("my-owner", "my-repo", "P", "1", "u")

        call_args = fake_shared.get_repo_files.call_args
        assert call_args[0][0] == "my-owner"
        assert call_args[0][1] == "my-repo"

    def test_prompt_contains_project_name_and_version(self, tool3, fake_shared):
        """The formatted prompt passed to call_claude includes project_name and version."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "d\n---GAPS---\ng"

        tool3.generate_biz_doc("o", "r", "SuperApp", "3.0.0", "u")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "SuperApp" in prompt_arg
        assert "3.0.0" in prompt_arg

    def test_file_content_truncated_to_3000_chars(self, tool3, fake_shared):
        """Files longer than 3000 chars are truncated in the prompt context."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "d\n---GAPS---\ng"

        tool3.generate_biz_doc("o", "r", "P", "1", "u")

        context_arg = fake_shared.call_claude.call_args[0][1]
        # Truncated content should appear (3000 x's), full 5000 should not
        assert "x" * 3000 in context_arg
        assert "x" * 3001 not in context_arg

    def test_multiple_files_all_appear_in_context(self, tool3, fake_shared):
        """All returned files are included in the Claude context string."""
        fake_shared.get_repo_files.return_value = {
            "a.py": "code_a",
            "b.tf": "code_b",
            "c.md": "code_c",
        }
        fake_shared.call_claude.return_value = "d\n---GAPS---\ng"

        tool3.generate_biz_doc("o", "r", "P", "1", "u")

        context_arg = fake_shared.call_claude.call_args[0][1]
        assert "code_a" in context_arg
        assert "code_b" in context_arg
        assert "code_c" in context_arg

    def test_delimiter_split_only_on_first_occurrence(self, tool3, fake_shared):
        """Only the first ---GAPS--- is used as a split point."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = (
            "doc part\n---GAPS---\nfirst gap\n---GAPS---\nshould be in gaps"
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1", "u")

        assert "doc part" in doc
        assert "first gap" in gaps
        assert "should be in gaps" in gaps
        assert "---GAPS---" not in doc

    def test_whitespace_stripped_from_both_parts(self, tool3, fake_shared):
        """Leading/trailing whitespace is stripped from doc and gaps."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "  doc  \n---GAPS---\n  gaps  \n"

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_empty_files_dict_does_not_raise(self, tool3, fake_shared):
        """An empty repository (no files returned) should not raise."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "d\n---GAPS---\ng"

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1", "u")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------


class TestBuildFullOutput:
    """Tests for the build_full_output() function."""

    def test_full_md_contains_doc_content(self, tool3):
        doc = "# My Solution\nGreat content."
        gaps = "1. What is the go-live date?"

        full_md, _ = tool3.build_full_output(doc, gaps, "acme", "repo", "MyApp", "2.0.0")

        assert "# My Solution" in full_md
        assert "Great content." in full_md

    def test_full_md_contains_gap_content(self, tool3):
        doc = "# Doc"
        gaps = "1. Who owns this?\n2. When does it go live?"

        full_md, _ = tool3.build_full_output(doc, gaps, "acme", "repo", "App", "1.0.0")

        assert "Who owns this?" in full_md
        assert "When does it go live?" in full_md

    def test_full_md_contains_source_attribution(self, tool3):
        full_md, _ = tool3.build_full_output("d", "g", "owner", "myrepo", "App", "1.5.0")

        assert "owner/myrepo" in full_md
        assert "1.5.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3):
        _, gap_only = tool3.build_full_output("d", "g", "o", "r", "SpecialProject", "3.1.4")

        assert "SpecialProject" in gap_only
        assert "3.1.4" in gap_only

    def test_gap_only_md_contains_gap_questions(self, tool3):
        gaps = "1. What teams use this?\n2. What is the budget?"
        _, gap_only = tool3.build_full_output("d", gaps, "o", "r", "App", "1.0")

        assert "What teams use this?" in gap_only
        assert "What is the budget?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, tool3):
        """The gap-only doc links back to the output repo."""
        _, gap_only = tool3.build_full_output("d", "g", "o", "r", "App", "1.0")

        # OUTPUT_REPO_OWNER and OUTPUT_REPO are "test-owner" / "test-output-repo" from fake_shared
        assert "test-owner" in gap_only or "github.com" in gap_only

    def test_returns_two_strings(self, tool3):
        result = tool3.build_full_output("d", "g", "o", "r", "P", "1.0")

        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_full_md_gap_questionnaire_header_present(self, tool3):
        full_md, _ = tool3.build_full_output("# Doc", "1. Q?", "o", "r", "App", "1.0")

        assert "Gap Questionnaire" in full_md

    def test_gap_only_md_instructions_present(self, tool3):
        """Gap-only doc should include human-friendly instructions."""
        _, gap_only = tool3.build_full_output("d", "g", "o", "r", "App", "1.0")

        # Should contain some instructional text
        assert "answer" in gap_only.lower() or "complete" in gap_only.lower() or "questions" in gap_only.lower()

    def test_empty_gaps_string_handled(self, tool3):
        """Empty gaps string should not crash build_full_output."""
        full_md, gap_only = tool3.build_full_output("# Doc", "", "o", "r", "App", "1.0")

        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_timestamp_in_full_md(self, tool3):
        """A UTC timestamp string should appear in full_md."""
        full_md, _ = tool3.build_full_output("d", "g", "o", "r", "App", "1.0")

        assert "UTC" in full_md

    def test_timestamp_in_gap_only_md(self, tool3):
        _, gap_only = tool3.build_full_output("d", "g", "o", "r", "App", "1.0")

        assert "UTC