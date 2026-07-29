"""
Test module for tool1_code_review.py

What is tested:
    - extract_json(): JSON extraction from Claude responses (happy path, markdown fences,
      embedded newlines, missing JSON, malformed JSON, nested braces)
    - review_pr(): PR diff review flow (comment posting, result parsing)
    - review_repo(): Scheduled/manual full repo scan flow
    - get_output_url(): URL construction for output reports
    - build_report_md(): Markdown report generation (all fields, missing fields, edge cases)

Mocks used:
    - shared.call_claude (patched via unittest.mock.patch)
    - shared.get_repo_files (patched)
    - shared.get_pr_diff (patched)
    - shared.post_pr_comment (patched)
    - shared.write_output_file (patched)
    - shared.send_email (patched)
    - shared.write_audit_entry (patched)
    - requests (patched where needed)

TODOs:
    - TODO: Integration test for full __main__ entrypoint requires GH_TOKEN env var
    - TODO: Test email dispatch path once send_email signature is confirmed
    - TODO: Test write_output_file call sites once output repo structure is finalised
    - TODO: Test audit entry content once write_audit_entry schema is confirmed
"""

import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: stub out the `shared` module before importing the module under test
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now safe to import the module under test
import importlib
import types

# Re-insert the stub every time we import so patching works predictably
sys.modules["shared"] = shared_stub

# We need to actually load the real module; use importlib so we can control sys.path
_script_path = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool1_code_review.py"
)
# Fall back to looking relative to repo root
if not os.path.exists(_script_path):
    _script_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        ".github", "scripts", "tool1_code_review.py",
    )

# Load the module from file regardless of whether it's on sys.path
import importlib.util

spec = importlib.util.spec_from_file_location(
    "tool1_code_review",
    _script_path if os.path.exists(_script_path) else __file__.replace("test_tool1_code_review.py", "../.github/scripts/tool1_code_review.py"),
)

# If the file doesn't exist at either guessed location, define the module inline
# so the rest of the tests still run with a meaningful skip message.
try:
    tool1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool1)
