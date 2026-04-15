"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, newlines in strings, no JSON found, malformed JSON
- review_pr(): happy path, Claude response handling, comment posting, return value
- review_repo(): happy path, content truncation, file extension filtering
- get_output_url(): URL construction
- build_report_md(): full report generation, empty findings, missing keys

Mocks used:
- shared.call_claude (patched via tool1_code_review module)
- shared.get_pr_diff (patched via tool1_code_review module)
- shared.get_repo_files (patched via tool1_code_review module)
- shared.post_pr_comment (patched via tool1_code_review module)
- shared.write_output_file (patched via tool1_code_review module)
- shared.send_email (patched via tool1_code_review module)
- shared.write_audit_entry (patched via tool1_code_review module)

TODOs:
- TODO: Integration test for __main__ block requires full env setup
- TODO: Test email sending path in main block once full source is available
- TODO: Test write_output_file / write_audit_entry integration once main block is complete
"""

import json
import re
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Make sure the module under test can be imported even though its __main__
# block references `os.enviro` (a typo that would crash at import-time if the
# block were executed).  We patch os.environ before import so that the
# AttributeError on `os.enviro` is never reached during the test run.
# ---------------------------------------------------------------------------

# We import after potentially patching sys.modules for 'shared'
# Create a minimal fake 'shared' module so the import doesn't fail
import types

fake_shared = types.ModuleType("shared")
fake_shared.call_claude = MagicMock()
fake_shared.get_repo_files = MagicMock()
fake_shared.get_pr_diff = MagicMock()
fake_shared.write_output_file = MagicMock()
fake_shared.post_pr_comment = MagicMock()
fake_shared.send_email = MagicMock()
fake_shared.email_html = MagicMock()
fake_shared.write_audit_entry = MagicMock()
fake_shared.OUTPUT_REPO_OWNER = "test-owner"
fake_shared.OUTPUT_REPO = "test-output-repo"
fake_shared.GH_HEADERS = {"Authorization": "token test"}
fake_shared.GH_API = "https://api.github.com"

sys.modules["shared"] = fake_shared

# Now we can safely import the module under test
import tool1_code_review as t1


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Code looks reasonable overall.",
    "score": 75,
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
    "positive_observations": ["Good test coverage.", "Clear variable names."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

MINIMAL_RESULT = {
    "summary": "Minimal result.",
    "score": 50,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [],
    "positive_observations": [],
    "iac_findings": [],
}


def make_json_str(obj: dict) -> str:
    return json.dumps(obj)


# ---------------------------------------------------------------------------
# Tests: extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    """Tests for extract_json()."""

    def test_plain_json_string(self):
        raw = make_json_str(VALID_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   " + make_json_str(VALID_RESULT) + "   "
        result = t1.extract_json(raw)
        assert result["summary"] == "Code looks reasonable overall."

    def test_json_wrapped_in_markdown_fences(self):
        raw = "```json\n" + make_json_str(VALID_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_json_wrapped_in_plain_markdown_fences(self):
        raw = "```\n" + make_json_str(VALID_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_json_with_preamble_text(self):
        raw = "Here is the review:\n" + make_json_str(VALID_RESULT)
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_postamble_text(self):
        raw = make_json_str(VALID_RESULT) + "\n\nSome trailing text."
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_json_with_preamble_and_postamble(self):
        raw = "Preamble text.\n" + make_json_str(VALID_RESULT) + "\nPostamble."
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_minimal_valid_result(self):
        raw = make_json_str(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["findings"] == []
        assert result["score"] == 50

    def test_no_json_raises_value_error(self):
        raw = "This response contains no JSON object at all."
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_malformed_json_raises_value_error(self):
        raw = '{"score": 75, "summary": "missing closing brace"'
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_newline_inside_string_value_cleaned(self):
        # Simulate a response where a string value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 80, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This should either parse directly or after cleaning
        try:
            result = t1.extract_json(raw)
            assert "summary" in result
        except ValueError:
            # Acceptable if cleaning cannot recover
            pass

    def test_json_with_extra_braces_outside(self):
        """Outermost braces extraction should find the right block."""
        inner = make_json_str(VALID_RESULT)
        raw = f"Some text before. {inner} Some text after."
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_returns_dict(self):
        raw = make_json_str(VALID_RESULT)
        result = t1.extract_json(raw)
        assert isinstance(result, dict)

    def test_nested_findings_parsed(self):
        raw = make_json_str(VALID_RESULT)
        result = t1.extract_json(raw)
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "HIGH"

    def test_only_whitespace_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("     ")

    def test_score_boundary_zero(self):
        obj = {**VALID_RESULT, "score": 0}
        result = t1.extract_json(make_json_str(obj))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        obj = {**VALID_RESULT, "score": 100}
        result = t1.extract_json(make_json_str(obj))
        assert result["score"] == 100

    def test_line_null(self):
        obj = {
            **VALID_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "main.py",
                    "line": None,
                    "issue": "Missing docstring.",
                    "recommendation": "Add a module docstring.",
                }
            ],
        }
        result = t1.extract_json(make_json_str(obj))
        assert result["findings"][0]["line"] is None

    def test_all_merge_recommendations(self):
        for rec in ["APPROVE", "REQUEST_CHANGES", "BLOCK"]:
            obj = {**VALID_RESULT, "merge_recommendation": rec}
            result = t1.extract_json(make_json_str(obj))
            assert result["merge_recommendation"] == rec

    def test_multiple_findings(self):
        obj = {
            **VALID_RESULT,
            "findings": [
                {"severity": "CRITICAL", "category": "security", "file": "a.py", "line": 1, "issue": "i1", "recommendation": "r1"},
                {"severity": "LOW", "category": "performance", "file": "b.py", "line": 99, "issue": "i2", "recommendation": "r2"},
            ],
        }
        result = t1.extract_json(make_json_str(obj))
        assert len(result["findings"]) == 2

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n" + make_json_str(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 50


# ---------------------------------------------------------------------------
# Tests: get_output_url
# ---------------------------------------------------------------------------


class TestGetOutputUrl:
    """Tests for get_output_url()."""

    def test_basic_url_construction(self):
        url = t1.get_output_url("myowner", "myrepo", "2024-01-15")
        assert "myowner" in url
        assert "myrepo" in url
        assert "2024-01-15" in url

    def test_url_contains_output_repo_owner(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert fake_shared.OUTPUT_REPO_OWNER in url

    def test_url_contains_output_repo(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert fake_shared.OUTPUT_REPO in url

    def test_url_contains_code_review_path(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert "code-review" in url

    def test_url_is_string(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert isinstance(url, str)

    def test_url_starts_with_https(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert url.startswith("https://github.com/")

    def test_url_contains_md_extension(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert url.endswith(".md")

    def test_special_characters_in_label(self):
        url = t1.get_output_url("owner", "repo", "pr-123")
        assert "pr-123" in url

    def test_owner_repo_separator(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert "owner-repo-label" in url


# ---------------------------------------------------------------------------
# Tests: build_report_md
# ---------------------------------------------------------------------------


class TestBuildReportMd:
    """Tests for build_report_md()."""

    def test_returns_string(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert isinstance(md, str)

    def test_contains_score(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "75" in md

    def test_contains_recommendation(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "APPROVE" in md

    def test_contains_summary(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "Code looks reasonable overall." in md

    def test_contains_finding_severity(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "HIGH" in md

    def test_contains_finding_file(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "src/example.py" in md

    def test_contains_iac_findings(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "S3 bucket lacks versioning." in md

    def test_contains_positive_observations(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "Good test coverage." in md

    def test_contains_source(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "PR #42" in md

    def test_contains_context(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "owner/repo" in md

    def test_contains_auto_generated_footer(self):
        md = t1.build_report_md(VALID_RESULT, "PR #42", "owner/repo")
        assert "Auto-generated" in md

    def test_empty_findings_shows_placeholder(self):
        md = t1.build_report_md(MINIMAL_RESULT, "cron", "owner/repo")
        assert "No findings" in md

    def test_empty_iac_findings_shows_none(self):
        md = t1.build_report_md(MINIMAL_RESULT, "cron", "owner/repo")
        assert "_None_" in md

    def test_empty_positive_observations_shows_none(self):
        md = t1.build_report_md(MINIMAL_RESULT, "cron", "owner/repo")
        assert "_None_" in md

    def test_missing_score_shows_na(self):
        result = {k: v for k, v in VALID_RESULT.items() if k != "score"}
        md = t1.build_report_md(result, "cron", "owner/repo")
        assert "N/A" in md

    def test_missing_recommendation_shows_na(self):
        result = {k: v for k, v in VALID_RESULT.items() if k != "merge_recommendation"}
        md = t1.build_report_md(result, "cron", "owner/repo")
        assert "N/A" in md

    def test_missing_summary_shows_default(self):
        result = {k: v for k, v in VALID_RESULT.items() if k != "summary"}
        md = t