"""
Test module for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fences, nested braces, newlines in strings,
      no JSON present, malformed JSON, boundary edge cases
    - review_pr(): happy path, Claude response handling, comment posting, return value
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): full report structure, empty findings, missing keys, IaC/positive sections

Mocks used:
    - shared.call_claude           (unittest.mock.patch)
    - shared.get_pr_diff           (unittest.mock.patch)
    - shared.get_repo_files        (unittest.mock.patch)
    - shared.post_pr_comment       (unittest.mock.patch)
    - shared.write_output_file     (unittest.mock.patch)
    - shared.send_email            (unittest.mock.patch)
    - shared.write_audit_entry     (unittest.mock.patch)
    - requests                     (not called directly in tested functions, but imported)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var wiring and GitHub credentials
    - TODO: Test email delivery path (send_email / email_html) once shared module contract is stable
    - TODO: Test write_output_file call sites once OUTPUT_REPO constants are injectable
"""

import json
import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal fake `shared` module so the import in
# tool1_code_review.py does not require real credentials or network access.
# ---------------------------------------------------------------------------

def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="{}")
    shared.get_repo_files = MagicMock(return_value={})
    shared.get_pr_diff = MagicMock(return_value="diff text")
    shared.write_output_file = MagicMock(return_value=None)
    shared.post_pr_comment = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "token fake"}
    shared.GH_API = "https://api.github.com"
    return shared


# Inject fake shared before importing the module under test
_fake_shared = _make_fake_shared()
sys.modules["shared"] = _fake_shared

# Also ensure requests is present (it is a standard lib dependency here)
import requests as _requests  # noqa: F401 – just ensuring it exists

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

import tool1_code_review as cr  # noqa: E402  (after sys.path manipulation)


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
            "file": "src/util.py",
            "line": None,
            "issue": "Bare except clause.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["CI pipeline configured", "Secrets manager in use"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role overly permissive"],
}


def _json_str(d: dict) -> str:
    return json.dumps(d)


# ---------------------------------------------------------------------------
# Tests: extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    """Happy paths, fences, malformed input, edge cases."""

    def test_plain_json_string(self):
        raw = _json_str(MINIMAL_RESULT)
        assert cr.extract_json(raw) == MINIMAL_RESULT

    def test_leading_trailing_whitespace(self):
        raw = f"   \n  {_json_str(MINIMAL_RESULT)}  \n  "
        assert cr.extract_json(raw) == MINIMAL_RESULT

    def test_markdown_triple_backtick_fence(self):
        raw = f"```\n{_json_str(MINIMAL_RESULT)}\n```"
        assert cr.extract_json(raw) == MINIMAL_RESULT

    def test_markdown_fence_with_language_tag(self):
        raw = f"```json\n{_json_str(MINIMAL_RESULT)}\n```"
        assert cr.extract_json(raw) == MINIMAL_RESULT

    def test_json_embedded_in_prose(self):
        raw = f"Here is the review:\n{_json_str(FULL_RESULT)}\nThat's all."
        assert cr.extract_json(raw) == FULL_RESULT

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a response where a string value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # After cleaning the newline should become a space and parse successfully
        result = cr.extract_json(raw)
        assert result["score"] == 50

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no braces.")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"key": "value", broken}')

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_braces_raises_value_error(self):
        # Single braces but not valid JSON
        with pytest.raises(ValueError):
            cr.extract_json("{not valid}")

    def test_nested_objects_parsed_correctly(self):
        nested = {"outer": {"inner": 1}, "list": [1, 2, 3]}
        raw = json.dumps(nested)
        assert cr.extract_json(raw) == nested

    def test_full_result_round_trip(self):
        raw = _json_str(FULL_RESULT)
        assert cr.extract_json(raw) == FULL_RESULT

    def test_minimal_result_round_trip(self):
        raw = _json_str(MINIMAL_RESULT)
        assert cr.extract_json(raw) == MINIMAL_RESULT

    def test_score_boundary_zero(self):
        d = {**MINIMAL_RESULT, "score": 0}
        assert cr.extract_json(_json_str(d))["score"] == 0

    def test_score_boundary_hundred(self):
        d = {**MINIMAL_RESULT, "score": 100}
        assert cr.extract_json(_json_str(d))["score"] == 100

    def test_extra_text_before_brace(self):
        raw = f"Sure! Here you go:\n\n{_json_str(MINIMAL_RESULT)}"
        result = cr.extract_json(raw)
        assert result["score"] == MINIMAL_RESULT["score"]

    def test_extra_text_after_brace(self):
        raw = f"{_json_str(MINIMAL_RESULT)}\n\nLet me know if you need more!"
        result = cr.extract_json(raw)
        assert result["score"] == MINIMAL_RESULT["score"]

    def test_fence_without_closing_fence(self):
        # Fence opened but not closed — should still attempt extraction
        raw = f"```json\n{_json_str(MINIMAL_RESULT)}"
        # split on \n gives the json part after first line; rsplit on ``` finds nothing,
        # so candidate is the whole remainder — valid JSON → should succeed
        result = cr.extract_json(raw)
        assert result["score"] == MINIMAL_RESULT["score"]


