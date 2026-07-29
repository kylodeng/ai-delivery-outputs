"""
Tests for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates fetching repo files and calling Claude for each doc type
    - build_index(): constructs markdown index page with correct links and metadata
    - __main__ block behaviour (success path and failure/exception path)

Mocks used:
    - shared.call_claude          — stubbed to return deterministic strings
    - shared.get_repo_files       — stubbed to return synthetic file dicts
    - shared.write_output_file    — stubbed to return fake GitHub URLs
    - shared.send_email           — stubbed (no-op)
    - shared.email_html           — stubbed to return a simple HTML string
    - shared.write_audit_entry    — stubbed (no-op)
    - shared.OUTPUT_REPO_OWNER    — patched to a known string
    - shared.OUTPUT_REPO          — patched to a known string
    - datetime.datetime.utcnow    — patched to a fixed timestamp

TODOs:
    - TODO: Integration test against a real GitHub repo requires credentials — skipped
    - TODO: Test actual Claude response parsing once prompt contract is formalised
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared replaced by a mock
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"

FAKE_README = "# Fake README"
FAKE_ARCH = "# Fake Architecture"
FAKE_RUNBOOK = "# Fake Runbook"

FAKE_PY_FILES = {
    "src/main.py": "def main(): pass",
    "src/utils.py": "def helper(): return 42",
}
FAKE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "bucket" {}',
    "infra/vars.yaml": "region: us-east-1",
}
FAKE_OUTPUT_URL = "https://github.com/test-owner/test-output-repo/blob/main/tech-docs/acme-myrepo/README.md"
FAKE_INDEX_URL = "https://github.com/test-owner/test-output-repo/blob/main/tech-docs/acme-myrepo/INDEX.md"


def _make_shared_mock():
    """Return a mock module that replaces `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(side_effect=[FAKE_README, FAKE_ARCH, FAKE_RUNBOOK])
    shared.get_repo_files = MagicMock(side_effect=[FAKE_PY_FILES, FAKE_IAC_FILES])
    shared.write_output_file = MagicMock(return_value=FAKE_OUTPUT_URL)
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>ok</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _load_module(shared_mock=None):
    """Import tool2_tech_docs with the provided shared mock."""
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Inject mock shared before loading
    sys.modules["shared"] = shared_mock

    # Force fresh import
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    import tool2_tech_docs as mod
    return mod, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def module_and_mock():
    """Provide a freshly imported module with a fresh shared mock."""
    mod, shared_mock = _load_module()
    yield mod, shared_mock
    # Cleanup
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]
    if "shared" in sys.modules:
        del sys.modules["shared"]


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_doc_keys(self, module_and_mock):
        mod, shared_mock = module_and_mock
        docs = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_comes_from_call_claude(self, module_and_mock):
        mod, shared_mock = module_and_mock
        docs = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert docs["README.md"] == FAKE_README

    def test_architecture_content_comes_from_call_claude(self, module_and_mock):
        mod, shared_mock = module_and_mock
        docs = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert docs["ARCHITECTURE.md"] == FAKE_ARCH

    def test_runbook_content_comes_from_call_claude(self, module_and_mock):
        mod, shared_mock = module_and_mock
        docs = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert docs["RUNBOOK.md"] == FAKE_RUNBOOK

    def test_get_repo_files_called_twice(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert shared_mock.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_fetches_source_files(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        first_call_args = shared_mock.get_repo_files.call_args_list[0]
        exts = first_call_args[0][2]  # positional arg index 2
        assert ".py" in exts
        assert ".ts" in exts

    def test_get_repo_files_second_call_fetches_iac_files(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        second_call_args = shared_mock.get_repo_files.call_args_list[1]
        exts = second_call_args[0][2]
        assert ".tf" in exts
        assert ".yaml" in exts or ".yml" in exts

    def test_call_claude_called_three_times(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        assert shared_mock.call_claude.call_count == 3

    def test_readme_prompt_contains_owner_repo(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        readme_user_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "acme/myrepo" in readme_user_prompt

    def test_arch_prompt_contains_owner_repo(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        arch_user_prompt = shared_mock.call_claude.call_args_list[1][0][1]
        assert "acme/myrepo" in arch_user_prompt

    def test_runbook_prompt_contains_owner_repo(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        runbook_user_prompt = shared_mock.call_claude.call_args_list[2][0][1]
        assert "acme/myrepo" in runbook_user_prompt

    def test_empty_repo_files_uses_no_files_found_placeholder(self):
        shared_mock = _make_shared_mock()
        # Return empty dicts
        shared_mock.get_repo_files = MagicMock(return_value={})
        shared_mock.call_claude = MagicMock(return_value="content")
        mod, _ = _load_module(shared_mock)

        mod.generate_docs("acme", "empty-repo", "https://github.com/run/1")

        # All three Claude prompts should contain the placeholder
        for c in shared_mock.call_claude.call_args_list:
            assert "_No files found_" in c[0][1]

    def test_file_content_truncated_to_4000_chars(self, module_and_mock):
        """Files with content > 4000 chars should be truncated in the prompt."""
        mod, shared_mock = module_and_mock
        long_content = "x" * 8000
        shared_mock.get_repo_files = MagicMock(side_effect=[
            {"src/big.py": long_content},
            {},
        ])

        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        # The 4000-char slice should appear in the prompt, not the full 8000
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_max_files_limits_are_passed(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        first_kwargs = shared_mock.get_repo_files.call_args_list[0]
        second_kwargs = shared_mock.get_repo_files.call_args_list[1]
        # max_files passed as keyword arg
        assert first_kwargs[1].get("max_files") == 15 or first_kwargs[0][-1] == 15
        assert second_kwargs[1].get("max_files") == 10 or second_kwargs[0][-1] == 10

    def test_different_owner_repo_values(self):
        """Parametrised-style check: prompts reflect the owner/repo passed in."""
        test_cases = [
            ("sun-life", "insurance-portal"),
            ("org-x", "repo-y"),
            ("a", "b"),
        ]
        for owner, repo in test_cases:
            shared_mock = _make_shared_mock()
            mod, _ = _load_module(shared_mock)
            docs = mod.generate_docs(owner, repo, "https://github.com/run/1")
            readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
            assert f"{owner}/{repo}" in readme_prompt

    def test_call_claude_receives_correct_system_prompts(self, module_and_mock):
        mod, shared_mock = module_and_mock
        mod.generate_docs("acme", "myrepo", "https://github.com/run/1")
        sys_prompts = [c[0][0] for c in shared_mock.call_claude.call_args_list]
        # Each system prompt should mention its purpose
        assert "README" in sys_prompts[0] or "technical writer" in sys_prompts[0].lower()
        assert "architect" in sys_prompts[1].lower() or "architecture" in sys_prompts[1].lower()
        assert "runbook" in sys_prompts[2].lower() or "DevOps" in sys_prompts[2]


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    @pytest.fixture()
    def mod(self, module_and_mock):
        return module_and_mock[0]

    def test_returns_string(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_repo_in_title(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "acme/myrepo" in result

    def test_contains_generated_timestamp(self, mod):
        now = "2024-06-01 12:30 UTC"
        result = mod.build_index("acme", "myrepo", {"README.md": ""}, now)
        assert now in result

    def test_contains_link_to_readme(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": "content"}, "2024-01-15 10:00 UTC")
        assert "README.md" in result

    def test_contains_link_to_architecture(self, mod):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_contains_correct_path_prefix(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "tech-docs/acme-myrepo/README.md" in result

    def test_link_format_is_github_blob_url(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "https://github.com/" in result
        assert "/blob/main/" in result

    def test_contains_auto_generated_footer(self, mod):
        result = mod.build_index("acme", "myrepo", {}, "2024-01-15 10:00 UTC")
        assert "Auto-generated" in result or "AI Delivery Bot" in result

    def test_empty_docs_dict_produces_no_links(self, mod):
        result = mod.build_index("acme", "myrepo", {}, "2024-01-15 10:00 UTC")
        assert "README.md" not in result
        assert "ARCHITECTURE.md" not in result

    def test_multiple_docs_all_appear_in_index(self, mod):
        