"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): happy path, multiple docs, empty docs dict, special characters in owner/repo
- __main__ block logic (success path, exception/failure path)
- fmt() helper (indirectly through generate_docs)

Mocks used:
- shared.call_claude (prevent real API calls)
- shared.get_repo_files (prevent real GitHub API calls)
- shared.write_output_file (prevent real file writes)
- shared.send_email (prevent real email sends)
- shared.email_html (prevent side effects)
- shared.write_audit_entry (prevent real audit writes)
- shared.OUTPUT_REPO_OWNER, shared.OUTPUT_REPO (constants)
- datetime.datetime.utcnow (deterministic timestamps)
- os.environ (controlled env vars)

TODOs:
- TODO: Integration test against a real GitHub repo + Claude API (requires credentials)
- TODO: Test actual content/quality of Claude-generated docs (requires LLM evaluation)
- TODO: Test write_output_file URL construction with real output repo
"""

import sys
import os
import importlib
import datetime
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module with mocked `shared` so we don't need the real
# shared.py present (it would fail without credentials).
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_mock():
    """Return a mock module that stands in for `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/example/url")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def patch_shared(monkeypatch):
    """
    Inject a mock `shared` module before each test and reload tool2_tech_docs
    so the module-level `from shared import ...` picks up our mocks.
    """
    shared_mock = _make_shared_mock()
    monkeypatch.setitem(sys.modules, "shared", shared_mock)

    # Remove cached version of the module under test so each test gets a clean import
    sys.modules.pop("tool2_tech_docs", None)

    yield shared_mock

    # Cleanup
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def module(patch_shared):
    """Import (or re-import) the module under test after shared is patched."""
    import tool2_tech_docs
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Fixtures for synthetic / common data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "main.py": "def main():\n    pass\n",
    "utils.py": "def helper():\n    return 42\n",
}

SAMPLE_IAC_FILES = {
    "main.tf": 'resource "aws_s3_bucket" "b" {}\n',
    "vars.yaml": "env: production\n",
}

SAMPLE_DOCS = {
    "README.md": "# README\nProject overview.",
    "ARCHITECTURE.md": "# Architecture\nOverview.",
    "RUNBOOK.md": "# Runbook\nOperations.",
}


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================


class TestGenerateDocs:

    def test_happy_path_calls_get_repo_files_twice(self, module, patch_shared):
        """get_repo_files should be called once for source files and once for IaC files."""
        patch_shared.get_repo_files.return_value = SAMPLE_PY_FILES

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert patch_shared.get_repo_files.call_count == 2

    def test_happy_path_calls_call_claude_three_times(self, module, patch_shared):
        """call_claude should be invoked for README, ARCHITECTURE, and RUNBOOK."""
        patch_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        patch_shared.call_claude.return_value = "# Doc content"

        docs = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert patch_shared.call_claude.call_count == 3

    def test_returns_dict_with_three_keys(self, module, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = "# content"

        docs = module.generate_docs("owner", "repo", "https://run")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_comes_from_call_claude(self, module, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        docs = module.generate_docs("owner", "repo", "https://run")

        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_get_repo_files_called_with_correct_extensions(self, module, patch_shared):
        """Verify that source and IaC file extensions are passed correctly."""
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = ""

        module.generate_docs("owner", "repo", "https://run")

        calls = patch_shared.get_repo_files.call_args_list
        # First call: source code files
        first_call_exts = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_call_exts
        assert ".js" in first_call_exts
        assert ".ts" in first_call_exts
        assert ".go" in first_call_exts

        # Second call: IaC files
        second_call_exts = calls[1][0][2]
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts
        assert ".yml" in second_call_exts

    def test_max_files_limits_applied(self, module, patch_shared):
        """Verify max_files keyword argument is passed for both calls."""
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = ""

        module.generate_docs("owner", "repo", "https://run")

        calls = patch_shared.get_repo_files.call_args_list
        assert calls[0][1]["max_files"] == 15
        assert calls[1][1]["max_files"] == 10

    def test_empty_files_uses_no_files_found_placeholder(self, module, patch_shared):
        """When get_repo_files returns empty dicts, the prompt should contain the placeholder."""
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = "doc"

        module.generate_docs("owner", "repo", "https://run")

        # Inspect all call_claude invocations for the placeholder
        for c in patch_shared.call_claude.call_args_list:
            user_msg = c[0][1]
            assert "_No files found_" in user_msg

    def test_file_content_truncated_to_4000_chars(self, module, patch_shared):
        """Files longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10000
        patch_shared.get_repo_files.side_effect = [
            {"bigfile.py": long_content},
            {},
        ]
        patch_shared.call_claude.return_value = "doc"

        module.generate_docs("owner", "repo", "https://run")

        # The README call should only include up to 4000 chars of bigfile.py
        readme_call_user_msg = patch_shared.call_claude.call_args_list[0][0][1]
        assert "x" * 4001 not in readme_call_user_msg
        assert "x" * 4000 in readme_call_user_msg

    def test_owner_and_repo_included_in_prompts(self, module, patch_shared):
        """Owner and repo names should appear in the prompts sent to Claude."""
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = "doc"

        module.generate_docs("acme-corp", "super-repo", "https://run")

        for c in patch_shared.call_claude.call_args_list:
            user_msg = c[0][1]
            assert "acme-corp" in user_msg
            assert "super-repo" in user_msg

    def test_get_repo_files_returns_mixed_files(self, module, patch_shared):
        """Both py/js files and IaC files should appear merged in the README prompt."""
        patch_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        patch_shared.call_claude.return_value = "doc"

        module.generate_docs("owner", "repo", "https://run")

        readme_prompt = patch_shared.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_prompt
        assert "main.tf" in readme_prompt

    def test_call_claude_receives_system_prompts(self, module, patch_shared):
        """Verify that the correct system prompts are passed (spot-check keywords)."""
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = "doc"

        module.generate_docs("owner", "repo", "https://run")

        system_prompts = [c[0][0] for c in patch_shared.call_claude.call_args_list]

        assert any("technical writer" in p for p in system_prompts)
        assert any("architect" in p for p in system_prompts)
        assert any("DevOps engineer" in p for p in system_prompts)

    def test_call_claude_raises_propagates(self, module, patch_shared):
        """If call_claude raises, generate_docs should propagate the exception."""
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.side_effect = RuntimeError("API unavailable")

        with pytest.raises(RuntimeError, match="API unavailable"):
            module.generate_docs("owner", "repo", "https://run")

    def test_get_repo_files_raises_propagates(self, module, patch_shared):
        """If get_repo_files raises, generate_docs should propagate the exception."""
        patch_shared.get_repo_files.side_effect = ConnectionError("GitHub down")

        with pytest.raises(ConnectionError, match="GitHub down"):
            module.generate_docs("owner", "repo", "https://run")


# ===========================================================================
# Tests for build_index()
# ===========================================================================


class TestBuildIndex:

    def test_returns_string(self, module):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, module):
        result = module.build_index("myorg", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "myorg" in result
        assert "myrepo" in result

    def test_contains_generated_timestamp(self, module):
        now = "2024-06-30 12:34 UTC"
        result = module.build_index("owner", "repo", SAMPLE_DOCS, now)
        assert now in result

    def test_contains_all_doc_names(self, module):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, module):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_are_github_urls(self, module):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "https://github.com/" in result

    def test_link_path_includes_owner_repo_subfolder(self, module):
        result = module.build_index("acme", "widget", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "tech-docs/acme-widget/" in result

    def test_contains_auto_generated_footer(self, module):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_empty_docs_dict(self, module):
        """build_index with an empty docs dict should still return a valid index."""
        result = module.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)
        assert "owner" in result

    def test_single_doc(self, module):
        result = module.build_index("o", "r", {"README.md": "content"}, "2024-01-01 00:00 UTC")
        assert "README.md" in result

    def test_special_characters_in_owner_repo(self, module):
        """Hyphens and dots in owner/repo should be handled without errors."""
        result = module.build_index("my-org.io", "my-repo.v2", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "my-org.io" in result
        assert "my-repo.v2" in result

    def test_index_title_format(self, module):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "# Tech Documentation Index" in result

    def test_generated_label_present(self, module):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "Generated" in result

    @pytest.mark.parametrize("doc_name", ["README.md", "ARCHITECTURE.md", "RUNBOOK.md"])
    def test_each_doc_has_link(self, module, doc_name):
        result = module.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert f"[{doc_