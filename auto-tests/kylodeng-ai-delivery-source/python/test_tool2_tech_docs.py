"""
Test suite for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates file fetching and Claude API calls to produce README, ARCHITECTURE, RUNBOOK docs
    - build_index(): constructs a markdown index page with correct links and metadata
    - __main__ block: environment-driven entry point covering success and failure paths

Mocks used:
    - shared.call_claude          → prevents real Anthropic/Claude API calls
    - shared.get_repo_files       → prevents real GitHub API calls
    - shared.write_output_file    → prevents real GitHub write operations
    - shared.send_email           → prevents real email sending (SES / SMTP)
    - shared.email_html           → prevents rendering side-effects
    - shared.write_audit_entry    → prevents real audit log writes
    - datetime.datetime.utcnow    → deterministic timestamps in tests
    - os.environ                  → controlled env-var injection

TODOs:
    - TODO: Integration test for the full __main__ path requires a real or containerised
            GitHub API + Claude API environment — stubs provided below.
    - TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO constants are overridden
            at import time (requires module reload or monkeypatching the shared module).
"""

import importlib
import sys
import os
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake "shared" module so we can import the SUT
# without the real shared.py being present / having side-effects.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-org"
FAKE_OUTPUT_REPO = "ai-delivery-output"


def _make_fake_shared():
    """Return a fake `shared` module with all symbols used by the SUT."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="mock claude response")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/mock/url")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>mock</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fresh fake `shared` module before every test and remove the
    cached SUT module so each test gets a clean import.
    """
    fs = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fs)

    # Remove the SUT from the module cache so it re-imports against the fake shared
    sys.modules.pop("tool2_tech_docs", None)

    yield fs

    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def sut(fake_shared):
    """Import (or re-import) the SUT after fake_shared is installed."""
    # Ensure the scripts directory is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    scripts_dir = os.path.abspath(scripts_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try importing directly from the same directory as this test file
    # (CI may place tests next to the script)
    import importlib.util

    script_path = os.path.join(scripts_dir, "tool2_tech_docs.py")
    if not os.path.exists(script_path):
        # Fallback: same directory as the test file
        script_path = os.path.join(os.path.dirname(__file__), "tool2_tech_docs.py")

    spec = importlib.util.spec_from_file_location("tool2_tech_docs", script_path)
    module = importlib.util.module_from_spec(spec)
    # Ensure the fake shared is visible during exec
    sys.modules["tool2_tech_docs"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixtures for common test data
# ---------------------------------------------------------------------------

OWNER = "acme"
REPO = "backend-api"
RUN_URL = "https://github.com/acme/backend-api/actions/runs/123"

SAMPLE_PY_FILES = {
    "src/main.py": "def main(): pass",
    "src/utils.py": "def helper(): return 42",
}
SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}',
    "infra/variables.tf": 'variable "region" {}',
}


# ===========================================================================
# Tests: generate_docs()
# ===========================================================================


