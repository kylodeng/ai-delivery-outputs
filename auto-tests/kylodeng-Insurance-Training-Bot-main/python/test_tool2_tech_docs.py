"""
Tests for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
- build_index(): builds a markdown index page with correct links and metadata
- __main__ block: environment variable handling, success path, failure/exception path

Mocks used:
- shared.call_claude (patched as tool2_tech_docs.call_claude)
- shared.get_repo_files (patched as tool2_tech_docs.get_repo_files)
- shared.write_output_file (patched as tool2_tech_docs.write_output_file)
- shared.send_email (patched as tool2_tech_docs.send_email)
- shared.email_html (patched as tool2_tech_docs.email_html)
- shared.write_audit_entry (patched as tool2_tech_docs.write_audit_entry)
- datetime.datetime (patched for deterministic timestamps)

TODOs:
- TODO: Integration test with real Claude API (requires ANTHROPIC_API_KEY)
- TODO: Integration test with real GitHub API (requires GH_TOKEN and repo access)
- TODO: Test behaviour when get_repo_files returns very large file contents (>4000 chars truncation)
"""

import sys
import os
import importlib
import runpy
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MODULE = "tool2_tech_docs"


def _import_module():
    """Re-import the module so patches applied before import take effect."""
    if MODULE in sys.modules:
        del sys.modules[MODULE]
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import tool2_tech_docs
    return tool2_tech_docs


FAKE_PY_FILES = {
    "src/main.py": "def main():\n    pass",
    "src/utils.py": "def helper():\n    return 42",
}
FAKE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "infra/vars.yaml": "region: us-east-1",
}
FAKE_README = "# My Project\nThis is the README."
FAKE_ARCH = "# Architecture\nOverview here."
FAKE_RUNBOOK = "# Runbook\nHealth checks here."

FAKE_DOCS = {
    "README.md": FAKE_README,
    "ARCHITECTURE.md": FAKE_ARCH,
    "RUNBOOK.md": FAKE_RUNBOOK,
}

OUTPUT_OWNER = "output-owner"
OUTPUT_REPO_NAME = "output-repo"


@pytest.fixture(autouse=True)
def patch_shared_constants(monkeypatch):
    """Patch OUTPUT_REPO_OWNER and OUTPUT_REPO constants used in the module."""
    monkeypatch.setenv("SOURCE_REPO_OWNER", "test-owner")
    monkeypatch.setenv("SOURCE_REPO_NAME", "test-repo")
    monkeypatch.setenv("GITHUB_RUN_URL", "https://github.com/runs/123")


