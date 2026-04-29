"""
Tests for tool3_business_docs.py
=================================
What is tested:
  - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty file set
  - build_full_output(): correct markdown assembly, standalone gap questionnaire
  - __main__ block logic: env-var wiring, success path, failure/exception path

Mocks used:
  - shared.call_claude          → patched to return controlled strings
  - shared.get_repo_files       → patched to return a small dict of fake files
  - shared.write_output_file    → patched to return a fake URL
  - shared.send_email           → patched (no-op)
  - shared.email_html           → patched to return a dummy string
  - shared.write_audit_entry    → patched (no-op)
  - datetime.datetime.utcnow    → patched for deterministic timestamps

TODOs:
  - TODO: Integration test against a real GitHub repo requires network access
  - TODO: Test the truncation behaviour of files_str (c[:3000]) with very large files
  - TODO: Test write_output_file failure propagation through __main__ once error handling
          is fully defined (source is truncated)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers to build a minimal fake "shared" module so the import succeeds
# without the real shared.py being present in the test environment
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO       = "test-output-repo"


def _make_shared_stub():
    """Create a minimal stub for the `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude        = MagicMock(return_value="doc body\n---GAPS---\n1. Gap question?")
    mod.get_repo_files     = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file  = MagicMock(return_value="https://github.com/output/file.md")
    mod.send_email         = MagicMock()
    mod.email_html         = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry  = MagicMock()
    mod.OUTPUT_REPO_OWNER  = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO        = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def inject_shared_stub(monkeypatch):
    """Ensure a fresh shared stub is injected before every test."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)
    # Remove cached tool3 module so each test gets a clean import
    sys.modules.pop("tool3_business_docs", None)
    yield stub


@pytest.fixture()
def tool3(inject_shared_stub):
    """Import tool3_business_docs fresh after stub injection."""
    return importlib.import_module("tool3_business_docs")


# ---------------------------------------------------------------------------
# Fixed datetime helper
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)


@pytest.fixture()
def frozen_utcnow():
    """Patch datetime.datetime.utcnow to return FIXED_DT inside tool3."""
    with patch("tool3_business_docs.datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
        yield mock_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:
    """Tests for the generate_biz_doc function."""

    def test_happy_path_splits_on_delimiter(self, tool3, inject_shared_stub):
        """Claude response containing ---GAPS--- is split correctly."""
        inject_shared_stub.call_claude.return_value = (
            "## Solution Overview\nSome text.\n---GAPS---\n1. What is the go-live date?"
        )
        inject_shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# Project",
        }

        doc, gaps = tool3.generate_biz_doc(
            "acme", "underwriting-tool", "Underwriting Risk Classification", "1.0.0", "https://run"
        )

        assert "Solution Overview" in doc
        assert "go-live date" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_calls_claude_with_correct_args(self, tool3, inject_shared_stub):
        """call_claude receives a prompt that includes project_name and version."""
        tool3.generate_biz_doc("owner", "repo", "MyProject", "2.3.1", "https://run")

        assert inject_shared_stub.call_claude.called
        prompt_arg = inject_shared_stub.call_claude.call_args[0][0]
        assert "MyProject" in prompt_arg
        assert "2.3.1" in prompt_arg

    def test_no_delimiter_in_claude_response(self, tool3, inject_shared_stub):
        """When ---GAPS--- is absent the full response goes into doc and gaps is fallback."""
        inject_shared_stub.call_claude.return_value = "Only a document, no delimiter here."

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "0.1", "u")

        assert doc == "Only a document, no delimiter here."
        assert "could not extract" in gaps.lower() or "manually" in gaps.lower()

    def test_empty_files_dict(self, tool3, inject_shared_stub):
        """get_repo_files returning an empty dict should not raise."""
        inject_shared_stub.get_repo_files.return_value = {}
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\n1. Question?"

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "0.1", "u")

        assert doc == "doc"
        assert "Question?" in gaps

    def test_multiple_delimiter_occurrences_splits_on_first(self, tool3, inject_shared_stub):
        """Only the first ---GAPS--- is used as the split point."""
        inject_shared_stub.call_claude.return_value = (
            "doc part\n---GAPS---\ngaps part\n---GAPS---\nextra"
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "0.1", "u")

        assert doc == "doc part"
        assert "---GAPS---" in gaps   # second delimiter stays inside gaps
        assert "extra" in gaps

    def test_file_content_truncated_at_3000_chars(self, tool3, inject_shared_stub):
        """Files with more than 3000 chars are truncated in the prompt sent to Claude."""
        long_content = "x" * 5000
        inject_shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\n1. Q?"

        tool3.generate_biz_doc("o", "r", "P", "0.1", "u")

        user_msg_arg = inject_shared_stub.call_claude.call_args[0][1]
        # The truncated content (3000 x's) should appear but NOT 5000 x's
        assert "x" * 3000 in user_msg_arg
        assert "x" * 3001 not in user_msg_arg

    def test_get_repo_files_called_with_correct_extensions(self, tool3, inject_shared_stub):
        """get_repo_files is called with the expected extension list."""
        tool3.generate_biz_doc("o", "r", "P", "0.1", "u")

        call_args = inject_shared_stub.get_repo_files.call_args
        exts = call_args[0][2]  # positional arg index 2
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in exts

    def test_returns_stripped_strings(self, tool3, inject_shared_stub):
        """Leading/trailing whitespace is stripped from both outputs."""
        inject_shared_stub.call_claude.return_value = (
            "   ## Doc\n   \n---GAPS---\n   1. Question?   "
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "0.1", "u")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_date_injected_into_prompt(self, tool3, inject_shared_stub, frozen_utcnow):
        """The current UTC date appears in the prompt passed to Claude."""
        tool3.generate_biz_doc("o", "r", "P", "0.1", "u")

        prompt_arg = inject_shared_stub.call_claude.call_args[0][0]
        assert "2024-06-15" in prompt_arg


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:
    """Tests for the build_full_output function."""

    def _call(self, tool3, doc="## Doc", gaps="1. Gap?",
              owner="acme", repo="underwriting", project_name="MyProj", version="1.0.0"):
        return tool3.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_two_strings(self, tool3):
        full_md, gap_only_md = self._call(tool3)
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, tool3):
        full_md, _ = self._call(tool3, doc="## Solution Overview\nContent here.")
        assert "## Solution Overview" in full_md
        assert "Content here." in full_md

    def test_full_md_contains_gaps(self, tool3):
        full_md, _ = self._call(tool3, gaps="1. What is the go-live date?")
        assert "1. What is the go-live date?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool3):
        full_md, _ = self._call(tool3)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool3):
        full_md, _ = self._call(tool3, owner="acme", repo="risk", version="2.1.0")
        assert "acme/risk" in full_md
        assert "v2.1.0" in full_md

    def test_gap_only_md_contains_project_name(self, tool3):
        _, gap_only_md = self._call(tool3, project_name="Underwriting Risk Classification")
        assert "Underwriting Risk Classification" in gap_only_md

    def test_gap_only_md_contains_gaps(self, tool3):
        _, gap_only_md = self._call(tool3, gaps="1. Gap question one?\n2. Gap question two?")
        assert "Gap question one?" in gap_only_md
        assert "Gap question two?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, tool3):
        _, gap_only_md = self._call(tool3)
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_contains_version(self, tool3):
        _, gap_only_md = self._call(tool3, version="3.4.5")
        assert "3.4.5" in gap_only_md

    def test_timestamp_appears_in_both_outputs(self, tool3, frozen_utcnow):
        full_md, gap_only_md = self._call(tool3)
        # frozen_utcnow → 2024-06-15 12:00 UTC
        assert "2024-06-15" in full_md
        assert "2024-06-15" in gap_only_md

    def test_empty_doc_and_gaps(self, tool3):
        """Empty strings should not raise, just produce minimal output."""
        full_md, gap_only_md = self._call(tool3, doc="", gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_does_not_contain_standalone_gap_header_preamble(self, tool3):
        """The full doc should NOT contain the standalone questionnaire preamble."""
        full_md, _ = self._call(tool3)
        assert "Estimated time: 10-15 minutes" not in full_md

    def test_gap_only_md_does_not_contain_solution_overview_content(self, tool3):
        """The gap-only file should not re-include the solution overview body."""
        _, gap_only_md = self._call(tool3, doc="## Detailed technical solution overview body")
        assert "Detailed technical solution overview body" not in gap_only_md

    @pytest.mark.parametrize("version", ["0.1.0", "1.0.0-rc1", "2024.06.15"])
    def test_various_version_strings(self, tool3, version):
        full_md, gap_only_md = self._call(tool3, version=version)
        assert version in full_md
        assert version in gap_only_md


# ===========================================================================
# Tests for the __main__ block (via importlib / subprocess simulation)
# ===========================================================================

class TestMainBlock:
    """Tests for the __main__ entry-point logic."""

    BASE_ENV = {
        "SOURCE_REPO_OWNER": "acme",
        "SOURCE_REPO_NAME":  "underwriting-tool",
        "PROJECT_NAME":      "Underwriting Risk Classification",
        "RELEASE_VERSION":   "1.2.3",
        "GITHUB_RUN_URL":    "https://github.com/runs/999",
    }

    def _run_main(self, tool3, env_overrides=None):
        """Execute the __main__ body by calling its functions directly with patched env."""
        env = {**self.BASE_ENV, **(env_overrides or {})}
        with patch.dict(os.environ, env, clear=False):
            # Re-execute the main block by calling the public functions
            # (The module is not run as __main__ directly; we test the logic path)
            doc, gaps = tool3.generate_biz_doc(
                env["SOURCE_REPO_OWNER"],
                env["SOURCE_REPO_NAME"],
                env.get("PROJECT_NAME", env["SOURCE_REPO_NAME"]),
                env.get("RELEASE_VERSION", "0.1.0"),
                env.get("GITHUB_RUN_URL", "https://github.com"),
            )
            return doc, gaps

    def test_generate_biz_doc_called_with_env_vars(self, tool3, inject_shared_stub):
        doc, gaps = self._run_main(tool3)
        assert inject_shared_stub.call_claude.called

    def test_write_output_file_called_twice_on_success(self, tool3, inject_shared_stub):
        """Two files should be written: solution-overview and gap-questionnaire."""
        with patch.dict(os.environ, self.BASE_ENV):
            doc, gaps = tool