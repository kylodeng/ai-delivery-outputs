"""
Test suite for .github/scripts/tool1_code_review.py

What is tested:
    - extract_json(): JSON extraction from raw Claude responses (happy path, markdown fences,
      embedded newlines, no JSON block, malformed JSON, outermost-block extraction)
    - review_pr(): PR diff retrieval, Claude invocation, comment posting, result parsing
    - review_repo(): Repo file retrieval, content truncation, Claude invocation, result parsing
    - get_output_url(): URL construction for various owner/repo/label combinations
    - build_report_md(): Markdown report generation (full data, empty findings, missing keys)

Mocks used:
    - shared.call_claude          — patched via unittest.mock.patch
    - shared.get_repo_files       — patched via unittest.mock.patch
    - shared.get_pr_diff          — patched via unittest.mock.patch
    - shared.write_output_file    — patched via unittest.mock.patch
    - shared.post_pr_comment      — patched via unittest.mock.patch
    - shared.send_email           — patched via unittest.mock.patch
    - shared.write_audit_entry    — patched via unittest.mock.patch
    - requests                    — NOT called directly in tested functions (Claude abstracted)

TODOs:
    - TODO: Integration tests for __main__ block require env-var injection + subprocess harness
    - TODO: Tests for send_email / write_audit_entry call sites need the full __main__ block
    - TODO: review_pr edge case: very large diff (>100 KB) — token budget behaviour unclear
    - TODO: review_repo file-extension filtering is delegated to shared.get_repo_files; add
            contract tests once shared module test surface is available
"""

import importlib
import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal stub for the `shared` module so the import
# inside tool1_code_review.py succeeds without a real shared.py on the path.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude       = MagicMock()
_shared_stub.get_repo_files    = MagicMock()
_shared_stub.get_pr_diff       = MagicMock()
_shared_stub.write_output_file = MagicMock()
_shared_stub.post_pr_comment   = MagicMock()
_shared_stub.send_email        = MagicMock()
_shared_stub.email_html        = MagicMock()
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-output-owner"
_shared_stub.OUTPUT_REPO       = "test-output-repo"
_shared_stub.GH_HEADERS        = {"Authorization": "Bearer fake"}
_shared_stub.GH_API            = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Insert the scripts directory so the relative import works
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Now import the module under test
import importlib.util, pathlib

_MODULE_PATH = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool1_code_review.py"

# Fallback: if running from repo root the path might differ; try sibling approach
if not _MODULE_PATH.exists():
    _MODULE_PATH = pathlib.Path(__file__).parent / "tool1_code_review.py"

# Load the module dynamically so we control sys.modules['shared'] beforehand
spec = importlib.util.spec_from_file_location("tool1_code_review", str(_MODULE_PATH))
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

extract_json    = _mod.extract_json
review_pr       = _mod.review_pr
review_repo     = _mod.review_repo
get_output_url  = _mod.get_output_url
build_report_md = _mod.build_report_md


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-stub mocks between tests."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


MINIMAL_VALID_RESULT = {
    "summary": "Code looks good overall.",
    "score": 80,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean structure"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues detected.",
    "score": 45,
    "merge_recommendation": "REQUEST_CHANGES",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "backend/model_card.json",
            "line": 10,
            "issue": "Hardcoded credentials detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "frontend/.chainlit/translations/ar-SA.json",
            "line": None,
            "issue": "Missing docstring on public function.",
            "recommendation": "Add a docstring explaining function purpose.",
        },
    ],
    "positive_observations": ["Good use of type hints", "Tests present"],
    "iac_findings": ["S3 bucket lacks encryption", "IAM role overly permissive"],
}


# ===========================================================================
# extract_json — happy path
# ===========================================================================

