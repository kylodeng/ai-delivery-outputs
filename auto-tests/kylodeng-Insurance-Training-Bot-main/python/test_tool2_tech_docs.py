"""
Tests for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates fetching repo files and calling Claude for README, ARCHITECTURE, RUNBOOK
- build_index(): constructs a markdown index page with links to generated docs
- __main__ block behaviour: happy path (writes docs, sends email, writes audit) and failure path (error handling)

Mocks used:
- shared.call_claude          — stubbed to return predictable strings
- shared.get_repo_files       — stubbed to return dict of {path: content}
- shared.write_output_file    — stubbed to return a fake URL
- shared.send_email           — stubbed (no-op)
- shared.email_html           — stubbed to return an HTML string
- shared.write_audit_entry    — stubbed (no-op)
- shared.OUTPUT_REPO_OWNER    — patched to "test-org"
- shared.OUTPUT_REPO          — patched to "test-output-repo"
- datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
- TODO: Integration test that exercises real `shared` helpers against a live GitHub API token
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are missing (None values)
- TODO: Test partial failure — e.g. write_output_file succeeds for some docs but not all
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so we never import the real
# one (which may depend on secrets / network).
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a fresh fake `shared` module each time."""
    mod = types.ModuleType("shared")
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    mod.call_claude = MagicMock(return_value="# Generated doc")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/some/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email body</html>")
    mod.write_audit_entry = MagicMock()
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject fake `shared` into sys.modules before every test so that
    `tool2_tech_docs` (re-)imported inside a test always uses the stub.
    """
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)
    # Remove any previously imported tool2_tech_docs so it re-imports fresh
    monkeypatch.delitem(sys.modules, "tool2_tech_docs", raising=False)
    return mod


def _import_tool():
    """Import tool2_tech_docs after fake_shared is installed."""
    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try relative to this file
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, ".github", "scripts"),
        os.path.join(here, "..", ".github", "scripts"),
        os.path.join(here, "..", "..", ".github", "scripts"),
        here,
    ]
    for c in candidates:
        c = os.path.normpath(c)
        if c not in sys.path:
            sys.path.insert(0, c)

    return importlib.import_module("tool2_tech_docs")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "src/main.py": "def hello(): return 'world'",
    "src/utils.py": "import os\ndef get_env(k): return os.environ[k]",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "infra/variables.yaml": "region: us-east-1",
}


@pytest.fixture()
def tool(fake_shared):
    """Import the module under test with mocks in place."""
    return _import_tool()


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_returns_string(self, tool):
        result = tool.build_index("acme", "myrepo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, tool):
        result = tool.build_index("acme", "myrepo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "acme/myrepo" in result

    def test_contains_generated_timestamp(self, tool):
        now = "2024-06-01 09:30 UTC"
        result = tool.build_index("acme", "myrepo", {"README.md": "x"}, now)
        assert now in result

    def test_contains_links_for_all_docs(self, tool):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = tool.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_link_format_uses_output_repo_constants(self, tool):
        docs = {"README.md": "r"}
        result = tool.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_contains_correct_path(self, tool):
        docs = {"README.md": "r"}
        result = tool.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/acme-myrepo/README.md" in result

    def test_empty_docs_produces_no_links(self, tool):
        result = tool.build_index("acme", "myrepo", {}, "2024-01-01 00:00 UTC")
        # Should still return a string with the header
        assert "Tech Documentation Index" in result
        assert "README.md" not in result

    def test_contains_auto_generated_footer(self, tool):
        result = tool.build_index("acme", "myrepo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_multiple_docs_each_appear_once(self, tool):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a"}
        result = tool.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert result.count("README.md") >= 1
        assert result.count("ARCHITECTURE.md") >= 1

    def test_owner_and_repo_with_special_chars(self, tool):
        """Hyphens/underscores in owner/repo names must be preserved."""
        result = tool.build_index("my-org", "my_repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "my-org/my_repo" in result
        assert "my-org-my_repo" in result


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_returns_dict_with_three_keys(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        result = tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        assert fake_shared.call_claude.call_count == 3

    def test_get_repo_files_called_twice(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        assert fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_py_extensions(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions

    def test_get_repo_files_iac_extensions(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions or ".yaml" in extensions

    def test_readme_content_comes_from_call_claude(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        assert result["README.md"] == "README content"

    def test_architecture_content_comes_from_call_claude(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        assert result["ARCHITECTURE.md"] == "ARCH content"

    def test_runbook_content_comes_from_call_claude(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_owner_and_repo_appear_in_claude_prompts(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("my-org", "my-repo", "https://example.com/run/1")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg is user prompt
            assert "my-org/my-repo" in user_prompt

    def test_file_content_truncated_at_4000_chars(self, tool, fake_shared):
        long_content = "x" * 8000
        fake_shared.get_repo_files.return_value = {"src/big.py": long_content}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        # The prompt should contain at most 4000 chars of the file content
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The truncated version (4000 x's) should appear, not the full 8000
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_empty_files_shows_no_files_found(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_call_claude_propagates_exception(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API down")
        with pytest.raises(RuntimeError, match="Claude API down"):
            tool.generate_docs("acme", "myrepo", "https://example.com/run/1")

    def test_get_repo_files_propagates_exception(self, tool, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool.generate_docs("acme", "myrepo", "https://example.com/run/1")

    def test_max_files_limit_passed_for_source_files(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        first_call_kwargs = fake_shared.get_repo_files.call_args_list[0][1]
        assert first_call_kwargs.get("max_files") == 15

    def test_max_files_limit_passed_for_iac_files(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        second_call_kwargs = fake_shared.get_repo_files.call_args_list[1][1]
        assert second_call_kwargs.get("max_files") == 10

    def test_py_and_iac_files_merged_for_readme_prompt(self, tool, fake_shared):
        def _side_effect(owner, repo, exts, max_files=10):
            if ".py" in exts:
                return {"src/main.py": "print('hello')"}
            return {"infra/main.tf": "resource {}"}

        fake_shared.get_repo_files.side_effect = _side_effect
        tool.generate_docs("acme", "myrepo", "https://example.com/run/1")
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "src/main.py" in readme_prompt
        assert "infra/main.tf" in readme_prompt

    def test_arch_prompt_contains_iac_files(self, tool, fake_shared):
        def _side_effect(owner, repo, exts, max_files=10):
            if