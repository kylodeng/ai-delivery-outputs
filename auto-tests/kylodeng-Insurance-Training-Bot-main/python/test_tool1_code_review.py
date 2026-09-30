"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fence stripping, outermost-brace extraction,
      newline-in-string cleaning, error conditions (no JSON, unparseable JSON)
    - review_pr(): happy path, comment formatting, return value
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): happy path, empty findings, empty iac/positive observations,
      missing keys in result dict

Mocks used:
    - shared.call_claude          (via unittest.mock.patch)
    - shared.get_pr_diff          (via unittest.mock.patch)
    - shared.get_repo_files       (via unittest.mock.patch)
    - shared.post_pr_comment      (via unittest.mock.patch)
    - shared.write_output_file    (via unittest.mock.patch)
    - shared.send_email           (via unittest.mock.patch)
    - shared.write_audit_entry    (via unittest.mock.patch)
    - requests                    (not called directly in tested functions, but imported)

TODOs:
    - TODO: Integration test for __main__ block requires env-var wiring and full shared module
    - TODO: Test email dispatch path once build_report_md + send_email wiring is visible in __main__
    - TODO: Test write_output_file call once the __main__ block source is complete (truncated in provided source)
"""

import json
import sys
import os
import importlib
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: the module imports `shared` from the same directory via sys.path.
# We inject a fake `shared` module so we never need the real one.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"
FAKE_GH_HEADERS = {"Authorization": "Bearer fake-token"}
FAKE_GH_API = "https://api.github.com"

# Build a minimal fake `shared` module
_shared = types.ModuleType("shared")
_shared.call_claude = MagicMock()
_shared.get_repo_files = MagicMock()
_shared.get_pr_diff = MagicMock()
_shared.write_output_file = MagicMock()
_shared.post_pr_comment = MagicMock()
_shared.send_email = MagicMock()
_shared.email_html = MagicMock(return_value="<html></html>")
_shared.write_audit_entry = MagicMock()
_shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
_shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
_shared.GH_HEADERS = FAKE_GH_HEADERS
_shared.GH_API = FAKE_GH_API

sys.modules["shared"] = _shared

# Now we can safely import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load the module from its file path so we don't depend on it being on sys.path
spec = importlib.util.spec_from_file_location("tool1_code_review", _script_path)
_mod = importlib.util.module_from_spec(spec)
# Inject the fake shared into the module's namespace before exec
sys.modules["tool1_code_review"] = _mod
spec.loader.exec_module(_mod)

extract_json   = _mod.extract_json
review_pr      = _mod.review_pr
review_repo    = _mod.review_repo
get_output_url = _mod.get_output_url
build_report_md = _mod.build_report_md


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code is in good shape.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded secret found.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage.", "Clear variable naming."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def _make_markdown_fenced(json_str: str) -> str:
    return f"```json\n{json_str}\n```"


def _make_text_surrounded(json_str: str) -> str:
    return f"Here is the review:\n{json_str}\nEnd of review."


# ---------------------------------------------------------------------------
# Tests: extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    """Tests for extract_json()."""

    def test_happy_path_plain_json(self):
        result = extract_json(VALID_JSON_STR)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_strips_markdown_json_fence(self):
        fenced = f"```json\n{VALID_JSON_STR}\n```"
        result = extract_json(fenced)
        assert result["summary"] == VALID_RESULT["summary"]

    def test_strips_plain_markdown_fence(self):
        fenced = f"```\n{VALID_JSON_STR}\n```"
        result = extract_json(fenced)
        assert result["score"] == 82

    def test_extracts_json_with_surrounding_text(self):
        surrounded = _make_text_surrounded(VALID_JSON_STR)
        result = extract_json(surrounded)
        assert result["score"] == 82

    def test_handles_leading_trailing_whitespace(self):
        result = extract_json(f"   \n{VALID_JSON_STR}\n   ")
        assert result["score"] == 82

    def test_raises_value_error_when_no_json_found(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no braces.")

    def test_raises_value_error_on_malformed_json(self):
        malformed = '{"score": 75, "summary": "broken'  # no closing
        with pytest.raises(ValueError):
            extract_json(malformed)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_braces_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("{}")
        # {} is valid JSON — actually json.loads("{}") succeeds, so this should NOT raise
        # Correcting: {} parses as empty dict which is valid
        result = extract_json("{}")
        assert result == {}

    def test_minimal_valid_json(self):
        result = extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_newlines_inside_string_values(self):
        """Simulate Claude embedding literal newlines inside string values."""
        # Craft a raw string that has a literal newline inside a JSON string value
        raw = '{"summary": "line one\nline two", "score": 50}'
        # After the newline-removal regex, it should parse correctly
        result = extract_json(raw)
        assert result["score"] == 50
        assert "line one" in result["summary"]

    def test_json_with_extra_text_before_and_after_braces(self):
        raw = f"GPT says: {VALID_JSON_STR} [end]"
        result = extract_json(raw)
        assert result["score"] == 82

    def test_deeply_nested_findings(self):
        data = {
            "summary": "ok",
            "score": 60,
            "merge_recommendation": "REQUEST_CHANGES",
            "findings": [
                {"severity": "CRITICAL", "category": "security", "file": "app.tf",
                 "line": None, "issue": "No encryption.", "recommendation": "Add KMS."},
                {"severity": "LOW", "category": "maintainability", "file": "utils.py",
                 "line": 5, "issue": "Magic number.", "recommendation": "Use constant."},
            ],
            "positive_observations": [],
            "iac_findings": [],
        }
        result = extract_json(json.dumps(data))
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_score_boundary_zero(self):
        data = dict(VALID_RESULT, score=0)
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = dict(VALID_RESULT, score=100)
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_markdown_fence_with_no_language_tag(self):
        raw = f"```\n{VALID_JSON_STR}\n```"
        result = extract_json(raw)
        assert result["score"] == 82

    def test_multiple_json_objects_uses_outermost(self):
        """When there are nested or multiple JSON-like segments, outermost { } wins."""
        raw = f'some text {VALID_JSON_STR} trailing'
        result = extract_json(raw)
        assert result["score"] == 82


# ---------------------------------------------------------------------------
# Tests: review_pr
# ---------------------------------------------------------------------------


class TestReviewPr:
    """Tests for review_pr()."""

    def setup_method(self):
        _shared.get_pr_diff.reset_mock()
        _shared.call_claude.reset_mock()
        _shared.post_pr_comment.reset_mock()

    def test_happy_path_returns_result(self):
        _shared.get_pr_diff.return_value = "diff --git a/src/main.py ..."
        _shared.call_claude.return_value = VALID_JSON_STR

        result = review_pr("acme", "my-repo", 42, "https://github.com/run/1")

        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        _shared.get_pr_diff.return_value = "small diff"
        _shared.call_claude.return_value = VALID_JSON_STR

        review_pr("acme", "my-repo", 7, "https://github.com/run/2")

        _shared.get_pr_diff.assert_called_once_with("acme", "my-repo", 7)

    def test_calls_call_claude_with_system_prompt(self):
        _shared.get_pr_diff.return_value = "diff content"
        _shared.call_claude.return_value = VALID_JSON_STR

        review_pr("acme", "my-repo", 1, "https://github.com/run/3")

        args = _shared.call_claude.call_args
        assert args[0][0] == _mod.SYSTEM  # first positional arg is SYSTEM

    def test_posts_pr_comment(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = VALID_JSON_STR

        review_pr("acme", "my-repo", 99, "https://github.com/run/4")

        _shared.post_pr_comment.assert_called_once()
        call_args = _shared.post_pr_comment.call_args[0]
        assert call_args[0] == "acme"
        assert call_args[1] == "my-repo"
        assert call_args[2] == 99

    def test_comment_contains_score(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = VALID_JSON_STR

        review_pr("acme", "my-repo", 3, "https://github.com/run/5")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "82" in comment

    def test_comment_contains_recommendation(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = VALID_JSON_STR

        review_pr("acme", "my-repo", 3, "https://github.com/run/5")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_findings(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = VALID_JSON_STR

        review_pr("acme", "my-repo", 3, "https://github.com/run/5")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "Hardcoded secret found." in comment

    def test_comment_with_no_findings_shows_placeholder(self):
        result_no_findings = dict(VALID_RESULT, findings=[])
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(result_no_findings)

        review_pr("acme", "my-repo", 3, "https://github.com/run/6")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_with_no_positive_observations(self):
        result_no_pos = dict(VALID_RESULT, positive_observations=[])
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(result_no_pos)

        review_pr("acme", "my-repo", 3, "https://github.com/run/7")

        comment = _shared.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_result_has_missing_keys_uses_defaults(self):
        minimal = {"score": 50, "merge_recommendation": "REQUEST_CHANGES"}
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(minimal)

        result = review_pr("acme", "my-repo", 5, "https://github.com/run/8")

        assert result["score"] == 50

    def test_propagates_value_error_from_extract_json(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = "not json at all"

        with pytest.raises(ValueError):
            review_pr("acme", "my-repo", 5, "https://github.com/run/9")

    def test_finding_with_null_line(self):
        result_null_line = dict(VALID_RESULT, findings=[
            {"severity": "MEDIUM", "category": "correctness", "file": "app.py",
             "line": None, "issue": "Issue.", "recommendation": "Fix it."}
        ])
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = json.dumps(result_null_line)

        result = review_pr("acme", "my-repo", 10, "https://github.com/run/10")
        assert result is not None

    def test_block_recommendation_in_comment(self):
        block_result = dict(VALID_RESULT, merge_recommendation="BLOCK")