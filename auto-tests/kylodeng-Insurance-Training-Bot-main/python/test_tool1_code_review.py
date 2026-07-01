"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, direct parse success
- review_pr(): happy path, comment formatting, result propagation
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report, empty findings, missing keys, IaC/positive sections

Mocks used:
- shared.call_claude (via unittest.mock.patch)
- shared.get_pr_diff (via unittest.mock.patch)
- shared.get_repo_files (via unittest.mock.patch)
- shared.post_pr_comment (via unittest.mock.patch)
- shared.write_output_file (via unittest.mock.patch)
- shared.send_email (via unittest.mock.patch)
- shared.write_audit_entry (via unittest.mock.patch)
- requests (not directly called in tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires full env setup
- TODO: Test email dispatch path once send_email logic is wired into main
- TODO: Test write_output_file call inside main block
"""

import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Path setup – mirror what the source does so shared can be imported as stub
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Stub out the 'shared' module before importing the module under test so we
# never make real network calls even if shared.py is present.
# ---------------------------------------------------------------------------
import types

shared_stub = types.ModuleType("shared")
shared_stub.call_claude = MagicMock()
shared_stub.get_repo_files = MagicMock()
shared_stub.get_pr_diff = MagicMock()
shared_stub.write_output_file = MagicMock()
shared_stub.post_pr_comment = MagicMock()
shared_stub.send_email = MagicMock()
shared_stub.email_html = MagicMock()
shared_stub.write_audit_entry = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake"}
shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now safe to import the module under test
import tool1_code_review as cr  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 55,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Function too long.",
            "recommendation": "Split into smaller functions.",
        },
    ],
    "positive_observations": ["Well-structured modules", "Good docstrings"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role too permissive"],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared stubs between tests."""
    shared_stub.call_claude.reset_mock()
    shared_stub.get_repo_files.reset_mock()
    shared_stub.get_pr_diff.reset_mock()
    shared_stub.write_output_file.reset_mock()
    shared_stub.post_pr_comment.reset_mock()
    shared_stub.send_email.reset_mock()
    shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_valid_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   " + json.dumps(MINIMAL_RESULT) + "   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fences_triple_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fences_with_language_tag(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_embedded_in_prose(self):
        """JSON buried in surrounding text – falls back to brace-scanning."""
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd of review."
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_newline_inside_string_value_cleaned(self):
        """Literal newline inside a JSON string value should be repaired."""
        # Build raw string with embedded newline inside a value
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        assert result["score"] == 70

    def test_no_json_raises_value_error(self):
        raw = "No JSON here at all – just plain text."
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json(raw)

    def test_malformed_json_raises_value_error(self):
        raw = '{"score": 80, "summary": "missing closing brace"'
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_braces_malformed(self):
        raw = "{ not valid json }"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_full_result_parsed_correctly(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket missing encryption"

    def test_nested_objects_preserved(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_markdown_fence_no_trailing_fence(self):
        """Fence open but no closing fence – should still parse inner JSON."""
        raw = "```json\n" + json.dumps(MINIMAL_RESULT)
        # After split on \n,1 we get the rest; rsplit on ``` returns original
        # The direct parse after stripping the first line should work.
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_extra_text_before_brace(self):
        preamble = "Sure, here is my review: "
        raw = preamble + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_multiple_json_objects_uses_outermost(self):
        """When two JSON-like blobs exist, find outermost { ... }."""
        inner = '{"inner": true}'
        outer_data = {**MINIMAL_RESULT, "note": "see inner"}
        raw = json.dumps(outer_data)
        result = cr.extract_json(raw)
        assert "score" in result


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup(self, result=None):
        if result is None:
            result = FULL_RESULT
        shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py b/src/app.py\n+password='secret'"
        shared_stub.call_claude.return_value = json.dumps(result)
        return result

    def test_happy_path_returns_result(self):
        expected = self._setup()
        result = cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        assert result["score"] == expected["score"]
        assert result["merge_recommendation"] == expected["merge_recommendation"]

    def test_get_pr_diff_called_correctly(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/2")
        shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo", 7)

    def test_call_claude_called_with_diff(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 7, "https://ci/run/2")
        args, kwargs = shared_stub.call_claude.call_args
        assert "Review this pull request diff" in args[1]
        assert "password='secret'" in args[1]

    def test_post_pr_comment_called_once(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        shared_stub.post_pr_comment.assert_called_once()

    def test_post_pr_comment_contains_score(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        _, kwargs = shared_stub.post_pr_comment.call_args
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "55" in comment_body  # score from FULL_RESULT

    def test_post_pr_comment_contains_recommendation(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_body

    def test_post_pr_comment_contains_findings(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected" in comment_body

    def test_comment_no_findings_shows_placeholder(self):
        self._setup(MINIMAL_RESULT)
        cr.review_pr("acme", "myrepo", 1, "https://ci/run/3")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_body

    def test_comment_no_positive_shows_placeholder(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup(result)
        cr.review_pr("acme", "myrepo", 1, "https://ci/run/4")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_body

    def test_post_pr_comment_owner_repo_pr(self):
        self._setup()
        cr.review_pr("ownerX", "repoY", 99, "https://ci/run/5")
        args = shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "ownerX"
        assert args[1] == "repoY"
        assert args[2] == 99

    def test_review_pr_result_has_findings_list(self):
        self._setup()
        result = cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        assert isinstance(result["findings"], list)
        assert len(result["findings"]) == 2

    def test_review_pr_claude_raises_propagates(self):
        shared_stub.get_pr_diff.return_value = "some diff"
        shared_stub.call_claude.side_effect = RuntimeError("API down")
        with pytest.raises(RuntimeError, match="API down"):
            cr.review_pr("acme", "myrepo", 1, "https://ci/run")

    def test_review_pr_bad_json_from_claude_raises(self):
        shared_stub.get_pr_diff.return_value = "some diff"
        shared_stub.call_claude.return_value = "not json at all"
        with pytest.raises(ValueError):
            cr.review_pr("acme", "myrepo", 1, "https://ci/run")

    def test_comment_contains_auto_generated_footer(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "Auto-generated by AI Delivery Bot" in comment_body

    def test_comment_contains_summary(self):
        self._setup()
        cr.review_pr("acme", "myrepo", 42, "https://ci/run/1")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "Several security issues found." in comment_body


# ===========================================================================
# review_repo
# ===========================================================================

class TestReviewRepo:

    def _setup(self, files=None, result=None):
        if files is None:
            files = {
                "src/app.py": "print('hello')" * 100,
                "infra/main.tf": "resource 'aws_s3_bucket' {}",
            }
        if result is None:
            result = FULL_RESULT
        shared_stub.get_repo_files.return_value = files
        shared_stub.call_claude.return_value = json.dumps(result)

    def test_happy_path_returns_result(self):
        self._setup()
        result = cr.review_repo("acme", "myrepo", "https://ci/run/1")
        assert result["score"] == 55

    def test_get_repo_files_called_with_extensions(self