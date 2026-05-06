"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, boundary/edge inputs
- review_pr: happy path, Claude response handling, comment posting, return value
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction with various owner/repo/label combinations
- build_report_md: happy path, missing fields, empty findings/iac/observations,
  multiple findings, special characters in content

Mocks used:
- shared.call_claude (patched via tool1_code_review module)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- datetime.datetime (for deterministic timestamp in build_report_md)

TODOs:
- TODO: Integration test for __main__ block requires full env var setup and live GitHub token
- TODO: Test email dispatch path in __main__ once send_email signature is confirmed
- TODO: Verify exact token budget behaviour for review_repo when content > 20000 chars
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call
import datetime

# ---------------------------------------------------------------------------
# Path setup – mirror what the source file does
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# We import the module under test; shared dependencies are mocked at module level
import importlib

# Patch shared imports before importing the module under test so that
# optional heavy dependencies don't need to be installed.
SHARED_ATTRS = [
    "call_claude", "get_repo_files", "get_pr_diff",
    "write_output_file", "post_pr_comment",
    "send_email", "email_html", "write_audit_entry",
    "OUTPUT_REPO_OWNER", "OUTPUT_REPO", "GH_HEADERS", "GH_API",
]

shared_mock = MagicMock()
shared_mock.OUTPUT_REPO_OWNER = "test-output-owner"
shared_mock.OUTPUT_REPO = "test-output-repo"
shared_mock.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_mock.GH_API = "https://api.github.com"

with patch.dict("sys.modules", {"shared": shared_mock, "requests": MagicMock()}):
    import tool1_code_review as t1


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_JSON = {
    "summary": "Code looks fine overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several high-severity issues found.",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Bare except clause.",
            "recommendation": "Catch specific exceptions.",
        },
    ],
    "positive_observations": ["Consistent naming conventions", "Good docstrings"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role overly permissive"],
}


@pytest.fixture()
def valid_json_str():
    return json.dumps(MINIMAL_VALID_JSON)


@pytest.fixture()
def full_result():
    return dict(FULL_RESULT)


# ---------------------------------------------------------------------------
# extract_json – happy path
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:
    def test_plain_json_object(self, valid_json_str):
        result = t1.extract_json(valid_json_str)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_leading_trailing_whitespace(self, valid_json_str):
        result = t1.extract_json(f"   \n{valid_json_str}\n   ")
        assert result["summary"] == "Code looks fine overall."

    def test_json_with_surrounding_text(self, valid_json_str):
        raw = f"Here is the result:\n{valid_json_str}\nEnd of response."
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fences_backtick3(self, valid_json_str):
        raw = f"```\n{valid_json_str}\n```"
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_markdown_fences_with_language(self, valid_json_str):
        raw = f"```json\n{valid_json_str}\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_full_result_object(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2

    def test_newline_inside_string_value_cleaned(self):
        # Simulate Claude inserting a literal newline inside a JSON string
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # Direct json.loads will fail; extract_json should recover
        result = t1.extract_json(raw)
        assert result["score"] == 50

    def test_empty_findings_list(self):
        obj = {**MINIMAL_VALID_JSON, "findings": []}
        result = t1.extract_json(json.dumps(obj))
        assert result["findings"] == []

    def test_findings_with_null_line(self):
        obj = {
            **MINIMAL_VALID_JSON,
            "findings": [
                {"severity": "LOW", "category": "correctness",
                 "file": "a.py", "line": None,
                 "issue": "Minor issue.", "recommendation": "Fix it."}
            ]
        }
        result = t1.extract_json(json.dumps(obj))
        assert result["findings"][0]["line"] is None


# ---------------------------------------------------------------------------
# extract_json – edge cases / error conditions
# ---------------------------------------------------------------------------

