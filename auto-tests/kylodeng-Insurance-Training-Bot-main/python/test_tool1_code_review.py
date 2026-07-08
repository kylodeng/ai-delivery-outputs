"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  no JSON found, malformed JSON, direct parse fallback
- review_pr: happy path, comment formatting, result propagation
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction
- build_report_md: full report generation, empty findings, missing keys

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- requests (not directly called in tested functions but imported)

TODOs:
- TODO: Integration test for __main__ block requires full env setup
- TODO: email_html / send_email flow tested only via mock; real template rendering untested
- TODO: write_output_file / write_audit_entry side-effects not verified end-to-end
"""

import json
import re
import datetime
import sys
import os
import types
import importlib
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Provide a minimal stub for `shared` so we don't need the real module
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude = MagicMock()
_shared_stub.get_repo_files = MagicMock()
_shared_stub.get_pr_diff = MagicMock()
_shared_stub.write_output_file = MagicMock()
_shared_stub.post_pr_comment = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock(return_value="<html></html>")
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import tool1_code_review as cr


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code is clean.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good use of type hints."],
    "iac_findings": ["S3 bucket lacks versioning."],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared stubs between tests."""
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

    def test_plain_json_string(self):
        raw = json.dumps(VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_with_leading_trailing_whitespace(self):
        raw = "   " + json.dumps(VALID_RESULT) + "   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Overall the code is clean."

    def test_markdown_fences_triple_backtick(self):
        raw = "```\n" + json.dumps(VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fences_with_language_hint(self):
        raw = "```json\n" + json.dumps(VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_embedded_in_prose(self):
        raw = "Here is my review:\n" + json.dumps(VALID_RESULT) + "\nEnd."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a response where a string value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This may fail direct parse; extract_json should clean it
        result = cr.extract_json(raw)
        assert result["score"] == 70

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("There is absolutely no JSON here at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 50, "summary": "bad json"')

    def test_only_opening_brace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("{no closing brace")

    def test_minimal_valid_json(self):
        raw = '{"score": 0}'
        result = cr.extract_json(raw)
        assert result["score"] == 0

    def test_nested_json_objects_parsed_correctly(self):
        nested = {
            "summary": "ok",
            "score": 60,
            "merge_recommendation": "REQUEST_CHANGES",
            "findings": [{"severity": "LOW", "category": "maintainability",
                          "file": "a.py", "line": None, "issue": "x", "recommendation": "y"}],
            "positive_observations": [],
            "iac_findings": [],
        }
        result = cr.extract_json(json.dumps(nested))
        assert result["findings"][0]["line"] is None

    def test_markdown_fence_no_newline_after_fence(self):
        raw = "```" + json.dumps(VALID_RESULT) + "```"
        result = cr.extract_json(raw)
        assert isinstance(result, dict)

    def test_score_boundary_zero(self):
        raw = json.dumps({**VALID_RESULT, "score": 0})
        result = cr.extract_json(raw)
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        raw = json.dumps({**VALID_RESULT, "score": 100})
        result = cr.extract_json(raw)
        assert result["score"] == 100

    def test_empty_findings_list(self):
        raw = json.dumps({**VALID_RESULT, "findings": []})
        result = cr.extract_json(raw)
        assert result["findings"] == []

    def test_extra_text_before_json(self):
        preamble = "Sure! Here is the analysis you requested.\n\n"
        raw = preamble + json.dumps(VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------


class TestReviewPr:

    def _setup(self, result_override=None):
        result = result_override or VALID_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py ..."
        _shared_stub.call_claude.return_value = json.dumps(result)

    def test_happy_path_returns_result(self):
        self._setup()
        result = cr.review_pr("my-org", "my-repo", 42, "https://github.com/run/1")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_get_pr_diff_called_with_correct_args(self):
        self._setup()
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _shared_stub.get_pr_diff.assert_called_once_with("my-org", "my-repo", 7)

    def test_call_claude_called_once(self):
        self._setup()
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        assert _shared_stub.call_claude.call_count == 1

    def test_post_pr_comment_called_once(self):
        self._setup()
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _shared_stub.post_pr_comment.assert_called_once()

    def test_comment_contains_score(self):
        self._setup()
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "85" in comment

    def test_comment_contains_recommendation(self):
        self._setup()
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._setup()
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "Overall the code is clean." in comment

    def test_comment_contains_finding_info(self):
        self._setup()
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "src/app.py" in comment
        assert "HIGH" in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup({**VALID_RESULT, "findings": []})
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "_No findings_" in comment

    def test_comment_no_positive_observations_shows_placeholder(self):
        self._setup({**VALID_RESULT, "positive_observations": []})
        cr.review_pr("my-org", "my-repo", 7, "https://run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "_None_" in comment

    def test_post_pr_comment_receives_correct_owner_repo_pr(self):
        self._setup()
        cr.review_pr("acme", "widget", 99, "https://run")
        owner, repo, pr_num, _ = _shared_stub.post_pr_comment.call_args[0]
        assert owner == "acme"
        assert repo == "widget"
        assert pr_num == 99

    def test_finding_with_null_line_rendered_as_na(self):
        result = {**VALID_RESULT, "findings": [{
            **VALID_RESULT["findings"][0], "line": None
        }]}
        self._setup(result)
        cr.review_pr("my-org", "my-repo", 1, "https://run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "n/a" in comment

    def test_call_claude_passes_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "my unique diff content XYZ"
        _shared_stub.call_claude.return_value = json.dumps(VALID_RESULT)
        cr.review_pr("my-org", "my-repo", 1, "https://run")
        prompt_arg = _shared_stub.call_claude.call_args[0][1]
        assert "my unique diff content XYZ" in prompt_arg

    def test_claude_raises_propagates(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            cr.review_pr("my-org", "my-repo", 1, "https://run")


# ---------------------------------------------------------------------------
# review_repo tests
# ---------------------------------------------------------------------------


class TestReviewRepo:

    def _setup_files(self, files=None):
        if files is None:
            files = {
                "src/app.py": "print('hello')" * 100,
                "infra/main.tf": "resource 'aws_s3_bucket' {}",
                "src/utils.js": "const x = 1;",
            }
        _shared_stub.get_repo_files.return_value = files
        _shared_stub.call_claude.return_value = json.dumps(VALID_RESULT)

    def test_happy_path_returns_parsed_result(self):
        self._setup_files()
        result = cr.review_repo("my-org", "my-repo", "https://run")
        assert result["score"] == 85

    def test_get_repo_files_called_with_extensions(self):
        self._setup_files()
        cr.review_repo("my-org", "my-repo", "https://run")
        args = _shared_stub.get_repo_files.call_args[0]
        assert ".py" in args[2]
        assert ".tf" in args[2]
        assert ".yaml" in args[2]

    def test_call_claude_called_once(self):
        self._setup_files()
        cr.review_repo("my-org", "my-repo", "https://run")
        assert _shared_stub.call_claude.call_count == 1

    def test_call_claude_max_tokens_set(self):
        self._setup_files()
        cr.review_repo("my-org", "my-repo", "https://run")
        kwargs = _shared_stub.call_claude.call_args[1]
        assert kwargs.get("max_tokens") == 8096

    def test_content_truncated_at_20000_chars(self):
        # Build a large file set totalling >> 20000 chars
        big_files = {f"file_{i}.py": "x" * 3000 for i in range(20)}
        _shared_stub.get_repo_files.return_value = big_files
        _shared_stub.call_claude.return_value = json.dumps