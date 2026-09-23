"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates fetching repo files and calling Claude for README, ARCHITECTURE, RUNBOOK
- build_index(): builds a markdown index page with correct links and metadata
- __main__ block: environment variable handling, file writing, email, audit, error path

Mocks used:
- shared.call_claude (patched via sys.modules injection)
- shared.get_repo_files
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- shared.OUTPUT_REPO_OWNER
- shared.OUTPUT_REPO
- datetime.datetime (for deterministic timestamps)

TODOs:
- TODO: Integration test with real Claude API (requires API key + network) — stubbed below
- TODO: Test actual GitHub Actions environment variable injection end-to-end
- TODO: Validate generated markdown structure/sections from Claude (requires real response)
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers: build a fake `shared` module so the import in tool2_tech_docs.py
# succeeds without the real module being present / making real network calls.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a fresh MagicMock-based fake `shared` module."""
    fake = types.ModuleType("shared")
    fake.call_claude = MagicMock(return_value="# Mock Claude Output")
    fake.get_repo_files = MagicMock(return_value={})
    fake.write_output_file = MagicMock(return_value="https://github.com/output/url")
    fake.send_email = MagicMock()
    fake.email_html = MagicMock(return_value="<html>mock</html>")
    fake.write_audit_entry = MagicMock()
    fake.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    fake.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return fake


@pytest.fixture(autouse=True)
def fake_shared_module():
    """
    Install a fake `shared` module before each test and remove it afterwards.
    Also removes tool2_tech_docs from sys.modules so it re-imports fresh each time.
    """
    fake = _make_fake_shared()
    sys.modules["shared"] = fake

    # Remove cached import so the module re-executes with the new fake shared
    sys.modules.pop("tool2_tech_docs", None)

    yield fake

    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool2(fake_shared_module):
    """Import tool2_tech_docs after the fake shared module is in place."""
    import tool2_tech_docs
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Fixtures: reusable data
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_py_files():
    return {
        "main.py": "def hello(): pass",
        "utils.py": "import os\n\ndef read_file(p): return open(p).read()",
    }


@pytest.fixture()
def sample_iac_files():
    return {
        "main.tf": 'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }',
        "serverless.yml": "service: my-service\nprovider:\n  name: aws",
    }


