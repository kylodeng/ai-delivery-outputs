"""
Test module for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, delimiter present/absent, Claude response handling
- build_full_output(): full markdown construction, gap-only markdown construction,
  correct metadata embedding, edge cases (empty gaps, empty doc)
- __main__ block: environment variable handling, success path, exception/failure path

Mocks used:
- shared.call_claude (patched via tool3_business_docs module)
- shared.get_repo_files (patched via tool3_business_docs module)
- shared.write_output_file (patched via tool3_business_docs module)
- shared.send_email (patched via tool3_business_docs module)
- shared.email_html (patched via tool3_business_docs module)
- shared.write_audit_entry (patched via tool3_business_docs module)
- datetime.datetime.utcnow (frozen for deterministic output)

TODOs:
- TODO: Integration test against a real Claude response once API key is available in CI
- TODO: Test write_output_file path construction with non-ASCII owner/repo names
- TODO: Test __main__ block failure branch fully (send_email in except) — partially covered
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with a fake 'shared' dependency
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot-owner"
FAKE_OUTPUT_REPO = "ai-bot-outputs"


def _make_fake_shared():
    """Build a minimal fake 'shared' module so the import doesn't fail."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    shared.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/file")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fresh fake shared module before each test."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Force reimport of the module under test
    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    return fake


def _import_module():
    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    # Try to import from the .github/scripts directory
    script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    return importlib.import_module(mod_name)


