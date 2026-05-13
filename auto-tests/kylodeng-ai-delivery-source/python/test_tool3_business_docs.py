"""
Tests for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, delimiter splitting, missing delimiter fallback
- build_full_output(): structure, content inclusion, metadata formatting
- __main__ block behaviour (success path, exception/failure path)
- Edge cases: empty gaps, empty doc, unusual delimiter placement, whitespace handling

Mocks used:
- shared.call_claude (patched to avoid real API calls)
- shared.get_repo_files (patched to avoid real GitHub calls)
- shared.write_output_file (patched to avoid real file writes)
- shared.send_email (patched to avoid real email sending)
- shared.email_html (patched)
- shared.write_audit_entry (patched to avoid real audit writes)
- datetime.datetime.utcnow (patched for deterministic timestamps)
- os.environ (patched for environment variable isolation)

TODOs:
- TODO: Integration test against a real Claude API response shape (need API key + sandbox)
- TODO: Test __main__ block for missing SOURCE_REPO_OWNER / SOURCE_REPO_NAME (None values)
- TODO: Test write_output_file returning different URL shapes
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
# Helpers to import the module under test with all shared deps pre-mocked
# ---------------------------------------------------------------------------

SHARED_MOCK_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. Question one?"),
    "get_repo_files": MagicMock(return_value={"main.py": "print('hello')"}),
    "write_output_file": MagicMock(return_value="https://github.com/output/repo/blob/main/file.md"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>body</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_mock():
    """Return a fresh mock module for `shared`."""
    mod = types.ModuleType("shared")
    for attr, val in SHARED_MOCK_ATTRS.items():
        if callable(val):
            setattr(mod, attr, MagicMock(side_effect=val.side_effect, return_value=val.return_value))
        else:
            setattr(mod, attr, val)
    return mod


def _import_tool(shared_mod=None):
    """
    Import tool3_business_docs with a mocked shared module.
    Re-imports each time to allow per-test mock customisation.
    """
    if shared_mod is None:
        shared_mod = _make_shared_mock()

    # Ensure the scripts directory is importable
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    scripts_dir = os.path.abspath(scripts_dir)

    with mock.patch.dict("sys.modules", {"shared": shared_mod}):
        if "tool3_business_docs" in sys.modules:
            del sys.modules["tool3_business_docs"]

        # Temporarily add scripts dir to path
        old_path = sys.path[:]
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        try:
            import tool3_business_docs as tool
        finally:
            sys.path = old_path

    return tool, shared_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def tool(shared_mock):
    mod, _ = _import_tool(shared_mock)
    return mod


@pytest.fixture()
def tool_and_shared(shared_mock):
    mod, sm = _import_tool(shared_mock)
    return mod, sm


# ---------------------------------------------------------------------------
# generate_biz_doc — happy path
# ---------------------------------------------------------------------------

class TestGenerateBizDocHappyPath:

    def test_returns_two_strings(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "Doc section\n---GAPS---\n1. Who is the sponsor?"
        sm.get_repo_files.return_value = {"main.py": "# code"}

        with patch("datetime.datetime") as dt_mock:
            dt_mock.utcnow.return_value = FIXED_DATE
            dt_mock.utcnow.return_value.strftime = FIXED_DATE.strftime
            doc, gaps = tool.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "http://run")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_splits_on_delimiter(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "DOCUMENT_PART\n---GAPS---\nGAPS_PART"
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "DOCUMENT_PART"
        assert gaps == "GAPS_PART"

    def test_strips_whitespace_from_parts(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "  Doc with spaces  \n---GAPS---\n  Gaps with spaces  "
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "Doc with spaces"
        assert gaps == "Gaps with spaces"

    def test_calls_get_repo_files_with_expected_extensions(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "doc---GAPS---gaps"
        sm.get_repo_files.return_value = {}

        tool.generate_biz_doc("myowner", "myrepo", "P", "2.0", "url")

        sm.get_repo_files.assert_called_once()
        args, kwargs = sm.get_repo_files.call_args
        assert args[0] == "myowner"
        assert args[1] == "myrepo"
        extensions = args[2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions

    def test_calls_get_repo_files_with_max_files_20(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "doc---GAPS---gaps"
        sm.get_repo_files.return_value = {}

        tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        _, kwargs = sm.get_repo_files.call_args
        assert kwargs.get("max_files") == 20

    def test_calls_call_claude_with_owner_repo_in_user_message(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "doc---GAPS---gaps"
        sm.get_repo_files.return_value = {"app.py": "code"}

        tool.generate_biz_doc("acme", "rocket", "Rocket", "3.1", "url")

        _, user_msg = sm.call_claude.call_args[0]
        assert "acme/rocket" in user_msg

    def test_prompt_contains_project_name(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "doc---GAPS---gaps"
        sm.get_repo_files.return_value = {}

        tool.generate_biz_doc("o", "r", "SpecialProject", "1.0", "url")

        system_prompt, _ = sm.call_claude.call_args[0]
        assert "SpecialProject" in system_prompt

    def test_prompt_contains_version(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "doc---GAPS---gaps"
        sm.get_repo_files.return_value = {}

        tool.generate_biz_doc("o", "r", "P", "9.9.9", "url")

        system_prompt, _ = sm.call_claude.call_args[0]
        assert "9.9.9" in system_prompt

    def test_file_contents_truncated_and_included_in_user_message(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "doc---GAPS---gaps"
        long_content = "x" * 5000
        sm.get_repo_files.return_value = {"bigfile.py": long_content}

        tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        _, user_msg = sm.call_claude.call_args[0]
        # File name should appear
        assert "bigfile.py" in user_msg
        # Content should be capped at 3000 chars
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg


# ---------------------------------------------------------------------------
# generate_biz_doc — missing delimiter (fallback)
# ---------------------------------------------------------------------------

class TestGenerateBizDocMissingDelimiter:

    def test_no_delimiter_uses_full_raw_as_doc(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "Just a document with no delimiter at all."
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "Just a document with no delimiter at all."

    def test_no_delimiter_gaps_is_fallback_message(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "Just a document."
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert "manually" in gaps.lower() or "could not" in gaps.lower()

    def test_empty_raw_response(self, tool_and_shared):
        tool, sm = tool_and_shared
        sm.call_claude.return_value = ""
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == ""
        assert isinstance(gaps, str)

    def test_delimiter_at_start(self, tool_and_shared):
        """Delimiter at position 0 → doc is empty string."""
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "---GAPS---\n1. Only gaps here"
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == ""
        assert "Only gaps here" in gaps

    def test_delimiter_at_end(self, tool_and_shared):
        """Delimiter at end → gaps is empty string."""
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "Only doc here\n---GAPS---"
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "Only doc here"
        assert gaps == ""

    def test_multiple_delimiters_splits_on_first(self, tool_and_shared):
        """Only the first ---GAPS--- delimiter should be used."""
        tool, sm = tool_and_shared
        sm.call_claude.return_value = "Part A\n---GAPS---\nPart B\n---GAPS---\nPart C"
        sm.get_repo_files.return_value = {}

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "Part A"
        assert "Part B" in gaps
        assert "Part C" in gaps


# ---------------------------------------------------------------------------
# build_full_output — structure & content
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, tool, doc="## Doc", gaps="1. A question?",
              owner="myowner", repo="myrepo", project="MyProject", version="1.2.3"):
        with patch("datetime.datetime") as dt_mock:
            dt_mock.utcnow.return_value = FIXED_DATE
            # Allow strftime to work normally on the fixed date
            dt_mock.utcnow.return_value.strftime = FIXED_DATE.strftime
            return tool.build_full_output(doc, gaps, owner, repo, project, version)

    def test_returns_two_strings(self, tool):
        result = self._call(tool)
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, tool):
        full_md, _ = self._call(tool, doc="## My Document Section")
        assert "## My Document Section" in full_md

    def test_full_md_contains_gaps(self, tool):
        full_md, _ = self._call(tool, gaps="1. What is the go-live date?")
        assert "1. What is the go-live date?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, tool):
        full_md, _ = self._call(tool)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool):
        full_md, _ = self._call(tool, owner="acme", repo="rocket", version="2.0")
        assert "acme/rocket" in full_md
        assert "2.0" in full_md

    def test_full_md_contains_ai_delivery_bot_signature(self, tool):
        full_md, _ = self._call(tool)
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_in_heading(self, tool):
        _, gap_only_md = self._call(tool, project="SuperProject", version="3.0")
        assert "SuperProject" in gap_only_md
        assert "3.0" in gap_only_md

    def test_gap_only_md_contains_gaps(self, tool):
        _, gap_only_md = self._call(tool, gaps="1. Who owns this?")
        assert "1. Who owns this?" in gap_only_md

    def test_gap_only_md_references_output_repo(self, tool):
        _, gap_only_md = self._call(tool)
        # Should link to the output repo (from shared constants)
        assert "github.com" in gap_only_