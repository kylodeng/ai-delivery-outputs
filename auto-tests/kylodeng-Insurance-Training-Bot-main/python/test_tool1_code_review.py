"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested JSON extraction,
  newline-in-string cleaning, error conditions, edge cases
- review_pr(): happy path, comment formatting, result propagation
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): happy path, missing fields, empty findings/iac/positive,
  score/recommendation rendering

Mocks used:
- shared.call_claude (patched at tool1_code_review module level)
- shared.get_pr_diff (patched at tool1_code_review module level)
- shared.get_repo_files (patched at tool1_code_review module level)
- shared.post_pr_comment (patched at tool1_code_review module level)
- shared.write_output_file (patched at tool1_code_review module level)
- shared.send_email (patched at tool1_code_review module level)
- shared.write_audit_entry (patched at tool1_code_review module level)
- requests (not directly called by tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires environment variables and
  full shared module; stub provided below.
- TODO: Test email dispatch path in __main__ — needs SMTP/email mock wiring.
- TODO: Test write_output_file and write_audit_entry call sites once __main__
  block source is complete (source was truncated).
"""

import json
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal stub for `shared` so the import in
# tool1_code_review.py succeeds without real credentials or network calls.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude = MagicMock()
_shared_stub.get_repo_files = MagicMock()
_shared_stub.get_pr_diff = MagicMock()
_shared_stub.write_output_file = MagicMock()
_shared_stub.post_pr_comment = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer test"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Ensure the scripts directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

import tool1_code_review as cr  # noqa: E402  (import after path setup)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the code is acceptable.",
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
    "positive_observations": ["Good test coverage."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

MINIMAL_RESULT_JSON = json.dumps(MINIMAL_RESULT)


def _make_fenced(content: str, lang: str = "") -> str:
    return f"```{lang}\n{content}\n```"


# ---------------------------------------------------------------------------
# extract_json — happy path
# ---------------------------------------------------------------------------


class TestExtractJsonHappyPath:
    def test_plain_json_string(self):
        result = cr.extract_json(MINIMAL_RESULT_JSON)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = f"   \n{MINIMAL_RESULT_JSON}\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Overall the code is acceptable."

    def test_json_with_markdown_fence_no_lang(self):
        raw = _make_fenced(MINIMAL_RESULT_JSON)
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_json_with_markdown_fence_json_lang(self):
        raw = _make_fenced(MINIMAL_RESULT_JSON, "json")
        result = cr.extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"

    def test_json_embedded_in_prose(self):
        raw = f"Here is the review:\n{MINIMAL_RESULT_JSON}\nEnd of response."
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_empty_findings_list(self):
        data = dict(MINIMAL_RESULT, findings=[])
        result = cr.extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_empty_positive_observations(self):
        data = dict(MINIMAL_RESULT, positive_observations=[])
        result = cr.extract_json(json.dumps(data))
        assert result["positive_observations"] == []

    def test_score_zero(self):
        data = dict(MINIMAL_RESULT, score=0)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_one_hundred(self):
        data = dict(MINIMAL_RESULT, score=100)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_null_line_number(self):
        data = dict(MINIMAL_RESULT)
        data["findings"] = [dict(MINIMAL_RESULT["findings"][0], line=None)]
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None


# ---------------------------------------------------------------------------
# extract_json — newline cleaning branch
# ---------------------------------------------------------------------------


class TestExtractJsonNewlineCleaning:
    def test_newline_inside_string_value_is_cleaned(self):
        # Build a JSON string with a literal newline inside a string value
        # (this would normally be invalid JSON)
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # direct json.loads will fail; extract_json should clean and succeed
        result = cr.extract_json(raw)
        assert "line one" in result["summary"]

    def test_multiple_newlines_in_string(self):
        raw = '{"summary": "a\nb\nc", "score": 10, "merge_recommendation": "BLOCK", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        assert result["score"] == 10


# ---------------------------------------------------------------------------
# extract_json — error / edge cases
# ---------------------------------------------------------------------------


class TestExtractJsonErrors:
    def test_completely_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON.")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 75, "broken": }')

    def test_only_opening_brace_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("{")

    def test_only_closing_brace_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("}")

    def test_markdown_fence_containing_invalid_json_raises(self):
        raw = "```\n{not valid json at all\n```"
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("     ")

    def test_array_at_top_level_raises(self):
        # No outer { ... } block — should raise
        with pytest.raises(ValueError):
            cr.extract_json("[1, 2, 3]")


# ---------------------------------------------------------------------------
# extract_json — boundary / parameterised severity values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
def test_extract_json_all_severities(severity):
    data = dict(MINIMAL_RESULT)
    data["findings"] = [dict(MINIMAL_RESULT["findings"][0], severity=severity)]
    result = cr.extract_json(json.dumps(data))
    assert result["findings"][0]["severity"] == severity


@pytest.mark.parametrize("rec", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
def test_extract_json_all_merge_recommendations(rec):
    data = dict(MINIMAL_RESULT, merge_recommendation=rec)
    result = cr.extract_json(json.dumps(data))
    assert result["merge_recommendation"] == rec


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------


class TestReviewPr:
    @pytest.fixture(autouse=True)
    def _reset_mocks(self):
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.call_claude.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()

    def test_happy_path_returns_parsed_result(self):
        _shared_stub.get_pr_diff.return_value = "diff --git a/foo.py ..."
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        result = cr.review_pr("my-org", "my-repo", 42, "https://run.url")

        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        cr.review_pr("org", "repo", 7, "https://run.url")

        _shared_stub.get_pr_diff.assert_called_once_with("org", "repo", 7)

    def test_calls_post_pr_comment(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        cr.review_pr("org", "repo", 7, "https://run.url")

        _shared_stub.post_pr_comment.assert_called_once()
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "org"
        assert args[1] == "repo"
        assert args[2] == 7

    def test_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        cr.review_pr("org", "repo", 1, "https://run.url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "75" in comment

    def test_comment_contains_recommendation(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        cr.review_pr("org", "repo", 1, "https://run.url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        cr.review_pr("org", "repo", 1, "https://run.url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Overall the code is acceptable." in comment

    def test_comment_contains_finding_file(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        cr.review_pr("org", "repo", 1, "https://run.url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/example.py" in comment

    def test_no_findings_shows_no_findings_placeholder(self):
        data = dict(MINIMAL_RESULT, findings=[])
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(data)

        cr.review_pr("org", "repo", 1, "https://run.url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_no_positive_observations_shows_placeholder(self):
        data = dict(MINIMAL_RESULT, positive_observations=[])
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(data)

        cr.review_pr("org", "repo", 1, "https://run.url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_finding_with_null_line_shows_na(self):
        data = dict(MINIMAL_RESULT)
        data["findings"] = [dict(MINIMAL_RESULT["findings"][0], line=None)]
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(data)

        cr.review_pr("org", "repo", 1, "https://run.url")

        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "n/a" in comment

    def test_call_claude_receives_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "my special diff content"
        _shared_stub.call_claude.return_value = MINIMAL_RESULT_JSON

        cr.review_pr("org", "repo", 1, "https://run.url")

        prompt_arg = _shared_stub.call_claude.call_args[0][1]
        assert "my special diff content" in prompt_arg

    def test_multiple_findings_all_appear_in_comment(self):
        data = dict(MINIMAL_RESULT)
        data["findings"] = [
            {
                "severity": "CRITICAL",
                "category": "security",
                "file": "auth.py",
                "line": 10,
                "issue": "SQL injection risk.",
                "recommendation": "Use parameterised queries.",
            },
            {
                "severity": "LOW",
                "category": "maintainability",
                "file": "utils.py",
                "line": 5,
                "issue": "Missing docstring