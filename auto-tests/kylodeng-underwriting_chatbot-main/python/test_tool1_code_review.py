"""
Test module for tool1_code_review.py

What is tested:
- extract_json: happy path (clean JSON), markdown-fenced JSON, JSON embedded in prose,
  newlines inside string values, no JSON found, unparseable JSON, edge cases.
- review_pr: happy path, Claude returning bad JSON, post_pr_comment called correctly.
- review_repo: happy path, content truncation to 20000 chars.
- get_output_url: URL construction.
- build_report_md: full report generation, empty findings, empty iac/positive fields.

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)

TODOs:
- TODO: Integration test for __main__ block requires full env setup (REVIEW_MODE, GH_TOKEN, etc.)
- TODO: Test post_pr_comment network failure handling once retry logic is added to review_pr.
- TODO: Test write_output_file and write_audit_entry integration once output repo structure is known.
"""

import json
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Patch the shared module imports BEFORE importing the module under test so
# that the module-level "from shared import …" doesn't fail in CI.
# ---------------------------------------------------------------------------
import sys
import types

# Build a minimal fake 'shared' module so the import succeeds without the
# real dependency being present.
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

# Also stub 'requests' if it is not installed in the test environment.
if "requests" not in sys.modules:
    sys.modules["requests"] = types.ModuleType("requests")

import tool1_code_review as mod  # noqa: E402  (import after sys.modules patch)


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks reasonable.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/auth.py",
            "line": 17,
            "issue": "Hardcoded password detected",
            "recommendation": "Use environment variables instead",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "Overly permissive IAM policy",
            "recommendation": "Apply least-privilege principle",
        },
    ],
    "positive_observations": ["Good docstrings", "Type hints used throughout"],
    "iac_findings": ["S3 bucket lacks encryption", "Missing resource tags"],
}


def _json_str(d: dict) -> str:
    return json.dumps(d)


# ===========================================================================
# extract_json tests
# ===========================================================================


