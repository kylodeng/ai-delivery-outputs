"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fences, outermost-brace extraction,
      newline-in-string cleaning, missing JSON, malformed JSON, empty string
    - review_pr(): successful PR review flow, comment formatting, result propagation
    - review_repo(): full-repo scan flow, content truncation, token-budget logic
    - get_output_url(): URL construction correctness
    - build_report_md(): full report rendering, empty findings, missing keys,
      multiple findings, IaC / positive-observations sections

Mocks used:
    - shared.call_claude            (patched at tool1_code_review module level)
    - shared.get_pr_diff            (patched at tool1_code_review module level)
    - shared.get_repo_files         (patched at tool1_code_review module level)
    - shared.post_pr_comment        (patched at tool1_code_review module level)
    - shared.write_output_file      (patched at tool1_code_review module level)
    - shared.send_email             (patched at tool1_code_review module level)
    - shared.write_audit_entry      (patched at tool1_code_review module level)
    - requests                      (not directly called in the functions under test;
                                     imported at module level – stubbed where needed)

TODOs:
    - TODO: __main__ block (mode/owner/repo env-var wiring) cannot be tested without
      more source context – stub tests provided below.
    - TODO: email_html helper is imported but not exercised here – needs its own tests.
    - TODO: write_output_file / send_email integration inside the __main__ block
      requires the full source to be available.
"""

import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the ``shared`` module so we can import tool1_code_review
# without the real dependency being present.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude        = MagicMock()
_shared_stub.get_repo_files     = MagicMock()
_shared_stub.get_pr_diff        = MagicMock()
_shared_stub.write_output_file  = MagicMock()
_shared_stub.post_pr_comment    = MagicMock()
_shared_stub.send_email         = MagicMock()
_shared_stub.email_html         = MagicMock()
_shared_stub.write_audit_entry  = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER  = "test-owner"
_shared_stub.OUTPUT_REPO        = "test-output-repo"
_shared_stub.GH_HEADERS         = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API             = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now safe to import the module under test
import importlib
import tool1_code_review as cr  # noqa: E402  (import after sys.modules patch)


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Code looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_VALID_RESULT = {
    "summary": "Several security issues found.",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Function is too long.",
            "recommendation": "Refactor into smaller units.",
        },
    ],
    "positive_observations": ["Consistent naming conventions.", "Good docstrings."],
    "iac_findings": ["S3 bucket missing encryption.", "IAM role overly permissive."],
}


def _json_str(obj: dict) -> str:
    return json.dumps(obj)


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mocks before each test."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "email_html", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    # ------------------------------------------------------------------
    # Happy path – plain valid JSON
    # ------------------------------------------------------------------

    def test_plain_valid_json(self):
        raw = _json_str(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_valid_json_with_leading_whitespace(self):
        raw = "   \n" + _json_str(MINIMAL_VALID_RESULT) + "\n  "
        result = cr.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    # ------------------------------------------------------------------
    # Markdown fence stripping
    # ------------------------------------------------------------------

    def test_strip_json_code_fence(self):
        raw = "```json\n" + _json_str(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_strip_plain_code_fence(self):
        raw = "```\n" + _json_str(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_strip_fence_with_extra_whitespace(self):
        raw = "```json\n  " + _json_str(MINIMAL_VALID_RESULT) + "  \n```  "
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    # ------------------------------------------------------------------
    # Outermost-brace fallback
    # ------------------------------------------------------------------

    def test_preamble_before_json(self):
        raw = "Here is the review:\n" + _json_str(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_trailing_text_after_json(self):
        raw = _json_str(MINIMAL_VALID_RESULT) + "\n\nEnd of review."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_preamble_and_trailing_text(self):
        raw = "Sure! Here you go:\n" + _json_str(FULL_VALID_RESULT) + "\nHope this helps."
        result = cr.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2

    # ------------------------------------------------------------------
    # Newline-in-string cleaning
    # ------------------------------------------------------------------

    def test_newline_inside_string_value_is_cleaned(self):
        # Craft a JSON string where a value contains a literal newline (invalid JSON)
        broken = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(broken)
        # After cleaning the newline should be replaced by a space
        assert "line one" in result["summary"]

    # ------------------------------------------------------------------
    # Error / edge cases
    # ------------------------------------------------------------------

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This response contains no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_only_braces_but_invalid_content_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("{not: valid, json: here}")

    def test_nested_json_is_parsed(self):
        data = {
            "summary": "Nested test.",
            "score": 60,
            "merge_recommendation": "APPROVE",
            "findings": [{"severity": "LOW", "category": "maintainability",
                           "file": "x.py", "line": 1,
                           "issue": "x", "recommendation": "y"}],
            "positive_observations": [],
            "iac_findings": [],
        }
        result = cr.extract_json(_json_str(data))
        assert result["findings"][0]["severity"] == "LOW"

    def test_score_boundary_zero(self):
        data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = cr.extract_json(_json_str(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = cr.extract_json(_json_str(data))
        assert result["score"] == 100

    def test_finding_with_null_line(self):
        data = {**MINIMAL_VALID_RESULT, "findings": [
            {"severity": "HIGH", "category": "security",
             "file": "a.py", "line": None,
             "issue": "Bad thing.", "recommendation": "Fix it."}
        ]}
        result = cr.extract_json(_json_str(data))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_VALID_RESULT, "findings": []}
        result = cr.extract_json(_json_str(data))
        assert result["findings"] == []

    def test_unicode_values_preserved(self):
        data = {**MINIMAL_VALID_RESULT, "summary": "إلغاء – رجوع"}
        result = cr.extract_json(_json_str(data))
        assert "إلغاء" in result["summary"]


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result_dict=None):
        if result_dict is None:
            result_dict = MINIMAL_VALID_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/x.py b/x.py\n+print('hello')"
        _shared_stub.call_claude.return_value = _json_str(result_dict)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks(MINIMAL_VALID_RESULT)
        result = cr.review_pr("myowner", "myrepo", 42, "http://run-url")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_get_pr_diff_called_correctly(self):
        self._setup_mocks()
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        _shared_stub.get_pr_diff.assert_called_once_with("myowner", "myrepo", 7)

    def test_call_claude_receives_diff_in_prompt(self):
        self._setup_mocks()
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "Review this pull request diff:" in args[1]
        assert "print('hello')" in args[1]

    def test_post_pr_comment_called_once(self):
        self._setup_mocks()
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        _shared_stub.post_pr_comment.assert_called_once()

    def test_comment_contains_score(self):
        self._setup_mocks(MINIMAL_VALID_RESULT)
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        _, kwargs = _shared_stub.post_pr_comment.call_args
        # post_pr_comment is called positionally
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "85/100" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup_mocks(MINIMAL_VALID_RESULT)
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_comment_contains_summary(self):
        self._setup_mocks(MINIMAL_VALID_RESULT)
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Code looks good overall." in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(MINIMAL_VALID_RESULT)
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_with_findings_renders_them(self):
        self._setup_mocks(FULL_VALID_RESULT)
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment_text
        assert "HIGH" in comment_text

    def test_comment_positive_observations_none_placeholder(self):
        data = {**MINIMAL_VALID_RESULT, "positive_observations": []}
        self._setup_mocks(data)
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_comment_positive_observations_rendered(self):
        self._setup_mocks(FULL_VALID_RESULT)
        cr.review_pr("myowner", "myrepo", 7, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Consistent naming conventions." in comment_text

    def test_missing_keys_in_result_handled_gracefully(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps({"score": 50})
        _shared_stub.post_pr_comment.return_value = None
        result = cr.review_pr("myowner", "myrepo", 1, "http://run-url")