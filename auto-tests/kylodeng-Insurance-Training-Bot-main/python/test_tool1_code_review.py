"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested JSON extraction, newline cleanup, error cases
- review_pr: happy path, comment formatting, Claude/API interaction
- review_repo: happy path, content truncation, file filtering interaction
- get_output_url: URL construction
- build_report_md: happy path, empty findings, missing keys, all severity levels

Mocks used:
- shared.call_claude (patched via sys.modules)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- requests (not directly used in tested functions but imported)
- datetime.datetime.utcnow (for deterministic timestamps)

TODOs:
- TODO: Integration tests for __main__ block require full env var setup
- TODO: Tests for write_audit_entry and send_email calls in main block need entrypoint refactor
- TODO: edge case when get_repo_files returns binary/non-utf8 content
"""

import sys
import os
import json
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: create a fake `shared` module before importing the target module
# ---------------------------------------------------------------------------

def _make_shared_module():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="{}")
    shared.get_repo_files = MagicMock(return_value={})
    shared.get_pr_diff = MagicMock(return_value="diff content")
    shared.write_output_file = MagicMock()
    shared.post_pr_comment = MagicMock()
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer fake"}
    shared.GH_API = "https://api.github.com"
    return shared


# Inject the fake shared module BEFORE importing the unit under test
_shared_mod = _make_shared_module()
sys.modules["shared"] = _shared_mod

# Now import the module under test
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load via spec so we can reload cleanly; fall back to direct import if path wrong
try:
    _spec = importlib.util.spec_from_file_location("tool1_code_review", str(_SCRIPT_PATH))
    tool1 = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(tool1)
except (FileNotFoundError, AttributeError):
    # Running from repo root may differ; try package-style import
    import importlib as _il
    tool1 = _il.import_module("tool1_code_review")

extract_json = tool1.extract_json
review_pr = tool1.review_pr
review_repo = tool1.review_repo
get_output_url = tool1.get_output_url
build_report_md = tool1.build_report_md


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks fine",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several critical issues found",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key",
            "recommendation": "Use environment variables or a secrets manager",
        },
        {
            "severity": "HIGH",
            "category": "performance",
            "file": "lib/utils.py",
            "line": None,
            "issue": "Unbounded loop may cause timeout",
            "recommendation": "Add iteration limit",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "infra/main.tf",
            "line": 55,
            "issue": "Missing resource tags",
            "recommendation": "Add mandatory cost-allocation tags",
        },
        {
            "severity": "LOW",
            "category": "correctness",
            "file": "tests/test_foo.py",
            "line": 3,
            "issue": "Unused import",
            "recommendation": "Remove unused import",
        },
    ],
    "positive_observations": ["CI pipeline is well structured", "All secrets are externalized"],
    "iac_findings": ["S3 bucket lacks server-side encryption", "IAM role is overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared mock call counts before each test."""
    _shared_mod.call_claude.reset_mock()
    _shared_mod.get_repo_files.reset_mock()
    _shared_mod.get_pr_diff.reset_mock()
    _shared_mod.write_output_file.reset_mock()
    _shared_mod.post_pr_comment.reset_mock()
    _shared_mod.send_email.reset_mock()
    _shared_mod.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:

    # --- Happy path: clean JSON string ---

    def test_clean_json_object(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_clean_json_with_whitespace_padding(self):
        raw = "  \n  " + json.dumps(MINIMAL_RESULT) + "  \n  "
        result = extract_json(raw)
        assert result["summary"] == "Looks fine"

    # --- Markdown fence stripping ---

    def test_triple_backtick_fence_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_triple_backtick_fence_no_language(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_fence_with_preamble_text(self):
        payload = json.dumps({"score": 55, "summary": "ok", "merge_recommendation": "APPROVE",
                               "findings": [], "positive_observations": [], "iac_findings": []})
        raw = "Here is my review:\n```json\n" + payload + "\n```\nHope this helps."
        result = extract_json(raw)
        assert result["score"] == 55

    # --- Extraction via brace scanning ---

    def test_json_embedded_in_prose(self):
        payload = json.dumps(MINIMAL_RESULT)
        raw = f"Sure! Here you go: {payload} Let me know if you need anything else."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_with_leading_prose_no_fence(self):
        payload = json.dumps({"score": 10, "summary": "bad", "merge_recommendation": "BLOCK",
                               "findings": [], "positive_observations": [], "iac_findings": []})
        raw = "Certainly, here is the JSON: " + payload
        result = extract_json(raw)
        assert result["score"] == 10

    # --- Newline-inside-string cleanup ---

    def test_newlines_inside_string_values_cleaned(self):
        # Manually craft a JSON with a literal newline inside a string value
        broken = '{"summary": "line one\nline two", "score": 70}'
        result = extract_json(broken)
        assert "line one" in result["summary"]

    # --- Full result with all severities ---

    def test_full_result_parsed(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 4
        assert result["findings"][0]["severity"] == "CRITICAL"
        assert result["iac_findings"][0] == "S3 bucket lacks server-side encryption"

    # --- Edge cases ---

    def test_empty_findings_list(self):
        payload = {**MINIMAL_RESULT, "findings": []}
        result = extract_json(json.dumps(payload))
        assert result["findings"] == []

    def test_score_zero(self):
        payload = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(payload))
        assert result["score"] == 0

    def test_score_hundred(self):
        payload = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(payload))
        assert result["score"] == 100

    def test_line_null(self):
        payload = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "correctness", "file": "a.py",
             "line": None, "issue": "x", "recommendation": "y"}
        ]}
        result = extract_json(json.dumps(payload))
        assert result["findings"][0]["line"] is None

    # --- Error conditions ---

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no JSON at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_incomplete_json_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json('{"score": 80, "summary": "incomplete"')

    def test_array_only_raises_value_error(self):
        # Valid JSON but not an object — brace scan should fail
        with pytest.raises(ValueError):
            extract_json('["just", "an", "array"]')

    def test_malformed_json_inside_braces_raises(self):
        with pytest.raises(ValueError):
            extract_json("{this is not valid json}")

    def test_multiple_json_objects_takes_outermost(self):
        inner = '{"a": 1}'
        outer = f'{{"wrapper": {inner}, "score": 99}}'
        result = extract_json(outer)
        assert result["score"] == 99

    def test_deeply_nested_json(self):
        payload = {**MINIMAL_RESULT, "findings": [
            {"severity": "HIGH", "category": "security", "file": "deep/nested/path/file.py",
             "line": 999, "issue": "Issue text", "recommendation": "Fix text"}
        ]}
        result = extract_json(json.dumps(payload))
        assert result["findings"][0]["file"] == "deep/nested/path/file.py"

    def test_unicode_in_values(self):
        payload = {**MINIMAL_RESULT, "summary": "Résumé with unicode: 日本語"}
        result = extract_json(json.dumps(payload))
        assert "日本語" in result["summary"]

    def test_json_with_extra_trailing_text_after_brace(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nSome trailing commentary."
        result = extract_json(raw)
        assert result["score"] == 80


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:

    def _setup_claude(self, result_dict=None):
        payload = result_dict or FULL_RESULT
        _shared_mod.call_claude.return_value = json.dumps(payload)
        _shared_mod.get_pr_diff.return_value = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new"

    def test_calls_get_pr_diff(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        _shared_mod.get_pr_diff.assert_called_once_with("myorg", "myrepo", 42)

    def test_calls_call_claude_with_diff(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        _shared_mod.call_claude.assert_called_once()
        call_args = _shared_mod.call_claude.call_args
        assert "Review this pull request diff" in call_args[0][1]

    def test_posts_pr_comment(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        _shared_mod.post_pr_comment.assert_called_once()
        _, kwargs = _shared_mod.post_pr_comment.call_args[0], _shared_mod.post_pr_comment.call_args
        args = _shared_mod.post_pr_comment.call_args[0]
        assert args[0] == "myorg"
        assert args[1] == "myrepo"
        assert args[2] == 42

    def test_comment_contains_score(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        comment = _shared_mod.post_pr_comment.call_args[0][3]
        assert "42" in comment

    def test_comment_contains_recommendation(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        comment = _shared_mod.post_pr_comment.call_args[0][3]
        assert "BLOCK" in comment

    def test_comment_contains_summary(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        comment = _shared_mod.post_pr_comment.call_args[0][3]
        assert "Several critical issues found" in comment

    def test_comment_contains_findings(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        comment = _shared_mod.post_pr_comment.call_args[0][3]
        assert "Hardcoded AWS secret key" in comment

    def test_comment_contains_positive_observations(self):
        self._setup_claude()
        review_pr("myorg", "myrepo", 42, "https://github.com/actions/run/1")
        comment = _shared_mod.post_pr_comment.call_args[0][3]
        assert "CI pipeline is well structured" in comment

    def test_comment_no_findings_shows_placeholder(self):
        payload = {**MINIMAL_RESULT}
        self._