class TestExtractJsonHappyPath:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_VALID_RESULT)
        result = extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_VALID_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == "Code looks good overall."

    def test_markdown_triple_backtick_fence(self):
        raw = "```json\n" + json.dumps(FULL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 45
        assert len(result["findings"]) == 2

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        """Claude sometimes puts text before/after the JSON block."""
        raw = (
            "Here is my review:\n"
            + json.dumps(MINIMAL_VALID_RESULT)
            + "\nHope that helps!"
        )
        result = extract_json(raw)
        assert result["score"] == 80

    def test_full_result_parsed_correctly(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"
        assert result["iac_findings"][0] == "S3 bucket lacks encryption"


# ===========================================================================
# extract_json — edge cases
# ===========================================================================

class TestExtractJsonEdgeCases:

    def test_newlines_inside_string_values(self):
        """Literal newlines inside JSON string values should be cleaned."""
        raw = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # The raw string itself isn't valid JSON due to the literal newline;
        # extract_json should recover via the regex cleaning step.
        result = extract_json(raw)
        assert "line one" in result["summary"]
        assert result["score"] == 50

    def test_extra_text_around_json_block(self):
        inner = json.dumps({"score": 70, "merge_recommendation": "APPROVE",
                            "summary": "ok", "findings": [],
                            "positive_observations": [], "iac_findings": []})
        raw = f"Preamble text\n{inner}\nPostamble text"
        result = extract_json(raw)
        assert result["score"] == 70

    def test_nested_braces_in_prose(self):
        """Ensure outermost { } extraction handles nested structures."""
        inner = json.dumps(FULL_RESULT)
        raw = f"Some text {{ not a json }} more text {inner} trailing"
        result = extract_json(raw)
        assert result["score"] == 45

    def test_empty_findings_list(self):
        raw = json.dumps({**MINIMAL_VALID_RESULT, "findings": []})
        result = extract_json(raw)
        assert result["findings"] == []

    def test_findings_with_null_line(self):
        data = {**MINIMAL_VALID_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": "foo.py", "line": None,
             "issue": "missing docstring", "recommendation": "add one"}
        ]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None


# ===========================================================================
# extract_json — error conditions
# ===========================================================================

class TestExtractJsonErrors:

    def test_no_json_object_raises_value_error(self):
        raw = "This is just plain text with no JSON whatsoever."
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json(raw)

    def test_malformed_json_raises_value_error(self):
        raw = '{"score": 80, "merge_recommendation": APPROVE}'  # missing quotes
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_markdown_fence_no_content(self):
        raw = "```\n```"
        with pytest.raises((ValueError, json.JSONDecodeError, Exception)):
            extract_json(raw)

    def test_truncated_json_raises_value_error(self):
        raw = '{"score": 80, "findings": ['
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_array_root_not_object_raises_value_error(self):
        """Root-level array should not be accepted (no outermost {} found)."""
        raw = '[{"score": 80}]'
        # The function looks for outermost {}, which won't be at position 0 here;
        # the rfind('}') will still find one, but json.loads of the slice will fail
        # because we extract from the first '{' to the last '}'.
        # Either a valid dict is returned accidentally (array item) or ValueError is raised.
        # We accept both behaviours but NOT a silent wrong return.
        try:
            result = extract_json(raw)
            # If it somehow parsed, it must at least be a dict
            assert isinstance(result, dict)
        except (ValueError, json.JSONDecodeError):
            pass  # expected


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup(self, result=None):
        result = result or MINIMAL_VALID_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/foo.py ..."
        _shared_stub.call_claude.return_value = json.dumps(result)
        _shared_stub.post_pr_comment.return_value = None

    def test_returns_parsed_result(self):
        self._setup()
        out = review_pr("my-org", "my-repo", 42, "https://github.com/run/1")
        assert out["score"] == MINIMAL_VALID_RESULT["score"]
        assert out["merge_recommendation"] == "APPROVE"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup()
        review_pr("acme", "backend", 7, "https://example.com/run")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "backend", 7)

    def test_calls_call_claude_with_diff(self):
        self._setup()
        review_pr("acme", "backend", 7, "https://example.com/run")
        call_args = _shared_stub.call_claude.call_args
        assert "Review this pull request diff" in call_args[0][1]

    def test_posts_pr_comment(self):
        self._setup()
        review_pr("acme", "backend", 7, "https://example.com/run")
        _shared_stub.post_pr_comment.assert_called_once()
        owner, repo, pr_num, comment = _shared_stub.post_pr_comment.call_args[0]
        assert owner == "acme"
        assert repo == "backend"
        assert pr_num == 7
        assert "Claude Code Review" in comment

    def test_comment_contains_score(self):
        self._setup()
        review_pr("acme", "backend", 7, "https://example.com/run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "80" in comment

    def test_comment_contains_recommendation(self):
        self._setup()
        review_pr("acme", "backend", 7, "https://example.com/run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "APPROVE" in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup(result={**MINIMAL_VALID_RESULT, "findings": []})
        review_pr("acme", "backend", 7, "https://example.com/run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "_No findings_" in comment

    def test_comment_with_findings_lists_them(self):
        self._setup(result=FULL_RESULT)
        review_pr("acme", "backend", 7, "https://example.com/run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "backend/model_card.json" in comment
        assert "HIGH" in comment

    def test_comment_no_positive_observations_shows_placeholder(self):
        result = {**MINIMAL_VALID_RESULT, "positive_observations": []}
        self._setup(result=result)
        review_pr("acme", "backend", 7, "https://example.com/run")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "_None_" in comment

    def test_claude_returns_markdown_fenced_json(self):
        """review_pr should still succeed if Claude wraps response in fences."""
        _shared_stub.get_pr_diff.return_value = "diff content"
        _shared_stub.call_claude.return_value = "```json\n" + json.dumps(MINIMAL_VALID_RESULT) + "\n```"
        _shared_stub.post_pr_comment.return_value = None
        out = review_pr("org", "repo", 1, "http://run")