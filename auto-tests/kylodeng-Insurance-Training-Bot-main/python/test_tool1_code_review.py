"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, whitespace-only input
- review_pr(): happy path, Claude response handling, comment formatting, error propagation
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction with various owner/repo/label combinations
- build_report_md(): full report generation, empty findings, empty iac/positive lists,
  missing keys in result dict

Mocks used:
- shared.call_claude (patched as tool1_code_review.call_claude)
- shared.get_repo_files (patched as tool1_code_review.get_repo_files)
- shared.get_pr_diff (patched as tool1_code_review.get_pr_diff)
- shared.write_output_file (patched as tool1_code_review.write_output_file)
- shared.post_pr_comment (patched as tool1_code_review.post_pr_comment)
- shared.send_email (patched as tool1_code_review.send_email)
- shared.write_audit_entry (patched as tool1_code_review.write_audit_entry)
- datetime.datetime (patched for deterministic timestamps)

TODOs:
- TODO: Integration test for __main__ block requires full env setup
- TODO: Test email dispatch path once shared.send_email signature is confirmed
- TODO: Test write_output_file call args once output path logic is confirmed
"""

import json
import sys
import os
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Module import — the source file does sys.path.insert at import time and
# depends on `shared` which may not exist in the test environment.
# We stub `shared` before importing the module under test.
# ---------------------------------------------------------------------------

SHARED_ATTRS = {
    "call_claude": MagicMock(return_value="{}"),
    "get_repo_files": MagicMock(return_value={}),
    "get_pr_diff": MagicMock(return_value=""),
    "write_output_file": MagicMock(return_value=None),
    "post_pr_comment": MagicMock(return_value=None),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html/>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
    "GH_HEADERS": {"Authorization": "Bearer test-token"},
    "GH_API": "https://api.github.com",
}

shared_stub = MagicMock()
for attr, val in SHARED_ATTRS.items():
    setattr(shared_stub, attr, val)

sys.modules.setdefault("shared", shared_stub)
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test
import importlib.util, types

_module_path = os.path.join(
    os.path.dirname(__file__), "..", ".github", "scripts", "tool1_code_review.py"
)

# Try relative path first, fall back to direct import
try:
    spec = importlib.util.spec_from_file_location("tool1_code_review", _module_path)
    tool1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool1)
except (FileNotFoundError, AttributeError):
    # If running from a different working dir, try to import directly
    try:
        import tool1_code_review as tool1  # type: ignore
    except ModuleNotFoundError:
        # Create a minimal stub so tests can at least be collected
        tool1 = types.ModuleType("tool1_code_review")
        tool1.extract_json = None  # will be skipped
        tool1.review_pr = None
        tool1.review_repo = None
        tool1.get_output_url = None
        tool1.build_report_md = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall looks good.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage.", "Clear naming conventions."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def _require(attr):
    """Skip test if module attribute is missing (stub scenario)."""
    obj = getattr(tool1, attr, None)
    if obj is None:
        pytest.skip(f"tool1_code_review.{attr} not available — module could not be loaded")
    return obj


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:

    def setup_method(self):
        self.fn = _require("extract_json")

    # --- Happy path ---

    def test_plain_valid_json(self):
        result = self.fn(VALID_JSON_STR)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_leading_trailing_whitespace(self):
        result = self.fn(f"   \n{VALID_JSON_STR}\n   ")
        assert result["summary"] == "Overall looks good."

    def test_markdown_fence_triple_backtick(self):
        wrapped = f"```json\n{VALID_JSON_STR}\n```"
        result = self.fn(wrapped)
        assert result["score"] == 82

    def test_markdown_fence_no_language_tag(self):
        wrapped = f"```\n{VALID_JSON_STR}\n```"
        result = self.fn(wrapped)
        assert result["findings"][0]["severity"] == "HIGH"

    def test_json_embedded_in_prose(self):
        text = f"Here is the review:\n{VALID_JSON_STR}\nEnd of review."
        result = self.fn(text)
        assert result["score"] == 82

    def test_minimal_json(self):
        minimal = '{"score": 50, "merge_recommendation": "APPROVE", "summary": "ok"}'
        result = self.fn(minimal)
        assert result["score"] == 50

    def test_empty_findings_list(self):
        data = {**VALID_RESULT, "findings": []}
        result = self.fn(json.dumps(data))
        assert result["findings"] == []

    # --- Newline cleanup ---

    def test_newlines_inside_string_values_are_cleaned(self):
        # Simulate a response with a literal \n inside a string value
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": []}'
        # This may or may not be valid JSON depending on strict mode;
        # extract_json should handle it
        result = self.fn(raw)
        assert "score" in result

    # --- Error conditions ---

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            self.fn("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            self.fn("   \n\t  ")

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            self.fn("This response contains no JSON at all.")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            self.fn('{"score": 75, "merge_recommendation": APPROVE}')  # unquoted value

    def test_unclosed_brace_raises_value_error(self):
        with pytest.raises(ValueError):
            self.fn('{"score": 75, "merge_recommendation": "APPROVE"')

    def test_array_only_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            self.fn('["not", "an", "object"]')

    # --- Boundary / edge cases ---

    def test_score_zero(self):
        data = {**VALID_RESULT, "score": 0}
        result = self.fn(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = {**VALID_RESULT, "score": 100}
        result = self.fn(json.dumps(data))
        assert result["score"] == 100

    def test_finding_with_null_line(self):
        data = {
            **VALID_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "app.py",
                    "line": None,
                    "issue": "Missing docstring.",
                    "recommendation": "Add module docstring.",
                }
            ],
        }
        result = self.fn(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_all_severity_levels(self):
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            data = {
                **VALID_RESULT,
                "findings": [
                    {**VALID_RESULT["findings"][0], "severity": sev}
                ],
            }
            result = self.fn(json.dumps(data))
            assert result["findings"][0]["severity"] == sev

    def test_all_merge_recommendations(self):
        for rec in ("APPROVE", "REQUEST_CHANGES", "BLOCK"):
            data = {**VALID_RESULT, "merge_recommendation": rec}
            result = self.fn(json.dumps(data))
            assert result["merge_recommendation"] == rec

    def test_multiple_findings(self):
        findings = [
            {
                "severity": "CRITICAL",
                "category": "security",
                "file": "infra/main.tf",
                "line": 10,
                "issue": "IAM policy uses wildcard.",
                "recommendation": "Restrict to least privilege.",
            },
            {
                "severity": "LOW",
                "category": "maintainability",
                "file": "app/utils.py",
                "line": None,
                "issue": "Unused import.",
                "recommendation": "Remove unused imports.",
            },
        ]
        data = {**VALID_RESULT, "findings": findings}
        result = self.fn(json.dumps(data))
        assert len(result["findings"]) == 2

    def test_extra_text_before_json(self):
        text = "Certainly! Here is the JSON:\n" + VALID_JSON_STR
        result = self.fn(text)
        assert result["score"] == 82

    def test_extra_text_after_json(self):
        text = VALID_JSON_STR + "\n\nPlease let me know if you need anything else."
        result = self.fn(text)
        assert result["score"] == 82

    def test_deeply_nested_prose_around_json(self):
        text = (
            "I have reviewed the code carefully.\n\n"
            + VALID_JSON_STR
            + "\n\nHope that helps!"
        )
        result = self.fn(text)
        assert result["merge_recommendation"] == "APPROVE"

    def test_unicode_in_values(self):
        data = {**VALID_RESULT, "summary": "Code review complete — 100% pass."}
        result = self.fn(json.dumps(data))
        assert "100%" in result["summary"]


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:

    def setup_method(self):
        self.fn = _require("review_pr")

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_happy_path_returns_result(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff --git a/app.py b/app.py\n+password = 'secret'"
        mock_claude.return_value = VALID_JSON_STR
        result = self.fn("my-org", "my-repo", 42, "https://ci.example.com/run/1")
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_post_pr_comment_called_once(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "some diff"
        mock_claude.return_value = VALID_JSON_STR
        self.fn("owner", "repo", 1, "http://run")
        mock_comment.assert_called_once()

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_comment_contains_score(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = VALID_JSON_STR
        self.fn("owner", "repo", 7, "http://run")
        comment_body = mock_comment.call_args[0][3]
        assert "82" in comment_body

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_comment_contains_recommendation(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = VALID_JSON_STR
        self.fn("owner", "repo", 7, "http://run")
        comment_body = mock_comment.call_args[0][3]
        assert "APPROVE" in comment_body

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_comment_contains_summary(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = VALID_JSON_STR
        self.fn("owner", "repo", 7, "http://run")
        comment_body = mock_comment.call_args[0][3]
        assert "Overall looks good" in comment_body

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_no_findings_shows_no_findings(self, mock_diff, mock_claude, mock_comment):
        data = {**VALID_RESULT, "findings": []}
        mock_diff.return_value = "diff"
        mock_claude.return_value = json.dumps(data)
        self.fn("owner", "repo", 7, "http://run")
        comment_body = mock_comment.call_args[0][3]
        