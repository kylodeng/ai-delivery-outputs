"""
Tests for tool3_business_docs.py
=================================
What is tested:
  - generate_biz_doc(): happy path, delimiter splitting, missing delimiter fallback
  - build_full_output(): full markdown assembly, standalone gap questionnaire
  - __main__ block logic (via subprocess or direct call simulation): env var handling,
    success path, failure/exception path

Mocks used:
  - shared.call_claude          — patched to return synthetic Claude responses
  - shared.get_repo_files       — patched to return synthetic file dicts
  - shared.write_output_file    — patched to return a synthetic URL
  - shared.send_email           — patched as no-op
  - shared.email_html           — patched to return a dummy HTML string
  - shared.write_audit_entry    — patched as no-op
  - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
  - TODO: Integration test against a real GitHub repo (requires GITHUB_TOKEN)
  - TODO: Test __main__ subprocess execution end-to-end with real env vars
  - TODO: Verify email HTML content structure once email_html signature is confirmed
"""

import datetime
import importlib
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers to build a synthetic "shared" stub module before importing the SUT
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a MagicMock that looks like the shared module."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="DOC PART\n---GAPS---\n1. What is the go-live date?")
    stub.get_repo_files = MagicMock(return_value={"README.md": "# Hello World"})
    stub.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    stub.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_shared(monkeypatch):
    """Inject a fresh shared stub before every test."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)
    # If the SUT was already imported we need it to see the new stub
    # We reload each time via the sut fixture below.
    return stub


@pytest.fixture()
def shared_stub(patch_shared):
    return patch_shared


@pytest.fixture()
def sut(patch_shared):
    """Import (or reload) the module under test with the stub in place."""
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Remove cached version so monkeypatched shared is picked up
    for key in list(sys.modules.keys()):
        if "tool3_business_docs" in key:
            del sys.modules[key]

    import importlib.util, pathlib

    sut_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool3_business_docs.py"
    if not sut_path.exists():
        pytest.skip(f"Source file not found at {sut_path}")

    spec = importlib.util.spec_from_file_location("tool3_business_docs", sut_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fixed_now():
    """A fixed UTC datetime for deterministic output."""
    return datetime.datetime(2024, 6, 15, 12, 0, 0)


# ---------------------------------------------------------------------------
# generate_biz_doc tests
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:
    """Tests for generate_biz_doc()."""

    def test_happy_path_returns_doc_and_gaps(self, sut, shared_stub):
        """Claude returns valid response with ---GAPS--- delimiter."""
        shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        shared_stub.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who are the key stakeholders?"
        )

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run-url")

        assert "# Solution overview: MyProject" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who are the key stakeholders?" in gaps
        # The delimiter itself should not appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_returns_full_raw_as_doc(self, sut, shared_stub):
        """When Claude omits ---GAPS--- the entire response goes to doc_part."""
        shared_stub.call_claude.return_value = "Some doc without any delimiter at all."

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run-url")

        assert doc == "Some doc without any delimiter at all."
        assert "Claude could not extract" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, sut, shared_stub):
        shared_stub.call_claude.return_value = "doc---GAPS---gaps"

        sut.generate_biz_doc("owner1", "repo1", "Proj", "0.2.0", "https://run")

        shared_stub.get_repo_files.assert_called_once()
        args, kwargs = shared_stub.get_repo_files.call_args
        # First two positional args are owner and repo
        assert args[0] == "owner1"
        assert args[1] == "repo1"
        # Extensions list must contain key file types
        extensions = args[2]
        assert ".py" in extensions
        assert ".md" in extensions
        assert ".tf" in extensions

    def test_call_claude_receives_project_version_date(self, sut, shared_stub, fixed_now):
        """Prompt forwarded to Claude must contain formatted project_name and version."""
        shared_stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fixed_now
            mock_dt.utcnow.return_value.strftime = fixed_now.strftime
            sut.generate_biz_doc("owner", "repo", "InsuranceApp", "2.3.4", "https://run")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "InsuranceApp" in prompt_arg
        assert "2.3.4" in prompt_arg

    def test_files_truncated_to_3000_chars_each(self, sut, shared_stub):
        """Files longer than 3000 chars are truncated in the prompt sent to Claude."""
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        shared_stub.call_claude.return_value = "doc---GAPS---gaps"

        sut.generate_biz_doc("owner", "repo", "Proj", "1.0", "https://run")

        user_message = shared_stub.call_claude.call_args[0][1]
        # The file content slice [:3000] means no more than 3000 x's should appear
        assert "x" * 3001 not in user_message
        assert "x" * 3000 in user_message

    def test_multiple_gaps_delimiter_splits_on_first(self, sut, shared_stub):
        """Only the first ---GAPS--- is used as split point."""
        shared_stub.call_claude.return_value = (
            "Doc content\n---GAPS---\nFirst gap section\n---GAPS---\nSecond gap section"
        )

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "v", "u")

        assert "---GAPS---" not in doc
        # gaps_part should contain everything after the first delimiter
        assert "First gap section" in gaps
        assert "Second gap section" in gaps

    def test_empty_repo_files(self, sut, shared_stub):
        """generate_biz_doc works even when no files are returned."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "minimal doc---GAPS---no questions"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == "minimal doc"
        assert gaps == "no questions"

    def test_claude_returns_only_delimiter(self, sut, shared_stub):
        """Edge case: Claude returns exactly the delimiter and nothing else."""
        shared_stub.call_claude.return_value = "---GAPS---"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == ""
        assert gaps == ""

    def test_whitespace_stripped_from_parts(self, sut, shared_stub):
        """Leading/trailing whitespace is stripped from both parts."""
        shared_stub.call_claude.return_value = "  \n  doc content  \n  ---GAPS---  \n  gap content  \n  "

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == "doc content"
        assert gaps == "gap content"


