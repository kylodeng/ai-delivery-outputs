"""
Test module for tool1_code_review.py

What is tested:
- extract_json(): JSON extraction from raw Claude responses (happy path, markdown fences,
  embedded newlines, missing braces, malformed JSON, edge cases)
- review_pr(): PR diff retrieval, Claude call, comment posting, result assembly
- review_repo(): Repo file retrieval, Claude call, result assembly
- get_output_url(): URL construction
- build_report_md(): Markdown report generation (findings, IaC, positive observations,
  empty fields, boundary values)

Mocks used:
- shared.call_claude (via unittest.mock.patch)
- shared.get_pr_diff (via unittest.mock.patch)
- shared.get_repo_files (via unittest.mock.patch)
- shared.post_pr_comment (via unittest.mock.patch)
- shared.write_output_file (via unittest.mock.patch)
- shared.write_audit_entry (via unittest.mock.patch)
- shared.send_email (via unittest.mock.patch)
- requests (not called directly by tested functions, but patched at module level)
- datetime.datetime.utcnow (patched to produce deterministic timestamps)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup and subprocess
- TODO: Test email sending path in main block (needs RECIPIENTS env var context)
- TODO: Test write_output_file / write_audit_entry integration paths in main block
"""

import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the script's directory is importable (mirrors sys.path.insert in source)
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

# We must stub out `shared` before importing the module under test so that
# the top-level `from shared import ...` does not fail in CI.
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
_shared_stub.GH_HEADERS = {"Authorization": "token test"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)
sys.modules.setdefault("requests", MagicMock())

import importlib
tool = importlib.import_module("tool1_code_review")

extract_json = tool.extract_json
review_pr = tool.review_pr
review_repo = tool.review_repo
get_output_url = tool.get_output_url
build_report_md = tool.build_report_md


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall looks good.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean structure"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket is publicly accessible.",
            "recommendation": "Set block_public_acls = true.",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "utils/helper.py",
            "line": 55,
            "issue": "Bare except clause used.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["Good test coverage", "Consistent naming"],
    "iac_findings": ["Missing encryption on RDS instance", "IAM role too permissive"],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset shared stubs before every test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:

    def test_plain_json_object(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == "Overall looks good."

    def test_markdown_fence_triple_backtick(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fence_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is my review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd of review."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_with_embedded_newline_in_string(self):
        # Simulate a Claude response where a string value contains a literal newline
        candidate = '{"summary": "Line one\nLine two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # We don't expect this to be parseable as-is — we exercise the cleaning path
        # by wrapping it so the direct parse fails but the brace-extraction + clean succeeds.
        raw = "Some preamble\n" + candidate + "\nsome suffix"
        # The cleaning regex replaces \n inside strings with a space
        result = extract_json(raw)
        assert "score" in result

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_raises_value_error_on_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is plain text with no JSON at all.")

    def test_raises_value_error_on_malformed_json(self):
        raw = '{"score": 80, "summary": "broken'  # unclosed string / object
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_markdown_fence_with_extra_text_before(self):
        raw = "Sure! Here you go:\n```json\n" + json.dumps(MINIMAL_RESULT) + "\n```\nHope that helps."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_nested_findings_preserved(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert result["findings"][1]["file"] == "infra/main.tf"
        assert result["findings"][1]["line"] is None

    def test_score_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_block_recommendation(self):
        data = {**MINIMAL_RESULT, "merge_recommendation": "BLOCK"}
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "BLOCK"

    def test_multiple_json_objects_takes_outermost(self):
        # When text contains text before the JSON, brace extraction should still work
        inner = json.dumps({"note": "ignore me"})
        outer = json.dumps(MINIMAL_RESULT)
        raw = f"Note: {inner} but here is the real one: {outer}"
        result = extract_json(raw)
        # Should parse the last valid outer object found by rfind("}")
        assert "score" in result


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:

    def _setup_mocks(self, result=None):
        if result is None:
            result = FULL_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py ..."
        _shared_stub.call_claude.return_value = json.dumps(result)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        assert result["score"] == FULL_RESULT["score"]
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("myorg", "myrepo", 42)

    def test_calls_call_claude_with_diff_in_prompt(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "diff --git" in args[1]

    def test_posts_pr_comment(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args[0]
        assert call_args[0] == "myorg"
        assert call_args[1] == "myrepo"
        assert call_args[2] == 42

    def test_comment_contains_score(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "42/100" in comment

    def test_comment_contains_recommendation(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment

    def test_comment_contains_findings(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected" in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_mocks(result=MINIMAL_RESULT)
        review_pr("myorg", "myrepo", 1, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_positive_observations(self):
        self._setup_mocks()
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment

    def test_comment_no_positive_observations_shows_placeholder(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup_mocks(result=result)
        review_pr("myorg", "myrepo", 1, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_missing_score_in_result_shows_question_mark(self):
        data = {k: v for k, v in FULL_RESULT.items() if k != "score"}
        self._setup_mocks(result=data)
        review_pr("myorg", "myrepo", 42, "https://ci/run/1")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "?/100" in comment

    def test_invalid_claude_response_raises(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = "not json at all"
        with pytest.raises(ValueError):
            review_pr("myorg", "myrepo", 42, "https://ci/run/1")

    def test_pr_number_zero(self):
        """Boundary: PR number 0 (unusual but should not crash)."""
        self._setup_mocks(result=MINIMAL_RESULT)
        result = review_pr("myorg", "myrepo", 0, "https://ci/run/0")
        assert result is not None

    def test_different_owners_and_repos(self):
        self._setup_mocks(result=MINIMAL_RESULT)
        review_pr("alice", "her-repo", 99, "https://ci/run/99")
        _shared_stub.get_pr_diff.assert_called_once_with("alice", "her-repo", 99)

    def test_line_na_when_none(self):
        """Findings with line=None should render as 'None' or 'n/a' gracefully."""
        self._setup_mocks(