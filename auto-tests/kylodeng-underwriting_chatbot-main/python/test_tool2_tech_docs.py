"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK docs
- build_index(): constructs a markdown index page with links to generated documents
- __main__ block behaviour: successful run (writes files, sends email, writes audit) and failure path

Mocks used:
- shared.call_claude          — avoids real Anthropic/Claude API calls
- shared.get_repo_files       — avoids real GitHub API calls
- shared.write_output_file    — avoids real GitHub file-write calls
- shared.send_email           — avoids real email delivery
- shared.email_html           — avoids dependency on email templating
- shared.write_audit_entry    — avoids real audit-log writes
- shared.OUTPUT_REPO_OWNER    — patched to known test value
- shared.OUTPUT_REPO          — patched to known test value
- datetime.datetime           — frozen so timestamp assertions are deterministic

TODOs:
- TODO: Integration test exercising the real `shared` helpers against a test GitHub repo
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are absent (None values)
- TODO: Verify exact Claude prompts contain expected substrings (requires capturing call_claude args)
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
# Helpers — build a minimal fake `shared` module so the import succeeds
# without requiring the real file to be on the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal stub of the `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/file")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fresh fake `shared` module before every test."""
    shared_mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)
    yield shared_mod


@pytest.fixture()
def tool2(fake_shared):  # noqa: F811
    """Import (or reload) tool2_tech_docs with the fake shared module active."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.abspath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Remove cached version so each test gets a fresh import
    sys.modules.pop("tool2_tech_docs", None)

    import tool2_tech_docs as t2
    return t2


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/model_card.py": "class ModelCard:\n    pass\n",
    "backend/app.py": "from flask import Flask\napp = Flask(__name__)\n",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" {}\n',
    "infra/variables.tf": 'variable "region" { default = "us-east-1" }\n',
}

SAMPLE_DOCS = {
    "README.md": "# My README\nProject overview here.",
    "ARCHITECTURE.md": "# Architecture\nCloud resources here.",
    "RUNBOOK.md": "# Runbook\nOperational notes here.",
}


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================


class TestGenerateDocs:
    def test_returns_three_documents(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        fake_shared.call_claude.return_value = "# Generated Doc"

        result = tool2.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_correct_extensions(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("my-org", "my-repo", "https://run")

        calls = fake_shared.get_repo_files.call_args_list
        # First call: source-code extensions
        first_exts = calls[0][0][2]
        assert ".py" in first_exts
        assert ".js" in first_exts
        assert ".ts" in first_exts
        assert ".go" in first_exts
        # Second call: IaC extensions
        second_exts = calls[1][0][2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts
        assert ".yml" in second_exts

    def test_calls_claude_three_times(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        fake_shared.call_claude.return_value = "# Doc"

        tool2.generate_docs("owner", "repo", "https://run")

        assert fake_shared.call_claude.call_count == 3

    def test_readme_content_comes_from_claude(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        result = tool2.generate_docs("owner", "repo", "https://run")

        assert result["README.md"] == "README content"

    def test_architecture_content_comes_from_claude(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README", "ARCH content", "RUNBOOK"]

        result = tool2.generate_docs("owner", "repo", "https://run")

        assert result["ARCHITECTURE.md"] == "ARCH content"

    def test_runbook_content_comes_from_claude(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README", "ARCH", "RUNBOOK content"]

        result = tool2.generate_docs("owner", "repo", "https://run")

        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_empty_files_uses_no_files_found_placeholder(self, tool2, fake_shared):
        """When get_repo_files returns {}, the formatted string should be '_No files found_'."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run")

        # All three Claude calls should receive '_No files found_' somewhere in their user prompt
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, tool2, fake_shared):
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run")

        # The formatted string must not contain 5000 x's in a row
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "x" * 4001 not in user_prompt

    def test_owner_and_repo_appear_in_claude_prompts(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("acme-corp", "widget-service", "https://run")

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme-corp" in user_prompt
            assert "widget-service" in user_prompt

    def test_claude_exception_propagates(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            tool2.generate_docs("owner", "repo", "https://run")

    def test_get_repo_files_exception_propagates(self, tool2, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError):
            tool2.generate_docs("owner", "repo", "https://run")

    def test_with_sample_py_and_iac_files(self, tool2, fake_shared):
        """Happy path with realistic file data from the synthetic samples."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.side_effect = [
            "# README generated",
            "# ARCHITECTURE generated",
            "# RUNBOOK generated",
        ]

        result = tool2.generate_docs("insurance-co", "underwriting", "https://ci/run/42")

        assert result["README.md"] == "# README generated"
        assert result["ARCHITECTURE.md"] == "# ARCHITECTURE generated"
        assert result["RUNBOOK.md"] == "# RUNBOOK generated"

    def test_max_files_limits_passed_to_get_repo_files(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run")

        calls = fake_shared.get_repo_files.call_args_list
        _, py_kwargs = calls[0]
        _, iac_kwargs = calls[1]
        assert py_kwargs.get("max_files", calls[0][0][2] and 15) in (15, None) or calls[0][0][3] == 15
        # Check positional args for max_files
        py_call_args = calls[0][0]
        iac_call_args = calls[1][0]
        assert py_call_args[3] == 15  # max_files for py/js files
        assert iac_call_args[3] == 10  # max_files for IaC files

    def test_fmt_includes_filename_in_output(self, tool2, fake_shared):
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, {}]
        fake_shared.call_claude.return_value = "content"

        tool2.generate_docs("owner", "repo", "https://run")

        readme_call = fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "backend/model_card.py" in user_prompt
        assert "backend/app.py" in user_prompt


# ===========================================================================
# Tests for build_index()
# ===========================================================================


class TestBuildIndex:
    def test_returns_string(self, tool2):
        result = tool2.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, tool2):
        result = tool2.build_index("acme", "widget", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "widget" in result

    def test_contains_generated_timestamp(self, tool2):
        now = "2024-06-30 12:34 UTC"
        result = tool2.build_index("owner", "repo", SAMPLE_DOCS, now)
        assert now in result

    def test_contains_links_to_all_documents(self, tool2):
        result = tool2.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, tool2):
        result = tool2.build_index("src-owner", "src-repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_correct_path_structure(self, tool2):
        result = tool2.build_index("myorg", "myrepo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        expected_fragment = "tech-docs/myorg-myrepo/README.md"
        assert expected_fragment in result

    def test_links_are_valid_github_urls(self, tool2):
        result = tool2.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "https://github.com/" in result

    def test_contains_auto_generated_footer(self, tool2):
        result = tool2.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_empty_docs_dict(self, tool2):
        result = tool2.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)
        assert "owner" in result
        assert "repo" in result

    def test_single_document(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" not in result

    def test_header_format(self, tool2):
        result = tool2.build_index("owner", "repo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")