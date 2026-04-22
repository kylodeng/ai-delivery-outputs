"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates calls to get_repo_files and call_claude
- build_index(): constructs a markdown index page from docs dict
- __main__ block behavior (happy path, exception/failure path)

Mocks used:
- shared.call_claude          — prevents real Anthropic API calls
- shared.get_repo_files       — prevents real GitHub API calls
- shared.write_output_file    — prevents real GitHub write operations
- shared.send_email           — prevents real email sending
- shared.email_html           — prevents real HTML generation
- shared.write_audit_entry    — prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    — patched as a module-level constant
- shared.OUTPUT_REPO          — patched as a module-level constant
- datetime.datetime           — for deterministic timestamp assertions
- os.environ                  — for controlling environment variables

TODOs:
- TODO: Integration test for the full __main__ pipeline against a real (sandbox) GitHub repo
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are missing (None)
- TODO: Validate Claude prompt content more strictly once prompt format is finalised
"""

import sys
import os
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – isolate the module under test so we can reload it per test when
# exercising the __main__ block without polluting other tests.
# ---------------------------------------------------------------------------

# We need to make sure the 'shared' sibling module is importable before we
# import tool2_tech_docs.  We do this by injecting a MagicMock for 'shared'
# into sys.modules before the first import.

SHARED_MODULE_PATH = "shared"

@pytest.fixture(autouse=True)
def mock_shared_module():
    """
    Replace the 'shared' module with a MagicMock before every test so that
    tool2_tech_docs can be imported without a real shared.py on the path.
    """
    mock_shared = MagicMock()
    mock_shared.OUTPUT_REPO_OWNER = "test-owner"
    mock_shared.OUTPUT_REPO = "test-output-repo"
    with patch.dict(sys.modules, {SHARED_MODULE_PATH: mock_shared}):
        yield mock_shared


@pytest.fixture()
def module(mock_shared_module):
    """
    Import (or reload) tool2_tech_docs with the mocked shared module in place.
    Returns the module object so tests can call its public functions directly.
    """
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also ensure the directory containing this test file's sibling scripts is
    # on the path.  Fall back to a relative path that works from the repo root.
    alt_dir = os.path.join(os.path.dirname(__file__))
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    import tool2_tech_docs
    importlib.reload(tool2_tech_docs)
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Fixtures – common test data
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_py_files():
    return {
        "main.py": "def main():\n    pass\n",
        "utils.py": "def helper():\n    return 42\n",
    }


@pytest.fixture()
def sample_iac_files():
    return {
        "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
        "variables.yml": "env: production\n",
    }


@pytest.fixture()
def sample_docs():
    return {
        "README.md": "# My README\nContent here.",
        "ARCHITECTURE.md": "# Architecture\nDetails.",
        "RUNBOOK.md": "# Runbook\nOps stuff.",
    }


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_contains_repo_name(self, module):
        docs = {"README.md": "...", "ARCHITECTURE.md": "..."}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "acme/my-repo" in result

    def test_happy_path_contains_generated_timestamp(self, module):
        docs = {"README.md": "..."}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "2024-01-15 10:00 UTC" in result

    def test_happy_path_links_all_docs(self, module, sample_docs):
        result = module.build_index("acme", "my-repo", sample_docs, "2024-01-15 10:00 UTC")
        for name in sample_docs:
            assert name in result

    def test_links_use_output_repo_owner_and_repo(self, module, mock_shared_module, sample_docs):
        mock_shared_module.OUTPUT_REPO_OWNER = "output-owner"
        mock_shared_module.OUTPUT_REPO = "output-repo"
        importlib.reload(module)  # pick up the new constants
        result = module.build_index("acme", "my-repo", sample_docs, "now")
        assert "output-owner" in result
        assert "output-repo" in result

    def test_links_include_correct_path_prefix(self, module, sample_docs):
        result = module.build_index("acme", "my-repo", sample_docs, "now")
        assert "tech-docs/acme-my-repo/README.md" in result

    def test_empty_docs_produces_no_links(self, module):
        result = module.build_index("acme", "my-repo", {}, "now")
        # Header should still be present
        assert "Tech Documentation Index" in result
        # No markdown list items for documents
        assert "- [" not in result

    def test_contains_auto_generated_footer(self, module):
        result = module.build_index("acme", "my-repo", {}, "now")
        assert "Auto-generated" in result

    def test_owner_with_special_characters(self, module):
        """Org names can contain hyphens."""
        result = module.build_index("my-org", "my-repo", {"README.md": "..."}, "now")
        assert "my-org/my-repo" in result
        assert "tech-docs/my-org-my-repo/README.md" in result

    def test_single_doc(self, module):
        result = module.build_index("o", "r", {"README.md": "content"}, "t")
        assert "README.md" in result
        assert result.count("- [") == 1

    def test_multiple_docs_all_listed(self, module, sample_docs):
        result = module.build_index("o", "r", sample_docs, "t")
        assert result.count("- [") == len(sample_docs)

    def test_returns_string(self, module):
        result = module.build_index("o", "r", {}, "t")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        result = module.generate_docs("acme", "my-repo", "https://run.url")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_values_come_from_call_claude(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        result = module.generate_docs("acme", "my-repo", "https://run.url")

        assert result["README.md"] == "README content"
        assert result["ARCHITECTURE.md"] == "ARCH content"
        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_get_repo_files_called_with_correct_extensions(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        calls = mock_shared_module.get_repo_files.call_args_list
        # First call fetches source code files
        first_call_exts = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_call_exts
        assert ".ts" in first_call_exts
        # Second call fetches IaC files
        second_call_exts = calls[1][0][2]
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts

    def test_call_claude_called_three_times(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        assert mock_shared_module.call_claude.call_count == 3

    def test_readme_prompt_contains_owner_and_repo(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        readme_prompt = mock_shared_module.call_claude.call_args_list[0][0][1]
        assert "acme/my-repo" in readme_prompt

    def test_arch_prompt_contains_owner_and_repo(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        arch_prompt = mock_shared_module.call_claude.call_args_list[1][0][1]
        assert "acme/my-repo" in arch_prompt

    def test_runbook_prompt_contains_owner_and_repo(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        runbook_prompt = mock_shared_module.call_claude.call_args_list[2][0][1]
        assert "acme/my-repo" in runbook_prompt

    def test_no_source_files_uses_fallback_text(
        self, module, mock_shared_module
    ):
        mock_shared_module.get_repo_files.side_effect = [{}, {}]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        result = module.generate_docs("acme", "empty-repo", "https://run.url")

        # Should still return three docs (Claude is still called)
        assert len(result) == 3
        # The fallback text should appear in at least one prompt
        all_prompts = " ".join(
            str(c[0][1]) for c in mock_shared_module.call_claude.call_args_list
        )
        assert "_No files found_" in all_prompts

    def test_file_content_truncated_to_4000_chars(
        self, module, mock_shared_module
    ):
        long_content = "x" * 10_000
        mock_shared_module.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        readme_prompt = mock_shared_module.call_claude.call_args_list[0][0][1]
        # The truncated block should contain exactly 4000 x's
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_call_claude_raised_propagates(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = RuntimeError("API failure")

        with pytest.raises(RuntimeError, match="API failure"):
            module.generate_docs("acme", "my-repo", "https://run.url")

    def test_get_repo_files_called_twice(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        assert mock_shared_module.get_repo_files.call_count == 2

    def test_max_files_respected_for_source_files(
        self, module, mock_shared_module, sample_py_files, sample_iac_files
    ):
        mock_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        mock_shared_module.call_claude.side_effect = ["r", "a", "rb"]

        module.generate_docs("acme", "my-repo", "https://run.url")

        first_call_kwargs = mock_shared_module.get_repo_files.call_args_list[0]
        # max_files=15 may be positional or keyword
        args, kwargs = first_call_kwargs
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 15

    def test_max_files_respected_for_iac_files(
        self, module, mock_shared_module, sample_py_files, sample_