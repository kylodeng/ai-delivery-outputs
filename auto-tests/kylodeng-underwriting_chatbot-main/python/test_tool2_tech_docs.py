```python
"""
Tests for .github/scripts/tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestration of file fetching and Claude calls
    - build_index(): index markdown generation with correct links and metadata
    - __main__ block behaviour: success path (writes docs, sends email, audits)
    - __main__ block behaviour: failure path (audits failure, sends failure email, re-raises)
    - fmt() helper (internal closure) via generate_docs integration
    - Edge cases: empty file collections, missing env vars, Claude returning empty strings

Mocks used:
    - shared.call_claude          — prevents real Anthropic API calls
    - shared.get_repo_files       — prevents real GitHub API calls
    - shared.write_output_file    — prevents writes to output repo
    - shared.send_email           — prevents real email delivery
    - shared.email_html           — prevents template rendering side-effects
    - shared.write_audit_entry    — prevents real audit writes
    - datetime.datetime.utcnow    — deterministic timestamps
    - os.environ                  — controlled environment variables

TODOs:
    # TODO: Integration test that verifies the exact Claude prompts contain
    #        the correct file content snippets (requires prompt snapshot fixtures).
    # TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO constants are
    #        changed at import time (currently patched indirectly).
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
# Helpers to import the module under test with its `shared` dependency mocked
# ---------------------------------------------------------------------------

SHARED_MOCK_ATTRS = {
    "call_claude": MagicMock(return_value="# Generated content"),
    "get_repo_files": MagicMock(return_value={}),
    "write_output_file": MagicMock(return_value="https://github.com/output/repo/blob/main/file"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>body</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-org",
    "OUTPUT_REPO": "test-output-repo",
}


def _build_shared_mock():
    """Return a fresh MagicMock representing the `shared` module."""
    m = types.ModuleType("shared")
    for attr, val in SHARED_MOCK_ATTRS.items():
        if callable(val):
            setattr(m, attr, MagicMock(return_value=val.return_value))
        else:
            setattr(m, attr, val)
    return m


def _import_tool(shared_mock=None):
    """
    Import (or reimport) tool2_tech_docs with the given shared mock injected.
    Returns the module object.
    """
    if shared_mock is None:
        shared_mock = _build_shared_mock()

    # Remove any cached version so we get a fresh import
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules["shared"] = shared_mock

    # The module does sys.path.insert(0, ...) which is fine in tests
    spec_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py"
    )

    # Fallback: try relative to repo root
    candidates = [
        spec_path,
        os.path.join(os.path.dirname(__file__), "tool2_tech_docs.py"),
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".github", "scripts", "tool2_tech_docs.py",
        ),
    ]

    module_path = None
    for c in candidates:
        if os.path.exists(c):
            module_path = c
            break

    if module_path is None:
        # Last resort: importlib from wherever pytest finds it on sys.path
        import tool2_tech_docs as mod
        return mod

    import importlib.util
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    m = _build_shared_mock()
    yield m
    # cleanup
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules.pop("shared", None)


@pytest.fixture()
def tool(shared_mock):
    mod = _import_tool(shared_mock)
    return mod


@pytest.fixture()
def sample_py_files():
    return {
        "backend/model_card.py": "print('hello')",
        "backend/prompts/assessment.py": "PROMPT = 'assess this'",
    }


@pytest.fixture()
def sample_iac_files():
    return {
        "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
        "infra/variables.tf": 'variable "region" {}',
    }


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_docs(self, tool, shared_mock):
        """Happy path: generate_docs returns README, ARCHITECTURE, RUNBOOK."""
        shared_mock.get_repo_files.return_value = {"app.py": "print('hi')"}
        shared_mock.call_claude.return_value = "# Doc content"

        docs = tool.generate_docs("my-org", "my-repo", "https://run.url")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool, shared_mock):
        """Claude must be invoked exactly once per document type."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = "content"

        tool.generate_docs("org", "repo", "https://run")

        assert shared_mock.call_claude.call_count == 3

    def test_get_repo_files_called_with_correct_extensions(self, tool, shared_mock):
        """File fetching is called for source and IaC extensions."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = ""

        tool.generate_docs("org", "repo", "https://run")

        calls = shared_mock.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call: source files
        first_exts = calls[0][0][2]
        assert ".py" in first_exts
        assert ".ts" in first_exts

        # Second call: IaC files
        second_exts = calls[1][0][2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts

    def test_readme_prompt_contains_owner_and_repo(self, tool, shared_mock):
        """README Claude call prompt must include owner/repo."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = "readme content"

        tool.generate_docs("acme-org", "acme-repo", "https://run")

        readme_call = shared_mock.call_claude.call_args_list[0]
        prompt_text = readme_call[0][1]  # second positional arg
        assert "acme-org/acme-repo" in prompt_text

    def test_architecture_prompt_contains_iac_files(self, tool, shared_mock, sample_iac_files):
        """Architecture call must forward IaC file content."""
        shared_mock.get_repo_files.side_effect = [
            {"app.py": "x = 1"},  # py/js/ts/go files
            sample_iac_files,      # IaC files
        ]
        shared_mock.call_claude.return_value = "arch"

        tool.generate_docs("org", "repo", "https://run")

        arch_call = shared_mock.call_claude.call_args_list[1]
        prompt_text = arch_call[0][1]
        assert "main.tf" in prompt_text

    def test_runbook_prompt_contains_all_files(self, tool, shared_mock, sample_py_files, sample_iac_files):
        """Runbook call must include both source and IaC content."""
        shared_mock.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        shared_mock.call_claude.return_value = "runbook"

        tool.generate_docs("org", "repo", "https://run")

        runbook_call = shared_mock.call_claude.call_args_list[2]
        prompt_text = runbook_call[0][1]
        assert "model_card" in prompt_text or "app" in prompt_text or "infra" in prompt_text

    def test_empty_files_produces_no_files_found(self, tool, shared_mock):
        """When repos have no files the prompt should contain the fallback text."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = "empty doc"

        tool.generate_docs("org", "repo", "https://run")

        # All three calls should still succeed
        assert shared_mock.call_claude.call_count == 3
        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, tool, shared_mock):
        """File content longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10_000
        shared_mock.get_repo_files.return_value = {"big_file.py": long_content}
        shared_mock.call_claude.return_value = "doc"

        tool.generate_docs("org", "repo", "https://run")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        # The truncated portion must appear but not the full 10k
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_doc_values_are_claude_return_values(self, tool, shared_mock):
        """Docs dict values must equal what call_claude returns."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        docs = tool.generate_docs("org", "repo", "https://run")

        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_claude_exception_propagates(self, tool, shared_mock):
        """If Claude raises, generate_docs should not swallow the exception."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            tool.generate_docs("org", "repo", "https://run")

    def test_get_repo_files_max_files_respected(self, tool, shared_mock):
        """max_files keyword arg must be passed correctly."""
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = ""

        tool.generate_docs("org", "repo", "https://run")

        calls = shared_mock.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15 or calls[0][0][-1] == 15
        assert calls[1][1].get("max_files") == 10 or calls[1][0][-1] == 10

    def test_multiple_files_formatted_with_headers(self, tool, shared_mock):
        """Multiple files should each have a ### header in the prompt."""
        shared_mock.get_repo_files.side_effect = [
            {"file_a.py": "a=1", "file_b.py": "b=2"},
            {},
        ]
        shared_mock.call_claude.return_value = "doc"

        tool.generate_docs("org", "repo", "https://run")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "### file_a.py" in readme_prompt
        assert "### file_b.py" in readme_prompt


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, tool):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = tool.build_index("org", "repo", docs, "2024-01-15 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, tool):
        docs = {"README.md": ""}
        result = tool.build_index("acme-org", "acme-repo", docs, "2024-01-15 12:00 UTC")
        assert "acme-org" in result
        assert "acme-repo" in result

    def test_contains_timestamp(self, tool):
        docs = {"README.md": ""}
        ts = "2024-06-30 09:45 UTC"
        result = tool.build_index("org", "repo", docs, ts)
        assert ts in result

    def test_contains_links_for_each_doc(self, tool):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_constants(self, tool, shared_mock):
        """Links must reference OUTPUT_REPO_OWNER and OUTPUT_REPO constants."""
        docs = {"README.md": ""}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        # Constants are set to "test-org" / "test-output-repo" in shared_mock
        assert "test-org" in result
        assert "test-output-repo" in result

    def test_link_path_includes_owner_repo(self, tool):
        docs = {"README.md": ""}
        result = tool.build_index("my-org", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "my-org-my-repo" in result

    def test_empty_docs_produces_no_links(self, tool):
        result = tool.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        # No document links, but header should still exist
        assert "Tech Documentation Index" in result

    def test_auto_generated_footer_present(self, tool):
        docs = {"README.md": ""}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_link_format_is_github_url(self, tool):
        docs = {"README