"""
Test suite for .github/scripts/tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates file fetching and Claude calls to produce docs dict
    - build_index(): produces a correctly formatted markdown index page
    - __main__ block: happy path and failure path (env var driven entry point)
    - fmt() inner function behaviour (via generate_docs inputs/outputs)
    - Edge cases: empty file sets, missing env vars, Claude/write failures

Mocks used:
    - shared.call_claude          — avoid real Anthropic API calls
    - shared.get_repo_files       — avoid real GitHub API calls
    - shared.write_output_file    — avoid real GitHub write operations
    - shared.send_email           — avoid real email sending
    - shared.email_html           — avoid template side-effects
    - shared.write_audit_entry    — avoid real audit log writes
    - shared.OUTPUT_REPO_OWNER    — module-level constant patched where needed
    - shared.OUTPUT_REPO          — module-level constant patched where needed
    - datetime.datetime           — deterministic timestamps

TODOs:
    # TODO: Integration test that validates real Claude response structure
    # TODO: Test for rate-limit / retry behaviour in call_claude (needs shared module internals)
    # TODO: Test __main__ with real subprocess to confirm exit code on failure
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared dependencies mocked
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub module that satisfies tool2_tech_docs imports."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/out/blob/main/file")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module(shared_stub=None):
    """Import (or re-import) tool2_tech_docs with the given shared stub."""
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Inject stub before import
    sys.modules["shared"] = shared_stub

    # Remove cached version so we get a fresh import
    sys.modules.pop("tool2_tech_docs", None)

    # Make the script directory importable
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import tool2_tech_docs as mod
    return mod, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_module_cache():
    """Ensure a fresh import for every test."""
    yield
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules.pop("shared", None)


@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def mod(shared_stub):
    module, _ = _import_module(shared_stub)
    return module


@pytest.fixture()
def mod_and_shared(shared_stub):
    module, stub = _import_module(shared_stub)
    return module, stub


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": "x"}, "2024-01-01 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_repo(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": "x"}, "2024-01-01 12:00 UTC")
        assert "acme/myrepo" in result

    def test_contains_timestamp(self, mod):
        now = "2024-06-15 09:30 UTC"
        result = mod.build_index("acme", "myrepo", {"README.md": "x"}, now)
        assert now in result

    def test_links_all_docs(self, mod):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_link_format_contains_github_url(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "https://github.com/" in result

    def test_link_references_output_repo(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_path_contains_owner_repo_and_filename(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/acme-myrepo/README.md" in result

    def test_empty_docs_produces_no_links(self, mod):
        result = mod.build_index("acme", "myrepo", {}, "2024-01-01 00:00 UTC")
        # The links section should be empty (no bullet points)
        assert "- [" not in result

    def test_auto_generated_footer_present(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_multiple_docs_each_have_separate_link(self, mod):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert result.count("- [") == 3

    def test_special_chars_in_owner_repo(self, mod):
        """Owner/repo with hyphens should still produce valid output."""
        docs = {"README.md": ""}
        result = mod.build_index("my-org", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "my-org/my-repo" in result
        assert "tech-docs/my-org-my-repo/README.md" in result


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_dict_with_three_keys(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc content"

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert stub.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_py_extensions(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        first_call_args = stub.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2] if len(first_call_args[0]) > 2 else first_call_args[1].get("extensions", first_call_args[0][2])
        # Check that py, js, ts, go are in the first call
        positional = first_call_args[0]
        assert ".py" in positional[2]
        assert ".js" in positional[2]

    def test_get_repo_files_called_with_iac_extensions(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        second_call_args = stub.get_repo_files.call_args_list[1][0]
        assert ".tf" in second_call_args[2] or ".yaml" in second_call_args[2]

    def test_calls_call_claude_three_times(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert stub.call_claude.call_count == 3

    def test_readme_content_comes_from_claude(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["README.md"] == "README content"

    def test_architecture_content_comes_from_claude(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["ARCHITECTURE.md"] == "ARCH content"

    def test_runbook_content_comes_from_claude(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_owner_repo_passed_to_claude_prompts(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        for c in stub.call_claude.call_args_list:
            prompt = c[0][1]  # second positional arg is the user prompt
            assert "acme/myrepo" in prompt

    def test_with_populated_files(self, mod_and_shared):
        """Files content should be embedded in Claude prompts."""
        mod, stub = mod_and_shared
        stub.get_repo_files.side_effect = [
            {"main.py": "print('hello')", "app.js": "console.log('hi')"},
            {"main.tf": "resource aws_s3_bucket {}"},
        ]
        stub.call_claude.return_value = "Generated"

        result = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        # README prompt should contain main.py content
        readme_prompt = stub.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_prompt
        assert "print('hello')" in readme_prompt

    def test_file_content_truncated_at_4000_chars(self, mod_and_shared):
        """Files with content > 4000 chars should be truncated."""
        mod, stub = mod_and_shared
        long_content = "x" * 10_000
        stub.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        stub.call_claude.return_value = "Generated"

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = stub.call_claude.call_args_list[0][0][1]
        # The truncated slice should be present, but not the full 10000 chars
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_empty_files_shows_no_files_found(self, mod_and_shared):
        """When no files are returned, fmt() should output _No files found_."""
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "Generated"

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = stub.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_claude_exception_propagates(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_exception_propagates(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_max_files_limit_passed_for_source(self, mod_and_shared):
        mod, stub = mod_and_shared
        stub.get_repo_files.return_value = {}

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        first_call_kwargs = stub.get_repo_files.call_args_list[0]
        # max_files could be positional or keyword
        args = first_call_kwargs[0]
        kwargs = first_call_kwargs[1]
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 15

    def test_max_files_limit_passed_for_iac(self, mod_and_shared):
        mod, stub = mod_and_shared