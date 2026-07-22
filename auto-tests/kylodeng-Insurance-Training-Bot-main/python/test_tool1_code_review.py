"""
Test suite for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, nested JSON extraction, newline cleaning,
  missing JSON, malformed JSON, edge cases (empty string, only fences)
- review_pr: happy path, Claude response handling, comment posting, result returned
- review_repo: happy path, content truncation, file filtering
- get_output_url: URL construction with various owner/repo/label combos
- build_report_md: full report, empty findings, empty iac/positive, missing keys

Mocks used:
- shared.call_claude (patched via tool1_code_review.call_claude)
- shared.get_pr_diff (patched via tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched via tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched via tool1_code_review.post_pr_comment)
- shared.write_output_file (patched via tool1_code_review.write_output_file)
- shared.send_email (patched via tool1_code_review.send_email)
- shared.write_audit_entry (patched via tool1_code_review.write_audit_entry)
- requests (not directly called in tested functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup + subprocess
- TODO: Test write_output_file / send_email orchestration once main() is complete
  (source is truncated)
"""

import json
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
import importlib, sys, os, types

# Provide a minimal stub for `shared` so the import doesn't fail when the
# real shared.py isn't on the path during testing.
def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude        = MagicMock()
    stub.get_repo_files     = MagicMock()
    stub.get_pr_diff        = MagicMock()
    stub.write_output_file  = MagicMock()
    stub.post_pr_comment    = MagicMock()
    stub.send_email         = MagicMock()
    stub.email_html         = MagicMock()
    stub.write_audit_entry  = MagicMock()
    stub.OUTPUT_REPO_OWNER  = "test-owner"
    stub.OUTPUT_REPO        = "test-output-repo"
    stub.GH_HEADERS         = {"Authorization": "Bearer fake"}
    stub.GH_API             = "https://api.github.com"
    return stub


if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Now import the module under test
import tool1_code_review as cr

# Convenience: shared stub reference
_shared = sys.modules["shared"]


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

