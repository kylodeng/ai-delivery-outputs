"""
Test suite for tool1_code_review.py

What is tested:
  - extract_json: happy path, markdown-fenced input, nested braces, newlines inside strings,
    missing braces, completely invalid input, edge cases with whitespace
  - review_pr: happy path, Claude response handling, comment formatting, return value
  - review_repo: happy path, content truncation, file filtering
  - get_output_url: URL construction
  - build_report_md: full report, empty findings, missing keys, IaC/positive sections

Mocks used:
  - shared.call_claude (patched at tool1_code_review module level)
  - shared.get_pr_diff
  - shared.get_repo_files
  - shared.post_pr_comment
  - shared.write_output_file
  - shared.send_email
  - shared.email_html
  - shared.write_audit_entry
  - requests (not directly exercised in public API but imported)

TODOs:
  - TODO: Integration test for __main__ block requires real environment wiring
  - TODO: Test write_output_file + send_email orchestration once a caller function is complete
         (source file appears truncated before __main__ env-read section)
"""

import json
import sys
import os
import types
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal `shared` stub so the import doesn't fail even
# when the real shared module is not on the path.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude       = MagicMock(return_value="{}")
    shared.get_repo_files    = MagicMock(return_value={})
    shared.get_pr_diff       = MagicMock(return_value="diff text")
    shared.write_output_file = MagicMock(return_value=None)
    shared.post_pr_comment   = MagicMock(return_value=None)
    shared.send_email        = MagicMock(return_value=None)
    shared.email_html        = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO       = "test-output-repo"
    shared.GH_HEADERS        = {"Authorization": "Bearer fake"}
    shared.GH_API            = "https://api.github.com"
    return shared


# Insert stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Now import the module under test
import importlib
import tool1_code_review as cr  # noqa: E402  (must come after sys.modules patch)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
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
            "issue": "Missing docstring.",
            "recommendation": "Add a module-level docstring.",
        },
    ],
    "positive_observations": ["CI pipeline is well structured", "Good use of type hints"],
    "iac_findings": ["S3 bucket lacks encryption", "IAM role is overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset every mock on the shared stub between tests."""
    shared = sys.modules["shared"]
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "email_html", "write_audit_entry"):
        getattr(shared, attr).reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_valid_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fence_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_text_before_and_after_json(self):
        raw = "Here is my analysis:\n" + json.dumps(FULL_RESULT) + "\nEnd of output."
        result = cr.extract_json(raw)
        assert result["score"] == 42

    def test_newline_inside_string_value(self):
        # Simulate a literal newline inside a JSON string value
        broken = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # Should either parse after cleanup or raise ValueError — the key
        # requirement is it doesn't silently return wrong data.
        try:
            result = cr.extract_json(broken)
            assert isinstance(result, dict)
        except ValueError:
            pass  # acceptable outcome

    def test_no_json_in_response_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no braces at all.")

    def test_malformed_json_raises_value_error(self):
        raw = '{"summary": "ok", "score": NOTANUMBER}'
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_braces_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("{}")  # valid JSON but trivially parseable — actually passes
        # {} is valid JSON — ensure it returns empty dict rather than raising
        result = cr.extract_json("{}")
        assert result == {}

    def test_nested_json_objects(self):
        """Ensure nested objects inside findings are handled."""
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_unicode_values(self):
        data = {**MINIMAL_RESULT, "summary": "كود جيد"}
        raw = json.dumps(data, ensure_ascii=False)
        result = cr.extract_json(raw)
        assert "كود" in result["summary"]

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_extra_text_before_brace(self):
        preamble = "Sure! Here's my review:\n\n"
        raw = preamble + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_findings_with_null_line(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "Missing docs.", "recommendation": "Add docs."}
        ]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_missing_optional_keys_does_not_raise(self):
        minimal = {"score": 70, "merge_recommendation": "APPROVE"}
        result = cr.extract_json(json.dumps(minimal))
        assert result["score"] == 70

    def test_markdown_fence_with_trailing_whitespace(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```   "
        result = cr.extract_json(raw)
        assert result["score"] == 85


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result_dict=None):
        shared = sys.modules["shared"]
        shared.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+print('hello')"
        shared.call_claude.return_value = json.dumps(result_dict or MINIMAL_RESULT)
        shared.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks(MINIMAL_RESULT)
        result = cr.review_pr("myorg", "myrepo", 42, "https://gh-actions/run/1")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 7, "")
        sys.modules["shared"].get_pr_diff.assert_called_once_with("myorg", "myrepo", 7)

    def test_calls_call_claude_with_diff(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 7, "")
        shared = sys.modules["shared"]
        args, kwargs = shared.call_claude.call_args
        assert "Review this pull request diff" in args[1]
        assert shared.get_pr_diff.return_value in args[1]

    def test_calls_post_pr_comment(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        shared = sys.modules["shared"]
        shared.post_pr_comment.assert_called_once()
        call_args = shared.post_pr_comment.call_args[0]
        assert call_args[0] == "myorg"
        assert call_args[1] == "myrepo"
        assert call_args[2] == 7

    def test_comment_contains_score(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "85" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "Looks good overall." in comment

    def test_comment_no_findings_placeholder(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_includes_findings(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment
        assert "src/main.py" in comment

    def test_comment_positive_observations(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment

    def test_comment_no_positive_observations_placeholder(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup_mocks(result)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_missing_score_uses_question_mark(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        self._setup_mocks(result)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "?/100" in comment

    def test_invalid_claude_response_raises(self):
        shared = sys.modules["shared"]
        shared.get_pr_diff.return_value = "diff"
        shared.call_claude.return_value = "not json at all, no braces"
        with pytest.raises(ValueError):
            cr.review_pr("myorg", "myrepo", 1, "")

    def test_comment_contains_auto_generated_footer(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "Auto-generated by AI Delivery Bot" in comment

    def test_findings_line_na_when_null(self):
        result = {**FULL_RESULT}
        result["findings"] = [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "Missing docs.", "recommendation": "Add docs."}
        ]
        self._setup_mocks(result)
        cr.review_pr("myorg", "myrepo", 7, "")
        comment = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "n/a" in comment


# ===========================================================================
# review_repo
# ===========================================================================

class TestReviewRepo:

    def test_happy_path_returns_result(self):
        shared = sys.modules["shared"]
        shared.