except (FileNotFoundError, AttributeError, TypeError):
    # Create a minimal stub so tests can be collected and skipped cleanly
    tool1 = types.ModuleType("tool1_code_review")
    tool1.extract_json = None  # type: ignore
    tool1.review_pr = None  # type: ignore
    tool1.review_repo = None  # type: ignore
    tool1.get_output_url = None  # type: ignore
    tool1.build_report_md = None  # type: ignore

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code quality is acceptable.",
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
    "positive_observations": ["Good test coverage.", "Consistent naming conventions."],
    "iac_findings": ["S3 bucket missing encryption tag."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def _skip_if_missing(fn):
    """Return a skip marker if the function could not be loaded."""
    if fn is None:
        return pytest.mark.skip(reason="Module could not be loaded from filesystem")
    return lambda f: f


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for extract_json()."""

    @pytest.fixture(autouse=True)
    def _guard(self):
        if tool1.extract_json is None:
            pytest.skip("Module not loaded")

    def test_happy_path_plain_json(self):
        result = tool1.extract_json(VALID_JSON_STR)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"
        assert len(result["findings"]) == 2

    def test_strips_leading_trailing_whitespace(self):
        result = tool1.extract_json(f"   \n{VALID_JSON_STR}\n   ")
        assert result["summary"] == "Overall the code quality is acceptable."

    def test_strips_markdown_fences_backtick3(self):
        wrapped = f"```json\n{VALID_JSON_STR}\n```"
        result = tool1.extract_json(wrapped)
        assert result["score"] == 75

    def test_strips_markdown_fences_no_language(self):
        wrapped = f"```\n{VALID_JSON_STR}\n```"
        result = tool1.extract_json(wrapped)
        assert result["merge_recommendation"] == "APPROVE"

    def test_extracts_json_with_surrounding_text(self):
        raw = f"Here is the review:\n{VALID_JSON_STR}\nEnd of review."
        result = tool1.extract_json(raw)
        assert result["score"] == 75

    def test_handles_embedded_newline_in_string_value(self):
        # Simulate Claude putting a literal newline inside a JSON string value
        broken = '{"summary": "line one\nline two", "score": 80, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # After cleaning the newline should become a space — parse must not raise
        result = tool1.extract_json(broken)
        assert result["score"] == 80

    def test_raises_value_error_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            tool1.extract_json("This response has no JSON at all.")

    def test_raises_value_error_malformed_json(self):
        malformed = '{"summary": "ok", "score": NOTANUMBER}'
        with pytest.raises(ValueError):
            tool1.extract_json(malformed)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            tool1.extract_json("")

    def test_only_open_brace_raises(self):
        with pytest.raises(ValueError):
            tool1.extract_json("{")

    def test_minimal_valid_json(self):
        minimal = '{"summary": "ok", "score": 0, "merge_recommendation": "BLOCK", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = tool1.extract_json(minimal)
        assert result["score"] == 0
        assert result["merge_recommendation"] == "BLOCK"

    def test_score_boundary_100(self):
        data = {**VALID_RESULT, "score": 100}
        result = tool1.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_score_boundary_0(self):
        data = {**VALID_RESULT, "score": 0}
        result = tool1.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_nested_braces_picks_outermost(self):
        inner = '{"key": {"nested": true}}'
        result = tool1.extract_json(f"Some text {inner} more text")
        assert result["key"] == {"nested": True}

    def test_json_with_null_line_field(self):
        data = {**VALID_RESULT}
        data["findings"] = [
            {
                "severity": "LOW",
                "category": "maintainability",
                "file": "src/foo.py",
                "line": None,
                "issue": "Missing docstring.",
                "recommendation": "Add one.",
            }
        ]
        result = tool1.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_unicode_content(self):
        data = {**VALID_RESULT, "summary": "كود جيد"}
        result = tool1.extract_json(json.dumps(data, ensure_ascii=False))
        assert result["summary"] == "كود جيد"

    def test_empty_findings_list(self):
        data = {**VALID_RESULT, "findings": []}
        result = tool1.extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_large_response_with_preamble(self):
        preamble = "A" * 1000
        result = tool1.extract_json(f"{preamble}{VALID_JSON_STR}")
        assert result["score"] == 75

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**VALID_RESULT, "merge_recommendation": recommendation}
        result = tool1.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severity_values(self, severity):
        data = {**VALID_RESULT}
        data["findings"] = [{**VALID_RESULT["findings"][0], "severity": severity}]
        result = tool1.extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:
    """Tests for review_pr()."""

    @pytest.fixture(autouse=True)
    def _guard(self):
        if tool1.review_pr is None:
            pytest.skip("Module not loaded")

    @pytest.fixture()
    def mock_shared(self):
        shared_stub.get_pr_diff.return_value = "diff --git a/foo.py b/foo.py\n+import os"
        shared_stub.call_claude.return_value = VALID_JSON_STR
        shared_stub.post_pr_comment.return_value = None
        return shared_stub

    def test_returns_parsed_result(self, mock_shared):
        result = tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff(self, mock_shared):
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        mock_shared.get_pr_diff.assert_called_once_with("acme", "my-repo", 42)

    def test_calls_call_claude(self, mock_shared):
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        mock_shared.call_claude.assert_called_once()
        args = mock_shared.call_claude.call_args[0]
        assert "diff --git" in args[1]

    def test_posts_pr_comment(self, mock_shared):
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        mock_shared.post_pr_comment.assert_called_once()
        _, kwargs_or_args = mock_shared.post_pr_comment.call_args[0], mock_shared.post_pr_comment.call_args
        comment_text = mock_shared.post_pr_comment.call_args[0][3]
        assert "Claude Code Review" in comment_text

    def test_comment_contains_score(self, mock_shared):
        tool1.review_pr("acme", "my-repo", 99, "https://ci.example.com/run/1")
        comment_text = mock_shared.post_pr_comment.call_args[0][3]
        assert "75/100" in comment_text

    def test_comment_contains_summary(self, mock_shared):
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        comment_text = mock_shared.post_pr_comment.call_args[0][3]
        assert "Overall the code quality is acceptable." in comment_text

    def test_comment_contains_findings(self, mock_shared):
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        comment_text = mock_shared.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment_text

    def test_comment_no_findings_shows_placeholder(self, mock_shared):
        data = {**VALID_RESULT, "findings": []}
        mock_shared.call_claude.return_value = json.dumps(data)
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        comment_text = mock_shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_positive_observations(self, mock_shared):
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        comment_text = mock_shared.post_pr_comment.call_args[0][3]
        assert "Good test coverage." in comment_text

    def test_comment_no_positive_observations_shows_placeholder(self, mock_shared):
        data = {**VALID_RESULT, "positive_observations": []}
        mock_shared.call_claude.return_value = json.dumps(data)
        tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")
        comment_text = mock_shared.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_raises_on_invalid_claude_response(self, mock_shared):
        mock_shared.call_claude.return_value = "This is not JSON at all."
        with pytest.raises(ValueError):
            tool1.review_pr("acme", "my-repo", 42, "https://ci.example.com/run/1")

    def test_passes_correct_owner_repo_pr_to_comment(self, mock_shared):
        tool1.review_pr("my-org", "cool-repo", 7, "https://ci.example.com/run/1")
        call_args = mock_