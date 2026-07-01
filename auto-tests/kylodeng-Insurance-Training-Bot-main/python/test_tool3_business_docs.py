"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing delimiter, empty files
    - build_full_output(): happy path, empty gaps, special characters in inputs
    - __main__ block behaviour (via subprocess or importlib): env-driven orchestration
    - Edge cases: delimiter appears multiple times, gaps counting, whitespace handling

Mocks used:
    - shared.call_claude          → prevents real Anthropic API calls
    - shared.get_repo_files       → prevents real GitHub API calls
    - shared.write_output_file    → prevents real file/repo writes
    - shared.send_email           → prevents real email dispatch
    - shared.email_html           → prevents HTML rendering side-effects
    - shared.write_audit_entry    → prevents real audit log writes
    - datetime.datetime.utcnow    → frozen timestamps for deterministic assertions

TODOs:
    - TODO: Test __main__ block end-to-end once a test harness for subprocess env injection is confirmed
    - TODO: Test write_output_file slug usage once OUTPUT_REPO_OWNER/OUTPUT_REPO are configurable per-test
    - TODO: Test failure branch (exception → write_audit_entry FAILED) — needs importlib reload approach
"""

import sys
import os
import types
import datetime
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import inside the source works
# without any real credentials or network access.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude       = MagicMock(return_value="# Doc\n---GAPS---\n1. A question?")
    shared.get_repo_files    = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/repo/blob/main/file.md")
    shared.send_email        = MagicMock()
    shared.email_html        = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO       = "test-repo"
    return shared


@pytest.fixture(autouse=True)
def stub_shared(monkeypatch):
    """Inject a stub `shared` module before the source is (re-)imported."""
    shared_stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", shared_stub)
    # Also make sure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        monkeypatch.syspath_prepend(script_dir)
    return shared_stub


@pytest.fixture()
def biz_docs(stub_shared):
    """
    Import (or re-import) the module under test with the stub in place.
    We use importlib so each test gets a fresh module state.
    """
    module_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
    )
    spec = importlib.util.spec_from_file_location("tool3_business_docs", module_path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FROZEN_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_utcnow():
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FROZEN_DATE
        mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
        # Make strftime work correctly on the mock
        mock_dt.utcnow.side_effect = None
        mock_dt.utcnow.return_value = FROZEN_DATE
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, biz_docs, stub_shared):
        """Claude returns content with ---GAPS--- delimiter — both parts returned."""
        stub_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        stub_shared.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content here."
            "\n---GAPS---\n"
            "1. What is the go-live date?\n2. Who owns this product?"
        )

        doc, gaps = biz_docs.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert "# Solution overview: MyProject" in doc
        assert "Some content here." in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who owns this product?" in gaps

    def test_missing_delimiter_falls_back_gracefully(self, biz_docs, stub_shared):
        """When Claude omits ---GAPS--- the full response becomes the doc and gaps is fallback text."""
        stub_shared.call_claude.return_value = "# Doc content without gaps"

        doc, gaps = biz_docs.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert doc == "# Doc content without gaps"
        assert "Claude could not extract gap questions" in gaps

    def test_delimiter_only_at_start(self, biz_docs, stub_shared):
        """Delimiter is the first thing — doc part is empty string, gaps part is the rest."""
        stub_shared.call_claude.return_value = "---GAPS---\n1. Only a gap question."

        doc, gaps = biz_docs.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert doc == ""
        assert "1. Only a gap question." in gaps

    def test_multiple_delimiters_splits_on_first(self, biz_docs, stub_shared):
        """When ---GAPS--- appears more than once, split on the first occurrence only."""
        stub_shared.call_claude.return_value = (
            "doc part\n---GAPS---\ngaps part\n---GAPS---\nextra"
        )

        doc, gaps = biz_docs.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert doc == "doc part"
        assert "gaps part" in gaps
        assert "extra" in gaps

    def test_get_repo_files_called_with_correct_args(self, biz_docs, stub_shared):
        """Verifies correct file extensions and max_files are passed to get_repo_files."""
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        biz_docs.generate_biz_doc("owner-x", "repo-y", "Proj", "2.0.0", "https://x")

        stub_shared.get_repo_files.assert_called_once_with(
            "owner-x",
            "repo-y",
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_call_claude_receives_project_name_in_prompt(self, biz_docs, stub_shared):
        """The system prompt sent to Claude contains the project name."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        biz_docs.generate_biz_doc("o", "r", "InsurancePortal", "3.1.0", "https://x")

        args, _ = stub_shared.call_claude.call_args
        prompt = args[0]
        assert "InsurancePortal" in prompt
        assert "3.1.0" in prompt

    def test_call_claude_receives_files_in_user_message(self, biz_docs, stub_shared):
        """File contents are serialised into the user message passed to Claude."""
        stub_shared.get_repo_files.return_value = {
            "README.md": "# Hello World",
        }
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        biz_docs.generate_biz_doc("o", "r", "Proj", "1.0.0", "https://x")

        _, user_msg = stub_shared.call_claude.call_args[0]
        assert "README.md" in user_msg
        assert "# Hello World" in user_msg

    def test_file_content_truncated_to_3000_chars(self, biz_docs, stub_shared):
        """Content longer than 3000 chars is truncated before being sent to Claude."""
        long_content = "x" * 5000
        stub_shared.get_repo_files.return_value = {"big.py": long_content}
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        biz_docs.generate_biz_doc("o", "r", "Proj", "1.0.0", "https://x")

        _, user_msg = stub_shared.call_claude.call_args[0]
        # The truncated version should appear (3000 x's), not the full 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_no_files(self, biz_docs, stub_shared):
        """When repo has no files, generate_biz_doc still calls Claude and returns output."""
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = biz_docs.generate_biz_doc("o", "r", "Proj", "1.0.0", "https://x")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_whitespace_stripped_from_parts(self, biz_docs, stub_shared):
        """Leading/trailing whitespace is stripped from both doc and gaps parts."""
        stub_shared.call_claude.return_value = "  \n  doc content  \n  ---GAPS---  \n  gap content  \n  "

        doc, gaps = biz_docs.generate_biz_doc("o", "r", "Proj", "1.0.0", "https://x")

        assert doc == "doc content"
        assert gaps == "gap content"

    def test_unicode_content_handled(self, biz_docs, stub_shared):
        """Unicode in filenames and content does not break the function."""
        stub_shared.get_repo_files.return_value = {
            "描述.md": "这是一个测试文档",
        }
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = biz_docs.generate_biz_doc("o", "r", "Proj", "1.0.0", "https://x")

        assert doc == "doc"
        assert gaps == "gaps"


