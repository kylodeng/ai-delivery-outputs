"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, direct parse fallback
- review_pr: happy path, comment formatting, result passthrough
- review_repo: happy path, content truncation, file extension filtering
- get_output_url: URL construction
- build_report_md: happy path, empty findings, empty iac/positive, missing fields

Mocks used:
- shared.call_claude (patched via tool1_code_review module)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- requests (not called directly in the tested functions but imported)

TODOs:
- TODO: Integration test with real Claude API response shapes (need live credentials)
- TODO: Test __main__ block execution paths (need subprocess or importlib reload)
- TODO: Test email/audit side-effects triggered from __main__ (need full env setup)
"""

import json
import pytest
from unittest.mock import patch, MagicMock, call
import sys
import os

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without a real `shared` module
# ---------------------------------------------------------------------------

# We create a minimal fake `shared` module so the import in tool1_code_review
# doesn't blow up when the real shared.py or its dependencies aren't present.
import types

_fake_shared = types.ModuleType("shared")
_fake_shared.call_claude = MagicMock()
_fake_shared.get_repo_files = MagicMock()
_fake_shared.get_pr_diff = MagicMock()
_fake_shared.write_output_file = MagicMock()
_fake_shared.post_pr_comment = MagicMock()
_fake_shared.send_email = MagicMock()
_fake_shared.email_html = MagicMock()
_fake_shared.write_audit_entry = MagicMock()
_fake_shared.OUTPUT_REPO_OWNER = "test-owner"
_fake_shared.OUTPUT_REPO = "test-output-repo"
_fake_shared.GH_HEADERS = {"Authorization": "Bearer fake"}
_fake_shared.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _fake_shared)

# Now we can safely import the module under test
import importlib
import tool1_code_review as cr  # noqa: E402  (after sys.modules patch)


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Everything looks fine.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
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
    "positive_observations": ["Good docstrings", "Type hints used throughout"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role is overly permissive"],
}


def make_raw_json(result: dict) -> str:
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Tests for extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:

    def test_plain_json_string(self):
        raw = make_raw_json(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + make_raw_json(MINIMAL_VALID_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Everything looks fine."

    def test_json_wrapped_in_markdown_fences(self):
        raw = "```json\n" + make_raw_json(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_wrapped_in_plain_fences(self):
        raw = "```\n" + make_raw_json(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_with_preamble_text(self):
        raw = "Here is the JSON output:\n" + make_raw_json(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 55

    def test_json_with_trailing_commentary(self):
        raw = make_raw_json(MINIMAL_VALID_RESULT) + "\n\nHope that helps!"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_with_preamble_and_suffix(self):
        raw = (
            "Sure, here you go:\n"
            + make_raw_json(FULL_RESULT)
            + "\nLet me know if you need more."
        )
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a response where a string value contains a literal newline
        raw = '{"summary": "Line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # After cleaning the newline should be replaced with a space
        result = cr.extract_json(raw)
        assert result["score"] == 70

    def test_no_json_object_raises_value_error(self):
        raw = "This response contains absolutely no JSON."
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json(raw)

    def test_malformed_json_raises_value_error(self):
        raw = '{"score": 80, "summary": "missing closing brace"'
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_full_result_parsed_correctly(self):
        raw = make_raw_json(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket lacks versioning"

    def test_extra_curly_braces_outside_json(self):
        # Text like "see {this} for {details}" before the actual JSON
        raw = "see {invalid fragment} for details: " + make_raw_json(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fence_without_closing(self):
        # Opening fence but no closing — should still extract via brace search
        raw = "```json\n" + make_raw_json(MINIMAL_VALID_RESULT)
        # After split on first newline the text starts after the fence line;
        # rsplit on ``` with no closing returns the whole text — direct parse
        # should succeed after stripping the remaining fence text.
        # Behaviour: the function strips the opening fence line, then tries
        # direct parse. The remaining text is valid JSON so it should succeed.
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_score_zero_boundary(self):
        result_data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = cr.extract_json(make_raw_json(result_data))
        assert result["score"] == 0

    def test_score_hundred_boundary(self):
        result_data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = cr.extract_json(make_raw_json(result_data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {**MINIMAL_VALID_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "Issue.", "recommendation": "Fix it."}
        ]}
        result = cr.extract_json(make_raw_json(data))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_VALID_RESULT, "findings": []}
        result = cr.extract_json(make_raw_json(data))
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# Tests for review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:

    def _patch_all(self):
        """Return a context manager that patches all shared dependencies."""
        return {
            "call_claude": patch.object(cr, "call_claude", return_value=make_raw_json(FULL_RESULT)),
            "get_pr_diff": patch.object(cr, "get_pr_diff", return_value="diff content here"),
            "post_pr_comment": patch.object(cr, "post_pr_comment"),
        }

    def test_happy_path_returns_result(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(FULL_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff content"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            result = cr.review_pr("owner", "repo", 42, "https://run.url")
            assert result["score"] == 55
            assert result["merge_recommendation"] == "REQUEST_CHANGES"
            mock_comment.assert_called_once()

    def test_pr_comment_contains_score(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(FULL_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review_pr("owner", "repo", 1, "https://run.url")
            comment_text = mock_comment.call_args[0][3]
            assert "55/100" in comment_text

    def test_pr_comment_contains_recommendation(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(FULL_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review_pr("owner", "repo", 1, "https://run.url")
            comment_text = mock_comment.call_args[0][3]
            assert "REQUEST_CHANGES" in comment_text

    def test_pr_comment_contains_summary(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(FULL_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review_pr("owner", "repo", 1, "https://run.url")
            comment_text = mock_comment.call_args[0][3]
            assert "Several issues found." in comment_text

    def test_pr_comment_contains_findings(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(FULL_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review_pr("owner", "repo", 1, "https://run.url")
            comment_text = mock_comment.call_args[0][3]
            assert "src/main.py" in comment_text
            assert "Hardcoded password detected." in comment_text

    def test_pr_comment_no_findings_shows_placeholder(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(MINIMAL_VALID_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review_pr("owner", "repo", 1, "https://run.url")
            comment_text = mock_comment.call_args[0][3]
            assert "_No findings_" in comment_text

    def test_pr_comment_positive_observations(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(FULL_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review_pr("owner", "repo", 1, "https://run.url")
            comment_text = mock_comment.call_args[0][3]
            assert "Good docstrings" in comment_text

    def test_pr_comment_no_positive_observations_shows_placeholder(self):
        data = {**FULL_RESULT, "positive_observations": []}
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(data)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review_pr("owner", "repo", 1, "https://run.url")
            comment_text = mock_comment.call_args[0][3]
            assert "_None_" in comment_text

    def test_get_pr_diff_called_with_correct_args(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(MINIMAL_VALID_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff") as mock_diff,
            patch.object(cr, "post_pr_comment"),
        ):
            cr.review_pr("myowner", "myrepo", 99, "https://run.url")
            mock_diff.assert_called_once_with("myowner", "myrepo", 99)

    def test_post_pr_comment_called_with_correct_owner_repo_pr(self):
        with (
            patch.object(cr, "call_claude", return_value=make_raw_json(MINIMAL_VALID_RESULT)),
            patch.object(cr, "get_pr_diff", return_value="diff"),
            patch.object(cr, "post_pr_comment") as mock_comment,
        ):
            cr.review