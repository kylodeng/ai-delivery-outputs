"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  no JSON found, malformed JSON, direct parse fallback
- review_pr: happy path comment construction, Claude response handling
- review_repo: happy path, content truncation to token budget
- get_output_url: URL construction
- build_report_md: full report generation, empty findings, missing keys,
  iac_findings and positive_observations fallback

Mocks used:
- shared.call_claude (patched via tool1_code_review module namespace)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- requests (not directly called but imported)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup and live GH token
- TODO: Test email delivery path once send_email call-site is confirmed in tool1
- TODO: Validate SYSTEM prompt structure if Claude API contract is formalised
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Make sure the module under test can be imported without running __main__
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# Patch heavy shared imports before importing the module under test
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude        = MagicMock()
_shared_stub.get_repo_files     = MagicMock()
_shared_stub.get_pr_diff        = MagicMock()
_shared_stub.write_output_file  = MagicMock()
_shared_stub.post_pr_comment    = MagicMock()
_shared_stub.send_email         = MagicMock()
_shared_stub.email_html         = MagicMock()
_shared_stub.write_audit_entry  = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER  = "test-owner"
_shared_stub.OUTPUT_REPO        = "test-output-repo"
_shared_stub.GH_HEADERS         = {"Authorization": "Bearer fake"}
_shared_stub.GH_API             = "https://api.github.com"

sys.modules["shared"] = _shared_stub
sys.modules["requests"] = MagicMock()

import tool1_code_review as t1  # noqa: E402  (import after sys.path manipulation)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean structure"],
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
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket has no encryption.",
            "recommendation": "Enable SSE-S3 or SSE-KMS.",
        },
    ],
    "positive_observations": ["Good docstrings", "Type hints used throughout"],
    "iac_findings": ["Missing resource tags on EC2 instances"],
}


