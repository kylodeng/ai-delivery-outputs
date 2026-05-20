"""
Tests for tool3_business_docs.py
=================================
What is tested:
  - generate_biz_doc(): happy path, split on ---GAPS---, missing delimiter fallback
  - build_full_output(): structure of full_md and gap_only_md strings, metadata injection
  - __main__ block logic (via direct function calls and env-var mocking)
  - Edge cases: empty gaps, multi-occurrence of delimiter, whitespace-only parts

Mocks used:
  - shared.call_claude          → unittest.mock.patch
  - shared.get_repo_files       → unittest.mock.patch
  - shared.write_output_file    → unittest.mock.patch
  - shared.send_email           → unittest.mock.patch
  - shared.email_html           → unittest.mock.patch
  - shared.write_audit_entry    → unittest.mock.patch
  - datetime.datetime.utcnow    → unittest.mock.patch (for deterministic timestamps)

TODOs:
  - TODO: Integration test against a real GitHub repo (requires credentials)
  - TODO: Test __main__ error-path send_email call once the truncated source is complete
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake `shared` module so we never import the real
# one (which would require GitHub tokens, etc.)
# ---------------------------------------------------------------------------

def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?")
    shared.get_repo_files     = MagicMock(return_value={"README.md": "# hello"})
    shared.write_output_file  = MagicMock(return_value="https://github.com/output/file")
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-owner"
    shared.OUTPUT_REPO        = "test-output-repo"
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fresh fake `shared` module before every test."""
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)
    # If tool3 was previously imported, force a reload
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]
    yield mod


