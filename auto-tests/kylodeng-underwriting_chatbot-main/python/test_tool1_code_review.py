"""
Test suite for tool1_code_review.py

What is tested:
  - extract_json(): happy path (plain JSON), markdown-fenced JSON, JSON embedded in text,
    newlines inside string values, missing JSON, invalid JSON, edge cases.
  - review_pr(): successful flow, Claude returns valid JSON, comment posted.
  - review_repo(): successful flow, file truncation logic, token budget guard.
  - get_output_url(): URL construction with various owner/repo/label combos.
  - build_report_md(): report structure, empty findings, empty IaC/positive observations,
    score and recommendation rendering.

Mocks used:
  - shared.call_claude          (patched at tool1_code_review module level)
  - shared.get_pr_diff          (patched at tool1_code_review module level)
  - shared.get_repo_files       (patched at tool1_code_review module level)
  - shared.post_pr_comment      (patched at tool1_code_review module level)
  - shared.write_output_file    (patched at tool1_code_review module level)
  - shared.send_email           (patched at tool1_code_review module level)
  - shared.write_audit_entry    (patched at tool1_code_review module level)
  - requests                    (not directly called by public functions under test)

TODOs:
  - TODO: Integration test for __main__ block requires full env-var setup and live GH token.
  - TODO: Test write_output_file / send_email orchestration once the full __main__ block
          is visible in source (source was truncated).
  - TODO: Parameterise severity icon mapping once sev_icons is exposed as a module constant.
"""

import json
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Import the module under test.
# shared is imported inside tool1_code_review so we patch at the right level.
# ---------------------------------------------------------------------------
import importlib, sys, types

# Provide a minimal fake 'shared' module so the import succeeds without the
# real dependency tree.
def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock()
    shared.get_repo_files     = MagicMock()
    shared.get_pr_diff        = MagicMock()
    shared.write_output_file  = MagicMock()
    shared.post_pr_comment    = MagicMock()
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html/>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-org"
    shared.OUTPUT_REPO        = "output-repo"
    shared.GH_HEADERS         = {"Authorization": "Bearer fake"}
    shared.GH_API             = "https://api.github.com"
    return shared

_fake_shared = _make_fake_shared()
sys.modules.setdefault("shared", _fake_shared)
sys.modules.setdefault("requests", MagicMock())

