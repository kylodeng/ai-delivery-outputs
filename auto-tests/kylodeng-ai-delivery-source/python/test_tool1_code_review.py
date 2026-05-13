"""
Test suite for tool1_code_review.py

What is tested:
    - extract_json: happy path, markdown fence stripping, newline cleaning,
      outermost-brace extraction, error conditions, edge cases
    - review_pr: happy path, comment formatting, return value
    - review_repo: happy path, content truncation behaviour
    - get_output_url: URL construction
    - build_report_md: full report rendering, empty/missing fields, findings table

Mocks used:
    - shared.call_claude            (patched via unittest.mock.patch)
    - shared.get_pr_diff            (patched)
    - shared.get_repo_files         (patched)
    - shared.post_pr_comment        (patched)
    - shared.write_output_file      (patched)
    - shared.send_email             (patched)
    - shared.write_audit_entry      (patched)
    - requests                      (not called directly by the functions under test,
                                     but imported; patched at module level for safety)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var wiring
    - TODO: Test for email dispatch path (needs send_email + email_html context)
    - TODO: Test write_output_file / write_audit_entry call counts inside review_pr
            once those call-sites are confirmed in the full source
"""

import json
import sys
import os
import datetime
import types
import importlib
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal stub for `shared` so the import succeeds
# without the real module being available in the test environment.
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
_shared_stub.OUTPUT_REPO_OWNER  = "test-owner"
_shared_stub.OUTPUT_REPO        = "test-output-repo"
_shared_stub.GH_HEADERS         = {"Authorization": "token fake"}
_shared_stub.GH_API             = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
sys.path.insert(0, script_dir)

import importlib.util, pathlib

_src_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool1_code_review.py"

# We load the module from the file path so the test file can live anywhere.
_spec = importlib.util.spec_from_file_location("tool1_code_review", str(_src_path))
_mod  = importlib.util.module_from_spec(_spec)
# Inject the stub before exec so `from shared import …` resolves correctly.
sys.modules["tool1_code_review"] = _mod
_spec.loader.exec_module(_mod)

extract_json    = _mod.extract_json
review_pr       = _mod.review_pr
review_repo     = _mod.review_repo
get_output_url  = _mod.get_output_url
build_report_md = _mod.build_report_md


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs before each test."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


MINIMAL_RESULT = {
    "summary": "Looks good overall.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Clean structure"],
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
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        },
        {
            "severity": "LOW",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Missing docstring.",
            "recommendation": "Add a module-level docstring.",
        },
    ],
    "positive_observations": ["Good test coverage", "Consistent naming"],
    "iac_findings": ["S3 bucket lacks versioning", "IAM role is overly permissive"],
}


# ===========================================================================
# extract_json
# ===========================================================================

