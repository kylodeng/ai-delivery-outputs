"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json: happy path (clean JSON), markdown fence stripping,
      outermost-braces extraction, newline-in-string cleanup, error paths
    - review_pr: diff retrieval, Claude call, comment posting, result return
    - review_repo: file retrieval, Claude call, content truncation, result return
    - get_output_url: URL construction
    - build_report_md: full markdown report generation with findings, empty findings,
      missing keys, IaC findings, positive observations

Mocks used:
    - shared.call_claude          (unittest.mock.patch)
    - shared.get_repo_files       (unittest.mock.patch)
    - shared.get_pr_diff          (unittest.mock.patch)
    - shared.write_output_file    (unittest.mock.patch)
    - shared.post_pr_comment      (unittest.mock.patch)
    - shared.send_email           (unittest.mock.patch)
    - shared.email_html           (unittest.mock.patch)
    - shared.write_audit_entry    (unittest.mock.patch)
    - requests                    (unittest.mock.patch where needed)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var setup
    - TODO: test_review_pr_with_real_diff needs a live GH token (skipped)
    - TODO: test_review_repo_token_budget needs to verify exact token count
"""

import importlib
import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import doesn't fail in CI
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
    shared.GH_HEADERS         = {"Authorization": "token test"}
    shared.GH_API             = "https://api.github.com"
    return shared


# Insert the stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Now we can safely import the module
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load module from file path so we don't rely on package installation
_spec = importlib.util.spec_from_file_location("tool1_code_review", _script_path)
cr = importlib.util.load_module_from_spec(_spec)
_spec.loader.exec_module(cr)


# ---------------------------------------------------------------------------
# Convenience references
# ---------------------------------------------------------------------------

extract_json   = cr.extract_json
review_pr      = cr.review_pr
review_repo    = cr.review_repo
get_output_url = cr.get_output_url
build_report_md = cr.build_report_md

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
    "score": 42,
    "merge_recommendation": "REQUEST_CHANGES",
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
            "issue": "Function is too long.",
            "recommendation": "Split into smaller functions.",
        },
    ],
    "positive_observations": ["Good docstrings", "Consistent naming"],
    "iac_findings": ["S3 bucket has no encryption", "IAM role is overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mocks between tests."""
    shared = sys.modules["shared"]
    shared.call_claude.reset_mock()
    shared.get_repo_files.reset_mock()
    shared.get_pr_diff.reset_mock()
    shared.write_output_file.reset_mock()
    shared.post_pr_comment.reset_mock()
    shared.send_email.reset_mock()
    shared.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    # --- happy path ---------------------------------------------------------

    def test_clean_json_object(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == "Looks good overall."

    # --- markdown fence stripping -------------------------------------------

    def test_strip_triple_backtick_fence(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_strip_triple_backtick_json_fence(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_strip_fence_with_extra_whitespace_inside(self):
        inner = json.dumps({"score": 55, "summary": "ok", "findings": []})
        raw = "```\n\n" + inner + "\n\n```"
        result = extract_json(raw)
        assert result["score"] == 55

    # --- outermost-braces extraction ----------------------------------------

    def test_json_embedded_in_prose(self):
        inner = json.dumps({"score": 70, "summary": "fine", "findings": []})
        raw = f"Here is my review:\n{inner}\nEnd of review."
        result = extract_json(raw)
        assert result["score"] == 70

    def test_json_with_preamble_and_postamble(self):
        inner = json.dumps(FULL_RESULT)
        raw = "Sure! Here is the JSON:\n" + inner + "\nHope that helps."
        result = extract_json(raw)
        assert result["score"] == 42

    # --- newline cleanup ----------------------------------------------------

    def test_newline_inside_string_value_cleaned(self):
        # Manually craft a JSON string with a literal newline inside a value
        raw = '{"summary": "first line\nsecond line", "score": 60, "findings": []}'
        # direct parse will fail; the cleaner should handle it
        result = extract_json(raw)
        assert "first line" in result["summary"]

    # --- full result round-trip ---------------------------------------------

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket has no encryption"

    # --- edge cases ---------------------------------------------------------

    def test_single_field_json(self):
        raw = '{"score": 0}'
        result = extract_json(raw)
        assert result["score"] == 0

    def test_score_boundary_zero(self):
        raw = json.dumps({"score": 0, "summary": "terrible", "findings": []})
        result = extract_json(raw)
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        raw = json.dumps({"score": 100, "summary": "perfect", "findings": []})
        result = extract_json(raw)
        assert result["score"] == 100

    def test_empty_arrays(self):
        raw = json.dumps({
            "summary": "x", "score": 50, "merge_recommendation": "APPROVE",
            "findings": [], "positive_observations": [], "iac_findings": []
        })
        result = extract_json(raw)
        assert result["findings"] == []
        assert result["positive_observations"] == []

    def test_finding_with_null_line(self):
        finding = {
            "severity": "LOW", "category": "maintainability",
            "file": "x.py", "line": None,
            "issue": "issue", "recommendation": "fix"
        }
        raw = json.dumps({"score": 50, "findings": [finding]})
        result = extract_json(raw)
        assert result["findings"][0]["line"] is None

    # --- error conditions ---------------------------------------------------

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This has no braces at all.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse Claude response"):
            extract_json('{"score": 50, "summary": missing_quotes}')

    def test_unclosed_brace_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json('{"score": 50')

    def test_array_at_top_level_no_braces_raises(self):
        with pytest.raises(ValueError):
            extract_json('[1, 2, 3]')

    def test_nested_braces_parsed_correctly(self):
        raw = json.dumps({"outer": {"inner": 1}, "score": 10, "findings": []})
        result = extract_json(raw)
        assert result["outer"]["inner"] == 1

    # --- parametrized severity/recommendation values ------------------------

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_all_severities_parsed(self, severity):
        finding = {
            "severity": severity, "category": "security",
            "file": "a.py", "line": 1,
            "issue": "issue", "recommendation": "fix"
        }
        raw = json.dumps({"score": 50, "findings": [finding]})
        result = extract_json(raw)
        assert result["findings"][0]["severity"] == severity

    @pytest.mark.parametrize("rec", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_all_merge_recommendations_parsed(self, rec):
        raw = json.dumps({"score": 50, "merge_recommendation": rec, "findings": []})
        result = extract_json(raw)
        assert result["merge_recommendation"] == rec


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup_claude(self, result_dict):
        sys.modules["shared"].get_pr_diff.return_value = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n+x=1"
        sys.modules["shared"].call_claude.return_value = json.dumps(result_dict)

    def test_happy_path_returns_result(self):
        self._setup_claude(MINIMAL_RESULT)
        result = review_pr("owner", "repo", 7, "https://example.com/run/1")
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("myorg", "myrepo", 42, "https://run")
        sys.modules["shared"].get_pr_diff.assert_called_once_with("myorg", "myrepo", 42)

    def test_calls_call_claude_with_diff_in_prompt(self):
        sys.modules["shared"].get_pr_diff.return_value = "SPECIAL_DIFF_CONTENT"
        sys.modules["shared"].call_claude.return_value = json.dumps(MINIMAL_RESULT)
        review_pr("o", "r", 1, "url")
        args = sys.modules["shared"].call_claude.call_args
        assert "SPECIAL_DIFF_CONTENT" in args[0][1]

    def test_posts_pr_comment(self):
        self._setup_claude(FULL_RESULT)
        review_pr("owner", "repo", 5, "https://run")
        sys.modules["shared"].post_pr_comment.assert_called_once()
        call_args = sys.modules["shared"].post_pr_comment.call_args[0]
        assert call_args[0] == "owner"
        assert call_args[1] == "repo"
        assert call_args[2] == 5

    def test_comment_contains_score(self):
        self._setup_claude(FULL_RESULT)
        review_pr("owner", "repo", 5, "https://run")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "42" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup_claude(FULL_RESULT)
        review_pr("owner", "repo", 5, "https://run")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_contains_summary(self):
        self._setup_claude(FULL_RESULT)
        review_pr("owner", "repo", 5, "https://run")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "Several issues found." in comment_text

    def test_comment_contains_finding(self):
        self._setup_claude(FULL_RESULT)
        review_pr("owner", "repo", 5, "https://run")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("owner", "repo", 5, "https://run")
        comment_text = sys.modules["shared"].post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_contains_positive_observations(self):
        self._setup_claude(MINIMAL_RESULT)
        review_pr("owner", "repo", 5, "https://run