"""
Test suite for tool1_code_review.py

What is tested:
  - extract_json(): happy path, markdown-fenced input, embedded newlines, no JSON found,
    malformed JSON, outermost-block extraction
  - review_pr(): happy path, Claude response handling, comment formatting, return value
  - review_repo(): happy path, content truncation, file filtering
  - get_output_url(): URL construction
  - build_report_md(): full report generation, empty findings, missing keys

Mocks used:
  - shared.call_claude          (unittest.mock.patch)
  - shared.get_pr_diff          (unittest.mock.patch)
  - shared.get_repo_files       (unittest.mock.patch)
  - shared.post_pr_comment      (unittest.mock.patch)
  - shared.write_output_file    (unittest.mock.patch)
  - shared.send_email           (unittest.mock.patch)
  - shared.write_audit_entry    (unittest.mock.patch)
  - requests                    (not called directly in public API, but imported)

TODOs:
  - TODO: Integration tests for __main__ block require env-var wiring + live GitHub token
  - TODO: Test email sending path once send_email call-site is confirmed in __main__
  - TODO: Verify write_audit_entry payload schema when full __main__ is available
"""

import json
import sys
import os
import datetime
import importlib
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal 'shared' stub so the module can be imported
# without the real shared.py on the path.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="{}")
    shared.get_repo_files = MagicMock(return_value={})
    shared.get_pr_diff = MagicMock(return_value="diff text")
    shared.write_output_file = MagicMock()
    shared.post_pr_comment = MagicMock()
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html></html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Install stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load via spec so we don't rely on it being on sys.path
spec = importlib.util.spec_from_file_location("tool1_code_review", _script_path)
cr = importlib.util.module_from_spec(spec)

# Patch 'shared' inside the module's namespace before exec
cr.__dict__["__spec__"] = spec
sys.modules["tool1_code_review"] = cr
spec.loader.exec_module(cr)

extract_json   = cr.extract_json
review_pr      = cr.review_pr
review_repo    = cr.review_repo
get_output_url = cr.get_output_url
build_report_md = cr.build_report_md

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the code is acceptable.",
    "score": 72,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": [],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues were found.",
    "score": 45,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/auth.py",
            "line": 23,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Bare except clause used.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["Good test coverage.", "Consistent naming conventions."],
    "iac_findings": ["S3 bucket missing encryption.", "IAM policy is overly permissive."],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs before every test."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_json_object(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 72
        assert result["merge_recommendation"] == "APPROVE"

    def test_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == "Overall the code is acceptable."

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 72

    def test_markdown_fenced_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 72

    def test_json_embedded_in_prose(self):
        raw = "Here is my review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd."
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_newline_inside_string_value_cleaned(self):
        # Simulate a response where a string value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 50}'
        result = extract_json(raw)
        assert result["score"] == 50
        # After cleaning the newline should be replaced with a space
        assert "\n" not in result["summary"]

    def test_no_json_found_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This response has no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json('{"score": 50, "summary": "missing closing brace"')

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_extra_text_before_and_after_braces(self):
        inner = json.dumps({"score": 99, "summary": "great"})
        raw = f"Preamble text {inner} postamble text"
        result = extract_json(raw)
        assert result["score"] == 99

    def test_nested_objects_parsed_correctly(self):
        data = {"score": 60, "findings": [{"severity": "LOW", "file": "a.py", "line": 1,
                                           "issue": "minor", "recommendation": "fix"}]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["file"] == "a.py"

    def test_score_boundary_zero(self):
        raw = json.dumps({**MINIMAL_RESULT, "score": 0})
        assert extract_json(raw)["score"] == 0

    def test_score_boundary_hundred(self):
        raw = json.dumps({**MINIMAL_RESULT, "score": 100})
        assert extract_json(raw)["score"] == 100

    def test_only_closing_brace_no_opening(self):
        with pytest.raises(ValueError):
            extract_json("no opening brace }")

    def test_markdown_fence_multiline(self):
        payload = json.dumps(FULL_RESULT)
        raw = f"Sure, here you go:\n```json\n{payload}\n```\nLet me know if you have questions."
        result = extract_json(raw)
        assert result["score"] == 45


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result=None):
        if result is None:
            result = FULL_RESULT
        _shared_stub.get_pr_diff.return_value = "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new"
        _shared_stub.call_claude.return_value = json.dumps(result)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        assert result["score"] == 45
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("myorg", "myrepo", 42)

    def test_calls_call_claude_with_diff_in_prompt(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[1]

    def test_calls_post_pr_comment(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args[0]
        assert call_args[0] == "myorg"
        assert call_args[1] == "myrepo"
        assert call_args[2] == 42

    def test_comment_contains_score(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "45" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment

    def test_comment_contains_findings(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected" in comment

    def test_comment_shows_no_findings_when_empty(self):
        self._setup_mocks(result=MINIMAL_RESULT)
        review_pr("myorg", "myrepo", 1, "https://ci.example.com/run/2")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_shows_positive_observations(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment

    def test_comment_shows_none_when_no_positive_observations(self):
        self._setup_mocks(result=MINIMAL_RESULT)
        review_pr("myorg", "myrepo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_comment_header_present(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "## Claude Code Review" in comment

    def test_pr_number_zero_edge_case(self):
        """PR number 0 is unusual but the function should not crash."""
        self._setup_mocks(result=MINIMAL_RESULT)
        result = review_pr("org", "repo", 0, "")
        assert "score" in result

    def test_invalid_claude_response_propagates_error(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "not valid json at all"
        with pytest.raises(ValueError):
            review_pr("org", "repo", 1, "")

    def test_line_null_rendered_as_na(self):
        result_with_null_line = {
            **MINIMAL_RESULT,
            "findings": [
                {"severity": "LOW", "category": "maintainability",
                 "file": "a.py", "line": None,
                 "issue": "Issue here", "recommendation": "Fix it"}
            ]
        }
        self._setup_mocks(result=result_with_null_line)
        review_pr("org", "repo", 5, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment


# ===========================================================================
# review_repo
# ===========================================================================

class TestReviewRepo:

    def _setup_mocks(self, files=None, result=None):
        if result is None:
            result = FULL_RESULT
        if files is None:
            files = {
                "src/auth.py": "import os\npassword = 'secret'",
                "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
            }
        _shared_stub.get_repo_files.return_value = files
        _shared_stub.call_claude.return_value = json.dumps(result)

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = review_repo("myorg", "myrepo", "https://ci.example.com/run/1")
        assert result["score"] == 45

    def test_calls_get_repo_files_with_extensions(self