FROZEN_NOW = datetime.datetime(2024, 6, 15, 10, 30, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 10:30 UTC"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mod(fake_shared):
    return _import_module()


@pytest.fixture()
def frozen_utcnow():
    """Patch datetime.datetime.utcnow to return a fixed time."""
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FROZEN_NOW
        mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_delimiter_present(self, mod, fake_shared, frozen_utcnow):
        """Claude returns both parts separated by ---GAPS---."""
        fake_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        fake_shared.call_claude.return_value = (
            "# Solution overview\nSome doc content.\n"
            "---GAPS---\n"
            "1. What is the target go-live date?\n2. Who is the sponsor?"
        )

        doc, gaps = mod.generate_biz_doc("my-org", "my-repo", "My Project", "1.0.0", "https://run")

        assert "# Solution overview" in doc
        assert "Some doc content." in doc
        assert "1. What is the target go-live date?" in gaps
        assert "2. Who is the sponsor?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_delimiter_absent_fallback(self, mod, fake_shared, frozen_utcnow):
        """Claude returns a single block without the delimiter."""
        fake_shared.call_claude.return_value = "# Solution overview\nOnly one part."

        doc, gaps = mod.generate_biz_doc("org", "repo", "Project", "0.1.0", "https://run")

        assert doc == "# Solution overview\nOnly one part."
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_args(self, mod, fake_shared, frozen_utcnow):
        """get_repo_files receives the expected extensions and max_files."""
        mod.generate_biz_doc("owner", "repo", "P", "1.0", "http://run")

        call_args = fake_shared.get_repo_files.call_args
        assert call_args[0][0] == "owner"
        assert call_args[0][1] == "repo"
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert call_args[0][2] == expected_exts
        assert call_args[1].get("max_files") == 20 or call_args[0][3] == 20

    def test_call_claude_receives_project_name_and_version(self, mod, fake_shared, frozen_utcnow):
        """The prompt passed to Claude contains project_name and version."""
        frozen_utcnow.utcnow.return_value = FROZEN_NOW
        mod.generate_biz_doc("owner", "repo", "InsuranceBot", "2.3.4", "http://run")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "InsuranceBot" in prompt_arg
        assert "2.3.4" in prompt_arg

    def test_call_claude_receives_file_contents(self, mod, fake_shared, frozen_utcnow):
        """The user content passed to Claude includes repo files."""
        fake_shared.get_repo_files.return_value = {"deploy.tf": "resource 'aws' {}"}
        mod.generate_biz_doc("o", "r", "Proj", "1.0", "http://run")

        user_content_arg = fake_shared.call_claude.call_args[0][1]
        assert "deploy.tf" in user_content_arg
        assert "resource 'aws' {}" in user_content_arg

    def test_empty_repo_files(self, mod, fake_shared, frozen_utcnow):
        """No files in repo — should still call Claude with empty files section."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "http://run")
        assert doc == "doc"
        assert gaps == "gaps"

    def test_multiple_delimiter_occurrences_splits_on_first(self, mod, fake_shared, frozen_utcnow):
        """Only the first ---GAPS--- delimiter is used for splitting."""
        fake_shared.call_claude.return_value = (
            "Doc part\n---GAPS---\nGaps part\n---GAPS---\nExtra junk"
        )

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "http://run")
        assert doc == "Doc part"
        assert "Gaps part" in gaps
        assert "Extra junk" in gaps

    def test_delimiter_at_start(self, mod, fake_shared, frozen_utcnow):
        """Delimiter appears at the very beginning — doc part is empty."""
        fake_shared.call_claude.return_value = "---GAPS---\n1. Question one?"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "http://run")
        assert doc == ""
        assert "1. Question one?" in gaps

    def test_delimiter_at_end(self, mod, fake_shared, frozen_utcnow):
        """Delimiter appears at the very end — gaps part is empty string."""
        fake_shared.call_claude.return_value = "Doc content\n---GAPS---"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "http://run")
        assert doc == "Doc content"
        assert gaps == ""

    def test_file_content_truncated_to_3000_chars(self, mod, fake_shared, frozen_utcnow):
        """File contents longer than 3000 chars are truncated in the prompt."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        mod.generate_biz_doc("o", "r", "P", "1.0", "http://run")

        user_content_arg = fake_shared.call_claude.call_args[0][1]
        # The truncated content (3000 x's) should appear, not the full 5000
        assert "x" * 3000 in user_content_arg
        assert "x" * 3001 not in user_content_arg

    @pytest.mark.parametrize("owner,repo,project_name,version", [
        ("org", "repo", "Generations II", "1.2.3"),
        ("sun-life", "insurance-bot", "Health Products Portal", "0.9.0"),
        ("", "repo", "Project", "1.0.0"),
        ("org", "", "Project", "1.0.0"),
    ])
    def test_parametrised_inputs(self, mod, fake_shared, frozen_utcnow, owner, repo, project_name, version):
        """Smoke test various owner/repo/project/version combinations."""
        fake_shared.call_claude.return_value = f"Doc for {project_name}\n---GAPS---\n1. Q?"
        doc, gaps = mod.generate_biz_doc(owner, repo, project_name, version, "http://run")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_full_md_contains_doc(self, mod, frozen_utcnow):
        full_md, _ = mod.build_full_output(
            "# Solution", "1. Q?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert "# Solution" in full_md

    def test_full_md_contains_gaps(self, mod, frozen_utcnow):
        full_md, _ = mod.build_full_output(
            "Doc", "1. Who is sponsor?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert "1. Who is sponsor?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, mod, frozen_utcnow):
        full_md, _ = mod.build_full_output(
            "Doc", "1. Q?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, mod, frozen_utcnow):
        full_md, _ = mod.build_full_output(
            "Doc", "1. Q?", "owner", "repo", "MyProject", "2.0.0"
        )
        assert "owner/repo" in full_md
        assert "v2.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, mod, frozen_utcnow):
        _, gap_only = mod.build_full_output(
            "Doc", "1. Q?", "owner", "repo", "Generations II", "3.1.0"
        )
        assert "Generations II" in gap_only
        assert "v3.1.0" in gap_only

    def test_gap_only_md_contains_gap_questions(self, mod, frozen_utcnow):
        _, gap_only = mod.build_full_output(
            "Doc", "1. What is go-live date?\n2. Who owns it?", "o", "r", "P", "1.0"
        )
        assert "1. What is go-live date?" in gap_only
        assert "2. Who owns it?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, mod, frozen_utcnow):
        _, gap_only = mod.build_full_output(
            "Doc", "1. Q?", "owner", "repo", "Proj", "1.0"
        )
        assert FAKE_OUTPUT_REPO_OWNER in gap_only
        assert FAKE_OUTPUT_REPO in gap_only

    def test_gap_only_md_does_not_contain_solution_doc_body(self, mod, frozen_utcnow):
        _, gap_only = mod.build_full_output(
            "## Executive Summary\nSome long narrative text.", "1. Q?",
            "o", "r", "P", "1.0"
        )
        # The full doc body should not bleed into the gap-only file
        assert "Some long narrative text." not in gap_only

    def test_full_md_estimated_time_note(self, mod, frozen_utcnow):
        _, gap_only = mod.build_full_output("D", "G", "o", "r", "P", "1.0")
        assert "10-15 minutes" in gap_only

    def test_empty_doc_and_gaps(self, mod, frozen_utcnow):
        full_md, gap_only = mod.build_full_output("", "", "o", "r", "P", "1.0")
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_returns_tuple_of_two_strings(self, mod, frozen_utcnow):
        result = mod.build_full_output("Doc", "Gaps", "o", "r", "Proj", "