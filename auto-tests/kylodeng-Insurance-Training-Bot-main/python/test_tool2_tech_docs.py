"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown structure, link generation, timestamp inclusion
- __main__ block behaviour: success path, exception/failure path
- fmt() inner function behaviour (via generate_docs integration)

Mocks used:
- shared.call_claude          → prevents real Anthropic API calls
- shared.get_repo_files       → prevents real GitHub API calls
- shared.write_output_file    → prevents real GitHub writes
- shared.send_email           → prevents real SES/SMTP calls
- shared.email_html           → prevents real template rendering
- shared.write_audit_entry    → prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    → constant patched to known value
- shared.OUTPUT_REPO          → constant patched to known value
- datetime.datetime.utcnow    → deterministic timestamp in tests

TODOs:
- TODO: Integration test against a real (sandboxed) GitHub repo — skipped below
- TODO: Test rate-limit / retry behaviour of call_claude — depends on shared internals
- TODO: Test file truncation at 4000 chars in fmt() with real oversized content
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so the import in tool2_tech_docs
# succeeds without any real credentials / network access.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub module that satisfies tool2_tech_docs imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated doc")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def stub_shared(monkeypatch):
    """Inject the fake shared module before each test and reload the SUT."""
    shared_stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", shared_stub)
    # Also patch the sys.path insertion so it doesn't affect real imports
    monkeypatch.syspath_prepend(os.path.dirname(__file__))
    yield shared_stub


@pytest.fixture()
def sut(stub_shared):
    """Import (or re-import) the module under test with fresh mocks."""
    # Remove any cached version so we always get a clean load
    sys.modules.pop("tool2_tech_docs", None)
    # Resolve path relative to this test file or the repo root
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if not os.path.isdir(scripts_dir):
        # Fallback: assume tests run from repo root
        scripts_dir = os.path.join(os.getcwd(), ".github", "scripts")
    sys.path.insert(0, scripts_dir)
    import tool2_tech_docs as mod
    return mod