# ---------------------------------------------------------------------------
# Tests: build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_happy_path_returns_tuple_of_two_strings(self, biz_docs):
        """build_full_output returns a 2-tuple of non-empty strings."""
        result = biz_docs.build_full_output(
            "## Doc content", "1. A gap question?",
            "acme", "my-repo", "MyProject", "1.2.3"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_full_md_contains_doc_content(self, biz_docs):
        """The full markdown document includes the original doc content."""
        full_md, _ = biz_docs.build_full_output(
            "## Executive Summary\nGreat product.", "1. Go-live date?",
            "acme", "my-repo", "MyProject", "1.0.0"
        )
        assert "## Executive Summary" in full_md
        assert "Great product." in full_md

    def test_full_md_contains_gaps(self, biz_docs):
        """The full markdown document includes the gap questionnaire section."""
        full_md, _ = biz_docs.build_full_output(
            "# Doc", "1. Question one?\n2. Question two?",
            "acme", "my-repo", "MyProject", "1.0.0"
        )
        assert "Gap Questionnaire" in full_md
        assert "1. Question one?" in full_md
        assert "2. Question two?" in full_md

    def test_full_md_contains_attribution_footer(self, biz_docs):
        """The full markdown includes the auto-generated footer."""
        full_md, _ = biz_docs.build_full_output(
            "# Doc", "1. Q?",
            "acme", "my-repo", "MyProject", "1.0.0"
        )
        assert "AI Delivery Bot" in full_md
        assert "acme/my-repo" in full_md
        assert "v1.0.0" in full_md

    def test_gap_only_md_contains_project_header(self, biz_docs):
        """The standalone gap questionnaire starts with the project name and version."""
        _, gap_only_md = biz_docs.build_full_output(
            "# Doc", "1. A question?",
            "acme", "my-repo", "InsurancePortal", "2.5.0"
        )
        assert "InsurancePortal" in gap_only_md
        assert "v2.5.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, biz_docs):
        """The standalone gap doc contains the raw gap questions."""
        _, gap_only_md = biz_docs.build_full_output(
            "# Doc", "1. Target go-live?\n2. Who is the sponsor?",
            "acme", "my-repo", "Proj", "1.0.0"
        )
        assert "1. Target go-live?" in gap_only_md
        assert "2. Who is the sponsor?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, biz_docs, stub_shared):
        """The standalone gap doc links to the output repo."""
        _, gap_only_md = biz_docs.build_full_output(
            "# Doc", "1. Q?",
            "acme", "my-repo", "Proj", "1.0.0"
        )
        assert stub_shared.OUTPUT_REPO_OWNER in gap_only_md
        assert stub_shared.OUTPUT_REPO in gap_only_md

    def test_empty_gaps_string(self, biz_docs):
        """build_full_output handles an empty gaps string without error."""
        full_md, gap_only_md = biz_docs.build_full_output(
            "# Doc", "",
            "acme", "my-repo", "Proj", "1.0.0"
        )
        assert "