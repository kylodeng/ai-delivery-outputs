"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, outermost-braces fallback,
  newline-in-value cleaning, missing JSON, completely unparseable input
- review_pr(): happy path, Claude response used to build and post comment
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report, empty findings/iac/positive, missing keys

Mocks used:
- shared.call_claude          (via unittest.mock.patch)
- shared.get_pr_diff          (via unittest.mock.patch)
- shared.get_repo_files       (via unittest.mock.patch)
- shared.post_pr_comment      (via unittest.mock.patch)
- shared.write_output_file    (via unittest.mock.patch)
- shared.send_email           (via unittest.mock.patch)
- shared.write_audit_entry    (via unittest.mock.patch)
- requests                    (not called directly in the tested functions,
                               but imported; patched at module level for safety)

TODOs:
- TODO: Integration test for __main__ block requires real env vars and GH token
- TODO: Test post_pr_comment failure propagation once error-handling is added
- TODO: Test review_pr when get_pr_diff raises an exception
- TODO: Test review_repo when call_claude raises an exception
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Make sure the script's directory is importable without a real `shared` module
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")

# We need a fake `shared` module before the real import happens
import types

_fake_shared = types.ModuleType("shared")
_fake_shared.call_claude       = MagicMock()
_fake_shared.get_repo_files    = MagicMock()
_fake_shared.get_pr_diff       = MagicMock()
_fake_shared.write_output_file = MagicMock()
_fake_shared.post_pr_comment   = MagicMock()
_fake_shared.send_email        = MagicMock()
_fake_shared.email_html        = MagicMock()
_fake_shared.write_audit_entry = MagicMock()
_fake_shared.OUTPUT_REPO_OWNER = "test-owner"
_fake_shared.OUTPUT_REPO       = "test-output-repo"
_fake_shared.GH_HEADERS        = {"Authorization": "Bearer test"}
_fake_shared.GH_API            = "https://api.github.com"

sys.modules["shared"] = _fake_shared

# Now we can safely import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
import tool1_code_review as t1  # noqa: E402


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

