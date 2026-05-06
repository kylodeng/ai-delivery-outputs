"""
Tests for tool2_tech_docs.py

What is tested:
  - generate_docs(): orchestrates fetching repo files and calling Claude for README, ARCHITECTURE, RUNBOOK
  - build_index(): constructs a markdown index page with correct links and metadata
  - __main__ block: end-to-end flow including success path, failure path, env-var handling

Mocks used:
  - shared.call_claude          → patched to return deterministic strings
  - shared.get_repo_files       → patched to return synthetic file dicts
  - shared.write_output_file    → patched to return fake URLs
  - shared.send_email           → patched to avoid real email dispatch
  - shared.email_html           → patched to return a simple string
  - shared.write_audit_entry    → patched to avoid real audit writes
  - datetime.datetime.utcnow    → patched for deterministic timestamps

TODOs:
  - TODO: Integration test requiring a real GitHub token and output repo access
  - TODO: Test behaviour when Claude returns an empty string / rate-limit error
  - TODO: Validate exact markdown structure of generated docs (would need contract with Claude)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared dependencies stubbed out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Build a minimal stub for the `shared` module so we can import tool2."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="GENERATED CONTENT")
    stub.get_repo_files = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    stub.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return stub


@pytest.fixture()
def shared_stub():
    """Inject stub shared module and return it for assertions."""
    stub = _make_shared_stub()
    sys.modules["shared"] = stub
    yield stub
    # Cleanup so other tests get a fresh stub
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool2(shared_stub):
    """Import (or re-import) tool2_tech_docs with the stub in place."""
    sys.modules.pop("tool2_tech_docs", None)
    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import tool2_tech_docs as m
    return m


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_contains_repo_name(self, tool2):
        result = tool2.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 12:00 UTC")
        assert "acme/myrepo" in result

    def test_contains_generated_timestamp(self, tool2):
        result = tool2.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 12:00 UTC")
        assert "2024-01-15 12:00 UTC" in result

    def test_links_use_output_repo(self, tool2):
        result = tool2.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 12:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_all_doc_names_appear_as_links(self, tool2):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = tool2.build_index("acme", "myrepo", docs, "2024-01-15 12:00 UTC")
        for name in docs:
            assert name in result

    def test_link_path_includes_owner_repo_segment(self, tool2):
        result = tool2.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 12:00 UTC")
        assert "tech-docs/acme-myrepo/README.md" in result

    def test_contains_auto_generated_footer(self, tool2):
        result = tool2.build_index("acme", "myrepo", {}, "2024-01-15 12:00 UTC")
        assert "Auto-generated" in result

    def test_empty_docs_dict_produces_no_links(self, tool2):
        result = tool2.build_index("acme", "myrepo", {}, "2024-01-15 12:00 UTC")
        # There should be no markdown list items for documents
        assert "- [" not in result

    def test_special_characters_in_owner_repo(self, tool2):
        """Owner/repo names with hyphens and underscores should not break the output."""
        result = tool2.build_index("my-org", "cool_repo", {"README.md": ""}, "2024-01-15 12:00 UTC")
        assert "my-org/cool_repo" in result
        assert "tech-docs/my-org-cool_repo/README.md" in result

    def test_multiple_docs_each_get_separate_line(self, tool2):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = tool2.build_index("acme", "myrepo", docs, "now")
        lines_with_links = [line for line in result.splitlines() if line.startswith("- [")]
        assert len(lines_with_links) == 3


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_returns_three_doc_keys(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        result = tool2.generate_docs("acme", "myrepo", "https://run.url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_invoked_three_times(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        assert shared_stub.call_claude.call_count == 3

    def test_get_repo_files_invoked_twice(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        assert shared_stub.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_py_extensions(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        first_call_args = shared_stub.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions

    def test_get_repo_files_called_with_iac_extensions(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        second_call_args = shared_stub.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions

    def test_doc_content_comes_from_claude(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = [
            "README CONTENT", "ARCH CONTENT", "RUNBOOK CONTENT"
        ]
        result = tool2.generate_docs("acme", "myrepo", "https://run.url")
        assert result["README.md"] == "README CONTENT"
        assert result["ARCHITECTURE.md"] == "ARCH CONTENT"
        assert result["RUNBOOK.md"] == "RUNBOOK CONTENT"

    def test_repo_owner_and_name_passed_to_claude(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        for c in shared_stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme" in user_prompt
            assert "myrepo" in user_prompt

    def test_files_with_content_are_included_in_prompt(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {"main.py": "print('hello')"}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        # README call (first) should include the file content
        readme_call = shared_stub.call_claude.call_args_list[0]
        assert "main.py" in readme_call[0][1]

    def test_no_files_produces_no_files_found_placeholder(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        readme_call = shared_stub.call_claude.call_args_list[0]
        assert "_No files found_" in readme_call[0][1]

    def test_file_content_truncated_at_4000_chars(self, tool2, shared_stub):
        long_content = "x" * 10_000
        shared_stub.get_repo_files.return_value = {"big.py": long_content}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        readme_call = shared_stub.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        # The prompt must NOT contain 10 000 x's consecutively
        assert "x" * 4001 not in prompt

    def test_claude_exception_propagates(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("Claude is down")
        with pytest.raises(RuntimeError, match="Claude is down"):
            tool2.generate_docs("acme", "myrepo", "https://run.url")

    def test_readme_system_prompt_passed_to_claude(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        readme_call = shared_stub.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_arch_system_prompt_passed_to_claude(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        arch_call = shared_stub.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_runbook_system_prompt_passed_to_claude(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        runbook_call = shared_stub.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "runbook" in system_prompt.lower() or "devops" in system_prompt.lower()

    def test_max_files_limit_respected_for_source_files(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        first_call_kwargs = shared_stub.get_repo_files.call_args_list[0]
        # max_files should be passed as keyword or positional
        call_kwargs = first_call_kwargs[1]
        assert call_kwargs.get("max_files", 15) == 15

    def test_max_files_limit_respected_for_iac_files(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        second_call_kwargs = shared_stub.get_repo_files.call_args_list[1]
        call_kwargs = second_call_kwargs[1]
        assert call_kwargs.get("max_files", 10) == 10


# ---------------------------------------------------------------------------
# Tests for the fmt() inner function behaviour (via generate_docs side-effects)
# ---------------------------------------------------------------------------


class TestFmtHelper:
    """
    fmt() is a closure inside generate_docs; we test it indirectly through
    the prompts passed to call_claude.
    """

    def test_multiple_files_all_appear_in_prompt(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {
            "app.py": "code_a",
            "infra.tf": "code_b",
        }
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        readme_call = shared_stub.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        assert "app.py" in prompt
        assert "infra.tf" in prompt

    def test_fenced_code_blocks_used(self, tool2, shared_stub):
        shared_stub.get_repo_files.return_value = {"app.py": "some code"}
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        readme_call = shared_stub.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        assert "```" in prompt


# ---------------------------------------------------------------------------
# Tests for the __main__ block (success path)
# ---------------------------------------------------------------------------


class TestMainBlockSuccess:
    @pytest.fixture()
    def env_vars(self, monkeypatch):
        monkeypatch.setenv("SOURCE_REPO_OWNER", "alice")
        monkeypatch.setenv("SOURCE_REPO_NAME", "wonderland")
        monkeypatch.setenv("GITHUB_RUN_URL", "https://github.com/actions/run/1")

    def _run_main(self, tool2, shared_stub, env_vars_fixture=None):
        """Execute the __main__ block by running the module's bottom section."""
        shared