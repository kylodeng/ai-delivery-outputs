"""
Test suite for tool2_tech_docs.py

What is tested:
  - generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
  - build_index(): constructs a markdown index from doc names, owner/repo, and timestamp
  - __main__ block behaviour: success path and failure/exception path

Mocks used:
  - shared.call_claude         → patched to return deterministic strings
  - shared.get_repo_files      → patched to return synthetic file dicts
  - shared.write_output_file   → patched to return fake URLs
  - shared.send_email          → patched to no-op
  - shared.email_html          → patched to return a plain string
  - shared.write_audit_entry   → patched to no-op
  - shared.OUTPUT_REPO_OWNER   → patched constant
  - shared.OUTPUT_REPO         → patched constant
  - datetime.datetime.utcnow   → patched for deterministic timestamps

TODOs:
  - TODO: Integration test against real Claude API (needs ANTHROPIC_API_KEY and network)
  - TODO: Test actual GitHub file fetching via get_repo_files (needs GH_TOKEN and network)
  - TODO: Test write_output_file against a real output repo (needs write credentials)
"""

import importlib
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"

FAKE_PY_FILES = {
    "src/main.py": "print('hello')",
    "src/utils.py": "def helper(): pass",
}

FAKE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "infra/variables.yaml": "env: prod",
}

FAKE_README = "# README\nThis is a generated readme."
FAKE_ARCH = "# ARCHITECTURE\nThis is a generated architecture doc."
FAKE_RUNBOOK = "# RUNBOOK\nThis is a generated runbook."


