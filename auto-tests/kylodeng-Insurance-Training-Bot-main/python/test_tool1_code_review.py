"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, direct parse fallback
- review_pr(): happy path, comment formatting, result propagation
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): happy path, missing fields, empty findings/iac/positive_observations,
  multiple findings formatting

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- requests (not directly exercised in public functions but imported)

TODOs:
- TODO: test __main__ block (requires environment variable manipulation + subprocess)
- TODO: test integration with real Claude API responses
- TODO: test write_output_file / send_email / write_audit_entry interactions once
        the __main__ block is fully visible in source
"""

import json
import re
import datetime
import sys
import os
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal stub for `shared` so the import of
# tool1_code_review does not fail when shared.py is not on the path.
# ---------------------------------------------------------------------------
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

import importlib
import tool1_code_review as cr  # noqa: E402  (import after stub registration)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
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
            "issue": "Missing docstring.",
            "recommendation": "Add a module-level docstring.",
        },
    ],
    "positive_observations": ["Clean code structure", "Good naming conventions"],
    "iac_findings": ["S3 bucket missing server-side encryption"],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared stubs before each test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_direct_valid_json(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_VALID_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_json_wrapped_in_backtick_fences(self):
        raw = "```json\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_wrapped_in_plain_backtick_fences(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_with_preamble_text(self):
        raw = "Here is the review:\n\n" + json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_trailing_text(self):
        raw = json.dumps(MINIMAL_VALID_RESULT) + "\n\nPlease let me know if you need anything else."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_with_newline_inside_string_value(self):
        # Simulate Claude injecting a literal newline inside a string
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # The regex cleaner should handle this
        result = cr.extract_json(raw)
        assert result["score"] == 50

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_raises_value_error_on_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no braces.")

    def test_raises_value_error_on_malformed_json(self):
        malformed = '{"score": 50, "summary": "broken,}'
        with pytest.raises(ValueError):
            cr.extract_json(malformed)

    def test_raises_value_error_on_empty_string(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_raises_value_error_on_whitespace_only(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_json_with_extra_braces_in_text(self):
        # Text around JSON that also contains braces
        inner = json.dumps(MINIMAL_VALID_RESULT)
        raw = f"Some text {{not json}} then {inner} then more {{stuff}}"
        result = cr.extract_json(raw)
        # Should find outermost { ... }
        assert isinstance(result, dict)

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n" + json.dumps({"score": 99, "summary": "ok",
                                     "merge_recommendation": "APPROVE",
                                     "findings": [], "positive_observations": [],
                                     "iac_findings": []}) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 99

    def test_score_boundary_zero(self):
        data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {**FULL_RESULT}
        raw = json.dumps(data)
        result = cr.extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_VALID_RESULT, "findings": []}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"] == []

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**MINIMAL_VALID_RESULT, "merge_recommendation": recommendation}
        result = cr.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severities_in_findings(self, severity):
        finding = {
            "severity": severity,
            "category": "security",
            "file": "main.py",
            "line": 1,
            "issue": "An issue.",
            "recommendation": "Fix it.",
        }
        data = {**MINIMAL_VALID_RESULT, "findings": [finding]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result_dict=None):
        if result_dict is None:
            result_dict = MINIMAL_VALID_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/file.py ..."
        _shared_stub.call_claude.return_value = json.dumps(result_dict)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = cr.review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 7, "https://run")
        _shared_stub.get_pr_diff.assert_called_once_with("myorg", "myrepo", 7)

    def test_calls_call_claude_with_system_prompt(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 7, "https://run")
        args, kwargs = _shared_stub.call_claude.call_args
        assert args[0] == cr.SYSTEM
        assert "Review this pull request diff" in args[1]

    def test_calls_post_pr_comment(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 42, "https://run")
        _shared_stub.post_pr_comment.assert_called_once()
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "myorg"
        assert args[1] == "myrepo"
        assert args[2] == 42

    def test_comment_contains_score(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "85" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._setup_mocks()
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Looks good overall." in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(MINIMAL_VALID_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_with_findings(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment
        assert "HIGH" in comment
        assert "Hardcoded password detected." in comment

    def test_comment_with_findings_null_line(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        # line None should render as "n/a"
        assert "n/a" in comment

    def test_comment_contains_positive_observations(self):
        self._setup_mocks(FULL_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Clean code structure" in comment

    def test_comment_no_positive_observations_shows_placeholder(self):
        data = {**MINIMAL_VALID_RESULT, "positive_observations": []}
        self._setup_mocks(data)
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_missing_score_key_renders_question_mark(self):
        data = {k: v for k, v in MINIMAL_VALID_RESULT.items() if k != "score"}
        self._setup_mocks(data)
        cr.review_pr("myorg", "myrepo", 1, "https://run")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "?/100" in comment

    def test_comment_contains_auto_generated_footer(self):
        self._