VALID_RESULT = {
    "summary": "Code looks generally good with minor issues.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/main.py",
            "line": 42,
            "issue": "Hardcoded AWS secret key found.",
            "recommendation": "Use environment variables or a secrets manager.",
        }
    ],
    "positive_observations": ["Good use of type hints.", "Tests are present."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def _fenced(content: str, lang: str = "") -> str:
    return f"```{lang}\n{content}\n```"


# ===========================================================================
# extract_json – happy paths
# ===========================================================================

class TestExtractJsonHappyPath:

    def test_plain_valid_json(self):
        result = cr.extract_json(VALID_JSON_STR)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_leading_trailing_whitespace(self):
        raw = f"   \n{VALID_JSON_STR}\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == VALID_RESULT["summary"]

    def test_markdown_fence_no_lang(self):
        raw = _fenced(VALID_JSON_STR)
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_markdown_fence_with_json_lang(self):
        raw = _fenced(VALID_JSON_STR, "json")
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_prefix_text_before_json(self):
        raw = f"Here is the review:\n{VALID_JSON_STR}"
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_suffix_text_after_json(self):
        raw = f"{VALID_JSON_STR}\nSome trailing text."
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_prefix_and_suffix_text(self):
        raw = f"Preamble text.\n{VALID_JSON_STR}\nTrailing text."
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_minimal_json(self):
        minimal = json.dumps({"summary": "ok", "score": 50,
                              "merge_recommendation": "APPROVE",
                              "findings": [], "positive_observations": [],
                              "iac_findings": []})
        result = cr.extract_json(minimal)
        assert result["score"] == 50

    def test_score_zero(self):
        data = dict(VALID_RESULT, score=0)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = dict(VALID_RESULT, score=100)
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_null_line_value(self):
        finding_with_null_line = dict(VALID_RESULT["findings"][0], line=None)
        data = dict(VALID_RESULT, findings=[finding_with_null_line])
        result = cr.extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_empty_findings_list(self):
        data = dict(VALID_RESULT, findings=[])
        result = cr.extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_multiple_findings(self):
        f2 = {
            "severity": "LOW",
            "category": "maintainability",
            "file": "utils.py",
            "line": 10,
            "issue": "Function is too long.",
            "recommendation": "Refactor into smaller functions.",
        }
        data = dict(VALID_RESULT, findings=[VALID_RESULT["findings"][0], f2])
        result = cr.extract_json(json.dumps(data))
        assert len(result["findings"]) == 2


# ===========================================================================
# extract_json – newline cleaning path
# ===========================================================================

class TestExtractJsonNewlineCleaning:

    def test_newline_inside_string_value_is_cleaned(self):
        """Simulate Claude putting a literal \n inside a JSON string value."""
        # Craft a JSON string that is broken by a literal newline in a value
        broken = '{"summary": "line one\nline two", "score": 70, ' \
                 '"merge_recommendation": "APPROVE", "findings": [], ' \
                 '"positive_observations": [], "iac_findings": []}'
        # This will fail direct parse; the cleaning regex should fix it
        result = cr.extract_json(broken)
        assert result["score"] == 70
        assert "line one" in result["summary"]


# ===========================================================================
# extract_json – error conditions
# ===========================================================================

class TestExtractJsonErrors:

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("   \n\t  ")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This is just plain text with no JSON at all.")

    def test_unclosed_brace_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"summary": "incomplete"')

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json('{"summary": "bad json",,,}')

    def test_only_fence_no_content_raises(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            cr.extract_json("```\n```")

    def test_array_root_raises_or_returns(self):
        """Root-level array has no { } so should raise ValueError."""
        with pytest.raises(ValueError):
            cr.extract_json("[1, 2, 3]")

    def test_completely_invalid_content_raises(self):
        with pytest.raises(ValueError):
            cr.extract_json("!@#$%^&*()")


# ===========================================================================
# extract_json – boundary / edge cases
# ===========================================================================

class TestExtractJsonEdgeCases:

    def test_nested_json_object_in_findings(self):
        """Findings with extra nested keys should still parse."""
        data = dict(VALID_RESULT)
        result = cr.extract_json(json.dumps(data))
        assert isinstance(result["findings"], list)

    def test_very_long_summary(self):
        long_summary = "A" * 5000
        data = dict(VALID_RESULT, summary=long_summary)
        result = cr.extract_json(json.dumps(data))
        assert len(result["summary"]) == 5000

    def test_unicode_in_values(self):
        data = dict(VALID_RESULT, summary="Résumé: 你好 мир")
        result = cr.extract_json(json.dumps(data))
        assert "Résumé" in result["summary"]

    def test_fenced_with_extra_blank_lines(self):
        raw = f"```json\n\n{VALID_JSON_STR}\n\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 82

    def test_score_boundary_1(self):
        data = dict(VALID_RESULT, score=1)
        assert cr.extract_json(json.dumps(data))["score"] == 1

    def test_score_boundary_99(self):
        data = dict(VALID_RESULT, score=99)
        assert cr.extract_json(json.dumps(data))["score"] == 99


# ===========================================================================
# get_output_url
# ===========================================================================

class TestGetOutputUrl:

    def test_basic_url(self):
        url = cr.get_output_url("myowner", "myrepo", "pr-42")
        assert "myowner-myrepo-pr-42.md" in url
        assert url.startswith("https://github.com/")

    def test_contains_output_repo_info(self):
        url = cr.get_output_url("o", "r", "label")
        # Uses OUTPUT_REPO_OWNER / OUTPUT_REPO from shared stub
        assert cr.OUTPUT_REPO_OWNER in url
        assert cr.OUTPUT_REPO in url

    def test_url_format(self):
        url = cr.get_output_url("acme", "platform", "weekly-2024-01-01")
        expected = (
            f"https://github.com/{cr.OUTPUT_REPO_OWNER}/{cr.OUTPUT_REPO}"
            f"/blob/main/code-review/acme-platform-weekly-2024-01-01.md"
        )
        assert url == expected

    def test_special_chars_in_label_pass_through(self):
        # Function does no sanitisation – just verify it doesn't crash
        url = cr.get_output_url("owner", "repo", "label/with/slashes")
        assert "label/with/slashes" in url

    def test_empty_strings(self):
        url = cr.get_output_url("", "", "")
        assert url.startswith("https://github.com/")


# ===========================================================================
# build_report_md
# ===========================================================================

class TestBuildReportMd:

    def test_contains_score(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        assert "82" in md

    def test_contains_recommendation(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        assert "APPROVE" in md

    def test_contains_summary(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        assert VALID_RESULT["summary"] in md

    def test_contains_finding_severity(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        assert "HIGH" in md

    def test_contains_finding_file(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        assert "src/main.py" in md

    def test_contains_iac_finding(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        assert "S3 bucket lacks versioning" in md

    def test_contains_positive_observation(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        assert "Good use of type hints" in md

    def test_contains_source(self):
        md = cr.build_report_md(VALID_RESULT, "scheduled-cron", "owner/repo")
        assert "scheduled-cron" in md

    def test_contains_context(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "acme/platform")
        assert "acme/platform" in md

    def test_contains_generated_timestamp(self):
        md = cr.build_report_md(VALID_RESULT, "PR #1", "owner/repo")
        year = str(datetime.datetime.utcnow().year)
        assert year in md

    def test_empty_findings_shows_no_findings_row(self):
        data = dict(VALID_RESULT, findings=[])
        md = cr.build_report_md(data, "PR #1", "owner/repo")
        assert "No findings" in md

    def test_empty_iac_shows_none(self):
        data = dict(VALID_RESULT, iac_findings=[])
        md = cr.build_report_md(data, "PR #1", "owner/repo")
        assert "_None_" in md

    def test_empty_positive_shows_none(self):
        data = dict(VALID_RESULT, positive_observations=[])
        md = cr.build_report_md(data, "PR #1", "owner/repo")
        assert "_None_" in md

    def test_missing_score_key_shows_na(self):
        data = {k: v for k, v in VALID_RESULT.items() if k != "score"}
        md = cr.build_report_md(data, "PR #1", "owner/repo