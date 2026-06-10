"""
Tests for tool2_tech_docs.py
============================
What is tested:
- generate_docs(): happy path, empty file sets, Claude call arguments
- build_index(): link generation, formatting, edge cases (empty docs, single doc, many docs)
- __main__ block logic (via subprocess or importlib): success path, exception/failure path
- fmt() helper (indirectly through generate_docs)

Mocks used:
- shared.call_claude          → unittest.mock.patch
- shared.get_repo_files       → unittest.mock.patch
- shared.write_output_file    → unittest.mock.patch
- shared.send_email           → unittest.mock.patch
- shared.email_html           → unittest.mock.patch
- shared.write_audit_entry    → unittest.mock.patch
- shared.OUTPUT_REPO_OWNER    → patched as module-level attribute
- shared.OUTPUT_REPO          → patched as module-level attribute
- datetime.datetime.utcnow    → unittest.mock.patch

TODOs:
- TODO: Integration test against a real GitHub repo (needs GH token + live repo)
- TODO: Test call_claude retry / rate-limit behaviour (needs shared.py internals)
- TODO: Verify exact email HTML structure (needs email_html implementation details)
"""

import sys
import os
import importlib
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared patched out
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"
TOOL_MODULE_NAME   = "tool2_tech_docs"


def _make_shared_stub():
    """Return a minimal stub for the `shared` module."""
    stub = types.ModuleType("shared")
    stub.call_claude        = MagicMock(return_value="# Generated content")
    stub.get_repo_files     = MagicMock(return_value={})
    stub.write_output_file  = MagicMock(return_value="https://github.com/out/file")
    stub.send_email         = MagicMock()
    stub.email_html         = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry  = MagicMock()
    stub.OUTPUT_REPO_OWNER  = "output-owner"
    stub.OUTPUT_REPO        = "output-repo"
    return stub


def _import_tool(shared_stub=None):
    """Import (or re-import) tool2_tech_docs with the given shared stub."""
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Ensure shared is in sys.modules before the tool imports it
    sys.modules["shared"] = shared_stub

    # Force re-import so module-level `from shared import …` runs fresh
    if TOOL_MODULE_NAME in sys.modules:
        del sys.modules[TOOL_MODULE_NAME]

    # Make sure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also allow importing directly from the repo root for CI
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    for candidate in [
        script_dir,
        os.path.join(repo_root, ".github", "scripts"),
    ]:
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)

    tool = importlib.import_module(TOOL_MODULE_NAME)
    return tool, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def tool(shared_stub):
    mod, _ = _import_tool(shared_stub)
    return mod


@pytest.fixture()
def tool_and_shared():
    stub = _make_shared_stub()
    mod, _ = _import_tool(stub)
    return mod, stub


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "main.py":   "def main():\n    pass\n",
    "utils.py":  "def helper():\n    return 42\n",
}

SAMPLE_IAC_FILES = {
    "main.tf":   'resource "aws_s3_bucket" "b" {}\n',
    "vars.yaml": "env: production\n",
}

