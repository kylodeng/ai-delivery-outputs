"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  no-JSON input, malformed JSON, empty input, direct JSON, outermost-block extraction
- review_pr(): happy path, Claude response handling, comment posting, return value
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report, empty findings, missing keys, IaC/positive sections

Mocks used:
- shared.call_claude          (patched via unittest.mock.patch)
- shared.get_repo_files       (patched)
- shared.get_pr_diff          (patched)
- shared.write_output_file    (patched)
- shared.post_pr_comment      (patched)
- shared.send_email           (patched)
- shared.email_html           (patched)
- shared.write_audit_entry    (patched)
- requests                    (patched where needed)

TODOs:
- TODO: Integration test for __main__ block (needs full env setup)
- TODO: Test write_output_file and write_audit_entry interactions in review_pr/review_repo
        once output repo credentials are available in CI
- TODO: Test email sending path once SMTP/SES config is available
"""

import json
import sys
import os
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without a real `shared` module
# ---------------------------------------------------------------------------

# We create a minimal fake `shared` module so the import in tool1_code_review
# does not fail even when the real shared.py is absent in the test environment.
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude = MagicMock()
_shared_stub.get_repo_files = MagicMock()
_shared_stub.get_pr_diff = MagicMock()
_shared_stub.write_output_file = MagicMock()
_shared_stub.post_pr_comment = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock(return_value="<html></html>")
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
# Also try the local directory so pytest can find it when run from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import importlib

# We need to (re)load tool1_code_review using the stub shared module
import tool1_code_review as t1  # type: ignore


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the code looks fine.",
    "score": 82,
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
            "file": "src/app.py",
            "line": 12,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Function is too long.",
            "recommendation": "Split into smaller functions.",
        },
    ],
    "positive_observations": ["Uses type hints", "Has docstrings"],
    "iac_findings": ["S3 bucket lacks encryption", "IAM role is overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mocks before every test."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:
    """Tests for extract_json()."""

    # --- happy path ---

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = t1.extract_json(raw)
        assert result["summary"] == "Overall the code looks fine."

    def test_markdown_fenced_json_triple_backtick(self):
        raw = "```json\n" + json.dumps(FULL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 45

    def test_markdown_fenced_json_no_language_tag(self):
        raw = "```\n" + json.dumps(FULL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_json_embedded_in_preamble_text(self):
        raw = "Here is my response:\n" + json.dumps(MINIMAL_RESULT) + "\nThank you."
        result = t1.extract_json(raw)
        assert result["score"] == 82

    def test_json_with_newline_inside_string_value(self):
        # Simulate Claude adding a literal newline inside a JSON string
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This may fail direct parse; extract_json should clean and parse it
        result = t1.extract_json(raw)
        assert result["score"] == 70

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket lacks encryption"

    # --- edge cases ---

    def test_extra_text_before_and_after_json(self):
        raw = "Preamble text\n\n" + json.dumps(MINIMAL_RESULT) + "\n\nPostamble text"
        result = t1.extract_json(raw)
        assert isinstance(result, dict)

    def test_minimal_json_object(self):
        raw = '{"key": "value"}'
        result = t1.extract_json(raw)
        assert result == {"key": "value"}

    def test_nested_json_objects_in_findings(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert result["findings"][1]["line"] is None

    # --- error / negative cases ---

    def test_raises_on_no_json_object(self):
        raw = "This is just plain text with no JSON."
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json(raw)

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_raises_on_malformed_json(self):
        raw = '{"summary": "missing closing brace", "score": 50'
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_raises_on_json_array_only(self):
        raw = '[1, 2, 3]'
        # No { } at all — should raise ValueError
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_markdown_fence_with_extra_spaces(self):
        raw = "```  \n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 82

    def test_whitespace_only_string(self):
        with pytest.raises(ValueError):
            t1.extract_json("   \n\t  ")

    def test_curly_brace_content_is_not_valid_json(self):
        raw = "{ this is not valid json at all }"
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_multiple_json_objects_extracts_outermost(self):
        """When there are multiple objects, the outermost { ... } wins."""
        inner = json.dumps({"inner": True})
        outer = json.dumps({"outer": True, "child": inner})
        result = t1.extract_json(outer)
        assert result["outer"] is True

    @pytest.mark.parametrize("score", [0, 1, 50, 99, 100])
    def test_boundary_scores(self, score):
        data = {**MINIMAL_RESULT, "score": score}
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == score

    @pytest.mark.parametrize("rec", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, rec):
        data = {**MINIMAL_RESULT, "merge_recommendation": rec}
        result = t1.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == rec


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:
    """Tests for review_pr()."""

    def _setup_mocks(self, result=None):
        if result is None:
            result = MINIMAL_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+print('hello')"
        _shared_stub.call_claude.return_value = json.dumps(result)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks(MINIMAL_RESULT)
        result = t1.review_pr("myorg", "myrepo", 42, "http://run-url")
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 7, "http://run-url")
        _shared_stub.get_pr_diff.assert_called_once_with("myorg", "myrepo", 7)

    def test_calls_call_claude_with_diff(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 1, "http://run-url")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[1]

    def test_calls_post_pr_comment(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        _shared_stub.post_pr_comment.assert_called_once()

    def test_comment_contains_score(self):
        self._setup_mocks(MINIMAL_RESULT)
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        _, kwargs = _shared_stub.post_pr_comment.call_args
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "82" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup_mocks(MINIMAL_RESULT)
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_comment_contains_summary(self):
        self._setup_mocks(MINIMAL_RESULT)
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Overall the code looks fine." in comment_text

    def test_comment_with_findings(self):
        self._setup_mocks(FULL_RESULT)
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment_text
        assert "HIGH" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(MINIMAL_RESULT)
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_contains_positive_observations(self):
        self._setup_mocks(MINIMAL_RESULT)
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup_mocks(result)
        t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_missing_score_uses_question_mark(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        self._setup_mocks(result)
        result_out = t1.review_pr("myorg", "myrepo", 5, "http://run-url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "?" in comment_text

    def test_pr_number_passed_to_comment(self):
        self._setup_mocks()
        t1.review_pr("org1", "repo1", 99, "http://run-url")
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[2] == 99

    def test_owner_and_repo_passed_to_comment(self):
        self._setup_mocks()
        t1.review_pr("org1", "repo1", 1, "http://run-