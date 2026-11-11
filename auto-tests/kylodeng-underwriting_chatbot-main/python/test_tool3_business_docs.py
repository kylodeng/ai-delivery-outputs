"""
Test suite for .github/scripts/tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path with delimiter, missing delimiter fallback
  - build_full_output(): structure, content, metadata injection
  - __main__ block: environment variable handling, success flow, failure/exception flow

Mocks used:
  - shared.call_claude          — prevents real Anthropic API calls
  - shared.get_repo_files       — prevents real GitHub API calls
  - shared.write_output_file    — prevents real file/Git writes
  - shared.send_email           — prevents real SMTP/SES calls
  - shared.email_html           — returns predictable HTML string
  - shared.write_audit_entry    — prevents real audit log writes
  - datetime.datetime.utcnow    — frozen for deterministic output

TODOs:
  - TODO: test interaction with real `shared` module once integration env is available
  - TODO: test large repo (>20 files) truncation behaviour in get_repo_files
  - TODO: test Claude response with malformed UTF-8 / binary content
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so the import in the script works
# without the actual file being on PYTHONPATH during test collection.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="Doc content\n---GAPS---\n1. Question one?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/file.md")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fake `shared` module before importing tool3_business_docs,
    and remove it afterwards so tests are isolated.
    """
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Remove cached import of the script under test so each test gets a clean slate
    monkeypatch.delitem(sys.modules, "tool3_business_docs", raising=False)
    yield fake


