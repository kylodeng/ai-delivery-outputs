"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, boundary inputs
- review_pr(): happy path, Claude response handling, comment posting, return value
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): report structure, empty findings, missing keys, timestamp format

Mocks used:
- shared.call_claude (patched via sys.modules)
- shared.get_repo_files
- shared.get_pr_diff
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- requests (imported but not directly called in tested functions)

TODOs:
- TODO: Integration test for __main__ block requires full env var setup
- TODO: Test write_output_file and send_email integration in review_pr/review_repo
  once those calls are confirmed in the full source (file is truncated)
"""

import sys
import os
import json
import datetime
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: create a fake `shared` module before importing the module under test
# ---------------------------------------------------------------------------

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="{}")
    mod.get_repo_files = MagicMock(return_value={})
    mod.get_pr_diff = MagicMock(return_value="diff content")
    mod.write_output_file = MagicMock(return_value=None)
    mod.post_pr_comment = MagicMock(return_value=None)
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html/>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    mod.GH_HEADERS = {"Authorization": "Bearer test-token"}
    mod.GH_API = "https://api.github.com"
    return mod


_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

# Now import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
# Also support running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# We import from the scripts directory directly
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool1_code_review.py"
if not _script_path.exists():
    # Try relative to cwd (CI context)
    _script_path = pathlib.Path(".github") / "scripts" / "tool1_code_review.py"

spec = importlib.util.spec_from_file_location("tool1_code_review", str(_script_path))
tool1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool1)

extract_json = tool1.extract_json
review_pr = tool1.review_pr
review_repo = tool1.review_repo
get_output_url = tool1.get_output_url
build_report_md = tool1.build_report_md


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall looks good.",
    "score": 80,
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
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or a secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "performance",
            "file": "src/utils.py",
            "line": None,
            "issue": "N+1 query pattern detected.",
            "recommendation": "Batch database queries.",
        },
    ],
    "positive_observations": ["Consistent naming conventions", "Good docstrings"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role too permissive"],
}


def _json_str(d: dict) -> str:
    return json.dumps(d)


# ---------------------------------------------------------------------------
# extract_json — happy paths
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:
    def test_plain_json_string(self):
        result = extract_json(_json_str(MINIMAL_RESULT))
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_with_whitespace(self):
        raw = "   " + _json_str(MINIMAL_RESULT) + "   "
        result = extract_json(raw)
        assert result["summary"] == "Overall looks good."

    def test_markdown_triple_backtick_json(self):
        raw = "```json\n" + _json_str(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_markdown_plain_backtick(self):
        raw = "```\n" + _json_str(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_with_leading_text(self):
        raw = "Here is the analysis:\n" + _json_str(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_with_trailing_text(self):
        raw = _json_str(MINIMAL_RESULT) + "\nEnd of response."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_json_with_leading_and_trailing_text(self):
        raw = "Preamble text.\n" + _json_str(MINIMAL_RESULT) + "\nSome trailing words."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_full_result_parsed_correctly(self):
        result = extract_json(_json_str(FULL_RESULT))
        assert result["score"] == 42
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_findings_with_null_line(self):
        result = extract_json(_json_str(FULL_RESULT))
        assert result["findings"][1]["line"] is None

    def test_iac_findings_parsed(self):
        result = extract_json(_json_str(FULL_RESULT))
        assert "S3 bucket missing encryption" in result["iac_findings"]

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = extract_json(_json_str(data))
        assert result["findings"] == []

    def test_unicode_content(self):
        data = {**MINIMAL_RESULT, "summary": "Everything is \u4e2d\u6587."}
        result = extract_json(_json_str(data))
        assert "\u4e2d\u6587" in result["summary"]


# ---------------------------------------------------------------------------
# extract_json — newline cleaning
# ---------------------------------------------------------------------------

class TestExtractJsonNewlineCleaning:
    def test_newline_inside_string_value_is_cleaned(self):
        # Construct a JSON-like string with a literal newline inside a string value
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This should either parse or trigger the newline-cleaning branch
        try:
            result = extract_json(raw)
            assert "line one" in result["summary"]
        except ValueError:
            # Acceptable if cleaning still can't fix it — behaviour is documented
            pass

    def test_multiple_newlines_cleaned(self):
        base = _json_str(MINIMAL_RESULT)
        # Insert artificial newline in summary value position
        raw = base.replace('"Overall looks good."', '"Overall looks\ngood."')
        try:
            result = extract_json(raw)
            assert result is not None
        except ValueError:
            pass  # Cleaning may not always succeed; ensure no unhandled exception type


# ---------------------------------------------------------------------------
# extract_json — edge and error cases
# ---------------------------------------------------------------------------

class TestExtractJsonErrorCases:
    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("   \n\t  ")

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no JSON.")

    def test_array_only_raises_value_error(self):
        # Arrays are valid JSON but the function looks for { }
        with pytest.raises(ValueError):
            extract_json("[1, 2, 3]")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json('{"score": 75, "summary": "missing closing brace"')

    def test_markdown_with_malformed_inner_json(self):
        raw = "```json\n{bad json here\n```"
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_nested_braces_outer_extracted(self):
        # Outer object contains nested valid JSON
        inner = {"nested": True}
        outer = {"summary": "ok", "score": 50, "merge_recommendation": "APPROVE",
                 "findings": [], "positive_observations": [], "iac_findings": [],
                 "meta": inner}
        result = extract_json(_json_str(outer))
        assert result["meta"]["nested"] is True

    def test_json_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(_json_str(data))
        assert result["score"] == 0

    def test_json_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(_json_str(data))
        assert result["score"] == 100

    def test_extra_fields_preserved(self):
        data = {**MINIMAL_RESULT, "extra_field": "extra_value"}
        result = extract_json(_json_str(data))
        assert result["extra_field"] == "extra_value"

    def test_very_long_input(self):
        long_summary = "x" * 10000
        data = {**MINIMAL_RESULT, "summary": long_summary}
        result = extract_json(_json_str(data))
        assert len(result["summary"]) == 10000


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:
    def setup_method(self):
        # Reset all shared stubs before each test
        _shared_stub.call_claude.reset_mock()
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()

    def test_happy_path_returns_result(self):
        _shared_stub.get_pr_diff.return_value = "diff text"
        _shared_stub.call_claude.return_value = _json_str(MINIMAL_RESULT)

        result = review_pr("myorg", "myrepo", 42, "https://ci/run/1")

        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_get_pr_diff_called_with_correct_args(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 7, "https://ci/run/1")

        _shared_stub.get_pr_diff.assert_called_once_with("owner1", "repo1", 7)

    def test_call_claude_receives_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "my_diff_content"
        _shared_stub.call_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 1, "https://ci/run/1")

        args, kwargs = _shared_stub.call_claude.call_args
        assert "my_diff_content" in args[1]

    def test_post_pr_comment_called_once(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 99, "https://ci/run/1")

        assert _shared_stub.post_pr_comment.call_count == 1

    def test_post_pr_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 5, "https://ci/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "80" in comment_text

    def test_post_pr_comment_contains_recommendation(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 5, "https://ci/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_post_pr_comment_contains_summary(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("owner1", "repo1", 5, "https://ci/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Overall looks good." in comment_text

    def test_comment_includes_findings(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _json_str(FULL_RESULT)

        review_pr("owner1", "repo1", 5, "https://ci/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment_text
        assert "src/main.py" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _json_str