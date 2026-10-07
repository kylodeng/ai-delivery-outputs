"""
Test module for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown-fenced input, nested/malformed JSON, newline cleanup,
  missing braces, completely invalid input
- review_pr: successful flow, Claude response handling, comment posting, return value
- review_repo: successful flow, content truncation, file filtering delegation
- get_output_url: URL construction with various owner/repo/label combinations
- build_report_md: full report structure, empty findings, missing keys, IaC/positive obs

Mocks used:
- shared.call_claude          (patched at tool1_code_review.call_claude)
- shared.get_pr_diff          (patched at tool1_code_review.get_pr_diff)
- shared.get_repo_files       (patched at tool1_code_review.get_repo_files)
- shared.post_pr_comment      (patched at tool1_code_review.post_pr_comment)
- shared.write_output_file    (patched at tool1_code_review.write_output_file)
- shared.send_email           (patched at tool1_code_review.send_email)
- shared.write_audit_entry    (patched at tool1_code_review.write_audit_entry)
- requests (not directly called in public functions under test, but imported)

TODOs:
- TODO: test __main__ block (requires subprocess or importlib reload with env vars)
- TODO: test interaction with real OUTPUT_REPO_OWNER / OUTPUT_REPO constants if they vary per env
- TODO: integration test for extract_json with actual Claude API response corpus
"""

import json
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Make the module importable without the 'shared' sibling being real.
# We stub out 'shared' before importing tool1_code_review.
# ---------------------------------------------------------------------------

FAKE_SHARED = MagicMock()
FAKE_SHARED.OUTPUT_REPO_OWNER = "test-owner"
FAKE_SHARED.OUTPUT_REPO = "test-output-repo"
FAKE_SHARED.GH_HEADERS = {"Authorization": "Bearer fake"}
FAKE_SHARED.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", FAKE_SHARED)

# Patch sys.path manipulation side-effect so the real shared is never loaded
with patch.dict("sys.modules", {"shared": FAKE_SHARED}):
    import importlib
    import tool1_code_review as cr  # noqa: E402  (import after mock setup)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Code looks mostly good with minor issues.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/app.py",
            "line": 10,
            "issue": "Hardcoded secret found.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage.", "Clear variable naming."],
    "iac_findings": ["S3 bucket lacks versioning."],
}


@pytest.fixture()
def valid_result():
    return dict(VALID_RESULT)


@pytest.fixture()
def valid_json_str():
    return json.dumps(VALID_RESULT)


# ---------------------------------------------------------------------------
# extract_json – happy path
# ---------------------------------------------------------------------------


class TestExtractJsonHappyPath:
    def test_plain_json_string(self, valid_json_str):
        result = cr.extract_json(valid_json_str)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self, valid_json_str):
        result = cr.extract_json(f"   \n{valid_json_str}\n   ")
        assert result["summary"] == "Code looks mostly good with minor issues."

    def test_json_wrapped_in_markdown_triple_backtick(self, valid_json_str):
        wrapped = f"```\n{valid_json_str}\n```"
        result = cr.extract_json(wrapped)
        assert result["score"] == 82

    def test_json_wrapped_in_markdown_json_fence(self, valid_json_str):
        wrapped = f"```json\n{valid_json_str}\n```"
        result = cr.extract_json(wrapped)
        assert result["findings"][0]["severity"] == "HIGH"

    def test_json_with_surrounding_text(self, valid_json_str):
        """Text before and after the JSON object."""
        raw = f"Here is the result:\n{valid_json_str}\nEnd of result."
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_minimal_json(self):
        raw = '{"summary": "ok", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = cr.extract_json(raw)
        assert result["score"] == 50
        assert result["findings"] == []


# ---------------------------------------------------------------------------
# extract_json – newline cleanup inside string values
# ---------------------------------------------------------------------------


class TestExtractJsonNewlineCleanup:
    def test_newline_inside_string_value_is_cleaned(self):
        """Simulate Claude inserting a literal newline inside a JSON string."""
        # Build a JSON string that has a newline inside a value (invalid JSON)
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This is invalid JSON but extract_json should try to clean it
        # The regex cleans single newlines inside string values
        result = cr.extract_json(raw)
        assert result["score"] == 70

    def test_markdown_fence_then_newline_in_value(self, valid_json_str):
        """Markdown fence combined with embedded newline."""
        dirty = valid_json_str.replace(
            '"Code looks mostly good with minor issues."',
            '"Code looks mostly\ngood with minor issues."',
        )
        wrapped = f"```json\n{dirty}\n```"
        result = cr.extract_json(wrapped)
        assert "score" in result


# ---------------------------------------------------------------------------
# extract_json – error / edge cases
# ---------------------------------------------------------------------------


class TestExtractJsonErrors:
    def test_completely_invalid_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is not JSON at all!")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t  ")

    def test_curly_braces_but_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Could not parse Claude response as JSON"):
            cr.extract_json("{ invalid : json content here !!!! }")

    def test_unclosed_brace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"summary": "test"')

    def test_array_only_no_object_raises_value_error(self):
        """A top-level array has no { } — should raise."""
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json('["a", "b", "c"]')

    def test_nested_valid_json_is_extracted(self):
        """Even when { appears multiple times, outermost block is extracted."""
        inner = json.dumps({"nested": True})
        outer = json.dumps({"summary": "ok", "score": 60,
                            "merge_recommendation": "APPROVE",
                            "findings": [], "positive_observations": [],
                            "iac_findings": [], "meta": {"inner": inner}})
        result = cr.extract_json(outer)
        assert result["score"] == 60

    def test_large_response_with_preamble(self, valid_json_str):
        preamble = "A" * 1000
        postamble = "Z" * 500
        raw = preamble + valid_json_str + postamble
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------


