"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json: happy path, markdown fences, nested braces, newlines in strings,
      no JSON present, malformed JSON, boundary/edge cases
    - review_pr: happy path, Claude returns valid JSON, comment posted correctly
    - review_repo: happy path, content truncation to 20000 chars
    - get_output_url: URL construction
    - build_report_md: happy path, missing fields, empty findings/iac/positive_observations

Mocks used:
    - shared.call_claude (patched at tool1_code_review module level)
    - shared.get_pr_diff (patched at tool1_code_review module level)
    - shared.get_repo_files (patched at tool1_code_review module level)
    - shared.post_pr_comment (patched at tool1_code_review module level)
    - shared.write_output_file (patched at tool1_code_review module level)
    - shared.send_email (patched at tool1_code_review module level)
    - shared.write_audit_entry (patched at tool1_code_review module level)
    - requests (not called directly in tested functions, but imported)

TODOs:
    - TODO: Integration test for __main__ block (requires full env setup)
    - TODO: Test write_output_file / send_email integration in review_pr/review_repo
      once those call-sites are confirmed in the full source
"""

import importlib
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool1_code_review
# without the real shared.py being present in the test environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="{}")
    shared.get_repo_files = MagicMock(return_value={})
    shared.get_pr_diff = MagicMock(return_value="")
    shared.write_output_file = MagicMock()
    shared.post_pr_comment = MagicMock()
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "token test"}
    shared.GH_API = "https://api.github.com"
    return shared


# Inject the stub before the module under test is loaded
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import tool1_code_review as cr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean structure"],
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
    "positive_observations": ["Good test coverage", "CI pipeline present"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role too permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stub mocks before every test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# Tests for extract_json
# ===========================================================================


class TestExtractJson:
    # --- Happy path -----------------------------------------------------------

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fence_backticks(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fence_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is my review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    # --- Newline-inside-string repair -----------------------------------------

    def test_newline_inside_string_value_repaired(self):
        # Simulate a raw JSON where a string value contains a literal newline
        broken = '{"summary": "line one\nline two", "score": 70}'
        result = cr.extract_json(broken)
        # After repair, summary should be a single string (space-joined or accepted)
        assert "summary" in result

    # --- Edge cases -----------------------------------------------------------

    def test_extra_text_before_brace(self):
        raw = "Some preamble text {" + '"score": 90, "summary": "ok", ' \
              '"merge_recommendation": "APPROVE", "findings": [], ' \
              '"positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        assert result["score"] == 90

    def test_nested_objects_in_findings(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_empty_lists_in_result(self):
        minimal = {"summary": "ok", "score": 100, "merge_recommendation": "APPROVE",
                   "findings": [], "positive_observations": [], "iac_findings": []}
        raw = json.dumps(minimal)
        result = cr.extract_json(raw)
        assert result["findings"] == []
        assert result["iac_findings"] == []

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    # --- Error conditions -----------------------------------------------------

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This string has no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 75, "summary": missing_quotes}')

    def test_incomplete_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 75, "summary":')

    def test_only_array_no_object_raises(self):
        # A bare array has no { } so should raise
        with pytest.raises(ValueError):
            cr.extract_json("[1, 2, 3]")

    def test_markdown_fence_with_malformed_json_raises(self):
        raw = "```json\n{bad json here\n```"
        with pytest.raises(ValueError):
            cr.extract_json(raw)


# ===========================================================================
# Tests for review_pr
# ===========================================================================


class TestReviewPr:
    def test_happy_path_returns_parsed_result(self):
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/main.py ..."
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        result = cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")

        assert result["score"] == 42
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_post_pr_comment_called_once(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("acme", "myrepo", 7, "https://ci/run/2")

        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args
        assert call_args[0][0] == "acme"
        assert call_args[0][1] == "myrepo"
        assert call_args[0][2] == 7

    def test_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("acme", "myrepo", 1, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "85" in comment_text
        assert "APPROVE" in comment_text

    def test_comment_contains_findings(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        cr.review_pr("owner", "repo", 99, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/main.py" in comment_text
        assert "Hardcoded password" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("owner", "repo", 5, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        result_no_pos = {**MINIMAL_RESULT, "positive_observations": []}
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(result_no_pos)

        cr.review_pr("owner", "repo", 5, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_get_pr_diff_called_with_correct_args(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("org", "therepo", 123, "url")

        _shared_stub.get_pr_diff.assert_called_once_with("org", "therepo", 123)

    def test_call_claude_receives_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "MY_SPECIAL_DIFF_CONTENT"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("org", "repo", 1, "")

        user_msg = _shared_stub.call_claude.call_args[0][1]
        assert "MY_SPECIAL_DIFF_CONTENT" in user_msg

    def test_missing_score_in_result_uses_question_mark(self):
        result_no_score = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(result_no_score)

        cr.review_pr("o", "r", 1, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "?" in comment_text

    def test_finding_with_null_line_renders_na(self):
        result_null_line = {
            **MINIMAL_RESULT,
            "findings": [
                {"severity": "LOW", "category": "maintainability",
                 "file": "foo.py", "line": None,
                 "issue": "no docstring", "recommendation": "add one"}
            ]
        }
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(result_null_line)

        cr.review_pr("o", "r", 1, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment_text

    def test_claude_returns_invalid_json_raises(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "Not JSON at all"

        with pytest.raises(ValueError):
            cr.review_pr("o", "r", 1, "")


# ===========================================================================
# Tests for review_repo
# ===========================================================================


class TestReviewRepo:
    def test_happy_path_returns_parsed_result(self):
        _shared_stub.get_repo_files.return_value = {
            "src/main.py": "print('hello')",
            "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        _shared_stub.call_