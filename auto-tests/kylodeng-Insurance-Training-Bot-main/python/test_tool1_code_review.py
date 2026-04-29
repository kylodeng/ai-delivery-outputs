"""
Test module for tool1_code_review.py

What is tested:
- extract_json: happy path (plain JSON, markdown-fenced JSON, JSON with surrounding text,
  JSON containing literal newlines inside string values), edge cases (empty input,
  no JSON object, invalid JSON that cannot be repaired), boundary values.
- review_pr: happy path, Claude returning bad JSON, PR comment posting.
- review_repo: happy path, file truncation behaviour, token budget.
- get_output_url: URL construction.
- build_report_md: full report with findings, empty findings, missing keys.

Mocks used:
- shared.call_claude           (unittest.mock.patch)
- shared.get_pr_diff           (unittest.mock.patch)
- shared.get_repo_files        (unittest.mock.patch)
- shared.post_pr_comment       (unittest.mock.patch)
- shared.write_output_file     (unittest.mock.patch)
- shared.send_email            (unittest.mock.patch)
- shared.write_audit_entry     (unittest.mock.patch)
- requests (not directly called by the functions under test, patched at module level)

TODOs:
- TODO: Integration test for __main__ block requires full env-var wiring and a live
  shared module – stub provided below.
- TODO: Test email formatting (email_html) requires shared.email_html implementation details.
"""

