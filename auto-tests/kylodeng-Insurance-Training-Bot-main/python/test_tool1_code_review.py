"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, embedded newlines, missing JSON, malformed JSON,
  outermost-braces extraction, boundary/edge cases
- review_pr(): happy path, Claude response handling, comment formatting, return value
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): happy path, empty findings, empty iac/positive observations,
  missing keys, score/recommendation rendering

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- datetime.datetime (for deterministic timestamp in build_report_md)

TODOs:
- TODO: Integration test for __main__ block requires full env setup
- TODO: Test review_pr with real GitHub API responses when VCR cassettes available
- TODO: Test email notification path once __main__ block source is complete
"""

import json
import re
import sys
import os
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# We need to make "shared" importable before importing the module under test.
# ---------------------------------------------------------------------------
import types

# Build a minimal fake 'shared' module so the import at the top of
# tool1_code_review.py doesn't blow up in a test environment where
# shared.py may depend on secrets / network.
_shared_mod = types.ModuleType("shared")
_shared_mod.call_claude = MagicMock()
_shared_mod.get_repo_files = MagicMock()
_shared_mod.get_pr_diff = MagicMock()
_shared_mod.write_output_file = MagicMock()
_shared_mod.post_pr_comment = MagicMock()
_shared_mod.send_email = MagicMock()
_shared_mod.email_html = MagicMock()
_shared_mod.write_audit_entry = MagicMock()
_shared_mod.OUTPUT_REPO_OWNER = "test-owner"
_shared_mod.OUTPUT_REPO = "test-output-repo"
_shared_mod.GH_HEADERS = {"Authorization": "token fake"}
_shared_mod.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_mod)

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

import tool1_code_review as cr  # noqa: E402


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
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key.",
            "recommendation": "Use environment variables or a secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket has no encryption.",
            "recommendation": "Enable SSE-S3 or SSE-KMS.",
        },
    ],
    "positive_observations": ["Consistent naming conventions", "Good docstrings"],
    "iac_findings": ["Missing resource tags on EC2 instances"],
}


@pytest.fixture()
def minimal_result():
    return dict(MINIMAL_RESULT)


@pytest.fixture()
def full_result():
    import copy
    return copy.deepcopy(FULL_RESULT)


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    """Tests for extract_json()."""

    def test_plain_json(self, minimal_result):
        raw = json.dumps(minimal_result)
        assert cr.extract_json(raw) == minimal_result

    def test_json_with_leading_trailing_whitespace(self, minimal_result):
        raw = "   \n" + json.dumps(minimal_result) + "\n  "
        assert cr.extract_json(raw) == minimal_result

    def test_markdown_fences_triple_backtick(self, minimal_result):
        raw = "```json\n" + json.dumps(minimal_result) + "\n```"
        assert cr.extract_json(raw) == minimal_result

    def test_markdown_fences_no_lang(self, minimal_result):
        raw = "```\n" + json.dumps(minimal_result) + "\n```"
        assert cr.extract_json(raw) == minimal_result

    def test_json_embedded_in_prose(self, minimal_result):
        """JSON object surrounded by prose text."""
        raw = "Here is my review:\n" + json.dumps(minimal_result) + "\nEnd of review."
        assert cr.extract_json(raw) == minimal_result

    def test_full_result_round_trip(self, full_result):
        raw = json.dumps(full_result)
        assert cr.extract_json(raw) == full_result

    def test_literal_newline_inside_string_value(self):
        """Newline inside a JSON string value should be cleaned and parsed."""
        # Simulate Claude embedding a literal newline inside a string value
        raw = '{"summary": "first line\nsecond line", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        # After cleaning the newline becomes a space
        assert "summary" in result
        assert result["score"] == 70

    def test_raises_when_no_json_at_all(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON.")

    def test_raises_when_braces_present_but_invalid_json(self):
        raw = "{ totally: not valid json }"
        with pytest.raises((ValueError, json.JSONDecodeError)):
            cr.extract_json(raw)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_opening_brace(self):
        with pytest.raises(ValueError):
            cr.extract_json("{")

    def test_only_closing_brace(self):
        with pytest.raises(ValueError):
            cr.extract_json("}")

    def test_nested_objects_parsed_correctly(self):
        data = {
            "summary": "ok",
            "score": 90,
            "merge_recommendation": "APPROVE",
            "findings": [
                {"severity": "LOW", "category": "maintainability",
                 "file": "foo.py", "line": 1,
                 "issue": "missing docstring", "recommendation": "add one"}
            ],
            "positive_observations": [],
            "iac_findings": [],
        }
        raw = json.dumps(data)
        assert cr.extract_json(raw) == data

    def test_score_zero_boundary(self):
        data = {"summary": "terrible", "score": 0,
                "merge_recommendation": "BLOCK",
                "findings": [], "positive_observations": [],
                "iac_findings": []}
        assert cr.extract_json(json.dumps(data))["score"] == 0

    def test_score_100_boundary(self):
        data = {"summary": "perfect", "score": 100,
                "merge_recommendation": "APPROVE",
                "findings": [], "positive_observations": [],
                "iac_findings": []}
        assert cr.extract_json(json.dumps(data))["score"] == 100

    def test_markdown_fence_with_extra_whitespace(self, minimal_result):
        raw = "  ```json\n" + json.dumps(minimal_result) + "\n```  "
        # The function checks startswith("```") after strip()
        result = cr.extract_json(raw)
        assert result == minimal_result

    def test_unicode_values(self):
        data = {"summary": "审查完成", "score": 55,
                "merge_recommendation": "REQUEST_CHANGES",
                "findings": [], "positive_observations": [],
                "iac_findings": []}
        assert cr.extract_json(json.dumps(data))["summary"] == "审查完成"

    def test_multiple_json_objects_returns_outermost(self):
        """When there are multiple { } blocks, outermost wins."""
        data = {"summary": "ok", "score": 80,
                "merge_recommendation": "APPROVE",
                "findings": [], "positive_observations": [],
                "iac_findings": []}
        # Wrap in text that has extra braces (like a code example)
        raw = "Some text {bad} then " + json.dumps(data)
        result = cr.extract_json(raw)
        assert result["score"] == 80


# ---------------------------------------------------------------------------
# get_output_url
# ---------------------------------------------------------------------------


class TestGetOutputUrl:
    def test_basic_url(self):
        url = cr.get_output_url("myowner", "myrepo", "pr-42")
        assert url == (
            f"https://github.com/{_shared_mod.OUTPUT_REPO_OWNER}"
            f"/{_shared_mod.OUTPUT_REPO}/blob/main/code-review/myowner-myrepo-pr-42.md"
        )

    def test_url_contains_owner_and_repo(self):
        url = cr.get_output_url("acme", "payments-service", "2024-01-01")
        assert "acme-payments-service-2024-01-01.md" in url

    def test_url_starts_with_github(self):
        url = cr.get_output_url("x", "y", "z")
        assert url.startswith("https://github.com/")

    def test_url_ends_with_md(self):
        url = cr.get_output_url("a", "b", "weekly")
        assert url.endswith(".md")


# ---------------------------------------------------------------------------
# build_report_md
# ---------------------------------------------------------------------------


class TestBuildReportMd:
    FIXED_NOW = "2024-06-15 12:00 UTC"

    @pytest.fixture(autouse=True)
    def patch_datetime(self):
        """Pin datetime so report timestamps are deterministic."""
        fixed_dt = datetime.datetime(2024, 6, 15, 12, 0, 0)
        with patch("tool1_code_review.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = fixed_dt
            mock_dt.datetime.utcnow.return_value.strftime = fixed_dt.strftime
            yield mock_dt

    def test_contains_score(self, full_result):
        md = cr.build_report_md(full_result, "PR #7", "owner/repo")
        assert "45/100" in md

    def test_contains_recommendation(self, full_result):
        md = cr.build_report_md(full_result, "PR #7", "owner/repo")
        assert "BLOCK" in md

    def test_contains_summary(self, full_result):
        md = cr.build_report_md(full_result, "PR #7", "owner/repo")
        assert "Several security issues found." in md

    def test_contains_finding_severity(self, full_result):
        md = cr.build_report_md(full_result, "PR #7", "owner/repo")
        assert "CRITICAL" in md

    def test_contains_iac_finding(self, full_result):
        md = cr.build_report_md(full_result, "PR #7", "owner/repo")
        assert "Missing resource tags on EC2 instances" in md

    def test_contains_positive_observation(self, full_result):
        md = cr.build_report_md(full_result, "PR #7", "owner/repo")
        assert "Consistent naming conventions" in md

    def test_source_and_context_present(self, full_result):
        md = cr.build_report_md(full_result, "weekly-cron", "acme/backend")
        assert "weekly-cron" in md
        assert "acme/backend" in md

    def test_empty_findings_shows_placeholder(self, minimal_result):
        md = cr.build_report_md(minimal_result, "PR #1", "x/y")
        assert "No findings" in md

    def test_empty_iac_findings_shows_none(self, minimal_result):
        md = cr.build_report_md(minimal_result, "PR #1", "x/y")
        assert "_None_" in md

    def test_empty_positive_observations_shows_none(self):
        result = {**MINIMAL_RESULT, "positive_observations": [], "iac_findings": []}
        md = cr.build_report_md(result, "PR #2", "x/y")
        assert "_None_" in md

    def test_missing_score_key_shows_na(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        md = cr.build_report_md(result, "manual", "x/y")
        assert "N/A" in md

    def test_missing_recommendation_key(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "merge_recommendation"}
        md = cr.build_report_md(result, "manual", "x/y")
        assert "N/A" in md

    def test_missing_summary_key(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "summary"}
        md = cr.build_report_md(result, "manual", "x/y")
        assert "No summary provided." in md

    def test_report_is_string(self, full_result):
        md = cr.build_report_md(full_result, "src", "ctx")
        assert isinstance(md, str)

    def test_report_starts_with_heading(self, minimal_result):
        md = cr.build_report_md(minimal_result, "src", "ctx")
        assert md.startswith("# Code Review Report")

    def test_multiple_findings_all_present(self, full_result):
        md = cr.build_report_md(full_result, "PR #7", "owner/repo")
        assert "Hardcoded AWS secret key." in md
        assert "S3 bucket has no encryption." in md

    def test_finding_with_null_line(self, full_result):
        """Line=None should render without error."""
        md = cr.build_report_md(full_result, "PR", "r")
        assert "None" in md or "?" in md  # either rendering is acceptable

    def test_auto_generated_footer(self, minimal_result):
        md = cr.build_report_md(minimal_result, "s", "c")
        assert "Auto-generated by AI Delivery Bot" in md

    def test_