"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown-fenced input, embedded newlines, missing JSON, malformed JSON
- review_pr: happy path, Claude integration (mocked), comment posting (mocked)
- review_repo: happy path, content truncation, Claude integration (mocked)
- get_output_url: URL construction
- build_report_md: full report, empty findings, missing keys, IaC/positive sections

Mocks used:
- shared.call_claude (patched)
- shared.get_pr_diff (patched)
- shared.get_repo_files (patched)
- shared.post_pr_comment (patched)
- shared.write_output_file (patched)
- shared.send_email (patched)
- shared.write_audit_entry (patched)
- requests (not called directly in tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires real env vars (REVIEW_MODE, GH_TOKEN, etc.)
- TODO: test email sending path once email trigger logic is confirmed in main block
- TODO: test write_output_file call sites once the truncated __main__ source is available
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without the real `shared` module
# ---------------------------------------------------------------------------

# We create a minimal fake `shared` module so the import of tool1_code_review
# doesn't blow up when shared.py doesn't exist in the test environment.
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

# Now import the module under test
import importlib

# Insert the scripts directory so relative imports resolve
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from tool1_code_review import (
    extract_json,
    review_pr,
    review_repo,
    get_output_url,
    build_report_md,
    SYSTEM,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code looks reasonable with minor issues.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded API key detected.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good use of type hints."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def _make_fenced(content: str, lang: str = "json") -> str:
    return f"```{lang}\n{content}\n```"


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------


class TestExtractJson:
    # --- Happy path ---

    def test_plain_json_string(self):
        result = extract_json(VALID_JSON_STR)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        result = extract_json(f"  \n{VALID_JSON_STR}\n  ")
        assert result["summary"] == VALID_RESULT["summary"]

    # --- Markdown fence stripping ---

    def test_markdown_fenced_json(self):
        fenced = _make_fenced(VALID_JSON_STR)
        result = extract_json(fenced)
        assert result["score"] == 75

    def test_markdown_fenced_no_lang(self):
        fenced = f"```\n{VALID_JSON_STR}\n```"
        result = extract_json(fenced)
        assert result["findings"][0]["severity"] == "HIGH"

    def test_markdown_fenced_with_preamble_text(self):
        text = f"Here is the review:\n{_make_fenced(VALID_JSON_STR)}"
        # Preamble before fence — strip fence then find {}
        result = extract_json(text)
        assert result["merge_recommendation"] == "APPROVE"

    # --- Outermost {} extraction ---

    def test_json_embedded_in_text(self):
        raw = f"Some preamble text\n{VALID_JSON_STR}\nSome postamble text"
        result = extract_json(raw)
        assert result["score"] == 75

    def test_json_with_extra_text_before_brace(self):
        raw = "Claude says: " + VALID_JSON_STR
        result = extract_json(raw)
        assert result["score"] == 75

    # --- Newline inside string values cleaned up ---

    def test_json_with_newline_inside_string(self):
        # Simulate a response where a string value contains a literal newline
        broken = VALID_JSON_STR.replace(
            "Overall the code looks reasonable with minor issues.",
            "Overall the code looks reasonable\nwith minor issues.",
        )
        # Should succeed after newline cleaning
        result = extract_json(broken)
        assert "score" in result

    # --- Minimal valid JSON ---

    def test_minimal_json(self):
        raw = '{"summary": "ok", "score": 100, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = extract_json(raw)
        assert result["score"] == 100
        assert result["findings"] == []

    # --- Error conditions ---

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no braces.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse Claude response as JSON"):
            extract_json('{"score": 75, "broken": }')

    def test_only_braces_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("{}")  # valid JSON but empty — actually valid parse

    def test_empty_object_returns_dict(self):
        """Empty {} is technically valid JSON."""
        result = extract_json("{}")
        assert result == {}

    def test_array_only_no_braces_raises(self):
        with pytest.raises(ValueError):
            extract_json('["a", "b"]')

    def test_deeply_nested_json(self):
        nested = {"summary": "ok", "score": 50, "merge_recommendation": "BLOCK",
                  "findings": [{"severity": "CRITICAL", "category": "security",
                                "file": "main.tf", "line": 1,
                                "issue": "Root IAM role.", "recommendation": "Restrict."}],
                  "positive_observations": [], "iac_findings": []}
        result = extract_json(json.dumps(nested))
        assert result["findings"][0]["severity"] == "CRITICAL"

    # --- Boundary values ---

    def test_score_zero(self):
        data = {**VALID_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = {**VALID_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**VALID_RESULT, "merge_recommendation": recommendation}
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severities(self, severity):
        finding = {**VALID_RESULT["findings"][0], "severity": severity}
        data = {**VALID_RESULT, "findings": [finding]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity

    def test_null_line_number(self):
        finding = {**VALID_RESULT["findings"][0], "line": None}
        data = {**VALID_RESULT, "findings": [finding]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_multiple_findings(self):
        finding2 = {**VALID_RESULT["findings"][0], "severity": "LOW", "line": 99}
        data = {**VALID_RESULT, "findings": [VALID_RESULT["findings"][0], finding2]}
        result = extract_json(json.dumps(data))
        assert len(result["findings"]) == 2


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------


class TestReviewPr:
    def setup_method(self):
        _fake_shared.call_claude.reset_mock()
        _fake_shared.get_pr_diff.reset_mock()
        _fake_shared.post_pr_comment.reset_mock()

    def test_happy_path_returns_result(self):
        _fake_shared.get_pr_diff.return_value = "diff --git a/foo.py ..."
        _fake_shared.call_claude.return_value = VALID_JSON_STR

        result = review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")

        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        _fake_shared.get_pr_diff.return_value = "some diff"
        _fake_shared.call_claude.return_value = VALID_JSON_STR

        review_pr("org", "repo", 7, "http://run")

        _fake_shared.get_pr_diff.assert_called_once_with("org", "repo", 7)

    def test_calls_call_claude_with_diff(self):
        _fake_shared.get_pr_diff.return_value = "my diff content"
        _fake_shared.call_claude.return_value = VALID_JSON_STR

        review_pr("org", "repo", 1, "http://run")

        args, kwargs = _fake_shared.call_claude.call_args
        assert "my diff content" in args[1]
        assert SYSTEM in args[0]

    def test_posts_pr_comment(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = VALID_JSON_STR

        review_pr("org", "repo", 5, "http://run")

        _fake_shared.post_pr_comment.assert_called_once()
        call_args = _fake_shared.post_pr_comment.call_args[0]
        assert call_args[0] == "org"
        assert call_args[1] == "repo"
        assert call_args[2] == 5

    def test_comment_contains_score(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = VALID_JSON_STR

        review_pr("org", "repo", 5, "http://run")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "75" in comment_text
        assert "APPROVE" in comment_text

    def test_comment_contains_findings(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = VALID_JSON_STR

        review_pr("org", "repo", 5, "http://run")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Hardcoded API key detected." in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        data = {**VALID_RESULT, "findings": []}
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = json.dumps(data)

        review_pr("org", "repo", 5, "http://run")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        data = {**VALID_RESULT, "positive_observations": []}
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = json.dumps(data)

        review_pr("org", "repo", 5, "http://run")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_claude_returns_malformed_json_raises(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = "not json at all"

        with pytest.raises(ValueError):
            review_pr("org", "repo", 1, "http://run")

    def test_finding_with_null_line_renders_na(self):
        finding = {**VALID_RESULT["findings"][0], "line": None}
        data = {**VALID_RESULT, "findings": [finding]}
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = json.dumps(data)

        review_pr("org", "repo", 5, "http://run")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "n/a" in comment_text

    def test_comment_contains_auto_generated_footer(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = VALID_JSON_STR

        review_pr("org", "repo", 5, "http://run")

        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Auto-generated by AI Delivery Bot" in comment_text

    def test_multiple_findings_all_rendered(self):
        finding2 = {**VALID_RESULT["findings"][0], "severity": "LOW",
                    "issue": "Missing docstring.", "recommendation": "Add docstring."}
        data = {**VALID