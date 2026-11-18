"""
Tests for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown structure, link generation, timestamp inclusion
- fmt() inner function behaviour (via generate_docs integration)
- __main__ block: success path, exception/failure path

Mocks used:
- shared.call_claude          → prevents real Anthropic API calls
- shared.get_repo_files       → prevents real GitHub API calls
- shared.write_output_file    → prevents real GitHub writes
- shared.send_email           → prevents real SES/SMTP calls
- shared.email_html           → prevents real template rendering
- shared.write_audit_entry    → prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    → patched to deterministic value
- shared.OUTPUT_REPO          → patched to deterministic value
- datetime.datetime           → frozen for deterministic timestamp tests
- sys.exit / raise            → verified in __main__ error path

TODOs:
# TODO: Integration test against a real GitHub repo + Claude API — skipped, needs credentials
# TODO: Test rate-limiting / retry behaviour in call_claude — depends on shared.py internals
# TODO: Test file truncation at 4000 chars — needs access to fmt() as a named function
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we can import tool2_tech_docs
# without the real shared.py needing to be importable in the test environment.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-org"
FAKE_OUTPUT_REPO = "ai-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>body</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fake shared module before every test."""
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)
    yield mod


@pytest.fixture()
def tool(fake_shared):
    """Import (or re-import) the module under test with the fake shared in place."""
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try importing directly from the scripts directory
    import importlib.util

    script_path = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts", "tool2_tech_docs.py"
    )
    script_path = os.path.normpath(script_path)

    if not os.path.exists(script_path):
        pytest.skip(f"Source file not found at {script_path}")

    spec = importlib.util.spec_from_file_location("tool2_tech_docs", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = module
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:
    def test_returns_string(self, tool):
        result = tool.build_index("my-owner", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, tool):
        result = tool.build_index("acme", "backend", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "backend" in result

    def test_contains_timestamp(self, tool):
        now = "2024-06-01 12:30 UTC"
        result = tool.build_index("owner", "repo", {"README.md": "x"}, now)
        assert now in result

    def test_contains_all_document_links(self, tool):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = tool.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_format_uses_output_repo(self, tool, fake_shared):
        docs = {"README.md": "content"}
        result = tool.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        expected_fragment = f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}/blob/main/tech-docs/owner-repo/README.md"
        assert expected_fragment in result

    def test_contains_auto_generated_footer(self, tool):
        result = tool.build_index("owner", "repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_empty_docs_dict(self, tool):
        result = tool.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)
        assert "owner/repo" in result

    def test_heading_format(self, tool):
        result = tool.build_index("my-owner", "my-repo", {}, "2024-01-01 00:00 UTC")
        assert "# Tech Documentation Index" in result

    def test_multiple_docs_each_have_link(self, tool):
        docs = {
            "README.md": "r",
            "ARCHITECTURE.md": "a",
            "RUNBOOK.md": "rb",
            "INDEX.md": "i",
        }
        result = tool.build_index("org", "svc", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_owner_repo_separator_in_link(self, tool):
        """Links should use {owner}-{repo} as the directory name."""
        docs = {"README.md": "x"}
        result = tool.build_index("my-owner", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "my-owner-my-repo" in result

    def test_generated_field_label(self, tool):
        result = tool.build_index("o", "r", {}, "2099-12-31 23:59 UTC")
        assert "**Generated:**" in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:
    def _setup_files(self, fake_shared, py_files=None, iac_files=None):
        py_files = py_files or {"main.py": "print('hello')"}
        iac_files = iac_files or {"main.tf": 'resource "aws_s3_bucket" "b" {}'}

        def _get_repo_files(owner, repo, extensions, max_files=10):
            if ".py" in extensions:
                return py_files
            return iac_files

        fake_shared.get_repo_files.side_effect = _get_repo_files

    def test_returns_dict_with_three_keys(self, tool, fake_shared):
        self._setup_files(fake_shared)
        fake_shared.call_claude.return_value = "# Doc"
        result = tool.generate_docs("owner", "repo", "https://run.url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        first_call_system = fake_shared.call_claude.call_args_list[0][0][0]
        assert "senior technical writer" in first_call_system

    def test_arch_uses_system_arch_prompt(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        second_call_system = fake_shared.call_claude.call_args_list[1][0][0]
        assert "cloud solutions architect" in second_call_system

    def test_runbook_uses_system_runbook_prompt(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        third_call_system = fake_shared.call_claude.call_args_list[2][0][0]
        assert "DevOps engineer" in third_call_system

    def test_owner_repo_in_user_prompt(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("acme", "backend", "https://run.url")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme/backend" in user_prompt

    def test_get_repo_files_called_twice(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        assert fake_shared.get_repo_files.call_count == 2

    def test_py_js_extensions_requested(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        calls = fake_shared.get_repo_files.call_args_list
        py_call_exts = calls[0][0][2]
        assert ".py" in py_call_exts
        assert ".js" in py_call_exts
        assert ".ts" in py_call_exts

    def test_iac_extensions_requested(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        calls = fake_shared.get_repo_files.call_args_list
        iac_call_exts = calls[1][0][2]
        assert ".tf" in iac_call_exts
        assert ".yaml" in iac_call_exts
        assert ".yml" in iac_call_exts

    def test_max_files_py_is_15(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        calls = fake_shared.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15

    def test_max_files_iac_is_10(self, tool, fake_shared):
        self._setup_files(fake_shared)
        tool.generate_docs("owner", "repo", "https://run.url")
        calls = fake_shared.get_repo_files.call_args_list
        assert calls[1][1].get("max_files") == 10

    def test_empty_file_sets_uses_no_files_found(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("owner", "repo", "https://run.url")
        # call_claude should still be called 3 times even with empty files
        assert fake_shared.call_claude.call_count == 3
        # The user prompt should contain the fallback string
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_doc_content_comes_from_call_claude(self, tool, fake_shared):
        self._setup_files(fake_shared)
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]
        result = tool.generate_docs("owner", "repo", "https://run.url")
        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_call_claude_exception_propagates(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            tool.generate_docs("owner", "repo", "https://run.url")

    def test_file_content_included_in_prompt(self, tool, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            {"app.py": "def hello(): pass"},
            {"infra.tf": 'resource "aws_lambda" {}'},
        ]
        tool.generate_docs("owner", "repo", "https://run.url")
        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "app.py" in readme_user_prompt
        assert "def hello(): pass" in readme_user_prompt

    def test_long_file_content_truncated_to_4000(self, tool, fake_shared):
        long_content = "x" * 5000
        fake_shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        tool.generate_docs("owner", "repo", "https://run.url")
        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The first 4000 chars should be present, but not beyond
        assert "x" * 4000 in readme_user_prompt
        assert "x" * 5000 not in readme_user_prompt

    def test_iac_files_in_arch_prompt(self, tool, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            {"app.py": "print('hi')"},
            {"main.tf": "terraform {}"},
        ]
        tool.generate_docs("owner", "repo", "https://run.url")
        arch_user_prompt = fake_shared.call_claude.call_args_list[1][0][1]
        assert "main.tf" in arch_user_prompt
        assert "terraform {}" in arch_user_prompt

    def test_synthetic_data_