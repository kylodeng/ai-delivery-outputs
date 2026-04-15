"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown-fenced input, embedded newlines, missing braces, invalid JSON
- review_pr(): successful PR review flow, comment posting, result returned
- review_repo(): successful repo scan, content truncation to 20000 chars
- get_output_url(): URL construction
- build_report_md(): report markdown generation, all fields, missing fields, empty findings

Mocks used:
- shared.call_claude (patched)
- shared.get_pr_diff (patched)
- shared.get_repo_files (patched)
- shared.post_pr_comment (patched)
- shared.write_output_file (patched)
- shared.send_email (patched)
- shared.write_audit_entry (patched)
- requests (patched where needed)

TODOs:
- TODO: Integration test for __main__ block requires full env setup (REVIEW_MODE, GH_TOKEN, etc.)
- TODO: Test email sending path in __main__ once email logic is confirmed
- TODO: Test write_output_file path once full __main__ is visible (source truncated)
"""

import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the script directory is importable.  The source does sys.path.insert
# so we replicate that here before importing the module under test.
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")

# We need to stub out `shared` before importing tool1_code_review because the
# module-level `from shared import ...` would otherwise fail in a test env.
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude = MagicMock()
_shared_stub.get_repo_files = MagicMock()
_shared_stub.get_pr_diff = MagicMock()
_shared_stub.write_output_file = MagicMock()
_shared_stub.post_pr_comment = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock()
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)
sys.modules.setdefault("requests", MagicMock())

# Now insert the script directory and import the module
sys.path.insert(0, SCRIPT_DIR)

import importlib

# We import via importlib so the stub is already in sys.modules
tool1 = importlib.import_module("tool1_code_review")

extract_json = tool1.extract_json
review_pr = tool1.review_pr
review_repo = tool1.review_repo
get_output_url = tool1.get_output_url
build_report_md = tool1.build_report_md

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
    "score": 45,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 42,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or secrets manager.",
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
    "positive_observations": ["CI pipeline is configured", "Dependency pinning used"],
    "iac_findings": ["S3 bucket missing server-side encryption", "IAM policy is overly permissive"],
}


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for extract_json()"""

    def test_plain_valid_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_valid_json_with_whitespace(self):
        raw = "   " + json.dumps(MINIMAL_RESULT) + "   "
        result = extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fenced_no_language_label(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_text_preamble(self):
        raw = "Here is my analysis:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_with_trailing_garbage(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nSome trailing text."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_with_leading_and_trailing_text(self):
        raw = "Analysis complete.\n" + json.dumps(FULL_RESULT) + "\nDone."
        result = extract_json(raw)
        assert result["score"] == 45
        assert len(result["findings"]) == 2

    def test_newline_inside_string_value_cleaned(self):
        # Simulate a response where a string value has a literal newline
        broken = '{"summary": "first line\nsecond line", "score": 70}'
        # This should either parse directly (some parsers handle it) or be cleaned
        # Our function attempts to clean embedded newlines
        result = extract_json(broken)
        assert result["score"] == 70

    def test_no_json_object_raises_value_error(self):
        raw = "There is no JSON here at all."
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_invalid_json_structure_raises_value_error(self):
        raw = '{"summary": "test", "score": }'  # Malformed
        with pytest.raises((ValueError, json.JSONDecodeError)):
            extract_json(raw)

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["findings"][1]["line"] is None
        assert result["iac_findings"][0] == "S3 bucket missing server-side encryption"

    def test_markdown_fence_with_preamble_text(self):
        raw = "Sure, here you go:\n```json\n" + json.dumps(MINIMAL_RESULT) + "\n```\nHope this helps!"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_single_brace_no_closing(self):
        raw = '{ "summary": "incomplete json without closing'
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_multiple_json_objects_extracts_outermost(self):
        # When there are nested/multiple objects, outermost {} should be used
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert "summary" in result


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:
    """Tests for review_pr()"""

    def setup_method(self):
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.call_claude.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()

    def test_happy_path_returns_result(self):
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py ..."
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        result = review_pr("myorg", "myrepo", 42, "https://github.com/actions/runs/1")

        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 99, "https://run-url")

        _shared_stub.get_pr_diff.assert_called_once_with("owner1", "repo1", 99)

    def test_calls_post_pr_comment(self):
        _shared_stub.get_pr_diff.return_value = "diff content"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 7, "https://run-url")

        _shared_stub.post_pr_comment.assert_called_once()
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "owner1"
        assert args[1] == "repo1"
        assert args[2] == 7

    def test_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        review_pr("o", "r", 1, "url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "45" in comment
        assert "REQUEST_CHANGES" in comment

    def test_comment_contains_findings(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        review_pr("o", "r", 1, "url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded AWS secret key detected." in comment
        assert "src/app.py" in comment

    def test_comment_no_findings_shows_placeholder(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        review_pr("o", "r", 1, "url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_contains_positive_observations(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        review_pr("o", "r", 1, "url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "CI pipeline is configured" in comment

    def test_finding_with_null_line_shows_na(self):
        result_with_null_line = {
            **MINIMAL_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "foo.py",
                    "line": None,
                    "issue": "Too complex.",
                    "recommendation": "Simplify.",
                }
            ],
        }
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(result_with_null_line)

        review_pr("o", "r", 1, "url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment

    def test_claude_returns_malformed_json_raises(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "Not JSON at all."

        with pytest.raises(ValueError):
            review_pr("o", "r", 1, "url")

    def test_calls_claude_with_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "my unique diff content"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        review_pr("o", "r", 1, "url")

        claude_call_args = _shared_stub.call_claude.call_args
        assert "my unique diff content" in claude_call_args[0][1]

    def test_result_contains_all_keys(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        result = review_pr("o", "r", 1, "url")

        for key in ("summary", "score", "merge_recommendation", "findings",
                    "positive_observations", "iac_findings"):
            assert key in result

    def test_missing_optional_fields_in_result_handled_gracefully(self):
        sparse = {"score": 60, "merge_recommendation": "APPROVE"}
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(sparse)

        result = review_pr("o", "r", 1, "url")
        assert result["score"] == 60
        # comment should still post without KeyError
        _shared_stub.post_pr_comment.assert_called_once()


# ---------------------------------------------------------------------------
# review_repo tests
# ---------------------------------------------------------------------------

class TestReviewRepo:
    """Tests for review_repo()"""

    def setup_method(self):
        _shared_stub.get_repo_files.reset_mock()
        _shared_stub.call_claude.reset_mock()

    def test_happy_path_returns_result(self):