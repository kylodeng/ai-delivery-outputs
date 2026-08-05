"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates fetching repo files and calling Claude to produce docs
- build_index(): constructs a markdown index page from docs dict
- __main__ block logic (via subprocess or monkeypatching os.environ + calling the logic directly)
- fmt() inner function behaviour (via generate_docs outputs)
- Edge cases: empty file sets, missing env vars, Claude/write failures

Mocks used:
- shared.call_claude          — prevents real Anthropic API calls
- shared.get_repo_files       — prevents real GitHub API calls
- shared.write_output_file    — prevents real GitHub write operations
- shared.send_email           — prevents real email delivery
- shared.email_html           — prevents rendering side-effects
- shared.write_audit_entry    — prevents real audit writes
- shared.OUTPUT_REPO_OWNER    — patched as a constant
- shared.OUTPUT_REPO          — patched as a constant
- datetime.datetime           — frozen for deterministic timestamps

TODOs:
# TODO: Integration test against a real GitHub repo (requires GH_TOKEN secret)
# TODO: Test actual Claude prompt content quality / hallucination guard
# TODO: Test __main__ block subprocess exit code when exception propagates
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with its `shared` dependency mocked
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot"
FAKE_OUTPUT_REPO = "output-repo"


def _make_shared_mock():
    """Return a mock module that stands in for `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="**Generated content**")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output-repo/blob/main/file.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture()
def shared_mock():
    """Inject a fresh shared mock and reload the module under test each time."""
    mock = _make_shared_mock()
    sys.modules["shared"] = mock

    # Remove cached module so it is re-imported with fresh mock
    sys.modules.pop("tool2_tech_docs", None)

    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the directory of this test file's parent for CI layouts
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dir = os.path.join(repo_root, ".github", "scripts")
    if os.path.isdir(target_dir) and target_dir not in sys.path:
        sys.path.insert(0, target_dir)

    import tool2_tech_docs  # noqa: F401 — imported so shared_mock is in effect
    yield mock, tool2_tech_docs

    # Cleanup
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules.pop("shared", None)


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_contains_repo_header(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "content", "ARCHITECTURE.md": "arch", "RUNBOOK.md": "run"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")

        assert "# Tech Documentation Index — acme/myrepo" in result

    def test_happy_path_contains_generated_timestamp(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")

        assert "2024-01-15 10:00 UTC" in result

    def test_happy_path_contains_links_for_all_docs(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "x"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")

        for name in docs:
            assert name in result

    def test_link_format_uses_output_repo_constants(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")

        expected_fragment = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            "/blob/main/tech-docs/acme-myrepo/README.md"
        )
        assert expected_fragment in result

    def test_auto_generated_footer_present(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")

        assert "_Auto-generated by AI Delivery Bot_" in result

    def test_empty_docs_dict_produces_valid_index(self, shared_mock):
        _, mod = shared_mock
        result = mod.build_index("acme", "myrepo", {}, "2024-01-15 10:00 UTC")

        assert "# Tech Documentation Index — acme/myrepo" in result
        # No links section should still produce a string
        assert isinstance(result, str)

    def test_owner_repo_with_special_chars_in_path(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "content"}
        result = mod.build_index("org-name", "repo.name", docs, "2024-06-01 00:00 UTC")

        assert "org-name/repo.name" in result
        assert "tech-docs/org-name-repo.name/README.md" in result

    def test_multiple_docs_each_have_separate_link_line(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "x"}
        result = mod.build_index("o", "r", docs, "now")

        # Each doc should appear as a list item
        assert result.count("- [") == len(docs)

    def test_different_owners_produce_different_indexes(self, shared_mock):
        _, mod = shared_mock
        docs = {"README.md": "content"}
        result_a = mod.build_index("owner-a", "repo", docs, "2024-01-01 00:00 UTC")
        result_b = mod.build_index("owner-b", "repo", docs, "2024-01-01 00:00 UTC")

        assert "owner-a" in result_a
        assert "owner-b" in result_b
        assert "owner-a" not in result_b
        assert "owner-b" not in result_a


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_calls_get_repo_files_twice(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert mock.get_repo_files.call_count == 2

    def test_first_get_repo_files_fetches_source_extensions(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        first_call_args = mock.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2] if first_call_args[0] else first_call_args[1]["extensions"]
        # Accept positional or keyword
        args, kwargs = first_call_args
        exts = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        for ext in [".py", ".js", ".ts", ".go"]:
            assert ext in exts

    def test_second_get_repo_files_fetches_iac_extensions(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        second_call_args = mock.get_repo_files.call_args_list[1]
        args, kwargs = second_call_args
        exts = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        for ext in [".tf", ".bicep", ".json", ".yaml", ".yml"]:
            assert ext in exts

    def test_calls_call_claude_three_times(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert mock.call_claude.call_count == 3

    def test_returns_dict_with_three_doc_keys(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}
        mock.call_claude.side_effect = ["readme content", "arch content", "runbook content"]

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_comes_from_first_claude_call(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}
        mock.call_claude.side_effect = ["README CONTENT", "ARCH CONTENT", "RUNBOOK CONTENT"]

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["README.md"] == "README CONTENT"

    def test_architecture_content_comes_from_second_claude_call(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}
        mock.call_claude.side_effect = ["README CONTENT", "ARCH CONTENT", "RUNBOOK CONTENT"]

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["ARCHITECTURE.md"] == "ARCH CONTENT"

    def test_runbook_content_comes_from_third_claude_call(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}
        mock.call_claude.side_effect = ["README CONTENT", "ARCH CONTENT", "RUNBOOK CONTENT"]

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["RUNBOOK.md"] == "RUNBOOK CONTENT"

    def test_readme_prompt_contains_owner_and_repo(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_user_prompt = mock.call_claude.call_args_list[0][0][1]
        assert "acme/myrepo" in readme_user_prompt

    def test_architecture_prompt_contains_owner_and_repo(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        arch_user_prompt = mock.call_claude.call_args_list[1][0][1]
        assert "acme/myrepo" in arch_user_prompt

    def test_with_source_files_includes_content_in_prompt(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.side_effect = [
            {"src/main.py": "print('hello')"},
            {},
        ]

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = mock.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_prompt

    def test_with_iac_files_includes_content_in_arch_prompt(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.side_effect = [
            {},
            {"infra/main.tf": 'resource "aws_s3_bucket" "b" {}'},
        ]

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        arch_prompt = mock.call_claude.call_args_list[1][0][1]
        assert "main.tf" in arch_prompt

    def test_no_files_found_uses_placeholder_string(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = mock.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, shared_mock):
        mock, mod = shared_mock
        long_content = "x" * 10_000
        mock.get_repo_files.side_effect = [
            {"bigfile.py": long_content},
            {},
        ]

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = mock.call_claude.call_args_list[0][0][1]
        # The truncated content (4000 x's) should appear, not the full 10000
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_claude_exception_propagates(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}
        mock.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_max_files_source(self, shared_mock):
        mock, mod = shared_mock
        mock.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        first_call_args, first_call_kwargs = mock.get_repo_files.call_args_list