class TestExtractJsonEdgeCases:
    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("   \n\t  ")

    def test_raises_on_plain_text_no_braces(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("Just some text without any JSON.")

    def test_raises_on_malformed_json(self):
        with pytest.raises(ValueError):
            t1.extract_json('{"score": 50, "summary": "Missing closing brace"')

    def test_raises_on_array_only(self):
        # An array at the top level with no surrounding object
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("[1, 2, 3]")

    def test_markdown_fence_with_bad_json_raises(self):
        raw = "```\n{bad json here\n```"
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_multiple_json_objects_returns_outermost(self):
        # Should extract the outermost { ... } block
        inner = json.dumps({"inner": True})
        outer = json.dumps({"outer": True, "nested": {"inner": True}})
        result = t1.extract_json(f"Prefix {outer} suffix")
        assert result["outer"] is True

    def test_score_boundary_zero(self):
        obj = {**MINIMAL_VALID_JSON, "score": 0}
        result = t1.extract_json(json.dumps(obj))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        obj = {**MINIMAL_VALID_JSON, "score": 100}
        result = t1.extract_json(json.dumps(obj))
        assert result["score"] == 100

    def test_unicode_content(self):
        obj = {**MINIMAL_VALID_JSON, "summary": "Ünïcödé chäracters present."}
        result = t1.extract_json(json.dumps(obj))
        assert "Ünïcödé" in result["summary"]

    def test_extra_keys_preserved(self):
        obj = {**MINIMAL_VALID_JSON, "extra_key": "extra_value"}
        result = t1.extract_json(json.dumps(obj))
        assert result["extra_key"] == "extra_value"


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:
    def _make_mocks(self, result_dict=None):
        result_dict = result_dict or MINIMAL_VALID_JSON
        shared_mock.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+print('hello')"
        shared_mock.call_claude.return_value = json.dumps(result_dict)
        shared_mock.post_pr_comment.return_value = None
        return result_dict

    def test_returns_parsed_result(self):
        self._make_mocks()
        result = t1.review_pr("owner", "repo", 42, "https://run.url")
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff(self):
        self._make_mocks()
        t1.review_pr("owner", "repo", 7, "https://run.url")
        shared_mock.get_pr_diff.assert_called_with("owner", "repo", 7)

    def test_calls_call_claude_with_diff(self):
        self._make_mocks()
        t1.review_pr("owner", "repo", 7, "https://run.url")
        call_args = shared_mock.call_claude.call_args
        assert "Review this pull request diff" in call_args[0][1]

    def test_calls_post_pr_comment(self):
        self._make_mocks()
        t1.review_pr("owner", "repo", 7, "https://run.url")
        shared_mock.post_pr_comment.assert_called_once()
        args = shared_mock.post_pr_comment.call_args[0]
        assert args[0] == "owner"
        assert args[1] == "repo"
        assert args[2] == 7

    def test_comment_contains_score(self):
        self._make_mocks()
        t1.review_pr("owner", "repo", 7, "https://run.url")
        comment_body = shared_mock.post_pr_comment.call_args[0][3]
        assert "80" in comment_body

    def test_comment_contains_recommendation(self):
        self._make_mocks()
        t1.review_pr("owner", "repo", 7, "https://run.url")
        comment_body = shared_mock.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_body

    def test_comment_contains_summary(self):
        self._make_mocks()
        t1.review_pr("owner", "repo", 7, "https://run.url")
        comment_body = shared_mock.post_pr_comment.call_args[0][3]
        assert "Code looks fine overall." in comment_body

    def test_comment_no_findings_shows_placeholder(self):
        self._make_mocks({**MINIMAL_VALID_JSON, "findings": []})
        t1.review_pr("owner", "repo", 7, "https://run.url")
        comment_body = shared_mock.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_body

    def test_comment_with_findings(self):
        self._make_mocks(FULL_RESULT)
        t1.review_pr("owner", "repo", 7, "https://run.url")
        comment_body = shared_mock.post_pr_comment.call_args[0][3]
        assert "src/main.py" in comment_body
        assert "HIGH" in comment_body

    def test_comment_finding_with_null_line_shows_na(self):
        result = {
            **MINIMAL_VALID_JSON,
            "findings": [{
                "severity": "LOW", "category": "correctness",
                "file": "a.py", "line": None,
                "issue": "Minor.", "recommendation": "Fix."
            }]
        }
        self._make_mocks(result)
        t1.review_pr("owner", "repo", 7, "https://run.url")
        comment_body = shared_mock.post_pr_comment.call_args[0][3]
        assert "n/a" in comment_body

    def test_comment_no_positive_observations_shows_none(self):
        self._make_mocks({**MINIMAL_VALID_JSON, "positive_observations": []})
        t1.review_pr("owner", "repo", 7, "https://run.url")
        comment_body = shared_mock.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_body

    def test_returns_full_result_dict(self):
        self._make_mocks(FULL_RESULT)
        result = t1.review_pr("owner", "repo", 1, "https://run.url")
        assert result["iac_findings"] == ["S3 bucket missing encryption", "IAM role overly permissive"]

    def test_pr_number_zero_edge(self):
        """PR number 0 is unusual but should not crash the function."""
        self._make_mocks()
        result = t1.review_pr("owner", "repo", 0, "https://run.url")
        assert "score" in result

    def test_large_pr_number(self):
        self._make_mocks()
        result = t1.review_pr("owner", "repo", 99999, "https://run.url")
        assert "score" in result


# ---------------------------------------------------------------------------
# review_repo
# ---------------------------------------------------------------------------

class TestReviewRepo:
    def _setup(self, result_dict=None):
        result_dict = result_dict or MINIMAL_VALID_JSON
        shared_mock.get_repo_files.return_value = {
            "src/main.py": "print('hello world')" * 100,
            "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
            "README.md": "# Project",
        }
        shared_mock.call_claude.return_value = json.dumps(result_dict)
        return result_dict

    def test_returns_parsed_result(self):
        self._setup()
        result = t1.review_repo("owner", "repo", "https://run.url")
        assert result["score"] == 80

    def test_calls_get_repo_files_with_extensions(self):
        self._setup()
        t1.review_repo("owner", "repo", "https://run.url")