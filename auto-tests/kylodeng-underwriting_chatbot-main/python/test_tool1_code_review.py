"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown-fenced input, nested braces, newlines inside strings,
  no JSON found, malformed JSON, edge cases with whitespace.
- review_pr(): happy path orchestration (diff fetch → Claude call → comment post → result returned).
- review_repo(): happy path orchestration (file fetch → Claude call → result returned).
- get_output_url(): URL construction.
- build_report_md(): happy path, missing/empty fields, no findings, no IaC, no positive observations.

Mocks used:
- shared.call_claude          (unittest.mock.patch)
- shared.get_pr_diff          (unittest.mock.patch)
- shared.get_repo_files       (unittest.mock.patch)
- shared.post_pr_comment      (unittest.mock.patch)
- shared.write_output_file    (unittest.mock.patch)
- shared.write_audit_entry    (unittest.mock.patch)
- shared.send_email           (unittest.mock.patch)
- requests (not called directly in tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires environment variables and live GitHub token.
- TODO: Test write_output_file and write_audit_entry interactions once PR/repo full flow is wired.
- TODO: Parameterise review_pr with multiple PR numbers once GitHub mock is fully scaffolded.
"""

import json
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal stub for `shared` so the import of
# tool1_code_review does not require the real module.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.call_claude         = MagicMock()
    mod.get_repo_files      = MagicMock()
    mod.get_pr_diff         = MagicMock()
    mod.write_output_file   = MagicMock()
    mod.post_pr_comment     = MagicMock()
    mod.send_email          = MagicMock()
    mod.email_html          = MagicMock()
    mod.write_audit_entry   = MagicMock()
    mod.OUTPUT_REPO_OWNER   = "test-owner"
    mod.OUTPUT_REPO         = "test-output-repo"
    mod.GH_HEADERS          = {"Authorization": "Bearer fake"}
    mod.GH_API              = "https://api.github.com"
    return mod


# Insert the stub before any import of tool1_code_review
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Ensure the script directory is on the path so the relative import works
_script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Now import the module under test
import importlib
tool1 = importlib.import_module(
    "tool1_code_review"
) if "tool1_code_review" in sys.modules else None

# If the import path resolution differs in CI, fall back
if tool1 is None:
    import tool1_code_review as tool1  # type: ignore

extract_json    = tool1.extract_json
review_pr       = tool1.review_pr
review_repo     = tool1.review_repo
get_output_url  = tool1.get_output_url
build_report_md = tool1.build_report_md


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the code is acceptable.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage."],
    "iac_findings": ["S3 bucket lacks versioning."],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset every mock on the shared stub before each test."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "email_html", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == MINIMAL_RESULT["summary"]

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 75

    def test_markdown_fenced_no_lang(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is my analysis:\n" + json.dumps(MINIMAL_RESULT) + "\nThat's it."
        result = extract_json(raw)
        assert result["score"] == 75

    def test_newlines_inside_string_values_cleaned(self):
        # Simulate a response where a string value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = extract_json(raw)
        assert result["score"] == 50

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        raw = json.dumps(data)
        result = extract_json(raw)
        assert result["findings"] == []

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("There is absolutely no JSON here at all.")

    def test_malformed_json_raises_value_error(self):
        raw = '{ "score": 75, "merge_recommendation": "APPROVE" invalid }'
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_braces_raises_value_error(self):
        # "{}" is valid JSON but represents an empty dict – should NOT raise
        result = extract_json("{}")
        assert result == {}

    def test_nested_json_structures(self):
        data = {
            "summary": "ok",
            "score": 100,
            "merge_recommendation": "APPROVE",
            "findings": [{"severity": "LOW", "category": "maintainability",
                          "file": "a.py", "line": None,
                          "issue": "minor", "recommendation": "fix"}],
            "positive_observations": [],
            "iac_findings": [],
        }
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_multiple_json_objects_uses_outermost(self):
        """When prose contains multiple braces the outermost block is used."""
        inner = '{"nested": true}'
        outer = json.dumps({**MINIMAL_RESULT, "extra": inner})
        result = extract_json(outer)
        assert "score" in result

    def test_markdown_fence_with_extra_text_after(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```\n\nSome trailing text."
        result = extract_json(raw)
        assert result["score"] == 75

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severities(self, severity):
        finding = {**MINIMAL_RESULT["findings"][0], "severity": severity}
        data = {**MINIMAL_RESULT, "findings": [finding]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:

    def _setup(self, result_override=None):
        result = result_override or MINIMAL_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/x.py b/x.py\n+print('hello')"
        _shared_stub.call_claude.return_value = json.dumps(result)
        _shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup()
        result = review_pr("acme", "my-repo", 42, "https://ci/run/1")
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff(self):
        self._setup()
        review_pr("acme", "my-repo", 42, "https://ci/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "my-repo", 42)

    def test_calls_call_claude_with_diff(self):
        self._setup()
        review_pr("acme", "my-repo", 42, "https://ci/run/1")
        args, kwargs = _shared_stub.call_claude.call_args
        assert "Review this pull request diff:" in args[1]

    def test_calls_post_pr_comment(self):
        self._setup()
        review_pr("acme", "my-repo", 42, "https://ci/run/1")
        _shared_stub.post_pr_comment.assert_called_once()
        call_args = _shared_stub.post_pr_comment.call_args[0]
        assert call_args[0] == "acme"
        assert call_args[1] == "my-repo"
        assert call_args[2] == 42

    def test_comment_contains_score(self):
        self._setup()
        review_pr("acme", "my-repo", 42, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "75" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup()
        review_pr("acme", "my-repo", 42, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_comment_contains_findings(self):
        self._setup()
        review_pr("acme", "my-repo", 42, "https://ci/run/1")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment_text

    def test_no_findings_shows_no_findings_placeholder(self):
        result = {**MINIMAL_RESULT, "findings": []}
        self._setup(result_override=result)
        review_pr("acme", "my-repo", 7, "https://ci/run/2")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_no_positive_observations_shows_placeholder(self):
        result = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup(result_override=result)
        review_pr("acme", "my-repo", 7, "https://ci/run/2")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_missing_score_shows_question_mark(self):
        result = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        self._setup(result_override=result)
        review_pr("acme", "my-repo", 7, "https://ci/run/2")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "?" in comment_text

    def test_claude_returns_markdown_fenced_json(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = (
            "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        )
        _shared_stub.post_pr_comment.return_value = None
        result = review_pr("acme", "my-repo", 1, "https://ci/run/1")
        assert result["score"] == 75

    def test_pr_number_zero_edge_case(self):
        self._setup()
        result = review_pr("acme", "my-repo", 0, "")
        assert isinstance(result, dict)

    def test_large_pr_number(self):
        self._setup()
        result = review_pr("acme", "my-repo", 999999, "https://ci/run/x")
        assert result["score"] == 75

    def test_critical_finding_included_in_comment(self):
        finding = {
            "severity": "CRITICAL",
            "category": "security",
            "file": "infra/main.tf",
            "line": 10,
            "issue": "Overly permissive IAM policy.",
            "recommendation": "Restrict IAM to least privilege.",
        }
        result = {**MINIMAL_RESULT, "findings": [finding]}
        self._setup(result_override=result)
        review_pr("acme", "my-repo", 3, "https://ci/run/3")
        comment = _shared_stub.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment
        assert "infra/main.tf" in comment


# ---------------------------------------------------------------------------
# review_repo tests
# ---------------------------------------------------------------------------

class TestReviewRepo:

    def _setup(self, result_override=None):
        result = result_override or MINIMAL_RESULT
        _shared_stub.get_repo_files.return_value = {
            "src/main.py": "print('hello')",
            "infra/main