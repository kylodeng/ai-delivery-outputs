"""
Tests for tool2_tech_docs.py
============================

What is tested:
  - generate_docs(): orchestrates file fetching and Claude calls for README, ARCHITECTURE, RUNBOOK
  - build_index(): produces correct markdown index with links and timestamps
  - __main__ block: success path (writes files, sends email, writes audit) and failure path (audit + email on exception)

Mocks used:
  - shared.call_claude          — prevents real API calls to Claude/Anthropic
  - shared.get_repo_files       — prevents real GitHub API calls
  - shared.write_output_file    — prevents real file writes to output repo
  - shared.send_email           — prevents real SES / SMTP calls
  - shared.email_html           — prevents dependency on email templating
  - shared.write_audit_entry    — prevents real audit log writes
  - datetime.datetime           — frozen for deterministic timestamp tests
  - os.environ                  — patched for __main__ block tests

TODOs:
  - TODO: Integration test that verifies the exact Claude prompt strings match expected templates
  - TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO constants have special characters
  - TODO: Test with real shared module if available in CI (currently fully mocked)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake "shared" module so the import at module load time
# does not require the real shared.py to be on the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake shared module with all symbols used by tool2."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def inject_fake_shared(monkeypatch):
    """Inject a fresh fake shared module before every test."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    yield fake


@pytest.fixture
def tool2(inject_fake_shared):
    """Import (or reload) the module under test after shared is patched."""
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import tool2_tech_docs
    return tool2_tech_docs


# ===========================================================================
# build_index()
# ===========================================================================

class TestBuildIndex:
    def test_returns_string(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("owner", "repo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_title_contains_owner_and_repo(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("myorg", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert "myorg/myrepo" in result

    def test_generated_timestamp_present(self, tool2):
        docs = {"README.md": "x"}
        now = "2024-06-30 12:34 UTC"
        result = tool2.build_index("o", "r", docs, now)
        assert now in result

    def test_links_use_output_repo_owner_and_repo(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("owner", "repo", docs, "now")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_includes_document_name(self, tool2):
        docs = {"README.md": "x", "ARCHITECTURE.md": "y"}
        result = tool2.build_index("owner", "repo", docs, "now")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result

    def test_link_includes_owner_repo_in_path(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("myowner", "myrepo", docs, "now")
        assert "tech-docs/myowner-myrepo/README.md" in result

    def test_multiple_docs_all_linked(self, tool2):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = tool2.build_index("o", "r", docs, "now")
        for name in docs:
            assert name in result

    def test_empty_docs_returns_string(self, tool2):
        result = tool2.build_index("o", "r", {}, "now")
        assert isinstance(result, str)
        # No links section should still render
        assert "Tech Documentation Index" in result

    def test_auto_generated_footer(self, tool2):
        result = tool2.build_index("o", "r", {"README.md": "x"}, "now")
        assert "Auto-generated" in result

    def test_link_format_is_github_url(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("o", "r", docs, "now")
        assert "https://github.com/" in result

    def test_special_characters_in_owner_repo(self, tool2):
        """owner/repo names with hyphens should be handled correctly."""
        docs = {"README.md": "x"}
        result = tool2.build_index("my-org", "my-repo", docs, "now")
        assert "my-org/my-repo" in result
        assert "tech-docs/my-org-my-repo/README.md" in result


# ===========================================================================
# generate_docs()
# ===========================================================================

class TestGenerateDocs:
    def test_returns_three_docs(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "# Doc content"

        result = tool2.generate_docs("owner", "repo", "https://run.url")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        assert inject_fake_shared.call_claude.call_count == 3

    def test_get_repo_files_called_twice(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        assert inject_fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_py_extensions(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        first_call_args = inject_fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions

    def test_get_repo_files_second_call_iac_extensions(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        second_call_args = inject_fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions or ".yaml" in extensions or ".yml" in extensions

    def test_readme_uses_readme_system_prompt(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        # First call to call_claude should pass SYSTEM_README as first arg
        first_call = inject_fake_shared.call_claude.call_args_list[0]
        system_prompt = first_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_architecture_uses_arch_system_prompt(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        second_call = inject_fake_shared.call_claude.call_args_list[1]
        system_prompt = second_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_runbook_uses_runbook_system_prompt(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        third_call = inject_fake_shared.call_claude.call_args_list[2]
        system_prompt = third_call[0][0]
        assert "runbook" in system_prompt.lower() or "devops" in system_prompt.lower()

    def test_doc_values_are_call_claude_return_values(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        readme_content = "# README content"
        arch_content = "# ARCH content"
        runbook_content = "# RUNBOOK content"
        inject_fake_shared.call_claude.side_effect = [readme_content, arch_content, runbook_content]

        result = tool2.generate_docs("owner", "repo", "https://run.url")

        assert result["README.md"] == readme_content
        assert result["ARCHITECTURE.md"] == arch_content
        assert result["RUNBOOK.md"] == runbook_content

    def test_owner_repo_in_user_prompt(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("myowner", "myrepo", "https://run.url")

        for c in inject_fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "myowner" in user_prompt
            assert "myrepo" in user_prompt

    def test_with_actual_file_content(self, tool2, inject_fake_shared):
        """Files returned from get_repo_files are embedded in the Claude prompt."""
        inject_fake_shared.get_repo_files.side_effect = [
            {"main.py": "print('hello')"},
            {"main.tf": "resource aws_s3_bucket {}"},
        ]
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        # README prompt (first call) should contain source file content
        readme_prompt = inject_fake_shared.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_prompt
        assert "print('hello')" in readme_prompt

    def test_no_files_produces_no_files_found_placeholder(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        readme_prompt = inject_fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_at_4000_chars(self, tool2, inject_fake_shared):
        long_content = "x" * 5000
        inject_fake_shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        readme_prompt = inject_fake_shared.call_claude.call_args_list[0][0][1]
        # The embedded snippet should contain 4000 x's, not 5000
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_call_claude_exception_propagates(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            tool2.generate_docs("owner", "repo", "https://run.url")

    def test_get_repo_files_max_files_respected_py(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        first_call_kwargs = inject_fake_shared.get_repo_files.call_args_list[0]
        # max_files should be 15 for py/js files
        assert first_call_kwargs[1].get("max_files") == 15 or \
               (len(first_call_kwargs[0]) > 3 and first_call_kwargs[0][3] == 15)

    def test_get_repo_files_max_files_respected_iac(self, tool2, inject_fake_shared):
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run.url")

        second_call = inject_fake_shared.get_repo_files.call_args_list[