"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested braces, newlines in strings,
  missing JSON, malformed JSON, boundary/edge cases
- review_pr: happy path, comment construction, result forwarding
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction
- build_report_md: full report generation, empty findings, missing keys

Mocks used:
- shared.call_claude (patched at tool1_code_review module level)
- shared.get_pr_diff (patched at tool1_code_review module level)
- shared.get_repo_files (patched at tool1_code_review module level)
- shared.post_pr_comment (patched at tool1_code_review module level)
- shared.write_output_file (patched at tool1_code_review module level)
- shared.send_email (patched at tool1_code_review module level)
- shared.write_audit_entry (patched at tool1_code_review module level)
- datetime.datetime.utcnow (patched for deterministic timestamps)

TODOs:
- TODO: test __main__ block execution – requires env-var orchestration and subprocess
- TODO: test email/audit integration paths once entry-point wiring is confirmed
- TODO: test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO env vars are absent
"""

import json
import sys
import os
import types
import importlib
from unittest.mock import patch, MagicMock, call
import pytest

# ---------------------------------------------------------------------------
# Bootstrap: the module imports `shared` via sys.path manipulation.
# We inject a fake `shared` module so the real one (which needs credentials)
# is never loaded.
# ---------------------------------------------------------------------------

def _make_fake_shared():
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock()
    mod.get_repo_files = MagicMock()
    mod.get_pr_diff = MagicMock()
    mod.write_output_file = MagicMock()
    mod.post_pr_comment = MagicMock()
    mod.send_email = MagicMock()
    mod.email_html = MagicMock()
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = "test-output-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    mod.GH_HEADERS = {"Authorization": "Bearer fake"}
    mod.GH_API = "https://api.github.com"
    return mod


# Inject fake shared before importing the module under test
_fake_shared = _make_fake_shared()
sys.modules["shared"] = _fake_shared

# Now we can safely import the module under test
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# Load the module from its actual path, but with our fake shared already in sys.modules
_spec = importlib.util.spec_from_file_location("tool1_code_review", _SCRIPT_PATH)
tool1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool1)

# Convenient aliases
extract_json  = tool1.extract_json
review_pr     = tool1.review_pr
review_repo   = tool1.review_repo
get_output_url = tool1.get_output_url
build_report_md = tool1.build_report_md


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
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
            "issue": "Hardcoded AWS secret key.",
            "recommendation": "Use environment variables or AWS Secrets Manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket has no encryption.",
            "recommendation": "Enable SSE-S3 or SSE-KMS on the bucket.",
        },
    ],
    "positive_observations": ["CI pipeline is well structured", "Good docstrings"],
    "iac_findings": ["Missing mandatory cost-centre tag on all resources"],
}


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mocks between tests."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment", "send_email",
                 "email_html", "write_audit_entry"):
        getattr(_fake_shared, attr).reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json – happy paths
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_plain_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n  "
        result = extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 85

    def test_markdown_fenced_no_language_tag(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_surrounding_prose(self):
        """Claude sometimes prepends a sentence."""
        raw = "Here is my review:\n" + json.dumps(FULL_RESULT) + "\nEnd."
        result = extract_json(raw)
        assert result["score"] == 42

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_findings_with_null_line(self):
        data = {**MINIMAL_RESULT, "findings": [{"severity": "LOW", "category": "correctness",
                                                 "file": "x.py", "line": None,
                                                 "issue": "Issue.", "recommendation": "Fix."}]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_extra_whitespace_before_brace(self):
        raw = "\n\n   " + json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert "summary" in result

    def test_newline_inside_string_value_cleaned(self):
        """Simulate Claude inserting a literal newline inside a string value."""
        partial = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This should NOT parse directly; extract_json must clean it
        result = extract_json(partial)
        assert result["score"] == 50
        assert "line one" in result["summary"]


# ---------------------------------------------------------------------------
# extract_json – error / edge cases
# ---------------------------------------------------------------------------

class TestExtractJsonErrors:

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no JSON at all.")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            extract_json('{"score": 75, "summary": "missing closing brace"')

    def test_array_only_no_object_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json('["a", "b", "c"]')

    def test_truncated_json_raises(self):
        with pytest.raises(ValueError):
            extract_json('{"score": 75, "summary": "truncated...')

    def test_nested_objects_extracted_correctly(self):
        """Outermost braces should be used, not inner ones."""
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert isinstance(result["findings"], list)

    def test_markdown_fence_with_malformed_inner_json_raises(self):
        raw = "```json\n{bad json here\n```"
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:

    def _setup(self, result=None):
        if result is None:
            result = FULL_RESULT
        _fake_shared.get_pr_diff.return_value = "diff --git a/src/app.py b/src/app.py\n+secret = 'abc123'"
        _fake_shared.call_claude.return_value = json.dumps(result)
        _fake_shared.post_pr_comment.return_value = None

    def test_returns_parsed_result(self):
        self._setup()
        out = review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        assert out["score"] == FULL_RESULT["score"]
        assert out["merge_recommendation"] == "BLOCK"

    def test_calls_get_pr_diff_with_correct_args(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        _fake_shared.get_pr_diff.assert_called_once_with("acme", "myrepo", 42)

    def test_calls_call_claude_once(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        assert _fake_shared.call_claude.call_count == 1

    def test_posts_pr_comment(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        _fake_shared.post_pr_comment.assert_called_once()
        args = _fake_shared.post_pr_comment.call_args[0]
        assert args[0] == "acme"
        assert args[1] == "myrepo"
        assert args[2] == 42

    def test_comment_contains_score(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "42" in comment_text

    def test_comment_contains_recommendation(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "BLOCK" in comment_text

    def test_comment_contains_summary(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "Several critical issues found." in comment_text

    def test_comment_contains_finding_details(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "https://ci.example.com/run/1")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment_text
        assert "CRITICAL" in comment_text

    def test_comment_no_findings_shows_placeholder(self):
        self._setup(result=MINIMAL_RESULT)
        review_pr("acme", "myrepo", 1, "")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment_text

    def test_comment_contains_positive_observations(self):
        self._setup()
        review_pr("acme", "myrepo", 42, "")
        comment_text = _fake_shared.post_pr_comment.call_args[0][3]
        assert "CI pipeline is well structured" in comment_text

    def test_handles_markdown_fenced_claude_response(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.return_value = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        _fake_shared.post_pr_comment.return_value = None
        result = review_pr("o", "r", 1, "")
        assert result["score"] == 85

    def test_propagates_call_claude_exception(self):
        _fake_shared.get_pr_diff.return_value = "diff"
        _fake_shared.call_claude.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            review_pr("o", "r", 1, "")

    def test_propagates_get_pr_diff_exception(self):
        _fake_shared.get_pr_diff.side_effect = ConnectionError("network failure")
        with pytest.raises(ConnectionError):
            review_pr("o", "r", 1, "")


# ---------------------------------------------------------------------------
# review_repo
# ---------------------------------------------------------------------------

class TestReviewRepo:

    _FILES = {
        "src/app.py": "print('hello')" * 100,          # 1400 chars, under 2000
        "infra/main.tf": "resource aws_s3_bucket b {}" * 200,  # > 2000, should be truncated
        "docs/readme.md": "# readme",                   # .md not in filter list
    }

    def _setup(self, result=None):
        if result is None:
            result = FULL_RESULT
        _fake_shared.get_repo_files.return_value = {
            "src/app.py": self._FILES["src/app.py"],
            "infra/main.tf": self._FILES["infra/main.tf"],
        }
        _fake_shared.call_claude.return_value = json.dumps(result)

    def test_returns_parsed_