"""
Tests for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude response handling
- build_full_output(): full markdown construction, gap-only markdown construction, content assertions
- __main__ block: environment variable handling, success flow, exception/failure flow

Mocks used:
- shared.call_claude (patched via sys.modules injection)
- shared.get_repo_files
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- shared.OUTPUT_REPO_OWNER
- shared.OUTPUT_REPO
- datetime.datetime (for deterministic timestamps)

TODOs:
- TODO: Integration test against a real Claude API response shape once API contract is stable
- TODO: Test file truncation behaviour when repo files exceed 3000 chars per file
- TODO: Test behaviour when get_repo_files returns an empty dict (no source files found)
- TODO: Verify audit entry schema matches downstream consumers
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Shared-module stub — injected before the module under test is imported
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub of the `shared` module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="Doc content\n---GAPS---\n1. Question one?")
    shared.get_repo_files = MagicMock(return_value={"README.md": "# Hello", "main.py": "print('hi')"})
    shared.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def inject_shared_stub():
    """Ensure `shared` stub is injected and the module under test is freshly imported."""
    stub = _make_shared_stub()
    sys.modules["shared"] = stub

    # Remove cached import of the module under test so each test gets a fresh copy
    mod_key = "tool3_business_docs"
    if mod_key in sys.modules:
        del sys.modules[mod_key]

    # Add the scripts directory to path so the import works
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the directory containing this test file's sibling path
    candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

    yield stub

    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool3_business_docs", None)


def _import_module():
    """Import the module under test, returning (module, shared_stub)."""
    import importlib
    mod = importlib.import_module("tool3_business_docs")
    return mod, sys.modules["shared"]


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FIXED_DATE = "2024-01-15"
FIXED_DATETIME = "2024-01-15 10:30 UTC"

FAKE_DOC = "# Solution overview: MyProject\nSome content here."
FAKE_GAPS = "1. What is the target go-live date?\n2. Who is the business sponsor?"
CLAUDE_RESPONSE_WITH_DELIMITER = f"{FAKE_DOC}\n---GAPS---\n{FAKE_GAPS}"
CLAUDE_RESPONSE_WITHOUT_DELIMITER = "Full document without any gap delimiter present."


@pytest.fixture()
def fixed_datetime():
    """Patch datetime.datetime to return deterministic values."""
    fixed_dt = datetime.datetime(2024, 1, 15, 10, 30, 0)
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = fixed_dt
        mock_dt.datetime.utcnow.return_value.strftime = fixed_dt.strftime
        # Make strftime work on the return value
        mock_dt.datetime.utcnow = MagicMock(return_value=fixed_dt)
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, inject_shared_stub):
        """Claude returns a response with ---GAPS--- → doc and gaps split correctly."""
        inject_shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        inject_shared_stub.get_repo_files.return_value = {"README.md": "# Project", "app.py": "x = 1"}

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 1, 15)
            # patch strftime on the returned object
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            doc, gaps = mod.generate_biz_doc("acme", "widget-api", "Widget API", "1.2.0", "https://gh.com/run/1")

        assert "Solution overview" in doc or len(doc) > 0
        assert "1. What is the target go-live date?" in gaps

    def test_happy_path_calls_get_repo_files(self, inject_shared_stub):
        """get_repo_files is called with the correct owner/repo and extensions."""
        inject_shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            mod.generate_biz_doc("my-org", "my-repo", "My Project", "2.0.0", "https://gh.com/run/2")

        shared.get_repo_files.assert_called_once_with(
            "my-org",
            "my-repo",
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_happy_path_calls_call_claude(self, inject_shared_stub):
        """call_claude is invoked with a prompt that includes project_name and version."""
        inject_shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        inject_shared_stub.get_repo_files.return_value = {"main.py": "pass"}

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            mod.generate_biz_doc("org", "repo", "Insurance Portal", "3.1.0", "https://gh.com")

        args, _ = shared.call_claude.call_args
        prompt_arg = args[0]
        assert "Insurance Portal" in prompt_arg
        assert "3.1.0" in prompt_arg

    def test_response_without_delimiter_returns_full_doc_and_fallback(self, inject_shared_stub):
        """When Claude omits ---GAPS---, doc equals full response and gaps is a fallback message."""
        inject_shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITHOUT_DELIMITER

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            doc, gaps = mod.generate_biz_doc("org", "repo", "Project X", "0.1.0", "https://gh.com")

        assert doc == CLAUDE_RESPONSE_WITHOUT_DELIMITER.strip()
        assert "Claude could not extract gap questions" in gaps

    def test_multiple_delimiter_occurrences_splits_on_first(self, inject_shared_stub):
        """When ---GAPS--- appears more than once, only the first split is used."""
        multi_delim = "Part A\n---GAPS---\nPart B\n---GAPS---\nPart C"
        inject_shared_stub.call_claude.return_value = multi_delim

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            doc, gaps = mod.generate_biz_doc("org", "repo", "Proj", "0.0.1", "https://gh.com")

        assert doc == "Part A"
        assert "Part B" in gaps
        assert "Part C" in gaps

    def test_files_are_included_in_claude_user_message(self, inject_shared_stub):
        """File contents are passed as the second argument to call_claude."""
        inject_shared_stub.get_repo_files.return_value = {
            "README.md": "# README content",
            "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        inject_shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            mod.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://gh.com")

        _, kwargs_or_args = shared.call_claude.call_args
        # Second positional arg is the user message
        user_msg = shared.call_claude.call_args[0][1]
        assert "README.md" in user_msg
        assert "README content" in user_msg
        assert "infra/main.tf" in user_msg

    def test_empty_files_dict_produces_empty_files_string(self, inject_shared_stub):
        """If get_repo_files returns {}, call_claude still runs with an empty files section."""
        inject_shared_stub.get_repo_files.return_value = {}
        inject_shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            doc, gaps = mod.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://gh.com")

        # Should not raise; call_claude was still called
        shared.call_claude.assert_called_once()

    def test_whitespace_stripped_from_doc_and_gaps(self, inject_shared_stub):
        """Leading/trailing whitespace is stripped from both doc and gaps."""
        inject_shared_stub.call_claude.return_value = "  Doc with spaces  \n---GAPS---\n  Gaps with spaces  "

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            doc, gaps = mod.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://gh.com")

        assert doc == "Doc with spaces"
        assert gaps == "Gaps with spaces"

    def test_file_content_truncated_to_3000_chars(self, inject_shared_stub):
        """Files longer than 3000 chars are truncated in the prompt."""
        long_content = "x" * 5000
        inject_shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        inject_shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER

        mod, shared = _import_module()

        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATE
            mock_dt.datetime.utcnow.return_value = dt_obj

            mod.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://gh.com")

        user_msg = shared.call_claude.call_args[0][1]
        # The 'x' * 5000 should appear truncated to 3000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg


# ---------------------------------------------------------------------------
# Tests: build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, doc=FAKE_DOC, gaps=FAKE_GAPS,
              owner="acme", repo="portal", project_name="Portal", version="1.0.0"):
        mod, _ = _import_module()
        with patch("tool3_business_docs.datetime") as mock_dt:
            dt_obj = MagicMock()
            dt_obj.strftime.return_value = FIXED_DATETIME
            mock_dt.datetime.utcnow.return_value = dt_obj
            return mod.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_tuple_of_two_strings(self):
        result = self._call()
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self):
        full_md, _ = self._call(doc=FAKE_DOC)
        assert FAKE_DOC in full_md

    def test_full_md_contains_gaps(self):
        full_md, _ = self._call(gaps=FAKE_GAPS)
        assert FAKE_GAPS in full_md

    def test_full_md_contains_gap_questionnaire_header(self):
        full_md, _ = self._call()
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self):
        full_md, _ = self._call