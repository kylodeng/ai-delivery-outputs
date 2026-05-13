"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls
- build_index(): constructs the markdown index page correctly
- __main__ block: happy path and failure path for the CLI entry point

Mocks used:
- shared.call_claude         — avoids real Anthropic API calls
- shared.get_repo_files      — avoids real GitHub API calls
- shared.write_output_file   — avoids real GitHub write operations
- shared.send_email          — avoids real email delivery
- shared.email_html          — pure helper, but mocked to isolate unit
- shared.write_audit_entry   — avoids real audit log writes
- shared.OUTPUT_REPO_OWNER   — constant patched for determinism
- shared.OUTPUT_REPO         — constant patched for determinism
- datetime.datetime.utcnow   — frozen for deterministic timestamp assertions

TODOs:
- TODO: Integration test that wires a real (sandboxed) GitHub token through
        get_repo_files → generate_docs end-to-end.
- TODO: Test behaviour when call_claude raises a rate-limit / 429 error
        (requires knowledge of retry/back-off logic in shared.call_claude).
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we can import tool2_tech_docs
# without the real dependency present.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-delivery-org"
FAKE_OUTPUT_REPO = "ai-delivery-output"


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
    """
    Inject a fresh fake `shared` module before every test and remove it
    afterwards so module-level state never leaks between tests.
    """
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)
    # Also make sure tool2_tech_docs is re-imported fresh each time so that
    # its module-level `from shared import …` picks up our fake.
    sys.modules.pop("tool2_tech_docs", None)
    yield mod
    sys.modules.pop("tool2_tech_docs", None)


def _import_tool():
    """Import tool2_tech_docs after shared has been patched."""
    import importlib
    return importlib.import_module("tool2_tech_docs")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/app.py": "def main(): pass",
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}',
    "infra/vars.yaml": "env: production\n",
}


# ---------------------------------------------------------------------------
# Tests for build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_contains_repo_name(self):
        tool = _import_tool()
        result = tool.build_index("myorg", "myrepo", {"README.md": "x"}, "2024-01-01 12:00 UTC")
        assert "myorg/myrepo" in result

    def test_contains_generated_timestamp(self):
        tool = _import_tool()
        result = tool.build_index("myorg", "myrepo", {"README.md": "x"}, "2024-06-15 09:30 UTC")
        assert "2024-06-15 09:30 UTC" in result

    def test_contains_link_for_each_doc(self):
        tool = _import_tool()
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_point_to_output_repo(self):
        tool = _import_tool()
        docs = {"README.md": ""}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_include_owner_and_repo_in_path(self):
        tool = _import_tool()
        docs = {"README.md": ""}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/myorg-myrepo/README.md" in result

    def test_auto_generated_footer_present(self):
        tool = _import_tool()
        result = tool.build_index("myorg", "myrepo", {}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_empty_docs_produces_no_links(self):
        tool = _import_tool()
        result = tool.build_index("myorg", "myrepo", {}, "2024-01-01 00:00 UTC")
        # No bullet links expected
        assert "- [" not in result

    def test_returns_string(self):
        tool = _import_tool()
        result = tool.build_index("o", "r", {"README.md": ""}, "now")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests for generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:
    def test_returns_three_docs(self, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        tool = _import_tool()
        docs = tool.generate_docs("myorg", "myrepo", "https://run.url")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_for_code_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        calls_args = [c[0][2] for c in fake_shared.get_repo_files.call_args_list]
        # First call should request source-code extensions
        assert ".py" in calls_args[0]

    def test_calls_get_repo_files_for_iac_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        calls_args = [c[0][2] for c in fake_shared.get_repo_files.call_args_list]
        # Second call should request IaC extensions
        assert ".tf" in calls_args[1]

    def test_call_claude_called_three_times(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# README"
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        first_call_system = fake_shared.call_claude.call_args_list[0][0][0]
        assert "technical writer" in first_call_system.lower()

    def test_architecture_doc_uses_arch_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# ARCH"
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        second_call_system = fake_shared.call_claude.call_args_list[1][0][0]
        assert "architect" in second_call_system.lower()

    def test_runbook_uses_runbook_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# RUNBOOK"
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        third_call_system = fake_shared.call_claude.call_args_list[2][0][0]
        assert "devops" in third_call_system.lower() or "runbook" in third_call_system.lower()

    def test_repo_name_included_in_claude_user_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "myorg/myrepo" in user_prompt

    def test_docs_values_are_claude_responses(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        tool = _import_tool()
        docs = tool.generate_docs("myorg", "myrepo", "https://run.url")
        assert docs["README.md"] == "README content"
        assert docs["ARCHITECTURE.md"] == "ARCH content"
        assert docs["RUNBOOK.md"] == "RUNBOOK content"

    def test_no_files_found_uses_placeholder(self, fake_shared):
        """When get_repo_files returns empty dict, fmt() should yield placeholder."""
        fake_shared.get_repo_files.return_value = {}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        # The user prompt sent to Claude should contain the placeholder
        first_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in first_user_prompt

    def test_file_content_truncated_to_4000_chars(self, fake_shared):
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        first_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The truncated content must appear (4000 x's), not the full 10000
        assert "x" * 4000 in first_user_prompt
        assert "x" * 4001 not in first_user_prompt

    def test_max_files_limit_passed_for_code_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        code_call_kwargs = fake_shared.get_repo_files.call_args_list[0][1]
        assert code_call_kwargs.get("max_files") == 15

    def test_max_files_limit_passed_for_iac_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        iac_call_kwargs = fake_shared.get_repo_files.call_args_list[1][1]
        assert iac_call_kwargs.get("max_files") == 10

    def test_get_repo_files_error_propagates(self, fake_shared):
        fake_shared.get_repo_files.side_effect = RuntimeError("GitHub API error")
        tool = _import_tool()
        with pytest.raises(RuntimeError, match="GitHub API error"):
            tool.generate_docs("myorg", "myrepo", "https://run.url")

    def test_call_claude_error_propagates(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ConnectionError("Anthropic unavailable")
        tool = _import_tool()
        with pytest.raises(ConnectionError, match="Anthropic unavailable"):
            tool.generate_docs("myorg", "myrepo", "https://run.url")

    def test_multiple_files_all_included_in_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        tool = _import_tool()
        tool.generate_docs("myorg", "myrepo", "https://run.url")
        first_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "backend/app.py" in first_user_prompt
        assert "backend/model_card.json" in first_user_prompt


# ---------------------------------------------------------------------------
# Tests for the fmt() inner function (via generate_docs observable behaviour)
# ---------------------------------------------------------------------------

class TestFmtHelper:
    def test_each_file_appears_as_fenced_code_block(self, fake_shared):
        fake_shared.get_repo_files.return_value = {"src/main.py": "print('hello')"}
        tool = _import_tool()
        tool.generate_docs("o", "r", "url")
        prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "```" in prompt
        assert "print('hello')" in prompt

    def test_file_path_is_header_in_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {"src/main.py": "pass"}
        tool = _import_tool()
        tool.generate_docs("o", "r", "url")
        prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "### src/main.py" in prompt


# ---------------------------------------------------------------------------
# Tests for __main__ block (happy path)
# ---------------------------------------------------------------------------

class TestMainBlockHappyPath:
    def _run_main(self, fake_shared, env_vars=None):
        """
        Execute the __main__ block by importing the module with __name__
        forced to '__main__' via runpy.
        """
        import runpy
        env = {
            "SOURCE_REPO_OWNER": "testowner",
            "SOURCE_REPO_NAME": "te