# ---------------------------------------------------------------------------
# build_full_output tests
# ---------------------------------------------------------------------------

class TestBuildFullOutput:
    """Tests for build_full_output()."""

    def test_returns_two_strings(self, sut):
        full_md, gap_only_md = sut.build_full_output(
            "# Doc", "1. Question?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_part(self, sut):
        full_md, _ = sut.build_full_output(
            "# Solution Overview", "1. Who owns this?", "owner", "repo", "Proj", "1.0"
        )
        assert "# Solution Overview" in full_md

    def test_full_md_contains_gaps_section_header(self, sut):
        full_md, _ = sut.build_full_output(
            "doc", "1. A question?", "owner", "repo", "Proj", "1.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self, sut):
        full_md, _ = sut.build_full_output(
            "doc", "1. A question?", "owner", "repo", "Proj", "1.0"
        )
        assert "1. A question?" in full_md

    def test_full_md_contains_source_attribution(self, sut):
        full_md, _ = sut.build_full_output(
            "doc", "gaps", "myowner", "myrepo", "Proj", "2.0"
        )
        assert "myowner/myrepo" in full_md
        assert "v2.0" in full_md

    def test_gap_only_contains_project_name_and_version(self, sut):
        _, gap_only = sut.build_full_output(
            "doc", "1. Question", "owner", "repo", "InsuranceApp", "3.1.4"
        )
        assert "InsuranceApp" in gap_only
        assert "3.1.4" in gap_only

    def test_gap_only_contains_output_repo_link(self, sut):
        _, gap_only = sut.build_full_output(
            "doc", "1. Question", "owner", "repo", "Proj", "1.0"
        )
        assert FAKE_OUTPUT_REPO_OWNER in gap_only
        assert FAKE_OUTPUT_REPO in gap_only

    def test_gap_only_contains_gap_content(self, sut):
        _, gap_only = sut.build_full_output(
            "doc", "1. What is the go-live date?", "owner", "repo", "Proj", "1.0"
        )
        assert "1. What is the go-live date?" in gap_only

    def test_gap_only_does_not_contain_doc_part(self, sut):
        _, gap_only = sut.build_full_output(
            "## Executive Summary\nSecret content", "1. Q?", "owner", "repo", "Proj", "1.0"
        )
        assert "## Executive Summary" not in gap_only
        assert "Secret content" not in gap_only

    def test_timestamp_present_in_both_outputs(self, sut, fixed_now):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fixed_now
            mock_dt.utcnow.return_value.strftime = fixed_now.strftime
            full_md, gap_only = sut.build_full_output(
                "doc", "gaps", "owner", "repo", "Proj", "1.0"
            )
        assert "2024-06-15" in full_md
        assert "2024-06-15" in gap_only

    def test_empty_doc_part(self, sut):
        full_md, _ = sut.build_full_output(
            "", "1. Q?", "owner", "repo", "Proj", "1.0"
        )
        # Should not raise; gap questionnaire header should still be present
        assert "## Gap Questionnaire" in full_md

    def test_empty_gaps_part(self, sut):
        full_md, gap_only = sut.build_full_output(
            "# Doc", "", "owner", "repo", "Proj", "1.0"
        )
        assert "## Gap Questionnaire" in full_md
        assert "Gap Questionnaire" in gap_only

    def test_special_characters_in_project_name(self, sut):
        """Project names with special chars should not break string formatting."""
        full_md, gap_only = sut.build_full_output(
            "doc", "1. Q?", "owner", "repo", "Project <Beta> & Test", "1.0"
        )
        assert "Project <Beta> & Test" in gap_only

    @pytest.mark.parametrize("version", ["0.0.1", "1.0.0", "10.20.30", "v2024.06.15"])
    def test_various_version_strings(self, sut, version):
        full_md, gap_only = sut.build_full_output(
            "doc", "1. Q?", "owner", "repo", "Proj", version
        )
        assert version in full_md
        assert version in gap_only


#