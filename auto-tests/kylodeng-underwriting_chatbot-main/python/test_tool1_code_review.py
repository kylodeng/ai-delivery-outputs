"""
Test module for tool1_code_review.py

What is tested:
  - extract_json: happy path (plain JSON), markdown-fenced JSON, JSON embedded in text,
    newlines inside string values, missing JSON block, unparseable JSON, edge cases.
  - review_pr: happy path, Claude returning formatted markdown, post_pr_comment called.
  - review_repo: happy path, content truncation to 20000 chars, extract_json integration.
  - get_output_url: URL construction.
  - build_report_md: full report with findings, empty findings, missing keys.

Mocks used:
  - shared.call_claude          (unittest.mock.patch)
  - shared.get_pr_diff          (unittest.mock.patch)
  - shared.get_repo_files       (unittest.mock.patch)
  - shared.post_pr_comment      (unittest.mock.patch)
  - shared.write_output_file    (unittest.mock.patch)
  - shared.write_audit_entry    (unittest.mock.patch)
  - shared.send_email           (unittest.mock.patch)
  - requests                    (not called directly in tested functions; kept as guard)

TODOs:
  - TODO: Integration test for __main__ block requires full env-var setup and live shared module.
  - TODO: Test email sending path in __main__ once email logic is visible in source.
  - TODO: Test write_output_file / write_audit_entry call counts when __main__ is fully visible.
"""

import json
import sys
import os
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool1_code_review
# does not fail when the real shared.py is absent from the test environment.
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude        = MagicMock()
_shared_stub.get_repo_files     = MagicMock()
_shared_stub.get_pr_diff        = MagicMock()
_shared_stub.write_output_file  = MagicMock()
_shared_stub.post_pr_comment    = MagicMock()
_shared_stub.send_email         = MagicMock()
_shared_stub.email_html         = MagicMock()
_shared_stub.write_audit_entry  = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER  = "test-output-owner"
_shared_stub.OUTPUT_REPO        = "test-output-repo"
_shared_stub.GH_HEADERS         = {"Authorization": "Bearer test-token"}
_shared_stub.GH_API             = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test.
import importlib
script_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool1_code_review.py")

# Load via importlib so we can import a file not on the normal package path.
import importlib.util as ilu
spec = ilu.spec_from_file_location("tool1_code_review", script_path)
_mod = ilu.module_from_spec(spec)
# Patch sys.modules so the module's own `sys.path.insert` + `from shared import …` reuse our stub.
sys.modules["tool1_code_review"] = _mod
spec.loader.exec_module(_mod)

extract_json    = _mod.extract_json
review_pr       = _mod.review_pr
review_repo     = _mod.review_repo
get_output_url  = _mod.get_output_url
build_report_md = _mod.build_report_md


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean code structure"],
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
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or a secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket lacks encryption.",
            "recommendation": "Enable server-side encryption on the bucket.",
        },
    ],
    "positive_observations": ["Good test coverage", "Clear naming conventions"],
    "iac_findings": ["S3 bucket public access block not configured"],
}


