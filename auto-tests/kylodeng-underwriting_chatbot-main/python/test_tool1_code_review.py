"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fence stripping, outermost-brace extraction,
      newline-in-string cleaning, error conditions (no JSON, bad JSON)
    - review_pr(): happy path, comment formatting, return value
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): full report rendering, empty findings, empty iac/pos lists

Mocks used:
    - shared.call_claude          (patched via unittest.mock.patch)
    - shared.get_pr_diff          (patched)
    - shared.get_repo_files       (patched)
    - shared.post_pr_comment      (patched)
    - shared.write_output_file    (patched)
    - shared.send_email           (patched)
    - shared.write_audit_entry    (patched)
    - requests                    (patched where needed)

TODOs:
    - TODO: Integration tests for __main__ block require real env-var wiring
    - TODO: Tests for email dispatch path (requires SMTP/SES mock details from shared.send_email)
    - TODO: Tests for write_output_file / write_audit_entry side-effects need output repo schema
"""

import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Path bootstrap – mirror what the source file does
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# We mock the entire `shared` module before importing the module under test
# so that missing secrets / network calls never fire.
shared_mock = MagicMock()
shared_mock.OUTPUT_REPO_OWNER = "test-owner"
shared_mock.OUTPUT_REPO = "test-output-repo"
shared_mock.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_mock.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_mock

import importlib
import types

# Now safe to import the module under test
import tool1_code_review as cr

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several critical security issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/auth.py",
            "line": 17,
            "issue": "Hardcoded AWS secret key found.",
            "recommendation": "Use environment variables or secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "performance",
            "file": "src/db.py",
            "line": None,
            "issue": "N+1 query pattern detected.",
            "recommendation": "Use select_related or prefetch_related.",
        },
    ],
    "positive_observations": ["Clear module structure", "Docstrings present"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mock():
    """Reset shared mock call history before every test."""
    shared_mock.reset_mock()
    # Re-apply constants that reset_mock wipes
    shared_mock.OUTPUT_REPO_OWNER = "test-owner"
    shared_mock.OUTPUT_REPO = "test-output-repo"
    shared_mock.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared_mock.GH_API = "https://api.github.com"
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    # --- Happy path: clean JSON string ---
    def test_clean_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_clean_json_with_leading_trailing_whitespace(self):
        raw = "   " + json.dumps(MINIMAL_RESULT) + "   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    # --- Markdown fence stripping ---
    def test_strips_triple_backtick_fence(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_strips_triple_backtick_fence_no_language(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_strips_fence_with_extra_whitespace(self):
        raw = "```json\n   " + json.dumps(FULL_RESULT) + "\n   ```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "BLOCK"

    # --- Outermost brace extraction ---
    def test_extracts_json_with_preamble_text(self):
        payload = json.dumps(MINIMAL_RESULT)
        raw = f"Sure, here is the review:\n{payload}"
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_extracts_json_with_postamble_text(self):
        payload = json.dumps(MINIMAL_RESULT)
        raw = f"{payload}\n\nLet me know if you need anything else."
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_extracts_json_embedded_in_prose(self):
        payload = json.dumps({"summary": "ok", "score": 50,
                               "merge_recommendation": "APPROVE",
                               "findings": [], "positive_observations": [],
                               "iac_findings": []})
        raw = f"Here is my analysis:\n\n{payload}\n\nEnd of analysis."
        result = cr.extract_json(raw)
        assert result["score"] == 50

    # --- Newline cleaning inside string values ---
    def test_cleans_literal_newlines_inside_string_values(self):
        # Simulate a response where a string value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 70, ' \
              '"merge_recommendation": "APPROVE", "findings": [], ' \
              '"positive_observations": [], "iac_findings": []}'
        # This should either parse directly or after cleaning
        result = cr.extract_json(raw)
        assert result["score"] == 70

    # --- Error conditions ---
    def test_raises_value_error_when_no_json_found(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_raises_value_error_for_empty_string(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_raises_value_error_for_only_whitespace(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n  \t  ")

    def test_raises_value_error_for_malformed_json(self):
        raw = '{"summary": "broken", "score": }'
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_raises_value_error_for_truncated_json(self):
        raw = '{"summary": "truncated'
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_raises_value_error_for_markdown_fence_with_bad_json(self):
        raw = "```json\n{bad json here}\n```"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    # --- Edge / boundary values ---
    def test_empty_findings_list(self):
        payload = {**MINIMAL_RESULT, "findings": []}
        result = cr.extract_json(json.dumps(payload))
        assert result["findings"] == []

    def test_large_number_of_findings(self):
        findings = [
            {"severity": "LOW", "category": "maintainability",
             "file": f"src/file{i}.py", "line": i, "issue": "issue",
             "recommendation": "fix it"}
            for i in range(50)
        ]
        payload = {**MINIMAL_RESULT, "findings": findings}
        result = cr.extract_json(json.dumps(payload))
        assert len(result["findings"]) == 50

    def test_score_boundary_zero(self):
        payload = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(payload))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        payload = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(payload))
        assert result["score"] == 100

    def test_unicode_values(self):
        payload = {**MINIMAL_RESULT, "summary": "تقييم \u062c\u064a\u062f"}
        result = cr.extract_json(json.dumps(payload))
        assert "تقييم" in result["summary"]

    def test_nested_json_picks_outermost(self):
        """When JSON contains nested objects it should parse the whole thing."""
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2

    def test_finding_with_null_line(self):
        payload = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "correctness",
             "file": "app.py", "line": None,
             "issue": "Missing return", "recommendation": "Add return"}
        ]}
        result = cr.extract_json(json.dumps(payload))
        assert result["findings"][0]["line"] is None


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result=None):
        if result is None:
            result = FULL_RESULT
        shared_mock.get_pr_diff.return_value = "diff --git a/src/auth.py ..."
        shared_mock.call_claude.return_value = json.dumps(result)
        shared_mock.post_pr_comment.return_value = None
        return result

    def test_happy_path_returns_result(self):
        expected = self._setup_mocks()
        result = cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        assert result["score"] == expected["score"]
        assert result["merge_recommendation"] == "BLOCK"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 99, "https://ci/run/2")
        shared_mock.get_pr_diff.assert_called_once_with("myorg", "myrepo", 99)

    def test_calls_call_claude_with_diff_in_prompt(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 1, "https://ci/run/1")
        args = shared_mock.call_claude.call_args
        assert "Review this pull request diff" in args[0][1]
        assert "diff --git" in args[0][1]

    def test_calls_post_pr_comment(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 7, "https://ci/run/1")
        shared_mock.post_pr_comment.assert_called_once()
        call_args = shared_mock.post_pr_comment.call_args[0]
        assert call_args[0] == "myorg"
        assert call_args[1] == "myrepo"
        assert call_args[2] == 7

    def test_comment_contains_score(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://ci")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "42" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://ci")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "BLOCK" in comment

    def test_comment_contains_findings(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://ci")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "src/auth.py" in comment
        assert "CRITICAL" in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(MINIMAL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://ci")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_positive_observations(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://ci")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "Clear module structure" in comment

    def test_comment_contains_auto_generated_footer(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 1, "https://ci")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "Auto-generated" in comment

    def test_result_missing_fields_uses_defaults(self):
        """Claude returns a partial response – should not raise."""
        shared_mock.get_pr_diff.return_value = "diff"
        shared_mock.call_claude.return_value = json.dumps({"score": 55})
        shared_mock.post_pr_comment.return_value = None
        result = cr.review_pr("o", "r", 1, "url")
        assert result["score"] == 55

    def test_finding_line_null_rendered_as_na(self):
        result = {**FULL_RESULT, "findings": [
            {"severity": "HIGH", "category": "security",
             "file": "app.py", "line": None,
             "issue": "Bad thing", "recommendation": "Fix it"}
        ]}
        self._setup_mocks(result)
        cr.review_pr("o", "r", 1, "url")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "n/a" in comment

    def test_empty_positive_observations_shows_placeholder(self):
        result = {**FULL_RESULT, "positive_observations": []}
        self._setup_mocks(result)
        cr.review_pr("o", "r", 1, "url")
        comment = shared_mock.post_pr_comment.call_args[0][3]
        assert "_None_" in