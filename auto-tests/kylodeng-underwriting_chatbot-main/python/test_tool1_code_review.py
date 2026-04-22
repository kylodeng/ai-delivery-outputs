"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, empty input, already-clean JSON
- review_pr(): happy path, Claude response handling, comment formatting, return value
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction with various owner/repo/label combos
- build_report_md(): full report generation, empty findings, missing keys, IaC findings,
  positive observations, score/recommendation rendering

Mocks used:
- shared.call_claude (patched via unittest.mock.patch)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- datetime.datetime (for deterministic timestamps)

TODOs:
- TODO: Integration test against a real GitHub PR requires GH_TOKEN + live repo
- TODO: Test __main__ block (requires env-var orchestration and subprocess execution)
- TODO: Test send_email / write_audit_entry call sites once wired into review_pr/review_repo
"""

import json
import sys
import os
import types
import importlib
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal `shared` stub so the import in the source file
# succeeds without the real shared module or any real credentials.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.call_claude        = MagicMock()
shared_stub.get_repo_files     = MagicMock()
shared_stub.get_pr_diff        = MagicMock()
shared_stub.write_output_file  = MagicMock()
shared_stub.post_pr_comment    = MagicMock()
shared_stub.send_email         = MagicMock()
shared_stub.email_html         = MagicMock()
shared_stub.write_audit_entry  = MagicMock()
shared_stub.OUTPUT_REPO_OWNER  = "test-output-owner"
shared_stub.OUTPUT_REPO        = "test-output-repo"
shared_stub.GH_HEADERS         = {"Authorization": "Bearer fake"}
shared_stub.GH_API             = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now we can safely import the module under test.
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load the module dynamically so we can reference its functions directly.
spec = importlib.util.spec_from_file_location("tool1_code_review", _script_path)
mod  = importlib.util.module_from_spec(spec)
# Inject the stub before exec so the from-import inside the module resolves.
sys.modules["tool1_code_review"] = mod
spec.loader.exec_module(mod)

extract_json   = mod.extract_json
review_pr      = mod.review_pr
review_repo    = mod.review_repo
get_output_url = mod.get_output_url
build_report_md = mod.build_report_md

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks good overall.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several issues found.",
    "score": 45,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
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
    "positive_observations": ["Good naming conventions", "Tests present"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role too permissive"],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared stubs before each test."""
    shared_stub.call_claude.reset_mock()
    shared_stub.get_repo_files.reset_mock()
    shared_stub.get_pr_diff.reset_mock()
    shared_stub.write_output_file.reset_mock()
    shared_stub.post_pr_comment.reset_mock()
    shared_stub.send_email.reset_mock()
    shared_stub.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:

    # --- Happy path: clean JSON string ---
    def test_clean_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    # --- Markdown fence: triple backtick without language tag ---
    def test_strips_plain_markdown_fence(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    # --- Markdown fence: json-labelled fence ---
    def test_strips_json_labelled_fence(self):
        raw = "```json\n" + json.dumps(FULL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 45

    # --- Leading/trailing whitespace ---
    def test_whitespace_stripped(self):
        raw = "   \n  " + json.dumps(MINIMAL_RESULT) + "\n  "
        result = extract_json(raw)
        assert result["score"] == 82

    # --- Preamble text before JSON ---
    def test_preamble_before_json(self):
        raw = "Here is my analysis:\n\n" + json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    # --- Trailing text after JSON ---
    def test_trailing_text_after_json(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nPlease let me know if you need more details."
        result = extract_json(raw)
        assert result["score"] == 82

    # --- Preamble AND trailing text ---
    def test_preamble_and_trailing_text(self):
        raw = "Sure!\n" + json.dumps(FULL_RESULT) + "\nDone."
        result = extract_json(raw)
        assert result["score"] == 45

    # --- Newline inside string value (cleaned by regex) ---
    def test_newline_inside_string_value(self):
        # Construct raw text with a literal newline inside a JSON string value.
        raw = '{"summary": "line one\nline two", "score": 70, ' \
              '"merge_recommendation": "APPROVE", "findings": [], ' \
              '"positive_observations": [], "iac_findings": []}'
        # Should either parse after cleaning or raise ValueError — not crash unexpectedly.
        try:
            result = extract_json(raw)
            # If it parsed, summary should have been collapsed.
            assert "line one" in result["summary"]
        except ValueError:
            pass  # Acceptable — the important thing is no unexpected exception type.

    # --- No JSON at all ---
    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This response contains no JSON whatsoever.")

    # --- Empty string ---
    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    # --- Whitespace only ---
    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    # --- Malformed JSON (truncated) ---
    def test_malformed_json_raises_value_error(self):
        raw = '{"summary": "truncated'
        with pytest.raises(ValueError):
            extract_json(raw)

    # --- Braces present but invalid content ---
    def test_braces_with_invalid_content(self):
        raw = "{ this is not json at all }"
        with pytest.raises(ValueError):
            extract_json(raw)

    # --- Full result round-trip ---
    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["findings"][1]["line"] is None

    # --- Unicode characters in values (from synthetic data, e.g. Arabic) ---
    def test_unicode_values(self):
        data = {
            "summary": "\u0625\u0644\u063a\u0627\u0621",
            "score": 60,
            "merge_recommendation": "APPROVE",
            "findings": [],
            "positive_observations": [],
            "iac_findings": [],
        }
        raw = json.dumps(data, ensure_ascii=False)
        result = extract_json(raw)
        assert result["score"] == 60

    # --- Nested objects survive extraction ---
    def test_nested_objects_preserved(self):
        data = dict(FULL_RESULT)
        raw = json.dumps(data)
        result = extract_json(raw)
        assert result["findings"][0]["file"] == "src/app.py"

    # --- Score boundary: 0 ---
    @pytest.mark.parametrize("score", [0, 1, 50, 99, 100])
    def test_score_boundary_values(self, score):
        data = {**MINIMAL_RESULT, "score": score}
        result = extract_json(json.dumps(data))
        assert result["score"] == score

    # --- Markdown fence with extra blank lines ---
    def test_fence_with_extra_blank_lines(self):
        raw = "```json\n\n" + json.dumps(MINIMAL_RESULT) + "\n\n```"
        result = extract_json(raw)
        assert "score" in result

    # --- Response with only closing brace missing ---
    def test_unclosed_json_raises(self):
        raw = '{"summary": "ok", "score": 80'
        with pytest.raises(ValueError):
            extract_json(raw)


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:

    def _make_claude_response(self, result=None):
        return json.dumps(result or MINIMAL_RESULT)

    def test_happy_path_returns_result(self):
        shared_stub.get_pr_diff.return_value = "diff --git a/foo.py ..."
        shared_stub.call_claude.return_value  = self._make_claude_response()

        result = review_pr("owner", "repo", 42, "http://run-url")

        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        shared_stub.get_pr_diff.return_value = "some diff"
        shared_stub.call_claude.return_value  = self._make_claude_response()

        review_pr("myowner", "myrepo", 7, "http://run")

        shared_stub.get_pr_diff.assert_called_once_with("myowner", "myrepo", 7)

    def test_calls_call_claude_with_diff_in_prompt(self):
        shared_stub.get_pr_diff.return_value = "THIS IS THE DIFF"
        shared_stub.call_claude.return_value  = self._make_claude_response()

        review_pr("o", "r", 1, "http://run")

        args, kwargs = shared_stub.call_claude.call_args
        assert "THIS IS THE DIFF" in args[1]

    def test_posts_pr_comment(self):
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value  = self._make_claude_response()

        review_pr("owner", "repo", 10, "http://run")

        shared_stub.post_pr_comment.assert_called_once()
        call_args = shared_stub.post_pr_comment.call_args
        assert call_args[0][0] == "owner"
        assert call_args[0][1] == "repo"
        assert call_args[0][2] == 10

    def test_comment_contains_score(self):
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value  = self._make_claude_response()

        review_pr("owner", "repo", 1, "http://run")

        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "82" in comment

    def test_comment_contains_recommendation(self):
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value  = self._make_claude_response()

        review_pr("owner", "repo", 1, "http://run")

        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "APPROVE" in comment

    def test_comment_contains_summary(self):
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value  = self._make_claude_response()

        review_pr("owner", "repo", 1, "http://run")

        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "Code looks good overall." in comment

    def test_comment_contains_no_findings_placeholder(self):
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value  = self._make_claude_response()

        review_pr("owner", "repo", 1, "http://run")

        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_shows_findings_when_present(self):
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value  = json.dumps(FULL_RESULT)

        review_pr("owner", "repo", 1, "http://run")

        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected." in comment
        assert "src/app.py" in comment

    def test_comment_positive_observations(self):
        shared_stub.get_pr_diff.return_value = "diff"
        shared_stub.call_claude.return_value  = json.dumps(FULL_RESULT)

        review_pr("owner", "repo", 1, "http://run")

        comment = shared_stub.post_pr_comment.call_args[0][3]
        assert "Good naming conventions" in comment

    def test_comment_missing_score_shows_question_mark(self):
        result_no_score = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        shared_stub.get_pr_diff.return_value =