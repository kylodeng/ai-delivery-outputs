"""
Test suite for tool2_tech_docs.py

What is tested:
  - generate_docs(): orchestration of file fetching and Claude calls
  - build_index(): markdown index generation with correct links, timestamps, and doc names
  - __main__ block execution: env-var reading, file writing, email/audit calls, error handling

Mocks used:
  - shared.call_claude          — prevents real Anthropic API calls
  - shared.get_repo_files       — prevents real GitHub API calls
  - shared.write_output_file    — prevents real GitHub file writes
  - shared.send_email           — prevents real email dispatch
  - shared.email_html           — prevents template rendering side-effects
  - shared.write_audit_entry    — prevents real audit log writes
  - shared.OUTPUT_REPO_OWNER    — patched to a known test value
  - shared.OUTPUT_REPO          — patched to a known test value
  - datetime.datetime           — frozen for deterministic timestamp tests

TODOs:
  # TODO: Integration test against a real (sandbox) GitHub repo — needs repo credentials
  # TODO: Test behaviour when Claude returns malformed / empty string responses
  # TODO: Test very large file sets (>15 py files, >10 iac files) for truncation logic
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers: build a minimal fake `shared` module so the import succeeds
# without the real shared.py being on sys.path in test environments.
# ---------------------------------------------------------------------------

FAKE_SHARED_ATTRS = {
    "call_claude": MagicMock(return_value="mock-doc-content"),
    "get_repo_files": MagicMock(return_value={}),
    "write_output_file": MagicMock(return_value="https://github.com/out/repo/blob/main/file"),
    "send_email": MagicMock(),
    "email_html": MagicMock(return_value="<html>mock</html>"),
    "write_audit_entry": MagicMock(),
    "OUTPUT_REPO_OWNER": "test-output-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_fake_shared():
    mod = types.ModuleType("shared")
    for k, v in FAKE_SHARED_ATTRS.items():
        setattr(mod, k, v)
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fake `shared` module before every test and reset mock call counts."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Ensure our target module is (re)loaded with the fake shared injected
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]
    yield fake
    # Cleanup
    sys.modules.pop("tool2_tech_docs", None)


def _import_module():
    """(Re)import tool2_tech_docs after fake_shared is in place."""
    import importlib.util, pathlib
    source = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool2_tech_docs.py"
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", str(source))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def module(fake_shared):
    return _import_module()


@pytest.fixture()
def sample_py_files():
    return {
        "app/main.py": "def main(): pass",
        "app/utils.py": "def helper(): return 42",
    }


@pytest.fixture()
def sample_iac_files():
    return {
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        "infra/variables.tf": 'variable "env" {}',
    }


# ---------------------------------------------------------------------------
# Tests: generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_calls_get_repo_files_twice(self, module, fake_shared, sample_py_files, sample_iac_files):
        """get_repo_files is called once for source files and once for IaC files."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("my-owner", "my-repo", "https://run.url")
        assert fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_correct_extensions(self, module, fake_shared):
        """Verify the exact extension lists passed to get_repo_files."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("owner", "repo", "https://run.url")

        calls = fake_shared.get_repo_files.call_args_list
        # First call: source code extensions
        first_exts = calls[0][0][2]
        assert set(first_exts) == {".py", ".js", ".ts", ".go"}
        assert calls[0][1].get("max_files") == 15 or calls[0][0][3] == 15

        # Second call: IaC extensions
        second_exts = calls[1][0][2]
        assert set(second_exts) == {".tf", ".bicep", ".json", ".yaml", ".yml"}
        assert calls[1][1].get("max_files") == 10 or calls[1][0][3] == 10

    def test_call_claude_called_three_times(self, module, fake_shared):
        """Claude should be invoked exactly once per document (README, ARCH, RUNBOOK)."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("owner", "repo", "https://run.url")
        assert fake_shared.call_claude.call_count == 3

    def test_returns_three_doc_keys(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        docs = module.generate_docs("owner", "repo", "https://run.url")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_doc_values_are_claude_responses(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = [
            "readme-content",
            "arch-content",
            "runbook-content",
        ]
        docs = module.generate_docs("owner", "repo", "https://run.url")
        assert docs["README.md"] == "readme-content"
        assert docs["ARCHITECTURE.md"] == "arch-content"
        assert docs["RUNBOOK.md"] == "runbook-content"

    def test_owner_repo_present_in_claude_prompts(self, module, fake_shared):
        """The user prompt sent to Claude must reference the source repo."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("acme", "my-service", "https://run.url")
        for c in fake_shared.call_claude.call_args_list:
            user_msg = c[0][1]  # second positional arg
            assert "acme/my-service" in user_msg

    def test_files_formatted_into_prompt(self, module, fake_shared, sample_py_files):
        """File paths and content should appear in the Claude prompt."""
        fake_shared.get_repo_files.side_effect = [sample_py_files, {}]
        module.generate_docs("owner", "repo", "https://run.url")

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "app/main.py" in user_prompt
        assert "def main(): pass" in user_prompt

    def test_empty_files_render_no_files_found(self, module, fake_shared):
        """When no files are found the prompt should contain the sentinel string."""
        fake_shared.get_repo_files.return_value = {}
        module.generate_docs("owner", "repo", "https://run.url")

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_long_file_content_truncated_to_4000_chars(self, module, fake_shared):
        """Content longer than 4000 chars must be truncated in the formatted string."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.side_effect = [{"big_file.py": long_content}, {}]
        module.generate_docs("owner", "repo", "https://run.url")

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The truncated slice is 4000 chars; verify the full 10000 is NOT present
        assert "x" * 10_000 not in user_prompt
        assert "x" * 4000 in user_prompt

    def test_iac_files_passed_to_arch_doc_call(self, module, fake_shared, sample_iac_files):
        """Architecture doc call should include IaC file content."""
        fake_shared.get_repo_files.side_effect = [{}, sample_iac_files]
        module.generate_docs("owner", "repo", "https://run.url")

        arch_call = fake_shared.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "infra/main.tf" in user_prompt

    def test_get_repo_files_raises_propagates(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            module.generate_docs("owner", "repo", "https://run.url")

    def test_call_claude_raises_propagates(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = Exception("Claude timeout")
        with pytest.raises(Exception, match="Claude timeout"):
            module.generate_docs("owner", "repo", "https://run.url")

    @pytest.mark.parametrize("owner,repo", [
        ("simple-owner", "simple-repo"),
        ("org-with-dashes", "repo-with-dashes"),
        ("Org123", "Repo_456"),
    ])
    def test_various_owner_repo_combinations(self, module, fake_shared, owner, repo):
        fake_shared.get_repo_files.return_value = {}
        docs = module.generate_docs(owner, repo, "https://run.url")
        assert len(docs) == 3


# ---------------------------------------------------------------------------
# Tests: build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, module, fake_shared):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content"}
        result = module.build_index("owner", "repo", docs, "2024-01-15 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_repo(self, module, fake_shared):
        docs = {"README.md": "c"}
        result = module.build_index("acme", "service", docs, "2024-01-01 00:00 UTC")
        assert "acme/service" in result

    def test_contains_timestamp(self, module, fake_shared):
        docs = {"README.md": "c"}
        result = module.build_index("o", "r", docs, "2099-12-31 23:59 UTC")
        assert "2099-12-31 23:59 UTC" in result

    def test_contains_all_doc_links(self, module, fake_shared):
        docs = {"README.md": "c", "ARCHITECTURE.md": "c", "RUNBOOK.md": "c"}
        result = module.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_links_point_to_correct_output_repo(self, module, fake_shared):
        """Links must reference OUTPUT_REPO_OWNER / OUTPUT_REPO constants."""
        docs = {"README.md": "c"}
        result = module.build_index("owner", "repo", docs, "now")
        assert "test-output-owner" in result
        assert "test-output-repo" in result

    def test_links_contain_owner_repo_in_path(self, module, fake_shared):
        docs = {"README.md": "c"}
        result = module.build_index("acme", "api", docs, "now")
        assert "tech-docs/acme-api/README.md" in result

    def test_empty_docs_produces_empty_links_section(self, module, fake_shared):
        result = module.build_index("o", "r", {}, "now")
        # Should still render the header without crashing
        assert "Tech Documentation Index" in result

    def test_auto_generated_footer_present(self, module, fake_shared):
        docs = {"README.md": "c"}
        result = module.build_index("o", "r", docs, "now")
        assert "Auto-generated" in result

    @pytest.mark.parametrize("doc_name", ["README.md", "ARCHITECTURE.md", "RUNBOOK.md", "INDEX.md"])
    def test_individual_doc_name_in_index(self, module, fake_shared, doc_name):
        docs = {doc_name: "content"}
        result = module.build_index("o", "r", docs, "2024-06-01 10:00 UTC")
        assert doc_name in result

    def test_github_url_format_in_links(self, module, fake_shared):
        """Links must be valid GitHub blob URLs."""
        docs = {"README.md": "c"}
        result = module.build_index("owner", "repo", docs, "now")
        assert "https://github.com/" in result

    def test_multiple_docs_all_appear_as_list_items(self, module, fake_shared):
        docs = {"README.md": "c", "ARCHITECTURE.md": "c", "RUNBOOK.md": "c"}
        result = module.build_index("o", "r", docs, "now")
        # Each item should appear as a markdown list entry
        list_items = [line for line in result.splitlines() if line.startswith("- [")]
        assert len(list_items) == 3


# ---------------------------------------------------------------------------
# Tests: __main__ block — happy path
# ---------------------------------------------------------------------------

class TestMainBlockHappyPath:

    def _run_main(self, module, fake_shared, env_vars=None):
        """Execute the __main__ block with controlled env vars."""
        default_env = {
            "SOURCE_REPO_OWNER": "test-owner",
            "SOURCE_REPO_NAME": "test-repo",
            "GITHUB_RUN_URL": "https://github.com/actions/runs/123",
        }
        if env_vars:
            default_env.update(env_vars)

        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = [
            "readme-content",
            "arch-content",
            "runbook-content",
        ]
        fake_shared.write_output_file.return_value = "https://github.com/out/file"
        fake_shared.email_html.return_value