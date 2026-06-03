"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, whitespace-only input
- review_pr(): happy path, Claude response handling, comment posting, return value
- review_repo(): happy path, file truncation behaviour, token budget
- get_output_url(): URL construction with various owner/repo/label combinations
- build_report_md(): full report structure, empty findings, missing keys, IaC/positive sections

Mocks used:
- shared.call_claude          (patched at tool1_code_review module level)
- shared.get_pr_diff          (patched at tool1_code_review module level)
- shared.get_repo_files       (patched at tool1_code_review module level)
- shared.post_pr_comment      (patched at tool1_code_review module level)
- shared.write_output_file    (patched at tool1_code_review module level)
- shared.write_audit_entry    (patched at tool1_code_review module level)
- shared.send_email           (patched at tool1_code_review module level)
- shared.email_html           (patched at tool1_code_review module level)
- datetime.datetime.utcnow    (for deterministic timestamp assertions)

TODOs:
- TODO: Integration test for __main__ block once full env-var contract is documented
- TODO: Test email/audit side-effects once send_email/write_audit_entry wiring is confirmed
- TODO: Test behaviour when get_repo_files returns binary/non-UTF8 content
"""

import json
import sys
import os
import types
import datetime
import importlib
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal 'shared' stub so the import in the source file
# succeeds without any real network/secrets dependencies.
# ---------------------------------------------------------------------------

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

# Ensure the scripts directory is on the path so relative import works
_scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Now import the module under test
import tool1_code_review as cr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_VALID_RESULT = {
    "summary": "Code looks good overall.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_VALID_RESULT = {
    "summary": "Several issues found.",
    "score": 45,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or a secrets manager.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "infra/main.tf",
            "line": None,
            "issue": "Missing resource tags.",
            "recommendation": "Add standard cost-allocation tags.",
        },
    ],
    "positive_observations": ["Consistent naming conventions", "Good docstrings"],
    "iac_findings": ["S3 bucket missing encryption at rest"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-stub mocks between tests."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "email_html", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json – happy paths
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_VALID_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_markdown_fence_backtick_triple(self):
        raw = "```json\n" + json.dumps(FULL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 45

    def test_markdown_fence_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_VALID_RESULT) + "\nEnd."
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["findings"][1]["line"] is None

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate Claude inserting a literal newline inside a string value
        malformed = '{"summary": "line one\nline two", "score": 50, ' \
                    '"merge_recommendation": "APPROVE", "findings": [], ' \
                    '"positive_observations": [], "iac_findings": []}'
        # Should either parse after cleaning or raise ValueError, but must not hang
        try:
            result = cr.extract_json(malformed)
            assert "summary" in result
        except ValueError:
            pass  # acceptable – cleaning may not always recover

    def test_extra_text_before_brace(self):
        raw = "Sure! Here is your JSON object:\n\n" + json.dumps(MINIMAL_VALID_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 82


# ---------------------------------------------------------------------------
# extract_json – edge / error cases
# ---------------------------------------------------------------------------

class TestExtractJsonEdgeCases:

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("   \n\t  ")

    def test_no_braces_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON.")

    def test_incomplete_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 50, "summary": "incomplete"')

    def test_array_instead_of_object_raises_value_error(self):
        # An array has no { } so it should raise
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("[1, 2, 3]")

    def test_nested_braces_picks_outermost(self):
        inner = {"a": 1}
        outer = {"wrapper": inner, "score": 99, "summary": "ok",
                 "merge_recommendation": "APPROVE", "findings": [],
                 "positive_observations": [], "iac_findings": []}
        raw = json.dumps(outer)
        result = cr.extract_json(raw)
        assert result["score"] == 99

    def test_score_boundary_zero(self):
        data = {**MINIMAL_VALID_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_VALID_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_unicode_values_preserved(self):
        data = {**MINIMAL_VALID_RESULT, "summary": "Très bien – ñoño"}
        result = cr.extract_json(json.dumps(data, ensure_ascii=False))
        assert "Très bien" in result["summary"]

    def test_findings_with_null_line(self):
        data = {**MINIMAL_VALID_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "Missing docstring", "recommendation": "Add one"}
        ]}
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:

    def test_happy_path_returns_result(self):
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py ..."
        _shared_stub.call_claude.return_value = json.dumps(FULL_VALID_RESULT)
        _shared_stub.post_pr_comment.return_value = None

        result = cr.review_pr("myorg", "myrepo", 42, "https://ci/run/1")

        assert result["score"] == 45
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_get_pr_diff_called_with_correct_args(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_VALID_RESULT)

        cr.review_pr("org", "repo", 7, "https://ci/run/7")

        _shared_stub.get_pr_diff.assert_called_once_with("org", "repo", 7)

    def test_call_claude_receives_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "my special diff content"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_VALID_RESULT)

        cr.review_pr("org", "repo", 1, "https://ci/run/1")

        args, _ = _shared_stub.call_claude.call_args
        assert "my special diff content" in args[1]

    def test_post_pr_comment_called_once(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_VALID_RESULT)

        cr.review_pr("org", "repo", 3, "https://ci/run/3")

        assert _shared_stub.post_pr_comment.call_count == 1

    def test_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_VALID_RESULT)

        cr.review_pr("org", "repo", 5, "https://ci/run/5")

        _, call_kwargs = _shared_stub.post_pr_comment.call_args
        # comment is positional arg index 3
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "45" in comment_text

    def test_comment_contains_recommendation(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_VALID_RESULT)

        cr.review_pr("org", "repo", 5, "https://ci/run/5")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_VALID_RESULT)

        cr.review_pr("org", "repo", 9, "https://ci/run/9")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_invalid_claude_response_raises(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "This is not JSON at all"

        with pytest.raises(ValueError):
            cr.review_pr("org", "repo", 11, "https://ci/run/11")

    def test_findings_appear_in_comment(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_VALID_RESULT)

        cr.review_pr("org", "repo", 6, "https://ci/run/6")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment_text
        assert "HIGH" in comment_text

    def test_positive_observations_in_comment(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = json.dumps(FULL_VALID_RESULT)

        cr.review_pr("org", "repo", 8, "https://ci/run/8")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Consistent naming conventions" in comment_text


# ---------------------------------------------------------------------------
# review_repo
# ---------------------------------------------------------------------------

class TestReviewRepo:

    def test_happy_path_returns_result(self):
        _shared_stub.get_repo_files.return_value = {
            "src/app.py": "print('hello')",
            "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        _shared_stub.call_claude.return_value = json.dumps(FULL_VALID_RESULT)

        result = cr.review_repo("org", "repo", "https://ci/run/1")

        assert result["score"] == 45

    def test_get_repo_files_called_with_extensions(self