SAMPLE_DOCS = {
    "README.md":       "# README\nContent here.",
    "ARCHITECTURE.md": "# Architecture\nContent here.",
    "RUNBOOK.md":      "# Runbook\nContent here.",
}


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_basic_links_present(self, tool):
        result = tool.build_index("myowner", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        for name in SAMPLE_DOCS:
            assert name in result

    def test_links_contain_github_url(self, tool):
        result = tool.build_index("myowner", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "https://github.com/" in result

    def test_links_contain_output_repo_owner_and_repo(self, tool, shared_stub):
        result = tool.build_index("myowner", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        # OUTPUT_REPO_OWNER and OUTPUT_REPO come from the stub
        assert shared_stub.OUTPUT_REPO_OWNER in result
        assert shared_stub.OUTPUT_REPO in result

    def test_links_contain_owner_and_repo_in_path(self, tool):
        result = tool.build_index("myowner", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "myowner-myrepo" in result

    def test_generated_timestamp_present(self, tool):
        now = "2024-06-01 08:30 UTC"
        result = tool.build_index("o", "r", SAMPLE_DOCS, now)
        assert now in result

    def test_header_contains_owner_and_repo(self, tool):
        result = tool.build_index("acme", "backend", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "acme/backend" in result

    def test_auto_generated_footer(self, tool):
        result = tool.build_index("o", "r", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_empty_docs_dict(self, tool):
        """build_index with no docs should not raise; links section is empty."""
        result = tool.build_index("o", "r", {}, "2024-01-01 00:00 UTC")
        assert "Tech Documentation Index" in result
        # No bullet links expected
        assert "blob/main" not in result

    def test_single_doc(self, tool):
        docs = {"README.md": "content"}
        result = tool.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result

    def test_many_docs(self, tool):
        docs = {f"DOC_{i}.md": f"content {i}" for i in range(10)}
        result = tool.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_special_characters_in_owner_repo(self, tool):
        """Hyphens and underscores in owner/repo names should be handled."""
        result = tool.build_index("my-org", "my_repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "my-org" in result
        assert "my_repo" in result

    def test_returns_string(self, tool):
        result = tool.build_index("o", "r", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)

    def test_link_format(self, tool):
        """Each doc should appear as a markdown list item with a link."""
        docs = {"README.md": "x"}
        result = tool.build_index("owner", "repo", docs, "2024-01-01")
        assert "- [README.md](" in result

    def test_full_link_path_structure(self, tool, shared_stub):
        """Full link should include tech-docs/<owner>-<repo>/<filename>."""
        docs = {"README.md": "x"}
        result = tool.build_index("owner", "repo", docs, "2024-01-01")
        expected_path = f"tech-docs/owner-repo/README.md"
        assert expected_path in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_returns_three_documents(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = SAMPLE_PY_FILES
        shared_stub.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]
        docs = tool.generate_docs("owner", "repo", "https://github.com/run/1")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("owner", "repo", "https://run")
        assert shared_stub.call_claude.call_count == 3

    def test_readme_system_prompt_used(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        first_call_system = shared_stub.call_claude.call_args_list[0][0][0]
        assert "README" in first_call_system or "technical writer" in first_call_system.lower()

    def test_arch_system_prompt_used(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        second_call_system = shared_stub.call_claude.call_args_list[1][0][0]
        assert "architect" in second_call_system.lower() or "architecture" in second_call_system.lower()

    def test_runbook_system_prompt_used(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        third_call_system = shared_stub.call_claude.call_args_list[2][0][0]
        assert "runbook" in third_call_system.lower() or "devops" in third_call_system.lower()

    def test_user_prompt_contains_owner_repo(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("acme", "backend", "url")
        for c in shared_stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme" in user_prompt
            assert "backend" in user_prompt

    def test_get_repo_files_called_for_code_files(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        calls = shared_stub.get_repo_files.call_args_list
        extensions_requested = [c[0][2] for c in calls]
        all_exts = [ext for sublist in extensions_requested for ext in sublist]
        assert ".py" in all_exts
        assert ".js" in all_exts

    def test_get_repo_files_called_for_iac_files(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        calls = shared_stub.get_repo_files.call_args_list
        extensions_requested = [c[0][2] for c in calls]
        all_exts = [ext for sublist in extensions_requested for ext in sublist]
        assert ".tf" in all_exts or ".yaml" in all_exts

    def test_max_files_limit_respected_for_code(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        # First call should have max_files=15
        first_call_kwargs = shared_stub.get_repo_files.call_args_list[0][1]
        assert first_call_kwargs.get("max_files") == 15

    def test_max_files_limit_respected_for_iac(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        # Second call should have max_files=10
        second_call_kwargs = shared_stub.get_repo_files.call_args_list[1][1]
        assert second_call_kwargs.get("max_files") == 10

    def test_doc_content_comes_from_claude(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = ["README text", "ARCH text", "RUNBOOK text"]
        docs = tool.generate_docs("o", "r", "url")
        assert docs["README.md"]       == "README text"
        assert docs["ARCHITECTURE.md"] == "ARCH text"
        assert docs["RUNBOOK.md"]      == "RUNBOOK text"

    def test_empty_files_uses_no_files_found_placeholder(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        # With no files, all prompts should contain the placeholder
        for c in shared_stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_at_4000_chars(self, tool, shared_stub):
        long_content = "x" * 10_000
        shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        shared_stub.call_claude.return_value = "content"
        tool.generate_docs("o", "r", "url")
        # The prompt should contain only up to 4000 chars of the file
        readme_prompt = shared_stub.call_claude.