"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, edge cases
- review_pr(): happy path, Claude response handling, comment formatting, return value
- review_repo(): happy path, file truncation behaviour, token budget logic
- get_output_url(): URL construction
- build_report_md(): report structure, missing fields, empty findings, IaC/positive obs

Mocks used:
- shared.call_claude          (unittest.mock.patch)
- shared.get_pr_diff          (unittest.mock.patch)
- shared.get_repo_files       (unittest.mock.patch)
- shared.post_pr_comment      (unittest.mock.patch)
- shared.write_output_file    (unittest.mock.patch)
- shared.send_email           (unittest.mock.patch)
- shared.write_audit_entry    (unittest.mock.patch)
- requests (not called directly in tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires environment variable wiring
- TODO: Test post_pr_comment actual HTTP call (needs GH_API/GH_HEADERS context)
- TODO: Test write_output_file + write_audit_entry side-effects in review_pr/__main__
"""

import sys
import os
import json
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Path setup – mirror what the source file does
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# We need to stub out the `shared` module before importing tool1_code_review
# because the import happens at module level.
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude        = MagicMock()
_shared_stub.get_repo_files     = MagicMock()
_shared_stub.get_pr_diff        = MagicMock()
_shared_stub.write_output_file  = MagicMock()
_shared_stub.post_pr_comment    = MagicMock()
_shared_stub.send_email         = MagicMock()
_shared_stub.email_html         = MagicMock()
_shared_stub.write_audit_entry  = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER  = "test-owner"
_shared_stub.OUTPUT_REPO        = "test-output-repo"
_shared_stub.GH_HEADERS         = {"Authorization": "Bearer fake"}
_shared_stub.GH_API             = "https://api.github.com"

sys.modules["shared"] = _shared_stub

# Now safe to import the module under test
import tool1_code_review as t1  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the code is acceptable.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": [],
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
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or a secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket has no encryption.",
            "recommendation": "Enable default SSE-S3 or SSE-KMS encryption.",
        },
    ],
    "positive_observations": ["Good test coverage.", "CI pipeline is well structured."],
    "iac_findings": ["Missing resource tags on all EC2 instances."],
}


def _make_raw_json(data: dict) -> str:
    return json.dumps(data)


# ---------------------------------------------------------------------------
# extract_json – happy paths
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:

    def test_plain_json_string(self):
        raw = _make_raw_json(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + _make_raw_json(MINIMAL_RESULT) + "\n   "
        result = t1.extract_json(raw)
        assert result["summary"] == "Overall the code is acceptable."

    def test_full_result_round_trip(self):
        raw = _make_raw_json(FULL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_markdown_triple_backtick_fences(self):
        raw = "```json\n" + _make_raw_json(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_markdown_backtick_no_language_tag(self):
        raw = "```\n" + _make_raw_json(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_preamble_text_before_json(self):
        raw = "Here is the review result:\n" + _make_raw_json(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 75

    def test_preamble_and_postamble(self):
        raw = "Some text before.\n" + _make_raw_json(FULL_RESULT) + "\nSome text after."
        result = t1.extract_json(raw)
        assert result["score"] == 42

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate Claude inserting a literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # direct json.loads would fail; extract_json should handle it
        result = t1.extract_json(raw)
        assert result["score"] == 50

    def test_extra_text_around_braces(self):
        payload = _make_raw_json({"score": 88, "summary": "ok", "merge_recommendation": "APPROVE",
                                   "findings": [], "positive_observations": [], "iac_findings": []})
        raw = f"Assistant response: {payload} end."
        result = t1.extract_json(raw)
        assert result["score"] == 88


# ---------------------------------------------------------------------------
# extract_json – edge / boundary cases
# ---------------------------------------------------------------------------

class TestExtractJsonEdgeCases:

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = t1.extract_json(_make_raw_json(data))
        assert result["findings"] == []

    def test_score_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = t1.extract_json(_make_raw_json(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = t1.extract_json(_make_raw_json(data))
        assert result["score"] == 100

    def test_null_line_in_finding(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "x", "recommendation": "y"}
        ]}
        result = t1.extract_json(_make_raw_json(data))
        assert result["findings"][0]["line"] is None

    def test_unicode_values_preserved(self):
        data = {**MINIMAL_RESULT, "summary": "الملخص هنا"}
        result = t1.extract_json(_make_raw_json(data))
        assert result["summary"] == "الملخص هنا"

    def test_multiple_json_objects_uses_outermost(self):
        # Only one top-level object should be parsed
        inner = '{"nested": true}'
        outer = json.dumps({**MINIMAL_RESULT, "extra": inner})
        result = t1.extract_json(outer)
        assert "score" in result

    def test_whitespace_only_string(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            t1.extract_json("   ")

    def test_empty_string(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            t1.extract_json("")


# ---------------------------------------------------------------------------
# extract_json – error / negative cases
# ---------------------------------------------------------------------------

class TestExtractJsonErrors:

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("This is plain text with no JSON.")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse Claude response as JSON"):
            t1.extract_json('{"score": 75, "summary": missing_quotes}')

    def test_truncated_json_raises(self):
        raw = '{"score": 75, "summary": "ok"'  # missing closing brace
        with pytest.raises((ValueError, json.JSONDecodeError)):
            t1.extract_json(raw)

    def test_array_at_top_level_raises(self):
        # Claude returning an array instead of an object
        with pytest.raises(ValueError):
            t1.extract_json("[1, 2, 3]")

    def test_only_markdown_fence_no_content(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            t1.extract_json("```\n```")


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:

    def setup_method(self):
        _shared_stub.call_claude.reset_mock()
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()

    def test_happy_path_returns_result(self):
        _shared_stub.get_pr_diff.return_value = "diff --git a/foo.py ..."
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        result = t1.review_pr("myorg", "myrepo", 42, "https://example.com/run/1")

        assert result["score"] == 42
        assert result["merge_recommendation"] == "BLOCK"

    def test_post_pr_comment_called_once(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        t1.review_pr("myorg", "myrepo", 1, "https://example.com/run/1")

        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args
        assert call_args[0][0] == "myorg"
        assert call_args[0][1] == "myrepo"
        assert call_args[0][2] == 1

    def test_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        t1.review_pr("myorg", "myrepo", 7, "https://example.com/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "42/100" in comment_text
        assert "BLOCK" in comment_text

    def test_comment_contains_findings(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        t1.review_pr("myorg", "myrepo", 7, "https://example.com/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment_text
        assert "src/example.py" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        t1.review_pr("myorg", "myrepo", 1, "https://example.com/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_positive_observations_shown(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        t1.review_pr("myorg", "myrepo", 1, "https://example.com/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage." in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        t1.review_pr("myorg", "myrepo", 1, "https://example.com/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_get_pr_diff_called_with_correct_args(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        t1.review_pr("orgA", "repoB", 99, "https://example.com/run/1")

        _shared_stub.get_pr_diff.assert_called_once_with("orgA", "repoB", 99)

    def test_call_claude_called_with_system_prompt(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        t1.review_pr("orgA", "repoB", 1, "https://example.com/run/1")

        call_args = _shared_stub.call_claude.call_args
        assert call_args[0][0] == t1.SYSTEM
        assert "some diff" in call_args[0][1]

    def test_invalid_claude_response_raises(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "not json at all"

        with