"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json: happy path, markdown fences, embedded newlines, missing braces, invalid JSON
    - review_pr: happy path, Claude response parsing, comment posting
    - review_repo: happy path, content truncation, file filtering
    - get_output_url: URL construction
    - build_report_md: structure, content, edge cases (empty findings, missing keys)

Mocks used:
    - shared.call_claude (patched at tool1_code_review.call_claude)
    - shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
    - shared.get_repo_files (patched at tool1_code_review.get_repo_files)
    - shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
    - shared.write_output_file (patched at tool1_code_review.write_output_file)
    - shared.send_email (patched at tool1_code_review.send_email)
    - requests (not directly called by functions under test, but imported)

TODOs:
    - TODO: Integration test for __main__ block requires env-var wiring and subprocess execution
    - TODO: Test write_audit_entry interaction once audit path is confirmed in review_pr/review_repo
    - TODO: Test email sending path in review_pr/review_repo if that path is wired up
"""

import json
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Module import – we need to guard against missing env vars that shared.py
# might read at import time.
# ---------------------------------------------------------------------------
import sys
import os

# Provide dummy env vars before importing the module under test so that
# shared.py (which we are NOT testing) doesn't blow up.
os.environ.setdefault("GH_TOKEN", "dummy-token")
os.environ.setdefault("OUTPUT_REPO_OWNER", "test-owner")
os.environ.setdefault("OUTPUT_REPO", "test-output-repo")
os.environ.setdefault("ANTHROPIC_API_KEY", "dummy-key")
os.environ.setdefault("SENDGRID_API_KEY", "dummy-sendgrid")

# Stub out the entire `shared` module so we don't need the real file.
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer dummy-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

import importlib
import tool1_code_review as mod

# Re-export helpers for convenience
extract_json = mod.extract_json
review_pr = mod.review_pr
review_repo = mod.review_repo
get_output_url = mod.get_output_url
build_report_md = mod.build_report_md


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code is well-structured with minor issues.",
    "score": 78,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded API key detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Function is overly long.",
            "recommendation": "Split into smaller functions.",
        },
    ],
    "positive_observations": ["Good test coverage", "Clear variable names"],
    "iac_findings": ["S3 bucket missing encryption tag"],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for extract_json helper."""

    def test_plain_valid_json(self):
        result = extract_json(VALID_JSON_STR)
        assert result["score"] == 78
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        result = extract_json(f"   \n{VALID_JSON_STR}\n   ")
        assert result["summary"] == VALID_RESULT["summary"]

    def test_json_wrapped_in_markdown_fences(self):
        raw = f"```json\n{VALID_JSON_STR}\n```"
        result = extract_json(raw)
        assert result["score"] == 78

    def test_json_wrapped_in_plain_markdown_fences(self):
        raw = f"```\n{VALID_JSON_STR}\n```"
        result = extract_json(raw)
        assert result["score"] == 78

    def test_json_with_preamble_text(self):
        raw = f"Here is the review:\n\n{VALID_JSON_STR}"
        result = extract_json(raw)
        assert result["score"] == 78

    def test_json_with_postamble_text(self):
        raw = f"{VALID_JSON_STR}\n\nPlease let me know if you need anything else."
        result = extract_json(raw)
        assert result["score"] == 78

    def test_json_with_both_preamble_and_postamble(self):
        raw = f"Sure! Here you go:\n{VALID_JSON_STR}\nHope this helps."
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_literal_newline_inside_string_value(self):
        # Simulate a response where Claude put a newline inside a string value
        broken = VALID_JSON_STR.replace(
            "Overall the code is well-structured with minor issues.",
            "Overall the code is\nwell-structured with minor issues.",
        )
        # This tests the regex-cleanup path
        result = extract_json(broken)
        assert "well-structured" in result["summary"]

    def test_empty_findings_and_observations(self):
        minimal = {
            "summary": "Minimal.",
            "score": 50,
            "merge_recommendation": "REQUEST_CHANGES",
            "findings": [],
            "positive_observations": [],
            "iac_findings": [],
        }
        result = extract_json(json.dumps(minimal))
        assert result["findings"] == []
        assert result["positive_observations"] == []

    def test_raises_when_no_json_object_present(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This response contains no JSON at all.")

    def test_raises_on_completely_invalid_json(self):
        with pytest.raises(ValueError):
            extract_json("{ totally: broken json !!!")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_score_boundary_zero(self):
        data = dict(VALID_RESULT, score=0)
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = dict(VALID_RESULT, score=100)
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_merge_recommendation_block(self):
        data = dict(VALID_RESULT, merge_recommendation="BLOCK")
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "BLOCK"

    def test_finding_with_null_line(self):
        data = dict(VALID_RESULT)
        data["findings"] = [
            {
                "severity": "MEDIUM",
                "category": "performance",
                "file": "app.py",
                "line": None,
                "issue": "N+1 query detected.",
                "recommendation": "Use select_related.",
            }
        ]
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_json_inside_extra_text_and_fences(self):
        raw = "```json\nSome preamble\n" + VALID_JSON_STR + "\nSome postamble\n```"
        result = extract_json(raw)
        assert result["score"] == 78

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_all_valid_severities(self, severity):
        data = dict(VALID_RESULT)
        data["findings"] = [dict(VALID_RESULT["findings"][0], severity=severity)]
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity

    @pytest.mark.parametrize("category", [
        "security", "performance", "maintainability", "correctness", "iac"
    ])
    def test_all_valid_categories(self, category):
        data = dict(VALID_RESULT)
        data["findings"] = [dict(VALID_RESULT["findings"][0], category=category)]
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["category"] == category


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:
    """Tests for review_pr function."""

    def _patch_all(self, diff="diff content", raw_response=None):
        if raw_response is None:
            raw_response = VALID_JSON_STR
        patches = {
            "get_pr_diff": patch.object(mod, "get_pr_diff", return_value=diff),
            "call_claude": patch.object(mod, "call_claude", return_value=raw_response),
            "post_pr_comment": patch.object(mod, "post_pr_comment", return_value=None),
        }
        return patches

    def test_happy_path_returns_parsed_result(self):
        with patch.object(mod, "get_pr_diff", return_value="diff content"), \
             patch.object(mod, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(mod, "post_pr_comment", return_value=None) as mock_comment:

            result = review_pr("myorg", "myrepo", 42, "https://run.url")

            assert result["score"] == 78
            assert result["merge_recommendation"] == "APPROVE"
            mock_comment.assert_called_once()

    def test_calls_get_pr_diff_with_correct_args(self):
        with patch.object(mod, "get_pr_diff", return_value="d") as mock_diff, \
             patch.object(mod, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(mod, "post_pr_comment", return_value=None):

            review_pr("owner1", "repo1", 99, "url")
            mock_diff.assert_called_once_with("owner1", "repo1", 99)

    def test_calls_call_claude_with_system_and_diff(self):
        with patch.object(mod, "get_pr_diff", return_value="MY DIFF"), \
             patch.object(mod, "call_claude", return_value=VALID_JSON_STR) as mock_claude, \
             patch.object(mod, "post_pr_comment", return_value=None):

            review_pr("o", "r", 1, "url")
            args, kwargs = mock_claude.call_args
            assert args[0] == mod.SYSTEM
            assert "MY DIFF" in args[1]

    def test_posts_comment_containing_score_and_recommendation(self):
        with patch.object(mod, "get_pr_diff", return_value="diff"), \
             patch.object(mod, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(mod, "post_pr_comment", return_value=None) as mock_comment:

            review_pr("o", "r", 5, "url")
            comment_text = mock_comment.call_args[0][3]
            assert "78" in comment_text
            assert "APPROVE" in comment_text

    def test_posts_comment_with_findings(self):
        with patch.object(mod, "get_pr_diff", return_value="diff"), \
             patch.object(mod, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(mod, "post_pr_comment", return_value=None) as mock_comment:

            review_pr("o", "r", 5, "url")
            comment_text = mock_comment.call_args[0][3]
            assert "HIGH" in comment_text
            assert "Hardcoded API key detected" in comment_text

    def test_posts_comment_with_no_findings_placeholder(self):
        empty_result = dict(VALID_RESULT, findings=[], positive_observations=[])
        with patch.object(mod, "get_pr_diff", return_value="diff"), \
             patch.object(mod, "call_claude", return_value=json.dumps(empty_result)), \
             patch.object(mod, "post_pr_comment", return_value=None) as mock_comment:

            review_pr("o", "r", 5, "url")
            comment_text = mock_comment.call_args[0][3]
            assert "_No findings_" in comment_text

    def test_posts_comment_with_no_positive_obs_placeholder(self):
        empty_result = dict(VALID_RESULT, findings=[], positive_observations=[])
        with patch.object(mod, "get_pr_diff", return_value="diff"), \
             patch.object(mod, "call_claude", return_value=json.dumps(empty_result)), \
             patch.object(mod, "post_pr_comment", return_value=None) as mock_comment:

            review_pr("o", "r", 5, "url")
            comment_text = mock_comment.call_args[0][3]
            assert "_None_" in comment_text

    def test_finding_with_null_line_renders_as_na(self):
        data = dict(VALID_RESULT)
        data["findings"] = [
            {
                "severity": "MEDIUM",
                "category": "maintainability",
                "file": "app.py",
                "line": None,
                "issue": "Complex function.",
                "recommendation": "Refactor.",
            }
        ]
        with patch.object(mod, "get_pr_diff", return_value="diff"), \
             patch.object(mod, "call_claude", return_value=json.dumps(data)), \
             patch.object(mod, "post_pr_comment", return_value=None) as mock_comment:

            review_pr("o", "r", 5, "url")
            comment_text = mock_comment.call_args[0][3]
            assert "n/a" in comment_text

    def test_propagates_extract_json_error(self):
        with patch.object(mod, "get_pr_diff", return_value="diff"), \
             patch.object(mod, "call_claude", return_value="not json at all!"), \
             patch.object(mod, "post_pr_comment", return_value=None):

            with pytest.raises(ValueError):
                review_pr("o", "r", 5, "url")

    def test_comment_posted_to_correct_pr(self):
        with patch.object(mod, "get_pr_diff", return_value="diff"), \
             patch.object(mod, "call_claude",