@pytest.fixture()
def tool3(fake_shared):
    """Import (or re-import) tool3_business_docs with the fake shared in place."""
    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try importing directly by path if the module is in .github/scripts
    import importlib.util

    script_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
    )
    if os.path.exists(script_path):
        spec = importlib.util.spec_from_file_location("tool3_business_docs", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["tool3_business_docs"] = module
        spec.loader.exec_module(module)
        return module

    # Fallback: normal import (works if pytest is run from repo root with path set)
    import tool3_business_docs
    return tool3_business_docs


# Fixed UTC time used throughout tests
FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR     = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


# ===========================================================================
# generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_splits_on_delimiter(self, tool3, fake_shared):
        """Claude returns a response with ---GAPS--- → both parts returned."""
        fake_shared.call_claude.return_value = (
            "# Solution overview\nSome doc text.\n---GAPS---\n1. What is the go-live date?"
        )
        fake_shared.get_repo_files.return_value = {"app.py": "print('hello')"}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            doc, gaps = tool3.generate_biz_doc(
                "acme", "my-repo", "My Project", "1.2.3", "https://ci.example.com/run/1"
            )

        assert "Solution overview" in doc
        assert "1. What is the go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_uses_fallback(self, tool3, fake_shared):
        """When Claude omits ---GAPS---, doc is full response and gaps is fallback message."""
        fake_shared.call_claude.return_value = "Just a document, no delimiter here."

        doc, gaps = tool3.generate_biz_doc(
            "acme", "my-repo", "My Project", "1.0.0", "https://ci.example.com"
        )

        assert doc == "Just a document, no delimiter here."
        assert "could not extract gap questions" in gaps.lower() or "manually" in gaps.lower()

    def test_delimiter_only_at_start(self, tool3, fake_shared):
        """Delimiter at the very beginning → empty doc part, gaps has content."""
        fake_shared.call_claude.return_value = "---GAPS---\n1. Where is the data stored?"

        doc, gaps = tool3.generate_biz_doc(
            "acme", "my-repo", "My Project", "1.0.0", "https://ci.example.com"
        )

        # doc part is empty string (stripped)
        assert doc == ""
        assert "Where is the data stored?" in gaps

    def test_delimiter_only_at_end(self, tool3, fake_shared):
        """Delimiter at the very end → full document, empty gaps."""
        fake_shared.call_claude.return_value = "# Full document\nAll content here.\n---GAPS---"

        doc, gaps = tool3.generate_biz_doc(
            "acme", "my-repo", "My Project", "1.0.0", "https://ci.example.com"
        )

        assert "Full document" in doc
        assert gaps == ""

    def test_multiple_delimiters_only_first_used(self, tool3, fake_shared):
        """Only the first ---GAPS--- should be used as the split point."""
        fake_shared.call_claude.return_value = (
            "Doc part\n---GAPS---\nGaps part\n---GAPS---\nExtra stuff"
        )

        doc, gaps = tool3.generate_biz_doc(
            "acme", "my-repo", "My Project", "2.0.0", "https://ci.example.com"
        )

        assert doc == "Doc part"
        # Everything after the first delimiter
        assert "Gaps part" in gaps
        assert "Extra stuff" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, fake_shared):
        """get_repo_files must be invoked with the expected extensions list."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "P", "v1", "url")

        call_args = fake_shared.get_repo_files.call_args
        owner_arg, repo_arg = call_args[0][0], call_args[0][1]
        extensions_arg      = call_args[0][2]

        assert owner_arg == "owner"
        assert repo_arg == "repo"
        for ext in [".py", ".md", ".tf"]:
            assert ext in extensions_arg

    def test_call_claude_receives_formatted_prompt(self, tool3, fake_shared):
        """The prompt passed to call_claude must contain project_name and version."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            tool3.generate_biz_doc("owner", "repo", "SpecialProject", "3.1.4", "url")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "SpecialProject" in prompt_arg
        assert "3.1.4" in prompt_arg

    def test_file_contents_truncated_and_passed_to_claude(self, tool3, fake_shared):
        """File content should appear in the user message sent to call_claude."""
        fake_shared.get_repo_files.return_value = {
            "main.py": "x = 1",
            "infra.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "P", "v1", "url")

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "main.py" in user_msg
        assert "infra.tf" in user_msg

    def test_empty_repo_files(self, tool3, fake_shared):
        """Empty file dict should not crash; Claude still gets called."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "P", "v1", "url")

        assert fake_shared.call_claude.called
        assert doc == "doc"

    def test_whitespace_stripped_from_parts(self, tool3, fake_shared):
        """Leading/trailing whitespace around both parts should be stripped."""
        fake_shared.call_claude.return_value = (
            "   \n  # Doc  \n  ---GAPS---  \n  1. Question?  \n  "
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "v", "u")

        assert doc == "# Doc"
        assert gaps == "1. Question?"

    @pytest.mark.parametrize("project_name,version", [
        ("Generations II", "2.0.0"),
        ("Global Network Hospital List", "1.5.0"),
        ("Sun Life Health", "0.1.0-beta"),
        ("", "0.0.1"),                   # empty project name edge case
    ])
    def test_various_project_names_and_versions(self, tool3, fake_shared, project_name, version):
        """Parameterised: various project names and versions don't cause crashes."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", project_name, version, "url")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ===========================================================================
# build_full_output
# ===========================================================================

class TestBuildFullOutput:

    @pytest.fixture()
    def frozen_now(self):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            yield

    def test_full_md_contains_doc_content(self, tool3):
        full_md, _ = tool3.build_full_output(
            "# Doc heading", "1. Gap question?",
            "owner", "repo", "MyProject", "1.0.0"
        )
        assert "# Doc heading" in full_md

    def test_full_md_contains_gaps_section(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "1. Gap question?",
            "owner", "repo", "MyProject", "1.0.0"
        )
        assert "Gap Questionnaire" in full_md
        assert "1. Gap question?" in full_md

    def test_full_md_contains_source_attribution(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "gaps",
            "acme-owner", "acme-repo", "AcmeProject", "2.3.4"
        )
        assert "acme-owner/acme-repo" in full_md
        assert "2.3.4" in full_md

    def test_gap_only_md_contains_project_header(self, tool3):
        _, gap_only = tool3.build_full_output(
            "doc", "1. Who owns this?",
            "owner", "repo", "SpecialProject", "1.0.0"
        )
        assert "SpecialProject" in gap_only
        assert "1.0.0" in gap_only

    def test_gap_only_md_contains_gap_questions(self, tool3):
        _, gap_only = tool3.build_full_output(
            "doc", "1. What is the SLA?\n2. Who are the users?",
            "owner", "repo", "P", "v"
        )
        assert "What is the SLA?" in gap_only
        assert "Who are the users?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, tool3, fake_shared):
        _, gap_only = tool3.build_full_output(
            "doc", "gaps",
            "owner", "repo", "P", "v"
        )
        # Should reference OUTPUT_REPO_OWNER / OUTPUT_REPO from shared
        assert fake_shared.OUTPUT_REPO_OWNER in gap_only
        assert fake_shared.OUTPUT_REPO in gap_only

    def test_full_md_contains_auto_generated_note(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "gaps",
            "owner", "repo", "P", "v"
        )
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_generated_note(self, tool3):
        _, gap_only = tool3.build_full_output(
            "doc", "gaps",
            "owner", "repo", "P", "v"
        )
        assert "Generated" in gap_only

    def test_returns_tuple_of_two_strings(self, tool3):
        result = tool3.build_full_output(
            "doc", "gaps",
            "owner", "repo", "P", "v"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_empty_doc_still_builds(self, tool3):
        full_md, gap_only = tool3.build_full_output(
            "", "1. Question?",
            "owner", "repo", "P", "v"
        