"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fence stripping, outermost-brace extraction,
  newline-in-string cleaning, missing JSON, completely invalid input
- review_pr(): diff retrieval, Claude call, comment posting, result returned
- review_repo(): file retrieval, content truncation, Claude call, result returned
- get_output_url(): URL construction
- build_report_md(): full report markdown generation, empty findings, missing keys

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- shared.send_email (patched at tool1_code_review.send_email)
- requests (not called directly by tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup
- TODO: Test write_output_file / send_email integration inside review_pr/review_repo
         once shared module contract is confirmed
- TODO: Verify exact truncation behaviour when combined file content > 20 000 chars
"""

import importlib
import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import without the real one
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="{}")
    shared.get_repo_files = MagicMock(return_value={})
    shared.get_pr_diff = MagicMock(return_value="")
    shared.write_output_file = MagicMock(return_value=None)
    shared.post_pr_comment = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer fake"}
    shared.GH_API = "https://api.github.com"
    return shared


# Install the stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Also stub `requests` to prevent accidental network calls
if "requests" not in sys.modules:
    sys.modules["requests"] = MagicMock()

# Now import the module under test
import tool1_code_review as cr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_JSON = {
    "summary": "Looks good",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean code"],
    "iac_findings": [],
}

FULL_VALID_JSON = {
    "summary": "Several issues found",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password",
            "recommendation": "Use environment variables",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils.py",
            "line": None,
            "issue": "Missing docstring",
            "recommendation": "Add docstring",
        },
    ],
    "positive_observations": ["Good test coverage", "CI pipeline present"],
    "iac_findings": ["S3 bucket lacks versioning"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mocks before every test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_valid_json(self):
        raw = json.dumps(MINIMAL_VALID_JSON)
        result = cr.extract_json(raw)
        assert result == MINIMAL_VALID_JSON

    def test_plain_json_with_leading_trailing_whitespace(self):
        raw = "  \n" + json.dumps(MINIMAL_VALID_JSON) + "\n  "
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fenced_json_backtick_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_VALID_JSON) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_markdown_fenced_json_plain_backticks(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_JSON) + "\n```"
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good"

    def test_extra_text_before_and_after_json(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_VALID_JSON) + "\nDone."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_newline_inside_string_value_gets_cleaned(self):
        # Simulate a value with a literal newline that breaks JSON
        raw = '{"summary": "line one\nline two", "score": 10, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This is intentionally invalid JSON; extract_json should recover
        result = cr.extract_json(raw)
        assert "line one" in result["summary"]
        assert result["score"] == 10

    def test_full_valid_json(self):
        raw = json.dumps(FULL_VALID_JSON)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_no_braces_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_malformed_json_inside_braces_raises_value_error(self):
        raw = "{ totally: not valid json !! }"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_nested_json_returned_correctly(self):
        nested = {
            "summary": "ok",
            "score": 99,
            "merge_recommendation": "APPROVE",
            "findings": [{"severity": "LOW", "category": "correctness",
                          "file": "a.py", "line": 1,
                          "issue": "x", "recommendation": "y"}],
            "positive_observations": [],
            "iac_findings": [],
        }
        result = cr.extract_json(json.dumps(nested))
        assert result["findings"][0]["file"] == "a.py"

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \t\n   ")

    def test_score_zero_is_valid(self):
        data = {**MINIMAL_VALID_JSON, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred_is_valid(self):
        data = {**MINIMAL_VALID_JSON, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_line_null_is_preserved(self):
        finding = {**FULL_VALID_JSON["findings"][1], "line": None}
        data = {**MINIMAL_VALID_JSON, "findings": [finding]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_unicode_values_parsed(self):
        data = {**MINIMAL_VALID_JSON, "summary": "Résumé: café code ☕"}
        result = cr.extract_json(json.dumps(data, ensure_ascii=False))
        assert "café" in result["summary"]

    def test_markdown_fence_without_trailing_newline(self):
        raw = "```json\n" + json.dumps(MINIMAL_VALID_JSON) + "```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_all_merge_recommendations(self, recommendation):
        data = {**MINIMAL_VALID_JSON, "merge_recommendation": recommendation}
        result = cr.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:
    def _setup_claude(self, payload=None):
        if payload is None:
            payload = FULL_VALID_JSON
        _shared_stub.call_claude.return_value = json.dumps(payload)

    def test_happy_path_returns_result(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/example.py b/src/example.py\n+password = 'secret'"
        result = cr.review_pr("myorg", "myrepo", 42, "https://run.url")
        assert result["score"] == FULL_VALID_JSON["score"]

    def test_get_pr_diff_called_correctly(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "some diff"
        cr.review_pr("owner1", "repo1", 7, "https://run.url")
        _shared_stub.get_pr_diff.assert_called_once_with("owner1", "repo1", 7)

    def test_call_claude_called_with_diff(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "my diff content"
        cr.review_pr("owner1", "repo1", 7, "https://run.url")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "my diff content" in args[1]

    def test_post_pr_comment_called(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("owner2", "repo2", 99, "https://run.url")
        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args[0]
        assert call_args[0] == "owner2"
        assert call_args[1] == "repo2"
        assert call_args[2] == 99

    def test_comment_contains_score(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("o", "r", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "42/100" in comment

    def test_comment_contains_recommendation(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("o", "r", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment

    def test_comment_contains_findings(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("o", "r", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password" in comment

    def test_comment_no_findings_shows_placeholder(self):
        payload = {**MINIMAL_VALID_JSON, "findings": []}
        _shared_stub.call_claude.return_value = json.dumps(payload)
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("o", "r", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_positive_observations_listed(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("o", "r", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment

    def test_comment_no_positive_observations_shows_none(self):
        payload = {**MINIMAL_VALID_JSON, "positive_observations": []}
        _shared_stub.call_claude.return_value = json.dumps(payload)
        _shared_stub.get_pr_diff.return_value = "diff"
        cr.review_pr("o", "r", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_result_has_findings_list(self):
        self._setup_claude()
        _shared_stub.get_pr_diff.return_value = "diff"
        result = cr.review_pr("o", "r", 1, "")
        assert isinstance(result["findings"], list)

    def test_missing_score_key_handled_gracefully(self):
        payload = {k: v for k, v in FULL_VALID_JSON.items() if k != "score"}
        _shared_stub.call_claude.return_value = json.dumps(payload)
        _shared_stub.get_pr_diff.return_value = "diff"
        result = cr.review_pr("o", "r", 1, "")
        assert result.get("score") is None  # key absent is fine

    def test_empty_diff_still_calls_claude(self):
        self._setup_claude(MINIMAL_VALID_JSON)
        _shared_stub.get_pr_diff.return_value = ""
        cr.review_pr("o", "r", 1, "")
        _shared_stub.call_claude.assert_called_once()

    def test_claude_returns_malformed_json_raises(self):
        _shared_stub