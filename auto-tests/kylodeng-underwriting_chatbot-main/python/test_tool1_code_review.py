"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  no JSON found, malformed JSON, edge cases with whitespace
- review_pr(): happy path, Claude response handling, comment posting, return value
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report, empty findings, missing keys, all recommendation types

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
- TODO: Integration tests for __main__ block require environment variable setup
  and full shared module — skipped here.
- TODO: test_review_pr_with_real_diff requires a live GitHub token — skipped.
- TODO: test_review_repo_rate_limit requires Claude rate-limit simulation — skipped.
"""

import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Path setup so we can import the module without the full GitHub Actions env
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))

# Stub out the `shared` module before importing tool1_code_review so that
# missing environment variables / network calls don't blow up on import.
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
_shared_stub.GH_HEADERS = {"Authorization": "token test"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)
sys.modules.setdefault("requests", MagicMock())

import importlib
import tool1_code_review as t1  # noqa: E402  (import after path/stub setup)

# ---------------------------------------------------------------------------
# Shared fixtures / constants
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several critical issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or AWS Secrets Manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "main.tf",
            "line": None,
            "issue": "S3 bucket is publicly accessible.",
            "recommendation": "Set block_public_acls to true.",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "app/utils.py",
            "line": 7,
            "issue": "Bare except clause swallows all exceptions.",
            "recommendation": "Catch specific exception types.",
        },
    ],
    "positive_observations": [
        "Comprehensive logging in place.",
        "All secrets referenced via environment variables in new code.",
    ],
    "iac_findings": [
        "Missing mandatory cost-allocation tags on EC2 instances.",
        "IAM role has wildcard actions on S3.",
    ],
}


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------


class TestExtractJson:
    """Tests for extract_json()."""

    def test_plain_valid_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "   \n"
        result = t1.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_markdown_fence_backtick_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fence_plain_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_extra_text_before_and_after_json(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd of review."
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a response with a literal newline inside a string value
        broken = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # json.loads would fail on this; extract_json should clean and succeed
        result = t1.extract_json(broken)
        assert result["score"] == 50

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("This response contains no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json('{"key": "value", broken}')

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_nested_text_around_json(self):
        """Handles preamble text with braces in it."""
        preamble = "Result (see config {a:1}): "
        raw = preamble + json.dumps(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_score_zero(self):
        data = dict(MINIMAL_RESULT, score=0, merge_recommendation="BLOCK")
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = dict(MINIMAL_RESULT, score=100, merge_recommendation="APPROVE")
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_markdown_fence_no_trailing_newline(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "```"
        result = t1.extract_json(raw)
        assert "score" in result

    def test_only_opening_brace_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("{ this is not json at all ...")

    def test_unicode_in_values(self):
        data = dict(MINIMAL_RESULT, summary="إلغاء تأكيد متابعة")
        result = t1.extract_json(json.dumps(data))
        assert "إلغاء" in result["summary"]


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------


class TestReviewPr:
    """Tests for review_pr()."""

    @pytest.fixture(autouse=True)
    def reset_mocks(self):
        _shared_stub.call_claude.reset_mock()
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()
        yield

    def test_happy_path_returns_result(self):
        _shared_stub.get_pr_diff.return_value = "diff --git a/foo.py ..."
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        result = t1.review_pr("acme", "myrepo", 7, "https://ci/run/1")

        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_post_pr_comment_called_once(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        t1.review_pr("acme", "myrepo", 7, "https://ci/run/1")

        _shared_stub.post_pr_comment.assert_called_once()
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "acme"
        assert args[1] == "myrepo"
        assert args[2] == 7

    def test_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        t1.review_pr("acme", "myrepo", 99, "https://ci/run/2")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "42/100" in comment_text

    def test_comment_contains_recommendation(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        t1.review_pr("acme", "myrepo", 99, "https://ci/run/2")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "BLOCK" in comment_text

    def test_comment_contains_findings(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        t1.review_pr("acme", "myrepo", 99, "https://ci/run/2")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded AWS secret key detected." in comment_text

    def test_no_findings_shows_no_findings_placeholder(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        t1.review_pr("acme", "myrepo", 1, "https://ci/run/3")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_call_claude_passes_diff(self):
        _shared_stub.get_pr_diff.return_value = "my specific diff content"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        t1.review_pr("org", "repo", 5, "https://ci")

        claude_prompt = _shared_stub.call_claude.call_args[0][1]
        assert "my specific diff content" in claude_prompt

    def test_claude_returns_markdown_fenced_json(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = (
            "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        )

        result = t1.review_pr("acme", "myrepo", 3, "https://ci")
        assert result["score"] == 80

    def test_claude_error_propagates(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.side_effect = RuntimeError("Claude unavailable")

        with pytest.raises(RuntimeError, match="Claude unavailable"):
            t1.review_pr("acme", "myrepo", 3, "https://ci")

    def test_missing_optional_keys_in_result(self):
        """review_pr should not crash when Claude omits optional keys."""
        minimal = {"score": 60, "merge_recommendation": "APPROVE"}
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(minimal)

        result = t1.review_pr("acme", "myrepo", 10, "https://ci")
        assert result["score"] == 60

    def test_line_null_renders_na(self):
        result_with_null_line = dict(
            FULL_RESULT,
            findings=[
                {
                    "severity": "HIGH",
                    "category": "security",
                    "file": "foo.py",
                    "line": None,
                    "issue": "Null line issue.",
                    "recommendation": "Fix it.",
                }
            ],
        )
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(result_with_null_line)

        t1.review_pr("acme", "myrepo", 2, "https://ci")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment_text

    def test_positive_observations_in_comment(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        t1.review_pr("acme", "myrepo", 9, "https://ci")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Comprehensive logging in place." in comment_text

    def test_empty_positive_observations_shows_placeholder(self):
        data = dict(MINIMAL_RESULT, positive_observations=[])
        _shared_stub.get_pr_diff.return_value = "diff"