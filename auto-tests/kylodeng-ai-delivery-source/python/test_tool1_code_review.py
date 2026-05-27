"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fences, outermost-block extraction,
      newline-in-string cleaning, missing JSON, malformed JSON
    - review_pr(): happy path, Claude response handling, comment formatting,
      missing fields in result
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): happy path, empty findings, empty iac/positive,
      missing result fields

Mocks used:
    - shared.call_claude          (patched via sys.modules)
    - shared.get_pr_diff          (patched via sys.modules)
    - shared.get_repo_files       (patched via sys.modules)
    - shared.post_pr_comment      (patched via sys.modules)
    - shared.write_output_file    (patched via sys.modules)
    - shared.send_email           (patched via sys.modules)
    - shared.write_audit_entry    (patched via sys.modules)
    - requests                    (patched at module level)

TODOs:
    - TODO: Integration test for __main__ block requires full env setup
    - TODO: Test email sending path once send_email signature is confirmed
    - TODO: Test write_output_file path once its signature is confirmed
"""

import sys
import os
import json
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: inject a fake `shared` module before importing the module under
# test so we never attempt real network / file I/O.
# ---------------------------------------------------------------------------

_shared_mock = types.ModuleType("shared")
_shared_mock.call_claude = MagicMock()
_shared_mock.get_repo_files = MagicMock()
_shared_mock.get_pr_diff = MagicMock()
_shared_mock.write_output_file = MagicMock()
_shared_mock.post_pr_comment = MagicMock()
_shared_mock.send_email = MagicMock()
_shared_mock.email_html = MagicMock()
_shared_mock.write_audit_entry = MagicMock()
_shared_mock.OUTPUT_REPO_OWNER = "test-owner"
_shared_mock.OUTPUT_REPO = "test-output-repo"
_shared_mock.GH_HEADERS = {"Authorization": "Bearer fake"}
_shared_mock.GH_API = "https://api.github.com"

sys.modules["shared"] = _shared_mock

# Make sure any previous import is cleared so our mock is used
for mod_name in list(sys.modules.keys()):
    if "tool1_code_review" in mod_name:
        del sys.modules[mod_name]

# Insert the scripts directory into sys.path so the import works
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import tool1_code_review as cr  # noqa: E402  (import after path manipulation)


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
    "summary": "Several critical issues found.",
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
            "file": "main.tf",
            "line": None,
            "issue": "S3 bucket has public access",
            "recommendation": "Enable block public access settings",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils.js",
            "line": 5,
            "issue": "Unused variable",
            "recommendation": "Remove unused variable",
        },
    ],
    "positive_observations": ["Tests present", "CI pipeline configured"],
    "iac_findings": ["Missing encryption on RDS instance"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared mocks between tests."""
    _shared_mock.call_claude.reset_mock()
    _shared_mock.get_repo_files.reset_mock()
    _shared_mock.get_pr_diff.reset_mock()
    _shared_mock.write_output_file.reset_mock()
    _shared_mock.post_pr_comment.reset_mock()
    _shared_mock.send_email.reset_mock()
    _shared_mock.write_audit_entry.reset_mock()
    yield


