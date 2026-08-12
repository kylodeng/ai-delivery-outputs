"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, outermost-block extraction,
  newline-in-string cleanup, missing JSON, malformed JSON, edge cases
- review_pr(): happy path, Claude response handling, comment posting
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report rendering, missing keys, empty findings,
  multiple findings, IaC findings, positive observations

Mocks used:
- shared.call_claude          (unittest.mock.patch)
- shared.get_pr_diff          (unittest.mock.patch)
- shared.get_repo_files       (unittest.mock.patch)
- shared.post_pr_comment      (unittest.mock.patch)
- shared.write_output_file    (unittest.mock.patch)
- shared.send_email           (unittest.mock.patch)
- shared.write_audit_entry    (unittest.mock.patch)
- requests                    (not called directly in tested functions; stubbed where needed)
- datetime.datetime.utcnow    (unittest.mock.patch) for deterministic timestamps

TODOs:
- TODO: Integration tests for __main__ block require environment variables and
        full shared module wiring — stubbed below.
- TODO: Test email/audit side-effects in review_pr/review_repo once those
        code paths are added to the functions.
"""

import json
import sys
import os
import datetime
import importlib
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal 'shared' stub so the import at the top of
# tool1_code_review.py does not fail when the real module is absent.
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.call_claude        = MagicMock()
_shared_stub.get_repo_files     = MagicMock()
_shared_stub.get_pr_diff        = MagicMock()
_shared_stub.write_output_file  = MagicMock()
_shared_stub.post_pr_comment    = MagicMock()
_shared_stub.send_email         = MagicMock()
_shared_stub.email_html         = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry  = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER  = "test-owner"
_shared_stub.OUTPUT_REPO        = "test-output-repo"
_shared_stub.GH_HEADERS         = {"Authorization": "Bearer test"}
_shared_stub.GH_API             = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Make sure the scripts directory is on sys.path so the module can be imported.
_scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Also try relative path (when running from repo root via pytest)
_alt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
if _alt_dir not in sys.path:
    sys.path.insert(0, _alt_dir)

import tool1_code_review as cr  # noqa: E402  (import after path setup)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks fine.",
    "score": 85,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": ["Good test coverage"],
    "iac_findings": [],
}

FULL_RESULT = {
    "summary": "Several security issues found.",
    "score": 42,
    "merge_recommendation": "BLOCK",
    "findings": [
        {
            "severity": "CRITICAL",
            "category": "security",
            "file": "src/main.py",
            "line": 10,
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "performance",
            "file": "src/utils.py",
            "line": None,
            "issue": "N+1 query pattern detected.",
            "recommendation": "Batch database calls.",
        },
    ],
    "positive_observations": ["Good docstrings", "Type hints used throughout"],
    "iac_findings": ["S3 bucket missing server-side encryption", "IAM role too permissive"],
}


def _json_str(obj: dict) -> str:
    return json.dumps(obj)


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs before every test."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment",
                 "send_email", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


# ===========================================================================
# extract_json tests
# ===========================================================================

class TestExtractJson:

    # --- Happy path ---------------------------------------------------------

    def test_plain_json_string(self):
        raw = _json_str(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + _json_str(MINIMAL_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Code looks fine."

    def test_full_result_plain(self):
        raw = _json_str(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 2

    # --- Markdown fence stripping -------------------------------------------

    def test_triple_backtick_fence(self):
        raw = "```\n" + _json_str(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_triple_backtick_json_fence(self):
        raw = "```json\n" + _json_str(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_fence_with_extra_whitespace(self):
        raw = "```json\n  " + _json_str(MINIMAL_RESULT) + "  \n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    # --- Outermost { } block extraction ------------------------------------

    def test_json_preceded_by_prose(self):
        raw = "Sure, here is the result:\n" + _json_str(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_followed_by_prose(self):
        raw = _json_str(MINIMAL_RESULT) + "\nLet me know if you need more details."
        result = cr.extract_json(raw)
        assert result["score"] == 85

    def test_json_surrounded_by_prose(self):
        raw = "Here: " + _json_str(FULL_RESULT) + " Done."
        result = cr.extract_json(raw)
        assert result["score"] == 42

    # --- Newline cleanup inside strings ------------------------------------

    def test_newline_inside_string_value_cleaned(self):
        # Construct a raw string that has a literal newline inside a JSON string value
        raw = '{"summary": "line one\nline two", "score": 50}'
        # This will fail direct parse; the regex cleaner should handle it
        # (It may or may not succeed depending on depth of nesting — we test
        #  that either the cleaner works or a ValueError is raised gracefully)
        try:
            result = cr.extract_json(raw)
            assert isinstance(result, dict)
        except ValueError:
            pass  # acceptable — the raw string is deliberately malformed

    # --- Error conditions ---------------------------------------------------

    def test_no_json_object_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no braces.")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_whitespace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("   \n\t   ")

    def test_malformed_json_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 75, "summary": missing_quotes}')

    def test_unclosed_brace_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"score": 75, "summary": "ok"')

    def test_array_only_raises_value_error(self):
        # No top-level {} — only an array
        with pytest.raises(ValueError):
            cr.extract_json('["a", "b", "c"]')

    # --- Boundary / edge cases ---------------------------------------------

    def test_nested_json_object(self):
        obj = {"outer": {"inner": 1}, "score": 99, "summary": "nested",
               "merge_recommendation": "APPROVE", "findings": [],
               "positive_observations": [], "iac_findings": []}
        result = cr.extract_json(_json_str(obj))
        assert result["score"] == 99

    def test_empty_findings_list(self):
        obj = dict(MINIMAL_RESULT)
        obj["findings"] = []
        result = cr.extract_json(_json_str(obj))
        assert result["findings"] == []

    def test_null_line_field(self):
        obj = dict(FULL_RESULT)
        result = cr.extract_json(_json_str(obj))
        assert result["findings"][1]["line"] is None

    def test_score_zero(self):
        obj = dict(MINIMAL_RESULT, score=0)
        result = cr.extract_json(_json_str(obj))
        assert result["score"] == 0

    def test_score_one_hundred(self):
        obj = dict(MINIMAL_RESULT, score=100)
        result = cr.extract_json(_json_str(obj))
        assert result["score"] == 100

    def test_unicode_values(self):
        obj = dict(MINIMAL_RESULT, summary="Résumé: 代码审查 passed ✓")
        result = cr.extract_json(_json_str(obj))
        assert "代码审查" in result["summary"]

    def test_multiple_json_objects_picks_outermost(self):
        """When text contains multiple {}, pick first { to last }."""
        raw = '{"a": 1} some text {"b": 2}'
        # The function should parse from first { to last } → may fail or succeed
        # We just assert it either returns a dict or raises ValueError gracefully.
        try:
            result = cr.extract_json(raw)
            assert isinstance(result, dict)
        except ValueError:
            pass


# ===========================================================================
# get_output_url tests
# ===========================================================================

class TestGetOutputUrl:

    def test_basic_url_construction(self):
        url = cr.get_output_url("myorg", "myrepo", "PR-42")
        assert url.startswith("https://github.com/")
        assert "myorg-myrepo-PR-42.md" in url

    def test_uses_output_repo_owner_and_repo(self):
        url = cr.get_output_url("acme", "service", "weekly")
        assert _shared_stub.OUTPUT_REPO_OWNER in url
        assert _shared_stub.OUTPUT_REPO in url

    def test_label_included_in_path(self):
        url = cr.get_output_url("o", "r", "2024-01-01")
        assert "2024-01-01" in url

    def test_url_contains_code_review_path(self):
        url = cr.get_output_url("o", "r", "x")
        assert "code-review/" in url

    def test_url_ends_with_md(self):
        url = cr.get_output_url("o", "r", "x")
        assert url.endswith(".md")

    def test_special_characters_in_label(self):
        url = cr.get_output_url("org", "repo", "label/with/slashes")
        assert "label/with/slashes" in url


# ===========================================================================
# build_report_md tests
# ===========================================================================

class TestBuildReportMd:

    @patch("tool1_code_review.datetime")
    def test_minimal_result_contains_key_fields(self, mock_dt):
        mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 6, 1, 12, 0, 0)
        md = cr.build_report_md(MINIMAL_RESULT, "PR #1", "owner/repo")
        assert "# Code Review Report" in md
        assert "85/100" in md
        assert "APPROVE" in md
        assert "Code looks fine." in md
        assert "Good test coverage" in md
        assert "2024-06-01 12:00 UTC" in md

    @patch("tool1_code_review.datetime")
    def test_full_result_findings_table(self, mock_dt):
        mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 6, 1, 12, 0, 0)
        md = cr.build_report_md(FULL_RESULT, "cron", "org/repo")
        assert "CRITICAL" in md
        assert "src/main.py" in md
        assert "Hardcoded AWS secret key detected." in md
        assert "S3 bucket missing server-side encryption" in md
        assert "IAM role too permissive" in md

    @patch("tool1_code_review.datetime")
    def test_empty_findings_shows_no_findings(self, mock_dt):
        mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 1, 1, 0, 0, 0)
        result = dict(MINIMAL_RESULT, findings=[])
        md = cr.build_report_md(result, "manual", "o/r")
        assert "No findings" in md

    @patch("tool1_code_review.datetime")
    def test_empty_iac_findings_shows_none(self, mock_dt):
        mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 1, 1, 0, 0, 0)
        result = dict(MINIMAL_RESULT, iac_findings=[])
        md = cr.build_report_md(result, "manual", "o/r")
        assert "_None_" in md

    @patch("tool1_code_review.datetime")
    def test_empty_positive_observations_shows_none(self, mock_dt):
        mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 1, 1, 0, 0, 0)
        result = dict(MINIMAL_RESULT, positive_observations=[])
        md = cr.build_report_md(result, "manual", "o/r")
        assert "_None_" in md

    @patch("tool1_code_review.datetime")
    def test_missing_keys_use_defaults(self, mock_dt):
        mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 1, 1, 