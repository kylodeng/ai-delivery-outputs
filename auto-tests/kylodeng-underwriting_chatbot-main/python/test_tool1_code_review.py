"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path (clean JSON), markdown fences, outermost-brace extraction,
  newline-in-string cleanup, missing JSON, malformed JSON, edge cases
- review_pr: happy path, Claude/post_pr_comment interactions
- review_repo: happy path, file content truncation behaviour
- get_output_url: URL construction
- build_report_md: full report generation, missing keys, empty findings/iac/pos

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- requests (not called directly in the tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block (requires full env-var setup)
- TODO: Test post_pr_comment failure path inside review_pr once error handling is added
- TODO: Test write_output_file / send_email calls in the CLI entrypoint
"""

import json
import re
import sys
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without installing the package
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# We need to stub out `shared` before importing the module under test because
# tool1_code_review does `from shared import ...` at module level.
SHARED_STUB = MagicMock()
SHARED_STUB.OUTPUT_REPO_OWNER = "test-owner"
SHARED_STUB.OUTPUT_REPO = "test-output-repo"
SHARED_STUB.GH_HEADERS = {"Authorization": "Bearer token"}
SHARED_STUB.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", SHARED_STUB)
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test
import importlib
import tool1_code_review as cr  # noqa: E402  (after path manipulation)

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks reasonable overall.",
    "score": 75,
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
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or AWS Secrets Manager.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Function is too long.",
            "recommendation": "Break into smaller functions.",
        },
    ],
    "positive_observations": ["Good docstrings", "Type hints used throughout"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role is overly permissive"],
}


# ===========================================================================
# extract_json tests
# ===========================================================================


class TestExtractJson:
    """Tests for extract_json()"""

    def test_clean_json_string(self):
        """Happy path: valid JSON string returned directly."""
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n  "
        result = cr.extract_json(raw)
        assert result["summary"] == "Code looks reasonable overall."

    def test_json_wrapped_in_markdown_fences(self):
        """Strip ```...``` fences before parsing."""
        raw = "```json\n" + json.dumps(FULL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2

    def test_json_wrapped_in_plain_fences(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_json_with_preamble_text(self):
        """JSON buried after prose text — outermost { } extraction."""
        preamble = "Sure! Here is the JSON:\n\n"
        raw = preamble + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_trailing_text(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nLet me know if you need anything else!"
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_json_embedded_in_prose(self):
        """Both preamble and trailing text."""
        raw = "Here is the review: " + json.dumps(FULL_RESULT) + " Hope that helps."
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_newline_inside_string_value_cleaned(self):
        """Literal \\n inside a string value is cleaned before re-parse."""
        # Manually craft invalid JSON with a newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 80, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This will fail the first json.loads attempt; the regex cleaner should fix it
        result = cr.extract_json(raw)
        assert "line one" in result["summary"]

    def test_no_json_object_raises_value_error(self):
        raw = "This response contains no JSON at all."
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("   \n\t  ")

    def test_malformed_json_raises_value_error(self):
        """Braces present but contents are not valid JSON."""
        raw = '{"key": value_without_quotes, broken}'
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_nested_json_parses_correctly(self):
        """Deeply nested structure should parse without issue."""
        data = {
            "summary": "ok",
            "score": 90,
            "merge_recommendation": "APPROVE",
            "findings": [
                {"severity": "LOW", "category": "maintainability",
                 "file": "a.py", "line": 1,
                 "issue": "minor", "recommendation": "fix it"}
            ],
            "positive_observations": [],
            "iac_findings": [],
        }
        raw = json.dumps(data)
        result = cr.extract_json(raw)
        assert result["findings"][0]["severity"] == "LOW"

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_finding_with_null_line(self):
        data = {
            **MINIMAL_RESULT,
            "findings": [
                {"severity": "MEDIUM", "category": "correctness",
                 "file": "b.py", "line": None,
                 "issue": "issue here", "recommendation": "fix here"}
            ],
        }
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_all_severity_levels_parsed(self):
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            data = {
                **MINIMAL_RESULT,
                "findings": [
                    {"severity": sev, "category": "security",
                     "file": "x.py", "line": 1,
                     "issue": "i", "recommendation": "r"}
                ],
            }
            result = cr.extract_json(json.dumps(data))
            assert result["findings"][0]["severity"] == sev

    def test_all_merge_recommendations_parsed(self):
        for rec in ("APPROVE", "REQUEST_CHANGES", "BLOCK"):
            data = {**MINIMAL_RESULT, "merge_recommendation": rec}
            result = cr.extract_json(json.dumps(data))
            assert result["merge_recommendation"] == rec

    def test_extra_keys_preserved(self):
        """Additional keys from Claude should not cause failures."""
        data = {**MINIMAL_RESULT, "extra_field": "bonus info"}
        result = cr.extract_json(json.dumps(data))
        assert result["extra_field"] == "bonus info"

    def test_multiple_json_objects_uses_outermost(self):
        """When multiple JSON-like blobs exist, the outermost { } wins."""
        inner = '{"inner": true}'
        raw = '{"outer": true, "nested": ' + inner + '}'
        result = cr.extract_json(raw)
        assert result["outer"] is True

    @pytest.mark.parametrize("fence_style", [
        "```json\n{}\n```",
        "```\n{}\n```",
        "``` json\n{}\n```",
    ])
    def test_various_fence_styles(self, fence_style):
        """Empty JSON object inside various fence styles."""
        result = cr.extract_json(fence_style)
        assert result == {}


# ===========================================================================
# review_pr tests
# ===========================================================================


class TestReviewPr:
    """Tests for review_pr()"""

    def _make_mocks(self, claude_return=None):
        if claude_return is None:
            claude_return = json.dumps(FULL_RESULT)
        mock_diff = MagicMock(return_value="diff --git a/src/example.py ...")
        mock_claude = MagicMock(return_value=claude_return)
        mock_post = MagicMock()
        return mock_diff, mock_claude, mock_post

    def test_happy_path_returns_parsed_result(self):
        mock_diff, mock_claude, mock_post = self._make_mocks()
        with patch.object(cr, "get_pr_diff", mock_diff), \
             patch.object(cr, "call_claude", mock_claude), \
             patch.object(cr, "post_pr_comment", mock_post):
            result = cr.review_pr("acme", "myrepo", 7, "https://ci/run/1")

        assert result["score"] == 42
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_calls_get_pr_diff_with_correct_args(self):
        mock_diff, mock_claude, mock_post = self._make_mocks()
        with patch.object(cr, "get_pr_diff", mock_diff), \
             patch.object(cr, "call_claude", mock_claude), \
             patch.object(cr, "post_pr_comment", mock_post):
            cr.review_pr("owner1", "repo1", 99, "https://ci/run/2")

        mock_diff.assert_called_once_with("owner1", "repo1", 99)

    def test_calls_call_claude_with_system_prompt(self):
        mock_diff, mock_claude, mock_post = self._make_mocks()
        with patch.object(cr, "get_pr_diff", mock_diff), \
             patch.object(cr, "call_claude", mock_claude), \
             patch.object(cr, "post_pr_comment", mock_post):
            cr.review_pr("o", "r", 1, "url")

        args, kwargs = mock_claude.call_args
        assert args[0] == cr.SYSTEM
        assert "Review this pull request diff" in args[1]

    def test_posts_pr_comment(self):
        mock_diff, mock_claude, mock_post = self._make_mocks()
        with patch.object(cr, "get_pr_diff", mock_diff), \
             patch.object(cr, "call_claude", mock_claude), \
             patch.object(cr, "post_pr_comment", mock_post):
            cr.review_pr("o", "r", 5, "url")

        mock_post.assert_called_once()
        _, _, _, comment = mock_post.call_args[0]
        assert "Claude Code Review" in comment

    def test_comment_contains_score(self):
        mock_diff, mock_claude, mock_post = self._make_mocks()
        with patch.object(cr, "get_pr_diff", mock_diff), \
             patch.object(cr, "call_claude", mock_claude), \
             patch.object(cr, "post_pr_comment", mock_post):
            cr.review_pr("o", "r", 5, "url")

        comment = mock_post.call_args[0][3]
        assert "42/100" in comment

    def test_comment_contains_recommendation(self):
        mock_diff, mock_claude, mock_post = self._make_mocks()
        with patch.object(cr, "get_pr_diff", mock_diff), \
             patch.object(cr, "call_claude", mock_claude), \
             patch.object(cr, "post_pr_comment", mock_post):
            cr.review_pr("o", "r", 5, "url")

        comment = mock_post.call_args[0][3]
        assert "REQUEST_CHANGES" in comment

    def test_comment_shows_no_findings_when_empty(self):
        empty_result = {**FULL_RESULT, "findings": []}
        mock_diff, mock_claude, mock_post = self._make_mocks(
            claude_return=json.dumps(empty_result)
        )
        with patch.object(cr, "get_pr_diff", mock_diff), \
             patch.object(cr, "call_claude", mock_claude), \
             patch.object(cr, "post_pr_comment", mock_post):
            cr.review_pr("o", "r", 5, "url")

        comment = mock_post.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_shows_positive_observations(self):
        mock_diff, mock_claude, mock_post = self._make_mocks()
        with patch.