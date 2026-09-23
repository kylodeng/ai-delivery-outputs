"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude API calls to produce README, ARCHITECTURE, RUNBOOK
- build_index(): constructs a markdown index page from generated docs
- __main__ block behaviour: env-var handling, success path, failure/exception path

Mocks used:
- shared.call_claude          → avoids real Anthropic API calls
- shared.get_repo_files       → avoids real GitHub API calls
- shared.write_output_file    → avoids real GitHub commit operations
- shared.send_email           → avoids real SES/SMTP calls
- shared.email_html           → avoids template rendering side-effects
- shared.write_audit_entry    → avoids real audit-log writes
- shared.OUTPUT_REPO_OWNER    → patched to a deterministic value
- shared.OUTPUT_REPO          → patched to a deterministic value
- datetime.datetime           → patched for deterministic timestamps

TODOs:
- TODO: Integration test that verifies the exact markdown structure Claude returns
        (requires a real or recorded Claude response fixture)
- TODO: Test behaviour when GitHub rate-limit headers are returned by get_repo_files
- TODO: Test partial-failure path (some write_output_file calls succeed, one raises)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake "shared" module so the import in
# tool2_tech_docs.py does not require the real shared.py to be importable.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a mock module that satisfies tool2_tech_docs' imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="## Generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/some/path")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject the fake shared module BEFORE tool2_tech_docs is imported/reloaded.
    Each test gets a fresh module so state does not bleed between tests.
    """
    shared_mock = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mock)

    # If the module was already imported in a previous test, force a reload.
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    yield shared_mock

    # Cleanup
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def module(fake_shared):
    """Import (or re-import) tool2_tech_docs after fake_shared is in place."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.abspath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import tool2_tech_docs as m
    return m


# ---------------------------------------------------------------------------
# Fixtures – synthetic test data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/model_card.py": "# model card loader\nimport json\n",
    "backend/app.py": "# main application entry point\n",
    "backend/prompts/assessment.py": "PROMPT = 'assess this'\n",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" { bucket = "my-docs" }',
    "infra/variables.yaml": "region: us-east-1\n",
}

SAMPLE_ALL_FILES = {**SAMPLE_PY_FILES, **SAMPLE_IAC_FILES}


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================


class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, module, fake_shared):
        """generate_docs should return dict with README, ARCHITECTURE, RUNBOOK keys."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "# Generated"

        result = module.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_get_repo_files_called_with_correct_extensions(self, module, fake_shared):
        """get_repo_files must be called once for source files and once for IaC files."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "https://run-url")

        calls = fake_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        first_exts = calls[0][0][2]   # positional arg index 2 = extensions list
        second_exts = calls[1][0][2]

        assert ".py" in first_exts
        assert ".js" in first_exts
        assert ".ts" in first_exts
        assert ".go" in first_exts

        assert ".tf" in second_exts
        assert ".yaml" in second_exts
        assert ".yml" in second_exts

    def test_get_repo_files_max_files_limits(self, module, fake_shared):
        """Verify max_files keyword arguments are forwarded correctly."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "url")

        calls = fake_shared.get_repo_files.call_args_list
        assert calls[0][1]["max_files"] == 15
        assert calls[1][1]["max_files"] == 10

    def test_call_claude_called_three_times(self, module, fake_shared):
        """Claude must be invoked exactly three times – one per document."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "url")

        assert fake_shared.call_claude.call_count == 3

    def test_call_claude_system_prompts_differ(self, module, fake_shared):
        """Each call_claude invocation must use a different system prompt."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "url")

        system_prompts = [c[0][0] for c in fake_shared.call_claude.call_args_list]
        # All three prompts must be distinct
        assert len(set(system_prompts)) == 3

    def test_owner_and_repo_injected_into_claude_prompt(self, module, fake_shared):
        """The owner/repo names should appear in each user prompt sent to Claude."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("acme-corp", "awesome-service", "url")

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme-corp" in user_prompt
            assert "awesome-service" in user_prompt

    def test_empty_files_produces_no_files_found_placeholder(self, module, fake_shared):
        """When repos have no matching files the placeholder string is used."""
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "url")

        # All three user prompts should contain the placeholder
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, module, fake_shared):
        """File content longer than 4000 chars must be truncated in the prompt."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "url")

        # The README prompt is the first call; inspect its user message
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The raw long string must not appear; the truncated version should
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_call_claude_return_value_stored_in_docs(self, module, fake_shared):
        """The return value of call_claude should be stored verbatim in the docs dict."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        result = module.generate_docs("owner", "repo", "url")

        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_call_claude_exception_propagates(self, module, fake_shared):
        """If Claude raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.side_effect = RuntimeError("API failure")

        with pytest.raises(RuntimeError, match="API failure"):
            module.generate_docs("owner", "repo", "url")

    def test_get_repo_files_exception_propagates(self, module, fake_shared):
        """If get_repo_files raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub down")

        with pytest.raises(ConnectionError, match="GitHub down"):
            module.generate_docs("owner", "repo", "url")

    def test_iac_files_appear_in_arch_prompt_not_only_readme(self, module, fake_shared):
        """IaC content must appear in the architecture prompt."""
        fake_shared.get_repo_files.side_effect = [
            {"app.py": "print('hello')"},
            {"main.tf": 'resource "aws_lambda_function" "fn" {}'},
        ]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "url")

        arch_prompt = fake_shared.call_claude.call_args_list[1][0][1]
        assert "aws_lambda_function" in arch_prompt

    def test_multiple_source_files_all_formatted_in_prompt(self, module, fake_shared):
        """All source files returned by get_repo_files should appear in the prompt."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, {}]
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "url")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        for filename in SAMPLE_PY_FILES:
            assert filename in readme_prompt


# ===========================================================================
# Tests for build_index()
# ===========================================================================


class TestBuildIndex:

    def test_returns_string(self, module):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content"}
        result = module.build_index("org", "repo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("acme", "my-service", docs, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "my-service" in result

    def test_contains_generated_timestamp(self, module):
        docs = {"README.md": "content"}
        now = "2024-06-01 12:30 UTC"
        result = module.build_index("org", "repo", docs, now)
        assert now in result

    def test_contains_links_for_all_docs(self, module):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = module.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, module):
        """Links should point to OUTPUT_REPO_OWNER/OUTPUT_REPO, not source repo."""
        docs = {"README.md": "content"}
        result = module.build_index("source-owner", "source-repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_correct_path_structure(self, module):
        """Each doc link should include tech-docs/{owner}-{repo}/{filename}."""
        docs = {"README.md": "content"}
        result = module.build_index("my-org", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/my-org-my-repo/README.md" in result

    def test_empty_docs_produces_no_list_items(self, module):
        result = module.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        # Should still return a string without crashing; no "- [" links
        assert isinstance(result, str)
        assert "- [" not in result

    def test_auto_generated_footer_present(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_heading_format(self, module):
        """Index heading should identify this as a Tech Documentation Index."""
        docs = {"README