class TestReviewPr:
    @pytest.fixture(autouse=True)
    def _patch_shared(self, valid_json_str):
        with patch.object(cr, "get_pr_diff", return_value="diff content") as mock_diff, \
             patch.object(cr, "call_claude", return_value=valid_json_str) as mock_claude, \
             patch.object(cr, "post_pr_comment") as mock_comment:
            self.mock_diff = mock_diff
            self.mock_claude = mock_claude
            self.mock_comment = mock_comment
            yield

    def test_returns_parsed_result(self):
        result = cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_get_pr_diff_called_with_correct_args(self):
        cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        self.mock_diff.assert_called_once_with("my-org", "my-repo", 42)

    def test_call_claude_called_with_system_prompt(self):
        cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        call_args = self.mock_claude.call_args
        assert call_args[0][0] == cr.SYSTEM
        assert "diff content" in call_args[0][1]

    def test_post_pr_comment_called_once(self):
        cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        self.mock_comment.assert_called_once()

    def test_comment_contains_score(self):
        cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "82" in comment_text

    def test_comment_contains_recommendation(self):
        cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "APPROVE" in comment_text

    def test_comment_contains_findings(self):
        cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "Hardcoded secret found." in comment_text

    def test_comment_contains_positive_observations(self):
        cr.review_pr("my-org", "my-repo", 42, "https://run.url")
        comment_text = self.mock_comment.call_args[0][3]
        assert "Good test coverage." in comment_text

    def test_no_findings_shows_placeholder(self):
        empty_result = dict(VALID_RESULT, findings=[], positive_observations=[])
        with patch.object(cr, "call_claude", return_value=json.dumps(empty_result)):
            cr.review_pr("my-org", "my-repo", 1, "https://run.url")
            comment_text = self.mock_comment.call_args[0][3]
            assert "_No findings_" in comment_text

    def test_post_pr_comment_receives_correct_owner_repo_pr(self):
        cr.review_pr("acme", "backend", 99, "https://run.url")
        args = self.mock_comment.call_args[0]
        assert args[0] == "acme"
        assert args[1] == "backend"
        assert args[2] == 99

    def test_claude_response_with_markdown_fence_is_handled(self):
        fenced = f"```json\n{json.dumps(VALID_RESULT)}\n```"
        with patch.object(cr, "call_claude", return_value=fenced):
            result = cr.review_pr("org", "repo", 1, "url")
            assert result["score"] == 82

    def test_invalid_claude_response_raises(self):
        with patch.object(cr, "call_claude", return_value="not json at all"):
            with pytest.raises(ValueError):
                cr.review_pr("org", "repo", 1, "url")

    def test_pr_number_zero_edge_case(self):
        """PR number 0 is unusual but should not crash the function logic."""
        result = cr.review_pr("org", "repo", 0, "url")
        assert "score" in result


# ---------------------------------------------------------------------------
# review_repo
# ---------------------------------------------------------------------------


class TestReviewRepo:
    FAKE_FILES = {
        "src/app.py": "print('hello')" * 100,
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        "frontend/index.ts": "const x = 1;",
    }

    @pytest.fixture(autouse=True)
    def _patch_shared(self, valid_json_str):
        with patch.object(cr, "get_repo_files", return_value=self.FAKE_FILES) as mock_files, \
             patch.object(cr, "call_claude", return_value=valid_json_str) as mock_claude:
            self.mock_files = mock_files
            self.mock_claude = mock_claude
            yield

    def test_returns_parsed_result(self):
        result = cr.review_repo("org", "repo", "https://run.url")
        assert result["score"] == 82

    def test_get_repo_files_called_with_extensions(self):
        cr.review_repo("org", "repo", "https://run.url")
        call_args = self.mock_files.call_args[0]
        extensions = call_args[2]
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions

    def test_call_claude_receives_file_content(self):
        cr.review_repo("org", "repo", "https://run.url")
        prompt = self.mock_claude.call_args[0][1]
        assert "src/app.py" in prompt

    def test_content_truncated_to_20000_chars(self):
        """Even with huge files the prompt content must not exceed 20000 chars."""
        big_files = {f"file_{i}.py": "x" * 5000 for i in range(20)}
        with patch.object(cr, "get_repo_files", return_value=big_files):
            cr.review_repo("org", "repo", "url")
            prompt = self.mock_claude.call_args[0][1]
            # The content portion is everything after the fixed preamble
            content_part = prompt.split("Review this repository codebase:\n\n", 1)[1]
            assert len(content_part) <= 20000

    def test_per_file_content_truncated_to_2000_chars(self):
        """Individual files should contribute at most 2000 chars of code."""
        huge_file = {"only.py": "y" * 10000}
        with patch.object(cr, "get_repo_files", return_value=huge_file):
            cr.review_repo("org", "repo", "url")
            prompt = self.mock_claude.call_args[0][1]
            # The code for only.py should appear trunc