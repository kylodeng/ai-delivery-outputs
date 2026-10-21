"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): orchestrates calls to get_repo_files and call_claude
- build_index(): constructs a markdown index page from docs dict
- __main__ block behaviour: happy path, failure/exception path

Mocks used:
- shared.call_claude          — patched to return canned strings
- shared.get_repo_files       — patched to return canned file dicts
- shared.write_output_file    — patched to return fake URLs
- shared.send_email           — patched (no-op)
- shared.email_html           — patched to return a simple string
- shared.write_audit_entry    — patched (no-op)
- shared.OUTPUT_REPO_OWNER    — monkey-patched constant
- shared.OUTPUT_REPO          — monkey-patched constant
- datetime.datetime           — patched for deterministic timestamps

TODOs:
- TODO: Integration test that verifies the real call_claude prompt content is
        well-formed (requires Anthropic credentials — skipped below).
- TODO: Test write_output_file ordering / commit messages once shared.py
        signature is fully stable.
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared patched out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot"
FAKE_OUTPUT_REPO = "ai-delivery-output"

FAKE_PY_FILES = {
    "src/main.py": "def main():\n    pass",
    "src/utils.py": "def helper():\n    return 42",
}
FAKE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "infra/vars.yaml": "env: prod",
}
FAKE_README = "# My README\nProject overview here."
FAKE_ARCH = "# Architecture\nOverview here."
FAKE_RUNBOOK = "# Runbook\nService overview here."


def _build_fake_shared():
    """Build a fake `shared` module so we can import tool2 without the real one."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(side_effect=[FAKE_README, FAKE_ARCH, FAKE_RUNBOOK])
    mod.get_repo_files = MagicMock(side_effect=[FAKE_PY_FILES, FAKE_IAC_FILES])
    mod.write_output_file = MagicMock(return_value="https://github.com/ai-bot/output/blob/main/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>ok</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


def _import_tool2(fake_shared=None):
    """
    Import (or re-import) tool2_tech_docs with a controlled fake shared module.
    Returns the imported module.
    """
    if fake_shared is None:
        fake_shared = _build_fake_shared()

    # Ensure a clean slate
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]
    if "shared" in sys.modules:
        del sys.modules["shared"]

    sys.modules["shared"] = fake_shared

    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also support running from the repo root where the script lives
    alt_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    alt_dir = os.path.normpath(alt_dir)
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    import tool2_tech_docs
    return tool2_tech_docs, fake_shared


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_shared():
    """Return a freshly constructed fake shared module."""
    return _build_fake_shared()


@pytest.fixture()
def tool2(fresh_shared):
    """Import tool2_tech_docs with a fresh fake shared module."""
    mod, shared = _import_tool2(fresh_shared)
    yield mod, shared
    # Cleanup
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]
    if "shared" in sys.modules:
        del sys.modules["shared"]


# ---------------------------------------------------------------------------
# Tests: generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_docs(self, tool2):
        mod, shared = tool2
        result = mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        assert shared.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_py_extensions(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        first_call_args = shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".go" in extensions

    def test_get_repo_files_second_call_iac_extensions(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        second_call_args = shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions

    def test_calls_call_claude_three_times(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        assert shared.call_claude.call_count == 3

    def test_readme_content_is_from_claude(self, tool2):
        mod, shared = tool2
        result = mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        assert result["README.md"] == FAKE_README

    def test_architecture_content_is_from_claude(self, tool2):
        mod, shared = tool2
        result = mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        assert result["ARCHITECTURE.md"] == FAKE_ARCH

    def test_runbook_content_is_from_claude(self, tool2):
        mod, shared = tool2
        result = mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        assert result["RUNBOOK.md"] == FAKE_RUNBOOK

    def test_owner_and_repo_appear_in_readme_prompt(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        readme_call = shared.call_claude.call_args_list[0]
        prompt = readme_call[0][1]  # second positional arg is the user prompt
        assert "acme" in prompt
        assert "my-repo" in prompt

    def test_owner_and_repo_appear_in_arch_prompt(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        arch_call = shared.call_claude.call_args_list[1]
        prompt = arch_call[0][1]
        assert "acme" in prompt
        assert "my-repo" in prompt

    def test_owner_and_repo_appear_in_runbook_prompt(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        runbook_call = shared.call_claude.call_args_list[2]
        prompt = runbook_call[0][1]
        assert "acme" in prompt
        assert "my-repo" in prompt

    def test_no_py_files_produces_no_files_found_string(self, tool2):
        mod, shared = tool2
        # Reset side_effect: first call returns empty, second call returns iac
        shared.get_repo_files.side_effect = [{}, FAKE_IAC_FILES]
        shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]
        result = mod.generate_docs("acme", "empty-repo", "https://github.com/run/1")
        readme_call = shared.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        assert "_No files found_" in prompt or "infra/main.tf" in prompt  # iac merged in

    def test_no_iac_files_produces_no_files_found_in_arch_prompt(self, tool2):
        mod, shared = tool2
        shared.get_repo_files.side_effect = [FAKE_PY_FILES, {}]
        shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]
        mod.generate_docs("acme", "no-iac", "https://github.com/run/1")
        arch_call = shared.call_claude.call_args_list[1]
        prompt = arch_call[0][1]
        assert "_No files found_" in prompt

    def test_file_content_truncated_to_4000_chars(self, tool2):
        mod, shared = tool2
        long_content = "x" * 10_000
        shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]
        mod.generate_docs("acme", "big-repo", "https://github.com/run/1")
        readme_call = shared.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        # The truncated content in the prompt should not exceed 4000 'x' chars
        assert "x" * 4001 not in prompt
        assert "x" * 4000 in prompt

    def test_call_claude_readme_uses_correct_system_prompt(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        readme_call = shared.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_call_claude_arch_uses_correct_system_prompt(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        arch_call = shared.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_call_claude_runbook_uses_correct_system_prompt(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        runbook_call = shared.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "runbook" in system_prompt.lower() or "devops" in system_prompt.lower()

    def test_call_claude_raises_propagates(self, tool2):
        mod, shared = tool2
        shared.call_claude.side_effect = RuntimeError("Claude unavailable")
        with pytest.raises(RuntimeError, match="Claude unavailable"):
            mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

    def test_get_repo_files_raises_propagates(self, tool2):
        mod, shared = tool2
        shared.get_repo_files.side_effect = ConnectionError("GitHub down")
        with pytest.raises(ConnectionError, match="GitHub down"):
            mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

    def test_max_files_param_passed_for_source_files(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        first_call_kwargs = shared.get_repo_files.call_args_list[0]
        # max_files can be positional or keyword
        all_args = list(first_call_kwargs[0]) + list(first_call_kwargs[1].values())
        assert 15 in all_args

    def test_max_files_param_passed_for_iac_files(self, tool2):
        mod, shared = tool2
        mod.generate_docs("acme", "my-repo", "https://github.com/run/1")
        second_call_kwargs = shared.get_repo_files.call_args_list[1]
        all_args = list(second_call_kwargs[0]) + list(second_call_kwargs[1].values())
        assert 10 in all_args


# ---------------------------------------------------------------------------
# Tests: build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    @pytest.fixture()
    def tool2_mod(self, tool2):
        mod, _ = tool2
        return mod

    def test_returns_string(self, tool2_mod):
        result = tool2_mod.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, tool2_mod):
        result = tool2_mod.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "my-repo" in result

    def test_contains_timestamp(self, tool2_mod):
        result = tool2_mod.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "2024-01-15 10:00 UTC" in result

    def test_contains_all_doc_names(self, tool2_mod):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = tool2_mod.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert