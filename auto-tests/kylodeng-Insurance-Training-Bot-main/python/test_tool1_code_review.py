"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path (plain JSON), markdown fences, newlines in strings,
  missing JSON, malformed JSON, nested braces, edge cases with whitespace
- review_pr: correct orchestration, comment formatting, return value
- review_repo: correct orchestration, truncation behaviour, return value
- get_output_url: URL construction
- build_report_md: report Markdown structure, empty findings, missing keys

Mocks used:
- shared.call_claude          → unittest.mock.patch
- shared.get_pr_diff          → unittest.mock.patch
- shared.get_repo_files       → unittest.mock.patch
- shared.post_pr_comment      → unittest.mock.patch
- shared.write_output_file    → unittest.mock.patch
- shared.send_email           → unittest.mock.patch
- shared.write_audit_entry    → unittest.mock.patch
- requests                    → unittest.mock.patch (not directly invoked in tested
                                 functions but imported at module level)

TODOs:
- TODO: Integration test for __main__ entry-point (requires env-var setup + subprocess)
- TODO: Test email formatting path once send_email integration is clearer
- TODO: Test write_output_file is called with correct path after full run (needs
        complete __main__ coverage)
"""

import json
import sys
import os
import types
import importlib
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal stub for `shared` so the import doesn't fail
# when the real module / credentials are absent.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    for name in (
        "call_claude", "get_repo_files", "get_pr_diff",
        "write_output_file", "post_pr_comment", "send_email",
        "email_html", "write_audit_entry",
    ):
        setattr(stub, name, MagicMock())
    stub.OUTPUT_REPO_OWNER = "test-owner"
    stub.OUTPUT_REPO       = "test-output-repo"
    stub.GH_HEADERS        = {"Authorization": "Bearer fake"}
    stub.GH_API            = "https://api.github.com"
    return stub


# Register the stub before importing the module under test.
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Also stub `requests` so the top-level import succeeds in isolation.
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test.
import importlib.util, pathlib

_src = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool1_code_review.py"

# If the file doesn't exist on the path (CI environment), fall back to a
# plain import attempt — the stub above keeps things clean either way.
if _src.exists():
    spec = importlib.util.spec_from_file_location("tool1_code_review", _src)
    _mod = importlib.util.module_from_spec(spec)
    sys.modules["tool1_code_review"] = _mod
    spec.loader.exec_module(_mod)
else:
    import tool1_code_review as _mod  # type: ignore

extract_json   = _mod.extract_json
review_pr      = _mod.review_pr
review_repo    = _mod.review_repo
get_output_url = _mod.get_output_url
build_report_md = _mod.build_report_md


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall looks good.",
    "score": 80,
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
    "positive_observations": ["Good naming conventions", "Consistent formatting"],
    "iac_findings": ["S3 bucket missing encryption", "IAM role too permissive"],
}


@pytest.fixture()
def shared_mocks(monkeypatch):
    """Reset shared stub mocks before each test and return them."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.get_pr_diff.reset_mock()
    _shared_stub.post_pr_comment.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()
    return _shared_stub


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJsonHappyPath:
    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        assert extract_json(raw) == MINIMAL_RESULT

    def test_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        assert extract_json(raw) == MINIMAL_RESULT

    def test_markdown_fences_backtick_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fences_plain_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_extra_text_before_json(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["summary"] == "Overall looks good."

    def test_extra_text_after_json(self):
        raw = json.dumps(MINIMAL_RESULT) + "\n\nDone."
        result = extract_json(raw)
        assert result["score"] == 80

    def test_extra_text_both_sides(self):
        raw = "Prefix text\n" + json.dumps(FULL_RESULT) + "\nSuffix text"
        result = extract_json(raw)
        assert len(result["findings"]) == 2

    def test_full_result_roundtrip(self):
        raw = json.dumps(FULL_RESULT)
        assert extract_json(raw) == FULL_RESULT

    def test_newline_inside_string_value_cleaned(self):
        # Simulate a response where Claude inserted a literal newline inside a
        # string value — extract_json should clean it and still parse.
        raw = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        result = extract_json(raw)
        assert "line one" in result["summary"]
        assert result["score"] == 70

    def test_markdown_fence_without_language_tag(self):
        raw = "```\n{\"score\": 55, \"summary\": \"ok\", \"merge_recommendation\": \"APPROVE\", \"findings\": [], \"positive_observations\": [], \"iac_findings\": []}\n```"
        result = extract_json(raw)
        assert result["score"] == 55


class TestExtractJsonEdgeCases:
    def test_json_with_null_line_field(self):
        result_with_null = dict(FULL_RESULT)
        result_with_null["findings"] = [
            {
                "severity": "MEDIUM",
                "category": "correctness",
                "file": "app.py",
                "line": None,
                "issue": "Missing error handling.",
                "recommendation": "Add try/except.",
            }
        ]
        raw = json.dumps(result_with_null)
        parsed = extract_json(raw)
        assert parsed["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["findings"] == []

    def test_score_boundary_zero(self):
        data = dict(MINIMAL_RESULT, score=0)
        assert extract_json(json.dumps(data))["score"] == 0

    def test_score_boundary_hundred(self):
        data = dict(MINIMAL_RESULT, score=100)
        assert extract_json(json.dumps(data))["score"] == 100

    def test_all_severity_levels(self):
        for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            data = dict(MINIMAL_RESULT)
            data["findings"] = [
                {"severity": sev, "category": "security", "file": "f.py",
                 "line": 1, "issue": "x", "recommendation": "y"}
            ]
            result = extract_json(json.dumps(data))
            assert result["findings"][0]["severity"] == sev

    def test_all_merge_recommendations(self):
        for rec in ("APPROVE", "REQUEST_CHANGES", "BLOCK"):
            data = dict(MINIMAL_RESULT, merge_recommendation=rec)
            assert extract_json(json.dumps(data))["merge_recommendation"] == rec


class TestExtractJsonErrorCases:
    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no JSON.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_truncated_json_raises_value_error(self):
        raw = '{"summary": "incomplete", "score": 50'  # no closing brace
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_array_only_raises_value_error(self):
        # A bare JSON array (no outer object) should raise ValueError
        raw = '[1, 2, 3]'
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_deeply_malformed_json_raises_value_error(self):
        raw = "{ bad json !! }"
        with pytest.raises(ValueError):
            extract_json(raw)


# ===========================================================================
# review_pr tests
# ===========================================================================

class TestReviewPr:
    def _setup(self, shared_mocks, result=None):
        if result is None:
            result = FULL_RESULT
        shared_mocks.get_pr_diff.return_value = "diff --git a/app.py ..."
        shared_mocks.call_claude.return_value = json.dumps(result)
        shared_mocks.post_pr_comment.return_value = None
        return result

    def test_returns_parsed_result(self, shared_mocks):
        expected = self._setup(shared_mocks)
        result = review_pr("owner", "repo", 42, "https://ci/run/1")
        assert result == expected

    def test_calls_get_pr_diff_with_correct_args(self, shared_mocks):
        self._setup(shared_mocks)
        review_pr("myowner", "myrepo", 7, "https://ci/run/1")
        shared_mocks.get_pr_diff.assert_called_once_with("myowner", "myrepo", 7)

    def test_calls_call_claude_with_diff(self, shared_mocks):
        self._setup(shared_mocks)
        review_pr("o", "r", 1, "url")
        args, kwargs = shared_mocks.call_claude.call_args
        # First positional arg is the system prompt; second contains the diff
        assert "Review this pull request diff" in args[1]

    def test_calls_post_pr_comment(self, shared_mocks):
        self._setup(shared_mocks)
        review_pr("o", "r", 5, "url")
        shared_mocks.post_pr_comment.assert_called_once()
        call_args = shared_mocks.post_pr_comment.call_args[0]
        assert call_args[0] == "o"
        assert call_args[1] == "r"
        assert call_args[2] == 5

    def test_comment_contains_score(self, shared_mocks):
        self._setup(shared_mocks, result=FULL_RESULT)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert "45" in comment

    def test_comment_contains_recommendation(self, shared_mocks):
        self._setup(shared_mocks, result=FULL_RESULT)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert "REQUEST_CHANGES" in comment

    def test_comment_contains_summary(self, shared_mocks):
        self._setup(shared_mocks)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert FULL_RESULT["summary"] in comment

    def test_comment_contains_finding_details(self, shared_mocks):
        self._setup(shared_mocks)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert "src/app.py" in comment or "app.py" in comment

    def test_comment_no_findings_shows_placeholder(self, shared_mocks):
        self._setup(shared_mocks, result=MINIMAL_RESULT)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert "_No findings_" in comment

    def test_comment_positive_observations(self, shared_mocks):
        self._setup(shared_mocks, result=FULL_RESULT)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert "Good naming conventions" in comment

    def test_comment_empty_positive_observations(self, shared_mocks):
        result = dict(FULL_RESULT, positive_observations=[])
        self._setup(shared_mocks, result=result)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert "_None_" in comment

    def test_comment_contains_auto_generated_footer(self, shared_mocks):
        self._setup(shared_mocks)
        review_pr("o", "r", 1, "url")
        comment = shared_mocks.post_pr_comment.call_args[0][3]
        assert "Auto-generated" in comment

    def test_