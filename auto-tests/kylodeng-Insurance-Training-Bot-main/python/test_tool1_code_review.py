"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, invalid JSON, whitespace-only input
- review_pr(): happy path, Claude response handling, comment posting, return value
- review_repo(): happy path, file truncation, token budget logic
- get_output_url(): URL construction
- build_report_md(): happy path, missing keys, empty findings, empty iac/positive lists

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- requests (not directly called by reviewed functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires environment variable setup + subprocess
- TODO: Test for write_output_file / send_email orchestration in __main__ block
  (source code is truncated before that logic is visible)
"""

import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Make sure the script's directory is importable even when tests run from repo
# root.  We replicate the sys.path.insert the script itself does.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

# We need a minimal stub for `shared` so the import of tool1_code_review
# doesn't blow up when the real shared module or its deps are unavailable.
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
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

import tool1_code_review as t1  # noqa: E402  (import after stub registration)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Code looks fine overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
    "score": 55,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/main.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Missing docstring.",
            "recommendation": "Add a module-level docstring.",
        },
    ],
    "positive_observations": ["Follows PEP-8", "Good naming conventions"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role too permissive"],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared stubs before every test."""
    for attr in (
        "call_claude",
        "get_repo_files",
        "get_pr_diff",
        "write_output_file",
        "post_pr_comment",
        "send_email",
        "write_audit_entry",
    ):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for extract_json()"""

    def test_happy_path_plain_json(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_strips_markdown_triple_backticks(self):
        raw = "```json\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["summary"] == "Code looks fine overall."

    def test_strips_plain_backtick_fence(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_leading_trailing_whitespace(self):
        raw = "   \n   " + json.dumps(MINIMAL_VALID_RESULT) + "   \n   "
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_text_before_json_object(self):
        raw = "Here is my analysis:\n" + json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 55

    def test_text_after_json_object(self):
        raw = json.dumps(FULL_RESULT) + "\n\nPlease let me know if you need more details."
        result = t1.extract_json(raw)
        assert len(result["findings"]) == 2

    def test_text_before_and_after_json(self):
        raw = "Preamble text.\n" + json.dumps(MINIMAL_VALID_RESULT) + "\nSuffix text."
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a raw response with a literal newline inside a JSON string value
        broken = '{"summary": "Line one\nLine two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # direct json.loads will fail; extract_json should clean and succeed
        result = t1.extract_json(broken)
        assert result["score"] == 70

    def test_no_json_raises_value_error(self):
        raw = "This response contains no JSON at all."
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("   \n\t  ")

    def test_invalid_json_inside_braces_raises_value_error(self):
        raw = '{ this is: not valid json }'
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["findings"][1]["line"] is None
        assert result["iac_findings"][0] == "S3 bucket lacks versioning"

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert isinstance(result, dict)

    def test_score_boundary_zero(self):
        data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_multiple_top_level_braces_picks_outermost(self):
        # Simulate text that contains a fake partial JSON before the real one
        raw = '{"fake": true} and then {"summary": "ok", "score": 90, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # The function uses rfind("}") so it should capture the full span
        result = t1.extract_json(raw)
        # It may pick either, but should not raise
        assert isinstance(result, dict)

    def test_critical_severity_in_findings(self):
        data = {
            **MINIMAL_VALID_RESULT,
            "findings": [
                {
                    "severity": "CRITICAL",
                    "category": "security",
                    "file": "deploy.tf",
                    "line": 1,
                    "issue": "Root account used.",
                    "recommendation": "Use least-privilege IAM roles.",
                }
            ],
        }
        result = t1.extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_block_merge_recommendation(self):
        data = {**MINIMAL_VALID_RESULT, "merge_recommendation": "BLOCK"}
        result = t1.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "BLOCK"


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:
    """Tests for review_pr()"""

    def _setup_mocks(self, result_dict=None):
        result_dict = result_dict or FULL_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/main.py ..."
        _shared_stub.call_claude.return_value = json.dumps(result_dict)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result_dict(self):
        self._setup_mocks()
        result = t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        assert result["score"] == 55
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("myorg", "myrepo", 42)

    def test_calls_call_claude_with_diff_in_prompt(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "Review this pull request diff:" in args[1]
        assert "diff --git" in args[1]

    def test_posts_pr_comment(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args[0]
        assert call_args[0] == "myorg"
        assert call_args[1] == "myrepo"
        assert call_args[2] == 42

    def test_comment_contains_score(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "55" in comment_text
        assert "100" in comment_text  # Score is shown as /100

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_contains_summary(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Several issues found." in comment_text

    def test_comment_contains_findings(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment_text
        assert "src/main.py" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(result_dict=MINIMAL_VALID_RESULT)
        t1.review_pr("myorg", "myrepo", 1, "https://example.com/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_contains_positive_observations(self):
        self._setup_mocks()
        t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Follows PEP-8" in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        data = {**MINIMAL_VALID_RESULT, "positive_observations": []}
        self._setup_mocks(result_dict=data)
        t1.review_pr("myorg", "myrepo", 1, "https://example.com/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_missing_keys_in_result_uses_defaults(self):
        """extract_json returns a dict with no optional keys; review_pr should not crash."""
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps({"summary": "ok"})
        _shared_stub.post_pr_comment.return_value = None
        result = t1.review_pr("myorg", "myrepo",