def _json_str(obj: dict) -> str:
    return json.dumps(obj)


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:

    def test_plain_json_string(self):
        raw = _json_str(MINIMAL_RESULT)
        assert extract_json(raw) == MINIMAL_RESULT

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + _json_str(MINIMAL_RESULT) + "\n   "
        assert extract_json(raw) == MINIMAL_RESULT

    def test_markdown_fenced_json_triple_backtick(self):
        raw = "```\n" + _json_str(MINIMAL_RESULT) + "\n```"
        assert extract_json(raw) == MINIMAL_RESULT

    def test_markdown_fenced_json_with_language_hint(self):
        raw = "```json\n" + _json_str(MINIMAL_RESULT) + "\n```"
        assert extract_json(raw) == MINIMAL_RESULT

    def test_json_embedded_in_text(self):
        raw = "Here is the result:\n" + _json_str(FULL_RESULT) + "\nEnd of response."
        assert extract_json(raw) == FULL_RESULT

    def test_json_with_newline_inside_string_value(self):
        # Simulate Claude inserting a literal newline inside a string value.
        raw = '{"summary": "line one\nline two", "score": 55}'
        result = extract_json(raw)
        # After cleaning the newline should be replaced by a space.
        assert result["score"] == 55
        assert "\n" not in result["summary"]

    def test_full_result_roundtrip(self):
        raw = _json_str(FULL_RESULT)
        assert extract_json(raw) == FULL_RESULT

    def test_raises_when_no_json_found(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no JSON at all.")

    def test_raises_when_json_malformed_and_unrecoverable(self):
        # Broken JSON that cannot be recovered.
        with pytest.raises(ValueError):
            extract_json("{key: value, broken json !!!}")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_whitespace_raises(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_nested_json_fields(self):
        data = {
            "summary": "ok",
            "score": 90,
            "merge_recommendation": "APPROVE",
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "a.py",
                    "line": 1,
                    "issue": "Minor style issue.",
                    "recommendation": "Use black formatter.",
                }
            ],
            "positive_observations": [],
            "iac_findings": [],
        }
        assert extract_json(_json_str(data)) == data

    def test_score_boundary_zero(self):
        data = dict(MINIMAL_RESULT, score=0)
        assert extract_json(_json_str(data))["score"] == 0

    def test_score_boundary_hundred(self):
        data = dict(MINIMAL_RESULT, score=100)
        assert extract_json(_json_str(data))["score"] == 100

    def test_extra_text_before_and_after_braces(self):
        raw = "Preamble text {\"key\": \"value\"} epilogue text"
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_markdown_fence_without_closing_fence(self):
        # Only opening fence — should still parse the JSON portion.
        raw = "```json\n" + _json_str(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result == MINIMAL_RESULT

    def test_multiple_json_objects_picks_outermost(self):
        # The function finds first { and last } — it should capture the outer object.
        inner = '{"inner": true}'
        outer = f'{{"outer": true, "nested": {inner}}}'
        result = extract_json(outer)
        assert result["outer"] is True
        assert result["nested"] == {"inner": True}


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_happy_path_returns_result(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff --git a/app.py b/app.py\n+print('hello')"
        mock_claude.return_value = _json_str(MINIMAL_RESULT)

        result = review_pr("owner", "repo", 42, "http://run-url")

        assert result == MINIMAL_RESULT
        mock_diff.assert_called_once_with("owner", "repo", 42)
        mock_claude.assert_called_once()
        mock_comment.assert_called_once()

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_comment_contains_score_and_recommendation(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "some diff"
        mock_claude.return_value = _json_str(FULL_RESULT)

        review_pr("owner", "repo", 7, "http://run")

        posted_comment = mock_comment.call_args[0][3]  # 4th positional arg
        assert "42" in posted_comment          # score
        assert "BLOCK" in posted_comment       # recommendation
        assert "CRITICAL" in posted_comment    # severity from findings

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_no_findings_shows_placeholder(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("owner", "repo", 1, "http://run")

        posted_comment = mock_comment.call_args[0][3]
        assert "_No findings_" in posted_comment

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_passes_pr_number_to_comment(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = _json_str(MINIMAL_RESULT)

        review_pr("my-owner", "my-repo", 99, "http://run")

        args = mock_comment.call_args[0]
        assert args[0] == "my-owner"
        assert args[1] == "my-repo"
        assert args[2] == 99

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_claude_returns_markdown_fenced_json(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = "```json\n" + _json_str(MINIMAL_RESULT) + "\n```"

        result = review_pr("owner", "repo", 3, "http://run")
        assert result["score"] == 80

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_raises_on_bad_claude_response(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = "This is not JSON at all."

        with pytest.raises(ValueError):
            review_pr("owner", "repo", 5, "http://run")

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_positive_observations_listed(self, mock_diff, mock_claude, mock_comment):
        mock_diff.return_value = "diff"
        mock_claude.return_value = _json_str(FULL_RESULT)

        review_pr("owner", "repo", 2, "http://run")

        comment = mock_comment.call_args[0][3]
        assert "Good test coverage" in comment
        assert "Clear naming conventions" in comment

    @patch("tool1_code_review.post_pr_comment")
    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_pr_diff")
    def test_empty_positive_observations_shows_none_placeholder(self, mock_diff, mock_claude, mock_comment):
        result_no_pos = dict(FULL_RESULT, positive_observations=[])
        mock_diff.return_value = "diff"
        mock_claude.return_value = _json_str(result_no_pos)

        review_pr("owner", "repo", 2, "http://run")

        comment = mock_comment.call_args[0][3]
        assert "_None_" in comment


# ===========================================================================
# review_repo tests
# ===========================================================================

class TestReviewRepo:

    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_review.get_repo_files")
    def test_happy_path(self, mock_files, mock_claude):
        mock_files.return_value = {"src/app.py": "print('hello')", "infra/main.tf": "resource {}"}
        mock_claude.return_value = _json_str(MINIMAL_RESULT)

        result = review_repo("owner", "repo", "http://run")

        assert result == MINIMAL_RESULT
        mock_files.assert_called_once_with(
            "owner", "repo", [".py", ".js", ".ts", ".tf", ".bicep", ".yaml", ".yml"]
        )
        mock_claude.assert_called_once()

    @patch("tool1_code_review.call_claude")
    @patch("tool1_code_files")
    def test_content_truncated_to_20000_chars(