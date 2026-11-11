"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, direct parse fallback
- review_pr(): happy path, comment formatting, result propagation
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): happy path, missing fields, empty findings/iac/observations,
  multiple findings, score/recommendation rendering

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- requests (not directly called in tested functions, but imported)

TODOs:
- TODO: test __main__ block (requires environment variable manipulation + subprocess)
- TODO: test integration with real Claude API responses
- TODO: test write_output_file / send_email orchestration in __main__ path
"""

import json
import re
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Make the script importable without executing __main__
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

import importlib, types

# We patch the `shared` module before importing tool1_code_review so that the
# module-level `from shared import ...` doesn't blow up in a test environment.
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
_fake_shared.GH_HEADERS = {}
_fake_shared.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _fake_shared)
sys.modules.setdefault("requests", MagicMock())

import tool1_code_review as cr  # noqa: E402  (import after path/mock setup)


# ---------------------------------------------------------------------------
# Shared fixtures & helpers
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Code looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
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
            "recommendation": "Split into smaller functions.",
        },
    ],
    "positive_observations": ["CI pipeline is configured.", "Dependencies are pinned."],
    "iac_findings": ["S3 bucket has no encryption.", "IAM role is overly permissive."],
}


def _make_json_str(obj: dict) -> str:
    return json.dumps(obj)


# ===========================================================================
# Tests for extract_json
# ===========================================================================


class TestExtractJson:
    """Happy-path and edge-case tests for extract_json()."""

    # --- happy paths --------------------------------------------------------

    def test_plain_json_string(self):
        raw = _make_json_str(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + _make_json_str(MINIMAL_VALID_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_json_wrapped_in_markdown_fences(self):
        raw = "```json\n" + _make_json_str(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_wrapped_in_plain_fences(self):
        raw = "```\n" + _make_json_str(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_full_result_round_trips(self):
        raw = _make_json_str(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_text_before_json_block(self):
        raw = "Here is my analysis:\n" + _make_json_str(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_text_after_json_block(self):
        raw = _make_json_str(MINIMAL_VALID_RESULT) + "\n\nSome trailing text."
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_text_before_and_after_json_block(self):
        raw = "Preamble text.\n" + _make_json_str(MINIMAL_VALID_RESULT) + "\nPostamble."
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    # --- newline-in-string cleaning ----------------------------------------

    def test_literal_newline_inside_string_value_is_cleaned(self):
        # Simulate Claude injecting a literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # direct json.loads will fail; extract_json should clean and succeed
        result = cr.extract_json(raw)
        assert "line one" in result["summary"]

    # --- error conditions ---------------------------------------------------

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_malformed_json_raises_value_error(self):
        raw = '{"summary": "broken", "score": }'  # invalid JSON
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_only_markdown_fence_no_json_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("```\nno json here\n```")

    def test_nested_braces_picks_outermost(self):
        inner = {"key": "value"}
        outer = {"outer": inner, "score": 99, "summary": "ok",
                 "merge_recommendation": "APPROVE", "findings": [],
                 "positive_observations": [], "iac_findings": []}
        raw = _make_json_str(outer)
        result = cr.extract_json(raw)
        assert result["score"] == 99
        assert result["outer"] == inner

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("    \t\n   ")

    # --- boundary values ----------------------------------------------------

    @pytest.mark.parametrize("score", [0, 1, 50, 99, 100])
    def test_score_boundary_values(self, score):
        obj = {**MINIMAL_VALID_RESULT, "score": score}
        result = cr.extract_json(_make_json_str(obj))
        assert result["score"] == score

    @pytest.mark.parametrize("rec", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_all_merge_recommendations(self, rec):
        obj = {**MINIMAL_VALID_RESULT, "merge_recommendation": rec}
        result = cr.extract_json(_make_json_str(obj))
        assert result["merge_recommendation"] == rec

    @pytest.mark.parametrize("sev", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_all_severity_levels_round_trip(self, sev):
        finding = {
            "severity": sev, "category": "security",
            "file": "foo.py", "line": 1,
            "issue": "An issue.", "recommendation": "Fix it.",
        }
        obj = {**MINIMAL_VALID_RESULT, "findings": [finding]}
        result = cr.extract_json(_make_json_str(obj))
        assert result["findings"][0]["severity"] == sev

    def test_line_null_is_preserved(self):
        finding = {
            "severity": "LOW", "category": "maintainability",
            "file": "foo.py", "line": None,
            "issue": "Long function.", "recommendation": "Refactor.",
        }
        obj = {**MINIMAL_VALID_RESULT, "findings": [finding]}
        result = cr.extract_json(_make_json_str(obj))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        obj = {**MINIMAL_VALID_RESULT, "findings": []}
        result = cr.extract_json(_make_json_str(obj))
        assert result["findings"] == []

    def test_many_findings(self):
        findings = [
            {"severity": "LOW", "category": "correctness",
             "file": f"file{i}.py", "line": i,
             "issue": "Minor issue.", "recommendation": "Small fix."}
            for i in range(50)
        ]
        obj = {**MINIMAL_VALID_RESULT, "findings": findings}
        result = cr.extract_json(_make_json_str(obj))
        assert len(result["findings"]) == 50


# ===========================================================================
# Tests for review_pr
# ===========================================================================


class TestReviewPr:
    """Tests for review_pr() — all external calls are mocked."""

    def setup_method(self):
        cr.get_pr_diff.reset_mock()
        cr.call_claude.reset_mock()
        cr.post_pr_comment.reset_mock()

    def test_happy_path_returns_result(self):
        cr.get_pr_diff.return_value = "diff --git a/foo.py ..."
        cr.call_claude.return_value = _make_json_str(MINIMAL_VALID_RESULT)
        cr.post_pr_comment.return_value = None

        result = cr.review_pr("owner", "repo", 42, "https://run.url")

        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        cr.get_pr_diff.return_value = "some diff"
        cr.call_claude.return_value = _make_json_str(MINIMAL_VALID_RESULT)
        cr.post_pr_comment.return_value = None

        cr.review_pr("myowner", "myrepo", 7, "https://run.url")

        cr.get_pr_diff.assert_called_once_with("myowner", "myrepo", 7)

    def test_calls_post_pr_comment(self):
        cr.get_pr_diff.return_value = "diff"
        cr.call_claude.return_value = _make_json_str(MINIMAL_VALID_RESULT)
        cr.post_pr_comment.return_value = None

        cr.review_pr("owner", "repo", 1, "https://run.url")

        assert cr.post_pr_comment.call_count == 1
        args = cr.post_pr_comment.call_args[0]
        assert args[0] == "owner"
        assert args[1] == "repo"
        assert args[2] == 1

    def test_comment_contains_score(self):
        cr.get_pr_diff.return_value = "diff"
        cr.call_claude.return_value = _make_json_str(FULL_RESULT)
        cr.post_pr_comment.return_value = None

        cr.review_pr("o", "r", 99, "url")

        comment_text = cr.post_pr_comment.call_args[0][3]
        assert "42" in comment_text
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_contains_summary(self):
        cr.get_pr_diff.return_value = "diff"
        cr.call_claude.return_value = _make_json_str(FULL_RESULT)
        cr.post_pr_comment.return_value = None

        cr.review_pr("o", "r", 1, "url")

        comment_text = cr.post_pr_comment.call_args[0][3]
        assert "Several security issues found." in comment_text

    def test_comment_contains_findings(self):
        cr.get_pr_diff.return_value = "diff"
        cr.call_claude.return_value = _make_json_str(FULL_RESULT)
        cr.post_pr_comment.return_value = None

        cr.review_pr("o", "r", 1, "url")

        comment_text = cr.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment_text
        assert "Hardcoded password detected." in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        obj = {**MINIMAL_VALID_RESULT, "findings": []}
        cr.get_pr_diff.return_value = "diff"
        cr.call_claude.return_value = _make_json_str(obj)
        cr.post_pr_comment.return_value = None

        cr.review_pr("o", "r", 1, "url")

        comment_text = cr.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_positive_observations_shown(self):
        cr.get_pr_diff.return_value = "diff"
        cr.call_claude.return_value = _make_json_str(FULL_RESULT)
        cr.post_pr_comment.return_value = None

        cr.review_pr("o", "r", 1, "url")

        comment_text = cr.post_pr_comment.call_args[0][3]
        assert "CI pipeline is