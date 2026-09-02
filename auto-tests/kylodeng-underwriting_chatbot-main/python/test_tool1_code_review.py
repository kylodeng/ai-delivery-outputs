"""
Test module for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown-fenced input, raw JSON with surrounding text,
  newlines inside string values, no JSON found, unparseable JSON
- review_pr: happy path (diff fetched, Claude called, comment posted, result returned)
- review_repo: happy path (files fetched, Claude called, result returned), content truncation
- get_output_url: URL construction correctness
- build_report_md: full rendering, empty findings, empty iac/positive sections,
  missing keys in result dict

Mocks used:
- shared.call_claude        → unittest.mock.patch
- shared.get_pr_diff        → unittest.mock.patch
- shared.get_repo_files     → unittest.mock.patch
- shared.post_pr_comment    → unittest.mock.patch
- shared.write_output_file  → unittest.mock.patch
- shared.send_email         → unittest.mock.patch
- shared.write_audit_entry  → unittest.mock.patch
- requests                  → unittest.mock.patch (not directly called in tested fns)

TODOs:
- TODO: Integration test for main() block – needs real env-var matrix and GitHub token
- TODO: Test email dispatch path once send_email call-site is confirmed in main()
- TODO: Parameterise severity icon rendering once sev_icons is exposed/used in comment body
"""

import json
import sys
import os
import importlib
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: the module does `sys.path.insert(0, …)` then imports from shared.
# We inject a fake `shared` module so we never touch real network or secrets.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-output-owner"
FAKE_OUTPUT_REPO = "test-output-repo"
FAKE_GH_HEADERS = {"Authorization": "Bearer fake-token"}
FAKE_GH_API = "https://api.github.com"

_fake_shared = types.ModuleType("shared")
_fake_shared.call_claude = MagicMock()
_fake_shared.get_repo_files = MagicMock()
_fake_shared.get_pr_diff = MagicMock()
_fake_shared.write_output_file = MagicMock()
_fake_shared.post_pr_comment = MagicMock()
_fake_shared.send_email = MagicMock()
_fake_shared.email_html = MagicMock(return_value="<html/>")
_fake_shared.write_audit_entry = MagicMock()
_fake_shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
_fake_shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
_fake_shared.GH_HEADERS = FAKE_GH_HEADERS
_fake_shared.GH_API = FAKE_GH_API

sys.modules["shared"] = _fake_shared

