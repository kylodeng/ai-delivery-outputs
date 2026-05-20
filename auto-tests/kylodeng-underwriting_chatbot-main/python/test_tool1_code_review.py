"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  no JSON found, malformed JSON, edge cases (empty string, only braces)
- review_pr(): happy path, Claude response handling, comment posting, return value
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): happy path, missing keys, empty findings/iac/observations,
  multiple findings, boundary values

Mocks used:
- shared.call_claude (patched at tool1_code_review.call_claude)
- shared.get_repo_files (patched at tool1_code_review.get_repo_files)
- shared.get_pr_diff (patched at tool1_code_review.get_pr_diff)
- shared.write_output_file (patched at tool1_code_review.write_output_file)
- shared.post_pr_comment (patched at tool1_code_review.post_pr_comment)
- shared.send_email (patched at tool1_code_review.send_email)
- shared.write_audit_entry (patched at tool1_code_review.write_audit_entry)
- requests (not directly used in tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires environment variable setup
  and full shared module context — stub provided below.
- TODO: Test review_pr with real PR number validation once PR API shape is confirmed.
- TODO: Test email sending path in __main__ block once full source is available.
"""

import json
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Module-level import guard — the source file does sys.path.insert and imports
# from 'shared', which may not exist in the test environment.  We stub it out
# before importing the module under test.
# ---------------------------------------------------------------------------
import sys
import types

# Build a minimal fake 'shared' module so the import succeeds without the
# real implementation being present.
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
_shared_stub.GH_HEADERS = {"Authorization": "token test"}
_shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import tool1_code_review as t1  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code looks reasonable.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password found.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage.", "Clear variable names."],
    "iac_findings": ["S3 bucket lacks server-side encryption."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


@pytest.fixture()
def valid_result():
    return dict(VALID_RESULT)


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs between tests."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment", "send_email",
                 "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:

    # --- Happy path ---

    def test_plain_json_string(self):
        result = t1.extract_json(VALID_JSON_STR)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + VALID_JSON_STR + "\n   "
        result = t1.extract_json(raw)
        assert result["summary"] == "Overall the code looks reasonable."

    def test_json_wrapped_in_markdown_fences(self):
        raw = "```json\n" + VALID_JSON_STR + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_json_wrapped_in_plain_backtick_fences(self):
        raw = "```\n" + VALID_JSON_STR + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_json_with_preamble_text(self):
        raw = "Here is my review:\n\n" + VALID_JSON_STR
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_postamble_text(self):
        raw = VALID_JSON_STR + "\n\nPlease let me know if you need more."
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_json_with_preamble_and_postamble(self):
        raw = "Sure, here:\n" + VALID_JSON_STR + "\nThat is all."
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_minimal_valid_json(self):
        raw = '{"summary": "ok", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = t1.extract_json(raw)
        assert result["score"] == 50
        assert result["findings"] == []

    def test_score_boundary_zero(self):
        obj = dict(VALID_RESULT, score=0)
        result = t1.extract_json(json.dumps(obj))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        obj = dict(VALID_RESULT, score=100)
        result = t1.extract_json(json.dumps(obj))
        assert result["score"] == 100

    def test_finding_with_null_line(self):
        obj = dict(VALID_RESULT)
        obj["findings"] = [dict(VALID_RESULT["findings"][0], line=None)]
        result = t1.extract_json(json.dumps(obj))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        obj = dict(VALID_RESULT, findings=[])
        result = t1.extract_json(json.dumps(obj))
        assert result["findings"] == []

    def test_multiple_findings(self):
        findings = [
            {"severity": "CRITICAL", "category": "security", "file": "a.py",
             "line": 1, "issue": "issue1", "recommendation": "rec1"},
            {"severity": "LOW", "category": "maintainability", "file": "b.tf",
             "line": None, "issue": "issue2", "recommendation": "rec2"},
        ]
        obj = dict(VALID_RESULT, findings=findings)
        result = t1.extract_json(json.dumps(obj))
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_newline_inside_string_value_is_cleaned(self):
        """A literal newline inside a JSON string value should be cleaned and parsed."""
        # Craft a raw string where a string value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 60, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # json.loads will reject this; extract_json should clean and return something
        result = t1.extract_json(raw)
        assert result["score"] == 60

    def test_markdown_fence_with_extra_whitespace_after_lang(self):
        raw = "```json  \n" + VALID_JSON_STR + "\n```"
        result = t1.extract_json(raw)
        assert isinstance(result, dict)

    # --- Error / negative cases ---

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("   \n\t  ")

    def test_plain_text_no_braces_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("This is just a sentence.")

    def test_malformed_json_raises_value_error(self):
        raw = '{"summary": "ok", "score": INVALID}'
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_only_opening_brace_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("{")

    def test_reversed_braces_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("}hello{")

    def test_truncated_json_raises_value_error(self):
        raw = '{"summary": "ok", "score": 70, "findings": ['
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_array_root_not_object_raises_value_error(self):
        """Root-level array has no outermost {…} so should raise."""
        raw = '[1, 2, 3]'
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_nested_json_picks_outermost(self):
        """Preamble containing a brace should not confuse the extractor."""
        inner = json.dumps(VALID_RESULT)
        raw = f"Some text {{old}} and then the real one: {inner}"
        result = t1.extract_json(raw)
        # The outermost { … } should correspond to the valid object
        assert "score" in result

    def test_markdown_fence_strips_correctly(self):
        raw = "```json\n{\"score\": 88, \"summary\": \"x\", \"merge_recommendation\": \"APPROVE\", \"findings\": [], \"positive_observations\": [], \"iac_findings\": []}\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 88


# ===========================================================================
# get_output_url tests
# ===========================================================================

class TestGetOutputUrl:

    def test_basic_url_construction(self):
        url = t1.get_output_url("my-owner", "my-repo", "20240101")
        assert url.startswith("https://github.com/")
        assert "my-owner-my-repo-20240101.md" in url

    def test_url_contains_output_repo_owner(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert _shared_stub.OUTPUT_REPO_OWNER in url

    def test_url_contains_output_repo(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert _shared_stub.OUTPUT_REPO in url

    def test_url_contains_code_review_path(self):
        url = t1.get_output_url("owner", "repo", "label")
        assert "code-review" in url

    def test_url_with_special_chars_in_label(self):
        url = t1.get_output_url("o", "r", "PR-123")
        assert "PR-123" in url

    def test_url_ends_with_md(self):
        url = t1.get_output_url("o", "r", "lbl")
        assert url.endswith(".md")

    def test_url_format_owner_repo_separator(self):
        url = t1.get_output_url("acme", "backend", "weekly")
        assert "acme-backend-weekly" in url


# ===========================================================================
# build_report_md tests
# ===========================================================================

class TestBuildReportMd:

    def test_returns_string(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert isinstance(md, str)

    def test_contains_title(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "# Code Review Report" in md

    def test_contains_score(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "75" in md

    def test_contains_merge_recommendation(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "APPROVE" in md

    def test_contains_summary(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "Overall the code looks reasonable." in md

    def test_contains_source(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #99", "owner/repo")
        assert "PR #99" in md

    def test_contains_context(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "acme/backend")
        assert "acme/backend" in md

    def test_contains_finding_severity(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "HIGH" in md

    def test_contains_finding_file(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "src/example.py" in md

    def test_contains_finding_line(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "42" in md

    def test_contains_finding_issue(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "Hardcoded password found." in md

    def test_contains_iac_finding(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "S3 bucket lacks server-side encryption." in md

    def test_contains_positive_observation(self, valid_result):
        md = t1.build_report_md(valid_result, "PR #1", "owner/repo")
        assert "Good test coverage." in md

    def test_contains_generated_timestamp(self, valid_result):
        md = t1.build_report_md(valid_