"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, edge cases
- review_pr: happy path, Claude returning bad JSON, PR comment posting
- review_repo: happy path, token budget truncation, empty file list
- get_output_url: URL construction
- build_report_md: full report, empty findings, missing keys, IaC/positive obs

Mocks used:
- shared.call_claude (patched via unittest.mock.patch)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- requests (not directly called in tested functions but imported)

TODOs:
- TODO: Integration test for __main__ block requires full environment variable setup
- TODO: Test write_output_file integration once OUTPUT_REPO constants are injectable
- TODO: Test email/audit side-effects in review_pr/review_repo if wired up
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: stub out the `shared` module before importing the module under test
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now import the module under test
import importlib
import types

# We need to import the actual file; sys.path manipulation already done in module
script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Import the module under test with shared already stubbed
from tool1_code_review import (
    extract_json,
    review_pr,
    review_repo,
    get_output_url,
    build_report_md,
    SYSTEM,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Code looks acceptable overall.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded secret detected.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage."],
    "iac_findings": ["Missing resource tags on S3 bucket."],
}

EMPTY_RESULT = {
    "summary": "No issues found.",
    "score": 100,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": [],
    "iac_findings": [],
}


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for the extract_json helper function."""

    # --- Happy path ---

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = extract_json(raw)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(EMPTY_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["score"] == 100

    def test_markdown_fences_triple_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["summary"] == "Code looks acceptable overall."

    def test_markdown_fences_with_language_tag(self):
        raw = "```json\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 75

    def test_json_embedded_in_prose(self):
        payload = json.dumps(MINIMAL_VALID_RESULT)
        raw = f"Here is my analysis:\n{payload}\nThank you."
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_empty_findings_list(self):
        raw = json.dumps(EMPTY_RESULT)
        result = extract_json(raw)
        assert result["findings"] == []
        assert result["iac_findings"] == []

    def test_score_boundary_zero(self):
        data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {
            **MINIMAL_VALID_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "README.md",
                    "line": None,
                    "issue": "Missing documentation.",
                    "recommendation": "Add docstrings.",
                }
            ],
        }
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_all_severity_values(self):
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            data = {
                **MINIMAL_VALID_RESULT,
                "findings": [{**MINIMAL_VALID_RESULT["findings"][0], "severity": sev}],
            }
            result = extract_json(json.dumps(data))
            assert result["findings"][0]["severity"] == sev

    def test_all_merge_recommendations(self):
        for rec in ("APPROVE", "REQUEST_CHANGES", "BLOCK"):
            data = {**MINIMAL_VALID_RESULT, "merge_recommendation": rec}
            result = extract_json(json.dumps(data))
            assert result["merge_recommendation"] == rec

    # --- Newline-in-string cleanup ---

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate Claude inserting a literal newline inside a JSON string value
        raw = '{"summary": "line one\nline two", "score": 80, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # Direct parse will fail; extract_json should recover
        result = extract_json(raw)
        assert result["score"] == 80

    # --- Error / negative cases ---

    def test_no_json_object_raises_value_error(self):
        raw = "This is just plain text with no JSON whatsoever."
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json(raw)

    def test_malformed_json_raises_value_error(self):
        raw = '{"summary": "broken", "score": NOTANUMBER}'
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_braces_no_content_raises_value_error(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            extract_json("{}")
        # {} is valid JSON → should return empty dict, not raise
        result = extract_json("{}")
        assert result == {}

    def test_only_braces_returns_empty_dict(self):
        result = extract_json("{}")
        assert isinstance(result, dict)

    def test_markdown_fence_with_extra_whitespace(self):
        raw = "```\n\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n\n```"
        result = extract_json(raw)
        assert "score" in result

    def test_deeply_nested_json(self):
        data = {
            "summary": "Deep test.",
            "score": 55,
            "merge_recommendation": "REQUEST_CHANGES",
            "findings": [
                {
                    "severity": "CRITICAL",
                    "category": "security",
                    "file": "backend/model_card.json",
                    "line": 10,
                    "issue": "Hardcoded model credentials.",
                    "recommendation": "Move credentials to secrets manager.",
                }
            ],
            "positive_observations": ["Good separation of concerns."],
            "iac_findings": ["Missing resource tags on S3 bucket."],
        }
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_multiple_json_objects_picks_outermost(self):
        inner = json.dumps({"inner": True})
        outer = json.dumps({"outer": True, "nested_str": inner})
        result = extract_json(outer)
        assert result["outer"] is True

    def test_unicode_content_in_values(self):
        # Simulates ar-SA.json content appearing as a string value
        data = {
            "summary": "Arabic content detected in translation file.",
            "score": 90,
            "merge_recommendation": "APPROVE",
            "findings": [],
            "positive_observations": ["\u0625\u0644\u063a\u0627\u0621"],
            "iac_findings": [],
        }
        result = extract_json(json.dumps(data))
        assert "\u0625\u0644\u063a\u0627\u0621" in result["positive_observations"]


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:
    """Tests for the review_pr function."""

    def _make_mocks(self, result_override=None):
        result = result_override if result_override is not None else MINIMAL_VALID_RESULT
        shared_stub.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+print('hello')"
        shared_stub.call_claude.return_value = json.dumps(result)
        shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._make_mocks()
        result = review_pr("my-org", "my-repo", 42, "https://github.com/actions/run/1")
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_post_pr_comment_called_once(self):
        self._make_mocks()
        shared_stub.post_pr_comment.reset_mock()
        review_pr("my-org", "my-repo", 42, "https://github.com/actions/run/1")
        shared_stub.post_pr_comment.assert_called_once()

    def test_post_pr_comment_receives_owner_repo_pr(self):
        self._make_mocks()
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "frontend", 99, "https://ci/run/2")
        args = shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "acme"
        assert args[1] == "frontend"
        assert args[2] == 99

    def test_comment_contains_score(self):
        self._make_mocks()
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "frontend", 1, "https://ci")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "75" in comment_body

    def test_comment_contains_recommendation(self):
        self._make_mocks()
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "frontend", 1, "https://ci")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_body

    def test_comment_contains_summary(self):
        self._make_mocks()
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "frontend", 1, "https://ci")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "Code looks acceptable overall." in comment_body

    def test_comment_contains_findings(self):
        self._make_mocks()
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "frontend", 1, "https://ci")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "src/example.py" in comment_body
        assert "Hardcoded secret detected." in comment_body

    def test_comment_no_findings_shows_placeholder(self):
        self._make_mocks(result_override=EMPTY_RESULT)
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "frontend", 1, "https://ci")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_body

    def test_comment_no_positive_obs_shows_placeholder(self):
        self._make_mocks(result_override=EMPTY_RESULT)
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "frontend", 1, "https://ci")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_body

    def test_call_claude_invoked_with_diff_content(self):
        self._make_mocks()
        shared_stub.call_claude.reset_mock()
        review_pr("acme", "repo", 7, "https://ci")
        claude_call_args = shared_stub.call_claude.call_args[0]
        assert "diff" in claude_call_args[1].lower() or "review" in claude_call_args[1].lower()

    def test_bad_json_from_claude_raises(self):
        shared_stub.get_pr_diff.return_value = "some diff"
        shared_stub.call_claude.return_value = "This is not JSON at all."
        with pytest.raises(ValueError):
            review_pr("acme", "repo", 7, "https://ci")

    def test_missing_score_key_uses_question_mark(self):
        result_no_score = {k: v for k, v in MINIMAL_VALID_RESULT.items() if k != "score"}
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value = json.dumps(result_no_score)
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme", "repo", 1, "https://ci")
        comment_body = shared_stub.post_pr_comment.call_args[0][3]
        assert "?" in comment_body

    def test_multiple_findings_all_appear_in_comment(self):
        multi_finding_result = {
            **MINIMAL_VALID_RESULT,
            "findings": [
                {
                    "severity": "CRITICAL",
                    "category": "security",
                    "file": "backend/model_card.json",
                    "line": 5,
                    "issue": "Hardcoded password.",
                