class TestExtractJson:

    def test_plain_json(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = extract_json(raw)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = extract_json(raw)
        assert result["summary"] == "Looks good overall."

    def test_markdown_fence_triple_backtick(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["score"] == 82

    def test_markdown_fence_with_language_hint(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd."
        result = extract_json(raw)
        assert result["score"] == 82

    def test_newline_inside_string_value_cleaned(self):
        # Simulate a response with a literal newline inside a JSON string value
        raw = '{"summary": "First line\nSecond line", "score": 50, ' \
              '"merge_recommendation": "APPROVE", "findings": [], ' \
              '"positive_observations": [], "iac_findings": []}'
        result = extract_json(raw)
        assert "First line" in result["summary"]
        assert result["score"] == 50

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is just plain text with no braces.")

    def test_invalid_json_in_braces_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("{ completely: invalid: json }")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    def test_nested_objects_parsed_correctly(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "HIGH"

    def test_findings_line_is_null(self):
        raw = json.dumps(FULL_RESULT)
        result = extract_json(raw)
        assert result["findings"][1]["line"] is None

    def test_extra_text_before_and_after_brace_block(self):
        inner = json.dumps({"score": 77, "merge_recommendation": "BLOCK",
                            "summary": "s", "findings": [],
                            "positive_observations": [], "iac_findings": []})
        raw = f"Preamble text {inner} trailing text"
        result = extract_json(raw)
        assert result["score"] == 77

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_unicode_content_preserved(self):
        data = {**MINIMAL_RESULT, "summary": "Résumé: ñoño 日本語"}
        result = extract_json(json.dumps(data, ensure_ascii=False))
        assert "日本語" in result["summary"]

    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_all_valid_merge_recommendations(self, recommendation):
        data = {**MINIMAL_RESULT, "merge_recommendation": recommendation}
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_all_valid_severities(self, severity):
        finding = {
            "severity": severity, "category": "security",
            "file": "a.py", "line": 1,
            "issue": "x", "recommendation": "y",
        }
        data = {**MINIMAL_RESULT, "findings": [finding]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity


# ===========================================================================
# review_pr
# ===========================================================================

class TestReviewPr:

    def _setup(self, result_override=None):
        result = result_override or FULL_RESULT
        _shared_stub.get_pr_diff.return_value = "diff --git a/src/main.py ..."
        _shared_stub.call_claude.return_value = json.dumps(result)
        _shared_stub.post_pr_comment.return_value = None
        return result

    def test_returns_parsed_result(self):
        self._setup()
        out = review_pr("acme", "myrepo", 42, "https://ci/run/1")
        assert out["score"] == FULL_RESULT["score"]
        assert out["merge_recommendation"] == "REQUEST_CHANGES"

    def test_get_pr_diff_called_correctly(self):
        self._setup()
        review_pr("acme", "myrepo", 99, "https://ci/run/1")
        _shared_stub.get_pr_diff.assert_called_once_with("acme", "myrepo", 99)

    def test_call_claude_receives_diff_in_prompt(self):
        self._setup()
        review_pr("acme", "myrepo", 1, "https://ci/run/1")
        _, prompt = _shared_stub.call_claude.call_args[0]
        assert "diff" in prompt.lower()

    def test_post_pr_comment_called_once(self):
        self._setup()
        review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _shared_stub.post_pr_comment.assert_called_once()

    def test_comment_contains_score(self):
        self._setup()
        review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "45" in comment

    def test_comment_contains_recommendation(self):
        self._setup()
        review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "REQUEST_CHANGES" in comment

    def test_comment_contains_finding_details(self):
        self._setup()
        review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "Hardcoded password" in comment

    def test_comment_no_findings_shows_placeholder(self):
        self._setup(result_override=MINIMAL_RESULT)
        review_pr("acme", "myrepo", 3, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "_No findings_" in comment

    def test_comment_no_positive_obs_shows_placeholder(self):
        minimal_no_pos = {**MINIMAL_RESULT, "positive_observations": []}
        self._setup(result_override=minimal_no_pos)
        review_pr("acme", "myrepo", 3, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "_None_" in comment

    def test_comment_includes_positive_observations(self):
        self._setup()
        review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "Good test coverage" in comment

    def test_finding_with_null_line_renders_na(self):
        self._setup()
        review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "n/a" in comment

    def test_finding_with_numeric_line_rendered(self):
        self._setup()
        review_pr("acme", "myrepo", 7, "https://ci/run/1")
        _, _, _, comment = _shared_stub.post_pr_comment.call_args[0]
        assert "10" in comment  # line number from FULL_RESULT finding

    def test_claude_called_with_system_prompt(self):
        self._setup()
        review_pr("acme", "myrepo", 1, "https://ci/run/1")
        system_arg = _shared_stub.call_claude.call_args[0][0]
        assert "senior code reviewer" in system_arg.lower()

    def test_markdown_fenced_response_handled(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        _shared_stub.post_pr_comment.return_value = None
        result = review_pr("acme", "myrepo", 1, "https://ci/run/1")
        assert result["score"] == 82

    def test_invalid_claude_response_raises(self):
        _shared_stub.get_pr_diff.return_value = "some diff"
        _shared_stub.call_claude.return_value = "I cannot provide a review right now."
        with pytest.raises(ValueError):
            review_pr("acme", "myrepo", 1, "https://ci/run/1")


#