# ---------------------------------------------------------------------------
# Tests: review_pr
# ---------------------------------------------------------------------------


class TestReviewPr:
    """Tests for the review_pr function."""

    def setup_method(self):
        # Reset shared mocks before each test
        _fake_shared.get_pr_diff.reset_mock()
        _fake_shared.call_claude.reset_mock()
        _fake_shared.post_pr_comment.reset_mock()

    def test_happy_path_returns_result(self):
        _fake_shared.get_pr_diff.return_value = "diff content"
        _fake_shared.call_claude.return_value = _json_str(FULL_RESULT)

        result = cr.review_pr("myorg", "myrepo", 42, "https://run.url")

        assert result == FULL_RESULT
        _fake_shared.get_pr_diff.assert_called_once_with("myorg", "myrepo", 42)
        _fake_shared.post_pr_comment.assert_called_once()

    def test_comment_contains_score(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(FULL_RESULT)

        cr.review_pr("org", "repo", 1, "url")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "42" in comment_text
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_contains_summary(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(FULL_RESULT)

        cr.review_pr("org", "repo", 1, "url")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert FULL_RESULT["summary"] in comment_text

    def test_comment_contains_findings(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(FULL_RESULT)

        cr.review_pr("org", "repo", 1, "url")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment_text
        assert "Hardcoded password detected." in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        result_no_findings = {**MINIMAL_RESULT, "findings": []}
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(result_no_findings)

        cr.review_pr("org", "repo", 1, "url")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_positive_observations(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(FULL_RESULT)

        cr.review_pr("org", "repo", 1, "url")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "CI pipeline configured" in comment_text

    def test_comment_no_positive_observations_shows_none(self):
        result_no_pos = {**MINIMAL_RESULT, "positive_observations": []}
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(result_no_pos)

        cr.review_pr("org", "repo", 1, "url")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_post_pr_comment_called_with_correct_owner_repo_pr(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(MINIMAL_RESULT)

        cr.review_pr("acme", "platform", 99, "url")

        args = _fake_shared.post_pr_comment.call_args[0]
        assert args[0] == "acme"
        assert args[1] == "platform"
        assert args[2] == 99

    def test_finding_line_none_shows_na(self):
        result_null_line = {
            **MINIMAL_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "foo.py",
                    "line": None,
                    "issue": "issue text",
                    "recommendation": "fix text",
                }
            ],
        }
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(result_null_line)

        cr.review_pr("org", "repo", 1, "url")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "n/a" in comment_text

    def test_call_claude_receives_diff_content(self):
        _fake_shared.get_pr_diff.return_value = "UNIQUE_DIFF_CONTENT_XYZ"
        _fake_shared.call_claude.return_value = _json_str(MINIMAL_RESULT)

        cr.review_pr("org", "repo", 1, "url")

        claude_prompt = _fake_shared.call_claude.call_args[0][1]
        assert "UNIQUE_DIFF_CONTENT_XYZ" in claude_prompt

    def test_result_missing_optional_keys_does_not_raise(self):
        sparse = {"score": 50, "merge_recommendation": "APPROVE"}
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = _json_str(sparse)

        result = cr.review_pr("org", "repo", 1, "url")
        assert result["score"] == 50


# ---------------------------------------------------------------------------
# Tests: review_repo
# ---------------------------------------------------------------------------


class TestReviewRepo:
    """Tests for the review_repo function."""

    def setup_method(self):
        _fake_shared.get_repo_files.reset_mock()
        _fake_shared.call_claude.reset_mock()

    def test_happy_path_returns_parsed_result(self):
        _fake_shared.get_repo_files.return_value = {"main.py": "print('hello')"}
        _fake_shared.call_claude.return_value