# ===========================================================================
# generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_happy_path_calls_claude_three_times(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [
            {"src/main.py": "print('hello')"},   # py/js/ts/go call
            {"infra/main.tf": "resource {}"},     # iac call
        ]
        stub_shared.call_claude.return_value = "# Doc content"

        docs = sut.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert stub_shared.call_claude.call_count == 3
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_returns_claude_output_verbatim(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [
            {"app.py": "x = 1"},
            {"main.tf": "resource {}"},
        ]
        stub_shared.call_claude.side_effect = ["readme content", "arch content", "runbook content"]

        docs = sut.generate_docs("org", "repo", "https://github.com/run/1")

        assert docs["README.md"] == "readme content"
        assert docs["ARCHITECTURE.md"] == "arch content"
        assert docs["RUNBOOK.md"] == "runbook content"

    def test_no_files_found_still_calls_claude(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Empty doc"

        docs = sut.generate_docs("org", "empty-repo", "https://github.com/run/2")

        assert stub_shared.call_claude.call_count == 3
        assert len(docs) == 3

    def test_no_files_produces_no_files_found_placeholder(self, sut, stub_shared):
        """When get_repo_files returns empty dicts fmt() should produce placeholder text."""
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/3")

        # The prompt passed to call_claude for README should contain the placeholder
        readme_call_args = stub_shared.call_claude.call_args_list[0]
        prompt = readme_call_args[0][1]  # positional second arg
        assert "_No files found_" in prompt

    def test_get_repo_files_called_with_correct_extensions(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("my-org", "my-repo", "https://github.com/run/4")

        calls = stub_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        first_exts = calls[0][0][2]  # positional arg: extensions list
        assert ".py" in first_exts
        assert ".ts" in first_exts
        assert ".go" in first_exts
        assert ".js" in first_exts

        second_exts = calls[1][0][2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts
        assert ".yml" in second_exts

    def test_max_files_limits_are_passed(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/5")

        calls = stub_shared.get_repo_files.call_args_list
        # max_files keyword argument
        assert calls[0][1].get("max_files") == 15
        assert calls[1][1].get("max_files") == 10

    def test_file_content_truncated_at_4000_chars(self, sut, stub_shared):
        long_content = "x" * 8000
        stub_shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/6")

        # The first call to call_claude is for README; check the prompt
        readme_prompt = stub_shared.call_claude.call_args_list[0][0][1]
        # The raw content in the prompt should be <= 4000 chars per file block
        assert long_content not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_architecture_doc_uses_iac_files_separately(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [
            {"app.py": "print('hi')"},
            {"main.tf": "terraform {}"},
        ]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/7")

        arch_prompt = stub_shared.call_claude.call_args_list[1][0][1]
        assert "main.tf" in arch_prompt
        assert "app.py" in arch_prompt

    def test_readme_prompt_contains_owner_and_repo(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("acme-corp", "awesome-service", "https://github.com/run/8")

        readme_prompt = stub_shared.call_claude.call_args_list[0][0][1]
        assert "acme-corp" in readme_prompt
        assert "awesome-service" in readme_prompt

    def test_call_claude_receives_correct_system_prompt_for_readme(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/9")

        system_prompt = stub_shared.call_claude.call_args_list[0][0][0]
        assert "senior technical writer" in system_prompt.lower() or "technical writer" in system_prompt

    def test_call_claude_receives_correct_system_prompt_for_arch(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/10")

        system_prompt = stub_shared.call_claude.call_args_list[1][0][0]
        assert "architect" in system_prompt.lower()

    def test_call_claude_receives_correct_system_prompt_for_runbook(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/11")

        system_prompt = stub_shared.call_claude.call_args_list[2][0][0]
        assert "devops" in system_prompt.lower() or "runbook" in system_prompt.lower()

    def test_call_claude_error_propagates(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [{}, {}]
        stub_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            sut.generate_docs("org", "repo", "https://github.com/run/12")

    def test_multiple_files_are_all_included_in_prompt(self, sut, stub_shared):
        stub_shared.get_repo_files.side_effect = [
            {"app.py": "code1", "utils.py": "code2", "main.go": "code3"},
            {},
        ]
        stub_shared.call_claude.return_value = "# Doc"

        sut.generate_docs("org", "repo", "https://github.com/run/13")

        readme_prompt = stub_shared.call_claude.call_args_list[0][0][1]
        assert "app.py" in readme_prompt
        assert "utils.py" in readme_prompt
        assert "main.go" in readme_prompt


# ===========================================================================
# build_index()
# ===========================================================================

class TestBuildIndex:

    def test_returns_string(self, sut):
        result = sut.build_index("org", "repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo_in_header(self, sut):
        result = sut.build_index("my-org", "my-repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "my-org" in result
        assert "my-repo" in result

    def test_contains_timestamp(self, sut):
        result = sut.build_index("org", "repo", {"README.md": "x"}, "2024-06-15 12:30 UTC")
        assert "2024-06-15 12:30 UTC" in result

    def test_links_all_docs(self, sut):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = sut.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_format_uses_output_repo(self, sut):
        docs = {"README.md": "content"}
        result = sut.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_format_includes_path_structure(self, sut):
        docs = {"README.md": "content"}
        result = sut.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/org-repo/README.md" in result

    def test_link_format_is_github_url(self, sut):
        docs = {"README.md": "content"}
        result = sut.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "https://github.com/" in result

    def test_contains_auto_generated_footer(self, sut):
        result = sut.build_index("org", "repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result or "auto-generated" in result.lower()

    def test_empty_docs_dict_produces_no_links(self, sut):
        result = sut.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        assert "README.md" not in result
        assert isinstance(result, str)

    def test_special_chars_in_owner_and_repo(self, sut):
        """Hyphens and dots in owner/repo names should appear in the index path."""
        docs = {"README.md": "content"}
        result = sut.build_index("my-org", "my.repo", docs, "2024-01-01 00:00 UTC")
        assert "my-org" in result
        assert "my.repo" in result

    def