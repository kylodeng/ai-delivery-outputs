"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce docs dict
- build_index(): builds a markdown index page from docs dict
- __main__ block behaviour (happy path and failure path)
- fmt() inner function behaviour (via generate_docs side effects)

Mocks used:
- shared.call_claude          — stubbed to return predictable markdown strings
- shared.get_repo_files       — stubbed to return predictable file dicts
- shared.write_output_file    — stubbed to return fake GitHub URLs
- shared.send_email           — stubbed (fire-and-forget, return value unused)
- shared.email_html           — stubbed to return a dummy HTML string
- shared.write_audit_entry    — stubbed (side-effect only)
- shared.OUTPUT_REPO_OWNER    — patched to "test-org"
- shared.OUTPUT_REPO          — patched to "test-output-repo"
- datetime.datetime.utcnow    — patched so timestamps are deterministic
- os.environ                  — patched for __main__ block tests

TODOs:
- TODO: Integration test that validates actual Claude prompt content when
        a real (or contract-test) Claude client is available.
- TODO: Test behaviour when get_repo_files returns truncated content
        (>4000 chars) to confirm slicing in fmt().
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we never import real network code
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a types.ModuleType that mimics the real `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated Doc\nContent here.")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/file.md")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


def _import_tool2(fake_shared):
    """
    Insert fake_shared into sys.modules and (re)import tool2_tech_docs,
    returning the fresh module object.
    """
    sys.modules["shared"] = fake_shared

    # Remove any previously cached version so importlib reloads cleanly
    sys.modules.pop("tool2_tech_docs", None)

    # The module lives under .github/scripts/ — make sure that directory is on
    # sys.path before importing.
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the same directory as this test file (CI puts them side-by-side)
    here = os.path.dirname(__file__)
    if here not in sys.path:
        sys.path.insert(0, here)

    import tool2_tech_docs  # noqa: PLC0415
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_shared():
    mod = _make_fake_shared()
    yield mod
    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool2(fake_shared):
    return _import_tool2(fake_shared)


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    """Tests for the build_index() pure function."""

    def test_returns_string(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_repo_info(self, tool2):
        result = tool2.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "acme/myrepo" in result

    def test_contains_timestamp(self, tool2):
        now = "2024-06-01 12:34 UTC"
        result = tool2.build_index("acme", "myrepo", {"README.md": ""}, now)
        assert now in result

    def test_contains_all_doc_links(self, tool2):
        docs = {
            "README.md": "r",
            "ARCHITECTURE.md": "a",
            "RUNBOOK.md": "ru",
        }
        result = tool2.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_link_format_uses_output_repo_constants(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        expected_fragment = f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}/blob/main/tech-docs/acme-myrepo/README.md"
        assert expected_fragment in result

    def test_empty_docs_dict(self, tool2):
        result = tool2.build_index("acme", "myrepo", {}, "2024-01-01 00:00 UTC")
        assert "acme/myrepo" in result
        # No links section should still produce a valid string
        assert isinstance(result, str)

    def test_contains_auto_generated_footer(self, tool2):
        result = tool2.build_index("acme", "myrepo", {}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_owner_and_repo_in_link_slug(self, tool2):
        docs = {"README.md": ""}
        result = tool2.build_index("owner-x", "repo-y", docs, "now")
        assert "owner-x-repo-y" in result

    def test_special_characters_in_repo_name(self, tool2):
        """Repo names can contain hyphens and dots — should not crash."""
        docs = {"README.md": ""}
        result = tool2.build_index("my-org", "my.repo", docs, "2024-01-01 00:00 UTC")
        assert "my-org" in result
        assert "my.repo" in result

    def test_multiple_docs_each_have_separate_link(self, tool2):
        docs = {"README.md": "", "ARCHITECTURE.md": ""}
        result = tool2.build_index("o", "r", docs, "t")
        # Both filenames appear as link text
        assert result.count("[README.md]") == 1
        assert result.count("[ARCHITECTURE.md]") == 1


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:
    """Tests for generate_docs() — all external calls are mocked."""

    def _setup_files(self, fake_shared, py_files=None, iac_files=None):
        """Configure get_repo_files side_effect for two sequential calls."""
        py_files = py_files or {"main.py": "print('hello')"}
        iac_files = iac_files or {"main.tf": 'resource "aws_s3_bucket" "b" {}'}
        fake_shared.get_repo_files.side_effect = [py_files, iac_files]

    def _setup_claude(self, fake_shared, responses=None):
        if responses is None:
            responses = [
                "# README\nContent.",
                "# ARCHITECTURE\nContent.",
                "# RUNBOOK\nContent.",
            ]
        fake_shared.call_claude.side_effect = responses

    def test_returns_three_doc_keys(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        self._setup_claude(fake_shared)
        docs = tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert fake_shared.get_repo_files.call_count == 2

    def test_first_get_repo_files_call_fetches_source_extensions(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".ts" in extensions

    def test_second_get_repo_files_call_fetches_iac_extensions(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions

    def test_calls_claude_three_times(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_all_files(self, tool2, fake_shared):
        py_files = {"app.py": "# app"}
        iac_files = {"infra.tf": "# tf"}
        self._setup_files(fake_shared, py_files=py_files, iac_files=iac_files)
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        # First Claude call is for README and should reference the repo
        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "acme/myrepo" in user_prompt

    def test_architecture_call_includes_iac_files(self, tool2, fake_shared):
        py_files = {"app.py": "code"}
        iac_files = {"main.tf": "terraform_resource"}
        self._setup_files(fake_shared, py_files=py_files, iac_files=iac_files)
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        arch_call = fake_shared.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "terraform_resource" in user_prompt

    def test_doc_values_are_claude_responses(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        responses = ["README content", "ARCH content", "RUNBOOK content"]
        self._setup_claude(fake_shared, responses=responses)
        docs = tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert docs["README.md"] == "README content"
        assert docs["ARCHITECTURE.md"] == "ARCH content"
        assert docs["RUNBOOK.md"] == "RUNBOOK content"

    def test_empty_repo_files_uses_no_files_found(self, tool2, fake_shared):
        """When get_repo_files returns empty dicts, fmt() returns '_No files found_'."""
        fake_shared.get_repo_files.side_effect = [{}, {}]
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_claude_exception_propagates(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        fake_shared.call_claude.side_effect = RuntimeError("Claude API timeout")
        with pytest.raises(RuntimeError, match="Claude API timeout"):
            tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_exception_propagates(self, tool2, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError):
            tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_owner_and_repo_passed_to_get_repo_files(self, tool2, fake_shared):
        self._setup_files(fake_shared)
        self._setup_claude(fake_shared)
        tool2.generate_docs("my-owner", "my-repo", "https://github.com/run/42")
        first_call = fake_shared.get_repo_files.call_args_list[0]
        assert first_call[0][0] == "my-owner"
        assert first_call[0][1] == "my-repo"

    def test_file_content_truncated_at_4000_chars_in_prompt(self, tool2, fake_shared):
        """Content longer than 4000 chars should be truncated via the [:4000] slice."""
        long_content = "x" * 8000
        py_files = {"big_file.py": long_content}
        iac_files = {}
        fake_shared.get_repo_files.side_effect = [py_files, iac_files]
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1")
        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The truncated version (4000 x's) must appear, not 8000
        assert "x" * 4000 in user_prompt
        assert "x" * 4001 not in user_prompt

    def test_system_prompts_differ_per_doc_type(self, tool2, fake_shared):
        """Each Claude call must use a different system prompt."""
        self._setup_files(fake_shared)
        self._setup_claude(fake_shared)
        tool2.generate_docs("acme", "myrepo", "https://github.com/run/1