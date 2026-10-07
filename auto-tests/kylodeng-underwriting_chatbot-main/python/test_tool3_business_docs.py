"""
Test module for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, Claude returning empty string
  - build_full_output(): full markdown structure, gap-only markdown structure, content correctness
  - __main__ block logic (via monkeypatching environment variables and module-level functions)

Mocks used:
  - shared.call_claude         — avoids real Anthropic/Claude API calls
  - shared.get_repo_files      — avoids real GitHub API calls
  - shared.write_output_file   — avoids real file/Git writes
  - shared.send_email          — avoids real email dispatch
  - shared.email_html          — avoids real HTML templating side-effects
  - shared.write_audit_entry   — avoids real audit log writes
  - datetime.datetime.utcnow   — deterministic timestamps in assertions

TODOs:
  - TODO: Test the __main__ except-branch fully (send_email truncated source prevents import)
  - TODO: Integration test once shared module stubs are available end-to-end
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake `shared` module so the import at the top of
# tool3_business_docs.py succeeds without the real file being present.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_module():
    """Return a MagicMock that behaves like the `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="DOC_CONTENT\n---GAPS---\n1. A gap question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>body</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def inject_shared(monkeypatch):
    """
    Insert a fake `shared` module into sys.modules BEFORE tool3 is imported
    so that `from shared import ...` resolves to our mock.
    The fixture tears down after each test.
    """
    fake_shared = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", fake_shared)
    yield fake_shared


@pytest.fixture()
def tool3(inject_shared):
    """
    Import (or reload) tool3_business_docs with the patched shared module in
    place.  We insert the scripts directory so the relative import works in CI.
    """
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the directory that contains *this* test file as a fallback
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    # Remove cached version so we always get a fresh import with our mocks
    sys.modules.pop("tool3_business_docs", None)

    # Resolve path: look next to this test file OR in .github/scripts
    candidate_paths = [
        os.path.join(here, "tool3_business_docs.py"),
        os.path.join(here, ".github", "scripts", "tool3_business_docs.py"),
    ]
    module_path = next((p for p in candidate_paths if os.path.exists(p)), None)

    if module_path is None:
        pytest.skip("tool3_business_docs.py not found – adjust path in fixture")

    spec = importlib.util.spec_from_file_location("tool3_business_docs", module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["tool3_business_docs"] = mod
    return mod


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic assertions
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


# ===========================================================================
# generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:
    """Tests for generate_biz_doc()."""

    def test_happy_path_with_delimiter(self, tool3, inject_shared):
        """Claude returns both sections; function splits them correctly."""
        inject_shared.call_claude.return_value = (
            "# Solution overview: MyApp\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n"
            "2. Who are the key users?"
        )
        inject_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "infra/main.tf": "resource \"aws_s3_bucket\" \"b\" {}",
        }

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

        assert "Solution overview" in doc
        assert "---GAPS---" not in doc, "Delimiter should not appear in doc_part"
        assert "go-live date" in gaps
        assert "---GAPS---" not in gaps, "Delimiter should not appear in gaps_part"

    def test_missing_delimiter_returns_fallback_gaps(self, tool3, inject_shared):
        """When Claude omits ---GAPS---, gaps_part should be the fallback message."""
        inject_shared.call_claude.return_value = "Only a document, no delimiter at all."

        doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

        assert doc == "Only a document, no delimiter at all."
        assert "could not extract gap questions" in gaps.lower()

    def test_delimiter_appears_only_once(self, tool3, inject_shared):
        """split(..., 1) means only the FIRST delimiter is used."""
        inject_shared.call_claude.return_value = (
            "Doc part\n---GAPS---\nGaps part\n---GAPS---\nExtra stuff"
        )
        doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "2.0.0", "https://run.url")

        assert "Doc part" in doc
        assert "Gaps part" in gaps
        assert "Extra stuff" in gaps  # second delimiter becomes part of gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, inject_shared):
        """Verify the exact extension list and max_files guard."""
        tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

        inject_shared.get_repo_files.assert_called_once()
        args, kwargs = inject_shared.get_repo_files.call_args
        assert args[0] == "acme"
        assert args[1] == "myrepo"
        assert ".py" in args[2]
        assert ".tf" in args[2]
        assert ".md" in args[2]
        assert kwargs.get("max_files") == 20

    def test_call_claude_receives_project_name_in_prompt(self, tool3, inject_shared):
        """The system prompt must be formatted with project_name and version."""
        tool3.generate_biz_doc("acme", "myrepo", "SpecialProject", "3.1.4", "https://run.url")

        inject_shared.call_claude.assert_called_once()
        prompt_arg = inject_shared.call_claude.call_args[0][0]
        assert "SpecialProject" in prompt_arg
        assert "3.1.4" in prompt_arg

    def test_file_content_truncated_to_3000_chars(self, tool3, inject_shared):
        """Files with content > 3000 chars must be truncated in the user message."""
        long_content = "x" * 5000
        inject_shared.get_repo_files.return_value = {"big_file.py": long_content}

        tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

        user_message_arg = inject_shared.call_claude.call_args[0][1]
        # The truncated content should have at most 3000 x's
        assert "x" * 3001 not in user_message_arg
        assert "x" * 3000 in user_message_arg

    def test_empty_repo_files(self, tool3, inject_shared):
        """Empty file dict should not crash; files_str will be empty string."""
        inject_shared.get_repo_files.return_value = {}
        inject_shared.call_claude.return_value = "DOC---GAPS---GAPS"

        doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_date_injected_into_prompt(self, tool3, inject_shared):
        """The current UTC date must appear in the prompt."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

        prompt_arg = inject_shared.call_claude.call_args[0][0]
        assert FIXED_DATE_STR in prompt_arg

    def test_call_claude_exception_propagates(self, tool3, inject_shared):
        """Exceptions from call_claude must bubble up unchanged."""
        inject_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

    def test_get_repo_files_exception_propagates(self, tool3, inject_shared):
        inject_shared.get_repo_files.side_effect = ConnectionError("GitHub down")

        with pytest.raises(ConnectionError, match="GitHub down"):
            tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")


# ===========================================================================
# build_full_output()
# ===========================================================================


class TestBuildFullOutput:
    """Tests for build_full_output()."""

    DOC = "# Solution overview: TestProject\nSome executive summary."
    GAPS = "1. Who owns the solution?\n2. What is the go-live date?"

    def _call(self, tool3, doc=None, gaps=None, owner="acme", repo="testrepo",
              project="TestProject", version="1.2.3"):
        return tool3.build_full_output(
            doc or self.DOC,
            gaps or self.GAPS,
            owner, repo, project, version,
        )

    def test_returns_two_strings(self, tool3):
        full_md, gap_only_md = self._call(tool3)
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, tool3):
        full_md, _ = self._call(tool3)
        assert "Solution overview: TestProject" in full_md

    def test_full_md_contains_gaps_section_header(self, tool3):
        full_md, _ = self._call(tool3)
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gap_questions(self, tool3):
        full_md, _ = self._call(tool3)
        assert "Who owns the solution?" in full_md
        assert "go-live date" in full_md

    def test_full_md_contains_version(self, tool3):
        full_md, _ = self._call(tool3, version="9.9.9")
        assert "9.9.9" in full_md

    def test_full_md_contains_source_repo(self, tool3):
        full_md, _ = self._call(tool3, owner="bigcorp", repo="analytics")
        assert "bigcorp/analytics" in full_md

    def test_full_md_footer_mentions_output_repo(self, tool3):
        """Footer should link to the output repo."""
        full_md, _ = self._call(tool3)
        assert FAKE_OUTPUT_REPO_OWNER in full_md or FAKE_OUTPUT_REPO in full_md

    def test_gap_only_md_title_contains_project_and_version(self, tool3):
        _, gap_only_md = self._call(tool3, project="InsuranceBot", version="2.0.0")
        assert "InsuranceBot" in gap_only_md
        assert "2.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, tool3):
        _, gap_only_md = self._call(tool3)
        assert "Who owns the solution?" in gap_only_md

    def test_gap_only_md_does_not_contain_doc_body(self, tool3):
        """The standalone gap file should not duplicate the full solution doc."""
        _, gap_only_md = self._call(tool3)
        assert "Some executive summary." not in gap_only_md

    def test_gap_only_md_references_output_repo_url(self, tool3):
        _, gap_only_md = self._call(tool3)
        assert f"github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}" in gap_only_md

    def test_timestamp_appears_in_both_outputs(self, tool3):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            full_md, gap_only_md = self._call(tool3)

        assert FIXED_DATETIME_STR in full_md
        assert FIXED_DATETIME_STR in gap_only_md

    def test_empty_doc_still_produces_output(self, tool3):
        full_md, gap_only_md = self._call(tool3, doc="", gaps="1. Only question?")
        assert "Gap Questionnaire" in full_md
        assert "Only question?" in gap_only_md

    def test_empty_gaps_still_produces_output(self, tool3):