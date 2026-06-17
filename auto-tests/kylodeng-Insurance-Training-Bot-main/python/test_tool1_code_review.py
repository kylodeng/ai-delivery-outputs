"""
Test module for tool1_code_review.py

What is tested:
    - extract_json(): happy path, markdown fences, nested braces, cleaned newlines,
      no-JSON response, malformed JSON, outermost-block extraction
    - review_pr(): happy path, Claude returning bad JSON, post_pr_comment called correctly
    - review_repo(): happy path, content truncation, file filtering
    - get_output_url(): URL construction
    - build_report_md(): full report, empty findings, empty IaC/positive lists,
      missing keys in result dict

Mocks used:
    - shared.call_claude          (patched at tool1_code_review module level)
    - shared.get_pr_diff          (patched at tool1_code_review module level)
    - shared.get_repo_files       (patched at tool1_code_review module level)
    - shared.post_pr_comment      (patched at tool1_code_review module level)
    - shared.write_output_file    (patched at tool1_code_review module level)
    - shared.write_audit_entry    (patched at tool1_code_review module level)
    - shared.send_email           (patched at tool1_code_review module level)
    - requests                    (not directly called in tested functions, no mock needed)

TODOs:
    - TODO: __main__ block requires os.environ setup and full integration; covered by stub only
    - TODO: email_html helper not exercised here; needs email template context
"""

import importlib
import json
import sys
import os
import types
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import succeeds without the
# real module being present in the test environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude         = MagicMock()
    stub.get_repo_files      = MagicMock()
    stub.get_pr_diff         = MagicMock()
    stub.write_output_file   = MagicMock()
    stub.post_pr_comment     = MagicMock()
    stub.send_email          = MagicMock()
    stub.email_html          = MagicMock()
    stub.write_audit_entry   = MagicMock()
    stub.OUTPUT_REPO_OWNER   = "test-owner"
    stub.OUTPUT_REPO         = "test-output-repo"
    stub.GH_HEADERS          = {"Authorization": "token fake"}
    stub.GH_API              = "https://api.github.com"
    return stub


# Insert the stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load as a module even though __main__ guard is present
_spec = importlib.util.spec_from_file_location("tool1_code_review", str(_SCRIPT_PATH))
tool1 = importlib.util.module_from_spec(_spec)
# Patch shared inside the new module namespace before exec
tool1.shared = _shared_stub  # type: ignore[attr-defined]
sys.modules["tool1_code_review"] = tool1
_spec.loader.exec_module(tool1)  # type: ignore[union-attr]

extract_json   = tool1.extract_json
review_pr      = tool1.review_pr
review_repo    = tool1.review_repo
get_output_url = tool1.get_output_url
build_report_md = tool1.build_report_md

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks fine overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
    "score": 55,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/main.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variable instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Missing docstring.",
            "recommendation": "Add a module-level docstring.",
        },
    ],
    "positive_observations": ["Clear naming conventions", "Tests present"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role overly permissive"],
}


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for extract_json()."""

    def test_plain_valid_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_and_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n  "
        result = extract_json(raw)
        assert result["summary"] == "Looks fine overall."

    def test_markdown_fence_triple_backtick(self):
        raw = "```json\n" + json.dumps(FULL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 55

    def test_markdown_fence_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        """JSON buried after some prose text."""
        payload = json.dumps(MINIMAL_RESULT)
        raw = f"Here is my analysis:\n\n{payload}\n\nLet me know if you have questions."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_literal_newline_inside_string_value(self):
        """Simulate Claude inserting a literal newline inside a string value."""
        dirty = '{"summary": "Good\ncode", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # json.loads will fail on this; extract_json should clean it
        result = extract_json(dirty)
        assert result["score"] == 70

    def test_raises_value_error_when_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This response contains no JSON at all.")

    def test_raises_value_error_when_malformed_json(self):
        malformed = '{ "summary": "broken", "score": NOTANUMBER }'
        with pytest.raises(ValueError):
            extract_json(malformed)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_markdown_fence_no_content(self):
        with pytest.raises(ValueError):
            extract_json("```\n```")

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket lacks versioning"

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_extra_text_before_brace(self):
        prefix = "Sure! Here is the JSON:\n"
        raw = prefix + json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_finding_with_null_line(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "Missing docstring.", "recommendation": "Add one."}
        ]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_block_recommendation(self):
        data = {**MINIMAL_RESULT, "merge_recommendation": "BLOCK"}
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "BLOCK"


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:
    """Tests for review_pr()."""

    def _reset_stubs(self):
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.call_claude.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()

    def test_happy_path_approve(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff --git a/foo.py ..."
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        result = review_pr("acme", "myrepo", 42, "https://ci/run/1")

        _shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo", 42)
        _shared_stub.call_claude.assert_called_once()
        _shared_stub.post_pr_comment.assert_called_once()

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "80/100" in comment_text
        assert "APPROVE" in comment_text
        assert result["score"] == 80

    def test_happy_path_request_changes(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        result = review_pr("acme", "myrepo", 7, "https://ci/run/2")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_text
        assert "src/main.py" in comment_text
        assert result["score"] == 55

    def test_findings_section_rendered_in_comment(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        review_pr("acme", "myrepo", 8, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment_text
        assert "Use environment variable instead." in comment_text

    def test_no_findings_shows_placeholder(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        review_pr("acme", "myrepo", 9, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_positive_observations_in_comment(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        review_pr("acme", "myrepo", 10, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment_text

    def test_bad_json_from_claude_raises(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "I cannot provide JSON right now."

        with pytest.raises(ValueError):
            review_pr("acme", "myrepo", 11, "")

    def test_post_pr_comment_called_with_correct_owner_repo_pr(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)

        review_pr("org-x", "repo-y", 99, "")

        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "org-x"
        assert args[1] == "repo-y"
        assert args[2] == 99

    def test_result_returned_contains_all_keys(self):
        self._reset_stubs()
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_RESULT)

        result = review_pr("acme", "myrepo", 1, "")
        for key in ("summary", "score", "merge_recommendation", "findings",
                    "positive_observations", "iac_findings"):
            assert key in result

    def test_line_null_renders_as_na(self):
        self._reset_stubs()
        data = {**FULL_RESULT}
        data["findings"] = [
            {"severity": "LOW", "category": "maintainability",
             "file": "x.py", "line": None,
             "issue": "Missing doc.", "recommendation": "Add doc."}
        ]
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(data)

        review_pr("acme", "myrepo", 12, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "line None" in comment_text or "n/a" in comment_text

    def test_empty_positive_observations_shows_none(self):
        self._reset_stubs()
        data = {**MINIMAL_RESULT, "positive_observations": []}
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(data)

        review_pr("acme", "myrepo", 13, "")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text


# ---------------------------------------------------------------------------
# review_repo tests
# ---------------------------------------------------------------------------

class TestReviewRepo:
    """Tests for review_repo()."""

    def _reset_stubs(self):
        _shared_stub.get_repo_files.reset_mock()
        _shared_stub.call_claude.reset_mock()

    def test_happy_path_returns_dict(self):
        self._reset_stubs()
        _shared_stub.get_repo_files.return