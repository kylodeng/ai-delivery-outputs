"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty files, partial files, Claude call arguments
- build_index(): correct markdown structure, link generation, timestamp inclusion,
  multiple docs, empty docs dict
- __main__ block: success path, exception/failure path, environment variable handling
- fmt() inner function behaviour (via generate_docs)
- write_output_file, send_email, email_html, write_audit_entry integration points

Mocks used:
- shared.call_claude          — prevents real Anthropic API calls
- shared.get_repo_files       — prevents real GitHub API calls
- shared.write_output_file    — prevents real GitHub file writes
- shared.send_email           — prevents real email sending
- shared.email_html           — prevents real template rendering
- shared.write_audit_entry    — prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    — patched to deterministic value
- shared.OUTPUT_REPO          — patched to deterministic value
- datetime.datetime           — frozen for deterministic timestamps

TODOs:
- TODO: test behaviour when call_claude returns non-string / None (needs shared.py context)
- TODO: test concurrent / race-condition behaviour in __main__ if parallelism is added
- TODO: integration test against a real GitHub sandbox repo
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared replaced by a mock
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot"
FAKE_OUTPUT_REPO = "docs-output"


def _make_shared_mock():
    """Return a minimal mock of the `shared` module."""
    m = MagicMock()
    m.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    m.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return m


def _import_tool(shared_mock):
    """
    Insert shared_mock into sys.modules and (re)import tool2_tech_docs so that
    every test starts from a clean module state.
    """
    sys.modules["shared"] = shared_mock
    # Remove cached version so importlib gives us a fresh module
    sys.modules.pop("tool2_tech_docs", None)

    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also support running from repo root where the file lives alongside tests
    import importlib.util
    candidates = [
        os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py"),
        os.path.join(os.path.dirname(__file__), "tool2_tech_docs.py"),
        os.path.join(script_dir, "tool2_tech_docs.py"),
    ]
    spec = None
    for c in candidates:
        if os.path.exists(c):
            spec = importlib.util.spec_from_file_location("tool2_tech_docs", c)
            break

    if spec is None:
        pytest.skip("tool2_tech_docs.py not found — adjust path in conftest or test file")

    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    m = _make_shared_mock()
    yield m
    # cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool(shared_mock):
    return _import_tool(shared_mock)


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, tool):
        result = tool.build_index("owner", "repo", {"README.md": "x"}, "2024-01-01 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_repo_heading(self, tool):
        result = tool.build_index("myorg", "myrepo", {"README.md": "x"}, "2024-01-01 12:00 UTC")
        assert "myorg/myrepo" in result

    def test_contains_generated_timestamp(self, tool):
        now = "2024-06-15 09:30 UTC"
        result = tool.build_index("o", "r", {"README.md": "x"}, now)
        assert now in result

    def test_links_all_docs(self, tool):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_uses_output_repo(self, tool):
        docs = {"README.md": "content"}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        expected_fragment = f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
        assert expected_fragment in result

    def test_link_contains_correct_path(self, tool):
        docs = {"README.md": "content"}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/myorg-myrepo/README.md" in result

    def test_empty_docs_dict(self, tool):
        result = tool.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        assert "org/repo" in result
        # No list items expected
        assert "- [" not in result

    def test_contains_auto_generated_footer(self, tool):
        result = tool.build_index("org", "repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_multiple_docs_produce_multiple_links(self, tool):
        docs = {"A.md": "a", "B.md": "b"}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert result.count("- [") == 2

    def test_owner_repo_with_special_chars_in_path(self, tool):
        """Hyphenated owner/repo names must be joined with a hyphen in the path."""
        docs = {"README.md": "x"}
        result = tool.build_index("my-org", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/my-org-my-repo/README.md" in result


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "main.py": "print('hello')",
    "utils.py": "def helper(): pass",
}
SAMPLE_IAC_FILES = {
    "main.tf": 'resource "aws_s3_bucket" "b" {}',
}
SAMPLE_CLAUDE_RESPONSES = {
    "README": "# README\nContent here",
    "ARCH": "# Architecture\nDetails",
    "RUNBOOK": "# Runbook\nOps details",
}


class TestGenerateDocs:

    def _setup_mocks(self, shared_mock, py_files=None, iac_files=None, claude_responses=None):
        py_files = py_files if py_files is not None else SAMPLE_PY_FILES
        iac_files = iac_files if iac_files is not None else SAMPLE_IAC_FILES
        responses = claude_responses or [
            SAMPLE_CLAUDE_RESPONSES["README"],
            SAMPLE_CLAUDE_RESPONSES["ARCH"],
            SAMPLE_CLAUDE_RESPONSES["RUNBOOK"],
        ]

        shared_mock.get_repo_files.side_effect = [py_files, iac_files]
        shared_mock.call_claude.side_effect = responses

    def test_returns_dict_with_three_keys(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_comes_from_claude(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert docs["README.md"] == SAMPLE_CLAUDE_RESPONSES["README"]

    def test_architecture_content_comes_from_claude(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert docs["ARCHITECTURE.md"] == SAMPLE_CLAUDE_RESPONSES["ARCH"]

    def test_runbook_content_comes_from_claude(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert docs["RUNBOOK.md"] == SAMPLE_CLAUDE_RESPONSES["RUNBOOK"]

    def test_get_repo_files_called_twice(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert shared_mock.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_py_extensions(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        first_call = shared_mock.get_repo_files.call_args_list[0]
        extensions = first_call[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".go" in extensions

    def test_get_repo_files_second_call_iac_extensions(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        second_call = shared_mock.get_repo_files.call_args_list[1]
        extensions = second_call[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions

    def test_call_claude_called_three_times(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert shared_mock.call_claude.call_count == 3

    def test_readme_prompt_contains_owner_repo(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "myorg/myrepo" in user_prompt

    def test_architecture_prompt_contains_iac_files(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        arch_call = shared_mock.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        # The IaC file content or path should appear
        assert "main.tf" in user_prompt or "aws_s3_bucket" in user_prompt

    def test_runbook_prompt_contains_owner_repo(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        runbook_call = shared_mock.call_claude.call_args_list[2]
        user_prompt = runbook_call[0][1]
        assert "myorg/myrepo" in user_prompt

    def test_empty_py_files_uses_no_files_found_placeholder(self, tool, shared_mock):
        self._setup_mocks(shared_mock, py_files={}, iac_files={})
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_empty_iac_files_uses_no_files_found_placeholder(self, tool, shared_mock):
        self._setup_mocks(shared_mock, py_files={}, iac_files={})
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        arch_call = shared_mock.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, tool, shared_mock):
        long_content = "x" * 10000
        shared_mock.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        shared_mock.call_claude.side_effect = ["r", "a", "rb"]
        tool.generate_docs("org", "repo", "https://github.com/run/1")
        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The truncated content should appear, not the full 10000 chars
        assert "x" * 4000 in user_prompt
        assert "x" * 4001 not in user_prompt

    def test_correct_system_prompt_used_for_readme(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("org", "repo", "https://github.com/run/1")
        readme_call = shared_mock.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_correct_system_prompt_used_for_architecture(self, tool, shared_mock):
        self._setup_mocks(shared_mock)
        tool.generate_docs("org", "repo", "https://github.com/run/1")
        arch_call = shared_mock.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_correct_system_prompt_used_for_runbook(self, tool, shared_mock):
        self._setup_mocks(shared