"""
Test suite for .github/scripts/tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates fetching repo files and calling Claude for README, ARCHITECTURE, RUNBOOK
- build_index(): constructs a markdown index page with correct links and metadata
- __main__ block: environment variable handling, file writing, email sending, audit logging, error handling

Mocks used:
- shared.call_claude         — prevents real Anthropic API calls
- shared.get_repo_files      — prevents real GitHub API calls
- shared.write_output_file   — prevents real GitHub commits
- shared.send_email          — prevents real SES/SMTP calls
- shared.email_html          — lightweight HTML builder stub
- shared.write_audit_entry   — prevents real audit writes
- shared.OUTPUT_REPO_OWNER   — module-level constant
- shared.OUTPUT_REPO         — module-level constant
- datetime.datetime.utcnow   — deterministic timestamps in __main__ tests

TODOs:
- TODO: Integration test covering real GitHub API round-trip (requires live credentials)
- TODO: Test Claude response truncation / token-limit behaviour once known prompt sizes are fixed
- TODO: Test network retry/back-off if shared.call_claude gains retry logic
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
# Helpers — build a fake `shared` module so we never import the real one
# ---------------------------------------------------------------------------

def _make_shared_module():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="# Generated content")
    shared.get_repo_files     = MagicMock(return_value={})
    shared.write_output_file  = MagicMock(return_value="https://github.com/out/file")
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-owner"
    shared.OUTPUT_REPO        = "test-output-repo"
    return shared


def _import_tool(shared_module):
    """
    Import (or re-import) tool2_tech_docs with the given fake shared module
    injected into sys.modules.
    """
    sys.modules["shared"] = shared_module
    # Ensure a clean import each time
    sys.modules.pop("tool2_tech_docs", None)

    script_dir = os.path.join(os.path.dirname(__file__),
                              ".github", "scripts")
    # Add script directory to path so the module resolves correctly.
    # Fall back gracefully when running tests from repo root.
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # The source file lives at .github/scripts/tool2_tech_docs.py.
    # We load it by spec so we don't rely on it being importable via PYTHONPATH.
    import importlib.util
    candidate_paths = [
        os.path.join(script_dir, "tool2_tech_docs.py"),
        os.path.join(os.path.dirname(__file__), "tool2_tech_docs.py"),
    ]
    spec = None
    for p in candidate_paths:
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("tool2_tech_docs", p)
            break

    if spec is None:
        pytest.skip("tool2_tech_docs.py not found — adjust path in test helper")

    module = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared():
    mod = _make_shared_module()
    yield mod
    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool(shared):
    return _import_tool(shared)


# ---------------------------------------------------------------------------
# Sample data (derived from synthetic samples provided)
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance agent..."}}',
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" { bucket = "my-docs" }',
    "infra/variables.yaml": "variables:\n  env: production",
}

SAMPLE_README_CONTENT    = "# README\nProject overview.\n## Tech stack\n..."
SAMPLE_ARCH_CONTENT      = "# Architecture\nOverview paragraph.\n## Resources deployed\n..."
SAMPLE_RUNBOOK_CONTENT   = "# Runbook\nService overview.\n## Health checks\n..."


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, tool, shared):
        """generate_docs returns dict with README.md, ARCHITECTURE.md, RUNBOOK.md."""
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT,
            SAMPLE_ARCH_CONTENT,
            SAMPLE_RUNBOOK_CONTENT,
        ]

        result = tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_comes_from_call_claude(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT,
            SAMPLE_ARCH_CONTENT,
            SAMPLE_RUNBOOK_CONTENT,
        ]

        result = tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["README.md"] == SAMPLE_README_CONTENT

    def test_architecture_content_comes_from_call_claude(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT,
            SAMPLE_ARCH_CONTENT,
            SAMPLE_RUNBOOK_CONTENT,
        ]

        result = tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["ARCHITECTURE.md"] == SAMPLE_ARCH_CONTENT

    def test_runbook_content_comes_from_call_claude(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT,
            SAMPLE_ARCH_CONTENT,
            SAMPLE_RUNBOOK_CONTENT,
        ]

        result = tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["RUNBOOK.md"] == SAMPLE_RUNBOOK_CONTENT

    def test_get_repo_files_called_with_correct_extensions(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        calls = shared.get_repo_files.call_args_list
        assert len(calls) == 2

        first_call_exts  = calls[0][0][2]   # positional arg index 2
        second_call_exts = calls[1][0][2]

        assert ".py" in first_call_exts
        assert ".ts" in first_call_exts
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts

    def test_get_repo_files_called_with_owner_and_repo(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("my-owner", "my-repo", "https://github.com/run/1")

        for c in shared.get_repo_files.call_args_list:
            assert c[0][0] == "my-owner"
            assert c[0][1] == "my-repo"

    def test_call_claude_called_three_times(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert shared.call_claude.call_count == 3

    def test_readme_prompt_contains_owner_and_repo(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_call = shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "acme/myrepo" in user_prompt

    def test_architecture_prompt_contains_iac_content(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        arch_call = shared.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        # IaC file path should appear in the architecture prompt
        assert "infra/main.tf" in user_prompt

    def test_empty_files_produces_no_files_found_placeholder(self, tool, shared):
        """When no files are found, fmt() returns the placeholder text."""
        shared.get_repo_files.side_effect = [{}, {}]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        # All three calls should still succeed; prompts contain placeholder
        readme_prompt = shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_at_4000_chars(self, tool, shared):
        long_content = "x" * 10_000
        shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = shared.call_claude.call_args_list[0][0][1]
        # The truncated content (4000 x's) must appear but not 10 000
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_call_claude_error_propagates(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_error_propagates(self, tool, shared):
        shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_max_files_limit_passed_for_py_files(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        py_call = shared.get_repo_files.call_args_list[0]
        assert py_call[1].get("max_files") == 15 or (
            len(py_call[0]) > 3 and py_call[0][3] == 15
        )

    def test_max_files_limit_passed_for_iac_files(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        iac_call = shared.get_repo_files.call_args_list[1]
        assert iac_call[1].get("max_files") == 10 or (
            len(iac_call[0]) > 3 and iac_call[0][3] == 10
        )

    def test_system_prompt_for_readme_contains_key_sections(self, tool, shared):
        shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared.call_claude.side_effect = [
            SAMPLE_README_CONTENT, SAMPLE_ARCH_CONTENT, SAMPLE_RUNBOOK_CONTENT
        ]

        tool.generate_docs("acme", "myrepo", "https://github.com/run/1")

        system_prompt = shared.call_claude.call_args_list[0][0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_system_prompt_for_arch_contains_key_sections(self, tool, shared):