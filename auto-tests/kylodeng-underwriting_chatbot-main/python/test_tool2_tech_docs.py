"""
Tests for tool2_tech_docs.py
============================
What is tested:
  - generate_docs(): orchestrates file fetching + Claude calls → returns doc dict
  - build_index(): constructs markdown index page from doc dict
  - __main__ block behaviour: success path and failure/exception path

Mocks used:
  - shared.call_claude          — prevents real Anthropic API calls
  - shared.get_repo_files       — prevents real GitHub API calls
  - shared.write_output_file    — prevents real GitHub file writes
  - shared.send_email           — prevents real SMTP/SES calls
  - shared.email_html           — prevents rendering side-effects
  - shared.write_audit_entry    — prevents real audit writes
  - shared.OUTPUT_REPO_OWNER    — constant patched for predictable URLs
  - shared.OUTPUT_REPO          — constant patched for predictable URLs
  - datetime.datetime           — frozen for deterministic timestamp tests

TODOs:
  - TODO: Integration test against a real GitHub repo (requires auth tokens)
  - TODO: Test with actual Anthropic Claude API (requires ANTHROPIC_API_KEY)
  - TODO: Test write_output_file race condition / rate-limit retry logic (needs shared impl)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake `shared` module so the import in the script
# succeeds without any real credentials or network access.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_module():
    """Return a fake `shared` module with controllable stubs."""
    mod = types.ModuleType("shared")
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    mod.call_claude = MagicMock(return_value="# Generated Doc")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Insert a fake `shared` module before each test and reload the target module
    so each test starts with a clean slate.
    """
    mod = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", mod)

    # Remove cached target module so it re-imports cleanly
    sys.modules.pop("tool2_tech_docs", None)
    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        monkeypatch.syspath_prepend(script_dir)

    yield mod


def _import_target():
    """Import (or re-import) the module under test."""
    sys.modules.pop("tool2_tech_docs", None)
    import tool2_tech_docs as target
    return target


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:
    def test_happy_path_contains_all_doc_names(self):
        target = _import_target()
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = target.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_happy_path_contains_owner_and_repo(self):
        target = _import_target()
        docs = {"README.md": "content"}
        result = target.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "acme/my-repo" in result

    def test_happy_path_contains_generated_timestamp(self):
        target = _import_target()
        docs = {"README.md": "content"}
        now = "2024-06-01 12:00 UTC"
        result = target.build_index("acme", "my-repo", docs, now)
        assert now in result

    def test_links_point_to_correct_github_url(self):
        target = _import_target()
        docs = {"README.md": "content"}
        result = target.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        expected_url = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/acme-my-repo/README.md"
        )
        assert expected_url in result

    def test_header_is_correct(self):
        target = _import_target()
        docs = {"README.md": "content"}
        result = target.build_index("acme", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert result.startswith("# Tech Documentation Index")

    def test_footer_attribution_present(self):
        target = _import_target()
        docs = {"README.md": "content"}
        result = target.build_index("acme", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_empty_docs_dict(self):
        target = _import_target()
        result = target.build_index("acme", "my-repo", {}, "2024-01-01 00:00 UTC")
        # Should still produce a valid index without crashing
        assert "# Tech Documentation Index" in result
        assert "acme/my-repo" in result

    def test_multiple_docs_each_have_separate_links(self):
        target = _import_target()
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = target.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        # Each doc should produce exactly one bullet link
        assert result.count("- [") == 3

    def test_owner_repo_with_special_characters_in_name(self):
        """Repo names can contain hyphens and dots."""
        target = _import_target()
        docs = {"README.md": "content"}
        result = target.build_index("my-org", "my.repo-v2", docs, "2024-01-01 00:00 UTC")
        assert "my-org/my.repo-v2" in result

    def test_link_path_uses_hyphenated_owner_repo(self):
        """Path segment in URL must use 'owner-repo' format (hyphen, not slash)."""
        target = _import_target()
        docs = {"README.md": "content"}
        result = target.build_index("acme", "backend", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/acme-backend/README.md" in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:
    def test_returns_three_doc_keys(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc content"
        target = _import_target()
        result = target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        assert fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_py_js_extensions(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        # First call: source files
        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".go" in extensions

    def test_get_repo_files_called_with_iac_extensions(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions

    def test_calls_call_claude_three_times(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        readme_call = fake_shared.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "senior technical writer" in system_prompt

    def test_architecture_uses_system_arch_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        arch_call = fake_shared.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "cloud solutions architect" in system_prompt

    def test_runbook_uses_system_runbook_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        runbook_call = fake_shared.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "DevOps engineer" in system_prompt

    def test_returned_docs_contain_claude_response(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Custom Claude Response"
        target = _import_target()
        result = target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        for doc_content in result.values():
            assert doc_content == "# Custom Claude Response"

    def test_owner_and_repo_appear_in_claude_user_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme/my-repo" in user_prompt

    def test_with_actual_file_content_included_in_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {
            "app.py": "print('hello')",
            "main.go": "package main",
        }
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "app.py" in readme_user_prompt
        assert "print('hello')" in readme_user_prompt

    def test_long_file_content_truncated_to_4000_chars(self, fake_shared):
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The truncated content (4000 x's) should be present; the full 10000 should not
        assert "x" * 4000 in readme_user_prompt
        assert "x" * 4001 not in readme_user_prompt

    def test_no_files_found_shows_placeholder(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_user_prompt

    def test_max_files_limit_passed_for_source_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        target = _import_target()
        target.generate_docs("acme", "my-repo", "https://github.com/actions/run/1")
        first_call_kwargs = fake_shared.get_repo_files.call_args_list[0]
        assert first_call_kwargs[1].get("max_files") == 15 or first_call_kwargs[0][3] == 15

    def test_max_files_limit_passed_for_iac_files(self, fake_shared):
        fake_shared.get_repo