# ---------------------------------------------------------------------------
# Tests: extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for cr.extract_json()"""

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n  "
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_json_wrapped_in_markdown_triple_backtick(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_wrapped_in_plain_triple_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_surrounded_by_prose(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd of review."
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_json_with_newline_inside_string_value(self):
        # Simulate Claude inserting literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        assert result["score"] == 50

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_raises_value_error_when_no_json_found(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_raises_value_error_on_malformed_json(self):
        malformed = '{"summary": "ok", "score": NOTANUMBER}'
        with pytest.raises(ValueError):
            cr.extract_json(malformed)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_whitespace_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_nested_braces_resolved_correctly(self):
        """Outer braces should be used; nested content preserved."""
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["iac_findings"] == ["Missing encryption on RDS instance"]

    def test_markdown_fence_without_closing_backticks(self):
        """If closing backticks absent, fallback to brace extraction."""
        inner = json.dumps(MINIMAL_RESULT)
        raw = "```json\n" + inner  # no closing ```
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_extra_text_after_json_in_fenced_block(self):
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"```json\n{inner}\n```\n\nSome extra note."
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    @pytest.mark.parametrize("score", [0, 1, 50, 99, 100])
    def test_boundary_score_values(self, score):
        data = {**MINIMAL_RESULT, "score": score}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == score

    def test_null_line_in_finding(self):
        data = {**FULL_RESULT}
        raw = json.dumps(data)
        result = cr.extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# Tests: get_output_url
# ---------------------------------------------------------------------------

class TestGetOutputUrl:
    def test_basic_url_structure(self):
        url = cr.get_output_url("myorg", "myrepo", "pr-42")
        assert url.startswith("https://github.com/")
        assert "test-owner" in url
        assert "test-output-repo" in url
        assert "myorg-myrepo-pr-42" in url

    def test_url_ends_with_md(self):
        url = cr.get_output_url("org", "repo", "label")
        assert url.endswith(".md")

    def test_url_contains_code_review_path(self):
        url = cr.get_output_url("org", "repo", "label")
        assert "code-review" in url

    def test_special_characters_in_label(self):
        # Should not crash; URL encoding is caller's responsibility
        url = cr.get_output_url("org", "repo", "2024-01-01")
        assert "2024-01-01" in url

    def test_owner_repo_label_all_reflected(self):
        url = cr.get_output_url("alpha", "beta", "gamma")
        assert "alpha" in url
        assert "beta" in url
        assert "gamma" in url


# ---------------------------------------------------------------------------
# Tests: build_report_md
# ---------------------------------------------------------------------------

class TestBuildReportMd:
    def test_returns_string(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert isinstance(md, str)

    def test_contains_score(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "80" in md

    def test_contains_merge_recommendation(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "APPROVE" in md

    def test_contains_summary(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "Looks good overall." in md

    def test_contains_source_and_context(self):
        md = cr.build_report_md(MINIMAL_RESULT, "scheduled", "weekly-cron")
        assert "scheduled" in md
        assert "weekly-cron" in md

    def test_full_result_findings_table(self):
        md = cr.build_report_md(FULL_RESULT, "pr", "PR #99")
        assert "CRITICAL" in md
        assert "Hardcoded password detected" in md
        assert "src/auth.py" in md

    def test_no_findings_placeholder(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "No findings" in md

    def test_iac_findings_present(self):
        md = cr.build_report_md(FULL_RESULT, "pr", "PR #99")
        assert "Missing encryption on RDS instance" in md

    def test_iac_findings_empty_shows_none(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "_None_" in md

    def test_positive_observations_present(self):
        md = cr.build_report_md(FULL_RESULT, "pr", "PR #99")
        assert "Tests present" in md
        assert "CI pipeline configured" in md

    def test_positive_observations_empty_shows_none(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        md = cr.build_report_md(result, "pr", "PR #1")
        assert "_None_" in md

    def test_contains_generated_timestamp(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "UTC" in md

    def test_missing_score_field(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        md = cr.build_report_md(result, "pr", "PR #1")
        assert "N/A" in md

    def test_missing_merge_recommendation(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "merge_recommendation"}
        md = cr.build_report_md(result, "pr", "PR #1")
        assert "N/A" in md

    def test_missing_summary(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "summary"}
        md = cr.build_report_md(result, "pr", "PR #1")
        assert "No summary provided." in md

    def test_finding_with_null_line(self):
        result = {
            **MINIMAL_RESULT,
            "findings": [
                {
                    "severity": "HIGH",
                    "category": "security",
                    "file": "app.py",
                    "line": None,
                    "issue": "Issue here",
                    "recommendation": "Fix it",
                }
            ],
        }
        md = cr.build_report_md(result, "pr", "PR #1")
        assert "app.py" in md

    def test_auto_generated_footer(self):
        md = cr.build_report_md(MINIMAL_RESULT, "pr", "PR #1")
        assert "AI Delivery Bot" in md

    def test_multiple_iac_findings(self):
        result = {**MINIMAL_RESULT, "iac_findings": ["Issue A", "Issue B", "Issue C"]}
        md = cr.build_report_md(result, "repo", "weekly")
        assert "Issue A" in md
        assert "Issue B" in md
        assert "Issue C" in md


# ---------------------------------------------------------------------------
# Tests: review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:
    def test_happy_path_returns_result(self):
        _shared_mock.get_pr_diff.return_value = "diff --git a/foo.py ..."
        _shared_mock.call_claude.return_value = json.dumps(FULL_RESULT)

        result = cr.review_pr("org", "repo", 