"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc: orchestrates file fetching + Claude call + splits output
    - build_full_output: assembles full markdown and gap-only markdown
    - __main__ block logic (via importlib / subprocess where feasible)

Mocks used:
    - shared.call_claude          → MagicMock / side_effect
    - shared.get_repo_files       → MagicMock
    - shared.write_output_file    → MagicMock
    - shared.send_email           → MagicMock
    - shared.email_html           → MagicMock
    - shared.write_audit_entry    → MagicMock
    - datetime.datetime.utcnow    → patched for deterministic timestamps

TODOs:
    - TODO: test the __main__ block fully end-to-end (requires subprocess + env vars for clean isolation)
    - TODO: test behaviour when get_repo_files returns very large files (>3000 chars truncation)
    - TODO: test write_output_file / send_email integration after real credentials available
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake 'shared' module so the import in tool3 doesn't fail
# ---------------------------------------------------------------------------

def _make_fake_shared():
    mod = types.ModuleType("shared")
    mod.call_claude        = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?")
    mod.get_repo_files     = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file  = MagicMock(return_value="https://github.com/output/file")
    mod.send_email         = MagicMock()
    mod.email_html         = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry  = MagicMock()
    mod.OUTPUT_REPO_OWNER  = "test-owner"
    mod.OUTPUT_REPO        = "test-output-repo"
    return mod


def _import_tool3(fake_shared: types.ModuleType):
    """
    Import (or re-import) tool3_business_docs with the given fake shared module
    injected into sys.modules so the 'from shared import ...' resolves cleanly.
    """
    sys.modules["shared"] = fake_shared

    # Force a fresh import every time
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]

    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import tool3_business_docs
    return tool3_business_docs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared():
    return _make_fake_shared()


@pytest.fixture()
def tool3(shared):
    return _import_tool3(shared)


