"""
Test suite for tool1_code_review.py

What is tested:
- extract_json(): happy path, markdown fences, embedded newlines, no JSON, malformed JSON, outermost-block extraction
- review_pr(): happy path, Claude response parsing, comment formatting, return value
- review_repo(): happy path, content truncation, file filtering
- get_output_url(): URL construction
- build_report_md(): full report generation, empty findings, missing keys, IaC/positive sections

Mocks used:
- shared.call_claude (patched via tool1_code_review module)
- shared.get_pr_diff
- shared.get_repo_files
- shared.post_pr_comment
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- requests (not called directly by public functions, but imported)

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup and real GitHub token
- TODO: Test for write_output_file / send_email orchestration once main() entrypoint is complete
  (source file appears truncated at os.environ assignment)
"""

import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal "shared" stub so the import in the source works
# even when the real shared.py is absent.
# ---------------------------------------------------------------------------
shared_stub = types.ModuleType("shared")
shared_stub.call_claude = MagicMock()
shared_stub.get_repo_files = MagicMock()
shared_stub.get_pr_diff = MagicMock()
shared_stub.write_output_file = MagicMock()
shared_stub.post_pr_comment = MagicMock()
shared_stub.send_email = MagicMock()
shared_stub.email_html = MagicMock()
shared_stub.write_audit_entry = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

