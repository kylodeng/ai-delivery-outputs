"""
Test module for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown-fenced input, outermost-block extraction,
  newline-inside-string cleanup, missing JSON block, completely invalid input
- review_pr(): happy path, Claude response forwarded to PR comment, return value
- review_repo(): happy path, content truncation, return value
- get_output_url(): URL construction
- build_report_md(): full report with findings, empty findings, missing keys

Mocks used:
- shared.call_claude          (patched at tool1_code_review module level)
- shared.get_pr_diff          (patched at tool1_code_review module level)
- shared.get_repo_files       (patched at tool1_code_review module level)
- shared.post_pr_comment      (patched at tool1_code_review module level)
- shared.write_output_file    (patched at tool1_code_review module level)
- shared.send_email           (patched at tool1_code_review module level)
- shared.write_audit_entry    (patched at tool1_code_review module level)
- requests                    (not called directly in public functions; stub present)

TODOs:
- TODO: test __main__ block (requires subprocess or importlib reload with env patching)
- TODO: test interaction with OUTPUT_REPO_OWNER / OUTPUT_REPO constants from shared
- TODO: integration test for write_output_file path inside review_pr / review_repo
        once the full __main__ wiring is visible (source is truncated)
"""

import json
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import importlib, sys, types, os

# ---------------------------------------------------------------------------
# We need to stub `shared` before importing the module under test because
# tool1_code_review does `from shared import ...` at module level.
# ---------------------------------------------------------------------------

SHARED_ATTRS = [
    "call_claude", "get_repo_files", "get_pr_diff",
    "write_output_file", "post_pr_comment",
    "send_email", "email_html", "write_audit_entry",
    "OUTPUT_REPO_OWNER", "OUTPUT_REPO", "GH_HEADERS", "GH_API",
]

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.call_claude       = MagicMock(return_value="{}")
    mod.get_repo_files    = MagicMock(return_value={})
    mod.get_pr_diff       = MagicMock(return_value="")
    mod.write_output_file = MagicMock()
    mod.post_pr_comment   = MagicMock()
    mod.send_email        = MagicMock()
    mod.email_html        = MagicMock(return_value="<html/>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO       = "test-output-repo"
    mod.GH_HEADERS        = {"Authorization": "Bearer FAKE"}
    mod.GH_API            = "https://api.github.com"
    return mod

# Install stub before import
_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

import tool1_code_review as cr  # noqa: E402  (must come after stub install)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean structure"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
    "score": 55,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/main.py",
            "line": 42,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or AWS Secrets Manager.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Bare except clause found.",
            "recommendation": "Catch specific exception types.",
        },
    ],
    "positive_observations": ["Good docstrings", "Type hints present"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared mocks before every test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_json_object(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_triple_backtick_fence(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_text_before_and_after_json(self):
        raw = "Here is the review:\n" + json.dumps(FULL_RESULT) + "\nEnd of review."
        result = cr.extract_json(raw)
        assert result["score"] == 55
        assert len(result["findings"]) == 2

    def test_newline_inside_string_value_gets_cleaned(self):
        # Simulate Claude inserting a newline inside a string value
        dirty = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(dirty)
        assert "line one" in result["summary"]

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_no_braces_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("{score: 80, bad json}")

    def test_nested_json_object(self):
        """extract_json should handle deeply nested structures."""
        data = {"outer": {"inner": {"value": 1}}, "score": 90,
                "summary": "x", "merge_recommendation": "APPROVE",
                "findings": [], "positive_observations": [], "iac_findings": []}
        result = cr.extract_json(json.dumps(data))
        assert result["outer"]["inner"]["value"] == 1

    def test_extra_text_before_brace(self):
        raw = "Some preamble text\n\n" + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket missing encryption"

    def test_score_zero_boundary(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred_boundary(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "app.py", "line": None, "issue": "bare except",
             "recommendation": "use specific exception"}
        ]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_markdown_fence_with_extra_whitespace(self):
        raw = "  ```json  \n" + json.dumps(MINIMAL_RESULT) + "\n```  "
        # The stripping logic should still find the JSON
        result = cr.extract_json(raw)
        assert result["score"] == 80

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = cr.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severities_in_findings(self, severity):
        finding = {"severity": severity, "category": "security",
                   "file": "x.py", "line": 1, "issue": "i", "recommendation": "r"}
        data = {**MINIMAL_RESULT, "findings": [finding]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_claude(self, result_dict):
        _shared_stub.call_claude.return_value = json.dumps(result_dict)
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/main.py ..."

    def test_happy_path_returns_result(self):
        self._setup_claude(MINIMAL_RESULT)
        result = cr.review_pr("acme", "my-repo", 42, "https://ci/run/1")
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_claude(MINIMAL_RESULT)
        cr.review_pr("acme", "my-repo", 99, "https://ci/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "my-repo", 99)

    def test_calls_call_claude(self):
        self._setup_claude(MINIMAL_RESULT)
        cr.review_pr("acme", "my-repo", 1, "https://ci/run/1")
        assert _shared_stub.call_claude.call_count == 1
        args = _shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[0][1]

    def test_posts_pr_comment(self):
        self._setup_claude(FULL_RESULT)
        cr.review_pr("acme", "my-repo", 7, "https://ci/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args[0]
        assert call_args[0] == "acme"
        assert call_args[1] == "my-repo"
        assert call_args[2] == 7

    def test_comment_contains_score(self):
        self._setup_claude(FULL_RESULT)
        cr.review_pr("acme", "my-repo", 7, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "55" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup_claude(FULL_RESULT)
        cr.review_pr("acme", "my-repo", 7, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_contains_findings(self):
        self._setup_claude(FULL_RESULT)
        cr.review_pr("acme", "my-repo", 7, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded AWS secret key detected" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_claude(MINIMAL_RESULT)
        cr.review_pr("acme", "my-repo", 1, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_positive_observations(self):
        self._setup_claude(FULL_RESULT)
        cr.review_pr("acme", "my-repo", 7, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good docstrings" in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        data = {**MINIMAL_RESULT, "positive_observations": []}
        _shared_stub.call_claude.return_value = json.dumps(data)
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("acme", "my-repo", 1, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_diff_is_passed_to_claude(self):
        _shared_stub.get_pr_diff.return_value = "MY_SPECIAL_DIFF_CONTENT"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)
        cr.review_pr("acme", "my-repo", 1, "https://ci/run/1")
        prompt = _shared_stub.call_claude.call_args[0][1]
        assert "MY_SPECIAL_DIFF_CONTENT" in prompt

    def test_missing_score_in_result_shows_question_mark(self):
        data = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        _shared_stub.call_claude.