def _make_raw_json(d: dict) -> str:
    return json.dumps(d)


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:

    def test_direct_valid_json(self):
        raw = _make_raw_json(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + _make_raw_json(MINIMAL_RESULT) + "\n   "
        result = t1.extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_json_wrapped_in_markdown_fence_backticks(self):
        inner = _make_raw_json(MINIMAL_RESULT)
        raw = f"```json\n{inner}\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_json_wrapped_in_plain_markdown_fence(self):
        inner = _make_raw_json(MINIMAL_RESULT)
        raw = f"```\n{inner}\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_json_with_preamble_text(self):
        """Claude sometimes writes text before the JSON object."""
        inner = _make_raw_json(MINIMAL_RESULT)
        raw = f"Here is the review:\n{inner}"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_json_with_trailing_text(self):
        inner = _make_raw_json(MINIMAL_RESULT)
        raw = f"{inner}\nLet me know if you need anything else."
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_json_with_both_preamble_and_trailing(self):
        inner = _make_raw_json(FULL_RESULT)
        raw = f"Review complete.\n{inner}\nEnd of review."
        result = t1.extract_json(raw)
        assert result["merge_recommendation"] == "BLOCK"

    def test_newline_inside_string_value_gets_cleaned(self):
        """Literal newlines inside string values should be collapsed."""
        # Manually craft broken JSON with a newline inside a value
        broken = '{"summary": "line one\nline two", "score": 50}'
        result = t1.extract_json(broken)
        # After cleaning the newline becomes a space; summary should still parse
        assert "line one" in result["summary"]

    def test_full_result_with_findings(self):
        raw = _make_raw_json(FULL_RESULT)
        result = t1.extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_raises_when_no_json_present(self):
        raw = "There is no JSON here at all."
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json(raw)

    def test_raises_on_malformed_json(self):
        raw = '{"score": 75, "findings": [BROKEN}'
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_only_whitespace_raises(self):
        with pytest.raises(ValueError):
            t1.extract_json("   \n\t  ")

    def test_nested_braces_picks_outermost(self):
        """Ensure the outermost { } is selected when there are inner objects."""
        raw = _make_raw_json(FULL_RESULT)  # findings contains nested objects
        result = t1.extract_json(raw)
        assert isinstance(result["findings"], list)

    def test_score_boundary_zero(self):
        d = {**MINIMAL_RESULT, "score": 0}
        result = t1.extract_json(_make_raw_json(d))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        d = {**MINIMAL_RESULT, "score": 100}
        result = t1.extract_json(_make_raw_json(d))
        assert result["score"] == 100

    def test_null_line_in_finding(self):
        d = {**FULL_RESULT}
        result = t1.extract_json(_make_raw_json(d))
        assert result["findings"][1]["line"] is None

    def test_empty_findings_list(self):
        d = {**MINIMAL_RESULT, "findings": []}
        result = t1.extract_json(_make_raw_json(d))
        assert result["findings"] == []

    def test_missing_optional_keys_still_parses(self):
        minimal = {"summary": "ok", "score": 55, "merge_recommendation": "APPROVE"}
        result = t1.extract_json(_make_raw_json(minimal))
        assert result["score"] == 55

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_all_valid_merge_recommendations(self, recommendation):
        d = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = t1.extract_json(_make_raw_json(d))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_all_valid_severities(self, severity):
        finding = {
            "severity": severity,
            "category": "security",
            "file": "a.py",
            "line": 1,
            "issue": "test issue",
            "recommendation": "test rec",
        }
        d = {**MINIMAL_RESULT, "findings": [finding]}
        result = t1.extract_json(_make_raw_json(d))
        assert result["findings"][0]["severity"] == severity


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:

    @pytest.fixture(autouse=True)
    def reset_mocks(self):
        _shared_stub.get_pr_diff.reset_mock()
        _shared_stub.call_claude.reset_mock()
        _shared_stub.post_pr_comment.reset_mock()
        yield

    def test_happy_path_calls_all_dependencies(self):
        _shared_stub.get_pr_diff.return_value = "diff --git a/x.py b/x.py\n+print('hello')"
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        result = t1.review_pr("acme", "myrepo", 99, "https://ci/run/1")

        _shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo", 99)
        _shared_stub.call_claude.assert_called_once()
        _shared_stub.post_pr_comment.assert_called_once()
        assert result["score"] == 42

    def test_comment_contains_score(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        t1.review_pr("acme", "myrepo", 1, "https://ci/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "42" in comment_text
        assert "BLOCK" in comment_text

    def test_comment_contains_findings(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        t1.review_pr("acme", "myrepo", 1, "https://ci/run/1")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "src/example.py" in comment_text
        assert "Hardcoded AWS secret key detected" in comment_text

    def test_comment_shows_no_findings_when_empty(self):
        _shared_stub.get_pr_diff.return_value = "minimal diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        t1.review_pr("acme", "myrepo", 2, "https://ci/run/2")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_shows_none_positive_observations_when_empty(self):
        d = {**FULL_RESULT, "positive_observations": []}
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(d)

        t1.review_pr("acme", "myrepo", 3, "https://ci/run/3")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_returns_parsed_result_dict(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        result = t1.review_pr("acme", "myrepo", 5, "https://ci/run/5")
        assert isinstance(result, dict)
        assert "score" in result

    def test_propagates_extract_json_error(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = "not json at all"

        with pytest.raises(ValueError):
            t1.review_pr("acme", "myrepo", 6, "https://ci/run/6")

    def test_line_null_renders_as_na(self):
        """Finding with line=null should render 'None' or 'n/a' in comment."""
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(FULL_RESULT)

        t1.review_pr("acme", "myrepo", 7, "https://ci/run/7")

        comment_text = _shared_stub.post_pr_comment.call_args[0][3]
        # The infra/main.tf finding has line: null -> rendered as n/a
        assert "n/a" in comment_text or "None" in comment_text

    def test_post_pr_comment_receives_correct_owner_repo_pr(self):
        _shared_stub.get_pr_diff.return_value = "diff"
        _shared_stub.call_claude.return_value = _make_raw_json(MINIMAL_RESULT)

        t1.review_pr("org-x", "repo-y", 123, "https://ci/run/123")

        args = _shared_stub.post_pr_comment.call_args[0]
        assert args[0] == "org-x"
        assert args[1] == "repo-y"
        assert args[2] == 123


# ---------------------------------------------------------------------------
# review_repo tests
# ---------------------------------------------------------------------------

class TestReviewRepo:

    @pytest.fixture(autouse=True)
    def reset_mocks(self):
        _shared_stub.get_repo_files.reset_mock()
        _shared_stub.call_claude.reset_mock()
        yield

    def test_happy_path_returns_parsed_result(self):
        _shared_stub.get_repo_files.return_value = {
            "src/main.py": "