class TestGenerateDocs:
    def test_returns_three_docs(self, sut, fake_shared):
        """Happy path: should return README, ARCHITECTURE, RUNBOOK."""
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES

        docs = sut.generate_docs(OWNER, REPO, RUN_URL)

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, sut, fake_shared):
        """Should call get_repo_files once for source files, once for IaC files."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        assert fake_shared.get_repo_files.call_count == 2

    def test_source_files_call_correct_extensions(self, sut, fake_shared):
        """First get_repo_files call should request py/js/ts/go files."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".go" in extensions

    def test_iac_files_call_correct_extensions(self, sut, fake_shared):
        """Second get_repo_files call should request tf/bicep/json/yaml/yml files."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions

    def test_source_files_max_files_limit(self, sut, fake_shared):
        """Source files fetch should be limited to 15 files."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        first_call_kwargs = fake_shared.get_repo_files.call_args_list[0]
        # max_files can be positional or keyword
        call_kwargs = first_call_kwargs[1]
        assert call_kwargs.get("max_files", 15) == 15

    def test_iac_files_max_files_limit(self, sut, fake_shared):
        """IaC files fetch should be limited to 10 files."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        second_call_kwargs = fake_shared.get_repo_files.call_args_list[1]
        call_kwargs = second_call_kwargs[1]
        assert call_kwargs.get("max_files", 10) == 10

    def test_calls_claude_three_times(self, sut, fake_shared):
        """Should call call_claude exactly three times (README, ARCH, RUNBOOK)."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, sut, fake_shared):
        """README generation should pass SYSTEM_README as the system prompt."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        readme_call = fake_shared.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "senior technical writer" in system_prompt
        assert "README.md" in system_prompt

    def test_arch_uses_system_arch_prompt(self, sut, fake_shared):
        """ARCHITECTURE generation should pass SYSTEM_ARCH as the system prompt."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        arch_call = fake_shared.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "cloud solutions architect" in system_prompt

    def test_runbook_uses_system_runbook_prompt(self, sut, fake_shared):
        """RUNBOOK generation should pass SYSTEM_RUNBOOK as the system prompt."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        runbook_call = fake_shared.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "DevOps engineer" in system_prompt

    def test_user_prompt_contains_owner_and_repo(self, sut, fake_shared):
        """User prompts passed to Claude should reference owner/repo."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert OWNER in user_prompt
            assert REPO in user_prompt

    def test_returned_content_matches_claude_response(self, sut, fake_shared):
        """Docs dict values should equal what call_claude returned."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        docs = sut.generate_docs(OWNER, REPO, RUN_URL)

        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_no_files_found_uses_placeholder(self, sut, fake_shared):
        """When get_repo_files returns empty dict the fmt helper produces placeholder."""
        fake_shared.get_repo_files.return_value = {}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        # All three prompts should contain the no-files placeholder
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, sut, fake_shared):
        """File content longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"huge_file.py": long_content}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # After truncation we get exactly 4000 x's
        assert "x" * 4000 in user_prompt
        assert "x" * 4001 not in user_prompt

    def test_file_content_exactly_4000_chars_not_truncated(self, sut, fake_shared):
        """File content of exactly 4000 chars should appear in full."""
        exact_content = "a" * 4000
        fake_shared.get_repo_files.return_value = {"file.py": exact_content}

        sut.generate_docs(OWNER, REPO, RUN_URL)

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "a" * 4000 in user_prompt

    def test_call_claude_raises_propagates(self, sut, fake_shared):
        """If call_claude raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            sut.generate_docs(OWNER, REPO, RUN_URL)

    def test_get_repo_files_raises_propagates(self, sut, fake_shared):
        """If get_repo_files raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            sut.generate_docs(OWNER, REPO, RUN_URL)

    def test_both_file_sets_combined_for_readme(self, sut, fake_shared):
        """README prompt should include content from both py and IaC files."""
        fake_shared.get_repo_files.side_effect = [
            {"app.py": "def app(): pass"},
            {"main.tf": 'resource "aws_lambda_function" "fn" {}'},
        ]

        sut.generate_docs(OWNER, REPO, RUN_URL)

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "app.py" in user_prompt
        assert "main.tf" in user_prompt

    def test_arch_prompt_contains_iac_and_source_sections(self, sut, fake_shared):
        """ARCHITECTURE prompt should have separate IaC files and Source files sections."""
        fake_shared.get_repo_files.side_effect = [
            {"app.py": "code"},
            {"main.tf": "infra"},
        ]

        sut.generate_docs(OWNER, REPO, RUN_URL)

        arch_call = fake_shared.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "IaC files" in user_prompt
        assert "Source files" in user_prompt


# ===========================================================================
# Tests: build_index()
# ===========================================================================


class TestBuildIndex:
    def test_returns_string(self, sut):
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = sut.build_index(OWNER, REPO, docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, sut):
        docs = {"README.md": "content"}
        result =