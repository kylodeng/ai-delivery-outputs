"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown output, URL construction, timestamp inclusion
- fmt() inner function behaviour (via generate_docs integration)
- __main__ block: success path, exception/failure path
- All Claude calls, file writes, email sends, audit entries are mocked

Mocks used:
- shared.call_claude
- shared.get_repo_files
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- shared.OUTPUT_REPO_OWNER
- shared.OUTPUT_REPO
- datetime.datetime (for deterministic timestamps)

TODOs:
- TODO: Integration test for real GitHub API interactions (requires credentials)
- TODO: Test actual Claude response parsing if response format becomes structured
- TODO: Test file content truncation at 4000 chars (requires shared.get_repo_files to return long content)
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we can import tool2_tech_docs
# without the real shared module on sys.path
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-delivery-org"
FAKE_OUTPUT_REPO = "output-repo"


def _make_fake_shared():
    """Create a minimal fake shared module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated doc")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output-repo/blob/main/file.md")
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Insert a fresh fake shared module before every test and reload
    tool2_tech_docs so it picks up the mock.
    """
    shared_mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)

    # Remove cached tool2_tech_docs so the import runs fresh each time
    sys.modules.pop("tool2_tech_docs", None)

    # Ensure the scripts directory is on sys.path
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        monkeypatch.syspath_prepend(scripts_dir)

    yield shared_mod


@pytest.fixture()
def module(fake_shared):
    """Import (or re-import) tool2_tech_docs and return it."""
    # We need to patch sys.path.insert inside the module to avoid side-effects
    with patch("builtins.__import__", wraps=__import__):
        import tool2_tech_docs as m
    return m


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/model_card.py": "class ModelCard:\n    pass\n",
    "backend/prompts/assessment.py": "PROMPT = 'You are an agent'\n",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}\n',
    "infra/variables.yaml": "vars:\n  env: prod\n",
}


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_basic_structure_contains_title(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("my-owner", "my-repo", docs, "2024-01-15 12:00 UTC")
        assert "# Tech Documentation Index" in result
        assert "my-owner/my-repo" in result

    def test_generated_timestamp_present(self, module):
        docs = {"README.md": ""}
        result = module.build_index("owner", "repo", docs, "2024-06-01 09:30 UTC")
        assert "2024-06-01 09:30 UTC" in result

    def test_links_use_output_repo_constants(self, module, fake_shared):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content"}
        result = module.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_all_doc_names_appear_as_links(self, module):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = module.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_link_format_is_github_url(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("acme", "myapp", docs, "2024-01-01 00:00 UTC")
        assert "https://github.com/" in result
        assert "tech-docs/acme-myapp/README.md" in result

    def test_footer_attribution_present(self, module):
        docs = {"README.md": ""}
        result = module.build_index("o", "r", docs, "now")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_empty_docs_dict(self, module):
        result = module.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")
        # Should still produce a valid index string without links
        assert "# Tech Documentation Index" in result
        assert "Documents" in result

    def test_owner_repo_separator_in_path(self, module):
        """Path should use owner-repo (hyphen), not owner/repo (slash)."""
        docs = {"README.md": ""}
        result = module.build_index("my-owner", "my-repo", docs, "t")
        assert "my-owner-my-repo" in result

    def test_multiple_owners_different_repos(self, module):
        docs_a = {"README.md": ""}
        docs_b = {"RUNBOOK.md": ""}
        result_a = module.build_index("org-a", "repo-x", docs_a, "t1")
        result_b = module.build_index("org-b", "repo-y", docs_b, "t2")
        assert "org-a/repo-x" in result_a
        assert "org-b/repo-y" in result_b


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def _setup_files(self, fake_shared, py_files=None, iac_files=None):
        """Configure get_repo_files side_effect to return different values per call."""
        if py_files is None:
            py_files = SAMPLE_PY_FILES
        if iac_files is None:
            iac_files = SAMPLE_IAC_FILES

        fake_shared.get_repo_files.side_effect = [py_files, iac_files]

    def test_returns_three_docs(self, module, fake_shared):
        self._setup_files(fake_shared)
        fake_shared.call_claude.return_value = "# Doc"
        docs = module.generate_docs("owner", "repo", "https://run-url")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        assert fake_shared.get_repo_files.call_count == 2

    def test_first_get_repo_files_call_fetches_source_extensions(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".go" in extensions

    def test_second_get_repo_files_call_fetches_iac_extensions(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions

    def test_calls_call_claude_three_times(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        first_claude_call = fake_shared.call_claude.call_args_list[0]
        system_prompt = first_claude_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_architecture_uses_system_arch_prompt(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        second_claude_call = fake_shared.call_claude.call_args_list[1]
        system_prompt = second_claude_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_runbook_uses_system_runbook_prompt(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        third_claude_call = fake_shared.call_claude.call_args_list[2]
        system_prompt = third_claude_call[0][0]
        assert "runbook" in system_prompt.lower() or "devops" in system_prompt.lower()

    def test_docs_values_match_claude_return_value(self, module, fake_shared):
        self._setup_files(fake_shared)
        fake_shared.call_claude.return_value = "# Claude Output"
        docs = module.generate_docs("owner", "repo", "https://run-url")
        for content in docs.values():
            assert content == "# Claude Output"

    def test_owner_and_repo_included_in_claude_user_prompt(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("my-owner", "my-repo", "https://run-url")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "my-owner" in user_prompt
            assert "my-repo" in user_prompt

    def test_empty_py_files(self, module, fake_shared):
        """When no source files exist, fmt() returns '_No files found_'."""
        fake_shared.get_repo_files.side_effect = [{}, SAMPLE_IAC_FILES]
        module.generate_docs("owner", "repo", "https://run-url")
        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_user_prompt

    def test_empty_iac_files(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, {}]
        module.generate_docs("owner", "repo", "https://run-url")
        arch_user_prompt = fake_shared.call_claude.call_args_list[1][0][1]
        assert "_No files found_" in arch_user_prompt

    def test_both_file_sets_empty(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        docs = module.generate_docs("owner", "repo", "https://run-url")
        assert len(docs) == 3

    def test_call_claude_raises_propagates(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")
        with pytest.raises(RuntimeError, match="Claude API error"):
            module.generate_docs("owner", "repo", "https://run-url")

    def test_get_repo_files_raises_propagates(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("Network error")
        with pytest.raises(ConnectionError, match="Network error"):
            module.generate_docs("owner", "repo", "https://run-url")

    def test_max_files_limit_passed_to_py_call(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        first_call_kwargs = fake_shared.get_repo_files.call_args_list[0]
        # Check keyword or positional max_files arg
        kwargs = first_call_kwargs[1]
        args = first_call_kwargs[0]
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 15

    def test_max_files_limit_passed_to_iac_call(self, module, fake_shared):
        self._setup_files(fake_shared)
        module.generate_docs("owner", "repo", "https://run-url")
        second_call_kwargs = fake_shared.get_repo_files.call_args_list[1]
        kwargs = second_call_kwargs[1]
        args = second_call_kwargs[0]
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 10


# ===========================================================================
# Tests for fmt() inner function behaviour (via generate_docs integration)
# ===========================================================================

class TestFmtInnerFunction:

    def test_file_path_appears_as_header(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            {"backend/model_card.py": "class ModelCard: pass"},
            {},
        ]
        module.generate_docs("owner", "repo", "url")
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "backend/model_card.py" in readme_prompt

    def test_file_content_included_in_prompt(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            {"backend/model_card.py": "class ModelCard: pass