@pytest.fixture()
def sample_docs():
    return {
        "README.md": "# README content",
        "ARCHITECTURE.md": "# ARCHITECTURE content",
        "RUNBOOK.md": "# RUNBOOK content",
    }


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, tool2, fake_shared_module,
                                           sample_py_files, sample_iac_files):
        """generate_docs returns README, ARCHITECTURE, RUNBOOK keys."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        fake_shared_module.call_claude.return_value = "# Generated content"

        result = tool2.generate_docs("my-owner", "my-repo", "https://run.url")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, tool2, fake_shared_module,
                                            sample_py_files, sample_iac_files):
        """Claude should be called exactly once per document type."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("owner", "repo", "https://run.url")

        assert fake_shared_module.call_claude.call_count == 3

    def test_get_repo_files_called_with_correct_extensions(self, tool2, fake_shared_module,
                                                            sample_py_files, sample_iac_files):
        """get_repo_files is called once for source files and once for IaC files."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("owner", "repo", "https://run.url")

        calls = fake_shared_module.get_repo_files.call_args_list
        assert len(calls) == 2

        first_call_extensions = calls[0][0][2]  # positional arg index 2
        second_call_extensions = calls[1][0][2]

        assert ".py" in first_call_extensions
        assert ".js" in first_call_extensions
        assert ".ts" in first_call_extensions
        assert ".go" in first_call_extensions

        assert ".tf" in second_call_extensions
        assert ".yaml" in second_call_extensions
        assert ".yml" in second_call_extensions

    def test_get_repo_files_respects_max_files(self, tool2, fake_shared_module,
                                               sample_py_files, sample_iac_files):
        """max_files kwargs are passed correctly."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("owner", "repo", "https://run.url")

        calls = fake_shared_module.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15
        assert calls[1][1].get("max_files") == 10

    def test_owner_and_repo_in_claude_prompt(self, tool2, fake_shared_module,
                                             sample_py_files, sample_iac_files):
        """Owner and repo name should appear in each Claude prompt."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("acme-org", "my-project", "https://run.url")

        for c in fake_shared_module.call_claude.call_args_list:
            user_msg = c[0][1]  # second positional arg is the user message
            assert "acme-org" in user_msg
            assert "my-project" in user_msg

    def test_correct_system_prompts_used(self, tool2, fake_shared_module,
                                         sample_py_files, sample_iac_files):
        """Each Claude call uses the expected system prompt constant."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("owner", "repo", "https://run.url")

        system_prompts = [c[0][0] for c in fake_shared_module.call_claude.call_args_list]
        assert tool2.SYSTEM_README in system_prompts
        assert tool2.SYSTEM_ARCH in system_prompts
        assert tool2.SYSTEM_RUNBOOK in system_prompts

    def test_empty_files_returns_docs_with_no_files_placeholder(self, tool2, fake_shared_module):
        """When no files are found, fmt() should produce '_No files found_'."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]

        tool2.generate_docs("owner", "repo", "https://run.url")

        # All three Claude calls should receive '_No files found_' somewhere
        for c in fake_shared_module.call_claude.call_args_list:
            user_msg = c[0][1]
            assert "_No files found_" in user_msg

    def test_file_content_truncated_at_4000_chars(self, tool2, fake_shared_module):
        """File content longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 8000
        fake_shared_module.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]

        tool2.generate_docs("owner", "repo", "https://run.url")

        # The README call (first) should contain only up to 4000 'x' chars from that file
        readme_call_msg = fake_shared_module.call_claude.call_args_list[0][0][1]
        # The raw 8000-char string should NOT appear; only 4000 chars max
        assert "x" * 4001 not in readme_call_msg
        assert "x" * 4000 in readme_call_msg

    def test_iac_files_included_in_architecture_prompt(self, tool2, fake_shared_module,
                                                        sample_iac_files):
        """IaC file names should appear in the ARCHITECTURE call."""
        fake_shared_module.get_repo_files.side_effect = [{}, sample_iac_files]

        tool2.generate_docs("owner", "repo", "https://run.url")

        arch_call_msg = fake_shared_module.call_claude.call_args_list[1][0][1]
        assert "main.tf" in arch_call_msg
        assert "serverless.yml" in arch_call_msg

    def test_source_files_included_in_readme_prompt(self, tool2, fake_shared_module,
                                                     sample_py_files):
        """Source file names should appear in the README call."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, {}]

        tool2.generate_docs("owner", "repo", "https://run.url")

        readme_call_msg = fake_shared_module.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_call_msg
        assert "utils.py" in readme_call_msg

    def test_call_claude_exception_propagates(self, tool2, fake_shared_module,
                                              sample_py_files, sample_iac_files):
        """If call_claude raises, the exception should propagate out of generate_docs."""
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        fake_shared_module.call_claude.side_effect = RuntimeError("API failure")

        with pytest.raises(RuntimeError, match="API failure"):
            tool2.generate_docs("owner", "repo", "https://run.url")

    def test_get_repo_files_exception_propagates(self, tool2, fake_shared_module):
        """If get_repo_files raises, the exception should propagate."""
        fake_shared_module.get_repo_files.side_effect = ConnectionError("network error")

        with pytest.raises(ConnectionError, match="network error"):
            tool2.generate_docs("owner", "repo", "https://run.url")

    def test_returned_content_matches_claude_response(self, tool2, fake_shared_module,
                                                       sample_py_files, sample_iac_files):
        """The value in the returned dict should be exactly what Claude returned."""
        responses = iter(["# README", "# ARCH", "# RUNBOOK"])
        fake_shared_module.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        fake_shared_module.call_claude.side_effect = lambda *a, **kw: next(responses)

        result = tool2.generate_docs("owner", "repo", "https://run.url")

        assert result["README.md"] == "# README"
        assert result["ARCHITECTURE.md"] == "# ARCH"
        assert result["RUNBOOK.md"] == "# RUNBOOK"


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_happy_path_contains_all_doc_links(self, tool2):
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = tool2.build_index("owner", "repo", docs, "2024-01-15 10:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("src-owner", "src-repo", docs, "2024-01-15 10:00 UTC")

        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_include_owner_repo_in_path(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("acme", "widget", docs, "2024-01-15 10:00 UTC")

        assert "acme-widget" in result
        assert "README.md" in result

    def test_title_contains_owner_and_repo(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("myorg", "myrepo", docs, "2024-06-01 09:30 UTC")

        assert "myorg/myrepo" in result

    def test_generated_timestamp_appears(self, tool2):
        docs = {"README.md": "x"}
        timestamp = "2024-06-01 09:30 UTC"
        result = tool2.build_index("owner", "repo", docs, timestamp)

        assert timestamp in result

    def test_auto_generated_footer_present(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")

        assert "Auto-generated" in result

    def test_empty_docs_produces_valid_index(self, tool2):
        result = tool2.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")

        assert "Tech Documentation Index" in result
        # No links but should not crash
        assert isinstance(result, str)

    def test_link_format_is_github_url(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")

        assert "https://github.com/" in result

    def test_link_points_to_blob_main(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")

        assert