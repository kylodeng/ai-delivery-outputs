"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude API calls to produce README, ARCHITECTURE, RUNBOOK docs
- build_index(): constructs a markdown index page with correct links and metadata
- __main__ block: end-to-end flow including write_output_file, send_email, write_audit_entry calls
- Error handling in __main__ block: audit/email on failure, re-raise

Mocks used:
- shared.call_claude — prevents real Anthropic API calls
- shared.get_repo_files — prevents real GitHub API calls
- shared.write_output_file — prevents real file/repo writes
- shared.send_email — prevents real email sending
- shared.email_html — prevents template rendering side-effects
- shared.write_audit_entry — prevents real audit writes
- shared.OUTPUT_REPO_OWNER, shared.OUTPUT_REPO — patched as constants
- datetime.datetime.utcnow — deterministic timestamps in __main__ tests
- os.environ — controlled via monkeypatch

TODOs:
- TODO: Integration test against a real (sandboxed) GitHub repo once credentials are available
- TODO: Test that call_claude prompts contain the correct system strings (requires inspecting call args more deeply)
- TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO contain special characters
"""

import sys
import os
import importlib
import runpy
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared patched out
# ---------------------------------------------------------------------------
SHARED_MODULE_PATH = "shared"
TOOL_MODULE = "tool2_tech_docs"


def _make_shared_mock(
    call_claude_return=None,
    get_repo_files_return=None,
    write_output_file_return="https://github.com/output/blob/main/file",
):
    """Return a mock `shared` module with sensible defaults."""
    m = types.ModuleType("shared")
    m.call_claude = MagicMock(return_value=call_claude_return or "# Generated content")
    m.get_repo_files = MagicMock(return_value=get_repo_files_return or {})
    m.write_output_file = MagicMock(return_value=write_output_file_return)
    m.send_email = MagicMock()
    m.email_html = MagicMock(return_value="<html>email body</html>")
    m.write_audit_entry = MagicMock()
    m.OUTPUT_REPO_OWNER = "test-output-owner"
    m.OUTPUT_REPO = "test-output-repo"
    return m


def _import_tool(shared_mock):
    """Import (or reimport) the tool module with the given shared mock injected."""
    sys.modules["shared"] = shared_mock
    # Remove cached version so we get a fresh import each time
    sys.modules.pop(TOOL_MODULE, None)
    spec = importlib.util.spec_from_file_location(
        TOOL_MODULE,
        os.path.join(os.path.dirname(__file__), "tool2_tech_docs.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[TOOL_MODULE] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def tool(shared_mock):
    return _import_tool(shared_mock)


# ---------------------------------------------------------------------------
# Synthetic file data (derived from the synthetic samples provided)
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/model_card.py": "# model card loader\nimport json\n\ndef load(): ...",
    "backend/prompts/assessment_criterias.py": "PROMPT = 'You are a finance assessment agent'",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_lambda_function" "api" { function_name = "underwriting" }',
    "infra/variables.tf": 'variable "region" { default = "eu-west-1" }',
}

ALL_SAMPLE_FILES = {**SAMPLE_PY_FILES, **SAMPLE_IAC_FILES}


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    """Tests for the generate_docs() public function."""

    def test_returns_three_docs_keys(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = SAMPLE_PY_FILES

        docs = tool.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_for_source_files(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("acme", "service-a", "https://run/1")

        # First call → source code extensions
        first_call_kwargs = shared_mock.get_repo_files.call_args_list[0]
        assert first_call_kwargs[0][0] == "acme"
        assert first_call_kwargs[0][1] == "service-a"
        assert ".py" in first_call_kwargs[0][2]
        assert ".ts" in first_call_kwargs[0][2]

    def test_calls_get_repo_files_for_iac_files(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("acme", "service-a", "https://run/1")

        second_call_kwargs = shared_mock.get_repo_files.call_args_list[1]
        assert ".tf" in second_call_kwargs[0][2]
        assert ".yaml" in second_call_kwargs[0][2]
        assert ".yml" in second_call_kwargs[0][2]

    def test_get_repo_files_max_files_source(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("acme", "service-a", "https://run/1")

        first_call = shared_mock.get_repo_files.call_args_list[0]
        assert first_call[1].get("max_files") == 15 or first_call[0][3] == 15

    def test_get_repo_files_max_files_iac(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("acme", "service-a", "https://run/1")

        second_call = shared_mock.get_repo_files.call_args_list[1]
        assert second_call[1].get("max_files") == 10 or second_call[0][3] == 10

    def test_call_claude_called_three_times(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = SAMPLE_PY_FILES

        tool.generate_docs("acme", "service-a", "https://run/1")

        assert shared_mock.call_claude.call_count == 3

    def test_readme_content_is_claude_response(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = SAMPLE_PY_FILES
        shared_mock.call_claude.side_effect = [
            "# My README", "# My ARCH", "# My RUNBOOK"
        ]

        docs = tool.generate_docs("acme", "service-a", "https://run/1")

        assert docs["README.md"] == "# My README"

    def test_architecture_content_is_claude_response(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = SAMPLE_PY_FILES
        shared_mock.call_claude.side_effect = [
            "# My README", "# My ARCH", "# My RUNBOOK"
        ]

        docs = tool.generate_docs("acme", "service-a", "https://run/1")

        assert docs["ARCHITECTURE.md"] == "# My ARCH"

    def test_runbook_content_is_claude_response(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = SAMPLE_PY_FILES
        shared_mock.call_claude.side_effect = [
            "# My README", "# My ARCH", "# My RUNBOOK"
        ]

        docs = tool.generate_docs("acme", "service-a", "https://run/1")

        assert docs["RUNBOOK.md"] == "# My RUNBOOK"

    def test_owner_and_repo_appear_in_readme_prompt(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("org-x", "repo-y", "https://run/1")

        readme_user_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "org-x" in readme_user_prompt
        assert "repo-y" in readme_user_prompt

    def test_owner_and_repo_appear_in_arch_prompt(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("org-x", "repo-y", "https://run/1")

        arch_user_prompt = shared_mock.call_claude.call_args_list[1][0][1]
        assert "org-x" in arch_user_prompt
        assert "repo-y" in arch_user_prompt

    def test_owner_and_repo_appear_in_runbook_prompt(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("org-x", "repo-y", "https://run/1")

        runbook_user_prompt = shared_mock.call_claude.call_args_list[2][0][1]
        assert "org-x" in runbook_user_prompt
        assert "repo-y" in runbook_user_prompt

    def test_no_files_found_renders_placeholder(self, shared_mock, tool):
        """When get_repo_files returns empty dict the prompt should include the placeholder."""
        shared_mock.get_repo_files.return_value = {}

        tool.generate_docs("acme", "empty-repo", "https://run/1")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, shared_mock, tool):
        """Files longer than 4000 chars must be truncated in prompts."""
        long_content = "x" * 5000
        shared_mock.get_repo_files.return_value = {"big_file.py": long_content}

        tool.generate_docs("acme", "big-repo", "https://run/1")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        # The truncated content should appear, not the full 5000 chars
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_file_content_exactly_4000_chars_not_truncated(self, shared_mock, tool):
        exact_content = "a" * 4000
        shared_mock.get_repo_files.return_value = {"file.py": exact_content}

        tool.generate_docs("acme", "repo", "https://run/1")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "a" * 4000 in readme_prompt

    def test_with_sample_iac_files(self, shared_mock, tool):
        """Smoke test using the synthetic IaC sample data."""
        def _side_effect(owner, repo, extensions, max_files=20):
            if ".tf" in extensions:
                return SAMPLE_IAC_FILES
            return SAMPLE_PY_FILES

        shared_mock.get_repo_files.side_effect = _side_effect

        docs = tool.generate_docs("acme", "underwriting", "https://run/1")

        assert len(docs) == 3
        # IaC content should appear in the arch prompt
        arch_prompt = shared_mock.call_claude.call_args_list[1][0][1]
        assert "aws_lambda_function" in arch_prompt

    def test_call_claude_raises_propagates(self, shared_mock, tool):
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            tool.generate_docs("acme", "repo", "https://run/1")

    def test_get_repo_files_raises_propagates(self, shared_mock, tool):
        shared_mock.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool.generate_docs("acme", "repo", "https://run/1")


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    """Tests for the build_index() public function."""

    DOCS = {
        "README.md": "# readme",
        "ARCHITECTURE.md": "# arch",
        "RUNBOOK.md": "# runbook",
    }

    def test_returns_string(self, shared_mock, tool):
        result = tool.build_index("acme", "my-repo", self.DOCS, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, shared_mock, tool):
        result = tool.build_index("acme", "my-repo", self.DOCS, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "my-repo" in result

    def test_contains_timestamp(self, shared_mock, tool):
        result = tool.build_index("acme", "my-repo", self.DOCS, "2024-01-15 10:00 UTC")
        assert "2024-01-15 10:00 UTC" in result

    def test_contains_all_doc_names(self, shared_mock, tool):
        result = tool.build_index("acme", "my-repo", self.DOCS, "2024-01-15 10:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, shared_mock, tool):
        result = tool.build_index("acme", "my-repo", self.DOCS, "2024-01-15 10:00 UTC")
        # shared_mock has OUTPUT_REPO_OWNER="test-output-owner", OUTPUT_REPO="test-output-repo"
        assert "test-output-owner" in result
        assert "test-output-repo" in result

    def test_links_are_valid_github_urls(self, shared_mock, tool):
        result = tool.build_index("acme", "my-repo", self.DOCS, "2024-01-15 10:00 UTC")
        assert "https://github.com/test-output-owner/test-output-repo/blob/main/tech-docs/acme-my-repo/" in result

    def test_each_doc_has_its_own_link(self, shared_mock, tool):
        result = tool.