"""
Test module for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fence stripping, brace extraction,
  newline-inside-string cleanup, missing JSON, malformed JSON
- review_pr(): happy path, comment formatting, Claude/API error propagation
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
- requests                    (not called directly in public API; patched where needed)

TODOs:
- TODO: Integration test for __main__ block requires real env vars and GH tokens
- TODO: Tests for send_email / write_output_file orchestration paths (need full main() surface)
- TODO: Verify behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO env vars are absent
"""

import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal stub for the `shared` module so the import of
# tool1_code_review does not fail in an environment where shared.py is absent.
# ---------------------------------------------------------------------------
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
_shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Ensure the scripts directory is on the path so the module can be imported.
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Also add the directory that actually contains the source file so a local run works.
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import tool1_code_review as t1  # noqa: E402  (import after path setup)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks acceptable.",
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
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected",
            "recommendation": "Use environment variables instead",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Function is too long",
            "recommendation": "Split into smaller functions",
        },
    ],
    "positive_observations": ["Good use of type hints", "Well structured modules"],
    "iac_findings": ["S3 bucket lacks encryption", "IAM role is overly permissive"],
}


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for extract_json()"""

    def test_plain_valid_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = t1.extract_json(raw)
        assert result["summary"] == "Code looks acceptable."

    def test_markdown_fence_triple_backtick(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fence_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_json_embedded_in_prose(self):
        """JSON buried inside surrounding text – brace extraction path."""
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nThat is all."
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_newline_inside_string_value_cleaned(self):
        """Claude sometimes embeds literal newlines inside string values."""
        dirty = '{"summary": "First line\nSecond line", "score": 70}'
        # Should not raise; cleaned version should parse
        result = t1.extract_json(dirty)
        assert result["score"] == 70

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("This is just plain text with no braces at all.")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json('{ "score": 80, "broken": }')

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_empty_json_object(self):
        result = t1.extract_json("{}")
        assert result == {}

    def test_markdown_fence_with_extra_text_after(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```\n\nSome trailing text."
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = t1.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {**FULL_RESULT}
        raw = json.dumps(data)
        result = t1.extract_json(raw)
        # Second finding has line: None
        assert result["findings"][1]["line"] is None

    def test_brace_extraction_when_direct_parse_fails(self):
        """Surrounding non-JSON text forces brace-extraction path."""
        inner = json.dumps({"score": 99, "summary": "ok"})
        raw = "Preamble text " + inner + " postamble text"
        result = t1.extract_json(raw)
        assert result["score"] == 99

    def test_deeply_nested_findings(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "CRITICAL", "category": "security",
             "file": "main.tf", "line": 1,
             "issue": "IAM *", "recommendation": "Restrict IAM"},
        ]}
        result = t1.extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == "CRITICAL"


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:
    """Tests for review_pr()"""

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_happy_path_returns_result(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff --git a/src/example.py ..."
        mock_claude.return_value = json.dumps(MINIMAL_RESULT)

        result = t1.review_pr("acme", "backend", 42, "https://ci/run/1")

        assert result["score"] == 80
        mock_diff.assert_called_once_with("acme", "backend", 42)
        mock_claude.assert_called_once()
        mock_comment.assert_called_once()

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_comment_contains_score(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff text"
        mock_claude.return_value = json.dumps(MINIMAL_RESULT)

        t1.review_pr("acme", "backend", 1, "https://ci/run/1")

        comment_text = mock_comment.call_args[0][3]
        assert "80" in comment_text
        assert "APPROVE" in comment_text

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_comment_contains_findings(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff text"
        mock_claude.return_value = json.dumps(FULL_RESULT)

        t1.review_pr("acme", "backend", 7, "https://ci/run/1")

        comment_text = mock_comment.call_args[0][3]
        assert "HIGH" in comment_text
        assert "Hardcoded password detected" in comment_text

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_no_findings_shows_no_findings_placeholder(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff text"
        mock_claude.return_value = json.dumps(MINIMAL_RESULT)

        t1.review_pr("acme", "backend", 3, "https://ci/run/1")

        comment_text = mock_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_pr_comment_called_with_correct_args(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = json.dumps(MINIMAL_RESULT)

        t1.review_pr("org", "myrepo", 99, "https://ci/run/99")

        args = mock_comment.call_args[0]
        assert args[0] == "org"
        assert args[1] == "myrepo"
        assert args[2] == 99

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_propagates_claude_error(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.side_effect = RuntimeError("Claude API unavailable")

        with pytest.raises(RuntimeError, match="Claude API unavailable"):
            t1.review_pr("org", "repo", 1, "https://ci/run/1")

        mock_comment.assert_not_called()

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_propagates_diff_error(self, mock_diff, mock_claude, mock_comment):
        mock_diff.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError):
            t1.review_pr("org", "repo", 5, "https://ci/run/5")

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_malformed_claude_response_raises(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = "This is not JSON at all!!!"

        with pytest.raises(ValueError):
            t1.review_pr("org", "repo", 2, "https://ci/run/2")

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_result_merge_recommendation_request_changes(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = json.dumps(FULL_RESULT)

        result = t1.review_pr("acme", "backend", 10, "https://ci/run/10")
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_positive_observations_included_in_comment(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = json.dumps(FULL_RESULT)

        t1.review_pr("acme", "backend", 11, "https://ci/run/11")

        comment_text = mock_comment.call_args[0][3]
        assert "Good use of type hints" in comment_text
        assert "Well structured modules" in comment_text


# ---------------------------------------------------------------------------
# review_repo
# ---------------------------------------------------------------------------

class TestReviewRepo:
    """Tests for review_repo()"""

    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.