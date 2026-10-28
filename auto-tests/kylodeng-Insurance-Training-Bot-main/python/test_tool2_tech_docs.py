"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, partial file sets, empty file sets, Claude call failures
- build_index(): correct markdown output, link construction, timestamp inclusion,
  single doc, multiple docs, empty docs dict
- fmt() inner function behaviour (via generate_docs integration)
- __main__ block: success path, exception/failure path, missing env vars

Mocks used:
- shared.call_claude         — patched to return synthetic doc strings
- shared.get_repo_files      — patched to return synthetic file dicts
- shared.write_output_file   — patched to return synthetic URLs
- shared.send_email          — patched to be a no-op
- shared.email_html          — patched to return a dummy HTML string
- shared.write_audit_entry   — patched to be a no-op
- shared.OUTPUT_REPO_OWNER   — patched as module-level constant
- shared.OUTPUT_REPO         — patched as module-level constant
- datetime.datetime.utcnow   — patched for deterministic timestamps

TODOs:
- TODO: Integration test against a real GitHub repo requires credentials/network
- TODO: Test Claude response truncation behaviour when files exceed 4000 chars
        (requires inspecting the prompt string passed to call_claude)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared stubbed out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub for the `shared` module."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="# Generated doc")
    stub.get_repo_files = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>fake</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    stub.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return stub