import json
import sys
import os
import importlib
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the script directory is importable (mirrors the sys.path.insert in
# the source file itself so we can import without a real 'shared' module).
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")

# ---------------------------------------------------------------------------
# Build a minimal fake 'shared' module so we never import the real one.
# ---------------------------------------------------------------------------
import types

_shared = types.ModuleType("shared")
_shared.call_claude = MagicMock()
_shared.get_repo_files = MagicMock()
_shared.get_pr_diff = MagicMock()
_shared.write_output_file = MagicMock()
_shared.post_pr_comment = MagicMock()
_shared.send_email = MagicMock()
_shared.email_html = MagicMock(return_value="<html></html>")
_shared.write_audit_entry = MagicMock()
_shared.OUTPUT_REPO_OWNER = "test-owner"
_shared.OUTPUT_REPO = "test-output-repo"
_shared.GH_HEADERS = {"Authorization": "Bearer fake"}
_shared.GH_API = "https://api.github.com"

sys.modules["shared"] = _shared

# Now import the module under test
sys.path.insert(0, SCRIPT_DIR)
import tool1_code_review as cr  # noqa: E402  (import after sys.path manipulation)


# ---------------------------------------------------------------------------
# Helpers / fixtures
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
    "summary": "Several security issues found.",
    "score": 45,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or AWS Secrets Manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket has public ACL.",
            "recommendation": "Set bucket ACL to private.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils/helper.py",
            "line": 55,
            "issue": "Function has no docstring.",
            "recommendation": "Add a docstring explaining function purpose.",
        },
    ],
    "positive_observations": ["Consistent naming convention", "Good use of type hints"],
    "iac_findings": ["Missing encryption on RDS instance", "No VPC flow logs enabled"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared mocks before every test."""
    _shared.call_claude.reset_mock()
    _shared.get_repo_files.reset_mock()
    _shared.get_pr_diff.reset_mock()
    _shared.write_output_file.reset_mock()
    _shared.post_pr_comment.reset_mock()
    _shared.send_email.reset_mock()
    _shared.write_audit_entry.reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json – happy paths
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n  "
        result = cr.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_markdown_fenced_json_triple_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fenced_json_with_language_tag(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd of review."
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_findings_with_null_line(self):
        data = dict(MINIMAL_RESULT)
        data["findings"] = [
            {
                "severity": "LOW",
                "category": "maintainability",
                "file": "foo.py",
                "line": None,
                "issue": "Missing docstring.",
                "recommendation": "Add one.",
            }
        ]
        raw = json.dumps(data)
        result = cr.extract_json(raw)
        assert result["findings"][0]["line"] is None

    def test_newline_inside_string_value_repaired(self):
        # Simulate Claude inserting a literal newline inside a JSON string value
        raw = '{"summary": "first line\nsecond line", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This may or may not parse directly – exercise the repair path
        result = cr.extract_json(raw)
        assert result["score"] == 70

    def test_json_score_zero(self):
        data = dict(MINIMAL_RESULT, score=0)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_json_score_100(self):
        data = dict(MINIMAL_RESULT, score=100)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_extra_text_before_brace(self):
        raw = "Sure! Here you go:\n\n" + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_extra_text_after_brace(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nHope that helps!"
        result = cr.extract_json(raw)
        assert result["score"] == 80


# ---------------------------------------------------------------------------
# extract_json – error / edge cases
# ---------------------------------------------------------------------------

class TestExtractJsonErrorCases:

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("   \n\t  ")

    def test_plain_text_no_braces_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is not JSON at all.")

    def test_array_only_raises(self):
        # A bare JSON array (no outer {}) should raise
        with pytest.raises(ValueError):
            cr.extract_json("[1, 2, 3]")

    def test_invalid_json_inside_braces_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("{totally: broken json !!!}")

    def test_markdown_fence_with_garbage_content_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("```\nnot json at all\n```")

    def test_truncated_json_raises(self):
        raw = json.dumps(FULL_RESULT)[:50]  # Deliberately truncate
        with pytest.raises((ValueError, json.JSONDecodeError)):
            cr.extract_json(raw)

    def test_empty_braces_returns_empty_dict(self):
        result = cr.extract_json("{}")
        assert result == {}

    def test_nested_json_objects(self):
        data = dict(MINIMAL_RESULT)
        data["meta"] = {"tool": "claude", "version": 3}
        raw = json.dumps(data)
        result = cr.extract_json(raw)
        assert result["meta"]["tool"] == "claude"


# ---------------------------------------------------------------------------
# extract_json – markdown fence variants
# ---------------------------------------------------------------------------

class TestExtractJsonMarkdownVariants:

    def test_fence_with_trailing_newline(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```\n"
        result = cr.extract_json(raw)
        assert isinstance(result, dict)

    def test_fence_without_language(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_double_fenced_response(self):
        # If model wraps content in fences and also has extra text
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"```json\n{inner}\n```\n\nSome trailing note."
        result = cr.extract_json(raw)
        assert result["score"] == 80


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:

    def test_happy_path_returns_result(self):
        _shared.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+x = 1"
        _shared.call_claude.return_value = json.dumps(FULL_RESULT)

        result = cr.review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")

        assert result["score"] == 45
        assert result["merge_recommendation"] == "BLOCK"

    def test_calls_get_pr_diff_with_correct_args(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("owner1", "repo1", 7, "https://ci/run")

        _shared.get_pr_diff.assert_called_once_with("owner1", "repo1", 7)

    def test_calls_post_pr_comment(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("owner1", "repo1", 7, "https://ci/run")

        _shared.post_pr_comment.assert_called_once()
        call_args = _shared.post_pr_comment.call_args
        assert call_args[0][0] == "owner1"
        assert call_args[0][1] == "repo1"
        assert call_args[0][2] == 7

    def test_comment_contains_score(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(FULL_RESULT)

        cr.review_pr("owner1", "repo1", 1, "https://ci/run")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "45" in comment
        assert "BLOCK" in comment

    def test_comment_contains_findings(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(FULL_RESULT)

        cr.review_pr("owner1", "repo1", 1, "https://ci/run")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment
        assert "src/app.py" in comment

    def test_comment_no_findings_shows_placeholder(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        cr.review_pr("owner1", "repo1", 1, "https://ci/run")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_claude_bad_json_raises(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = "This is not JSON"

        with pytest.raises(ValueError):
            cr.review_pr("owner1", "repo1", 1, "https://ci/run")

    def test_positive_observations_in_comment(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(FULL_RESULT)

        cr.review_pr("owner1", "repo1", 1, "https://ci/run")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "Consistent naming convention" in comment

    def test_empty_findings_list(self):
        data = dict(FULL_RESULT, findings=[])
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(data)

        result = cr.review_pr("owner1", "repo1", 1, "https://ci/run")
        assert result["findings"] == []

    def test_result_missing_optional_keys(self):
        minimal = {"score": 60, "merge_recommendation": "APPROVE"}
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(minimal)

        # Should not raise even with missing optional keys
        result = cr.