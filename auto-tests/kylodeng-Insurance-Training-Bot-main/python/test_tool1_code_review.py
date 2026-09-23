"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): JSON extraction from raw Claude responses (happy path, markdown fences,
  embedded newlines, no-JSON input, malformed JSON, outermost-block extraction)
- review_pr(): PR diff retrieval, Claude call, comment posting, result return
- review_repo(): repo file retrieval, Claude call, result return
- get_output_url(): URL construction
- build_report_md(): markdown report generation (with/without findings, iac, observations)

Mocks used:
- shared.call_claude (patched via tool1_code_review.call_claude)
- shared.get_pr_diff (patched via tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched via tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched via tool1_code_review.post_pr_comment)
- shared.write_output_file (patched where needed)
- datetime.datetime.utcnow (patched for deterministic timestamps)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup and live shared module
- TODO: Test send_email / write_audit_entry integration once email fixtures are available
- TODO: Test cleaned regex branch with real pathological multi-newline JSON samples
"""

import json
import re
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without the real `shared` module
# ---------------------------------------------------------------------------

# We create a minimal fake `shared` module so the import at the top of
# tool1_code_review.py doesn't fail when the real shared.py is absent.
import types

_shared = types.ModuleType("shared")
_shared.call_claude = MagicMock()
_shared.get_repo_files = MagicMock()
_shared.get_pr_diff = MagicMock()
_shared.write_output_file = MagicMock()
_shared.post_pr_comment = MagicMock()
_shared.send_email = MagicMock()
_shared.email_html = MagicMock()
_shared.write_audit_entry = MagicMock()
_shared.OUTPUT_REPO_OWNER = "test-owner"
_shared.OUTPUT_REPO = "test-output-repo"
_shared.GH_HEADERS = {"Authorization": "Bearer fake"}
_shared.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared)

# Add the scripts directory to path so the module can be imported
scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if os.path.isdir(scripts_dir):
    sys.path.insert(0, scripts_dir)
else:
    # Try relative to this file (when tests sit next to .github/)
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts"))

import tool1_code_review as t1  # noqa: E402  (import after path manipulation)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the PR looks good.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 45,
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
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket missing encryption.",
            "recommendation": "Enable server-side encryption.",
        },
    ],
    "positive_observations": ["CI pipeline is well structured", "Meaningful commit messages"],
    "iac_findings": ["S3 bucket missing versioning", "IAM role overly permissive"],
}


def _raw(d: dict) -> str:
    """Serialise dict to a plain JSON string (simulates Claude's raw response)."""
    return json.dumps(d)


# ---------------------------------------------------------------------------
# extract_json — happy path
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:
    def test_plain_json_string(self):
        raw = _raw(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result == MINIMAL_RESULT

    def test_whitespace_padded(self):
        raw = "   " + _raw(MINIMAL_RESULT) + "   \n"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_full_result_parsed(self):
        result = t1.extract_json(_raw(FULL_RESULT))
        assert result["merge_recommendation"] == "BLOCK"
        assert len(result["findings"]) == 2

    def test_returns_dict(self):
        result = t1.extract_json(_raw(MINIMAL_RESULT))
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# extract_json — markdown fence stripping
# ---------------------------------------------------------------------------

class TestExtractJsonMarkdownFences:
    def test_triple_backtick_fence(self):
        raw = "```\n" + _raw(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_triple_backtick_json_fence(self):
        raw = "```json\n" + _raw(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["summary"] == "Overall the PR looks good."

    def test_fence_with_trailing_text(self):
        """Text before closing fence should still parse the inner JSON."""
        raw = "```json\n" + _raw(MINIMAL_RESULT) + "\n```\nSome extra text"
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"


# ---------------------------------------------------------------------------
# extract_json — outermost-block extraction
# ---------------------------------------------------------------------------

class TestExtractJsonOutermostBlock:
    def test_preamble_before_json(self):
        raw = "Here is the review:\n" + _raw(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_postamble_after_json(self):
        raw = _raw(MINIMAL_RESULT) + "\nThat is all."
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_preamble_and_postamble(self):
        raw = "Sure!\n" + _raw(FULL_RESULT) + "\nDone."
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "BLOCK"

    def test_nested_json_object_in_findings(self):
        """The outermost {} must be selected, not an inner one."""
        result = t1.extract_json(_raw(FULL_RESULT))
        assert "findings" in result


# ---------------------------------------------------------------------------
# extract_json — newline-in-string cleaning
# ---------------------------------------------------------------------------

class TestExtractJsonNewlineCleaning:
    def test_literal_newline_inside_string_value(self):
        """A string value split across two lines should be cleaned and parsed."""
        # Manually craft JSON with a literal newline inside a string value
        broken = '{"summary": "first line\nsecond line", "score": 70, ' \
                 '"merge_recommendation": "APPROVE", "findings": [], ' \
                 '"positive_observations": [], "iac_findings": []}'
        # Direct json.loads will fail; our function should handle it
        result = t1.extract_json(broken)
        assert result["score"] == 70
        assert "first line" in result["summary"]


# ---------------------------------------------------------------------------
# extract_json — error conditions
# ---------------------------------------------------------------------------

class TestExtractJsonErrors:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json("just some plain text without any json")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("{bad json: [}")

    def test_only_whitespace_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("   \n\t  ")

    def test_unclosed_brace_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json('{"key": "value"')

    def test_array_not_object_raises(self):
        """A bare JSON array has no {} so should raise ValueError."""
        with pytest.raises(ValueError):
            t1.extract_json('["a", "b"]')


# ---------------------------------------------------------------------------
# extract_json — boundary / edge values
# ---------------------------------------------------------------------------

class TestExtractJsonBoundaryValues:
    def test_score_zero(self):
        d = {**MINIMAL_RESULT, "score": 0}
        assert t1.extract_json(_raw(d))["score"] == 0

    def test_score_hundred(self):
        d = {**MINIMAL_RESULT, "score": 100}
        assert t1.extract_json(_raw(d))["score"] == 100

    def test_empty_findings_list(self):
        d = {**MINIMAL_RESULT, "findings": []}
        result = t1.extract_json(_raw(d))
        assert result["findings"] == []

    def test_null_line_in_finding(self):
        d = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "a.py", "line": None,
             "issue": "issue", "recommendation": "fix"}
        ]}
        result = t1.extract_json(_raw(d))
        assert result["findings"][0]["line"] is None

    def test_unicode_in_summary(self):
        d = {**MINIMAL_RESULT, "summary": "Résumé: café ☕"}
        result = t1.extract_json(_raw(d))
        assert "café" in result["summary"]

    def test_many_findings(self):
        findings = [
            {"severity": "LOW", "category": "maintainability",
             "file": f"file{i}.py", "line": i,
             "issue": f"issue {i}", "recommendation": f"fix {i}"}
            for i in range(50)
        ]
        d = {**MINIMAL_RESULT, "findings": findings}
        result = t1.extract_json(_raw(d))
        assert len(result["findings"]) == 50


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:
    def setup_method(self):
        _shared.get_pr_diff.reset_mock()
        _shared.call_claude.reset_mock()
        _shared.post_pr_comment.reset_mock()

    def test_returns_parsed_result(self):
        _shared.get_pr_diff.return_value = "diff --git a/x.py b/x.py\n+code"
        _shared.call_claude.return_value = _raw(MINIMAL_RESULT)

        result = t1.review_pr("octo", "myrepo", 42, "https://ci/run/1")

        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(MINIMAL_RESULT)

        t1.review_pr("octo", "myrepo", 7, "https://ci/run/2")

        _shared.get_pr_diff.assert_called_once_with("octo", "myrepo", 7)

    def test_calls_post_pr_comment(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(MINIMAL_RESULT)

        t1.review_pr("octo", "myrepo", 3, "https://ci/run/3")

        _shared.post_pr_comment.assert_called_once()
        _, kwargs_or_args = _shared.post_pr_comment.call_args[0], _shared.post_pr_comment.call_args
        args = _shared.post_pr_comment.call_args[0]
        assert args[0] == "octo"
        assert args[1] == "myrepo"
        assert args[2] == 3

    def test_comment_contains_score(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(FULL_RESULT)

        t1.review_pr("octo", "myrepo", 5, "")

        comment_text = _shared.post_pr_comment.call_args[0][3]
        assert "45" in comment_text

    def test_comment_contains_recommendation(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(FULL_RESULT)

        t1.review_pr("octo", "myrepo", 5, "")

        comment_text = _shared.post_pr_comment.call_args[0][3]
        assert "BLOCK" in comment_text

    def test_comment_contains_findings(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(FULL_RESULT)

        t1.review_pr("octo", "myrepo", 5, "")

        comment_text = _shared.post_pr_comment.call_args[0][3]
        assert "Hardcoded AWS secret key detected" in comment_text

    def test_comment_no_findings_placeholder(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(MINIMAL_RESULT)

        t1.review_pr("octo", "myrepo", 5, "")

        comment_text = _shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_positive_observations(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(FULL_RESULT)

        t1.review_pr("octo", "myrepo", 5, "")

        comment_text = _shared.post_pr_comment.call_args[0][3]
        assert "CI pipeline is well structured" in comment_text

    def test_comment_auto_generated_footer(self):
        _shared.get_pr_diff.return_value = "diff"
        _shared.call_claude.return_value = _raw(MINIMAL_RESULT)

        t1.review_pr("octo", "myrepo", 5, "")

        comment_text = _shared.post_pr_comment.call_args[0][3]
        assert "Auto-generated" in comment_text

    def test_missing_keys_in_result_defaults(self):
        """Claude returns partial result — should not raise, use defaults."""
        partial = {"score": 60}
        _shared.get