class TestExtractJson:
    """Tests for extract_json()."""

    # --- happy paths --------------------------------------------------------

    def test_clean_json_string(self):
        raw = _json_str(MINIMAL_RESULT)
        result = mod.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = f"   \n\n{_json_str(MINIMAL_RESULT)}\n   "
        result = mod.extract_json(raw)
        assert result["summary"] == "Code looks reasonable."

    def test_markdown_fenced_json_triple_backtick(self):
        raw = f"```\n{_json_str(MINIMAL_RESULT)}\n```"
        result = mod.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fenced_json_with_language_tag(self):
        raw = f"```json\n{_json_str(MINIMAL_RESULT)}\n```"
        result = mod.extract_json(raw)
        assert result["score"] == 80

    def test_json_embedded_in_prose(self):
        raw = f"Here is my analysis:\n{_json_str(FULL_RESULT)}\nEnd of response."
        result = mod.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2

    def test_full_result_round_trip(self):
        raw = _json_str(FULL_RESULT)
        result = mod.extract_json(raw)
        assert result == FULL_RESULT

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate Claude including a literal newline inside a JSON string value.
        dirty = '{"summary": "line one\nline two", "score": 50}'
        result = mod.extract_json(dirty)
        # After cleaning the newline becomes a space; score still parsed.
        assert result["score"] == 50
        assert "\n" not in result["summary"]

    def test_findings_with_null_line(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "Missing docstring", "recommendation": "Add one"}
        ]}
        result = mod.extract_json(_json_str(data))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = mod.extract_json(_json_str(data))
        assert result["findings"] == []

    # --- edge cases ---------------------------------------------------------

    def test_extra_text_before_and_after_braces(self):
        inner = _json_str(MINIMAL_RESULT)
        raw = f"Some preamble text {inner} some trailing text"
        result = mod.extract_json(raw)
        assert result["score"] == 80

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = mod.extract_json(_json_str(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = mod.extract_json(_json_str(data))
        assert result["score"] == 100

    def test_multiple_findings(self):
        result = mod.extract_json(_json_str(FULL_RESULT))
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    # --- error conditions ---------------------------------------------------

    def test_no_json_at_all_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            mod.extract_json("This is plain text with no JSON whatsoever.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            mod.extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            mod.extract_json("   \n\t  ")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse Claude response as JSON"):
            mod.extract_json('{"score": 50, "summary": "broken" GARBAGE}')

    def test_incomplete_json_raises_value_error(self):
        with pytest.raises(ValueError):
            mod.extract_json('{"score": 50, "summary":')

    def test_array_only_no_object_raises_value_error(self):
        # A JSON array at the top level with no enclosing object.
        with pytest.raises(ValueError):
            mod.extract_json('["item1", "item2"]')

    def test_markdown_fence_with_garbage_content_raises(self):
        with pytest.raises(ValueError):
            mod.extract_json("```\nnot valid json at all\n```")

    # --- negative / boundary ------------------------------------------------

    def test_nested_json_objects_in_findings(self):
        """Findings contain dicts — ensure they survive round-trip."""
        raw = _json_str(FULL_RESULT)
        result = mod.extract_json(raw)
        assert isinstance(result["findings"][1], dict)
        assert result["findings"][1]["file"] == "infra/main.tf"

    def test_unicode_values_survive(self):
        data = {**MINIMAL_RESULT, "summary": "Résumé avec accents: àéîõü"}
        result = mod.extract_json(_json_str(data))
        assert "Résumé" in result["summary"]

    @pytest.mark.parametrize("recommendation", [
        "APPROVE", "REQUEST_CHANGES", "BLOCK"
    ])
    def test_all_merge_recommendations_parsed(self, recommendation):
        data = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = mod.extract_json(_json_str(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_all_severities_parsed(self, severity):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": severity, "category": "security",
             "file": "x.py", "line": 1,
             "issue": "issue", "recommendation": "fix"}
        ]}
        result = mod.extract_json(_json_str(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# get_output_url tests
# ===========================================================================


class TestGetOutputUrl:
    """Tests for get_output_url()."""

    def test_basic_url_construction(self):
        url = mod.get_output_url("myorg", "myrepo", "pr-42")
        assert url == (
            "https://github.com/test-owner/test-output-repo"
            "/blob/main/code-review/myorg-myrepo-pr-42.md"
        )

    def test_url_contains_owner_and_repo(self):
        url = mod.get_output_url("acme", "backend", "weekly")
        assert "acme-backend-weekly" in url

    def test_url_starts_with_https(self):
        url = mod.get_output_url("a", "b", "c")
        assert url.startswith("https://")

    def test_url_contains_output_owner(self):
        url = mod.get_output_url("x", "y", "z")
        assert "test-owner" in url
        assert "test-output-repo" in url

    def test_url_label_special_chars(self):
        """Labels with hyphens should survive."""
        url = mod.get_output_url("org", "repo", "pr-999-hotfix")
        assert "pr-999-hotfix" in url

    @pytest.mark.parametrize("owner,repo,label,expected_fragment", [
        ("org1", "repo1", "pr-1", "org1-repo1-pr-1"),
        ("myorg", "my-service", "2024-01-01", "myorg-my-service-2024-01-01"),
    ])
    def test_url_parametrized(self, owner, repo, label, expected_fragment):
        url = mod.get_output_url(owner, repo, label)
        assert expected_fragment in url


# ===========================================================================
# build_report_md tests
# ===========================================================================


class TestBuildReportMd:
    """Tests for build_report_md()."""

    def test_contains_score(self):
        md = mod.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "80/100" in md

    def test_contains_recommendation(self):
        md = mod.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "APPROVE" in md

    def test_contains_summary(self):
        md = mod.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "Code looks reasonable." in md

    def test_contains_source_and_context(self):
        md = mod.build_report_md(MINIMAL_RESULT, "repo-scan", "weekly cron")
        assert "repo-scan" in md
        assert "weekly cron" in md

    def test_no_findings_shows_placeholder(self):
        md = mod.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "No findings" in md

    def test_findings_appear_in_table(self):
        md = mod.build_report_md(FULL_RESULT, "pr", "PR #2")
        assert "Hardcoded password detected" in md
        assert "CRITICAL" in md
        assert "src/auth.py" in md

    def test_iac_findings_appear(self):
        md = mod.build_report_md(FULL_RESULT, "pr", "PR #2")
        assert "S3 bucket lacks encryption" in md
        assert "Missing resource tags" in md

    def test_no_iac_findings_shows_none(self):
        md = mod.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "_None_" in md

    def test_positive_observations_appear(self):
        md = mod.build_report_md(FULL_RESULT, "pr", "PR #2")
        assert "Good docstrings" in md
        assert "Type hints used throughout" in md

    def test_no_positive_observations_shows_none(self):
        data = {**MINIMAL_RESULT, "positive_observations": []}
        md = mod.build_report_md(data, "pr", "PR #1")
        assert "_None_" in md

    def test_generated_timestamp_present(self):
        md = mod.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        assert today in md

    def test_auto_generated_footer(self):
        md = mod.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "Auto-generated" in md

    def test_