import tool1_code_review as mod   # noqa: E402  (must come after sys.modules patch)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean structure"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils/helper.js",
            "line": None,
            "issue": "Function is too long.",
            "recommendation": "Split into smaller functions.",
        },
    ],
    "positive_observations": ["Good test coverage", "Consistent naming"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role is overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared mock call counts between tests."""
    _fake_shared.call_claude.reset_mock()
    _fake_shared.get_repo_files.reset_mock()
    _fake_shared.get_pr_diff.reset_mock()
    _fake_shared.write_output_file.reset_mock()
    _fake_shared.post_pr_comment.reset_mock()
    _fake_shared.send_email.reset_mock()
    _fake_shared.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    # --- happy path ---

    def test_plain_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = mod.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = mod.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fenced_json_backtick3(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = mod.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fenced_json_with_language_tag(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = mod.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n\n" + json.dumps(MINIMAL_RESULT) + "\n\nEnd."
        result = mod.extract_json(raw)
        assert result["score"] == 85

    def test_full_result_parsed(self):
        raw = json.dumps(FULL_RESULT)
        result = mod.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate Claude inserting a literal \n inside a string value
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # Direct json.loads will fail; extract_json should clean and succeed
        result = mod.extract_json(raw)
        assert result["score"] == 70

    def test_json_with_extra_text_before_brace(self):
        raw = "Sure! Here is the JSON: " + json.dumps(MINIMAL_RESULT)
        result = mod.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    # --- edge cases ---

    def test_empty_findings_list(self):
        data = dict(MINIMAL_RESULT, findings=[])
        result = mod.extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_score_zero(self):
        data = dict(MINIMAL_RESULT, score=0)
        result = mod.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_100(self):
        data = dict(MINIMAL_RESULT, score=100)
        result = mod.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_merge_recommendation_block(self):
        data = dict(MINIMAL_RESULT, merge_recommendation="BLOCK")
        result = mod.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "BLOCK"

    # --- error / negative cases ---

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            mod.extract_json("This is plain text with no JSON.")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            mod.extract_json('{"key": "value"')   # missing closing brace

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            mod.extract_json("")

    def test_only_brackets_no_valid_json(self):
        with pytest.raises(ValueError):
            mod.extract_json("{{{not valid}}}")

    def test_json_array_at_top_level_not_found(self):
        # A bare array is not the expected dict; no { } found — should raise
        with pytest.raises(ValueError):
            mod.extract_json("[1, 2, 3]")

    def test_markdown_fence_with_invalid_inner_json(self):
        raw = "```\n{bad json here\n```"
        with pytest.raises(ValueError):
            mod.extract_json(raw)

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            mod.extract_json("   \n\t  ")


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_claude(self, result=None):
        payload = result or MINIMAL_RESULT
        _fake_shared.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+print('hello')"
        _fake_shared.call_claude.return_value = json.dumps(payload)

    def test_happy_path_returns_result(self):
        self._setup_claude(MINIMAL_RESULT)
        result = mod.review_pr("owner", "repo", 1, "https://run.url")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_get_pr_diff_called_with_correct_args(self):
        self._setup_claude()
        mod.review_pr("my-org", "my-repo", 99, "https://run.url")
        _fake_shared.get_pr_diff.assert_called_once_with("my-org", "my-repo", 99)

    def test_call_claude_called_with_diff(self):
        self._setup_claude()
        mod.review_pr("owner", "repo", 1, "https://run.url")
        args, kwargs = _fake_shared.call_claude.call_args
        assert "Review this pull request diff:" in args[1]

    def test_post_pr_comment_called_once(self):
        self._setup_claude()
        mod.review_pr("owner", "repo", 1, "https://run.url")
        _fake_shared.post_pr_comment.assert_called_once()

    def test_post_pr_comment_contains_score(self):
        self._setup_claude()
        mod.review_pr("owner", "repo", 1, "https://run.url")
        _, kwargs = _fake_shared.post_pr_comment.call_args
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "85" in comment_text

    def test_post_pr_comment_contains_recommendation(self):
        self._setup_claude(MINIMAL_RESULT)
        mod.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_post_pr_comment_contains_summary(self):
        self._setup_claude(MINIMAL_RESULT)
        mod.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Looks good overall." in comment_text

    def test_findings_rendered_in_comment(self):
        self._setup_claude(FULL_RESULT)
        mod.review_pr("owner", "repo", 2, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "src/example.py" in comment_text
        assert "HIGH" in comment_text

    def test_no_findings_shows_no_findings_placeholder(self):
        self._setup_claude(MINIMAL_RESULT)
        mod.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_positive_observations_rendered(self):
        self._setup_claude(MINIMAL_RESULT)
        mod.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Clean structure" in comment_text

    def test_no_positive_observations_shows_placeholder(self):
        payload = dict(MINIMAL_RESULT, positive_observations=[])
        self._setup_claude(payload)
        mod.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_result_contains_findings_key(self):
        self._setup_claude(FULL_RESULT)
        result = mod.review_pr("owner", "repo", 3, "https://run.url")
        assert "findings" in result
        assert len(result["findings"]) == 2

    def test_missing_score_key_uses_question_mark(self):
        payload = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        self._setup_claude(payload)
        mod.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "?/100" in comment_text

    def test_line_none_shown_as_na(self):
        payload = dict(FULL_RESULT)  # FULL_RESULT has one finding with line=None
        self._setup_claude(payload)
        mod.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "n/a" in comment_text


# ===========================================================================
# review_repo
# ===========================================================================

class TestReviewRepo:

    def _setup(self, files=None, result=None):
        payload = result or MINIMAL_RESULT
        _fake_shared.get_repo_files.return_value = files or {
            "src/main.py": "print('hello')",
            "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        _fake_shared.call_claude.return_value = json.dumps(payload)

    def test_happy_path_returns_result(self):
        self._setup()
        result = mod.review_repo("owner", "repo", "https://run.url")
        assert result["score"] == 85

    def test_get_repo_files_called_with_extensions(self):
        self._setup()
        mod.review_repo("owner", "repo