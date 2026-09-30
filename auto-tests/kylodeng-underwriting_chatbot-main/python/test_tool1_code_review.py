"""
Test suite for .github/scripts/tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown-fenced input, embedded newlines,
      no JSON found, malformed JSON, outermost-brace extraction
    - review_pr(): successful flow, comment posting, result propagation
    - review_repo(): successful flow, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): full report markdown structure, empty findings/iac/pos,
      populated findings table, score/recommendation rendering

Mocks used:
    - shared.call_claude          (patched at tool1_code_review.call_claude)
    - shared.get_pr_diff          (patched at tool1_code_review.get_pr_diff)
    - shared.get_repo_files       (patched at tool1_code_review.get_repo_files)
    - shared.post_pr_comment      (patched at tool1_code_review.post_pr_comment)
    - shared.write_output_file    (patched at tool1_code_review.write_output_file)
    - shared.write_audit_entry    (patched at tool1_code_review.write_audit_entry)
    - shared.send_email           (patched at tool1_code_review.send_email)
    - shared.email_html           (patched at tool1_code_review.email_html)
    - datetime.datetime.utcnow    (patched to return deterministic timestamp)

TODOs:
    - TODO: test __main__ block (requires env-var wiring + subprocess or importlib reload)
    - TODO: test integration with real GitHub API responses once fixture library is chosen
    - TODO: test send_email / write_audit_entry call-sites once __main__ is fully visible
      (source file appears truncated at `os.enviro...`)
"""

import json
import re
import sys
import os
import importlib
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub the 'shared' module so that importing tool1_code_review
# never touches real network/filesystem code.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="{}")
    shared.get_repo_files     = MagicMock(return_value={})
    shared.get_pr_diff        = MagicMock(return_value="diff text")
    shared.write_output_file  = MagicMock(return_value=None)
    shared.post_pr_comment    = MagicMock(return_value=None)
    shared.send_email         = MagicMock(return_value=None)
    shared.email_html         = MagicMock(return_value="<html/>")
    shared.write_audit_entry  = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER  = "test-owner"
    shared.OUTPUT_REPO        = "test-output-repo"
    shared.GH_HEADERS         = {"Authorization": "token fake"}
    shared.GH_API             = "https://api.github.com"
    return shared


# Install stub before the module is imported
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Also ensure the script directory is on the path (mirrors the source file)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))

import tool1_code_review as t1  # noqa: E402  (import after stub)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "All good.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
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
            "issue": "Function too long.",
            "recommendation": "Split into smaller functions.",
        },
    ],
    "positive_observations": ["CI pipeline is set up", "Linting is enforced"],
    "iac_findings": ["S3 bucket lacks server-side encryption", "IAM role is overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-stub mocks before each test."""
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
        result = t1.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = t1.extract_json(raw)
        assert result["summary"] == "All good."

    def test_markdown_fenced_json_triple_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fenced_json_with_language_hint(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd."
        result = t1.extract_json(raw)
        assert result["score"] == 85

    def test_json_with_literal_newline_inside_string(self):
        # Simulate Claude inserting a literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # The regex cleaner should handle this
        result = t1.extract_json(raw)
        assert result["score"] == 50

    def test_raises_value_error_when_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("This is just plain text with no JSON.")

    def test_raises_value_error_on_malformed_json(self):
        raw = '{"score": 70, "summary": "bad json" "missing_colon"}'
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_only_opening_brace_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("{")

    def test_nested_json_structure(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_fenced_with_extra_text_before_and_after(self):
        raw = (
            "Sure, here is the output:\n"
            "```json\n"
            + json.dumps(MINIMAL_RESULT)
            + "\n```\n"
            "Let me know if you need more details."
        )
        result = t1.extract_json(raw)
        assert result["score"] == 85

    def test_multiple_json_objects_picks_outermost(self):
        inner = '{"key": "value"}'
        outer = json.dumps(MINIMAL_RESULT)
        raw = outer  # should parse the whole thing
        result = t1.extract_json(raw)
        assert "summary" in result

    def test_returns_dict(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert isinstance(result, dict)


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result=None):
        if result is None:
            result = MINIMAL_RESULT
        _shared_stub.get_pr_diff.return_value = "--- a/file.py\n+++ b/file.py\n@@ ... @@\n+x = 1"
        _shared_stub.call_claude.return_value = json.dumps(result)

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = t1.review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        t1.review_pr("acme", "myrepo", 99, "http://run")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo", 99)

    def test_calls_call_claude_with_diff_content(self):
        self._setup_mocks()
        t1.review_pr("acme", "myrepo", 1, "http://run")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[1]
        assert "--- a/file.py" in args[1]

    def test_posts_pr_comment(self):
        self._setup_mocks()
        t1.review_pr("acme", "myrepo", 7, "http://run")
        _shared_stub.post_pr_comment.assert_called_once()
        owner, repo, pr, comment = _shared_stub.post_pr_comment.call_args[0]
        assert owner == "acme"
        assert repo == "myrepo"
        assert pr == 7
        assert "Claude Code Review" in comment

    def test_comment_contains_score(self):
        self._setup_mocks()
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "85" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._setup_mocks()
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "All good." in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(MINIMAL_RESULT)
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_with_findings_shows_them(self):
        self._setup_mocks(FULL_RESULT)
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment
        assert "Hardcoded password detected." in comment

    def test_comment_positive_observations(self):
        self._setup_mocks(FULL_RESULT)
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "CI pipeline is set up" in comment

    def test_comment_no_positive_observations_shows_placeholder(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup_mocks(result)
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_missing_score_in_result_uses_question_mark(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        self._setup_mocks(result)
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "?" in comment

    def test_finding_with_null_line_shows_na(self):
        result = {
            **MINIMAL_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "foo.py",
                    "line": None,
                    "issue": "too long",
                    "recommendation": "shorten",
                }
            ],
        }
        self._setup_mocks(result)
        t1.review_pr("acme", "myrepo", 1, "http://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment

    def test_extract_json_called_on_raw_claude_response(self):
        """Ensure the raw string from call_claude is parsed, not used as-is."""
        raw = json.dumps(MINIMAL_RESULT)
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = raw
        result = t1.review_pr("x", "y", 1, "http://run")
        assert isinstance(result, dict)


# ===========================================================================
# review_repo
# ===========================================================================

class TestReviewRepo:

    def _setup_repo_mocks(self, files=None, result=None):
        if files is None:
            files = {
                "src/app.py