def _import_module(shared_stub=None):
    """Import tool2_tech_docs with a fresh sys.modules entry each time."""
    # Remove cached copies so we get a clean import
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]

    if shared_stub is None:
        shared_stub = _make_shared_stub()

    sys.modules["shared"] = shared_stub

    # The source lives in .github/scripts/; add it to sys.path
    scripts_dir = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts"
    )
    scripts_dir = os.path.normpath(scripts_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    import tool2_tech_docs as mod
    return mod, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def module(shared_stub):
    mod, stub = _import_module(shared_stub)
    return mod, stub


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_happy_path_multiple_docs(self, module):
        mod, stub = module
        docs = {
            "README.md": "# readme",
            "ARCHITECTURE.md": "# arch",
            "RUNBOOK.md": "# runbook",
        }
        result = mod.build_index("myorg", "myrepo", docs, "2024-01-15 10:00 UTC")

        assert "Tech Documentation Index" in result
        assert "myorg/myrepo" in result
        assert "2024-01-15 10:00 UTC" in result
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_contain_correct_github_url(self, module):
        mod, stub = module
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "service", docs, "2024-06-01 00:00 UTC")

        expected_url = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/acme-service/README.md"
        )
        assert expected_url in result

    def test_links_use_owner_repo_path_segment(self, module):
        mod, stub = module
        docs = {"RUNBOOK.md": "content"}
        result = mod.build_index("owner-x", "repo-y", docs, "now")

        assert "tech-docs/owner-x-repo-y/RUNBOOK.md" in result

    def test_single_doc(self, module):
        mod, stub = module
        docs = {"ARCHITECTURE.md": "arch content"}
        result = mod.build_index("o", "r", docs, "ts")

        assert "ARCHITECTURE.md" in result
        # Should not contain README or RUNBOOK links
        assert "README" not in result
        assert "RUNBOOK" not in result

    def test_empty_docs(self, module):
        mod, stub = module
        result = mod.build_index("o", "r", {}, "ts")

        # Index page should still be generated without crashing
        assert "Tech Documentation Index" in result
        assert "o/r" in result

    def test_footer_present(self, module):
        mod, stub = module
        result = mod.build_index("a", "b", {}, "ts")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_generated_timestamp_present(self, module):
        mod, stub = module
        ts = "2099-12-31 23:59 UTC"
        result = mod.build_index("a", "b", {"README.md": ""}, ts)
        assert ts in result

    def test_owner_with_hyphens(self, module):
        mod, stub = module
        docs = {"README.md": "x"}
        result = mod.build_index("my-org", "my-repo", docs, "ts")
        assert "my-org/my-repo" in result
        assert "tech-docs/my-org-my-repo/README.md" in result


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def _make_py_files(self):
        return {
            "main.py": "print('hello')",
            "utils.py": "def foo(): pass",
        }

    def _make_iac_files(self):
        return {
            "main.tf": 'resource "aws_s3_bucket" "b" {}',
            "variables.tf": 'variable "region" {}',
        }

    def test_happy_path_returns_three_docs(self, shared_stub):
        shared_stub.get_repo_files.side_effect = [
            self._make_py_files(),
            self._make_iac_files(),
        ]
        shared_stub.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]
        mod, _ = _import_module(shared_stub)

        result = mod.generate_docs("owner", "repo", "https://run.url")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}
        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_get_repo_files_called_with_correct_extensions(self, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        calls = shared_stub.get_repo_files.call_args_list
        assert len(calls) == 2

        first_extensions = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_extensions
        assert ".js" in first_extensions
        assert ".ts" in first_extensions
        assert ".go" in first_extensions

        second_extensions = calls[1][0][2]
        assert ".tf" in second_extensions
        assert ".yaml" in second_extensions
        assert ".yml" in second_extensions
        assert ".json" in second_extensions

    def test_call_claude_called_three_times(self, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        assert shared_stub.call_claude.call_count == 3

    def test_owner_repo_in_claude_prompts(self, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("myowner", "myrepo", "url")

        for c in shared_stub.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg to call_claude
            assert "myowner/myrepo" in user_prompt

    def test_empty_files_shows_no_files_found(self, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        # When no files, the prompt should contain the fallback string
        for c in shared_stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_files_included_in_readme_prompt(self, shared_stub):
        shared_stub.get_repo_files.side_effect = [
            {"app.py": "code here"},
            {"main.tf": "terraform here"},
        ]
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        readme_call = shared_stub.call_claude.call_args_list[0]
        readme_prompt = readme_call[0][1]
        assert "app.py" in readme_prompt
        assert "code here" in readme_prompt

    def test_iac_files_in_architecture_prompt(self, shared_stub):
        shared_stub.get_repo_files.side_effect = [
            {"app.py": "source"},
            {"main.tf": "infra code"},
        ]
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        arch_call = shared_stub.call_claude.call_args_list[1]
        arch_prompt = arch_call[0][1]
        assert "main.tf" in arch_prompt
        assert "infra code" in arch_prompt

    def test_claude_failure_propagates(self, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("Claude API down")
        mod, _ = _import_module(shared_stub)

        with pytest.raises(RuntimeError, match="Claude API down"):
            mod.generate_docs("o", "r", "url")

    def test_get_repo_files_failure_propagates(self, shared_stub):
        shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        mod, _ = _import_module(shared_stub)

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("o", "r", "url")

    def test_file_content_truncated_at_4000_chars(self, shared_stub):
        long_content = "x" * 8000
        shared_stub.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        readme_call = shared_stub.call_claude.call_args_list[0]
        readme_prompt = readme_call[0][1]
        # The truncated content (4000 x's) should appear, not the full 8000
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_system_prompts_passed_correctly(self, shared_stub):
        """Each call_claude invocation should use a different system prompt."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        system_prompts = [
            c[0][0] for c in shared_stub.call_claude.call_args_list
        ]
        # All three system prompts should be distinct
        assert len(set(system_prompts)) == 3

    def test_max_files_limits_applied(self, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        mod.generate_docs("o", "r", "url")

        calls = shared_stub.get_repo_files.call_args_list
        # First call: max_files=15, second call: max_files=10
        assert calls[0][1].get("max_files") == 15 or calls[0][0][-1] == 15
        assert calls[1][1].get("max_files") == 10 or calls[1][0][-1] == 10

    @pytest.mark.parametrize("owner,repo", [
        ("simple-org", "simple-repo"),
        ("org-with-hyphens", "repo.with.dots"),
        ("CamelCaseOrg", "CamelCaseRepo"),
        ("org123", "repo456"),
    ])
    def test_various_owner_repo_formats(self, shared_stub, owner, repo):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc"
        mod, _ = _import_module(shared_stub)

        result = mod.generate_docs(owner, repo, "url")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result


# ---------------------------------------------------------------------------
# Tests for the fmt() inner function (via generate_docs integration)
# ---------------------------------------------------------------------------


class TestFmtInnerFunction:
    def test_fmt_with_single_file(self, shared_stub):
        shared_stub.get_repo_files.side_effect = [
            {"file.py": "some code"},
            {},