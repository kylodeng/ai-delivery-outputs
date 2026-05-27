"""
Tests for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
- build_index(): constructs a markdown index page linking to generated documents
- __main__ block behaviour: happy path (writes files, sends email, writes audit); failure path (audit + email on exception)

Mocks used:
- shared.call_claude          → prevents real Anthropic API calls
- shared.get_repo_files       → prevents real GitHub API calls
- shared.write_output_file    → prevents real GitHub write operations
- shared.send_email           → prevents real email delivery
- shared.email_html           → prevents real HTML rendering
- shared.write_audit_entry    → prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    → constant patched to a test value
- shared.OUTPUT_REPO          → constant patched to a test value
- datetime.datetime           → frozen for deterministic timestamp assertions

TODOs:
- TODO: Integration test that wires a real (sandboxed) Claude client once a test key is available
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are absent (None) at __main__ level
"""

import importlib
import sys
import os
import types
import datetime
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with all shared deps stubbed out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub module that replaces `shared`."""
    stub = types.ModuleType("shared")
    stub.call_claude = mock.MagicMock(return_value="# Generated content")
    stub.get_repo_files = mock.MagicMock(return_value={})
    stub.write_output_file = mock.MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/some/path")
    stub.send_email = mock.MagicMock()
    stub.email_html = mock.MagicMock(return_value="<html>mock</html>")
    stub.write_audit_entry = mock.MagicMock()
    stub.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    stub.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return stub


def _import_tool(shared_stub=None):
    """
    Import (or re-import) tool2_tech_docs with a fresh shared stub.
    Returns (module, shared_stub).
    """
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Insert stub before importing so the module picks it up via sys.path insertion
    sys.modules["shared"] = shared_stub

    # Force a fresh import every time
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import tool2_tech_docs as mod
    return mod, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_stub():
    stub = _make_shared_stub()
    yield stub
    # Cleanup
    if "shared" in sys.modules:
        del sys.modules["shared"]
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]


@pytest.fixture()
def tool(shared_stub):
    mod, _ = _import_tool(shared_stub)
    return mod


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, tool, shared_stub):
        """generate_docs returns README.md, ARCHITECTURE.md and RUNBOOK.md."""
        shared_stub.get_repo_files.return_value = {"main.py": "print('hello')"}
        shared_stub.call_claude.return_value = "# Doc content"

        result = tool.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool, shared_stub):
        """One Claude call per document type."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        assert shared_stub.call_claude.call_count == 3

    def test_get_repo_files_called_twice(self, tool, shared_stub):
        """File fetching: once for source files, once for IaC files."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        assert shared_stub.get_repo_files.call_count == 2

    def test_source_files_extensions(self, tool, shared_stub):
        """Source-file fetch requests .py/.js/.ts/.go extensions."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        first_call_extensions = shared_stub.get_repo_files.call_args_list[0][0][2]
        assert ".py" in first_call_extensions
        assert ".js" in first_call_extensions
        assert ".ts" in first_call_extensions
        assert ".go" in first_call_extensions

    def test_iac_files_extensions(self, tool, shared_stub):
        """IaC file fetch requests .tf/.bicep/.json/.yaml/.yml extensions."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        second_call_extensions = shared_stub.get_repo_files.call_args_list[1][0][2]
        assert ".tf" in second_call_extensions
        assert ".yaml" in second_call_extensions
        assert ".yml" in second_call_extensions

    def test_claude_receives_owner_repo_in_prompt(self, tool, shared_stub):
        """Each Claude call embeds owner/repo in the user prompt."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("acme", "widget-service", "https://github.com/run/1")

        for call in shared_stub.call_claude.call_args_list:
            user_prompt = call[0][1]
            assert "acme" in user_prompt
            assert "widget-service" in user_prompt

    def test_doc_values_come_from_claude(self, tool, shared_stub):
        """The content of each document is exactly what call_claude returned."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "## My special content"

        result = tool.generate_docs("org", "repo", "https://github.com/run/1")

        for doc_content in result.values():
            assert doc_content == "## My special content"

    def test_empty_files_produces_no_files_found_placeholder(self, tool, shared_stub):
        """When no files are found the formatted string contains the placeholder."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        # Inspect the README call – its prompt should contain the placeholder
        readme_call = shared_stub.call_claude.call_args_list[0]
        assert "_No files found_" in readme_call[0][1]

    def test_file_content_truncated_to_4000_chars(self, tool, shared_stub):
        """Files longer than 4000 characters are truncated in the prompt."""
        long_content = "x" * 10_000
        shared_stub.get_repo_files.return_value = {"bigfile.py": long_content}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        readme_call = shared_stub.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        # The prompt must NOT contain the full 10 000 chars
        assert "x" * 4001 not in prompt
        # But should contain up to 4000
        assert "x" * 4000 in prompt

    def test_get_repo_files_max_files_respected(self, tool, shared_stub):
        """max_files kwargs are passed correctly."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        calls = shared_stub.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15  # source files
        assert calls[1][1].get("max_files") == 10  # iac files

    def test_call_claude_raises_propagates(self, tool, shared_stub):
        """If Claude raises, generate_docs propagates the exception."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            tool.generate_docs("org", "repo", "https://github.com/run/1")

    def test_get_repo_files_raises_propagates(self, tool, shared_stub):
        """If file fetching raises, generate_docs propagates the exception."""
        shared_stub.get_repo_files.side_effect = ConnectionError("network error")

        with pytest.raises(ConnectionError, match="network error"):
            tool.generate_docs("org", "repo", "https://github.com/run/1")

    def test_multiple_files_all_included_in_prompt(self, tool, shared_stub):
        """Multiple fetched files all appear in the Claude prompt."""
        shared_stub.get_repo_files.return_value = {
            "app.py": "def main(): pass",
            "utils.py": "def helper(): pass",
        }
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        readme_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        assert "app.py" in readme_prompt
        assert "utils.py" in readme_prompt

    def test_correct_system_prompt_for_readme(self, tool, shared_stub):
        """README generation uses SYSTEM_README as the system prompt."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        system_prompt = shared_stub.call_claude.call_args_list[0][0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_correct_system_prompt_for_architecture(self, tool, shared_stub):
        """Architecture generation uses SYSTEM_ARCH as the system prompt."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        system_prompt = shared_stub.call_claude.call_args_list[1][0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_correct_system_prompt_for_runbook(self, tool, shared_stub):
        """Runbook generation uses SYSTEM_RUNBOOK as the system prompt."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://github.com/run/1")

        system_prompt = shared_stub.call_claude.call_args_list[2][0][0]
        assert "runbook" in system_prompt.lower() or "devops" in system_prompt.lower()


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, tool):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content"}
        result = tool.build_index("org", "repo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_title_contains_owner_and_repo(self, tool):
        result = tool.build_index("acme", "widget", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "widget" in result

    def test_generated_timestamp_appears(self, tool):
        result = tool.build_index("org", "repo", {"README.md": ""}, "2024-06-30 12:00 UTC")
        assert "2024-06-30 12:00 UTC" in result

    def test_all_doc_names_appear_as_links(self, tool):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, tool):
        docs = {"README.md": ""}
        result = tool.build_index("src-org", "src-repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_correct_path_structure(self, tool):
        docs = {"README.md": ""}
        result = tool.build_index("my-org", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/my-org-my-repo/README.md" in result

    def test_auto_generated_footer_present(self, tool):
        result = tool.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result or "auto-generated" in result.lower()

    def test_empty_docs_dict(self, tool):
        """build_index should not raise when docs is empty."""
        result = tool.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)

    def test_single_doc(self, tool):
        result = tool.build_index("org", "repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "README.md"