def _make_shared_stub():
    """Return a minimal stub module for `shared` so we can import tool2_tech_docs."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="stub")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/stub")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html/>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def stub_shared(monkeypatch):
    """Inject a stub `shared` module before every test."""
    shared_stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", shared_stub)
    # Remove cached tool2_tech_docs so it re-imports with fresh stubs
    sys.modules.pop("tool2_tech_docs", None)
    yield shared_stub
    sys.modules.pop("tool2_tech_docs", None)


def import_tool2():
    """Import (or re-import) tool2_tech_docs after stubs are in place."""
    return importlib.import_module("tool2_tech_docs")


# ---------------------------------------------------------------------------
# generate_docs() tests
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_all_three_docs(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        result = mod.generate_docs("my-org", "my-repo", "https://github.com/actions/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}
        assert result["README.md"] == FAKE_README
        assert result["ARCHITECTURE.md"] == FAKE_ARCH
        assert result["RUNBOOK.md"] == FAKE_RUNBOOK

    def test_get_repo_files_called_with_correct_args(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("owner", "repo", "http://run")

        calls = stub_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call: source files
        first_args, first_kwargs = calls[0]
        assert first_args[0] == "owner"
        assert first_args[1] == "repo"
        assert ".py" in first_args[2]
        assert first_kwargs.get("max_files") == 15

        # Second call: IaC files
        second_args, second_kwargs = calls[1]
        assert second_args[0] == "owner"
        assert second_args[1] == "repo"
        assert ".tf" in second_args[2]
        assert second_kwargs.get("max_files") == 10

    def test_call_claude_called_three_times(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("owner", "repo", "http://run")

        assert stub_shared.call_claude.call_count == 3

    def test_readme_prompt_contains_owner_repo(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("my-org", "cool-repo", "http://run")

        readme_user_prompt = stub_shared.call_claude.call_args_list[0][0][1]
        assert "my-org" in readme_user_prompt
        assert "cool-repo" in readme_user_prompt

    def test_arch_prompt_contains_owner_repo(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("my-org", "cool-repo", "http://run")

        arch_user_prompt = stub_shared.call_claude.call_args_list[1][0][1]
        assert "my-org" in arch_user_prompt
        assert "cool-repo" in arch_user_prompt

    def test_runbook_prompt_contains_owner_repo(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("my-org", "cool-repo", "http://run")

        runbook_user_prompt = stub_shared.call_claude.call_args_list[2][0][1]
        assert "my-org" in runbook_user_prompt
        assert "cool-repo" in runbook_user_prompt

    def test_no_py_files_still_generates_docs(self, stub_shared):
        """Edge case: repo has no Python/JS/TS/Go files."""
        stub_shared.get_repo_files.side_effect = [{}, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        result = mod.generate_docs("owner", "repo", "http://run")

        assert len(result) == 3

    def test_no_iac_files_still_generates_docs(self, stub_shared):
        """Edge case: repo has no IaC files."""
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, {}]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        result = mod.generate_docs("owner", "repo", "http://run")

        assert len(result) == 3

    def test_no_files_at_all_still_generates_docs(self, stub_shared):
        """Edge case: completely empty repo."""
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        result = mod.generate_docs("owner", "repo", "http://run")

        assert len(result) == 3
        # Prompt should contain the no-files placeholder
        readme_prompt = stub_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_fmt_truncates_file_content_at_4000_chars(self, stub_shared):
        """Files longer than 4000 characters must be truncated in the prompt."""
        long_content = "x" * 10_000
        stub_shared.get_repo_files.side_effect = [{"src/big.py": long_content}, {}]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("owner", "repo", "http://run")

        readme_prompt = stub_shared.call_claude.call_args_list[0][0][1]
        # The truncated content (4000 x's) should appear; 10000 x's should not
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_call_claude_propagates_exception(self, stub_shared):
        """If Claude call fails, exception should bubble up."""
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = RuntimeError("Claude API error")

        mod = import_tool2()
        with pytest.raises(RuntimeError, match="Claude API error"):
            mod.generate_docs("owner", "repo", "http://run")

    def test_get_repo_files_propagates_exception(self, stub_shared):
        stub_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        mod = import_tool2()
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("owner", "repo", "http://run")

    def test_system_prompt_readme_passed_to_claude(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("owner", "repo", "http://run")

        readme_system_prompt = stub_shared.call_claude.call_args_list[0][0][0]
        assert "senior technical writer" in readme_system_prompt.lower() or \
               "README" in readme_system_prompt

    def test_system_prompt_arch_passed_to_claude(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("owner", "repo", "http://run")

        arch_system_prompt = stub_shared.call_claude.call_args_list[1][0][0]
        assert "architect" in arch_system_prompt.lower() or "architecture" in arch_system_prompt.lower()

    def test_system_prompt_runbook_passed_to_claude(self, stub_shared):
        stub_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        stub_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        mod = import_tool2()
        mod.generate_docs("owner", "repo", "http://run")

        runbook_system_prompt = stub_shared.call_claude.call_args_list[2][0][0]
        assert "devops" in runbook_system_prompt.lower() or "runbook" in runbook_system_prompt.lower()


# ---------------------------------------------------------------------------
# build_index() tests
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def _get_build_index(self):
        mod = import_tool2()
        return mod.build_index

    def test_happy_path_contains_all_doc_names(self, stub_shared):
        build_index = self._get_build_index()
        docs = {"README.md": FAKE_README, "ARCHITECTURE.md": FAKE_ARCH, "RUNBOOK.md": FAKE_RUNBOOK}
        result = build_index("my-org", "my-repo", docs, "2024-01-15 10:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_contains_owner_and_repo(self, stub_shared):
        build_index = self._get_build_index()
        docs = {"README.md": FAKE_README}
        result = build_index("my-org", "my-repo", docs, "2024-01-15 10:00 UTC")

        assert "my-org" in result
        assert "my-repo" in result

    def test_contains_timestamp(self, stub_shared):
        build_index = self._get_build_index()
        docs = {"README.md": FAKE_README}
        timestamp = "2024-06-01 09:30 UTC"
        result = build_index("owner", "repo", docs, timestamp)

        assert timestamp in result

    def test_links_use_output_repo_owner_and_repo(self, stub_shared):
        build_index = self._get_build_index()
        docs = {"README.md": FAKE_README}
        result = build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")

        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_are_valid_github_urls(self, stub_shared):
        build_index = self._get_build_index()
        docs = {"README.md": FAKE_README,