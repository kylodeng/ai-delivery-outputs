"""
Tests for .github/scripts/tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates fetching repo files and calling Claude to produce docs
    - build_index(): builds a markdown index page from docs dict
    - __main__ block behaviour: happy path, failure path (via subprocess or direct call simulation)

Mocks used:
    - shared.call_claude          — patched to return deterministic strings
    - shared.get_repo_files       — patched to return synthetic file dicts
    - shared.write_output_file    — patched to return fake URLs
    - shared.send_email           — patched to no-op
    - shared.email_html           — patched to return a dummy HTML string
    - shared.write_audit_entry    — patched to no-op
    - shared.OUTPUT_REPO_OWNER    — patched via monkeypatch on the imported module
    - shared.OUTPUT_REPO          — patched via monkeypatch on the imported module
    - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
    - TODO: Integration test that verifies real Claude prompt content / structure
            (requires API credentials and is out of scope for unit tests)
    - TODO: Test __main__ block fully end-to-end without subprocess
            (requires refactoring entry-point into a callable main() function)
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
# Helpers to import the module under test with all external deps mocked out
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"
TOOL_MODULE_PATH   = "tool2_tech_docs"  # after we insert the scripts dir into sys.path

SCRIPTS_DIR = os.path.join(
    os.path.dirname(__file__), "..", ".github", "scripts"
)


def _make_shared_stub():
    """Return a mock 'shared' module that stands in for the real one."""
    stub = types.ModuleType("shared")
    stub.call_claude        = MagicMock(return_value="## Generated content")
    stub.get_repo_files     = MagicMock(return_value={})
    stub.write_output_file  = MagicMock(return_value="https://github.com/out/repo/blob/main/file")
    stub.send_email         = MagicMock()
    stub.email_html         = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry  = MagicMock()
    stub.OUTPUT_REPO_OWNER  = "test-owner"
    stub.OUTPUT_REPO        = "test-output-repo"
    return stub


@pytest.fixture(autouse=True)
def _patch_sys_path(tmp_path):
    """Make sure the scripts directory exists on sys.path for the import."""
    # We don't need the real scripts dir — we inject a fake 'shared' directly.
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    yield


@pytest.fixture()
def shared_stub():
    stub = _make_shared_stub()
    sys.modules["shared"] = stub
    # Also remove any previously imported tool module so we get a fresh import
    sys.modules.pop(TOOL_MODULE_PATH, None)
    yield stub
    sys.modules.pop("shared", None)
    sys.modules.pop(TOOL_MODULE_PATH, None)


@pytest.fixture()
def tool(shared_stub):
    """Import (or reimport) tool2_tech_docs with the stub shared module in place."""
    import importlib.util, pathlib

    scripts_path = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts", "tool2_tech_docs.py"
    )
    # Resolve relative to this test file's location
    scripts_path = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), scripts_path)
    )

    spec = importlib.util.spec_from_file_location(TOOL_MODULE_PATH, scripts_path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules[TOOL_MODULE_PATH] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Synthetic file data (derived from the provided samples)
# ---------------------------------------------------------------------------

SYNTHETIC_PY_FILES = {
    "backend/model_card.py": "import json\ncard = json.load(open('model_card.json'))",
    "backend/app.py":        "from flask import Flask\napp = Flask(__name__)",
}

SYNTHETIC_IAC_FILES = {
    "infra/main.tf":       'resource "aws_lambda_function" "fn" { filename = "fn.zip" }',
    "infra/variables.yaml": "variables:\n  env: production\n  region: eu-west-1",
}

SYNTHETIC_ALL_FILES = {**SYNTHETIC_PY_FILES, **SYNTHETIC_IAC_FILES}


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_returns_string(self, tool, shared_stub):
        result = tool.build_index("myorg", "myrepo", {"README.md": "# Hi"}, "2024-01-15 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, tool, shared_stub):
        result = tool.build_index("myorg", "myrepo", {"README.md": "# Hi"}, "2024-01-15 12:00 UTC")
        assert "myorg" in result
        assert "myrepo" in result

    def test_contains_generated_timestamp(self, tool, shared_stub):
        now = "2024-06-01 09:30 UTC"
        result = tool.build_index("myorg", "myrepo", {"README.md": ""}, now)
        assert now in result

    def test_contains_links_to_all_docs(self, tool, shared_stub):
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_constants(self, tool, shared_stub):
        docs = {"README.md": "content"}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        # shared_stub has OUTPUT_REPO_OWNER="test-owner", OUTPUT_REPO="test-output-repo"
        assert "test-owner" in result
        assert "test-output-repo" in result

    def test_link_url_structure(self, tool, shared_stub):
        docs = {"README.md": "content"}
        result = tool.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        assert "https://github.com/test-owner/test-output-repo/blob/main/tech-docs/o-r/README.md" in result

    def test_empty_docs_dict(self, tool, shared_stub):
        result = tool.build_index("myorg", "myrepo", {}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)
        # Should still contain header
        assert "Tech Documentation Index" in result

    def test_footer_branding(self, tool, shared_stub):
        result = tool.build_index("o", "r", {}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_multiple_docs_all_appear_in_links(self, tool, shared_stub):
        docs = {f"DOC{i}.md": f"content {i}" for i in range(5)}
        result = tool.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_owner_repo_separator_in_path(self, tool, shared_stub):
        """Path segment should be owner-repo (hyphen separated)."""
        result = tool.build_index("acme", "widget", {"README.md": ""}, "2024-01-01 00:00 UTC")
        assert "acme-widget" in result

    def test_special_characters_in_owner_repo(self, tool, shared_stub):
        """Hyphens in owner/repo names should be handled gracefully."""
        result = tool.build_index("my-org", "my-repo", {"README.md": ""}, "2024-01-01 00:00 UTC")
        assert "my-org" in result
        assert "my-repo" in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_returns_dict_with_three_keys(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "## Doc content"

        result = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert isinstance(result, dict)
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert shared_stub.call_claude.call_count == 3

    def test_get_repo_files_called_twice(self, tool, shared_stub):
        """Once for source files, once for IaC files."""
        shared_stub.get_repo_files.return_value = {}

        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert shared_stub.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_source_extensions(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        first_call_args = shared_stub.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".ts" in extensions
        assert ".js" in extensions
        assert ".go" in extensions

    def test_get_repo_files_second_call_iac_extensions(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        second_call_args = shared_stub.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions or ".yml" in extensions

    def test_readme_content_comes_from_call_claude(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        result = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_owner_and_repo_passed_to_owner_files(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        tool.generate_docs("acme", "widget", "https://github.com/run/1")

        first_call = shared_stub.get_repo_files.call_args_list[0][0]
        assert first_call[0] == "acme"
        assert first_call[1] == "widget"

    def test_source_files_included_in_readme_prompt(self, tool, shared_stub):
        shared_stub.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,
            SYNTHETIC_IAC_FILES,
        ]
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "backend/app.py" in user_prompt or "backend/model_card.py" in user_prompt

    def test_iac_files_included_in_arch_prompt(self, tool, shared_stub):
        shared_stub.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,
            SYNTHETIC_IAC_FILES,
        ]
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        arch_call = shared_stub.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "infra/main.tf" in user_prompt or "infra/variables.yaml" in user_prompt

    def test_empty_files_returns_no_files_found_placeholder(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        # When no files found, fmt() returns "_No files found_"
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_call_claude_raises_propagates(self, tool, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_raises_propagates(self, tool, shared_stub):
        shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_file_content_truncated_at_4000_chars(self, tool, shared_stub):
        long_content = "x" * 10_000
        shared_stub.get_repo_files.side_effect = [
            {"bigfile.py": long_content},
            {},
        ]
        shared_stub.call_claude.return_value = "content"

        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The truncated content should be at most 4000 chars of the file
        assert "