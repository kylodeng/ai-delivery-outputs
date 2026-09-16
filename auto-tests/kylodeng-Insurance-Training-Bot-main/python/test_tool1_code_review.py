"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, outermost-block extraction,
  newline-inside-string cleaning, missing JSON, unparseable JSON
- review_pr(): happy path comment construction, Claude/diff integration
- review_repo(): happy path, token-budget truncation
- get_output_url(): URL construction
- build_report_md(): full report, empty findings, empty iac/positive lists,
  missing keys, boundary score values

Mocks used:
- shared.call_claude          (patched via unittest.mock.patch)
- shared.get_pr_diff          (patched)
- shared.get_repo_files       (patched)
- shared.post_pr_comment      (patched)
- shared.write_output_file    (patched)
- shared.send_email           (patched)
- shared.write_audit_entry    (patched)
- requests                    (not called directly by tested functions; imported
                               by the module but not exercised here)

TODOs:
- TODO: Integration test for __main__ block requires full env-var + subprocess setup
- TODO: Test the cleaned-newline regex branch with a real-world malformed payload
  that survives the { } extraction but fails the first json.loads
"""

import json
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Minimal stub for `shared` so the import in tool1_code_review doesn't fail
# even when the real module is absent from the test environment.
# ---------------------------------------------------------------------------
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
_shared_stub.GH_HEADERS = {}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test
scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

import importlib
import tool1_code_review as cr  # noqa: E402  (import after path manipulation)


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
    "summary": "Several issues found.",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils/helpers.py",
            "line": None,
            "issue": "Function is too long.",
            "recommendation": "Break it into smaller functions.",
        },
    ],
    "positive_observations": ["Well-structured modules", "Good naming conventions"],
    "iac_findings": ["S3 bucket lacks encryption", "IAM role too permissive"],
}


def _json_str(obj: dict) -> str:
    return json.dumps(obj)


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_json_string(self):
        raw = _json_str(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + _json_str(MINIMAL_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_triple_backtick_fences(self):
        raw = "```json\n" + _json_str(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fences_no_language_label(self):
        raw = "```\n" + _json_str(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_text_before_and_after_json(self):
        raw = "Sure! Here is the review:\n" + _json_str(FULL_RESULT) + "\nDone."
        result = cr.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2

    def test_only_outermost_braces_extracted(self):
        # Extra text wrapping the JSON
        inner = _json_str(MINIMAL_RESULT)
        raw = f"prefix text {inner} suffix text"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_newline_inside_string_values_cleaned(self):
        # Simulate a response where a string value contains a literal newline
        malformed = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This will fail direct parse; extract_json should clean and succeed
        result = cr.extract_json(malformed)
        # After cleaning the newline becomes a space
        assert "line one" in result["summary"]

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This response has no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_malformed_json_inside_braces_raises_value_error(self):
        raw = "{ not valid json !!! }"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_full_result_roundtrip(self):
        raw = _json_str(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][1] == "IAM role too permissive"

    def test_score_zero_boundary(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(_json_str(data))
        assert result["score"] == 0

    def test_score_100_boundary(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(_json_str(data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {
            **MINIMAL_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "foo.py",
                    "line": None,
                    "issue": "Some issue",
                    "recommendation": "Fix it",
                }
            ],
        }
        result = cr.extract_json(_json_str(data))
        assert result["findings"][0]["line"] is None

    def test_nested_markdown_fence_content_stripped(self):
        """Ensure content after fence stripping is valid JSON."""
        payload = _json_str(MINIMAL_RESULT)
        raw = f"```json\n{payload}\n```\n"
        result = cr.extract_json(raw)
        assert isinstance(result, dict)

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_all_merge_recommendations_parsed(self, recommendation):
        data = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = cr.extract_json(_json_str(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_all_severities_parsed(self, severity):
        data = {
            **MINIMAL_RESULT,
            "findings": [
                {
                    "severity": severity,
                    "category": "security",
                    "file": "a.py",
                    "line": 1,
                    "issue": "Issue.",
                    "recommendation": "Fix.",
                }
            ],
        }
        result = cr.extract_json(_json_str(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# get_output_url
# ===========================================================================

class TestGetOutputUrl:

    def test_basic_url_structure(self):
        url = cr.get_output_url("myowner", "myrepo", "pr-42")
        assert url.startswith("https://github.com/")
        assert "myowner-myrepo-pr-42.md" in url

    def test_uses_output_repo_constants(self):
        url = cr.get_output_url("owner", "repo", "label")
        assert _shared_stub.OUTPUT_REPO_OWNER in url
        assert _shared_stub.OUTPUT_REPO in url

    def test_url_contains_code_review_path(self):
        url = cr.get_output_url("o", "r", "l")
        assert "code-review" in url

    def test_special_chars_in_label(self):
        url = cr.get_output_url("o", "r", "2024-01-01")
        assert "2024-01-01" in url

    @pytest.mark.parametrize("owner,repo,label,expected_fragment", [
        ("acme", "frontend", "pr-1", "acme-frontend-pr-1.md"),
        ("org", "backend-api", "weekly", "org-backend-api-weekly.md"),
    ])
    def test_parametrized_url_fragments(self, owner, repo, label, expected_fragment):
        url = cr.get_output_url(owner, repo, label)
        assert expected_fragment in url


# ===========================================================================
# build_report_md
# ===========================================================================

class TestBuildReportMd:

    def test_basic_structure_present(self):
        md = cr.build_report_md(MINIMAL_RESULT, "PR #1", "owner/repo")
        assert "# Code Review Report" in md
        assert "## Summary" in md
        assert "## Findings" in md
        assert "## IaC Findings" in md
        assert "## Positive Observations" in md

    def test_score_and_recommendation_in_header(self):
        md = cr.build_report_md(MINIMAL_RESULT, "PR #1", "owner/repo")
        assert "85/100" in md
        assert "APPROVE" in md

    def test_summary_text_appears(self):
        md = cr.build_report_md(MINIMAL_RESULT, "PR #1", "owner/repo")
        assert "Looks good overall." in md

    def test_full_result_findings_table(self):
        md = cr.build_report_md(FULL_RESULT, "cron", "owner/repo")
        assert "Hardcoded password detected." in md
        assert "src/app.py" in md
        assert "HIGH" in md
        assert "LOW" in md

    def test_iac_findings_listed(self):
        md = cr.build_report_md(FULL_RESULT, "cron", "owner/repo")
        assert "S3 bucket lacks encryption" in md
        assert "IAM role too permissive" in md

    def test_positive_observations_listed(self):
        md = cr.build_report_md(FULL_RESULT, "cron", "owner/repo")
        assert "Well-structured modules" in md
        assert "Good naming conventions" in md

    def test_empty_findings_shows_no_findings_placeholder(self):
        md = cr.build_report_md(MINIMAL_RESULT, "src", "ctx")
        assert "No findings" in md

    def test_empty_iac_shows_none(self):
        md = cr.build_report_md(MINIMAL_RESULT, "src", "ctx")
        assert "_None_" in md

    def test_source_and_context_appear(self):
        md = cr.build_report_md(MINIMAL_RESULT, "weekly-cron", "myorg/myrepo")
        assert "weekly-cron" in md
        assert "myorg/myrepo" in md

    def test_generated_timestamp_format(self):
        md = cr.build_report_md(MINIMAL_RESULT, "src", "ctx")
        # Should contain a date string like "2024-01-15 12:00 UTC"
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", md)

    def test_missing_score_key_shows_na(self):
        data = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        md = cr.build_report_md(data, "src", "ctx")
        assert "N/A" in md

    def test_missing_recommendation_key_shows_na(self):
        data = {k: v for k, v in MINIMAL_RESULT.items() if k != "merge_recommendation"}
        md = cr.build_report_md(data, "src", "ctx")
        assert "N/A" in md

    def test_missing_summary_key_shows_default(self):
        data = {k: v for k, v in MINIMAL_RESULT.items() if k != "summary"}
        md = cr.build_report_md(data, "src", "ctx")
        assert "No summary provided." in md

    def test_footer_present(self):
        md = cr.build_report_md(MINIMAL_RESULT, "src", "ctx")
        assert "Auto-generated by AI Delivery Bot" in md

    def test_score_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        md = cr.build_report_md(data, "src", "ctx")
        assert "0/100" in md

    def test_score_100(self):
        data = {**MINIMAL_RESULT, "score": 100}
        md = cr.build_report_md(data, "src", "ctx")
        assert "100/100" in md

    def test_block_recommendation(self):
        data = {**MINIMAL_RESULT, "merge_recommendation": "BLOCK"}
        md = cr.build_report_md(data, "src", "ctx")
        assert "BLOCK" in md

    def test_finding_with_null_line_renders(self):
        data = {
            **MINIMAL_RESULT,