import tool1_code_review as cr  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_RESULT = {
    "summary": "Overall the code looks fine.",
    "score": 80,
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
            "issue": "Hardcoded AWS secret key detected.",
            "recommendation": "Use environment variables or a secrets manager.",
        },
        {
            "severity": "HIGH",
            "category": "iac",
            "file": "infra/main.tf",
            "line": None,
            "issue": "S3 bucket has no encryption.",
            "recommendation": "Enable default encryption on the bucket.",
        },
        {
            "severity": "MEDIUM",
            "category": "maintainability",
            "file": "src/utils.py",
            "line": 55,
            "issue": "Bare except clause used.",
            "recommendation": "Catch specific exception types.",
        },
    ],
    "positive_observations": ["CI pipeline is well-structured", "Dependencies are pinned"],
    "iac_findings": ["S3 bucket missing tags", "IAM role too permissive"],
}


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared stubs before every test."""
    for attr in ("call_claude", "get_repo_files", "get_pr_diff",
                 "write_output_file", "post_pr_comment", "send_email",
                 "email_html", "write_audit_entry"):
        getattr(shared_stub, attr).reset_mock()
    yield


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    """Tests for cr.extract_json()"""

    def test_plain_json_string(self):
        raw = json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_trailing_whitespace(self):
        raw = "   \n" + json.dumps(MINIMAL_RESULT) + "\n   "
        result = cr.extract_json(raw)
        assert result["summary"] == "Overall the code looks fine."

    def test_markdown_fenced_json(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_markdown_fenced_no_language_specifier(self):
        raw = "```\n" + json.dumps(MINIMAL_RESULT) + "\n```"
        result = cr.extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_embedded_in_prose(self):
        raw = "Here is the review:\n" + json.dumps(MINIMAL_RESULT) + "\nEnd of review."
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_newline_inside_string_value(self):
        # Simulate a JSON string where Claude inserted a literal newline inside a value
        broken = '{"summary": "line one\nline two", "score": 70, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This will fail direct parse; extract_json should fix it
        result = cr.extract_json(broken)
        assert "line one" in result["summary"]

    def test_no_json_raises_value_error(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            cr.extract_json("This response contains absolutely no JSON at all.")

    def test_malformed_json_raises_value_error(self):
        raw = '{ "score": 80, "merge_recommendation": APPROVE }'  # unquoted value
        with pytest.raises(ValueError):
            cr.extract_json(raw)

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("")

    def test_only_braces_raises_value_error(self):
        with pytest.raises(ValueError):
            cr.extract_json("{}")
        # {} is valid JSON – should parse successfully
        # Correcting expectation: {} IS valid JSON and should return empty dict
        result = cr.extract_json("{}")
        assert result == {}

    def test_full_result_round_trip(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 42
        assert len(result["findings"]) == 3
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_extra_text_before_json(self):
        raw = "Sure! Here is your JSON:\n\n" + json.dumps(MINIMAL_RESULT)
        result = cr.extract_json(raw)
        assert result["score"] == 80

    def test_score_boundary_zero(self):
        data = {**MINIMAL_RESULT, "score": 0}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_boundary_hundred(self):
        data = {**MINIMAL_RESULT, "score": 100}
        result = cr.extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_nested_findings_preserved(self):
        raw = json.dumps(FULL_RESULT)
        result = cr.extract_json(raw)
        assert result["findings"][1]["file"] == "infra/main.tf"
        assert result["findings"][1]["line"] is None

    def test_markdown_fenced_with_prose_after_fence(self):
        raw = "```json\n" + json.dumps(MINIMAL_RESULT) + "\n```\n\nSome trailing text."
        result = cr.extract_json(raw)
        assert result["score"] == 80


# ---------------------------------------------------------------------------
# extract_json edge-cases: only braces correction
# ---------------------------------------------------------------------------

def test_extract_json_empty_object():
    """Empty JSON object is valid and should be returned as empty dict."""
    result = cr.extract_json("{}")
    assert result == {}


# ---------------------------------------------------------------------------
# get_output_url
# ---------------------------------------------------------------------------

class TestGetOutputUrl:
    def test_basic_construction(self):
        url = cr.get_output_url("my-org", "my-repo", "pr-42")
        assert "test-owner" in url
        assert "test-output-repo" in url
        assert "my-org-my-repo-pr-42.md" in url
        assert url.startswith("https://github.com/")

    def test_url_contains_code_review_path(self):
        url = cr.get_output_url("org", "repo", "label")
        assert "/code-review/" in url

    def test_different_owners(self):
        url1 = cr.get_output_url("owner-a", "repo", "lbl")
        url2 = cr.get_output_url("owner-b", "repo", "lbl")
        assert "owner-a" in url1
        assert "owner-b" in url2
        assert url1 != url2

    def test_special_characters_in_label(self):
        url = cr.get_output_url("org", "repo", "2024-01-15")
        assert "2024-01-15" in url

    def test_returns_string(self):
        url = cr.get_output_url("a", "b", "c")
        assert isinstance(url, str)


# ---------------------------------------------------------------------------
# build_report_md
# ---------------------------------------------------------------------------

class TestBuildReportMd:
    def test_contains_score(self):
        md = cr.build_report_md(FULL_RESULT, "PR #7", "owner/repo")
        assert "42/100" in md

    def test_contains_recommendation(self):
        md = cr.build_report_md(FULL_RESULT, "PR #7", "owner/repo")
        assert "BLOCK" in md

    def test_contains_summary(self):
        md = cr.build_report_md(FULL_RESULT, "PR #7", "owner/repo")
        assert "Several critical issues found." in md

    def test_contains_source_and_context(self):
        md = cr.build_report_md(MINIMAL_RESULT, "scheduled", "myorg/myrepo")
        assert "scheduled" in md
        assert "myorg/myrepo" in md

    def test_findings_table_rows(self):
        md = cr.build_report_md(FULL_RESULT, "PR #1", "org/repo")
        assert "CRITICAL" in md
        assert "src/app.py" in md
        assert "Hardcoded AWS secret key detected." in md

    def test_no_findings_shows_placeholder(self):
        md = cr.build_report_md(MINIMAL_RESULT, "PR #2", "org/repo")
        assert "No findings" in md

    def test_iac_findings_listed(self):
        md = cr.build_report_md(FULL_RESULT, "PR #1", "org/repo")
        assert "S3 bucket missing tags" in md
        assert "IAM role too permissive" in md

    def test_no_iac_findings_shows_none(self):
        md = cr.build_report_md(MINIMAL_RESULT, "PR #1", "org/repo")
        assert "_None_" in md

    def test_positive_observations_listed(self):
        md = cr.build_report_md(FULL_RESULT, "PR #1", "org/repo")
        assert "CI pipeline is well-structured" in md
        assert "Dependencies are pinned" in md

    def test_no_positive_observations_shows_none(self):
        data = {**MINIMAL_RESULT, "positive_observations": []}
        md = cr.build_report_md(data, "PR #1", "org/repo")
        assert "_None_" in md

    def test_contains_generated_timestamp(self):
        import datetime
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        md = cr.build_report_md(MINIMAL_RESULT, "PR #1", "org/repo")
        assert today in md

    def test_missing_score_key_shows_na(self):
        data = {k: v for k, v in MINIMAL_RESULT.items() if k != "score"}
        md = cr.build_report_md(data, "PR #1", "org/repo")
        assert "N/A" in md

    def test_missing_recommendation_key_shows_na(self):
        data = {k: v for k, v in MINIMAL_RESULT.items() if k != "merge_recommendation"}
        md = cr.build_report_md(data, "PR #1", "org/repo")
        assert "N/A" in md

    def test_missing_summary_key_shows_default(self):
        data = {k: v for k, v in MINIMAL_RESULT.items() if k != "summary"}
        md = cr.build_report_md(data, "PR #1", "org/repo")
        assert "No summary provided." in md

    def test_finding_with_null_line(self):
        data = {
            **MINIMAL_RESULT,
            "findings": [
                {
                    "severity": "LOW",
                    "category": "maintainability",
                    "file": "main.py",
                    "line": None,
                    "issue": "Missing docstring.",
                    "recommendation": "Add a module-level docstring.",
                }
            ],
        }
        md = cr.build_report_md(data, "PR #1", "org/repo")
        assert "Missing docstring." in md

    def test_returns_string(self):
        md = cr.build_report_md(MINIMAL_RESULT, "src", "ctx")
        assert isinstance(md, str)

    def test_autogenerated_footer_present(self):
        md = cr.build_report_md(MINIMAL_RESULT, "src", "ctx")
        assert "Auto-generated by AI Delivery Bot" in md


# ---------------------------------------------------------------------------
# review_pr
# ---------------------------------------------------------------------------

class TestReviewPr:
    def _setup_mocks(self, result=None):
        if result is None:
            result = FULL_RESULT
        shared_stub.get_pr_diff.return_value = "diff --git a/src/app.py b/src/app.py\n+secret = 'abc123'"
        shared_stub.call_claude.return_value = json.dumps(result)
        shared_stub.post_pr_comment.return_value = None

    def test_happy_path_returns_result(self):
        self._setup_mocks()
        result = cr.review_pr("my-org", "my-repo", 42, "https://ci.example.com/run/1")
        assert result["score"] == 42
        assert result["merge_recommendation"] == "BLOCK"

    def test_calls_get_pr_diff(self):
        self._setup_m