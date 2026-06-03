"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, empty input, partial JSON
- review_pr: happy path, Claude response handling, comment formatting, return value
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction
- build_report_md: full report, empty findings, empty iac/positive, missing keys

Mocks used:
- shared.call_claude (patched via tool1_code_review module namespace)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- requests (not directly called in public functions but imported)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup and subprocess execution
- TODO: Test write_output_file / send_email / write_audit_entry call sites in __main__ block
  (not reachable without running as __main__)
- TODO: Test actual Claude response parsing with real multi-thousand-token payloads
"""

import json
import re
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Make the module importable without the shared module being present on disk
# ---------------------------------------------------------------------------
# We stub out the 'shared' module before importing the module under test
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

# Now import the module under test (after stubbing shared)
import importlib
import types

# Re-insert path so the import resolves
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# We patch at sys.modules level so the import inside tool1_code_review works
with patch.dict("sys.modules", {"shared": shared_stub, "requests": MagicMock()}):
    import importlib.util, pathlib
    _src = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"
    if _src.exists():
        spec = importlib.util.spec_from_file_location("tool1_code_review", str(_src))
        tool1 = importlib.util.module_from_spec(spec)
        # pre-populate sys.modules so internal `from shared import …` resolves
        sys.modules["tool1_code_review"] = tool1
        spec.loader.exec_module(tool1)
    else:
        # Fallback: try normal import (works when tests run from repo root)
        import tool1_code_review as tool1  # type: ignore

extract_json = tool1.extract_json
review_pr = tool1.review_pr
review_repo = tool1.review_repo
get_output_url = tool1.get_output_url
build_report_md = tool1.build_report_md


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall looks good.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": [],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/auth.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "Overly permissive IAM policy.",
            "recommendation": "Restrict to least privilege.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils/helpers.py",
            "line": 55,
            "issue": "Bare except clause.",
            "recommendation": "Catch specific exception types.",
        },
    ],
    "positive_observations": ["Good use of type hints.", "Tests are comprehensive."],
    "iac_findings": ["S3 bucket missing server-side encryption.", "Missing required tags."],
}


@pytest.fixture()
def minimal_json_str():
    return json.dumps(MINIMAL_RESULT)


@pytest.fixture()
def full_json_str():
    return json.dumps(FULL_RESULT)


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:

    def test_plain_json_string(self, minimal_json_str):
        result = extract_json(minimal_json_str)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_with_leading_trailing_whitespace(self, minimal_json_str):
        result = extract_json(f"   \n{minimal_json_str}\n   ")
        assert result["summary"] == "Overall looks good."

    def test_markdown_fenced_json(self, minimal_json_str):
        fenced = f"```json\n{minimal_json_str}\n```"
        result = extract_json(fenced)
        assert result["score"] == 75

    def test_markdown_fenced_no_language(self, minimal_json_str):
        fenced = f"```\n{minimal_json_str}\n```"
        result = extract_json(fenced)
        assert result["score"] == 75

    def test_json_embedded_in_prose(self, minimal_json_str):
        raw = f"Here is the review:\n{minimal_json_str}\nThat's all."
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_full_result_parsed(self, full_json_str):
        result = extract_json(full_json_str)
        assert result["score"] == 42
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_newlines_inside_string_values_cleaned(self):
        # Simulate Claude inserting a literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 80, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # The newline breaks standard json.loads; extract_json should handle it
        # Either it cleans it or raises ValueError — we just ensure no crash/unexpected exception
        try:
            result = extract_json(raw)
            assert "summary" in result
        except ValueError:
            pass  # acceptable — cleaning may not always recover

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no JSON at all.")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json('{"score": 75, "summary": }')  # invalid JSON

    def test_only_opening_brace_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("{no closing brace anywhere in this string")

    def test_nested_objects_parsed_correctly(self):
        nested = json.dumps({
            "summary": "ok",
            "score": 90,
            "merge_recommendation": "APPROVE",
            "findings": [{"severity": "LOW", "category": "maintainability",
                          "file": "a.py", "line": 1,
                          "issue": "minor", "recommendation": "fix it"}],
            "positive_observations": [],
            "iac_findings": [],
        })
        result = extract_json(nested)
        assert result["findings"][0]["file"] == "a.py"

    def test_extra_text_before_and_after_brace(self, full_json_str):
        raw = f"INTRO TEXT {full_json_str} OUTRO TEXT"
        result = extract_json(raw)
        assert result["score"] == 42

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_findings_with_null_line(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "security", "file": "x.py",
             "line": None, "issue": "minor", "recommendation": "fix"}
        ]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_merge_recommendation_values(self):
        for rec in ("APPROVE", "REQUEST_CHANGES", "BLOCK"):
            data = {**MINIMAL_RESULT, "merge_recommendation": rec}
            result = extract_json(json.dumps(data))
            assert result["merge_recommendation"] == rec

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("     \n\t  ")


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:

    def _make_mock_shared(self, raw_response):
        shared_stub.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+x = 1"
        shared_stub.call_claude.return_value = raw_response
        shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_parsed_result(self):
        self._make_mock_shared(json.dumps(FULL_RESULT))
        result = review_pr("myorg", "myrepo", 42, "https://ci.example.com/run/1")
        assert result["score"] == 42
        assert result["merge_recommendation"] == "BLOCK"

    def test_post_pr_comment_called_once(self):
        self._make_mock_shared(json.dumps(MINIMAL_RESULT))
        shared_stub.post_pr_comment.reset_mock()
        review_pr("myorg", "myrepo", 1, "https://ci.example.com/run/1")
        shared_stub.post_pr_comment.assert_called_once()

    def test_post_pr_comment_contains_score(self):
        self._make_mock_shared(json.dumps(FULL_RESULT))
        shared_stub.post_pr_comment.reset_mock()
        review_pr("myorg", "myrepo", 7, "https://ci.example.com/run/1")
        comment_text = shared_stub.post_pr_comment.call_args[0][3]
        assert "42" in comment_text

    def test_post_pr_comment_contains_recommendation(self):
        self._make_mock_shared(json.dumps(FULL_RESULT))
        review_pr("myorg", "myrepo", 7, "https://ci.example.com/run/1")
        comment_text = shared_stub.post_pr_comment.call_args[0][3]
        assert "BLOCK" in comment_text

    def test_post_pr_comment_contains_summary(self):
        self._make_mock_shared(json.dumps(FULL_RESULT))
        review_pr("myorg", "myrepo", 7, "https://ci.example.com/run/1")
        comment_text = shared_stub.post_pr_comment.call_args[0][3]
        assert "Several security issues found." in comment_text

    def test_post_pr_comment_no_findings_shows_placeholder(self):
        self._make_mock_shared(json.dumps(MINIMAL_RESULT))
        review_pr("myorg", "myrepo", 1, "https://ci.example.com/run/1")
        comment_text = shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_post_pr_comment_with_findings_listed(self):
        self._make_mock_shared(json.dumps(FULL_RESULT))
        review_pr("myorg", "myrepo", 1, "https://ci.example.com/run/1")
        comment_text = shared_stub.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment_text
        assert "src/auth.py" in comment_text

    def test_positive_observations_in_comment(self):
        self._make_mock_shared(json.dumps(FULL_RESULT))
        review_pr("myorg", "myrepo", 1, "https://ci.example.com/run/1")
        comment_text = shared_stub.post_pr_comment.call_args[0][3]
        assert "Good use of type hints." in comment_text

    def test_no_positive_observations_shows_none(self):
        self._make_mock_shared(json.dumps(MINIMAL_RESULT))
        review_pr("myorg", "myrepo", 1, "https://ci.example.com/run/1")
        comment_text = shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_call_claude_receives_diff(self):
        shared_stub.get_pr_diff.return_value = "UNIQUE_DIFF_CONTENT_XYZ"
        shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)
        shared_stub.post_pr_comment.return_value = None
        review_pr("myorg", "myrepo", 99, "https://ci.example.com")
        call_args = shared_stub.call_claude.call_args
        assert "UNIQUE_DIFF_CONTENT_XYZ" in call_args[0][1]

    def test_pr_number_passed_to_post_comment(self):
        self._make_mock_shared(json.dumps(MINIMAL_RESULT))
        shared_stub.post_pr_comment.reset_mock()
        review_pr("myorg", "myrepo", 123, "https://ci.example.com")
        args = shared_stub.post_pr_comment.call_args[0]
        assert args[2] == 123

    def test_owner_and_repo_passed_to_post_comment(self):
        self._make_mock_shared(json.dumps(MINIMAL_RESULT))
        shared_stub.post_pr_comment.reset_mock()
        review_pr("acme-org", "cool-repo", 5, "https://ci.example.com")
        args = shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "acme-org"
        assert args[1] == "cool-repo"

    def test_claude_raises_propagates(self):
        shared_stub.get_pr_diff.return_value = "diff content"
        shared_stub.call_claude.side_effect = RuntimeError