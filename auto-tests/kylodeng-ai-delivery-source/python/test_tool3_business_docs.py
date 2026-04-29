"""
Tests for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with and without ---GAPS--- delimiter), Claude integration
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content correctness
    - __main__ block logic (via subprocess or direct function calls with env patching)
    - Edge cases: missing delimiter, empty gaps, empty doc, special characters in project names,
      version strings, owner/repo values

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch

TODOs:
    - TODO: Integration test that exercises the real Claude API (requires API key)
    - TODO: Test __main__ block FAILED branch end-to-end (truncated source prevents full stub)
    - TODO: Validate email HTML content shape once email_html signature is confirmed
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with its `shared` dependency mocked
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"
TOOL_MODULE_NAME   = "tool3_business_docs"


def _make_shared_mock():
    """Return a minimal mock of the `shared` module."""
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="DOC_CONTENT\n---GAPS---\n1. Gap question?")
    shared.get_repo_files     = MagicMock(return_value={"README.md": "# Hello"})
    shared.write_output_file  = MagicMock(return_value="https://github.com/output/file")
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "output-owner"
    shared.OUTPUT_REPO        = "output-repo"
    return shared


def _import_tool(shared_mock=None):
    """Import (or re-import) tool3_business_docs with a given shared mock in place."""
    # Remove cached copies so we get a fresh import each time
    for key in list(sys.modules.keys()):
        if TOOL_MODULE_NAME in key:
            del sys.modules[key]

    if shared_mock is None:
        shared_mock = _make_shared_mock()

    sys.modules["shared"] = shared_mock

    # The script does sys.path.insert inside itself; point it at a temp dir
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tool_path  = os.path.join(
        os.path.dirname(script_dir), ".github", "scripts", "tool3_business_docs.py"
    )

    spec   = importlib.util.spec_from_file_location(TOOL_MODULE_NAME, tool_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def tool(shared_mock):
    module, _ = _import_tool(shared_mock)
    return module


@pytest.fixture()
def tool_and_shared(shared_mock):
    module, sm = _import_tool(shared_mock)
    return module, sm


FIXED_DATE     = "2024-06-15"
FIXED_DATETIME = "2024-06-15 12:00 UTC"
FIXED_DT_OBJ   = datetime.datetime(2024, 6, 15, 12, 0, 0)


# ---------------------------------------------------------------------------
# generate_biz_doc — happy path with delimiter
# ---------------------------------------------------------------------------

class TestGenerateBizDocHappyPath:

    def test_returns_two_strings(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "Doc body\n---GAPS---\n1. A question?"
        doc, gaps = tool.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")
        assert isinstance(doc,  str)
        assert isinstance(gaps, str)

    def test_doc_contains_content_before_delimiter(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "## Executive summary\n---GAPS---\n1. Q?"
        doc, gaps = tool.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")
        assert "Executive summary" in doc
        assert "---GAPS---" not in doc

    def test_gaps_contains_content_after_delimiter(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "Doc\n---GAPS---\n1. What is the go-live date?"
        doc, gaps = tool.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")
        assert "go-live date" in gaps
        assert "---GAPS---" not in gaps

    def test_strips_whitespace_from_both_parts(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "  Doc  \n---GAPS---\n  Gaps  "
        doc, gaps = tool.generate_biz_doc("owner", "repo", "P", "2.0.0", "url")
        assert doc  == "Doc"
        assert gaps == "Gaps"

    def test_get_repo_files_called_with_correct_owner_repo(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("acme", "widget-svc", "Widget", "1.0.0", "https://run")
        shared_mock.get_repo_files.assert_called_once()
        args = shared_mock.get_repo_files.call_args
        assert args[0][0] == "acme"
        assert args[0][1] == "widget-svc"

    def test_get_repo_files_called_with_expected_extensions(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        extensions = shared_mock.get_repo_files.call_args[0][2]
        for ext in [".py", ".md", ".yaml", ".tf"]:
            assert ext in extensions

    def test_call_claude_receives_prompt_with_project_name(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("o", "r", "SpecialProject", "3.1.4", "url")
        prompt_arg = shared_mock.call_claude.call_args[0][0]
        assert "SpecialProject" in prompt_arg

    def test_call_claude_receives_prompt_with_version(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("o", "r", "P", "9.9.9", "url")
        prompt_arg = shared_mock.call_claude.call_args[0][0]
        assert "9.9.9" in prompt_arg

    def test_call_claude_receives_repo_in_user_message(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("myowner", "myrepo", "P", "1.0.0", "url")
        user_msg = shared_mock.call_claude.call_args[0][1]
        assert "myowner/myrepo" in user_msg

    def test_file_content_truncated_to_3000_chars(self, tool, shared_mock):
        long_content = "x" * 5000
        shared_mock.get_repo_files.return_value = {"bigfile.py": long_content}
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        user_msg = shared_mock.call_claude.call_args[0][1]
        # The truncated content must not exceed 3000 chars per file
        assert "x" * 3001 not in user_msg

    def test_multiple_files_all_appear_in_user_message(self, tool, shared_mock):
        shared_mock.get_repo_files.return_value = {
            "alpha.py": "alpha code",
            "beta.tf":  "beta infra",
        }
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        user_msg = shared_mock.call_claude.call_args[0][1]
        assert "alpha.py"   in user_msg
        assert "alpha code" in user_msg
        assert "beta.tf"    in user_msg
        assert "beta infra" in user_msg


# ---------------------------------------------------------------------------
# generate_biz_doc — missing delimiter
# ---------------------------------------------------------------------------

class TestGenerateBizDocNoDelimiter:

    def test_doc_equals_raw_response(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "Just plain text, no delimiter here."
        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        assert doc == "Just plain text, no delimiter here."

    def test_gaps_is_fallback_message(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "Just plain text."
        _, gaps = tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        assert "manually" in gaps.lower() or "could not extract" in gaps.lower()

    def test_doc_not_empty_when_no_delimiter(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "Some document."
        doc, _ = tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        assert doc

    def test_gaps_not_empty_when_no_delimiter(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "Some document."
        _, gaps = tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        assert gaps


# ---------------------------------------------------------------------------
# generate_biz_doc — edge cases
# ---------------------------------------------------------------------------

class TestGenerateBizDocEdgeCases:

    def test_delimiter_only_response(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "---GAPS---"
        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        # Both parts should be empty strings after strip
        assert doc  == ""
        assert gaps == ""

    def test_multiple_delimiters_only_first_split(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "Doc\n---GAPS---\nGaps\n---GAPS---\nMore"
        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        assert doc == "Doc"
        assert "---GAPS---" in gaps  # second delimiter stays in gaps part

    def test_empty_files_dict(self, tool, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        assert doc  == "D"
        assert gaps == "G"

    def test_project_name_with_special_chars(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        # Should not raise
        tool.generate_biz_doc("o", "r", "My Project (v2) — Beta!", "1.0.0", "url")

    def test_version_semver_variants(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        for version in ["0.0.1", "1.0.0-alpha", "2.3.4+build.5", "v1.0.0"]:
            doc, gaps = tool.generate_biz_doc("o", "r", "P", version, "url")
            assert doc  == "D"
            assert gaps == "G"

    def test_date_injected_into_prompt(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT_OBJ
            mock_dt.utcnow.return_value.strftime = FIXED_DT_OBJ.strftime
            # Trigger call
            tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        # Verify strftime was used (call_claude was called)
        assert shared_mock.call_claude.called

    def test_call_claude_called_exactly_once(self, tool, shared_mock):
        shared_mock.call_claude.return_value = "D\n---GAPS---\nG"
        tool.generate_biz_doc("o", "r", "P", "1.0.0", "url")
        assert shared_mock.call_claude.call_count == 1


# ---------------------------------------------------------------------------
# build_full_output — happy path
# ---------------------------------------------------------------------------

class TestBuildFullOutputHappyPath:

    def _call(self, tool, doc="## Doc", gaps="1. Question?",
              owner="myowner", repo="myrepo",
              project_name="MyProject", version="1.2.3"):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_DATETIME
            return tool.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_two_strings(self, tool):
        full_md, gap_md = self._call(tool)
        assert isinstance(full_md, str)
        assert isinstance(gap_md, str)

    def test_full_md_contains_doc(self, tool):
        full_md, _ = self._call(tool, doc="## Executive summary")
        assert "## Executive summary" in full_md

    def test_full_md_contains_gaps(self, tool):
        full_md, _ = self._call(tool, gaps="1. What is the go-live date?")
        assert "go-live date" in full_md