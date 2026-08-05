"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, boundary/edge cases
- review_pr(): happy path, comment formatting, result propagation
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report rendering, empty findings, missing keys

Mocks used:
- shared.call_claude          (patched at tool1_code_review.call_claude)
- shared.get_pr_diff          (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files       (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment      (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file    (patched at tool1_code_review.write_output_file)
- shared.send_email           (patched at tool1_code_review.send_email)
- shared.write_audit_entry    (patched at tool1_code_review.write_audit_entry)
- requests (not directly called in public API; patched defensively)

TODOs:
- TODO: Integration test for __main__ block requires env-var wiring and subprocess
- TODO: Test write_output_file / send_email orchestration once main() is complete
  (source file is truncated before the __main__ block finishes)
"""

import json
import sys
import os
import importlib
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without installing the package
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# We import the module under test after patching its heavy shared dependency
# so that import-time side-effects do not fire real network calls.
# ---------------------------------------------------------------------------
_shared_mock = MagicMock()
_shared_mock.OUTPUT_REPO_OWNER = "test-owner"
_shared_mock.OUTPUT_REPO = "test-output-repo"
_shared_mock.GH_HEADERS = {"Authorization": "Bearer test-token"}
_shared_mock.GH_API = "https://api.github.com"

with patch.dict("sys.modules", {"shared": _shared_mock}):
    import tool1_code_review as cr
    # Re-bind the names the module captured at import time so our patches work
    importlib.reload(cr)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several critical issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/auth.py",
            "line": 17,
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
            "file": "app/utils.py",
            "line": 99,
            "issue": "Bare except clause swallows all errors.",
            "recommendation": "Catch specific exception types.",
        },
    ],
    "positive_observations": ["CI pipeline is well configured.", "All secrets use Vault."],
    "iac_findings": ["Missing mandatory cost-centre tag on EC2 resource."],
}


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJsonHappyPath:
    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_leading_trailing_whitespace(self):
        raw = "   " + json.dumps(MINIMAL_RESULT) + "   \n"
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fences_backtick_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fences_plain_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n" + json.dumps(FULL_RESULT) + "\nEnd of review."
        result = cr.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 3

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result == FULL_RESULT

    def test_newline_inside_string_value_cleaned(self):
        """Claude sometimes emits literal newlines inside string values."""
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # The cleaned version should parse successfully (newline replaced with space)
        # This may succeed on direct parse failure path or cleaned path
        result = cr.extract_json(raw)
        assert "summary" in result

    def test_extra_text_before_brace(self):
        raw = "Sure! Here you go: " + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85


class TestExtractJsonEdgeCases:
    def test_minimal_valid_json(self):
        raw = '{"score": 0}'
        result = cr.extract_json(raw)
        assert result["score"] == 0

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        assert cr.extract_json(json.dumps(data))["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        assert cr.extract_json(json.dumps(data))["score"] == 100

    def test_findings_with_null_line(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "correctness",
             "file": "a.py", "line": None,
             "issue": "x", "recommendation": "y"}
        ]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_unicode_in_values(self):
        data = {**MINIMAL_RESULT, "summary": "Ünicode çharacters are fine."}
        result = cr.extract_json(json.dumps(data))
        assert "Ünicode" in result["summary"]


class TestExtractJsonErrorConditions:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("   \n  ")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 75, "summary": missing_quotes}')

    def test_only_opening_brace_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("{")

    def test_only_closing_brace_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("}")

    def test_markdown_fence_with_invalid_json_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("```json\n{invalid}\n```")

    def test_array_only_raises(self):
        """Top-level array without surrounding object should raise."""
        with pytest.raises(ValueError):
            cr.extract_json("[1, 2, 3]")


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:
    @pytest.fixture(autouse=True)
    def _patch(self):
        self._diff = "diff --git a/src/auth.py\n+password = 'secret'"
        self._raw_response = json.dumps(FULL_RESULT)
        with patch.object(cr, "get_pr_diff", return_value=self._diff) as mock_diff, \
             patch.object(cr, "call_claude", return_value=self._raw_response) as mock_claude, \
             patch.object(cr, "post_pr_comment") as mock_comment:
            self.mock_diff = mock_diff
            self.mock_claude = mock_claude
            self.mock_comment = mock_comment
            yield

    def test_returns_parsed_result(self):
        result = cr.review_pr("owner", "repo", 42, "https://run.url")
        assert result["score"] == 42
        assert result["merge_recommendation"] == "BLOCK"

    def test_calls_get_pr_diff_with_correct_args(self):
        cr.review_pr("myowner", "myrepo", 7, "https://run.url")
        self.mock_diff.assert_called_once_with("myowner", "myrepo", 7)

    def test_calls_call_claude_with_diff(self):
        cr.review_pr("owner", "repo", 1, "https://run.url")
        args, kwargs = self.mock_claude.call_args
        assert self._diff in args[1]

    def test_posts_pr_comment(self):
        cr.review_pr("owner", "repo", 99, "https://run.url")
        self.mock_comment.assert_called_once()
        call_args = self.mock_comment.call_args[0]
        assert call_args[0] == "owner"
        assert call_args[1] == "repo"
        assert call_args[2] == 99

    def test_comment_contains_score(self):
        cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "42" in comment_text

    def test_comment_contains_recommendation(self):
        cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "BLOCK" in comment_text

    def test_comment_contains_findings(self):
        cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "src/auth.py" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        with patch.object(cr, "call_claude", return_value=json.dumps(MINIMAL_RESULT)):
            cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_contains_positive_observations(self):
        cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "CI pipeline is well configured" in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        data = {**FULL_RESULT, "positive_observations": []}
        with patch.object(cr, "call_claude", return_value=json.dumps(data)):
            cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_missing_score_in_result_shows_question_mark(self):
        data = {k: v for k, v in FULL_RESULT.items() if k != "score"}
        with patch.object(cr, "call_claude", return_value=json.dumps(data)):
            cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "?/100" in comment_text

    def test_finding_with_null_line_shows_na(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "HIGH", "category": "security",
             "file": "f.py", "line": None,
             "issue": "bad thing", "recommendation": "fix it"}
        ]}
        with patch.object(cr, "call_claude", return_value=json.dumps(data)):
            cr.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "n/a" in comment_text


# ===========================================================================
# review_repo
# ===========================================================================

class TestReviewRepo:
    SAMPLE_FILES = {
        "src/main.py": "print('hello')" * 100,
        "infra/main.tf": "resource aws_s3_bucket {}",
        "src/long.py": "x = 1\n" * 2000,   # will be truncated per-file
    }

    @pytest.fixture(autouse=True)
    def _patch(self):
        with patch.object(cr, "get_repo_files", return_value=self.SAMPLE_FILES) as mock_files, \
             patch.object(cr, "call_claude", return_value=json.dumps(MINIMAL_RESULT)) as mock_claude:
            self.mock_files = mock_files
            self.mock_claude = mock_claude
            yield

    def test_returns_parsed_result(self):
        result = cr.review_repo("owner", "repo", "https://run.url")
        assert result["score"] == 85

    def test_calls_get_repo_files_with_expected_extensions(self):
        cr.review_repo("owner", "repo", "https://run.url")
        _, kwargs_or_args = self.mock_files.call_args[0], self.mock_files.call_args
        exts = self.mock_files.call_args[0][2]
        assert ".py" in exts
        assert ".tf" in exts
        assert ".yaml" in exts
        assert ".yml" in exts

    def test