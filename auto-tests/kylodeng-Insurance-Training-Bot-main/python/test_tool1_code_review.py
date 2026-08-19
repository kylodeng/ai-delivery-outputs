"""
Test module for tool1_code_review.py

What is tested:
    - extract_json: happy path, markdown fence stripping, outermost-brace extraction,
      newline-inside-string cleaning, missing JSON, unparseable JSON, edge cases
    - review_pr: happy path, Claude returns result, comment posted, result returned
    - review_repo: happy path, file content truncation, result returned
    - get_output_url: URL construction
    - build_report_md: full report generation, empty findings/iac/pos, missing keys

Mocks used:
    - shared.call_claude          (unittest.mock.patch)
    - shared.get_pr_diff          (unittest.mock.patch)
    - shared.get_repo_files       (unittest.mock.patch)
    - shared.post_pr_comment      (unittest.mock.patch)
    - shared.write_output_file    (unittest.mock.patch)
    - shared.send_email           (unittest.mock.patch)
    - shared.write_audit_entry    (unittest.mock.patch)
    - requests                    (not called directly by public functions under test)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var wiring and
      a live GitHub token — stub provided below.
    - TODO: test_review_repo_token_budget needs real token-count measurement; currently
      asserts on character limit only.
"""

import importlib
import json
import sys
import types
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool1_code_review
# without a real shared.py on the path.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="{}")
    shared.get_repo_files     = MagicMock(return_value={})
    shared.get_pr_diff        = MagicMock(return_value="diff text")
    shared.write_output_file  = MagicMock()
    shared.post_pr_comment    = MagicMock()
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html/>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-owner"
    shared.OUTPUT_REPO        = "test-output-repo"
    shared.GH_HEADERS         = {"Authorization": "token fake"}
    shared.GH_API             = "https://api.github.com"
    return shared


# Insert stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Graceful skip if the source file is not present in the test environment
if not _script_path.exists():
    pytest.skip(
        f"Source file not found at {_script_path}",
        allow_module_level=True,
    )

spec = importlib.util.spec_from_file_location("tool1_code_review", _script_path)
mod  = importlib.util.module_from_spec(spec)
# Ensure the stub is visible during exec
sys.modules["shared"] = _shared_stub
spec.loader.exec_module(mod)

extract_json    = mod.extract_json
review_pr       = mod.review_pr
review_repo     = mod.review_repo
get_output_url  = mod.get_output_url
build_report_md = mod.build_report_md


# ===========================================================================
# Fixtures
# ===========================================================================

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
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
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key.",
            "recommendation": "Use environment variables or secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "performance",
            "file": "src/utils.py",
            "line": None,
            "issue": "N+1 query pattern detected.",
            "recommendation": "Batch database calls.",
        },
    ],
    "positive_observations": ["Clear variable naming", "Comprehensive logging"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs between tests."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    # --- Happy path: plain JSON -------------------------------------------

    def test_plain_json_minimal(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_full(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2

    def test_leading_and_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == "Looks good overall."

    # --- Markdown fence stripping -----------------------------------------

    def test_strips_triple_backtick_json_fence(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 85

    def test_strips_triple_backtick_plain_fence(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 85

    def test_strips_fence_with_extra_text_after(self):
        inner = json.dumps({"score": 50, "summary": "ok", "findings": []})
        raw = "```json\n" + inner + "\n```\nSome trailing text."
        result = extract_json(raw)
        assert result["score"] == 50

    # --- Outermost-brace extraction ---------------------------------------

    def test_extracts_json_with_preamble_text(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_extracts_json_with_postamble_text(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nPlease review carefully."
        result = extract_json(raw)
        assert result["score"] == 85

    def test_extracts_json_with_both_preamble_and_postamble(self):
        raw = "Preamble\n" + json.dumps(FULL_RESULT) + "\nPostamble"
        result = extract_json(raw)
        assert result["score"] == 42

    # --- Newline-inside-string cleaning -----------------------------------

    def test_cleans_newline_inside_string_value(self):
        # Construct a JSON string where a value contains a literal newline
        raw = '{"summary": "line one\nline two", "score": 70, "findings": []}'
        # This will fail direct parse; extract_json should recover
        result = extract_json(raw)
        assert "line one" in result["summary"]
        assert result["score"] == 70

    # --- Error conditions -------------------------------------------------

    def test_raises_when_no_json_object_found(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no JSON.")

    def test_raises_when_braces_present_but_invalid_json(self):
        with pytest.raises(ValueError):
            extract_json("{ totally: broken json !!!")

    def test_raises_on_empty_string(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_raises_on_whitespace_only(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_raises_on_array_only(self):
        # Valid JSON but not an object — no braces at outermost level
        with pytest.raises(ValueError):
            extract_json("[1, 2, 3]")

    # --- Boundary / edge cases --------------------------------------------

    def test_nested_objects_in_findings(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert result["findings"][0]["severity"] == "CRITICAL"
        assert result["findings"][1]["line"] is None

    def test_empty_findings_list(self):
        data = {**MINIMAL_RESULT, "findings": []}
        result = extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_score_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_unicode_values_preserved(self):
        data = {**MINIMAL_RESULT, "summary": "Résumé: ñoño 中文"}
        result = extract_json(json.dumps(data, ensure_ascii=False))
        assert "Résumé" in result["summary"]

    def test_multiple_json_objects_uses_outermost(self):
        """When multiple brace pairs exist, outermost { ... } wins."""
        inner = '{"nested": true}'
        outer = json.dumps({"score": 99, "findings": [], "summary": inner})
        result = extract_json(outer)
        assert result["score"] == 99

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severities(self, severity):
        finding = {
            "severity": severity,
            "category": "security",
            "file": "f.py",
            "line": 1,
            "issue": "issue",
            "recommendation": "fix",
        }
        data = {**MINIMAL_RESULT, "findings": [finding]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_claude(self, result_dict):
        _shared_stub.get_pr_diff.return_value = "diff content"
        _shared_stub.call_claude.return_value = json.dumps(result_dict)

    def test_happy_path_returns_result(self):
        self._setup_claude(MINIMAL_RESULT)
        result = review_pr("octocat", "hello-world", 42, "http://run.url")
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 7, "http://run.url")
        _shared_stub.get_pr_diff.assert_called_once_with("octocat", "hello-world", 7)

    def test_calls_call_claude_with_diff_in_prompt(self):
        _shared_stub.get_pr_diff.return_value = "my special diff"
        _shared_stub.call_claude.return_value = json.dumps(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 1, "http://run.url")
        args = _shared_stub.call_claude.call_args
        assert "my special diff" in args[0][1]

    def test_posts_pr_comment(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 3, "http://run.url")
        assert _shared_stub.post_pr_comment.called

    def test_pr_comment_contains_score(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 3, "http://run.url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "85" in comment_text

    def test_pr_comment_contains_recommendation(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 3, "http://run.url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_pr_comment_contains_summary(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 3, "http://run.url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Looks good overall." in comment_text

    def test_pr_comment_contains_findings(self):
        self._setup_claude(FULL_RESULT)
        review_pr("octocat", "hello-world", 5, "http://run.url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "CRITICAL" in comment_text
        assert "src/main.py" in comment_text

    def test_pr_comment_no_findings_shows_placeholder(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 5, "http://run.url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_pr_comment_positive_observations_listed(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("octocat", "hello-world", 5, "http://run.url")
        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "Good test coverage" in comment_text

    def test_pr_comment_posted_