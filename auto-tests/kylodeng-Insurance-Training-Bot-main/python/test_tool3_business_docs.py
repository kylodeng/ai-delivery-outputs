"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path with ---GAPS--- delimiter, missing delimiter fallback
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content correctness
    - __main__ block logic (via subprocess / importlib patching): env var handling, success path,
      exception/failure path
    - Boundary values: empty gaps string, gaps with multiple newlines, very long doc content
    - Edge cases: missing ---GAPS--- in Claude response, project_name defaulting to repo name

Mocks used:
    - shared.call_claude          — patched to return controlled strings
    - shared.get_repo_files       — patched to return a dict of fake file contents
    - shared.write_output_file    — patched to return a fake URL
    - shared.send_email           — patched to a no-op
    - shared.email_html           — patched to return a stub HTML string
    - shared.write_audit_entry    — patched to a no-op
    - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
    - TODO: Integration test against a real Claude API key (requires secret injection)
    - TODO: Test __main__ block in full via subprocess once the truncated source is complete
    - TODO: Validate email HTML structure produced by real email_html helper
"""

import datetime
import importlib
import sys
import os
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared fully mocked out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"

STUB_SHARED_ATTRS = {
    "call_claude": MagicMock(),
    "get_repo_files": MagicMock(),
    "write_output_file": MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md"),
    "send_email": MagicMock(),
    "email_html": MagicMock(return_value="<html>stub</html>"),
    "write_audit_entry": MagicMock(),
    "OUTPUT_REPO_OWNER": FAKE_OUTPUT_REPO_OWNER,
    "OUTPUT_REPO": FAKE_OUTPUT_REPO,
}


def _make_shared_module() -> types.ModuleType:
    """Create a fake 'shared' module in sys.modules."""
    mod = types.ModuleType("shared")
    for k, v in STUB_SHARED_ATTRS.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def mock_shared(monkeypatch):
    """
    Install a fresh fake 'shared' module before every test and reset all mocks.
    Also ensures tool3_business_docs is re-imported from scratch so it picks up
    the fake shared module.
    """
    shared_mod = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)

    # Remove cached version of the module under test so the import runs fresh
    sys.modules.pop("tool3_business_docs", None)

    # Make sure the scripts directory is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        monkeypatch.syspath_prepend(scripts_dir)

    yield shared_mod

    # Reset all mock call histories
    for attr in STUB_SHARED_ATTRS.values():
        if callable(getattr(attr, "reset_mock", None)):
            attr.reset_mock()


def _import_module(mock_shared_mod):
    """Import tool3_business_docs, injecting the mock shared module."""
    # Patch sys.path so the relative import resolves
    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               ".github", "scripts")
    with mock.patch.dict(sys.modules, {"shared": mock_shared_mod}):
        spec = importlib.util.spec_from_file_location(
            "tool3_business_docs",
            os.path.join(scripts_dir, "tool3_business_docs.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["tool3_business_docs"] = module
        spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_DATE = "2024-06-01"
FAKE_DATETIME = "2024-06-01 12:00 UTC"
FAKE_DATETIME_OBJ = datetime.datetime(2024, 6, 1, 12, 0, 0)

SAMPLE_DOC_PART = """# Solution overview: MyProject
**Version:** 1.2.3 | **Date:** 2024-06-01 | **Status:** Draft

