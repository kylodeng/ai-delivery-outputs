"""
Test module for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  no-JSON response, malformed JSON, empty string, whitespace-only input
- review_pr: happy path, comment formatting, result propagation
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction with various inputs
- build_report_md: full report generation, empty findings, missing keys,
  IaC findings, positive observations

Mocks used:
- shared.call_claude (patched via tool1_code_review module)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- requests (not directly called in the tested functions but imported)

TODOs:
- TODO: Integration test for __main__ block requires full environment variable setup
- TODO: Test for write_output_file / send_email integration inside a full pipeline run
- TODO: Test behaviour when Claude returns tokens beyond max_tokens limit
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the script's directory is importable before importing the module
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPT_DIR))

# We patch the heavy shared imports before importing the module under test
import importlib

# Minimal stubs so the import doesn't fail when 'shared' isn't on the path
SHARED_ATTRS = [
    "call_claude", "get_repo_files", "get_pr_diff",
    "write_output_file", "post_pr_comment",
    "send_email", "email_html", "write_audit_entry",
    "OUTPUT_REPO_OWNER", "OUTPUT_REPO", "GH_HEADERS", "GH_API",
]

shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)
sys.modules.setdefault("requests", MagicMock())

import tool1_code_review as mod  # noqa: E402  (import after path setup)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks good overall.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 45,
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
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Bare except clause found.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["Clear variable names", "Well-structured modules"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset shared stub call history between tests."""
    shared_stub.reset_mock()
    shared_stub.OUTPUT_REPO_OWNER = "test-owner"
    shared_stub.OUTPUT_REPO = "test-output-repo"
    shared_stub.GH_HEADERS = {"Authorization": "Bearer test"}
    shared_stub.GH_API = "https://api.github.com"
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_happy_path_plain_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = mod.extract_json(raw)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_strips_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = mod.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_strips_markdown_fences_backtick_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = mod.extract_json(raw)
        assert result["score"] == 82

    def test_strips_markdown_fences_plain_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = mod.extract_json(raw)
        assert result["score"] == 82

    def test_extracts_json_with_surrounding_text(self):
        raw = "Here is my review:\n" + json.dumps(FULL_RESULT) + "\nEnd of review."
        result = mod.extract_json(raw)
        assert result["score"] == 45

    def test_handles_newline_inside_string_value(self):
        # Simulate a newline accidentally inside a JSON string value
        broken = '{"summary": "Line one\nLine two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # After regex cleanup this should parse
        result = mod.extract_json(broken)
        assert result["score"] == 50

    def test_raises_value_error_on_empty_string(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            mod.extract_json("")

    def test_raises_value_error_on_whitespace_only(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            mod.extract_json("   \n\t  ")

    def test_raises_value_error_on_plain_text_no_braces(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            mod.extract_json("This is not JSON at all.")

    def test_raises_value_error_on_malformed_json(self):
        with pytest.raises(ValueError):
            mod.extract_json('{"key": "value" missing_comma "key2": 1}')

    def test_handles_nested_objects_in_findings(self):
        raw = json.dumps(FULL_RESULT)
        result = mod.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_handles_extra_text_before_and_after_braces(self):
        inner = json.dumps({"score": 99, "summary": "ok", "merge_recommendation": "APPROVE",
                            "findings": [], "positive_observations": [], "iac_findings": []})
        raw = f"Some preamble text\n{inner}\nSome postamble text"
        result = mod.extract_json(raw)
        assert result["score"] == 99

    def test_minimal_valid_json_single_key(self):
        raw = '{"score": 0}'
        result = mod.extract_json(raw)
        assert result["score"] == 0

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = mod.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = mod.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {**FULL_RESULT}
        raw = json.dumps(data)
        result = mod.extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_unicode_content(self):
        data = {**MINIMAL_RESULT, "summary": "إلغاء تأكيد متابعة"}
        raw = json.dumps(data, ensure_ascii=False)
        result = mod.extract_json(raw)
        assert "إلغاء" in result["summary"]

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n{\"score\": 77}\n```"
        result = mod.extract_json(raw)
        assert result["score"] == 77

    def test_raises_value_error_unclosed_brace(self):
        with pytest.raises((ValueError, Exception)):
            mod.extract_json('{"score": 50, "summary": "ok"')

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = mod.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severities_in_findings(self, severity):
        finding = {
            "severity": severity,
            "category": "security",
            "file": "main.py",
            "line": 1,
            "issue": "Some issue.",
            "recommendation": "Fix it.",
        }
        data = {**MINIMAL_RESULT, "findings": [finding]}
        result = mod.extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result=None):
        if result is None:
            result = MINIMAL_RESULT
        shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py ..."
        shared_stub.call_claude.return_value = json.dumps(result)
        shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        assert result["score"] == MINIMAL_RESULT["score"]

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        shared_stub.get_pr_diff.assert_called_once_with("octocat", "hello-world", 42)

    def test_calls_call_claude_with_diff(self):
        self._setup_mocks()
        mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        args, kwargs = shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[1]
        assert "diff --git" in args[1]

    def test_calls_post_pr_comment(self):
        self._setup_mocks()
        mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        shared_stub.post_pr_comment.assert_called_once()
        call_args = shared_stub.post_pr_comment.call_args[0]
        assert call_args[0] == "octocat"
        assert call_args[1] == "hello-world"
        assert call_args[2] == 42

    def test_comment_contains_score(self):
        self._setup_mocks()
        mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "82" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._setup_mocks()
        mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "Code looks good overall." in comment

    def test_comment_shows_no_findings_placeholder(self):
        self._setup_mocks(result=MINIMAL_RESULT)
        mod.review_pr("octocat", "hello-world", 42, "https://run.url/1")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_shows_findings_when_present(self):
        self._setup_mocks(result=FULL_RESULT)
        mod.review_pr("octocat", "hello-world", 99, "https://run.url/2")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment
        assert "Hardcoded password detected." in comment

    def test_comment_shows_positive_observations(self):
        self._setup_mocks(result=FULL_RESULT)
        mod.review_pr("octocat", "hello-world", 99, "https://run.url/2")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "Clear variable names" in comment

    def test_comment_none_positive_observations_placeholder(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup_mocks(result=result)
        mod.review_pr("octocat", "hello-world", 1, "https://run.url")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_finding_with_null_line_renders_na(self):
        self._setup_mocks(result=FULL_RESULT)
        mod.review_pr("octocat", "hello-world", 99, "https://run.url/2")
        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment

    def test_returns_parsed_result_dict(self):
        self._setup_mocks(result=FULL_RESULT)
        result = mod.review_pr("octocat", "repo", 5, "https://run.url")
        assert isinstance(result, dict)
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_propagates_extract_json_error(self):
        shared_stub.get_pr_diff.return_value = "some diff"
        shared_stub.call_claude.return_value = "not json at all"
        with pytest.raises(ValueError):
            mod.review_pr("octocat", "repo", 1, "https://run.url")

    @pytest.mark.parametrize("pr_number", [1, 100, 9999])
    def test_various_pr_numbers(self, pr_number):
        