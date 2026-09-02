"""
Test module for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates calls to get_repo_files and call_claude
    - build_index(): constructs a markdown index page from docs dict
    - __main__ block behaviour (happy path and failure path)
    - fmt() helper (indirectly via generate_docs)
    - Edge cases: empty file collections, missing env vars, Claude failures

Mocks used:
    - shared.call_claude          — prevents real Anthropic API calls
    - shared.get_repo_files       — prevents real GitHub API calls
    - shared.write_output_file    — prevents real GitHub commits
    - shared.send_email           — prevents real SES/SMTP calls
    - shared.email_html           — prevents template rendering side-effects
    - shared.write_audit_entry    — prevents real audit-log writes
    - shared.OUTPUT_REPO_OWNER    — patched to deterministic value
    - shared.OUTPUT_REPO          — patched to deterministic value
    - datetime.datetime           — pinned to a fixed UTC timestamp

TODOs:
    - TODO: Integration test that exercises a real GitHub repository fixture
    - TODO: Test for rate-limit / retry behaviour in call_claude when that
            logic is added to shared.py
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake "shared" module so tool2_tech_docs can be
# imported without the real shared.py on PYTHONPATH.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot-org"
FAKE_OUTPUT_REPO = "ai-delivery-output"


def _make_fake_shared():
    """Return a MagicMock that looks enough like `shared` to satisfy imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/repo/blob/main/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fresh fake `shared` module before every test and reload
    tool2_tech_docs so it picks up the patched module.
    """
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)

    # Remove cached tool2_tech_docs so the reimport sees the new shared mock
    sys.modules.pop("tool2_tech_docs", None)

    # Also patch sys.path so the insert(0, ...) in the script doesn't cause issues
    script_dir = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.syspath_prepend(script_dir)

    yield mod

    # Cleanup
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool_module(fake_shared):
    """Import (or reimport) the module under test and return it."""
    import importlib
    # Ensure the shared constants are visible at module level after import
    import tool2_tech_docs as m
    return m


@pytest.fixture()
def sample_py_files():
    return {
        "backend/model_card.py": "import catboost\n# Risk model",
        "backend/prompts/assessment.py": "PROMPT = 'You are a finance agent'",
    }


@pytest.fixture()
def sample_iac_files():
    return {
        "infra/main.tf": 'resource "aws_s3_bucket" "docs" {}',
        "infra/variables.yaml": "region: us-east-1",
    }


@pytest.fixture()
def sample_docs():
    return {
        "README.md": "# Project\nOverview text.",
        "ARCHITECTURE.md": "# Architecture\nDetails.",
        "RUNBOOK.md": "# Runbook\nOps steps.",
    }


FIXED_NOW = "2024-06-15 12:00 UTC"
FIXED_DATETIME = datetime.datetime(2024, 6, 15, 12, 0, 0)


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_contains_repo_and_date(self, tool_module, sample_docs):
        result = tool_module.build_index("myorg", "myrepo", sample_docs, FIXED_NOW)

        assert "myorg/myrepo" in result
        assert FIXED_NOW in result

    def test_happy_path_contains_all_doc_links(self, tool_module, sample_docs):
        result = tool_module.build_index("myorg", "myrepo", sample_docs, FIXED_NOW)

        for filename in sample_docs:
            assert filename in result

    def test_links_point_to_output_repo(self, tool_module, sample_docs):
        result = tool_module.build_index("myorg", "myrepo", sample_docs, FIXED_NOW)

        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_format_includes_path(self, tool_module, sample_docs):
        result = tool_module.build_index("myorg", "myrepo", sample_docs, FIXED_NOW)

        assert "tech-docs/myorg-myrepo/README.md" in result
        assert "tech-docs/myorg-myrepo/ARCHITECTURE.md" in result
        assert "tech-docs/myorg-myrepo/RUNBOOK.md" in result

    def test_auto_generated_footer_present(self, tool_module, sample_docs):
        result = tool_module.build_index("myorg", "myrepo", sample_docs, FIXED_NOW)

        assert "Auto-generated by AI Delivery Bot" in result

    def test_empty_docs_dict_produces_valid_markdown(self, tool_module):
        result = tool_module.build_index("myorg", "myrepo", {}, FIXED_NOW)

        assert "myorg/myrepo" in result
        assert "## Documents" in result
        # No doc links should appear
        assert "README.md" not in result

    def test_special_characters_in_repo_name(self, tool_module, sample_docs):
        """Repo names with hyphens are common on GitHub."""
        result = tool_module.build_index("my-org", "my-repo", sample_docs, FIXED_NOW)

        assert "my-org/my-repo" in result
        assert "tech-docs/my-org-my-repo/README.md" in result

    def test_single_doc(self, tool_module):
        docs = {"README.md": "# Readme"}
        result = tool_module.build_index("o", "r", docs, FIXED_NOW)

        assert "README.md" in result
        assert result.count("- [") == 1

    def test_owner_and_repo_used_in_heading(self, tool_module, sample_docs):
        result = tool_module.build_index("acme", "backend-service", sample_docs, FIXED_NOW)

        assert "# Tech Documentation Index — acme/backend-service" in result


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_calls_get_repo_files_for_source_files(self, tool_module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        # Should be called twice: once for py/js/ts/go, once for iac extensions
        assert fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_correct_extensions(self, tool_module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        calls = fake_shared.get_repo_files.call_args_list
        first_call_exts = calls[0][0][2]  # positional arg index 2
        second_call_exts = calls[1][0][2]

        assert ".py" in first_call_exts
        assert ".ts" in first_call_exts
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts
        assert ".yml" in second_call_exts

    def test_returns_three_documents(self, tool_module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Generated"

        docs = tool_module.generate_docs("owner", "repo", "https://run.url")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool_module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_all_files(self, tool_module, fake_shared, sample_py_files, sample_iac_files):
        fake_shared.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]  # second positional arg
        # Both py and iac files should appear in the README prompt
        assert "backend/model_card.py" in user_prompt
        assert "infra/main.tf" in user_prompt

    def test_architecture_doc_includes_iac_and_source(self, tool_module, fake_shared,
                                                       sample_py_files, sample_iac_files):
        fake_shared.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        arch_call = fake_shared.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "infra/main.tf" in user_prompt
        assert "backend/model_card.py" in user_prompt

    def test_runbook_uses_all_files(self, tool_module, fake_shared, sample_py_files, sample_iac_files):
        fake_shared.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        runbook_call = fake_shared.call_claude.call_args_list[2]
        user_prompt = runbook_call[0][1]
        assert "backend/model_card.py" in user_prompt
        assert "infra/main.tf" in user_prompt

    def test_claude_response_stored_correctly(self, tool_module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        docs = tool_module.generate_docs("owner", "repo", "https://run.url")

        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_no_files_found_still_calls_claude(self, tool_module, fake_shared):
        """When repos have no matching files, Claude is still invoked."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Minimal doc"

        docs = tool_module.generate_docs("owner", "repo", "https://run.url")

        assert fake_shared.call_claude.call_count == 3
        assert len(docs) == 3

    def test_no_files_prompt_contains_placeholder(self, tool_module, fake_shared):
        """Fmt helper should produce '_No files found_' when dict is empty."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_repo_name_included_in_prompts(self, tool_module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("acme-corp", "backend-service", "https://run.url")

        for c in fake_shared.call_claude.call_args_list:
            prompt = c[0][1]
            assert "acme-corp/backend-service" in prompt

    def test_file_content_truncated_to_4000_chars(self, tool_module, fake_shared):
        """Files longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.side_effect = [
            {"bigfile.py": long_content},
            {},
        ]
        fake_shared.call_claude.return_value = "# Doc"

        tool_module.generate_docs("owner", "repo", "https://run.url")

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The prompt should contain exactly 4000 x's, not 5000
        assert "x" * 4000 in user_prompt
        assert "x" * 4001 not in user_prompt

    def test_call_claude_propagates_exception(self, tool_module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("API unavailable")

        with pytest.raises(RuntimeError, match="API unavailable"):
            tool_module.generate_docs("owner", "repo", "https://run.url")

    def test_get_repo_files_propagates_exception(self, tool_module, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub down")

        with pytest.raises(ConnectionError, match="GitHub down"):
            tool_module.generate_docs("owner", "repo", "https://run.url")

    def test_max_files_limits_passed_correctly(self, tool