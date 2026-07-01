"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
- build_index(): builds a markdown index page with correct links and metadata
- __main__ block behaviour: success path (writes files, sends email, writes audit) and failure path

Mocks used:
- shared.call_claude          — avoids real Anthropic API calls
- shared.get_repo_files       — avoids real GitHub API calls
- shared.write_output_file    — avoids real GitHub commits
- shared.send_email           — avoids real SMTP/SES calls
- shared.email_html           — pure helper, still mocked to isolate unit
- shared.write_audit_entry    — avoids real audit-log writes
- shared.OUTPUT_REPO_OWNER    — patched to a stable test value
- shared.OUTPUT_REPO          — patched to a stable test value
- datetime.datetime           — patched in the main-block test to get deterministic timestamps

TODOs:
- TODO: test actual content quality/structure of generated prompts sent to Claude
        (needs inspection of call_claude call args in more detail)
- TODO: integration test against a real GitHub repo (requires GitHub token + test repo)
- TODO: test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are missing (None values)
"""

import importlib
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers to import the module under test with its `shared` dependency mocked
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_mock():
    """Return a mock module that stands in for `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/some/path")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_tool(shared_mock=None):
    """Import (or re-import) tool2_tech_docs with an optional shared mock."""
    # Remove cached copies so we can inject a fresh mock each time
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]

    mock = shared_mock or _make_shared_mock()
    sys.modules["shared"] = mock

    # The module does sys.path.insert at import time; we need to allow that
    import tool2_tech_docs as mod  # noqa: PLC0415  (import not at top level, intentional)
    return mod, mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def tool(shared_mock):
    mod, _ = _import_tool(shared_mock)
    return mod, shared_mock


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_docs_on_happy_path(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {"src/main.py": "print('hello')"}
        smock.call_claude.return_value = "# Doc content"

        result = mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert smock.call_claude.call_count == 3

    def test_get_repo_files_called_twice_with_correct_extensions(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        calls = smock.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call: source file extensions
        first_exts = calls[0][0][2]  # positional arg[2]
        assert ".py" in first_exts
        assert ".js" in first_exts
        assert ".ts" in first_exts
        assert ".go" in first_exts

        # Second call: IaC extensions
        second_exts = calls[1][0][2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts
        assert ".yml" in second_exts

    def test_get_repo_files_respects_max_files_limits(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        calls = smock.get_repo_files.call_args_list
        # max_files kwarg
        assert calls[0][1]["max_files"] == 15
        assert calls[1][1]["max_files"] == 10

    def test_readme_doc_contains_call_claude_return_value(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        result = mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_readme_prompt_contains_owner_and_repo(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("myowner", "myrepo", "https://github.com/run/1")

        readme_user_prompt = smock.call_claude.call_args_list[0][0][1]
        assert "myowner/myrepo" in readme_user_prompt

    def test_arch_prompt_contains_owner_and_repo(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("myowner", "myrepo", "https://github.com/run/1")

        arch_user_prompt = smock.call_claude.call_args_list[1][0][1]
        assert "myowner/myrepo" in arch_user_prompt

    def test_runbook_prompt_contains_owner_and_repo(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("myowner", "myrepo", "https://github.com/run/1")

        runbook_user_prompt = smock.call_claude.call_args_list[2][0][1]
        assert "myowner/myrepo" in runbook_user_prompt

    def test_files_included_in_prompts(self, tool):
        mod, smock = tool
        smock.get_repo_files.side_effect = [
            {"src/app.py": "x = 1"},   # source files
            {"main.tf": "resource {}"},  # iac files
        ]
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        readme_prompt = smock.call_claude.call_args_list[0][0][1]
        assert "src/app.py" in readme_prompt
        assert "main.tf" in readme_prompt

    def test_empty_files_returns_no_files_found_placeholder(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        readme_prompt = smock.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_at_4000_chars(self, tool):
        mod, smock = tool
        long_content = "A" * 5000
        smock.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        readme_prompt = smock.call_claude.call_args_list[0][0][1]
        # The truncated version should be present (4000 A's), not 5000
        assert "A" * 4000 in readme_prompt
        assert "A" * 4001 not in readme_prompt

    def test_call_claude_receives_correct_system_prompt_for_readme(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        system_prompt = smock.call_claude.call_args_list[0][0][0]
        assert "README.md" in system_prompt or "technical writer" in system_prompt.lower()

    def test_call_claude_receives_correct_system_prompt_for_arch(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        system_prompt = smock.call_claude.call_args_list[1][0][0]
        assert "architect" in system_prompt.lower()

    def test_call_claude_receives_correct_system_prompt_for_runbook(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "content"

        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        system_prompt = smock.call_claude.call_args_list[2][0][0]
        assert "devops" in system_prompt.lower() or "runbook" in system_prompt.lower()

    def test_call_claude_exception_propagates(self, tool):
        mod, smock = tool
        smock.get_repo_files.return_value = {}
        smock.call_claude.side_effect = RuntimeError("Claude is down")

        with pytest.raises(RuntimeError, match="Claude is down"):
            mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

    def test_get_repo_files_exception_propagates(self, tool):
        mod, smock = tool
        smock.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

    @pytest.mark.parametrize("owner,repo", [
        ("sun-life", "insurance-portal"),
        ("acme-corp", "backend-api"),
        ("org123", "repo-with-numbers-456"),
    ])
    def test_various_owner_repo_combinations(self, owner, repo):
        mod, smock = _import_tool()
        smock.get_repo_files.return_value = {}
        smock.call_claude.return_value = "# content"

        result = mod.generate_docs(owner, repo, "https://github.com/run/1")

        assert "README.md" in result
        for c in smock.call_claude.call_args_list:
            assert f"{owner}/{repo}" in c[0][1]


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, tool):
        mod, smock = tool
        docs = {"README.md": "content", "ARCHITECTURE.md": "content2"}
        result = mod.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo_in_header(self, tool):
        mod, smock = tool
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "acme/my-repo" in result

    def test_contains_timestamp(self, tool):
        mod, smock = tool
        docs = {"README.md": "content"}
        now = "2024-06-30 12:34 UTC"
        result = mod.build_index("acme", "my-repo", docs, now)
        assert now in result

    def test_links_use_output_repo_owner_and_repo(self, tool):
        mod, smock = tool
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_all_doc_filenames(self, tool):
        mod, smock = tool
        docs = {
            "README.md": "r",
            "ARCHITECTURE.md": "a",
            "RUNBOOK.md": "rb",
        }
        result = mod.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_format_is_correct_github_url(self, tool):
        mod, smock = tool
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        expected_url = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/acme-my-repo/README.md"
        )
        assert expected_url in result

    def test_link_