MINIMAL_RESULT = {
    "summary": "Code looks fine overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 45,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected",
            "recommendation": "Use environment variables instead",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils/helper.py",
            "line": None,
            "issue": "Function is too long",
            "recommendation": "Break into smaller functions",
        },
    ],
    "positive_observations": ["Good use of type hints", "Well structured modules"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role overly permissive"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared mocks between tests."""
    _fake_shared.call_claude.reset_mock()
    _fake_shared.get_repo_files.reset_mock()
    _fake_shared.get_pr_diff.reset_mock()
    _fake_shared.write_output_file.reset_mock()
    _fake_shared.post_pr_comment.reset_mock()
    _fake_shared.send_email.reset_mock()
    _fake_shared.write_audit_entry.reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = t1.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = t1.extract_json(raw)
        assert result["summary"] == "Code looks fine overall."

    def test_markdown_fence_triple_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fence_with_language_tag(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_extra_text_before_and_after_json(self):
        raw = "Here is the review:\n" + json.dumps(FULL_RESULT) + "\nEnd of review."
        result = t1.extract_json(raw)
        assert result["score"] == 45
        assert len(result["findings"]) == 2

    def test_newline_inside_string_value_is_cleaned(self):
        # Simulate a JSON string where a value contains a literal newline
        raw = '{"summary": "Line one\nLine two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # Direct parse will fail; fallback should clean and succeed
        result = t1.extract_json(raw)
        assert result["score"] == 70

    def test_no_json_object_found_raises_value_error(self):
        raw = "There is absolutely no JSON here at all."
        with pytest.raises(ValueError, match="No JSON object found"):
            t1.extract_json(raw)

    def test_malformed_json_raises_value_error(self):
        raw = '{"summary": "bad json", "score": }'
        with pytest.raises((ValueError, json.JSONDecodeError)):
            t1.extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            t1.extract_json("")

    def test_only_braces_raises_value_error(self):
        # Just braces with no valid content
        with pytest.raises((ValueError, json.JSONDecodeError)):
            t1.extract_json("{ not valid json }")

    def test_nested_objects_parsed_correctly(self):
        raw = json.dumps(FULL_RESULT)
        result = t1.extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["findings"][1]["line"] is None

    def test_multiple_json_objects_uses_outermost(self):
        """When extra text wraps a JSON object, outermost braces are used."""
        inner = json.dumps(MINIMAL_RESULT)
        raw = f"Prefix text {inner} suffix text"
        result = t1.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fence_only_no_json_raises(self):
        raw = "```\nno json here\n```"
        with pytest.raises(ValueError):
            t1.extract_json(raw)

    @pytest.mark.parametrize("score,rec", [
        (0,   "BLOCK"),
        (50,  "REQUEST_CHANGES"),
        (100, "APPROVE"),
    ])
    def test_various_scores_and_recommendations(self, score, rec):
        data = {**MINIMAL_RESULT, "score": score, "merge_recommendation": rec}
        raw = json.dumps(data)
        result = t1.extract_json(raw)
        assert result["score"] == score
        assert result["merge_recommendation"] == rec

    def test_iac_findings_empty_list(self):
        data = {**MINIMAL_RESULT, "iac_findings": []}
        result = t1.extract_json(json.dumps(data))
        assert result["iac_findings"] == []

    def test_iac_findings_populated(self):
        data = {**MINIMAL_RESULT, "iac_findings": ["Missing encryption", "IAM too broad"]}
        result = t1.extract_json(json.dumps(data))
        assert len(result["iac_findings"]) == 2


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:

    def _setup_claude(self, result_dict=None):
        if result_dict is None:
            result_dict = FULL_RESULT
        _fake_shared.get_pr_diff.return_value = "diff --git a/src/example.py ..."
        _fake_shared.call_claude.return_value = json.dumps(result_dict)
        _fake_shared.post_pr_comment.return_value = None

    def test_happy_path_returns_parsed_result(self):
        self._setup_claude(FULL_RESULT)
        result = t1.review_pr("owner", "repo", 42, "https://run.url")
        assert result["score"] == 45
        assert result["merge_recommendation"] == "REQUEST_CHANGES"

    def test_get_pr_diff_is_called_with_correct_args(self):
        self._setup_claude()
        t1.review_pr("myowner", "myrepo", 7, "https://run.url")
        _fake_shared.get_pr_diff.assert_called_once_with("myowner", "myrepo", 7)

    def test_call_claude_receives_diff_content(self):
        self._setup_claude()
        t1.review_pr("owner", "repo", 1, "https://run.url")
        call_args = _fake_shared.call_claude.call_args
        assert "Review this pull request diff" in call_args[0][1]
        assert "diff --git" in call_args[0][1]

    def test_post_pr_comment_is_called_once(self):
        self._setup_claude()
        t1.review_pr("owner", "repo", 1, "https://run.url")
        _fake_shared.post_pr_comment.assert_called_once()

    def test_comment_contains_score(self):
        self._setup_claude(FULL_RESULT)
        t1.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "45" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup_claude(FULL_RESULT)
        t1.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment_text

    def test_comment_contains_finding_details(self):
        self._setup_claude(FULL_RESULT)
        t1.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Hardcoded password detected" in comment_text
        assert "src/example.py" in comment_text

    def test_comment_shows_no_findings_when_empty(self):
        self._setup_claude(MINIMAL_RESULT)
        t1.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_shows_positive_observations(self):
        self._setup_claude(FULL_RESULT)
        t1.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Good use of type hints" in comment_text

    def test_comment_none_positive_observations(self):
        data = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup_claude(data)
        t1.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_None_" in comment_text

    def test_finding_line_null_shows_na(self):
        data = {**FULL_RESULT}  # FULL_RESULT has line=None for second finding
        self._setup_claude(data)
        t1.review_pr("owner", "repo", 1, "https://run.url")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "n/a" in comment_text

    def test_system_prompt_passed_to_claude(self):
        self._setup_claude()
        t1.review_pr("owner", "repo", 1, "https://run.url")
        call_args = _fake_shared.call_claude.call_args
        system_arg = call_args[0][0]
        assert "senior code reviewer" in system_arg

    @pytest.mark.skip(reason="TODO: Need to decide error propagation strategy when get_pr_diff raises")
    def test_review_pr_when_diff_fetch_fails(self):
        _fake_shared.get_pr_diff.side_effect = Exception("GitHub API error")
        t1.review_pr("owner", "repo", 1, "https://run.url")

    @pytest.mark.skip(reason="TODO: Need to decide error propagation strategy when call_claude raises")
    def test_review_pr_when_claude_raises(self):
        _fake_shared.get_pr_diff.return_value = "some diff"
        _fake_shared.call_claude.side_effect = Exception("Claude API error")
        t1.review_pr("owner", "repo", 1, "https://run.url")


# ===========================================================================
# review_repo tests
# ===========================================================================

class TestReviewRepo:

    SAMPLE_FILES = {
        "src/main.py": "print('hello')" * 100,
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        "frontend/app.ts": "const x = 1;",
    }

    def _setup(self, files=None, result=None):
        _fake_shared.get_repo_files.return_value = files or self.SAMPLE_FILES
        _fake_shared.call_claude.return_value = json.dumps(result or MINIMAL_RESULT)

    def test_happy_path_returns_parsed_result(self):
        self._setup()
        result = t1.review_repo("owner", "repo", "https://run.url")
        assert