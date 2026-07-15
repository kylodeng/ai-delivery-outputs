"""
Test module for tool3_business_docs.py

What is tested:
- generate_biz_doc: happy path (with/without ---GAPS--- delimiter), error propagation
- build_full_output: happy path, content structure, edge cases (empty doc/gaps, special chars)
- __main__ block behaviour via subprocess / importlib (environment-driven entry point)
- Boundary values: empty files dict, very long content, missing delimiter variants

Mocks used:
- shared.call_claude          → unittest.mock.patch
- shared.get_repo_files       → unittest.mock.patch
- shared.write_output_file    → unittest.mock.patch
- shared.send_email           → unittest.mock.patch
- shared.email_html           → unittest.mock.patch
- shared.write_audit_entry    → unittest.mock.patch
- datetime.datetime.utcnow    → unittest.mock.patch (fixed timestamp)

TODOs:
- TODO: Test actual Claude response parsing with real API key (integration test, skipped here)
- TODO: Test write_output_file path construction against live GitHub repo (integration, skipped)
- TODO: Test send_email with real SMTP server (integration, skipped)
- TODO: Validate markdown output renders correctly in a headless browser (E2E, skipped)
"""

import datetime
import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers: build a minimal fake `shared` module so the import in
# tool3_business_docs.py does not require the real shared.py on sys.path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a fake `shared` module with all symbols tool3 imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fake `shared` module before every test and reload tool3 so it
    picks up the mock.  Restores sys.modules afterwards automatically.
    """
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)

    # Ensure the script directory is on sys.path (mirrors the real script)
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        monkeypatch.syspath_prepend(scripts_dir)

    # Remove cached module so each test gets a fresh import
    monkeypatch.delitem(sys.modules, "tool3_business_docs", raising=False)

    return fake


@pytest.fixture()
def tool3(fake_shared):
    """Import (or re-import) tool3_business_docs with the fake shared in place."""
    # The file lives at .github/scripts/tool3_business_docs.py relative to repo root.
    # We load it via importlib so we can control the environment cleanly.
    spec = importlib.util.spec_from_file_location(
        "tool3_business_docs",
        os.path.join(
            os.path.dirname(__file__),
            ".github",
            "scripts",
            "tool3_business_docs.py",
        ),
    )
    if spec is None:
        pytest.skip("tool3_business_docs.py not found at expected path")
    mod = importlib.util.module_from_spec(spec)
    # Patch __name__ so the `if __name__ == "__main__"` block does NOT run.
    sys.modules["tool3_business_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic assertions
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DT_STR = "2024-06-15 12:00 UTC"


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================


class TestGenerateBizDoc:
    """Tests for the generate_biz_doc() function."""

    def test_happy_path_with_delimiter(self, tool3, fake_shared):
        """Claude returns both parts separated by ---GAPS---."""
        fake_shared.call_claude.return_value = (
            "# Solution Overview\nSome content\n---GAPS---\n1. First question?\n2. Second question?"
        )
        fake_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# Project",
        }

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            doc, gaps = tool3.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run")

        assert "Solution Overview" in doc
        assert "1. First question?" in gaps
        assert "2. Second question?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_delimiter(self, tool3, fake_shared):
        """Claude returns text without the ---GAPS--- delimiter; fallback message used."""
        fake_shared.call_claude.return_value = "Only document, no delimiter present."
        fake_shared.get_repo_files.return_value = {"app.py": "x = 1"}

        doc, gaps = tool3.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run")

        assert doc == "Only document, no delimiter present."
        assert "Claude could not extract" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, fake_shared):
        """Verifies the correct file extensions are passed to get_repo_files."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "proj", "0.2.0", "url")

        fake_shared.get_repo_files.assert_called_once()
        args, kwargs = fake_shared.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_prompt_with_project_name(self, tool3, fake_shared):
        """Project name and version are interpolated into the prompt."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "InsurancePortal", "3.2.1", "url")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg
        assert "3.2.1" in prompt_arg

    def test_call_claude_receives_file_content_in_user_message(self, tool3, fake_shared):
        """File contents from the repo are passed in the user message to Claude."""
        fake_shared.get_repo_files.return_value = {
            "infra/main.tf": "resource aws_lambda {}",
            "src/handler.py": "def handler(): pass",
        }
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "infra/main.tf" in user_msg
        assert "src/handler.py" in user_msg
        assert "resource aws_lambda {}" in user_msg

    def test_empty_repo_files(self, tool3, fake_shared):
        """generate_biz_doc handles an empty files dict gracefully."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "minimal doc\n---GAPS---\n1. What does this do?"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

        assert doc == "minimal doc"
        assert "1. What does this do?" in gaps

    def test_multiple_gaps_delimiters_only_first_used(self, tool3, fake_shared):
        """Only the first ---GAPS--- occurrence is used as a split point."""
        fake_shared.call_claude.return_value = (
            "doc part\n---GAPS---\ngaps part\n---GAPS---\nextra stuff"
        )

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

        assert doc == "doc part"
        assert gaps == "gaps part\n---GAPS---\nextra stuff"

    def test_whitespace_stripped_from_parts(self, tool3, fake_shared):
        """Leading/trailing whitespace is stripped from doc and gaps."""
        fake_shared.call_claude.return_value = "  doc content  \n---GAPS---\n  gaps content  "

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

        assert doc == "doc content"
        assert gaps == "gaps content"

    def test_call_claude_exception_propagates(self, tool3, fake_shared):
        """Exceptions from call_claude bubble up to the caller."""
        fake_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

    def test_get_repo_files_exception_propagates(self, tool3, fake_shared):
        """Exceptions from get_repo_files bubble up to the caller."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

    def test_large_file_content_truncated_in_prompt(self, tool3, fake_shared):
        """Files with content > 3000 chars are sliced before being sent to Claude."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

        user_msg = fake_shared.call_claude.call_args[0][1]
        # The slice [:3000] means we expect at most 3000 x's in the message
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_date_injected_into_prompt(self, tool3, fake_shared):
        """The current UTC date is injected into the prompt."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = "2024-06-15"
            tool3.generate_biz_doc("owner", "repo", "proj", "1.0.0", "url")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "2024-06-15" in prompt_arg


# ===========================================================================
# Tests for build_full_output
# ===========================================================================


class TestBuildFullOutput:
    """Tests for the build_full_output() function."""

    @pytest.fixture()
    def base_args(self):
        return dict(
            doc="# Solution Overview\nContent here.",
            gaps="1. What is the go-live date?\n2. Who are the key stakeholders?",
            owner="acme-corp",
            repo="insurance-portal",
            project_name="Insurance Portal",
            version="2.0.0",
        )

    def test_returns_two_strings(self, tool3, base_args):
        result = tool3.build_full_output(**base_args)
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, tool3, base_args):
        full_md, _ = tool3.build_full_output(**base_args)
        assert "# Solution Overview" in full_md
        assert "Content here." in full_md

    def test_full_md_contains_gaps(self, tool3, base_args):
        full_md, _ = tool3.build_full_output(**base_args)
        assert "1. What is the go-live date?" in full_md
        assert "2. Who are the key stakeholders?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool3, base_args):
        full_md, _ = tool3.build_full_output(**base_args)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_footer_with_owner_repo_version(self, tool3, base_args):
        full_md, _ = tool3.build_full_output(**base_args)
        assert "acme-corp/insurance-portal" in full_md
        assert "v2.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3, base_args):
        _, gap_only_md = tool3.build_full_output(**base_args)
        assert "Insurance Portal" in gap_only_md
        assert "v2.0.0" in gap_only_md

    def test_gap_only_md_contains_gaps_text(self, tool3, base_args):
        _, gap_only_md = tool3.build_full_output(**base_args)
        assert "1. What is the go-live date?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, tool3, base_args):
        _, gap_only_md = tool3.build_full_output(**base_args)
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_does_not_contain