FIXED_NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR       = "2024-06-15"
FIXED_DATETIME_STR   = "2024-06-15 12:00 UTC"


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, tool3, shared):
        """Claude returns properly delimited output → both parts extracted."""
        shared.call_claude.return_value = (
            "## Solution overview\nSome content here.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the business sponsor?"
        )
        shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_NOW
            mock_dt.utcnow.return_value.strftime = FIXED_NOW.strftime
            doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "http://run")

        assert "Solution overview" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the business sponsor?" in gaps

    def test_no_delimiter_fallback(self, tool3, shared):
        """Claude returns output without ---GAPS--- → gaps gets fallback message."""
        shared.call_claude.return_value = "Some document without delimiter"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "http://run")

        assert doc == "Some document without delimiter"
        assert "could not extract gap questions" in gaps

    def test_delimiter_appears_only_once(self, tool3, shared):
        """Split on first occurrence only."""
        shared.call_claude.return_value = (
            "Part A\n---GAPS---\nPart B\n---GAPS---\nPart C"
        )

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "Proj", "0.1", "url")

        assert doc == "Part A"
        assert "Part B" in gaps
        assert "Part C" in gaps

    def test_empty_claude_response(self, tool3, shared):
        """Empty string from Claude → no delimiter → fallback gaps."""
        shared.call_claude.return_value = ""

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "Proj", "0.1", "url")

        assert doc == ""
        assert "could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, shared):
        """Verify the correct file extensions are requested."""
        shared.call_claude.return_value = "doc---GAPS---gaps"

        tool3.generate_biz_doc("myowner", "myrepo", "Proj", "1.0", "url")

        args, kwargs = shared.get_repo_files.call_args
        assert args[0] == "myowner"
        assert args[1] == "myrepo"
        extensions = args[2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_prompt_with_project_info(self, tool3, shared):
        """System prompt passed to Claude must contain project_name and version."""
        shared.call_claude.return_value = "doc---GAPS---gaps"

        tool3.generate_biz_doc("owner", "repo", "InsurancePortal", "2.3.1", "url")

        claude_prompt_arg = shared.call_claude.call_args[0][0]
        assert "InsurancePortal" in claude_prompt_arg
        assert "2.3.1" in claude_prompt_arg

    def test_call_claude_receives_files_in_user_message(self, tool3, shared):
        """User message to Claude must include repo files content."""
        shared.get_repo_files.return_value = {
            "main.tf": "resource aws_s3_bucket {}",
        }
        shared.call_claude.return_value = "doc---GAPS---gaps"

        tool3.generate_biz_doc("owner", "repo", "Proj", "1.0", "url")

        user_message = shared.call_claude.call_args[0][1]
        assert "main.tf" in user_message
        assert "resource aws_s3_bucket" in user_message

    def test_empty_repo_files(self, tool3, shared):
        """No files returned → empty files string → Claude still called."""
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc---GAPS---gaps"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "Proj", "1.0", "url")

        assert shared.call_claude.called
        assert doc == "doc"
        assert gaps == "gaps"

    def test_multiple_files_concatenated(self, tool3, shared):
        """Multiple files are all included in the Claude prompt."""
        shared.get_repo_files.return_value = {
            "app.py": "# app code",
            "infra.tf": "# terraform",
            "README.md": "# docs",
        }
        shared.call_claude.return_value = "doc---GAPS---gaps"

        tool3.generate_biz_doc("owner", "repo", "Proj", "1.0", "url")

        user_msg = shared.call_claude.call_args[0][1]
        assert "app.py" in user_msg
        assert "infra.tf" in user_msg
        assert "README.md" in user_msg

    def test_file_content_truncated_to_3000_chars(self, tool3, shared):
        """File content longer than 3000 chars should be truncated."""
        long_content = "x" * 5000
        shared.get_repo_files.return_value = {"big_file.py": long_content}
        shared.call_claude.return_value = "doc---GAPS---gaps"

        tool3.generate_biz_doc("owner", "repo", "Proj", "1.0", "url")

        user_msg = shared.call_claude.call_args[0][1]
        # The truncated version is 3000 x's
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_doc_and_gaps_stripped_of_whitespace(self, tool3, shared):
        """Leading/trailing whitespace is stripped from both parts."""
        shared.call_claude.return_value = "   doc content   \n---GAPS---\n   gap content   \n"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "Proj", "1.0", "url")

        assert doc == "doc content"
        assert gaps == "gap content"

    def test_date_in_prompt_matches_utcnow(self, tool3, shared):
        """The date embedded in the prompt should match utcnow strftime."""
        shared.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_NOW
            tool3.generate_biz_doc("owner", "repo", "Proj", "1.0", "url")

        prompt_arg = shared.call_claude.call_args[0][0]
        assert FIXED_DATE_STR in prompt_arg

    def test_insurance_project_name_in_prompt(self, tool3, shared):
        """Synthetic data: insurance-style project names work correctly."""
        shared.call_claude.return_value = "doc---GAPS---gaps"

        tool3.generate_biz_doc(
            "sun-life", "generations-ii", "Generations II", "2.0.0", "url"
        )

        prompt = shared.call_claude.call_args[0][0]
        assert "Generations II" in prompt
        assert "2.0.0" in prompt


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_happy_path_returns_two_strings(self, tool3):
        doc = "## Solution overview\nContent here."
        gaps = "1. What is the go-live date?\n2. Who owns this?"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_NOW
            full_md, gap_only_md = tool3.build_full_output(
                doc, gaps, "owner", "repo", "MyProject", "1.0.0"
            )

        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, tool3):
        doc = "## Solution overview\nVery important content."
        gaps = "1. A question?"

        full_md, _ = tool3.build_full_output(doc, gaps, "o", "r", "P", "1.0")

        assert "Very important content." in full_md

    def test_full_md_contains_gaps(self, tool3):
        doc = "Doc"
        gaps = "1. Critical question here?"

        full_md, _ = tool3.build_full_output(doc, gaps, "o", "r", "P", "1.0")

        assert "Critical question here?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool3):
        full_md, _ = tool3.build_full_output("doc", "gaps", "o", "r", "P", "1.0")

        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool3):
        full_md, _ = tool3.build_full_output("doc", "gaps", "myowner", "myrepo", "P", "2.1")

        assert "myowner/myrepo" in full_md
        assert "2.1" in full_md

    def test_full_md_contains_autogenerated_notice(self, tool3):
        full_md, _ = tool3.build_full_output("doc", "gaps", "o", "r", "P", "1.0")

        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3):
        _, gap_only_md = tool3.build_full_output(
            "doc", "1. A gap?", "o", "r", "InsurancePortal", "3.0.0"
        )

        assert "InsurancePortal" in gap_only_md
        assert "3.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, tool3):
        gaps = "1. What is the target go-live date?\n2. Who is the business sponsor?"
        _, gap_only_md = tool3.build_full_output("doc", gaps, "o", "r", "P", "1.0")

        assert "What is the target go-live date?" in gap_only_md
        assert "Who is the business sponsor?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, tool3, shared):
        _, gap_only_md = tool3.build_full_output("doc", "gaps", "o", "r", "P", "1.0")

        assert "test-owner" in gap_only_md or "github.com" in gap_only_md

    def test_gap_only_md_estimated_time_message(self, tool3):
        _, gap_only_md = tool3.build_full_output("doc", "gaps", "o", "r", "P", "1.0")

        assert "10-15 minutes" in gap_only_md

    def test_full_md_timestamp_present(self, tool3):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_NOW
            full_md, _ = tool3.build_full_output("doc", "gaps", "o", "r", "P", "1.0")

        assert "UTC" in full_md

    def test_empty_doc_and_gaps(self, tool3):
        """