"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): orchestrates file fetching, Claude call, and response splitting
    - build_full_output(): assembles final markdown documents from doc/gaps parts

Mocks used:
    - shared.call_claude          — prevents real API calls to Anthropic/Claude
    - shared.get_repo_files       — prevents real GitHub API calls
    - shared.write_output_file    — prevents real GitHub commits
    - shared.send_email           — prevents real email dispatch
    - shared.email_html           — prevents real HTML templating side-effects
    - shared.write_audit_entry    — prevents real audit log writes
    - datetime.datetime.utcnow    — pins timestamps for deterministic assertions
    - os.environ                  — controls environment variable injection

TODOs:
    - TODO: Integration test covering the full __main__ block end-to-end with
            all mocks wired together (requires a richer shared module stub).
    - TODO: Test behaviour when get_repo_files returns binary/non-UTF8 content.
    - TODO: Test Claude response containing multiple '---GAPS---' delimiters.
"""

import sys
import os
import types
import importlib
from unittest.mock import patch, MagicMock, call
import datetime

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool3_business_docs
# does not require the real implementation to be importable.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    stub.get_repo_files = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value="https://github.com/output/file")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "test-owner"
    stub.OUTPUT_REPO = "test-output-repo"
    return stub


# Insert the stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test (may already be cached)
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool3_business_docs.py"

# Load the module directly from the file path so we don't rely on it being on
# sys.path, and so we can reload it cleanly per-session.
_spec = importlib.util.spec_from_file_location("tool3_business_docs", str(_SCRIPT_PATH))
_mod  = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

generate_biz_doc  = _mod.generate_biz_doc
build_full_output = _mod.build_full_output

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

OWNER        = "acme-corp"
REPO         = "underwriting-risk"
PROJECT_NAME = "Underwriting Risk Classification"
VERSION      = "1.2.3"
RUN_URL      = "https://github.com/actions/run/42"

_FIXED_DATE     = "2024-06-15"
_FIXED_DATETIME = "2024-06-15 10:30 UTC"

_FAKE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent"}}',
    "README.md": "# Underwriting Risk\n\nThis project classifies insurance risk.",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-stub mocks between tests."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.email_html.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()

    # Restore sensible defaults
    _shared_stub.call_claude.return_value = "doc content\n---GAPS---\n1. A question?"
    _shared_stub.get_repo_files.return_value = {}
    _shared_stub.write_output_file.return_value = "https://github.com/output/file"
    yield


@pytest.fixture()
def fixed_utcnow_date():
    """Patch datetime.datetime.utcnow to return a fixed date for generate_biz_doc."""
    fixed = datetime.datetime(2024, 6, 15, 10, 30, 0)
    with patch.object(_mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
        mock_dt.utcnow.return_value = fixed
        yield fixed


@pytest.fixture()
def fixed_utcnow_full():
    """Patch datetime.datetime.utcnow to return a fixed datetime for build_full_output."""
    fixed = datetime.datetime(2024, 6, 15, 10, 30, 0)
    with patch.object(_mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
        mock_dt.utcnow.return_value = fixed
        yield fixed


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    # --- Happy path ---

    def test_returns_tuple_of_two_strings(self):
        result = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert isinstance(result, tuple)
        assert len(result) == 2
        doc, gaps = result
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_calls_get_repo_files_with_correct_extensions(self):
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        _shared_stub.get_repo_files.assert_called_once()
        args, kwargs = _shared_stub.get_repo_files.call_args
        assert args[0] == OWNER
        assert args[1] == REPO
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert args[2] == expected_exts
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_calls_claude_once(self):
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert _shared_stub.call_claude.call_count == 1

    def test_claude_prompt_contains_project_name(self):
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert PROJECT_NAME in prompt_arg

    def test_claude_prompt_contains_version(self):
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert VERSION in prompt_arg

    def test_claude_user_message_contains_repo_identifier(self):
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        user_msg = _shared_stub.call_claude.call_args[0][1]
        assert f"{OWNER}/{REPO}" in user_msg

    def test_splits_on_gaps_delimiter(self):
        _shared_stub.call_claude.return_value = (
            "# Solution Overview\nSome doc text.\n---GAPS---\n1. What is the go-live date?"
        )
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert "# Solution Overview" in doc
        assert "1. What is the go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_doc_part_is_stripped(self):
        _shared_stub.call_claude.return_value = "   doc text   \n---GAPS---\n   gaps text   "
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_gaps_part_is_stripped(self):
        _shared_stub.call_claude.return_value = "doc\n---GAPS---\n   1. Question?   "
        _, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert gaps == gaps.strip()

    # --- Edge cases ---

    def test_no_gaps_delimiter_returns_full_raw_as_doc(self):
        _shared_stub.call_claude.return_value = "Just a doc with no delimiter at all."
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert doc == "Just a doc with no delimiter at all."

    def test_no_gaps_delimiter_returns_fallback_gaps_message(self):
        _shared_stub.call_claude.return_value = "Just a doc with no delimiter at all."
        _, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert "Claude could not extract gap questions" in gaps

    def test_empty_claude_response(self):
        _shared_stub.call_claude.return_value = ""
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        # doc is empty string (stripped "")
        assert doc == ""
        assert "Claude could not extract gap questions" in gaps

    def test_delimiter_only_response(self):
        _shared_stub.call_claude.return_value = "---GAPS---"
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert doc == ""
        assert gaps == ""

    def test_multiple_files_joined_in_prompt(self):
        _shared_stub.get_repo_files.return_value = _FAKE_FILES
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        user_msg = _shared_stub.call_claude.call_args[0][1]
        for path in _FAKE_FILES:
            assert path in user_msg

    def test_file_content_truncated_to_3000_chars(self):
        long_content = "x" * 5000
        _shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        user_msg = _shared_stub.call_claude.call_args[0][1]
        # The truncated content (3000 x's) should appear; 5000 x's should not appear as a run
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files(self):
        _shared_stub.get_repo_files.return_value = {}
        # Should not raise
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_date_appears_in_prompt(self, fixed_utcnow_date):
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert _FIXED_DATE in prompt_arg

    # --- Error conditions ---

    def test_propagates_call_claude_exception(self):
        _shared_stub.call_claude.side_effect = RuntimeError("Claude API down")
        with pytest.raises(RuntimeError, match="Claude API down"):
            generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    def test_propagates_get_repo_files_exception(self):
        _shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    # --- Boundary values ---

    def test_splits_only_on_first_delimiter_occurrence(self):
        """Only the first ---GAPS--- should be used as a split point."""
        _shared_stub.call_claude.return_value = (
            "doc part\n---GAPS---\ngaps part\n---GAPS---\nextra content"
        )
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert doc == "doc part"
        # Everything after the first delimiter ends up in gaps
        assert "gaps part" in gaps
        assert "extra content" in gaps

    def test_version_zero_point_one(self):
        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, "0.1.0", RUN_URL)
        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert "0.1.0" in prompt_arg

    def test_unicode_project_name(self):
        unicode_name = "Über-Zeichnung Risiko 日本語"
        _shared_stub.call_claude.return_value = f"# {unicode_name}\n---GAPS---\n1. Question?"
        doc, gaps = generate_biz_doc(OWNER, REPO, unicode_name, VERSION, RUN_URL)
        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ===========================================================================
# Tests for build_full_output
# ===========================================================================

class TestBuildFullOutput:

    DOC_TEXT  = "# Solution Overview\nSome content here."
    GAPS_TEXT = "1. What is the go-live date?\n2. Who are the key users?"

    def _call(self, doc=None, gaps=None, owner=OWNER, repo=REPO,
              project_name=PROJECT_NAME, version=VERSION):
        doc  = doc  if doc  is not None else self.DOC_TEXT
        gaps = gaps if gaps is not None else self.GAPS_TEXT
        return build_full_output(doc, gaps, owner, repo, project_name, version)

    # --- Return type ---

    def test_returns_tuple_of_two_strings(self):
        result = self._call()
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    # --- full_md content ---

    def test_full_md_contains_doc_text(self):
        full_md, _ = self._call()
        assert self.DOC_TEXT in full_md

    def test_full_md_contains_gaps_text(self):
        full_md, _ = self._call()
        assert self.GAPS_TEXT in full_md

    def test_full_md_contains_gap_questionnaire_heading(self):
        full_md, _ = self._