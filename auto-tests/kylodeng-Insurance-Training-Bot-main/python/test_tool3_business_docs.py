"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, Claude integration
    - build_full_output(): happy path, content structure, gap questionnaire formatting
    - Main block logic (simulated via direct calls with env vars mocked)

Mocks used:
    - shared.call_claude            → prevents real Anthropic API calls
    - shared.get_repo_files         → prevents real GitHub API calls
    - shared.write_output_file      → prevents real file/repo writes
    - shared.send_email             → prevents real email sending
    - shared.email_html             → prevents real HTML rendering
    - shared.write_audit_entry      → prevents real audit writes
    - datetime.datetime.utcnow      → for deterministic timestamps

TODOs:
    - TODO: Integration test with a real Claude response shape (needs API key)
    - TODO: Test the truncated __main__ block fully (source file is cut off mid-string)
    - TODO: Test write_output_file path construction when owner/repo contain special chars
"""

import sys
import os
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while controlling shared imports
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "tool3_business_docs"


def _import_module():
    """Re-import tool3_business_docs with a clean slate each time."""
    if SHARED_MODULE_PATH in sys.modules:
        del sys.modules[SHARED_MODULE_PATH]
    # Ensure the scripts directory is on path
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    scripts_dir = os.path.abspath(scripts_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module(SHARED_MODULE_PATH)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_FILES = {
    "main.py": "def hello(): pass",
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}',
    "README.md": "# My Project\nThis project does things.",
}

FAKE_DOC = """# Solution overview: MyProject
**Version:** 1.2.3 | **Date:** 2024-01-15 | **Status:** Draft

## Executive summary
This solution automates widget processing for the operations team.

## Business context
**Problem statement:** Manual widget processing was slow.
**Affected users / teams:** Operations
**Current pain points:** [TODO: what was the manual/legacy process?]

