"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, outermost-brace extraction,
  newline-in-string cleaning, no-JSON error, malformed JSON error
- review_pr(): successful flow, comment construction, result returned
- review_repo(): successful flow, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report markdown generation, empty findings, empty
  iac/positive sections, missing keys

Mocks used:
- shared.call_claude          (via unittest.mock.patch)
- shared.get_pr_diff          (via unittest.mock.patch)
- shared.get_repo_files       (via unittest.mock.patch)
- shared.post_pr_comment      (via unittest.mock.patch)
- shared.write_output_file    (via unittest.mock.patch)
- shared.send_email           (via unittest.mock.patch)
- shared.write_audit_entry    (via unittest.mock.patch)
- requests                    (not called directly in public API; patched
                               defensively where needed)
- datetime.datetime.utcnow    (for deterministic timestamp in build_report_md)

TODOs:
- TODO: Integration test for __main__ block requires real env-var wiring —
  stub added below.
- TODO: Tests for send_email / write_output_file orchestration paths need the
  full __main__ entrypoint refactored into a callable function.
"""

import json
import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal 'shared' stub so we can import the module under
# test without needing the real shared.py or any GitHub tokens.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="{}")
    shared.get_repo_files     = MagicMock(return_value={})
    shared.get_pr_diff        = MagicMock(return_value="diff text")
    shared.write_output_file  = MagicMock()
    shared.post_pr_comment    = MagicMock()
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html/>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-owner"
    shared.OUTPUT_REPO        = "test-output-repo"
    shared.GH_HEADERS         = {"Authorization": "token FAKE"}
    shared.GH_API             = "https://api.github.com"
    return shared


# Inject stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

# Now import the module under test
import tool1_code_review as cr

# Keep a reference to the stub for per-test resetting
shared_stub = sys.modules["shared"]


# ---------------------------------------------------------------------------
# Helpers / fixtures
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
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded secret detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Missing docstring.",
            "recommendation": "Add module-level docstring.",
        },
    ],
    "positive_observations": ["Clear variable naming", "Good test coverage"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role too permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs before every test."""
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

    # ---- happy path: plain JSON -----------------------------------------

    def test_plain_json_object(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_with_leading_trailing_whitespace(self):
        raw = "   " + json.dumps(MINIMAL_RESULT) + "\n\n"
        result = cr.extract_json(raw)
        assert result["summary"] == "Looks good overall."

    # ---- markdown fence stripping ----------------------------------------

    def test_strips_triple_backtick_fence(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_strips_plain_triple_backtick_fence(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_strips_fence_with_trailing_text(self):
        inner = json.dumps({"summary": "ok", "score": 70,
                            "merge_recommendation": "APPROVE",
                            "findings": [], "positive_observations": [],
                            "iac_findings": []})
        raw = "```json\n" + inner + "\n```\nSome trailing text"
        result = cr.extract_json(raw)
        assert result["score"] == 70

    # ---- outermost-brace extraction --------------------------------------

    def test_extracts_json_with_preamble(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_extracts_json_with_postamble(self):
        raw = json.dumps(MINIMAL_RESULT) + "\nPlease let me know."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_extracts_json_embedded_in_text(self):
        raw = "Some text before. " + json.dumps(FULL_RESULT) + " Some text after."
        result = cr.extract_json(raw)
        assert result["score"] == 42

    # ---- newline-in-string cleaning -------------------------------------

    def test_cleans_literal_newline_inside_string_value(self):
        # Simulate a response where a string value has a literal newline
        raw = '{"summary": "line one\nline two", "score": 50, ' \
              '"merge_recommendation": "APPROVE", "findings": [], ' \
              '"positive_observations": [], "iac_findings": []}'
        # json.loads would fail on the raw string; extract_json should fix it
        result = cr.extract_json(raw)
        assert "line one" in result["summary"]

    # ---- error conditions ------------------------------------------------

    def test_raises_value_error_when_no_json_found(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no braces.")

    def test_raises_value_error_on_malformed_json(self):
        # Has braces but is not valid JSON
        raw = "{ totally: not valid json !! }"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_raises_value_error_on_empty_string(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_raises_value_error_on_whitespace_only(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_raises_value_error_on_unclosed_brace(self):
        raw = '{"summary": "missing closing brace"'
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    # ---- edge cases ------------------------------------------------------

    def test_handles_nested_objects_in_findings(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_handles_null_line_field(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_handles_empty_findings_list(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"] == []

    def test_handles_score_zero(self):
        data = dict(MINIMAL_RESULT, score=0)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_handles_score_100(self):
        data = dict(MINIMAL_RESULT, score=100)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_fence_without_closing_fence(self):
        """Content that starts with ``` but has no closing fence."""
        raw = "```json\n" + json.dumps(MINIMAL_RESULT)
        # rsplit on ``` will return the full string if no closing fence present;
        # the JSON should still be parseable via direct parse or brace extraction.
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_multiple_json_objects_extracts_outermost(self):
        """When multiple {} appear, outermost should win."""
        inner = '{"x": 1}'
        outer = json.dumps(MINIMAL_RESULT)
        raw = outer + " extra: " + inner
        result = cr.extract_json(raw)
        # outermost braces span the full MINIMAL_RESULT
        assert result["score"] == 85


# ===========================================================================
# get_output_url
# ===========================================================================

class TestGetOutputUrl:

    def test_basic_url_construction(self):
        url = cr.get_output_url("myowner", "myrepo", "2024-01-15")
        assert url == (
            "https://github.com/test-owner/test-output-repo"
            "/blob/main/code-review/myowner-myrepo-2024-01-15.md"
        )

    def test_url_contains_owner(self):
        url = cr.get_output_url("acme-corp", "backend", "pr-42")
        assert "acme-corp" in url

    def test_url_contains_repo(self):
        url = cr.get_output_url("acme-corp", "backend", "pr-42")
        assert "backend" in url

    def test_url_contains_label(self):
        url = cr.get_output_url("acme-corp", "backend", "pr-42")
        assert "pr-42" in url

    def test_url_uses_output_repo_owner(self):
        url = cr.get_output_url("x", "y", "z")
        assert "test-owner" in url

    def test_url_uses_output_repo(self):
        url = cr.get_output_url("x", "y", "z")
        assert "test-output-repo" in url

    def test_url_starts_with_https(self):
        url = cr.get_output_url("x", "y", "z")
        assert url.startswith("https://github.com/")

    def test_url_with_special_chars_in_label(self):
        url = cr.get_output_url("owner", "repo", "weekly-2024-01-15")
        assert "weekly-2024-01-15" in url

    def test_url_path_contains_code_review_segment(self):
        url = cr.get_output_url("owner", "repo", "label")
        assert "/code-review/" in url


# ===========================================================================
# build_report_md
# ===========================================================================

class TestBuildReportMd:

    FIXED_NOW = "2024-06-01 12:00 UTC"

    @pytest.fixture
    def frozen_datetime(self):
        fixed_dt = datetime.datetime(2024, 6, 1, 12, 0, 0)
        with patch("tool1_code_review.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = fixed_dt
            mock_dt.datetime.utcnow.return_value.strftime = fixed_dt.strftime
            yield mock_dt

    def _build(self, result=None, source="pr", context="PR #42"):
        result = result or MINIMAL_RESULT
        return cr.build_report_md(result, source, context)

    def test_returns_string(self):
        md = self._build()
        assert isinstance(md, str)

    def test_contains_score(self):
        md = self._build(FULL_RESULT)
        assert "42" in md

    def test_contains_merge_recommendation(self):
        md = self._build(FULL_RESULT)
        assert "REQUEST_CHANGES" in md

    def test_contains_summary(self):
        md = self._build(FULL_RESULT)
        assert "Several issues found." in md

    def test_contains_source(self):
        md = self._build(source="scheduled-scan")
        assert "scheduled-scan" in md

    def test_contains_context(self):
        md = self._build(context="PR #99")
        assert "PR #99" in md

    def test_contains_finding_severity(self):
        md = self._build(FULL_RESULT)
        assert "HIGH" in md

    def test_contains_finding_file(self):
        md = self._build(FULL_RESULT)
        assert "src/main.py" in md

    def test_contains_finding_issue(self):
        md = self._build(FULL_RESULT)
        assert "Hardcoded secret detected." in md

    def test_contains_finding_recommendation(self):
        md = self._build(FULL_RESULT)
        assert "Use environment variables instead." in md

    def test_no_findings_shows_placeholder(self):
        md = self._build(MINIMAL_RESULT)
        assert "No findings" in md

    def test_iac_findings_present(self):
        md = self._build(FULL_RESULT)
        assert "S3 bucket lacks versioning" in md
        assert "IAM role too permissive" in md

    def test_no_iac_findings_shows_none(self):
        md = self._build(MINIMAL_RESULT)
        assert "_None_" in md

    def test_positive_observations_present(self):
        md = self._build(FULL_RESULT)
        assert "Clear variable naming" in md

    def test_no_positive_observations_shows_none(self):
        data = dict(MINIMAL_RESULT, positive_observations=[])
        md = self