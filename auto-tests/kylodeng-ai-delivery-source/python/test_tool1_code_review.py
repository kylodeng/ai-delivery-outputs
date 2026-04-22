"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fences, nested braces, newlines in strings,
      missing JSON, malformed JSON, edge cases
    - review_pr(): happy path, Claude response handling, comment posting, error propagation
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): full report, empty findings, missing keys, IaC findings,
      positive observations

Mocks used:
    - shared.call_claude          (patched via unittest.mock.patch)
    - shared.get_pr_diff          (patched)
    - shared.get_repo_files       (patched)
    - shared.post_pr_comment      (patched)
    - shared.write_output_file    (patched)
    - shared.send_email           (patched)
    - shared.write_audit_entry    (patched)
    - requests                    (patched where needed)
    - datetime.datetime.utcnow    (patched for deterministic output)

TODOs:
    - TODO: Integration test for __main__ block requires environment variable setup
    - TODO: Test rate-limiting / retry behaviour in call_claude (needs shared.py internals)
    - TODO: Test write_output_file interaction once output repo structure is confirmed
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call
import datetime

# ---------------------------------------------------------------------------
# Path setup — mirror what the source file does
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

# We must stub out the `shared` module before importing tool1_code_review,
# because the source does a bare `from shared import …` at module level.
import types

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
_shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = _shared_stub