@pytest.fixture()
def mock_shared():
    """Return a namespace of common mocks for shared module functions."""
    mocks = types.SimpleNamespace(
        call_claude=MagicMock(side_effect=[FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]),
        get_repo_files=MagicMock(side_effect=[FAKE_PY_FILES, FAKE_IAC_FILES]),
        write_output_file=MagicMock(return_value="https://github.com/output/file"),
        send_email=MagicMock(),
        email_html=MagicMock(return_value="<html>email</html>"),
        write_audit_entry=MagicMock(),
        OUTPUT_REPO_OWNER=OUTPUT_OWNER,
        OUTPUT_REPO=OUTPUT_REPO_NAME,
    )
    return mocks


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def _patch_and_import(self, mock_shared):
        patches = {
            "tool2_tech_docs.call_claude": mock_shared.call_claude,
            "tool2_tech_docs.get_repo_files": mock_shared.get_repo_files,
            "tool2_tech_docs.write_output_file": mock_shared.write_output_file,
            "tool2_tech_docs.send_email": mock_shared.send_email,
            "tool2_tech_docs.email_html": mock_shared.email_html,
            "tool2_tech_docs.write_audit_entry": mock_shared.write_audit_entry,
            "tool2_tech_docs.OUTPUT_REPO_OWNER": OUTPUT_OWNER,
            "tool2_tech_docs.OUTPUT_REPO": OUTPUT_REPO_NAME,
        }
        return patches

    def test_generate_docs_returns_three_documents(self, mock_shared):
        """Happy path: generate_docs returns README, ARCHITECTURE, RUNBOOK keys."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            result = tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_generate_docs_readme_content(self, mock_shared):
        """README.md content matches what call_claude returns first."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            result = tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert result["README.md"] == FAKE_README

    def test_generate_docs_architecture_content(self, mock_shared):
        """ARCHITECTURE.md content matches what call_claude returns second."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            result = tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert result["ARCHITECTURE.md"] == FAKE_ARCH

    def test_generate_docs_runbook_content(self, mock_shared):
        """RUNBOOK.md content matches what call_claude returns third."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            result = tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert result["RUNBOOK.md"] == FAKE_RUNBOOK

    def test_generate_docs_calls_get_repo_files_twice(self, mock_shared):
        """get_repo_files is called once for source files and once for IaC files."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert mock_shared.get_repo_files.call_count == 2

    def test_generate_docs_get_repo_files_py_extensions(self, mock_shared):
        """First get_repo_files call requests Python/JS/TS/Go extensions."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        first_call_args = mock_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2] if len(first_call_args[0]) > 2 else first_call_args[1].get("extensions", first_call_args[0][2])
        # positional: (owner, repo, extensions, max_files=...)
        positional = first_call_args[0]
        assert ".py" in positional[2]
        assert ".js" in positional[2]

    def test_generate_docs_get_repo_files_iac_extensions(self, mock_shared):
        """Second get_repo_files call requests IaC-related extensions."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        second_call_args = mock_shared.get_repo_files.call_args_list[1]
        positional = second_call_args[0]
        assert ".tf" in positional[2]
        assert ".yaml" in positional[2]

    def test_generate_docs_calls_call_claude_three_times(self, mock_shared):
        """call_claude is invoked exactly three times (README, ARCH, RUNBOOK)."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert mock_shared.call_claude.call_count == 3

    def test_generate_docs_owner_repo_in_claude_prompt(self, mock_shared):
        """The owner/repo name appears in the prompt sent to Claude."""
        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        # Check all three Claude calls contain 'acme/my-service'
        for c in mock_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg is the user prompt
            assert "acme/my-service" in user_prompt

    def test_generate_docs_empty_iac_files(self, mock_shared):
        """When IaC files are empty, fmt returns '_No files found_' and docs still generated."""
        mock_shared.get_repo_files.side_effect = [FAKE_PY_FILES, {}]
        mock_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            result = tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert "ARCHITECTURE.md" in result
        # The architecture prompt should contain the no-files placeholder
        arch_call = mock_shared.call_claude.call_args_list[1]
        assert "_No files found_" in arch_call[0][1]

    def test_generate_docs_empty_source_files(self, mock_shared):
        """When source files are empty, fmt returns '_No files found_' placeholder."""
        mock_shared.get_repo_files.side_effect = [{}, FAKE_IAC_FILES]
        mock_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            result = tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

        assert "README.md" in result
        readme_call = mock_shared.call_claude.call_args_list[0]
        assert "_No files found_" in readme_call[0][1]

    def test_generate_docs_call_claude_raises(self, mock_shared):
        """If call_claude raises, generate_docs propagates the exception."""
        mock_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            with pytest.raises(RuntimeError, match="Claude API error"):
                tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

    def test_generate_docs_get_repo_files_raises(self, mock_shared):
        """If get_repo_files raises, generate_docs propagates the exception."""
        mock_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with patch("tool2_tech_docs.call_claude", mock_shared.call_claude), \
             patch("tool2_tech_docs.get_repo_files", mock_shared.get_repo_files):
            import tool2_tech_docs
            importlib.reload(tool2_tech_docs)
            with pytest.raises(ConnectionError, match="GitHub unreachable"):
                tool2_tech_docs.generate_docs("acme", "my-service", "https://github.com/runs/1")

    def test_generate_docs_file_content_truncated_in_fmt(self, mock_shared):
        """Files with content >4000 chars are truncated to 4000 chars in the prompt."""
        long_content = "x" * 8000
        mock_shared.get_repo_files.side_effect = [
            {"src/big.py": long_content},
            {},
        ]
        mock_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        with patch("tool2_tech_docs.call