## Executive summary
This solution solves X for Y by doing Z.
"""

SAMPLE_GAPS_PART = """1. Who is the primary business sponsor?
2. What is the target go-live date?
3. What are the data retention requirements?"""

SAMPLE_RAW_WITH_DELIMITER = f"{SAMPLE_DOC_PART}\n---GAPS---\n{SAMPLE_GAPS_PART}"
SAMPLE_RAW_WITHOUT_DELIMITER = SAMPLE_DOC_PART

FAKE_FILES = {
    "main.py": "def main(): pass",
    "README.md": "# My Project\nThis project does things.",
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}',
}


@pytest.fixture()
def mod(mock_shared):
    return _import_module(mock_shared)


@pytest.fixture()
def frozen_datetime():
    """Patch datetime.datetime to return a fixed value."""
    with patch("tool3_business_docs.datetime") as dt_mock:
        dt_mock.datetime.utcnow.return_value = FAKE_DATETIME_OBJ
        dt_mock.datetime.utcnow.return_value.strftime = FAKE_DATETIME_OBJ.strftime
        # Make strftime work properly on the return value
        dt_mock.datetime.utcnow.return_value = FAKE_DATETIME_OBJ
        yield dt_mock


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, mod, mock_shared):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        mock_shared.get_repo_files.return_value = FAKE_FILES
        mock_shared.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        doc, gaps = mod.generate_biz_doc("acme", "my-repo", "MyProject", "1.2.3", "https://run.url")

        assert doc == SAMPLE_DOC_PART.strip()
        assert gaps == SAMPLE_GAPS_PART.strip()

    def test_no_delimiter_fallback(self, mod, mock_shared):
        """When Claude omits ---GAPS---, gaps gets a fallback message."""
        mock_shared.get_repo_files.return_value = FAKE_FILES
        mock_shared.call_claude.return_value = SAMPLE_RAW_WITHOUT_DELIMITER

        doc, gaps = mod.generate_biz_doc("acme", "my-repo", "MyProject", "1.2.3", "https://run.url")

        assert doc == SAMPLE_RAW_WITHOUT_DELIMITER.strip()
        assert "could not extract gap questions" in gaps
        assert "review the document manually" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, mod, mock_shared):
        """get_repo_files must be called with the expected extensions."""
        mock_shared.get_repo_files.return_value = {}
        mock_shared.call_claude.return_value = "---GAPS---"

        mod.generate_biz_doc("owner", "repo", "proj", "0.1.0", "url")

        call_args = mock_shared.get_repo_files.call_args
        positional_args = call_args[0]
        assert positional_args[0] == "owner"
        assert positional_args[1] == "repo"
        exts = positional_args[2]
        for expected_ext in [".py", ".tf", ".md", ".yaml"]:
            assert expected_ext in exts

    def test_call_claude_receives_project_name_and_version(self, mod, mock_shared):
        """The prompt passed to Claude must contain project_name and version."""
        mock_shared.get_repo_files.return_value = {}
        mock_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        mod.generate_biz_doc("owner", "repo", "InsurancePortal", "2.0.1", "url")

        prompt_arg = mock_shared.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg
        assert "2.0.1" in prompt_arg

    def test_file_contents_truncated_to_3000_chars(self, mod, mock_shared):
        """Files longer than 3000 chars should be truncated in the prompt."""
        long_content = "x" * 5000
        mock_shared.get_repo_files.return_value = {"bigfile.py": long_content}
        mock_shared.call_claude.return_value = "---GAPS---"

        mod.generate_biz_doc("o", "r", "p", "v", "u")

        user_message = mock_shared.call_claude.call_args[0][1]
        # The truncated content (3000 chars) should appear, but not 5000 chars
        assert "x" * 3000 in user_message
        assert "x" * 3001 not in user_message

    def test_multiple_files_joined_in_prompt(self, mod, mock_shared):
        """All returned files should appear in the Claude user message."""
        mock_shared.get_repo_files.return_value = {
            "a.py": "content_a",
            "b.tf": "content_b",
        }
        mock_shared.call_claude.return_value = "---GAPS---"

        mod.generate_biz_doc("o", "r", "p", "v", "u")

        user_message = mock_shared.call_claude.call_args[0][1]
        assert "content_a" in user_message
        assert "content_b" in user_message

    def test_empty_repo_files(self, mod, mock_shared):
        """Empty file dict should not crash; Claude is still called."""
        mock_shared.get_repo_files.return_value = {}
        mock_shared.call_claude.return_value = "Just a doc.\n---GAPS---\n1. Question?"

        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert "Just a doc." in doc
        assert "1. Question?" in gaps

    def test_delimiter_appears_multiple_times_only_first_split(self, mod, mock_shared):
        """Only the first ---GAPS--- should be used as delimiter."""
        raw = "doc content\n---GAPS---\nfirst gaps\n---GAPS---\nextra content"
        mock_shared.get_repo_files.return_value = {}
        mock_shared.call_claude.return_value = raw

        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "doc content"
        # Everything after first delimiter is gaps
        assert "first gaps" in gaps
        assert "extra content" in gaps

    def test_whitespace_only_response(self, mod, mock_shared):
        """Whitespace-only Claude response should still not crash."""
        mock_shared.get_repo_files.return_value = {}
        mock_shared.call_claude.return_value = "   \n   "

        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        # No delimiter found → fallback gaps message
        assert "could not extract gap questions" in gaps

    def test_call_claude_exception_propagates(self, mod, mock_shared):
        """Exceptions from call_claude must propagate to the caller."""
        mock_shared.get_repo_files.return_value = {}
        mock_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            mod.generate_biz_doc("o", "r", "p", "v", "u")

    def test_get_repo_files_max_files_argument(self, mod, mock_shared):
        """get_repo_files should be called with max_files=20."""
        mock_shared.get_repo_files.return_value = {}
        mock_shared.call_claude.return_value = "---GAPS---"

        mod.generate_biz_doc("o", "r", "p", "v", "u")

        call_kwargs = mock_shared.get_repo_files.call_args[1]
        assert call_kwargs.get("max_files") == 20


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_full_md_contains_doc_content(self, mod):
        full_md, _ = mod.build_full_output(
            SAMPLE_DOC_PART, SAMPLE_GAPS_PART,
            "owner", "repo", "MyProject", "1.2.3"
        )
        assert "Solution overview: MyProject" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, mod):
        full_md, _ = mod.build_full_output(
            SAMPLE_DOC_PART, SAMPLE_GAPS_PART,
            "owner", "repo", "MyProject", "1.2.3"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self, mod):
        full_md, _ = mod.build_full_output(
            SAMPLE_DOC_PART, SAMPLE_GAPS_PART,
            "owner", "repo", "MyProject", "1.2.3"
        )
        assert "Who is the primary business sponsor?" in full_md

    def test_full_md_contains_source_attribution(self, mod):
        full_md, _ = mod.build_full_output(
            SAMPLE_DOC_PART, SAMPLE_GAPS_PART,
            "owner", "repo", "MyProject", "1.2.3"
        )
        assert "owner/repo" in full_md
        assert "v1.2.3" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, mod):
        _, gap_only_md = mod.build_full_output(
            SAMPLE_DOC_PART, SAMPLE_GAPS_PART,
            "owner", "repo", "MyProject", "1.2.3"
        )
        assert "MyProject" in gap_only_md
        assert "v1.2.3" in gap_only_md

    def test_gap_only_md_contains_gaps_content(self, mod):
        _, gap_only_md = mod.build_full_output(
            SAMPLE_DOC_PART, SAMPLE_GAPS_PART,
            "owner", "repo", "MyProject", "1.2.3"
        )
        assert "Who is the primary business sponsor?" in gap_only_md
        assert "What is the target go-live date?" in gap_only_md

    def test_gap_only_md_links_to_output_repo(self, mod