import tool1_code_review as cr  # noqa: E402  (must come after stub)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Overall code quality is acceptable.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": [],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
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
            "file": "src/utils.py",
            "line": None,
            "issue": "Missing docstring on public function.",
            "recommendation": "Add a docstring.",
        },
    ],
    "positive_observations": ["Good use of type hints.", "Tests are present."],
    "iac_findings": ["S3 bucket missing encryption.", "IAM role too permissive."],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared-stub mocks before every test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:
    """Tests for cr.extract_json()"""

    # --- Happy path ---

    def test_plain_json_object(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_whitespace(self):
        raw = "   \n  " + json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["summary"] == "Overall code quality is acceptable."

    def test_json_with_trailing_whitespace(self):
        raw = json.dumps(MINIMAL_VALID_RESULT) + "   \n"
        result = cr.extract_json(raw)
        assert result["score"] == 75

    # --- Markdown fence stripping ---

    def test_triple_backtick_json_fence(self):
        raw = "```json\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_triple_backtick_plain_fence(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_fence_with_extra_text_after(self):
        """Text after closing fence should be stripped."""
        raw = "```json\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```\nSome trailing text."
        result = cr.extract_json(raw)
        assert result["score"] == 75

    # --- Brace extraction fallback ---

    def test_json_embedded_in_text(self):
        inner = json.dumps(MINIMAL_VALID_RESULT)
        raw = f"Here is your review:\n{inner}\nEnd of review."
        result = cr.extract_json(raw)
        assert result["score"] == 75

    def test_json_with_preamble(self):
        inner = json.dumps(FULL_RESULT)
        raw = "Sure! Here is the JSON:\n" + inner
        result = cr.extract_json(raw)
        assert result["score"] == 42

    # --- Newline cleaning inside string values ---

    def test_newline_inside_string_value(self):
        """Literal newline inside a JSON string value should be cleaned."""
        raw = '{"summary": "This is a\nsummary.", "score": 50, ' \
              '"merge_recommendation": "APPROVE", "findings": [], ' \
              '"positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        assert "summary" in result
        # After cleaning the newline should be replaced with a space
        assert "\n" not in result["summary"]

    # --- Error conditions ---

    def test_raises_on_no_json_at_all(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no braces.")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_raises_on_malformed_json(self):
        raw = '{"score": 75, "summary": "broken'  # unterminated string
        with pytest.raises(ValueError, match="Could not parse Claude response as JSON"):
            cr.extract_json(raw)

    def test_raises_on_braces_only(self):
        with pytest.raises(ValueError):
            cr.extract_json("{{{{{")

    def test_raises_on_array_only(self):
        """An array at top level is valid JSON but extract_json looks for braces."""
        # The outer-brace extractor won't find matching { }, so ValueError expected
        with pytest.raises(ValueError):
            cr.extract_json("[1, 2, 3]")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    # --- Boundary / edge ---

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket missing encryption."

    def test_minimal_json(self):
        raw = '{"score": 0}'
        result = cr.extract_json(raw)
        assert result["score"] == 0

    def test_score_100(self):
        raw = json.dumps({**MINIMAL_VALID_RESULT, "score": 100})
        result = cr.extract_json(raw)
        assert result["score"] == 100

    def test_line_null_in_finding(self):
        payload = {**MINIMAL_VALID_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "main.py", "line": None,
             "issue": "Missing docstring.", "recommendation": "Add one."}
        ]}
        result = cr.extract_json(json.dumps(payload))
        assert result["findings"][0]["line"] is None

    def test_json_with_unicode(self):
        payload = {**MINIMAL_VALID_RESULT, "summary": "Résumé: all good. 日本語テスト"}
        result = cr.extract_json(json.dumps(payload))
        assert "Résumé" in result["summary"]


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:
    """Tests for cr.review_pr()"""

    def _setup(self, result_payload=None):
        payload = result_payload or FULL_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py ..."
        _shared_stub.call_claude.return_value = json.dumps(payload)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup()
        result = cr.review_pr("myorg", "myrepo", 42, "https://actions/run/1")
        assert result["score"] == 42
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup()
        cr.review_pr("myorg", "myrepo", 7, "https://actions/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("myorg", "myrepo", 7)

    def test_calls_call_claude_with_diff_in_prompt(self):
        self._setup()
        cr.review_pr("myorg", "myrepo", 7, "https://actions/run/1")
        args, kwargs = _shared_stub.call_claude.call_args
        # Second positional arg is the user prompt
        assert "diff" in args[1].lower()

    def test_posts_pr_comment(self):
        self._setup()
        cr.review_pr("myorg", "myrepo", 42, "https://actions/run/1")
        assert _shared_stub.post_pr_comment.called
        call_args = _shared_stub.post_pr_comment.call_args
        assert call_args[0][0] == "myorg"
        assert call_args[0][1] == "myrepo"
        assert call_args[0][2] == 42

    def test_comment_contains_score(self):
        self._setup()
        cr.review_pr("myorg", "myrepo", 42, "https://actions/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "42" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup()
        cr.review_pr("myorg", "myrepo", 42, "https://actions/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_contains_finding_details(self):
        self._setup()
        cr.review_pr("myorg", "myrepo", 42, "https://actions/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment_text
        assert "HIGH" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup(result_payload=MINIMAL_VALID_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://actions/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self):
        self._setup(result_payload=MINIMAL_VALID_RESULT)
        cr.review_pr("myorg", "myrepo", 1, "https://actions/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_comment_contains_positive_observations(self):
        self._setup()
        cr.review_pr("myorg", "myrepo", 42, "https://actions/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good use of type hints." in comment_text

    def test_propagates_get_pr_diff_exception(self):
        _shared_stub.get_pr_diff.side_effect = RuntimeError("Network error")
        with pytest.raises(RuntimeError, match="Network error"):
            cr.review_pr("myorg", "myrepo", 1, "https://actions/run/1")

    def test_propagates_call_claude_exception(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.side_effect = RuntimeError("Claude API error")
        with pytest.raises(RuntimeError, match="Claude API error"):
            cr.review_pr("myorg", "myrepo", 1, "https://actions/run/1")

    def test_raises_when_claude_returns_no_json(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = "Sorry, I cannot help with that."
        with pytest.raises(ValueError):
            cr.review_pr("myorg", "myrepo", 1, "https://actions/run/1")

    def test_pr_number_zero_is_passed_through(self):
        """PR number 0 is unusual but should not crash at the review_pr level."""
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_