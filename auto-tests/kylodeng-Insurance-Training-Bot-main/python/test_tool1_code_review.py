"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): JSON extraction from raw Claude responses (happy path, markdown fences,
  embedded newlines, missing JSON, malformed JSON, outermost-block extraction)
- review_pr(): PR diff retrieval, Claude call, comment posting, result return
- review_repo(): Repo file retrieval, Claude call, result return
- get_output_url(): URL construction
- build_report_md(): Markdown report generation (full data, missing fields, empty lists)

Mocks used:
- shared.call_claude (patched via 'tool1_code_review.call_claude')
- shared.get_repo_files (patched via 'tool1_code_review.get_repo_files')
- shared.get_pr_diff (patched via 'tool1_code_review.get_pr_diff')
- shared.write_output_file (patched via 'tool1_code_review.write_output_file')
- shared.post_pr_comment (patched via 'tool1_code_review.post_pr_comment')
- shared.send_email (patched via 'tool1_code_review.send_email')
- shared.write_audit_entry (patched via 'tool1_code_review.write_audit_entry')
- datetime.datetime (patched for deterministic timestamps)

TODOs:
- TODO: Test __main__ block — needs subprocess or importlib approach with env var injection
- TODO: Test integration with real Claude API responses — requires API key and live network
- TODO: Test write_output_file and write_audit_entry side effects once shared module is available
"""

import json
import re
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../.github/scripts"))

import tool1_code_review as module


# ---------------------------------------------------------------------------
# Fixtures / shared test data
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Code looks reasonable overall.",
    "score": 72,
    "merge_recommendation": "APPROVE",
    "findings": [],
    "positive_observations": [],
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
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables or a secrets manager.",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": None,
            "issue": "Bare except clause used.",
            "recommendation": "Catch specific exceptions instead.",
        },
    ],
    "positive_observations": ["Good test coverage.", "Consistent naming conventions."],
    "iac_findings": ["S3 bucket missing encryption.", "IAM role overly permissive."],
}


# ---------------------------------------------------------------------------
# extract_json — happy paths
# ---------------------------------------------------------------------------

class TestExtractJsonHappyPath:

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = module.extract_json(raw)
        assert result == MINIMAL_RESULT

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(FULL_RESULT) + "\n   "
        result = module.extract_json(raw)
        assert result == FULL_RESULT

    def test_json_wrapped_in_backtick_fences(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = module.extract_json(raw)
        assert result == MINIMAL_RESULT

    def test_json_wrapped_in_json_language_fence(self):
        raw = "```json\n" + json.dumps(FULL_RESULT) + "\n```"
        result = module.extract_json(raw)
        assert result == FULL_RESULT

    def test_json_with_preamble_text_before_brace(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT)
        result = module.extract_json(raw)
        assert result == MINIMAL_RESULT

    def test_json_with_trailing_text_after_brace(self):
        raw = json.dumps(MINIMAL_RESULT) + "\nThat is the end."
        result = module.extract_json(raw)
        assert result == MINIMAL_RESULT

    def test_json_with_preamble_and_trailing_text(self):
        raw = "Preamble text.\n" + json.dumps(FULL_RESULT) + "\nTrailing text."
        result = module.extract_json(raw)
        assert result == FULL_RESULT

    def test_score_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = module.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = module.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_all_severity_levels_parsed(self):
        findings = [
            {"severity": sev, "category": "security", "file": "f.py",
             "line": 1, "issue": "x", "recommendation": "y"}
            for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        ]
        data = {**MINIMAL_RESULT, "findings": findings}
        result = module.extract_json(json.dumps(data))
        assert len(result["findings"]) == 4

    def test_merge_recommendation_block(self):
        data = {**MINIMAL_RESULT, "merge_recommendation": "BLOCK"}
        result = module.extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "BLOCK"


# ---------------------------------------------------------------------------
# extract_json — markdown / formatting edge cases
# ---------------------------------------------------------------------------

class TestExtractJsonMarkdownEdgeCases:

    def test_fence_without_closing_backticks(self):
        """Only opening fence — should still find JSON via brace search."""
        raw = "```json\n" + json.dumps(MINIMAL_RESULT)
        result = module.extract_json(raw)
        assert result["score"] == MINIMAL_RESULT["score"]

    def test_newline_inside_string_value(self):
        """Literal newline inside a JSON string value — cleaned by regex."""
        inner = json.dumps(MINIMAL_RESULT)
        # Inject a literal newline inside the summary string value
        broken = inner.replace(
            '"Code looks reasonable overall."',
            '"Code looks\nreasonable overall."'
        )
        result = module.extract_json(broken)
        assert "summary" in result

    def test_extra_whitespace_in_fence_prefix(self):
        raw = "```  \n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = module.extract_json(raw)
        assert result == MINIMAL_RESULT

    def test_nested_json_object_in_findings(self):
        """Findings list with null line field."""
        data = {**FULL_RESULT}
        result = module.extract_json(json.dumps(data))
        null_line_findings = [f for f in result["findings"] if f["line"] is None]
        assert len(null_line_findings) == 1


# ---------------------------------------------------------------------------
# extract_json — error / negative cases
# ---------------------------------------------------------------------------

class TestExtractJsonErrors:

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            module.extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            module.extract_json("   \n\t  ")

    def test_no_braces_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            module.extract_json("This is just plain text with no JSON.")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            module.extract_json('{"key": "value", "broken":}')

    def test_truncated_json_raises(self):
        with pytest.raises(ValueError):
            module.extract_json('{"summary": "incomplete...')

    def test_array_only_raises(self):
        """A bare JSON array has no outermost { } and should raise."""
        with pytest.raises(ValueError, match="No JSON object found"):
            module.extract_json('[1, 2, 3]')

    def test_debug_output_on_failure(self, capsys):
        with pytest.raises(ValueError):
            module.extract_json('{"broken":}')
        captured = capsys.readouterr()
        assert "[DEBUG]" in captured.out

    def test_almost_valid_nested_braces(self):
        """Outermost braces present but content is still broken."""
        with pytest.raises(ValueError):
            module.extract_json('{ "a": { "b": broken } }')


# ---------------------------------------------------------------------------
# extract_json — boundary values
# ---------------------------------------------------------------------------

class TestExtractJsonBoundaryValues:

    def test_single_key_object(self):
        result = module.extract_json('{"only_key": "value"}')
        assert result == {"only_key": "value"}

    def test_empty_object(self):
        result = module.extract_json('{}')
        assert result == {}

    def test_very_large_payload(self):
        data = {**MINIMAL_RESULT, "findings": [
            {"severity": "LOW", "category": "maintainability",
             "file": f"file_{i}.py", "line": i, "issue": "x", "recommendation": "y"}
            for i in range(200)
        ]}
        result = module.extract_json(json.dumps(data))
        assert len(result["findings"]) == 200

    def test_unicode_in_values(self):
        data = {**MINIMAL_RESULT, "summary": "Résumé avec unicode: 日本語"}
        result = module.extract_json(json.dumps(data))
        assert "Résumé" in result["summary"]


# ---------------------------------------------------------------------------
# get_output_url
# ---------------------------------------------------------------------------

class TestGetOutputUrl:

    def test_basic_url_construction(self):
        with patch.object(module, "OUTPUT_REPO_OWNER", "myorg"), \
             patch.object(module, "OUTPUT_REPO", "myrepo"):
            url = module.get_output_url("owner1", "repo1", "2024-01-01")
        assert url == "https://github.com/myorg/myrepo/blob/main/code-review/owner1-repo1-2024-01-01.md"

    def test_url_contains_owner_and_repo(self):
        with patch.object(module, "OUTPUT_REPO_OWNER", "org"), \
             patch.object(module, "OUTPUT_REPO", "out"):
            url = module.get_output_url("my-owner", "my-repo", "label")
        assert "my-owner" in url
        assert "my-repo" in url
        assert "label" in url

    def test_url_starts_with_https(self):
        with patch.object(module, "OUTPUT_REPO_OWNER", "org"), \
             patch.object(module, "OUTPUT_REPO", "out"):
            url = module.get_output_url("a", "b", "c")
        assert url.startswith("https://github.com/")

    def test_url_with_special_chars_in_label(self):
        with patch.object(module, "OUTPUT_REPO_OWNER", "org"), \
             patch.object(module, "OUTPUT_REPO", "out"):
            url = module.get_output_url("owner", "repo", "pr-42")
        assert "pr-42" in url


# ---------------------------------------------------------------------------
# build_report_md
# ---------------------------------------------------------------------------

class TestBuildReportMd:

    FIXED_NOW = "2024-06-15 10:30 UTC"

    def _build(self, result, source="pr", context="owner/repo#1"):
        fake_dt = MagicMock()
        fake_dt.utcnow.return_value.strftime.return_value = self.FIXED_NOW
        with patch("tool1_code_review.datetime") as mock_dt:
            mock_dt.datetime = fake_dt
            return module.build_report_md(result, source, context)

    def test_contains_score(self):
        md = self._build(FULL_RESULT)
        assert "45/100" in md

    def test_contains_recommendation(self):
        md = self._build(FULL_RESULT)
        assert "REQUEST_CHANGES" in md

    def test_contains_summary(self):
        md = self._build(FULL_RESULT)
        assert "Several security issues detected." in md

    def test_contains_source(self):
        md = self._build(FULL_RESULT, source="scheduled")
        assert "scheduled" in md

    def test_contains_context(self):
        md = self._build(FULL_RESULT, context="myowner/myrepo#99")
        assert "myowner/myrepo#99" in md

    def test_contains_timestamp(self):
        md = self._build(FULL_RESULT)
        assert self.FIXED_NOW in md

    def test_contains_findings_table_rows(self):
        md = self._build(FULL_RESULT)
        assert "src/example.py" in md
        assert "Hardcoded password detected." in md

    def test_contains_iac_findings(self):
        md = self._build(FULL_RESULT)
        assert "S3 bucket missing encryption." in md
        assert "IAM role overly permissive." in md

    def test_contains_positive_observations(self):
        md = self._build(FULL_RESULT)
        assert "Good test coverage." in md
        assert "Consistent naming conventions." in md

    def test_empty_findings_shows_no_findings_placeholder(self):
        md = self._build(MINIMAL_RESULT)
        assert "No findings" in md

    def test_empty_iac_findings_shows_none(self):
        md = self._build(MINIMAL_RESULT)
        assert "_None_" in md

    def test_empty_positive_observations_shows_none(self):
        md = self._build(MINIMAL_RESULT)
        assert "_None_" in md

    def test_missing_score_shows_na(self):
        result = {k: v for k, v in FULL_RESULT.items() if k != "score"}
        md = self._build(result)
        assert "N/A" in md

    def test_missing_recommendation_shows_na(self):
        result = {k: v for k, v in FULL_RESULT.items() if k != "merge_recommendation"}
        md = self._build(result)
        assert "N/A" in md

    def test_missing_summary_shows_default(self):
        result = {k: v for k, v in FULL_RESULT.items() if k != "summary"}
        md = self._build(result)
        assert "No summary provided." in md

    def test_report_starts_with_heading(self):
        md = self._build(MINIMAL_RESULT)
        assert md.startswith("# Code Review Report")

    def test_report_contains_auto_generated_footer(self):
        md = self._build(MINIMAL_RESULT)
        assert "Auto-generated by AI Delivery Bot" in md

    def test_finding_with_null_line(self):
        result = {
            **MINIMAL_RESULT,
            "findings": [{
                "severity": "LOW",
                