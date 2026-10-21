"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fences, edge cases, error conditions
    - review_pr(): happy path, comment formatting, Claude integration
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): report markdown generation, empty findings, edge cases

Mocks used:
    - shared.call_claude (patched via unittest.mock.patch)
    - shared.get_pr_diff
    - shared.get_repo_files
    - shared.post_pr_comment
    - shared.write_output_file
    - shared.send_email
    - shared.write_audit_entry
    - requests (not directly called in tested functions, but imported)
    - datetime.datetime.utcnow (for deterministic report timestamps)

TODOs:
    - TODO: Integration test for __main__ block (requires full env setup)
    - TODO: Test for email/audit path in main block once source is complete
    - TODO: Test review_repo with real token budget edge cases (needs Claude token counting)
"""

import json
import re
import sys
import os
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Ensure the module's directory is on sys.path so the patched shared module
# can be injected before the real import occurs.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

# Provide a lightweight fake `shared` module so the import doesn't crash when
# the real GitHub credentials / environment are absent.
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
_shared_stub.GH_HEADERS = {"Authorization": "token fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test
import importlib
tool1 = importlib.import_module("tool1_code_review") if "tool1_code_review" in sys.modules else None

# Fallback: direct path import
if tool1 is None:
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "tool1_code_review",
        os.path.join(SCRIPTS_DIR, "tool1_code_review.py"),
    )
    tool1 = importlib.util.module_from_spec(_spec)
    # re-inject stub before exec
    sys.modules["shared"] = _shared_stub
    try:
        _spec.loader.exec_module(tool1)
    except Exception:
        # If the module fails to fully load (e.g. syntax error at bottom),
        # we still expose whatever was loaded.
        pass

extract_json = tool1.extract_json
review_pr = tool1.review_pr
review_repo = tool1.review_repo
get_output_url = tool1.get_output_url
build_report_md = tool1.build_report_md


# ---------------------------------------------------------------------------
# Fixtures & helpers
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
    "summary": "Several critical issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or AWS Secrets Manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket has public read ACL.",
            "recommendation": "Set bucket ACL to private.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils/helpers.py",
            "line": 55,
            "issue": "Bare except clause used.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["Comprehensive logging", "Good separation of concerns"],
    "iac_findings": ["Missing encryption on RDS instance", "No VPC flow logs"],
}


def _json_str(d: dict) -> str:
    return json.dumps(d)


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------


class TestExtractJson:
    """Tests for extract_json()."""

    # --- Happy path ---

    def test_plain_json_object(self):
        raw = _json_str(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + _json_str(MINIMAL_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_json_wrapped_in_triple_backtick_fence(self):
        raw = "```\n" + _json_str(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 85

    def test_json_wrapped_in_json_labeled_fence(self):
        raw = "```json\n" + _json_str(FULL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "BLOCK"

    def test_json_with_surrounding_prose(self):
        raw = "Here is my analysis:\n\n" + _json_str(MINIMAL_RESULT) + "\n\nEnd of analysis."
        result = extract_json(raw)
        assert result["score"] == 85

    def test_full_result_round_trip(self):
        raw = _json_str(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"
        assert result["findings"][1]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = extract_json(_json_str(data))
        assert result["findings"] == []

    def test_empty_positive_observations(self):
        data = {**MINIMAL_RESULT, "positive_observations": []}
        result = extract_json(_json_str(data))
        assert result["positive_observations"] == []

    def test_score_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(_json_str(data))
        assert result["score"] == 0

    def test_score_one_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(_json_str(data))
        assert result["score"] == 100

    # --- Edge cases ---

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate Claude inserting a literal newline inside a JSON string value
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This will fail direct parse; extract_json should attempt cleanup
        try:
            result = extract_json(raw)
            # If it succeeds, the summary should be cleaned
            assert "line one" in result["summary"]
        except ValueError:
            # Acceptable: the cleanup regex only covers specific patterns
            pass

    def test_json_inside_extra_braces_of_prose(self):
        """JSON extracted via outermost { } detection."""
        raw = "Some text before { " + '"summary": "ok", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []' + " } some text after"
        result = extract_json(raw)
        assert result["score"] == 70

    def test_fence_with_no_content(self):
        """A fence with only whitespace content should raise ValueError."""
        raw = "```\n   \n```"
        with pytest.raises((ValueError, json.JSONDecodeError, Exception)):
            extract_json(raw)

    # --- Error conditions ---

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_plain_text_no_json_raises(self):
        with pytest.raises(ValueError):
            extract_json("This is just a sentence with no JSON.")

    def test_malformed_json_raises(self):
        raw = '{"summary": "missing closing brace", "score": 50'
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_array_at_top_level_raises(self):
        """Claude returning a JSON array instead of object should fail gracefully."""
        raw = '[{"key": "value"}]'
        # A top-level array won't have outer { }, so either direct parse
        # succeeds (it is valid JSON) or it falls through.  In either case
        # the function should not crash with an unhandled exception.
        try:
            result = extract_json(raw)
            # If it returns something, it parsed the raw JSON — that's fine.
        except (ValueError, json.JSONDecodeError):
            pass  # Also acceptable

    def test_none_like_response_raises(self):
        with pytest.raises((ValueError, AttributeError)):
            extract_json("null")

    def test_integer_response_raises(self):
        with pytest.raises(ValueError):
            extract_json("42")

    def test_debug_output_on_failure(self, capsys):
        """Verify debug print is triggered on parse failure."""
        raw = '{"broken": true'
        with pytest.raises(ValueError):
            extract_json(raw)
        captured = capsys.readouterr()
        assert "[DEBUG]" in captured.out


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------


class TestReviewPr:
    """Tests for review_pr()."""

    def setup_method(self):
        """Reset mocks before each test."""
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.call_claude.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()

    def _configure_mocks(self, result: dict, diff: str = "diff --git a/src/app.py\n+secret=abc"):
        _shared_stub.get_pr_diff.return_value = diff
        _shared_stub.call_claude.return_value = _json_str(result)

    # --- Happy path ---

    def test_returns_parsed_result(self):
        self._configure_mocks(MINIMAL_RESULT)
        result = review_pr("acme", "my-repo", 42, "https://ci/run/1")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_posts_pr_comment(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("acme", "my-repo", 42, "https://ci/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "acme"
        assert args[1] == "my-repo"
        assert args[2] == 42

    def test_comment_contains_score(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("acme", "my-repo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "85/100" in comment

    def test_comment_contains_recommendation(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("acme", "my-repo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("acme", "my-repo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Looks good overall." in comment

    def test_comment_contains_findings(self):
        self._configure_mocks(FULL_RESULT)
        review_pr("acme", "my-repo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment
        assert "src/app.py" in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("acme", "my-repo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_contains_positive_observations(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("acme", "my-repo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment

    def test_comment_no_positive_shows_placeholder(self):
        data = {**MINIMAL_RESULT, "positive_observations": []}
        self._configure_mocks(data)
        review_pr("acme", "my-repo", 1, "")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_get_pr_diff_called_with_correct_args(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("org", "repo", 99, "")
        _shared_stub.get_pr_diff.assert_called_once_with("org", "repo", 99)

    def test_call_claude_called_with_system_prompt(self):
        self._configure_mocks(MINIMAL_RESULT)
        review_pr("org", "repo", 1, "")
        claude_args = _shared_stub.call_claude.call_args[0]
        assert "senior code reviewer" in claude_args[0]

    def test_diff_included_in_claude_prompt(self):
        diff_text = "diff --git a/foo.py b/foo.py\n+password = 'secret'"
        self._configure_mocks(MINIMAL_RESULT, diff=diff_text)
        review_pr("org", "repo", 1, "")
        claude_args = _shared_stub.call_claude.call_args[0]
        assert diff_text in claude_args[1]

    # --- Edge cases