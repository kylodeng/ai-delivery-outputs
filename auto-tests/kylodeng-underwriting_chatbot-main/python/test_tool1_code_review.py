"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, invalid JSON, edge cases (empty string, only braces, etc.)
- review_pr: happy path, Claude error propagation, comment posting, return value
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction with various owner/repo/label combinations
- build_report_md: happy path, empty findings, empty iac/positive, missing keys,
  all severity/category/recommendation values

Mocks used:
- shared.call_claude (patched via tool1_code_review module)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- requests (not directly called in public functions but imported)

TODOs:
- TODO: Integration test for __main__ block requires real environment variables and
  GitHub credentials — stub tests added with pytest.mark.skip
- TODO: Test email/audit side-effects once those calls are wired into review_pr/review_repo
"""

import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool1_code_review
# does not fail when the real shared.py is absent in the test environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="{}")
    shared.get_repo_files = MagicMock(return_value={})
    shared.get_pr_diff = MagicMock(return_value="diff text")
    shared.write_output_file = MagicMock(return_value=None)
    shared.post_pr_comment = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer fake"}
    shared.GH_API = "https://api.github.com"
    return shared


# Insert stub before the module under test is imported
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib
import tool1_code_review as cr  # noqa: E402  (may need PYTHONPATH set)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Everything looks fine.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_VALID_RESULT = {
    "summary": "Several issues found.",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Function is too long.",
            "recommendation": "Break it into smaller helpers.",
        },
    ],
    "positive_observations": ["Good naming conventions", "Comprehensive docstrings"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role is overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stub mocks between tests."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ---------------------------------------------------------------------------
# Tests: extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:

    def test_plain_json_object(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_with_leading_trailing_whitespace(self):
        raw = "   " + json.dumps(MINIMAL_VALID_RESULT) + "\n  "
        result = cr.extract_json(raw)
        assert result["summary"] == "Everything looks fine."

    def test_markdown_triple_backtick_json_fence(self):
        inner = json.dumps(MINIMAL_VALID_RESULT)
        raw = f"```json\n{inner}\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_triple_backtick_no_language(self):
        inner = json.dumps(MINIMAL_VALID_RESULT)
        raw = f"```\n{inner}\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_with_surrounding_text_finds_braces(self):
        inner = json.dumps(MINIMAL_VALID_RESULT)
        raw = f"Here is the review:\n{inner}\nEnd of review."
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a JSON string where a value has a literal newline
        raw = '{"summary": "This is line one\nand line two", "score": 50, ' \
              '"merge_recommendation": "APPROVE", "findings": [], ' \
              '"positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        assert result["score"] == 50

    def test_full_valid_result_round_trip(self):
        raw = json.dumps(FULL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket lacks versioning"

    def test_raises_value_error_on_empty_string(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_raises_value_error_on_plain_text(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_raises_value_error_on_invalid_json_braces(self):
        raw = "{not valid json: 'at all'}"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_raises_value_error_on_markdown_with_invalid_inner_json(self):
        raw = "```json\n{bad: json}\n```"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_raises_value_error_when_end_before_start(self):
        # } appears before { — no valid block
        raw = "} not a thing {"
        # The rfind(}) will be at index 0, find({) at some later index — end < start
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json(raw)

    def test_empty_findings_list(self):
        data = {**MINIMAL_VALID_RESULT, "findings": []}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_score_boundary_zero(self):
        data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_extra_text_before_json_block(self):
        inner = json.dumps(MINIMAL_VALID_RESULT)
        raw = "Certainly! Here is my analysis:\n" + inner
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_line_null_in_finding(self):
        data = {**FULL_VALID_RESULT}
        raw = json.dumps(data)
        result = cr.extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_markdown_fence_with_extra_whitespace_lines(self):
        inner = json.dumps(MINIMAL_VALID_RESULT)
        raw = f"```json\n\n{inner}\n\n```"
        # After split on first \n the remainder starts with \n{inner}\n\n
        # The function should still find the JSON via brace extraction
        result = cr.extract_json(raw)
        assert result["score"] == 85


# ---------------------------------------------------------------------------
# Tests: review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:

    def _setup_claude(self, result_dict=None):
        data = result_dict or MINIMAL_VALID_RESULT
        _shared_stub.call_claude.return_value = json.dumps(data)
        _shared_stub.get_pr_diff.return_value = "--- a/foo.py\n+++ b/foo.py\n@@ some diff"
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_claude(MINIMAL_VALID_RESULT)
        result = cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_claude()
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo", 7)

    def test_calls_call_claude_with_diff_in_prompt(self):
        self._setup_claude()
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[1]
        assert "some diff" in args[1]

    def test_calls_post_pr_comment(self):
        self._setup_claude()
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "acme"
        assert args[1] == "myrepo"
        assert args[2] == 7

    def test_comment_contains_score(self):
        self._setup_claude(MINIMAL_VALID_RESULT)
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "85/100" in comment

    def test_comment_contains_recommendation(self):
        self._setup_claude(MINIMAL_VALID_RESULT)
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._setup_claude(MINIMAL_VALID_RESULT)
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Everything looks fine." in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_claude({**MINIMAL_VALID_RESULT, "findings": []})
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_with_findings(self):
        self._setup_claude(FULL_VALID_RESULT)
        cr.review_pr("acme", "myrepo", 99, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "[HIGH]" in comment
        assert "src/example.py" in comment
        assert "line 42" in comment

    def test_comment_finding_with_null_line_shows_na(self):
        self._setup_claude(FULL_VALID_RESULT)
        cr.review_pr("acme", "myrepo", 99, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "line n/a" in comment

    def test_comment_positive_observations_present(self):
        self._setup_claude(MINIMAL_VALID_RESULT)
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment

    def test_comment_no_positive_observations_shows_placeholder(self):
        self._setup_claude({**MINIMAL_VALID_RESULT, "positive_observations": []})
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_claude_raises_propagates(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.side_effect = RuntimeError("API failure")
        with pytest.raises(RuntimeError, match="API failure"):
            cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")

    def test_invalid_claude_response_raises_value_error(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "not json at all"
        with pytest.raises(ValueError):
            cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")

    def test_pr_number_zero_is_passed_through(self):
        """Boundary: PR number 0 (unusual but shouldn't crash)."""
        self._setup_claude()
        cr.review_pr("acme", "myrepo", 0, "https://ci/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo",