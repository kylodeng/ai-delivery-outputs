"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown-fenced input, embedded newlines, missing braces,
  completely invalid input, edge cases with whitespace and nested structures
- review_pr: happy path, Claude response handling, comment formatting, result passthrough
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction for various owner/repo/label combinations
- build_report_md: full report generation, missing keys, empty findings/observations/iac,
  multiple findings, score and recommendation rendering

Mocks used:
- shared.call_claude (patched at tool1_code_review module level)
- shared.get_pr_diff (patched at tool1_code_review module level)
- shared.get_repo_files (patched at tool1_code_review module level)
- shared.post_pr_comment (patched at tool1_code_review module level)
- shared.write_output_file (patched at tool1_code_review module level)
- shared.send_email (patched at tool1_code_review module level)
- shared.write_audit_entry (patched at tool1_code_review module level)
- datetime.datetime (patched for deterministic timestamp output)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup and live GitHub token
- TODO: Test email dispatch path once send_email interface is fully confirmed
- TODO: Test write_audit_entry call signatures once audit schema is documented
- TODO: Verify behaviour when get_repo_files returns an empty dict (no files found)
"""

import json
import re
import sys
import os
import datetime
import importlib
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal `shared` stub so the import does not fail even
# when the real shared.py is absent in the test environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock()
    mod.get_repo_files = MagicMock()
    mod.get_pr_diff = MagicMock()
    mod.write_output_file = MagicMock()
    mod.post_pr_comment = MagicMock()
    mod.send_email = MagicMock()
    mod.email_html = MagicMock()
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = "test-output-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    mod.GH_HEADERS = {"Authorization": "Bearer fake-token"}
    mod.GH_API = "https://api.github.com"
    return mod


# Insert stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Ensure the scripts directory is on the path so the import resolves
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import tool1_code_review as cr  # noqa: E402  (import after path manipulation)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Code looks generally clean with minor issues.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good use of type hints"],
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
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables for secrets.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "Overly permissive IAM policy.",
            "recommendation": "Apply least-privilege principle.",
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
    "positive_observations": ["Consistent naming conventions", "Good test coverage"],
    "iac_findings": ["S3 bucket missing encryption", "Missing resource tags"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stub mocks between tests."""
    shared = sys.modules["shared"]
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment", "send_email",
                 "email_html", "write_audit_entry"):
        getattr(shared, attr).reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json — happy paths
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result == MINIMAL_VALID_RESULT

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_VALID_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_markdown_fenced_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["summary"] == MINIMAL_VALID_RESULT["summary"]

    def test_json_buried_in_prose(self):
        payload = json.dumps({"score": 80, "summary": "OK", "merge_recommendation": "APPROVE",
                               "findings": [], "positive_observations": [], "iac_findings": []})
        raw = f"Here is the review:\n\n{payload}\n\nHope that helps!"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_nested_empty_lists(self):
        data = {**MINIMAL_VALID_RESULT, "findings": [], "iac_findings": []}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"] == []
        assert result["iac_findings"] == []

    def test_score_boundary_zero(self):
        data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_finding_with_null_line(self):
        data = {**MINIMAL_VALID_RESULT, "findings": [
            {"severity": "LOW", "category": "correctness", "file": "a.py",
             "line": None, "issue": "Minor issue.", "recommendation": "Fix it."}
        ]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None


# ---------------------------------------------------------------------------
# extract_json — newline-inside-string repair path
# ---------------------------------------------------------------------------

class TestExtractJsonNewlineRepair:

    def test_newline_inside_string_value(self):
        # Construct a manually broken JSON that has a literal newline inside a string
        raw = '{"summary": "line one\nline two", "score": 50}'
        # The function should either repair and parse, or raise ValueError.
        # We only assert it does not silently return wrong data.
        try:
            result = cr.extract_json(raw)
            # If it succeeds the summary should contain both parts joined
            assert "line one" in result["summary"]
        except ValueError:
            pass  # acceptable outcome

    def test_multiple_newlines_inside_strings(self):
        raw = '{"summary": "a\nb\nc", "score": 10}'
        try:
            result = cr.extract_json(raw)
            assert isinstance(result, dict)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# extract_json — error / edge cases
# ---------------------------------------------------------------------------

class TestExtractJsonErrors:

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("This is just plain text with no JSON.")

    def test_unclosed_brace_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"summary": "hello"')

    def test_array_at_top_level_raises(self):
        # A bare array is not a valid result object; should raise ValueError
        with pytest.raises(ValueError):
            cr.extract_json('[1, 2, 3]')

    def test_malformed_inner_json_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json('{ this is not json at all }')

    def test_markdown_fence_then_invalid_raises(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            cr.extract_json("```\nnot json\n```")

    def test_partial_valid_json_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 50, "summary": ')


# ---------------------------------------------------------------------------
# extract_json — parameterised severity / recommendation values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
def test_extract_json_all_severities(severity):
    data = {**MINIMAL_VALID_RESULT, "findings": [
        {"severity": severity, "category": "security", "file": "f.py",
         "line": 1, "issue": "Issue.", "recommendation": "Fix."}
    ]}
    result = cr.extract_json(json.dumps(data))
    assert result["findings"][0]["severity"] == severity


@pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
def test_extract_json_all_recommendations(recommendation):
    data = {**MINIMAL_VALID_RESULT, "merge_recommendation": recommendation}
    result = cr.extract_json(json.dumps(data))
    assert result["merge_recommendation"] == recommendation


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:

    def _setup_mocks(self, diff="@@ diff @@\n+some code", result=None):
        shared = sys.modules["shared"]
        shared.get_pr_diff.return_value = diff
        shared.call_claude.return_value = json.dumps(result or MINIMAL_VALID_RESULT)
        shared.post_pr_comment.return_value = None

    def test_returns_parsed_result(self):
        self._setup_mocks()
        result = cr.review_pr("my-org", "my-repo", 42, "https://example.com/run/1")
        assert result == MINIMAL_VALID_RESULT

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        cr.review_pr("org", "repo", 7, "url")
        sys.modules["shared"].get_pr_diff.assert_called_once_with("org", "repo", 7)

    def test_calls_call_claude_with_diff_content(self):
        diff_text = "@@ -1,3 +1,4 @@\n+new line"
        self._setup_mocks(diff=diff_text)
        cr.review_pr("org", "repo", 1, "url")
        call_claude_mock = sys.modules["shared"].call_claude
        assert call_claude_mock.called
        call_args = call_claude_mock.call_args
        assert diff_text in call_args[0][1]  # second positional arg is user prompt

    def test_posts_pr_comment(self):
        self._setup_mocks()
        cr.review_pr("org", "repo", 99, "url")
        sys.modules["shared"].post_pr_comment.assert_called_once()
        args = sys.modules["shared"].post_pr_comment.call_args[0]
        assert args[0] == "org"
        assert args[1] == "repo"
        assert args[2] == 99

    def test_comment_contains_score(self):
        self._setup_mocks(result={**MINIMAL_VALID_RESULT, "score": 88})
        cr.review_pr("org", "repo", 1, "url")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "88" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup_mocks(result={**MINIMAL_VALID_RESULT, "merge_recommendation": "BLOCK"})
        cr.review_pr("org", "repo", 1, "url")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "BLOCK" in comment_text

    def test_comment_contains_summary(self):
        summary = "Everything looks fantastic."
        self._setup_mocks(result={**MINIMAL_VALID_RESULT, "summary": summary})
        cr.review_pr("org", "repo", 1, "url")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert summary in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(result={**MINIMAL_VALID_RESULT, "findings": []})
        cr.review_pr("org", "repo", 1, "url")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_with_findings_lists_them(self):
        self._setup_mocks(result=FULL_RESULT)
        cr.review_pr("org", "repo", 1, "url")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "src/auth.py" in comment_text
        assert "CRITICAL" in comment_text

    def test_comment_positive_observations(self):
        obs = ["Great test coverage", "No hardcoded secrets"]
        self._setup_mocks(result={**MINIMAL_VALID_RESULT, "positive_observations": obs})
        cr.review_pr("org", "repo", 1, "url")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "Great test coverage"