@pytest.fixture()
def script(fake_shared):
    """Import (or re-import) the module under test after shared is patched."""
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Frozen datetime for deterministic timestamp assertions
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Patch datetime.datetime inside the script module."""
    mock_dt = MagicMock(wraps=datetime.datetime)
    mock_dt.utcnow.return_value = FROZEN_DT
    # We need to patch it inside the module's namespace
    monkeypatch.setattr("tool3_business_docs.datetime.datetime", mock_dt)
    return mock_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, script, fake_shared, frozen_datetime):
        """Claude returns a well-formed response with ---GAPS--- delimiter."""
        fake_shared.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who owns this?"
        )
        fake_shared.get_repo_files.return_value = {
            "README.md": "# MyProject",
            "main.py": "print('hello')",
        }

        doc, gaps = script.generate_biz_doc("my-org", "my-repo", "MyProject", "1.0.0", "https://gh.com/run/1")

        assert "Solution overview: MyProject" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who owns this?" in gaps
        # doc part should NOT contain the delimiter
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_fallback(self, script, fake_shared, frozen_datetime):
        """When Claude omits ---GAPS---, gaps falls back to the warning message."""
        fake_shared.call_claude.return_value = "Only a document, no separator here."

        doc, gaps = script.generate_biz_doc("org", "repo", "Proj", "0.1.0", "https://gh.com")

        assert doc == "Only a document, no separator here."
        assert "Claude could not extract gap questions" in gaps

    def test_multiple_delimiter_occurrences_splits_on_first(self, script, fake_shared, frozen_datetime):
        """Only the first ---GAPS--- delimiter is used for splitting."""
        fake_shared.call_claude.return_value = (
            "Doc part\n"
            "---GAPS---\n"
            "Gaps part\n"
            "---GAPS---\n"
            "Extra stuff"
        )

        doc, gaps = script.generate_biz_doc("org", "repo", "Proj", "0.1.0", "https://gh.com")

        assert doc == "Doc part"
        assert "Gaps part" in gaps
        assert "Extra stuff" in gaps

    def test_prompt_contains_project_name_version_date(self, script, fake_shared, frozen_datetime):
        """SYSTEM prompt is formatted with project_name, version, and today's date."""
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\nQ1?"

        script.generate_biz_doc("org", "repo", "InsurancePortal", "2.3.1", "https://gh.com")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg
        assert "2.3.1" in prompt_arg
        assert FROZEN_DATE_STR in prompt_arg

    def test_repo_files_passed_to_claude(self, script, fake_shared, frozen_datetime):
        """File content from get_repo_files is forwarded to Claude as context."""
        fake_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "..."}}',
        }
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\nQ1?"

        script.generate_biz_doc("my-org", "my-repo", "RiskModel", "1.0.0", "https://gh.com")

        context_arg = fake_shared.call_claude.call_args[0][1]
        assert "model_card.json" in context_arg
        assert "Underwriting Risk Classification" in context_arg
        assert "assessment_criterias.json" in context_arg

    def test_get_repo_files_called_with_correct_extensions(self, script, fake_shared, frozen_datetime):
        """get_repo_files is called with the expected extension list and max_files."""
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\nQ?"

        script.generate_biz_doc("org", "repo", "P", "v1", "https://gh.com")

        args, kwargs = fake_shared.get_repo_files.call_args
        assert args[0] == "org"
        assert args[1] == "repo"
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert args[2] == expected_exts
        assert kwargs.get("max_files") == 20

    def test_empty_repo_files(self, script, fake_shared, frozen_datetime):
        """An empty repository (no files returned) does not crash."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\nQ?"

        doc, gaps = script.generate_biz_doc("org", "repo", "EmptyProject", "0.0.1", "https://gh.com")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_file_content_truncated_to_3000_chars(self, script, fake_shared, frozen_datetime):
        """File content longer than 3 000 characters is truncated in the prompt."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\nQ?"

        script.generate_biz_doc("org", "repo", "BigProject", "1.0.0", "https://gh.com")

        context_arg = fake_shared.call_claude.call_args[0][1]
        # Only 3000 x's should appear in the prompt
        assert "x" * 3000 in context_arg
        assert "x" * 3001 not in context_arg

    def test_whitespace_stripped_from_outputs(self, script, fake_shared, frozen_datetime):
        """Surrounding whitespace is stripped from both doc and gaps."""
        fake_shared.call_claude.return_value = "  \n  Doc content  \n  ---GAPS---\n  Gaps content  \n  "

        doc, gaps = script.generate_biz_doc("org", "repo", "P", "v", "u")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_call_claude_propagates_exception(self, script, fake_shared, frozen_datetime):
        """If call_claude raises, generate_biz_doc lets it propagate."""
        fake_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            script.generate_biz_doc("org", "repo", "P", "v", "u")


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def test_full_md_contains_doc_and_gaps(self, script, frozen_datetime):
        doc = "# Solution overview: TestProject\nSome details."
        gaps = "1. What is the go-live date?\n2. Who is the sponsor?"

        full_md, gap_only_md = script.build_full_output(
            doc, gaps, "my-org", "my-repo", "TestProject", "1.2.3"
        )

        assert "# Solution overview: TestProject" in full_md
        assert "Some details." in full_md
        assert "1. What is the go-live date?" in full_md
        assert "2. Who is the sponsor?" in full_md

    def test_full_md_contains_metadata_footer(self, script, frozen_datetime):
        full_md, _ = script.build_full_output(
            "Doc", "Gaps", "org", "repo", "P", "0.1.0"
        )

        assert "org/repo" in full_md
        assert "0.1.0" in full_md
        assert FROZEN_DATETIME_STR in full_md

    def test_full_md_contains_gap_questionnaire_header(self, script, frozen_datetime):
        full_md, _ = script.build_full_output(
            "Doc", "Gaps", "org", "repo", "P", "1.0.0"
        )

        assert "Gap Questionnaire" in full_md
        assert "Claude could not determine" in full_md or "Gaps" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, script, frozen_datetime):
        _, gap_only_md = script.build_full_output(
            "Doc", "1. Question?", "org", "repo", "InsurancePortal", "2.0.0"
        )

        assert "InsurancePortal" in gap_only_md
        assert "2.0.0" in gap_only_md
        assert "1. Question?" in gap_only_md

    def test_gap_only_md_contains_link_to_output_repo(self, script, frozen_datetime):
        _, gap_only_md = script.build_full_output(
            "Doc", "Q?", "org", "repo", "P", "v1"
        )

        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_contains_time_estimate_hint(self, script, frozen_datetime):
        _, gap_only_md = script.build_full_output(
            "Doc", "Q?", "org", "repo", "P", "v1"
        )

        assert "10-15 minutes" in gap_only_md

    def test_returns_tuple_of_two_strings(self, script, frozen_datetime):
        result = script.build_full_output("Doc", "Q?", "org", "repo", "P", "v1")

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_empty_doc_and_gaps(self, script, frozen_datetime):
        """build_full_output should not crash with empty strings."""
        full_md, gap_only_md = script.build_full_output("", "", "org", "repo", "P", "v1")

        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_special_characters_in_project_name(self, script, frozen_datetime):
        """Project names with special chars (spaces, slashes) are embedded safely."""
        full_md, gap_only_md = script.build_full_output(
            "Doc", "Q?", "org", "repo", "My Project / Sub-Module", "1.0.0-rc1"
        )

        assert "My Project / Sub-Module" in full_md or "My Project / Sub-Module" in gap_only_md

    def test_full_md_separator_present(self, script, frozen_datetime):
        """Horizontal rules (---) are used as separators in full_md."""
        full_md, _ = script.build_full_output("Doc", "Gaps", "org", "repo", "P", "v")

        assert "---" in full_md

    @pytest.mark.parametrize("version", ["0.1.0", "1.0.0", "2.3.1-beta", "v10.0.0"])
    def test_various_versions_embedded_correctly(self, script, frozen_datetime, version):
        full_md, gap_only_md = script.build_full_output(
            "Doc", "Q?", "org", "repo", "Project", version
        )

        assert version in full_md
        assert version in gap_only_