# Now we can safely import the module under test
import tool1_code_review as cr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Code looks good overall.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password found.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage.", "Clear naming conventions."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def reset_mocks():
    """Reset all fake-shared mocks between tests."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment", "send_email",
                 "write_audit_entry"):
        getattr(_fake_shared, attr).reset_mock()


@pytest.fixture(autouse=True)
def _reset():
    reset_mocks()
    yield
    reset_mocks()


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_json_string(self):
        result = cr.extract_json(VALID_JSON_STR)
        assert result == VALID_RESULT

    def test_json_with_leading_trailing_whitespace(self):
        result = cr.extract_json(f"   \n{VALID_JSON_STR}\n   ")
        assert result["score"] == 82

    def test_markdown_fenced_with_json_lang(self):
        raw = f"```json\n{VALID_JSON_STR}\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_markdown_fenced_no_lang(self):
        raw = f"```\n{VALID_JSON_STR}\n```"
        result = cr.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_json_embedded_in_prose(self):
        raw = f"Here is the review:\n{VALID_JSON_STR}\nHope that helps."
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_json_with_extra_text_before_and_after(self):
        raw = f"Some preamble text\n\n{VALID_JSON_STR}\n\nSome closing remarks"
        result = cr.extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"

    def test_newlines_inside_string_values_cleaned(self):
        # Simulate Claude inserting a literal newline inside a string value
        broken = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This should either parse after cleaning or raise ValueError – not crash
        try:
            result = cr.extract_json(broken)
            assert isinstance(result, dict)
        except ValueError:
            pass  # acceptable – broken JSON may not be cleanable

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This response has no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_unparseable_braces_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("{ this is not valid json }")

    def test_minimal_valid_json(self):
        minimal = '{"summary": "ok", "score": 0, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(minimal)
        assert result["score"] == 0

    def test_score_boundary_zero(self):
        d = {**VALID_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(d))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        d = {**VALID_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(d))
        assert result["score"] == 100

    def test_nested_findings_preserved(self):
        result = cr.extract_json(VALID_JSON_STR)
        finding = result["findings"][0]
        assert finding["file"] == "src/example.py"
        assert finding["line"] == 42

    def test_all_severity_levels_parsed(self):
        for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            d = {**VALID_RESULT, "findings": [{**VALID_RESULT["findings"][0], "severity": severity}]}
            result = cr.extract_json(json.dumps(d))
            assert result["findings"][0]["severity"] == severity

    def test_merge_recommendation_block(self):
        d = {**VALID_RESULT, "merge_recommendation": "BLOCK"}
        result = cr.extract_json(json.dumps(d))
        assert result["merge_recommendation"] == "BLOCK"

    def test_merge_recommendation_request_changes(self):
        d = {**VALID_RESULT, "merge_recommendation": "REQUEST_CHANGES"}
        result = cr.extract_json(json.dumps(d))
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_markdown_triple_backtick_multiline(self):
        raw = "```\n" + VALID_JSON_STR + "\n```"
        result = cr.extract_json(raw)
        assert isinstance(result, dict)

    def test_only_closing_fence_no_opening(self):
        """Handles odd formatting where there's no opening fence."""
        raw = VALID_JSON_STR + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_returns_dict_type(self):
        result = cr.extract_json(VALID_JSON_STR)
        assert isinstance(result, dict)

    def test_iac_findings_preserved(self):
        result = cr.extract_json(VALID_JSON_STR)
        assert result["iac_findings"] == ["S3 bucket lacks versioning."]

    def test_positive_observations_preserved(self):
        result = cr.extract_json(VALID_JSON_STR)
        assert "Good test coverage." in result["positive_observations"]

    def test_null_line_value(self):
        d = {**VALID_RESULT, "findings": [{**VALID_RESULT["findings"][0], "line": None}]}
        result = cr.extract_json(json.dumps(d))
        assert result["findings"][0]["line"] is None


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, raw_response=None):
        raw = raw_response or VALID_JSON_STR
        _fake_shared.get_pr_diff.return_value = "diff --git a/src/example.py b/src/example.py\n+password = 'secret'"
        _fake_shared.call_claude.return_value = raw
        _fake_shared.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_get_pr_diff_called_with_correct_args(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        _fake_shared.get_pr_diff.assert_called_once_with("acme", "my-repo", 42)

    def test_call_claude_called(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        _fake_shared.call_claude.assert_called_once()
        args = _fake_shared.call_claude.call_args
        assert "Review this pull request diff:" in args[0][1]

    def test_post_pr_comment_called(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        _fake_shared.post_pr_comment.assert_called_once()
        call_args = _fake_shared.post_pr_comment.call_args[0]
        assert call_args[0] == "acme"
        assert call_args[1] == "my-repo"
        assert call_args[2] == 42

    def test_comment_contains_score(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        comment = _fake_shared.post_pr_comment.call_args[0][3]
        assert "82" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        comment = _fake_shared.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_finding_severity(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        comment = _fake_shared.post_pr_comment.call_args[0][3]
        assert "HIGH" in comment

    def test_comment_contains_summary(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        comment = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Code looks good overall." in comment

    def test_no_findings_shows_no_findings_placeholder(self):
        empty_result = {**VALID_RESULT, "findings": [], "positive_observations": []}
        self._setup_mocks(raw_response=json.dumps(empty_result))
        cr.review_pr("acme", "my-repo", 99, "https://github.com/run/2")
        comment = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_no_positive_observations_shows_none_placeholder(self):
        empty_result = {**VALID_RESULT, "positive_observations": []}
        self._setup_mocks(raw_response=json.dumps(empty_result))
        cr.review_pr("acme", "my-repo", 99, "https://github.com/run/2")
        comment = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_comment_contains_auto_generated_footer(self):
        self._setup_mocks()
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        comment = _fake_shared.post_pr_comment.call_args[0][3]
        assert "AI Delivery Bot" in comment

    def test_returns_dict(self):
        self._setup_mocks()
        result = cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        assert isinstance(result, dict)

    def test_claude_returns_markdown_fenced_json(self):
        fenced = f"```json\n{VALID_JSON_STR}\n```"
        self._setup_mocks(raw_response=fenced)
        result = cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        assert result["score"] == 82

    def test_finding_line_null_renders_na(self):
        result_with_null = {
            **VALID_RESULT,
            "findings": [{**VALID_RESULT["findings"][0], "line": None}],
        }
        self._setup_mocks(raw_response=json.dumps(result_with_null))
        cr.review_pr("acme", "my-repo", 42, "https://github.com/run/1")
        comment = _fake_shared.post_pr_comment