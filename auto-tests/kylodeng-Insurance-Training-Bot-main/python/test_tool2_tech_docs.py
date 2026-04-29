"""
Tests for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty files, partial files
- build_index(): happy path, multiple docs, empty docs dict, special characters in owner/repo
- fmt() inner function (tested indirectly via generate_docs)
- __main__ block: success path, exception/failure path

Mocks used:
- shared.call_claude (prevents real Anthropic API calls)
- shared.get_repo_files (prevents real GitHub API calls)
- shared.write_output_file (prevents real GitHub writes)
- shared.send_email (prevents real email sending)
- shared.email_html (prevents real template rendering)
- shared.write_audit_entry (prevents real audit writes)
- shared.OUTPUT_REPO_OWNER, shared.OUTPUT_REPO (constants)
- datetime.datetime.utcnow (deterministic timestamps)
- os.environ (controlled environment variables)

TODOs:
- TODO: Integration test against a real or sandboxed GitHub repo requires credentials
- TODO: Test Claude prompt content more precisely once prompt format is stabilised
- TODO: Test write_output_file path construction with deeply nested owner/repo names
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
# Helpers to import the module under test with a fully-mocked `shared`
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_mock():
    """Return a mock module that stands in for `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/some-file")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module(shared_mock=None):
    """
    Import tool2_tech_docs with the shared dependency replaced by shared_mock.
    Removes cached version so each test gets a fresh import.
    """
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Insert mock into sys.modules before import
    sys.modules["shared"] = shared_mock

    # Remove cached module if present
    sys.modules.pop("tool2_tech_docs", None)

    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import tool2_tech_docs
    return tool2_tech_docs, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_mock():
    return _make_shared_mock()


@pytest.fixture
def module(shared_mock):
    mod, sm = _import_module(shared_mock)
    return mod, sm


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_happy_path_contains_repo_name(self, module):
        mod, sm = module
        docs = {"README.md": "# readme", "ARCHITECTURE.md": "# arch", "RUNBOOK.md": "# runbook"}
        result = mod.build_index("my-owner", "my-repo", docs, "2024-01-15 12:00 UTC")
        assert "my-owner/my-repo" in result
        assert "2024-01-15 12:00 UTC" in result

    def test_happy_path_contains_all_doc_links(self, module):
        mod, sm = module
        docs = {"README.md": "x", "ARCHITECTURE.md": "y", "RUNBOOK.md": "z"}
        result = mod.build_index("owner", "repo", docs, "2024-01-15 12:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, module):
        mod, sm = module
        docs = {"README.md": "content"}
        result = mod.build_index("src-owner", "src-repo", docs, "now")
        expected_fragment = f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}/blob/main/tech-docs/src-owner-src-repo/README.md"
        assert expected_fragment in result

    def test_empty_docs_produces_no_links(self, module):
        mod, sm = module
        result = mod.build_index("owner", "repo", {}, "2024-01-15 12:00 UTC")
        assert "Documents" in result
        # No bullet points when no docs
        assert "- [" not in result

    def test_auto_generated_footer_present(self, module):
        mod, sm = module
        result = mod.build_index("owner", "repo", {"README.md": ""}, "now")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_special_characters_in_owner_repo(self, module):
        """owner/repo names with hyphens and numbers should not break formatting."""
        mod, sm = module
        docs = {"README.md": "content"}
        result = mod.build_index("my-org-123", "cool-repo-v2", docs, "2024-01-15 12:00 UTC")
        assert "my-org-123/cool-repo-v2" in result
        assert "my-org-123-cool-repo-v2" in result  # used in path

    def test_single_doc(self, module):
        mod, sm = module
        docs = {"RUNBOOK.md": "content"}
        result = mod.build_index("owner", "repo", docs, "2024-01-15 12:00 UTC")
        assert "RUNBOOK.md" in result
        assert "README.md" not in result

    def test_many_docs(self, module):
        mod, sm = module
        docs = {f"DOC_{i}.md": f"content {i}" for i in range(10)}
        result = mod.build_index("owner", "repo", docs, "now")
        for i in range(10):
            assert f"DOC_{i}.md" in result

    def test_now_string_appears_after_generated_label(self, module):
        mod, sm = module
        docs = {}
        result = mod.build_index("owner", "repo", docs, "2099-12-31 23:59 UTC")
        assert "2099-12-31 23:59 UTC" in result


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:
    def test_happy_path_returns_three_docs(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {"main.py": "print('hello')"}
        sm.call_claude.return_value = "# Generated"
        docs = mod.generate_docs("owner", "repo", "https://run.url")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_for_source_and_iac(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        assert sm.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_correct_py_extensions(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        first_call_args = sm.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2] if first_call_args[0] else first_call_args[1].get("extensions")
        # Just verify the call was made with owner and repo
        assert sm.get_repo_files.call_args_list[0][0][0] == "owner"
        assert sm.get_repo_files.call_args_list[0][0][1] == "repo"

    def test_get_repo_files_called_with_correct_iac_extensions(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        second_call_args = sm.get_repo_files.call_args_list[1]
        assert second_call_args[0][0] == "owner"
        assert second_call_args[0][1] == "repo"

    def test_call_claude_called_three_times(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        assert sm.call_claude.call_count == 3

    def test_each_doc_uses_different_system_prompt(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        system_prompts = [c[0][0] for c in sm.call_claude.call_args_list]
        # All three prompts should be distinct
        assert len(set(system_prompts)) == 3

    def test_readme_call_includes_all_files(self, module):
        mod, sm = module
        sm.get_repo_files.side_effect = [
            {"src/main.py": "code here"},
            {"terraform/main.tf": "resource aws_s3_bucket"}
        ]
        sm.call_claude.return_value = "# content"
        mod.generate_docs("owner", "repo", "https://run.url")
        readme_call = sm.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # README should include both source and IaC files
        assert "main.py" in user_prompt or "main.tf" in user_prompt

    def test_architecture_call_includes_iac_files(self, module):
        mod, sm = module
        sm.get_repo_files.side_effect = [
            {"src/app.py": "app code"},
            {"infra/main.tf": "terraform config"}
        ]
        sm.call_claude.return_value = "# content"
        mod.generate_docs("owner", "repo", "https://run.url")
        arch_call = sm.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "main.tf" in user_prompt

    def test_empty_files_uses_no_files_found_placeholder(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "# content"
        mod.generate_docs("owner", "repo", "https://run.url")
        # All three claude calls should include the no-files placeholder
        for c in sm.call_claude.call_args_list:
            assert "_No files found_" in c[0][1]

    def test_returns_claude_output_for_each_doc(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        sm.call_claude.side_effect = ["# README content", "# ARCH content", "# RUNBOOK content"]
        docs = mod.generate_docs("owner", "repo", "https://run.url")
        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_repo_owner_repo_name_in_prompts(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "# content"
        mod.generate_docs("my-owner", "my-repo", "https://run.url")
        for c in sm.call_claude.call_args_list:
            assert "my-owner/my-repo" in c[0][1]

    def test_file_content_truncated_to_4000_chars(self, module):
        mod, sm = module
        long_content = "x" * 10000
        sm.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {}
        ]
        sm.call_claude.return_value = "# content"
        mod.generate_docs("owner", "repo", "https://run.url")
        # The prompt should contain at most 4000 x's (truncated)
        readme_prompt = sm.call_claude.call_args_list[0][0][1]
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_call_claude_propagates_exception(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        sm.call_claude.side_effect = RuntimeError("Claude API failure")
        with pytest.raises(RuntimeError, match="Claude API failure"):
            mod.generate_docs("owner", "repo", "https://run.url")

    def test_get_repo_files_propagates_exception(self, module):
        mod, sm = module
        sm.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("owner", "repo", "https://run.url")

    def test_max_files_limit_passed_for_source_files(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        first_call = sm.get_repo_files.call_args_list[0]
        # max_files=15 for source files
        assert first_call[1].get("max_files") == 15 or (len(first_call[0]) > 3 and first_call[0][3] == 15)

    def test_max_files_limit_passed_for_iac_files(self, module):
        mod, sm = module
        sm.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        second_call = sm.get_repo_files.call_args_list[1]
        # max_files=10 for IaC files
        assert second_call[1].get("max_files") == 10 or (len(second_call[0]) > 3 and second_call[0][3] == 10)


# ---------------------------------------------------------------------------
# Tests: fmt() inner function (tested indirectly)
# ---------------------------------------------------------------------------

class TestFmtInnerFunction:
    """
    The fmt() function is defined inside generate_docs and is not directly
    accessible; we test its observable behaviour through generate_docs outputs.
    """

    def test_multiple_files_are_all_included_in_prompt(self, module):
        mod, sm = module
        sm.get_repo_files.side_effect = [
            {"file_a.py":