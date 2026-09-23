"""
Test module for .github/scripts/tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, Claude response variants
    - build_full_output(): happy path, content structure, metadata injection, edge cases
    - __main__ block behaviour via subprocess / env-var injection (stubbed)
    - Boundary values: empty gaps, empty doc, long content, special characters in project_name/version

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (fixed timestamp)

TODOs:
    # TODO: Integration test against a real (sandboxed) Claude endpoint — skipped here
    # TODO: Test __main__ block end-to-end with subprocess once env scaffolding is available
    # TODO: Verify write_output_file path construction matches downstream consumers
"""

import sys
import os
import importlib
import types
from unittest.mock import patch, MagicMock, call
import datetime
import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool3_business_docs
# does not require the real file or its dependencies.
# ---------------------------------------------------------------------------

SHARED_STUB_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. Question one?"),
    "get_repo_files": MagicMock(return_value={"README.md": "# Hello world"}),
    "write_output_file": MagicMock(return_value="https://github.com/output/repo/blob/main/file.md"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>mock</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_stub() -> types.ModuleType:
    mod = types.ModuleType("shared")
    for attr, val in SHARED_STUB_ATTRS.items():
        setattr(mod, attr, val)
    return mod


@pytest.fixture(autouse=True)
def shared_stub(monkeypatch):
    """Inject a fresh shared stub into sys.modules before each test."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)
    # Also ensure the scripts directory path manipulation works
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    monkeypatch.syspath_prepend(scripts_dir)
    return stub


@pytest.fixture
def tool(shared_stub):
    """Import (or re-import) the module under test with the stub in place."""
    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    # Point the import machinery at the actual source file
    source_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
    )
    spec = importlib.util.spec_from_file_location(mod_name, source_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic tests
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 10, 30, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 10:30 UTC"


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:
    """Tests for the generate_biz_doc function."""

    @patch("datetime.datetime")
    def test_happy_path_returns_doc_and_gaps(self, mock_dt, tool, shared_stub):
        """Claude returns a well-formed response with the delimiter."""
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime

        shared_stub.call_claude.return_value = (
            "## Solution Overview\nSome content here."
            "\n---GAPS---\n"
            "1. What is the go-live date?\n2. Who are the key users?"
        )
        shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }

        doc, gaps = tool.generate_biz_doc(
            "acme-corp", "underwriting-app", "Underwriting Risk Classification", "1.0.0", "https://run.url"
        )

        assert "Solution Overview" in doc
        assert "go-live date" in gaps
        assert "key users" in gaps
        # Ensure the delimiter itself is not in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    @patch("datetime.datetime")
    def test_missing_delimiter_returns_raw_and_fallback(self, mock_dt, tool, shared_stub):
        """When Claude omits the delimiter, doc_part = full response, gaps = fallback message."""
        mock_dt.utcnow.return_value = FIXED_DT

        shared_stub.call_claude.return_value = "## Solution Overview\nNo delimiter here."
        shared_stub.get_repo_files.return_value = {"app.py": "# code"}

        doc, gaps = tool.generate_biz_doc(
            "acme", "repo", "My Project", "0.1.0", "https://run"
        )

        assert "Solution Overview" in doc
        assert "Claude could not extract" in gaps

    @patch("datetime.datetime")
    def test_get_repo_files_called_with_correct_extensions(self, mock_dt, tool, shared_stub):
        """Verify that get_repo_files is called with expected file-extension filter."""
        mock_dt.utcnow.return_value = FIXED_DT
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"
        shared_stub.get_repo_files.return_value = {}

        tool.generate_biz_doc("owner", "repo", "proj", "0.2.0", "url")

        shared_stub.get_repo_files.assert_called_once()
        args, kwargs = shared_stub.get_repo_files.call_args
        # First positional: owner, second: repo, third: extensions list
        extensions_arg = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions_arg
        assert ".tf" in extensions_arg
        assert ".md" in extensions_arg

    @patch("datetime.datetime")
    def test_call_claude_receives_formatted_prompt(self, mock_dt, tool, shared_stub):
        """Ensure project_name and version are injected into the prompt."""
        mock_dt.utcnow.return_value = FIXED_DT

        shared_stub.get_repo_files.return_value = {"tf/main.tf": "resource {}"}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool.generate_biz_doc(
            "owner", "repo", "Underwriting Risk Classification", "3.2.1", "url"
        )

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "Underwriting Risk Classification" in prompt_arg
        assert "3.2.1" in prompt_arg

    @patch("datetime.datetime")
    def test_multiple_gaps_delimiters_only_first_split(self, mock_dt, tool, shared_stub):
        """Only the first ---GAPS--- should split the response."""
        mock_dt.utcnow.return_value = FIXED_DT
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = (
            "Part A\n---GAPS---\nPart B\n---GAPS---\nPart C"
        )

        doc, gaps = tool.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "Part A"
        # gaps should include everything after the first delimiter
        assert "Part B" in gaps
        assert "Part C" in gaps

    @patch("datetime.datetime")
    def test_empty_file_list_from_repo(self, mock_dt, tool, shared_stub):
        """Should not crash when get_repo_files returns an empty dict."""
        mock_dt.utcnow.return_value = FIXED_DT
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool.generate_biz_doc("o", "r", "proj", "1.0", "url")

        assert doc == "doc"
        assert gaps == "gaps"

    @patch("datetime.datetime")
    def test_file_content_truncated_at_3000_chars(self, mock_dt, tool, shared_stub):
        """File content should be truncated to 3000 chars before sending to Claude."""
        mock_dt.utcnow.return_value = FIXED_DT
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool.generate_biz_doc("o", "r", "proj", "1.0", "url")

        user_message = shared_stub.call_claude.call_args[0][1]
        # The truncated content (3000 x's) should appear; 5000 x's should not
        assert "x" * 3000 in user_message
        assert "x" * 3001 not in user_message

    @patch("datetime.datetime")
    def test_whitespace_stripped_from_parts(self, mock_dt, tool, shared_stub):
        """Leading/trailing whitespace should be stripped from both parts."""
        mock_dt.utcnow.return_value = FIXED_DT
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "  \n  doc content  \n  ---GAPS---  \n  gaps content  \n  "

        doc, gaps = tool.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "doc content"
        assert gaps == "gaps content"

    @patch("datetime.datetime")
    def test_call_claude_propagates_exception(self, mock_dt, tool, shared_stub):
        """If call_claude raises, the exception should propagate out."""
        mock_dt.utcnow.return_value = FIXED_DT
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            tool.generate_biz_doc("o", "r", "p", "v", "u")

    @pytest.mark.skip(reason="TODO: Integration test requires live Claude API credentials")
    def test_integration_with_real_claude(self):
        """TODO: End-to-end test against a sandboxed Claude endpoint."""
        pass


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================


class TestBuildFullOutput:
    """Tests for the build_full_output function."""

    @patch("datetime.datetime")
    def test_returns_two_strings(self, mock_dt, tool):
        mock_dt.utcnow.return_value = FIXED_DT

        result = tool.build_full_output(
            "## Doc", "1. Question?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    @patch("datetime.datetime")
    def test_full_md_contains_doc_content(self, mock_dt, tool):
        mock_dt.utcnow.return_value = FIXED_DT

        full_md, _ = tool.build_full_output(
            "## Executive Summary\nContent here.",
            "1. Gap question?",
            "owner", "repo", "TestProj", "2.0.0"
        )

        assert "## Executive Summary" in full_md
        assert "Content here." in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_gaps(self, mock_dt, tool):
        mock_dt.utcnow.return_value = FIXED_DT

        full_md, _ = tool.build_full_output(
            "Doc content",
            "1. What is the go-live date?\n2. Who owns this?",
            "owner", "repo", "TestProj", "2.0.0"
        )

        assert "What is the go-live date?" in full_md
        assert "Who owns this?" in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_gap_questionnaire_section_header(self, mock_dt, tool):
        mock_dt.utcnow.return_value = FIXED_DT

        full_md, _ = tool.build_full_output(
            "Doc", "1. Q?", "owner", "repo", "Proj", "1.0.0"
        )

        assert "Gap Questionnaire" in full_md

    @patch("datetime.datetime")
    def test_full_md_contains_source_metadata(self, mock_dt, tool):
        mock_dt.utcnow.return_value = FIXED_DT

        full_md, _ = tool.build_full_output(
            "Doc", "Gaps", "acme", "underwriting-app", "Underwriting", "3.0.0"
        )

        assert "acme/underwriting-app" in full_md
        assert "v3.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    @patch("datetime.datetime")
    def test_gap_only_md_contains_project_name_and_version(self, mock_dt, tool):
        mock_dt.utcnow.return_value = FIXED_DT

        _, gap_only_md = tool.build_full_output(
            "Doc", "1. Timeline?\n2. Budget?",
            "owner", "repo", "Underwriting Risk Classification", "1.2.3"
        )

        assert "Underwriting Risk Classification" in gap_only_md
        assert "v1.2.3" in gap_only_md

    @patch("datetime.datetime")
    def test_gap_only_md_contains_gap_questions(self, mock_dt, tool):
        mock_dt.utcnow.return_value = FIXED_DT

        _, gap_only_md = tool.build_full_output(
            "Doc",
            "1. What is the target go-live date?\n2. Who is the sponsor?",
            "owner", "repo", "Proj", "1.0.0"
        )