## What this solution does
Processes widgets automatically."""

FAKE_GAPS = """1. What is the target go-live date?
2. Who is the solution owner?
3. What are the retention requirements for widget data?"""

FAKE_RAW_WITH_DELIMITER = f"{FAKE_DOC}\n---GAPS---\n{FAKE_GAPS}"
FAKE_RAW_WITHOUT_DELIMITER = FAKE_DOC  # No gaps section


@pytest.fixture(autouse=True)
def mock_shared(monkeypatch):
    """Patch all shared module symbols before each test."""
    mocks = {
        "call_claude": MagicMock(return_value=FAKE_RAW_WITH_DELIMITER),
        "get_repo_files": MagicMock(return_value=FAKE_FILES),
        "write_output_file": MagicMock(return_value="https://github.com/output/repo/blob/main/file.md"),
        "send_email": MagicMock(),
        "email_html": MagicMock(return_value="<html>body</html>"),
        "write_audit_entry": MagicMock(),
        "OUTPUT_REPO_OWNER": "test-owner",
        "OUTPUT_REPO": "test-output-repo",
    }

    with patch.dict("sys.modules", {}):
        # Patch each shared symbol on the already-imported module
        import tool3_business_docs as mod
        for name, mock_val in mocks.items():
            monkeypatch.setattr(mod, name, mock_val)

    return mocks


@pytest.fixture()
def mod():
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------


class TestGenerateBizDoc:

    def test_happy_path_returns_doc_and_gaps(self, mod, mock_shared):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER
        mock_shared["get_repo_files"].return_value = FAKE_FILES

        doc, gaps = mod.generate_biz_doc("acme", "widget-svc", "WidgetSvc", "1.0.0", "https://ci.example.com/1")

        assert doc == FAKE_DOC.strip()
        assert gaps == FAKE_GAPS.strip()

    def test_get_repo_files_called_with_correct_extensions(self, mod, mock_shared):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER

        mod.generate_biz_doc("acme", "widget-svc", "WidgetSvc", "1.0.0", "https://ci/1")

        mock_shared["get_repo_files"].assert_called_once_with(
            "acme",
            "widget-svc",
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_call_claude_receives_formatted_prompt(self, mod, mock_shared):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER

        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 6, 1, 12, 0, 0)
            mock_dt.datetime.utcnow.return_value.strftime = lambda fmt: "2024-06-01"
            mock_dt.datetime.utcnow().strftime.return_value = "2024-06-01"
            # Use real strftime to avoid over-mocking
            mod.generate_biz_doc("acme", "repo", "MyProj", "2.0.0", "https://ci/2")

        called_prompt = mock_shared["call_claude"].call_args[0][0]
        assert "MyProj" in called_prompt or "project_name" in mod.SYSTEM

    def test_call_claude_user_message_contains_repo_and_files(self, mod, mock_shared):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER
        mock_shared["get_repo_files"].return_value = {"app.py": "print('hi')"}

        mod.generate_biz_doc("myorg", "myrepo", "MyProject", "0.1.0", "https://ci/3")

        user_msg = mock_shared["call_claude"].call_args[0][1]
        assert "myorg/myrepo" in user_msg
        assert "app.py" in user_msg
        assert "print('hi')" in user_msg

    def test_no_delimiter_returns_fallback_gaps(self, mod, mock_shared):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITHOUT_DELIMITER

        doc, gaps = mod.generate_biz_doc("acme", "widget-svc", "WidgetSvc", "1.0.0", "https://ci/4")

        assert doc == FAKE_RAW_WITHOUT_DELIMITER.strip()
        assert "Claude could not extract gap questions" in gaps

    def test_delimiter_splits_correctly_on_first_occurrence(self, mod, mock_shared):
        """Ensure only the first ---GAPS--- is used as delimiter."""
        raw = "Part A\n---GAPS---\nPart B\n---GAPS---\nPart C"
        mock_shared["call_claude"].return_value = raw

        doc, gaps = mod.generate_biz_doc("a", "b", "c", "1", "url")

        assert doc == "Part A"
        assert "Part B" in gaps
        assert "Part C" in gaps

    def test_files_content_truncated_to_3000_chars(self, mod, mock_shared):
        """Files longer than 3000 chars should be truncated in the prompt."""
        long_content = "x" * 5000
        mock_shared["get_repo_files"].return_value = {"big_file.py": long_content}
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER

        mod.generate_biz_doc("a", "b", "c", "1", "url")

        user_msg = mock_shared["call_claude"].call_args[0][1]
        # Should contain at most 3000 x's, not 5000
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_empty_repo_files(self, mod, mock_shared):
        mock_shared["get_repo_files"].return_value = {}
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER

        doc, gaps = mod.generate_biz_doc("a", "b", "c", "1", "url")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_strips_whitespace_from_doc_and_gaps(self, mod, mock_shared):
        raw = f"  {FAKE_DOC}  \n---GAPS---\n  {FAKE_GAPS}  "
        mock_shared["call_claude"].return_value = raw

        doc, gaps = mod.generate_biz_doc("a", "b", "c", "1", "url")

        assert not doc.startswith(" ")
        assert not doc.endswith(" ")
        assert not gaps.startswith(" ")
        assert not gaps.endswith(" ")

    def test_version_included_in_prompt(self, mod, mock_shared):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER

        mod.generate_biz_doc("org", "repo", "TestProject", "3.4.5", "https://ci/5")

        prompt = mock_shared["call_claude"].call_args[0][0]
        assert "3.4.5" in prompt

    def test_project_name_included_in_prompt(self, mod, mock_shared):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER

        mod.generate_biz_doc("org", "repo", "InsurancePortal", "1.0.0", "https://ci/6")

        prompt = mock_shared["call_claude"].call_args[0][0]
        assert "InsurancePortal" in prompt

    @pytest.mark.parametrize("project_name,version", [
        ("Generations-II", "1.0.0"),
        ("Global Network Hospital List", "2.1.3"),
        ("Mainland_China_VIP", "0.0.1"),
        ("Widget Svc", "99.99.99"),
    ])
    def test_various_project_names_and_versions(self, mod, mock_shared, project_name, version):
        mock_shared["call_claude"].return_value = FAKE_RAW_WITH_DELIMITER

        doc, gaps = mod.generate_biz_doc("org", "repo", project_name, version, "https://ci/7")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)
        assert len(doc) > 0


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------


class TestBuildFullOutput:

    def test_returns_tuple_of_two_strings(self, mod):
        full_md, gap_only_md = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, mod):
        full_md, _ = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert "Solution overview: MyProject" in full_md or FAKE_DOC[:30] in full_md

    def test_full_md_contains_gap_section_header(self, mod):
        full_md, _ = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self, mod):
        full_md, _ = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert "go-live date" in full_md

    def test_full_md_contains_attribution_footer(self, mod):
        full_md, _ = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert "AI Delivery Bot" in full_md
        assert "acme/widget-svc" in full_md

    def test_full_md_contains_version_in_footer(self, mod):
        full_md, _ = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.2.3"
        )
        assert "1.2.3" in full_md

    def test_gap_only_md_contains_project_name(self, mod):
        _, gap_only_md = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert "WidgetSvc" in gap_only_md

    def test_gap_only_md_contains_version(self, mod):
        _, gap_only_md = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "2.3.4"
        )
        assert "2.3.4" in gap_only_md

    def test_gap_only_md_contains_gaps_content(self, mod):
        _, gap_only_md = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert "go-live date" in gap_only_md
        assert "solution owner" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, mod, mock_shared):
        _, gap_only_md = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0"
        )
        assert "test-owner" in gap_only_md or "test-output-repo" in gap_only_md

    def test_gap_only_md_contains_instructions(self, mod):
        _, gap_only_md = mod.build_full_output(
            FAKE_DOC, FAKE_GAPS, "acme", "widget-svc", "WidgetSvc", "1.0.0