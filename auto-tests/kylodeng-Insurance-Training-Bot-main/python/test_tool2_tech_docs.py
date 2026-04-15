"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
- build_index(): produces a correct markdown index page with proper links
- __main__ block behaviour: success path (writes files, sends email, writes audit) and failure path

Mocks used:
- shared.call_claude          – stubbed to return predictable doc strings
- shared.get_repo_files       – stubbed to return synthetic file dicts
- shared.write_output_file    – stubbed to return fake GitHub URLs
- shared.send_email           – stubbed (no real SMTP)
- shared.email_html           – stubbed to return a simple HTML string
- shared.write_audit_entry    – stubbed (no real file/API writes)
- shared.OUTPUT_REPO_OWNER    – patched as module-level constant
- shared.OUTPUT_REPO          – patched as module-level constant
- datetime.datetime           – patched for deterministic timestamps

TODOs:
- TODO: Integration test that verifies real Claude prompt format/content requires API key
- TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO env vars are missing
- TODO: Verify write_output_file is called with correct commit message format (needs deeper contract)
"""

import importlib
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we never import the real one
# ---------------------------------------------------------------------------

def _make_fake_shared():
    """Return a fresh fake `shared` module with sensible defaults."""
    fake = types.ModuleType("shared")
    fake.OUTPUT_REPO_OWNER = "test-output-owner"
    fake.OUTPUT_REPO = "test-output-repo"
    fake.call_claude = MagicMock(side_effect=lambda system, user: f"MOCK_DOC::{system[:20]}")
    fake.get_repo_files = MagicMock(return_value={})
    fake.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    fake.send_email = MagicMock()
    fake.email_html = MagicMock(return_value="<html>mock</html>")
    fake.write_audit_entry = MagicMock()
    return fake


def _load_module(fake_shared):
    """
    Insert fake_shared into sys.modules and (re)import tool2_tech_docs
    so it picks up the mock.
    """
    sys.modules["shared"] = fake_shared
    module_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py")
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", module_path)
    mod = importlib.util.module_from_spec(spec)
    # Ensure the script directory is on the path (mirrors the real script's sys.path.insert)
    script_dir = os.path.dirname(module_path)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fake_shared():
    fs = _make_fake_shared()
    yield fs
    # Clean up so other tests start fresh
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def module(fake_shared):
    mod = _load_module(fake_shared)
    return mod


# ---------------------------------------------------------------------------
# Synthetic / reusable test data
# ---------------------------------------------------------------------------

SYNTHETIC_PY_FILES = {
    "src/main.py": "def main():\n    pass\n",
    "src/utils.py": "def helper():\n    return 42\n",
}

SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }',
    "infra/variables.yml": "env: production\n",
}

EXPECTED_DOC_KEYS = {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_returns_all_three_documents(self, module, fake_shared):
        """Happy path: should return a dict with exactly the three expected docs."""
        fake_shared.get_repo_files.return_value = SYNTHETIC_PY_FILES
        result = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert set(result.keys()) == EXPECTED_DOC_KEYS

    def test_calls_get_repo_files_twice(self, module, fake_shared):
        """get_repo_files should be called once for code files and once for IaC files."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert fake_shared.get_repo_files.call_count == 2

    def test_code_files_fetched_with_correct_extensions(self, module, fake_shared):
        """First call should request .py, .js, .ts, .go extensions."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg 3
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".go" in extensions

    def test_iac_files_fetched_with_correct_extensions(self, module, fake_shared):
        """Second call should request .tf, .bicep, .json, .yaml, .yml extensions."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions
        assert ".json" in extensions

    def test_code_files_max_files_limit(self, module, fake_shared):
        """Code files call should pass max_files=15."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        first_call_kwargs = fake_shared.get_repo_files.call_args_list[0][1]
        assert first_call_kwargs.get("max_files") == 15

    def test_iac_files_max_files_limit(self, module, fake_shared):
        """IaC files call should pass max_files=10."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        second_call_kwargs = fake_shared.get_repo_files.call_args_list[1][1]
        assert second_call_kwargs.get("max_files") == 10

    def test_call_claude_called_three_times(self, module, fake_shared):
        """call_claude should be invoked once per document."""
        fake_shared.get_repo_files.return_value = SYNTHETIC_PY_FILES
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, module, fake_shared):
        """README generation should pass SYSTEM_README as the system prompt."""
        fake_shared.get_repo_files.return_value = SYNTHETIC_PY_FILES
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        readme_call = fake_shared.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "technical writer" in system_prompt.lower() or "README" in system_prompt

    def test_architecture_doc_uses_system_arch_prompt(self, module, fake_shared):
        """ARCHITECTURE.md generation should pass SYSTEM_ARCH as the system prompt."""
        fake_shared.get_repo_files.return_value = SYNTHETIC_PY_FILES
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        arch_call = fake_shared.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_runbook_uses_system_runbook_prompt(self, module, fake_shared):
        """RUNBOOK.md generation should pass SYSTEM_RUNBOOK as the system prompt."""
        fake_shared.get_repo_files.return_value = SYNTHETIC_PY_FILES
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        runbook_call = fake_shared.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "devops" in system_prompt.lower() or "runbook" in system_prompt.lower()

    def test_owner_repo_appear_in_claude_user_prompt(self, module, fake_shared):
        """The owner/repo name should be embedded in the user prompts sent to Claude."""
        fake_shared.get_repo_files.return_value = SYNTHETIC_PY_FILES
        module.generate_docs("acme-corp", "rocket-service", "https://github.com/run/1")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme-corp" in user_prompt
            assert "rocket-service" in user_prompt

    def test_empty_file_lists_handled_gracefully(self, module, fake_shared):
        """When no files are found, generate_docs should still return three docs."""
        fake_shared.get_repo_files.return_value = {}
        result = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert set(result.keys()) == EXPECTED_DOC_KEYS

    def test_no_files_produces_no_files_found_placeholder(self, module, fake_shared):
        """When files dict is empty the formatted string should be '_No files found_'."""
        fake_shared.get_repo_files.return_value = {}
        # Capture the user prompt for README (first Claude call)
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, module, fake_shared):
        """File contents longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"bigfile.py": long_content}
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The truncated content "x"*4000 should appear, but not more
        assert "x" * 4000 in user_prompt
        assert "x" * 4001 not in user_prompt

    def test_call_claude_return_value_stored_in_docs(self, module, fake_shared):
        """The return value of call_claude should be the doc content stored in docs."""
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        fake_shared.get_repo_files.return_value = {}
        result = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert result["README.md"] == "README content"
        assert result["ARCHITECTURE.md"] == "ARCH content"
        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_call_claude_exception_propagates(self, module, fake_shared):
        """If call_claude raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API timeout")
        with pytest.raises(RuntimeError, match="Claude API timeout"):
            module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_exception_propagates(self, module, fake_shared):
        """If get_repo_files raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_both_file_types_merged_for_readme_prompt(self, module, fake_shared):
        """README prompt should include content from both code and IaC files."""
        def side_effect(owner, repo, exts, max_files):
            if ".py" in exts:
                return {"main.py": "print('hello')"}
            return {"main.tf": 'resource "x" {}'}

        fake_shared.get_repo_files.side_effect = side_effect
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_user_prompt
        assert "main.tf" in readme_user_prompt

    def test_architecture_prompt_contains_iac_and_source_separately(self, module, fake_shared):
        """ARCHITECTURE prompt should reference both IaC files and source files sections."""
        def side_effect(owner, repo, exts, max_files):
            if ".py" in exts:
                return {"app.py": "# app"}
            return {"infra.tf": "# tf"}

        fake_shared.get_repo_files.side_effect = side_effect
        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        arch_user_prompt = fake_shared.call_claude.call_args_list[1][0][1]
        assert "IaC files" in arch_user_prompt
        assert "Source files" in arch_user_prompt


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_returns_string(self, module):
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = module.build_index("myorg", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("acme", "rocket", docs, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "rocket" in result

    def test_contains_generated_timestamp(self, module):
        docs =