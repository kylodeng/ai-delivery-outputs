"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown-fenced input, nested braces, newlines in strings,
      missing JSON, invalid JSON fallback, boundary/edge cases
    - review_pr(): happy path, Claude response handling, comment formatting, return value
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): happy path, missing fields, empty findings, empty iac/positive observations,
      all severity/category combinations

Mocks used:
    - shared.call_claude (patched via sys.modules injection)
    - shared.get_pr_diff
    - shared.get_repo_files
    - shared.post_pr_comment
    - shared.write_output_file
    - shared.send_email
    - shared.email_html
    - shared.write_audit_entry
    - requests (imported in module)

TODOs:
    - TODO: Integration test for __main__ block requires full env var setup and live shared module
    - TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO env vars are absent
    - TODO: Test write_output_file and write_audit_entry interactions once shared module contract is stable
"""

import sys
import os
import json
import types
import importlib
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Shared-module stub — must be injected BEFORE importing the module under test
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude        = MagicMock(return_value="{}")
    stub.get_repo_files     = MagicMock(return_value={})
    stub.get_pr_diff        = MagicMock(return_value="diff content")
    stub.write_output_file  = MagicMock(return_value=None)
    stub.post_pr_comment    = MagicMock(return_value=None)
    stub.send_email         = MagicMock(return_value=None)
    stub.email_html         = MagicMock(return_value="<html/>")
    stub.write_audit_entry  = MagicMock(return_value=None)
    stub.OUTPUT_REPO_OWNER  = "test-owner"
    stub.OUTPUT_REPO        = "test-output-repo"
    stub.GH_HEADERS         = {"Authorization": "Bearer test"}
    stub.GH_API             = "https://api.github.com"
    return stub


# Inject stub before import so tool1_code_review can resolve `from shared import ...`
_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

# Also stub requests at module level so the import inside tool1 doesn't fail
_requests_stub = MagicMock()
sys.modules.setdefault("requests", _requests_stub)

# Now import the module under test
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load via spec so we don't rely on the file being on sys.path
_spec = importlib.util.spec_from_file_location("tool1_code_review", _SCRIPT_PATH)
tool1 = importlib.util.module_from_spec(_spec)
# Provide a minimal __name__ so the if __name__ == "__main__" block is skipped
tool1.__name__ = "tool1_code_review"
_spec.loader.exec_module(tool1)

extract_json    = tool1.extract_json
review_pr       = tool1.review_pr
review_repo     = tool1.review_repo
get_output_url  = tool1.get_output_url
build_report_md = tool1.build_report_md


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-stub mocks between tests."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


MINIMAL_RESULT = {
    "summary": "Overall the code looks fine.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 45,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Bare except clause.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["Clear variable names", "Good docstrings"],
    "iac_findings": ["S3 bucket lacks encryption", "IAM role is too permissive"],
}


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:
    # ---- Happy path --------------------------------------------------------

    def test_plain_json_object(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "   \n"
        result = extract_json(raw)
        assert result["summary"] == "Overall the code looks fine."

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fenced_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd of review."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    # ---- Newline cleanup ---------------------------------------------------

    def test_newline_inside_string_value_cleaned(self):
        # Simulate a raw response that has a literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 50}'
        result = extract_json(raw)
        # After cleanup, parse must succeed and value contains the joined text
        assert result["score"] == 50

    # ---- Edge / boundary cases ---------------------------------------------

    def test_extra_text_before_json(self):
        raw = "Sure! Here you go: " + json.dumps({"score": 99})
        result = extract_json(raw)
        assert result["score"] == 99

    def test_nested_json_objects(self):
        payload = {"outer": {"inner": 1}, "score": 55}
        raw = json.dumps(payload)
        result = extract_json(raw)
        assert result["score"] == 55
        assert result["outer"]["inner"] == 1

    def test_empty_findings_list(self):
        raw = json.dumps({"findings": [], "score": 0})
        result = extract_json(raw)
        assert result["findings"] == []

    def test_score_boundary_zero(self):
        raw = json.dumps({"score": 0})
        result = extract_json(raw)
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        raw = json.dumps({"score": 100})
        result = extract_json(raw)
        assert result["score"] == 100

    # ---- Error / negative cases -------------------------------------------

    def test_no_json_raises_value_error(self):
        raw = "This is just plain text with no JSON at all."
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json(raw)

    def test_malformed_json_raises_value_error(self):
        raw = '{"score": 50, "summary": "broken'
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_array_only_raises_value_error(self):
        # A bare JSON array has no outermost { } so should raise
        with pytest.raises(ValueError):
            extract_json("[1, 2, 3]")

    def test_mismatched_braces_raises_value_error(self):
        raw = '{"score": 50'
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_markdown_fenced_with_bad_json_raises(self):
        raw = "```json\n{bad: json}\n```"
        with pytest.raises(ValueError):
            extract_json(raw)


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:

    def _setup_claude(self, payload: dict):
        _shared_stub.call_claude.return_value = json.dumps(payload)
        _shared_stub.get_pr_diff.return_value = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"

    def test_returns_parsed_result(self):
        self._setup_claude(MINIMAL_RESULT)
        result = review_pr("acme", "myrepo", 42, "https://run.url")
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("acme", "myrepo", 42, "https://run.url")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo", 42)

    def test_calls_call_claude_with_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "my special diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)
        review_pr("acme", "myrepo", 1, "")
        args = _shared_stub.call_claude.call_args
        assert "my special diff" in args[0][1]

    def test_posts_pr_comment(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("acme", "myrepo", 7, "https://run.url")
        assert _shared_stub.post_pr_comment.call_count == 1

    def test_pr_comment_contains_score(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("acme", "myrepo", 7, "")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "80" in comment_text

    def test_pr_comment_contains_recommendation(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("acme", "myrepo", 7, "")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_pr_comment_contains_summary(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("acme", "myrepo", 7, "")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Overall the code looks fine." in comment_text

    def test_pr_comment_contains_findings(self):
        self._setup_claude(FULL_RESULT)
        review_pr("acme", "myrepo", 7, "")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment_text

    def test_pr_comment_no_findings_shows_placeholder(self):
        payload = dict(MINIMAL_RESULT, findings=[])
        self._setup_claude(payload)
        review_pr("acme", "myrepo", 7, "")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_pr_comment_positive_observations(self):
        self._setup_claude(FULL_RESULT)
        review_pr("acme", "myrepo", 7, "")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Clear variable names" in comment_text

    def test_pr_comment_empty_positive_observations_shows_placeholder(self):
        payload = dict(MINIMAL_RESULT, positive_observations=[])
        self._setup_claude(payload)
        review_pr("acme", "myrepo", 7, "")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_result_with_missing_score_key(self):
        payload = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        self._setup_claude(payload)
        result = review_pr("acme", "myrepo", 99, "")
        assert "score" not in result or result.get("score") is None

    def test_passes_correct_owner_repo_pr_to_post_comment(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("myowner", "therepo", 55, "")
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "myowner"
        assert args[1] == "therepo"
        assert args[2] == 55


# ===========================================================================
# review_repo tests
# ===========================================================================

class TestReviewRepo:

    def test_returns_parsed_result(self):
        _shared_stub.get_repo_files.return_value = {"main.py": "print('hello')"}
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)
        result = review_repo("acme", "myrepo", "https://run.url")
        assert result["score"] == 80

    def test_calls_get_repo_files_with_correct_extensions(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)
        review_repo("acme", "my