"""
Test module for tool1_code_review.py

What is tested:
    - extract_json: happy path, markdown fences, embedded newlines, missing braces, invalid JSON
    - review_pr: happy path, Claude response handling, comment posting
    - review_repo: happy path, content truncation, file filtering
    - get_output_url: URL construction
    - build_report_md: full report structure, empty findings, missing keys

Mocks used:
    - shared.call_claude (patched via tool1_code_review module)
    - shared.get_pr_diff
    - shared.get_repo_files
    - shared.post_pr_comment
    - shared.write_output_file
    - shared.send_email
    - shared.write_audit_entry
    - requests (imported but not directly called in tested functions)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var setup and GitHub token
    - TODO: Test for email dispatch path (send_email) needs SMTP/SES mock details from shared.py
    - TODO: write_output_file assertion needs the actual output-repo structure from shared.py
"""

import json
import re
import datetime
import importlib
import sys
import os
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool1_code_review
# without the real dependency being present.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock()
    stub.get_repo_files = MagicMock()
    stub.get_pr_diff = MagicMock()
    stub.write_output_file = MagicMock()
    stub.post_pr_comment = MagicMock()
    stub.send_email = MagicMock()
    stub.email_html = MagicMock()
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "test-output-owner"
    stub.OUTPUT_REPO = "test-output-repo"
    stub.GH_HEADERS = {"Authorization": "Bearer fake"}
    stub.GH_API = "https://api.github.com"
    return stub


# Insert stub before the real module is loaded
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)
# Also ensure requests won't make real HTTP calls
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test
import tool1_code_review as cr  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the code is acceptable.",
    "score": 72,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password found.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage.", "Clear variable naming."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

MINIMAL_RESULT_EMPTY = {
    "summary": "No issues found.",
    "score": 100,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": [],
    "iac_findings": [],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-stub mocks between tests."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 72
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Overall the code is acceptable."

    def test_json_wrapped_in_markdown_fences(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 72

    def test_json_wrapped_in_plain_fences(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 72

    def test_json_with_preamble_text(self):
        raw = "Here is your review:\n\n" + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_postamble_text(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nLet me know if you need more detail."
        result = cr.extract_json(raw)
        assert result["score"] == 72

    def test_json_with_both_preamble_and_postamble(self):
        raw = "Some intro.\n" + json.dumps(MINIMAL_RESULT) + "\nSome outro."
        result = cr.extract_json(raw)
        assert isinstance(result, dict)

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a Claude response that contains a literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This should either parse directly or after cleaning
        result = cr.extract_json(raw)
        assert result["score"] == 50

    def test_empty_findings_list(self):
        raw = json.dumps(MINIMAL_RESULT_EMPTY)
        result = cr.extract_json(raw)
        assert result["findings"] == []
        assert result["positive_observations"] == []

    def test_raises_value_error_on_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_raises_value_error_on_malformed_json(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 50, "unclosed": ')

    def test_raises_value_error_on_empty_string(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_raises_value_error_on_only_braces(self):
        # A pair of braces with garbage inside that can't be fixed
        with pytest.raises(ValueError):
            cr.extract_json("{ this: is: not: json }")

    def test_nested_findings(self):
        data = dict(MINIMAL_RESULT)
        data["findings"] = [
            {"severity": "CRITICAL", "category": "security", "file": "main.tf",
             "line": None, "issue": "No encryption.", "recommendation": "Enable encryption."},
            {"severity": "LOW", "category": "maintainability", "file": "utils.py",
             "line": 10, "issue": "Magic number.", "recommendation": "Use a constant."},
        ]
        raw = json.dumps(data)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_score_boundary_zero(self):
        data = dict(MINIMAL_RESULT_EMPTY)
        data["score"] = 0
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = dict(MINIMAL_RESULT_EMPTY)
        data["score"] = 100
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n{\"score\": 88, \"summary\": \"ok\", \"merge_recommendation\": \"APPROVE\", \"findings\": [], \"positive_observations\": [], \"iac_findings\": []}\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 88

    def test_extra_whitespace_inside_fences(self):
        inner = json.dumps(MINIMAL_RESULT_EMPTY)
        raw = f"```json\n\n  {inner}  \n\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = dict(MINIMAL_RESULT_EMPTY)
        data["merge_recommendation"] = recommendation
        result = cr.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severities(self, severity):
        data = dict(MINIMAL_RESULT)
        data["findings"][0]["severity"] = severity
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result=None):
        if result is None:
            result = MINIMAL_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/example.py b/src/example.py\n+password = 'secret'"
        _shared_stub.call_claude.return_value = json.dumps(result)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        assert result["score"] == 72
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("myorg", "myrepo", 42)

    def test_calls_call_claude(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        assert _shared_stub.call_claude.call_count == 1

    def test_calls_post_pr_comment(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "myorg"
        assert args[1] == "myrepo"
        assert args[2] == 42

    def test_comment_contains_score(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "72" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Overall the code is acceptable." in comment

    def test_comment_contains_finding_issue(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password found." in comment

    def test_comment_contains_positive_observations(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage." in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(result=MINIMAL_RESULT_EMPTY)
        cr.review_pr("myorg", "myrepo", 1, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_no_positive_shows_placeholder(self):
        self._setup_mocks(result=MINIMAL_RESULT_EMPTY)
        cr.review_pr("myorg", "myrepo", 1, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_invalid_json_from_claude_raises(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = "NOT JSON AT ALL"
        with pytest.raises(ValueError):
            cr.review_pr("myorg", "myrepo", 99, "https://ci/run/1")

    def test_comment_contains_file_and_line(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/example.py" in comment
        assert "42" in comment

    def test_finding_with_null_line(self):
        result = dict(MINIMAL_RESULT)
        result["findings"] = [
            {"severity": "LOW", "category": "maintainability",
             "file": "app.py", "line": None,
             "issue": "Magic number.", "recommendation": "Use a constant."}
        ]
        self._setup_mocks(result=result)
        cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment

    def test_multiple_findings_in_comment(self):
        result = dict(MINIMAL_RESULT)
        result["findings"] = [
            {"severity": "HIGH", "category": "security", "file": "a.py",
             "line": 1, "issue": "Issue A.", "recommendation":