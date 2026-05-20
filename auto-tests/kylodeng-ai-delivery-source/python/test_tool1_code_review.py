"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, newlines in strings, missing braces, invalid JSON
- review_pr(): diff retrieval, Claude call, comment posting, result parsing
- review_repo(): file retrieval, content truncation, Claude call, result parsing
- get_output_url(): URL construction
- build_report_md(): report markdown generation with full/empty/partial result dicts

Mocks used:
- shared.call_claude (patched via tool1_code_review.call_claude)
- shared.get_pr_diff (patched via tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched via tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched via tool1_code_review.post_pr_comment)
- shared.write_output_file (patched via tool1_code_review.write_output_file)
- shared.send_email (patched via tool1_code_review.send_email)
- shared.write_audit_entry (patched via tool1_code_review.write_audit_entry)
- requests (patched where needed)

TODOs:
- TODO: Integration test for __main__ block requires real environment variables and GitHub tokens
- TODO: Test email/audit side-effects once the truncated __main__ block is complete
- TODO: Test content truncation boundary (20000 chars) with a real large file set
"""

import json
import re
import datetime
import sys
import os
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool1_code_review
# succeeds without real credentials or network access.
# ---------------------------------------------------------------------------
shared_stub = types.ModuleType("shared")
shared_stub.call_claude = MagicMock()
shared_stub.get_repo_files = MagicMock()
shared_stub.get_pr_diff = MagicMock()
shared_stub.write_output_file = MagicMock()
shared_stub.post_pr_comment = MagicMock()
shared_stub.send_email = MagicMock()
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now we can safely import the module under test
import tool1_code_review as cr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean code structure"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket is publicly accessible.",
            "recommendation": "Set block_public_acls to true.",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": 55,
            "issue": "Bare except clause used.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["Good test coverage", "Consistent naming"],
    "iac_findings": ["Missing encryption on RDS instance", "No resource tags"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs between tests."""
    shared_stub.call_claude.reset_mock()
    shared_stub.get_repo_files.reset_mock()
    shared_stub.get_pr_diff.reset_mock()
    shared_stub.write_output_file.reset_mock()
    shared_stub.post_pr_comment.reset_mock()
    shared_stub.send_email.reset_mock()
    shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================


class TestExtractJson:
    """Tests for cr.extract_json()"""

    # --- Happy paths ---

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_json_wrapped_in_triple_backtick_fence(self):
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"```json\n{inner}\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_wrapped_in_plain_triple_backtick_fence(self):
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"```\n{inner}\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_with_preamble_text(self):
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"Here is my review:\n\n{inner}"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_trailing_text(self):
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"{inner}\n\nPlease let me know if you need anything else."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_full_result_dict(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a literal newline inside a JSON string value
        raw = '{"summary": "This is\na summary", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This should either parse directly (after cleaning) or raise ValueError
        # The function strips newlines inside string values via regex
        result = cr.extract_json(raw)
        assert "summary" in result

    def test_empty_findings_list(self):
        raw = json.dumps({**MINIMAL_RESULT, "findings": []})
        result = cr.extract_json(raw)
        assert result["findings"] == []

    def test_null_line_in_finding(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "main.py", "line": None,
             "issue": "Missing docstring.", "recommendation": "Add docstring."}
        ]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    # --- Edge cases ---

    def test_json_with_extra_text_around_braces(self):
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"Some leading text {inner} some trailing text"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_score_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_one_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_unicode_in_values(self):
        data = {**MINIMAL_RESULT, "summary": "Résumé: ça va bien."}
        result = cr.extract_json(json.dumps(data))
        assert "Résumé" in result["summary"]

    # --- Error conditions ---

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no braces.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_invalid_json_braces_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse Claude response as JSON"):
            cr.extract_json("{this is not: valid json!!!}")

    def test_incomplete_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"summary": "missing closing brace"')

    def test_array_only_raises_value_error(self):
        # A bare array has no { } so we expect ValueError
        with pytest.raises(ValueError):
            cr.extract_json("[1, 2, 3]")

    def test_markdown_fence_with_invalid_inner_json_raises(self):
        raw = "```json\n{bad json\n```"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_nested_fence_double_backtick_not_stripped(self):
        # Double backtick should not be stripped; the JSON may still be found
        # via the brace-search fallback
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"``\n{inner}\n``"
        # Not a triple-backtick fence; fallback brace search should still work
        result = cr.extract_json(raw)
        assert result["score"] == 85


# ===========================================================================
# review_pr tests
# ===========================================================================


class TestReviewPr:
    """Tests for cr.review_pr()"""

    def _setup_mocks(self, result_dict=None):
        result_dict = result_dict or MINIMAL_RESULT
        shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py ..."
        shared_stub.call_claude.return_value = json.dumps(result_dict)
        shared_stub.post_pr_comment.return_value = None

    def test_happy_path_approve(self):
        self._setup_mocks(MINIMAL_RESULT)
        result = cr.review_pr("acme", "myrepo", 42, "https://github.com/runs/1")
        assert result["merge_recommendation"] == "APPROVE"
        assert result["score"] == 85

    def test_happy_path_block(self):
        self._setup_mocks(FULL_RESULT)
        result = cr.review_pr("acme", "myrepo", 99, "https://github.com/runs/2")
        assert result["merge_recommendation"] == "BLOCK"
        assert result["score"] == 42

    def test_get_pr_diff_called_correctly(self):
        self._setup_mocks()
        cr.review_pr("owner1", "repo1", 7, "http://run")
        shared_stub.get_pr_diff.assert_called_once_with("owner1", "repo1", 7)

    def test_call_claude_called_with_diff(self):
        self._setup_mocks()
        cr.review_pr("owner1", "repo1", 7, "http://run")
        args, kwargs = shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[1]
        assert "diff --git" in args[1]

    def test_post_pr_comment_called_once(self):
        self._setup_mocks()
        cr.review_pr("owner1", "repo1", 7, "http://run")
        shared_stub.post_pr_comment.assert_called_once()

    def test_comment_contains_score(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("acme", "myrepo", 42, "http://run")
        _, kwargs = shared_stub.post_pr_comment.call_args
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "85" in comment_body

    def test_comment_contains_recommendation(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("acme", "myrepo", 42, "http://run")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_body

    def test_comment_contains_summary(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("acme", "myrepo", 42, "http://run")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "Looks good overall." in comment_body

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks({**MINIMAL_RESULT, "findings": []})
        cr.review_pr("acme", "myrepo", 42, "http://run")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_body

    def test_comment_with_findings(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("acme", "myrepo", 42, "http://run")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment_body
        assert "src/app.py" in comment_body

    def test_comment_no_positive_observations_shows_placeholder(self):
        self._setup_mocks({**MINIMAL_RESULT, "positive_observations": []})
        cr.review_pr("acme", "myrepo", 42, "http://run")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_body

    def test_comment_with_positive_observations(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("acme", "myrepo", 42, "http://run")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment_body

    def test_pr_number_passed_to_post_comment(self):
        self._setup_mocks()
        cr.review_pr("owner1", "repo1", 123, "http://run")
        args = shared_stub.post_pr_comment.call_args[0]
        assert args[2] == 123

    def test_returns_parsed_dict_not_raw