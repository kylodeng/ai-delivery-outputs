"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates fetching repo files and calling Claude for README, ARCHITECTURE, RUNBOOK
- build_index(): constructs the markdown index page with correct links and timestamp
- __main__ block: happy path (docs written, email sent, audit logged) and failure path (audit + email on exception)

Mocks used:
- shared.call_claude           → avoids real Anthropic API calls
- shared.get_repo_files        → avoids real GitHub API calls
- shared.write_output_file     → avoids real GitHub output repo writes
- shared.send_email            → avoids real SES / SMTP calls
- shared.email_html            → pure helper, also mocked for isolation
- shared.write_audit_entry     → avoids real audit log writes
- shared.OUTPUT_REPO_OWNER     → patched to a known test value
- shared.OUTPUT_REPO           → patched to a known test value
- datetime.datetime.utcnow     → deterministic timestamps in __main__ tests

TODOs:
- TODO: Integration test against a real (sandboxed) GitHub repo once credentials available
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env-vars are absent (None values)
"""

import sys
import os
import importlib
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so we never import the real one
# ---------------------------------------------------------------------------

def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude         = MagicMock(return_value="# Generated doc")
    shared.get_repo_files      = MagicMock(return_value={})
    shared.write_output_file   = MagicMock(return_value="https://github.com/out/repo/blob/main/file.md")
    shared.send_email          = MagicMock()
    shared.email_html          = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry   = MagicMock()
    shared.OUTPUT_REPO_OWNER   = "test-output-owner"
    shared.OUTPUT_REPO         = "test-output-repo"
    return shared


def _load_tool2(fake_shared):
    """Import (or re-import) tool2_tech_docs with the fake shared module injected."""
    sys.modules["shared"] = fake_shared
    # Remove cached version so we always get a fresh import with our fake shared
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Ensure the scripts directory is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also allow import from the same directory as this test file (CI layout)
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    import tool2_tech_docs
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_shared():
    return _make_fake_shared()


@pytest.fixture()
def tool2(fake_shared):
    return _load_tool2(fake_shared)


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, tool2, fake_shared):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content2"}
        result = tool2.build_index("myowner", "myrepo", docs, "2024-01-15 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_repo_header(self, tool2, fake_shared):
        docs = {"README.md": "x"}
        result = tool2.build_index("acme", "backend", docs, "2024-01-15 12:00 UTC")
        assert "acme/backend" in result

    def test_contains_generated_timestamp(self, tool2, fake_shared):
        docs = {"README.md": "x"}
        now = "2024-06-30 09:45 UTC"
        result = tool2.build_index("acme", "backend", docs, now)
        assert now in result

    def test_links_use_output_repo_owner_and_repo(self, tool2, fake_shared):
        docs = {"README.md": "x"}
        result = tool2.build_index("src-owner", "src-repo", docs, "2024-01-01 00:00 UTC")
        assert fake_shared.OUTPUT_REPO_OWNER in result
        assert fake_shared.OUTPUT_REPO in result

    def test_all_doc_names_appear_as_links(self, tool2, fake_shared):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = tool2.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_link_path_contains_owner_repo_folder(self, tool2, fake_shared):
        docs = {"README.md": "a"}
        result = tool2.build_index("myowner", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/myowner-myrepo/README.md" in result

    def test_empty_docs_dict(self, tool2, fake_shared):
        result = tool2.build_index("o", "r", {}, "2024-01-01 00:00 UTC")
        # Should still produce a valid string with header
        assert "o/r" in result
        assert "Documents" in result

    def test_auto_generated_footer_present(self, tool2, fake_shared):
        docs = {"README.md": "x"}
        result = tool2.build_index("o", "r", docs, "now")
        assert "Auto-generated" in result

    def test_multiple_docs_all_linked(self, tool2, fake_shared):
        docs = {f"DOC{i}.md": f"content{i}" for i in range(5)}
        result = tool2.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_link_format_is_github_url(self, tool2, fake_shared):
        docs = {"README.md": "x"}
        result = tool2.build_index("o", "r", docs, "t")
        assert "https://github.com/" in result


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_dict_with_three_keys(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# doc"
        result = tool2.generate_docs("owner", "repo", "https://run-url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_get_repo_files_called_twice(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool2.generate_docs("owner", "repo", "https://run-url")
        assert fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_py_extensions(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool2.generate_docs("owner", "repo", "https://run-url")
        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2] if first_call_args[0] else first_call_args[1].get("extensions", first_call_args[0][2])
        # Check positional args
        args, kwargs = first_call_args
        exts = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in exts

    def test_get_repo_files_second_call_iac_extensions(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool2.generate_docs("owner", "repo", "https://run-url")
        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        args, kwargs = second_call_args
        exts = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".tf" in exts or ".yaml" in exts or ".yml" in exts

    def test_call_claude_called_three_times(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# doc"
        tool2.generate_docs("owner", "repo", "https://run-url")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_value_is_claude_output(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Generated README"
        result = tool2.generate_docs("owner", "repo", "https://run-url")
        assert result["README.md"] == "# Generated README"

    def test_architecture_value_is_claude_output(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        call_responses = ["# README", "# ARCHITECTURE", "# RUNBOOK"]
        fake_shared.call_claude.side_effect = call_responses
        result = tool2.generate_docs("owner", "repo", "https://run-url")
        assert result["ARCHITECTURE.md"] == "# ARCHITECTURE"

    def test_runbook_value_is_claude_output(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        call_responses = ["# README", "# ARCHITECTURE", "# RUNBOOK"]
        fake_shared.call_claude.side_effect = call_responses
        result = tool2.generate_docs("owner", "repo", "https://run-url")
        assert result["RUNBOOK.md"] == "# RUNBOOK"

    def test_owner_repo_in_claude_prompt(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc"
        tool2.generate_docs("acme-corp", "underwriting-api", "https://run-url")
        all_prompts = " ".join(str(c) for c in fake_shared.call_claude.call_args_list)
        assert "acme-corp" in all_prompts
        assert "underwriting-api" in all_prompts

    def test_with_actual_source_files(self, tool2, fake_shared):
        """Test with synthetic file data resembling the actual repo structure."""
        py_files = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "..."}}',
        }
        iac_files = {
            "infra/main.tf": 'resource "aws_s3_bucket" "docs" {}',
        }
        fake_shared.get_repo_files.side_effect = [py_files, iac_files]
        fake_shared.call_claude.return_value = "# doc"
        result = tool2.generate_docs("owner", "repo", "https://run-url")
        assert "README.md" in result

    def test_empty_files_produces_no_files_found_message(self, tool2, fake_shared):
        """When no files are returned, fmt() should produce '_No files found_'."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# doc"
        # Should not raise
        result = tool2.generate_docs("owner", "repo", "https://run-url")
        assert isinstance(result, dict)
        # Verify _No files found_ was passed to at least one call_claude invocation
        all_prompts = " ".join(str(c) for c in fake_shared.call_claude.call_args_list)
        assert "_No files found_" in all_prompts

    def test_call_claude_raises_propagates(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")
        with pytest.raises(RuntimeError, match="Claude API error"):
            tool2.generate_docs("owner", "repo", "https://run-url")

    def test_get_repo_files_raises_propagates(self, tool2, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub API down")
        with pytest.raises(ConnectionError, match="GitHub API down"):
            tool2.generate_docs("owner", "repo", "https://run-url")

    def test_file_content_truncated_to_4000_chars(self, tool2, fake_shared):
        """Files with >4000 chars should be truncated in the prompt."""
        long_content = "x" * 10_000
        py_files = {"backend/big_file.py": long_content}
        fake_shared.get_repo_files.side_effect = [py_files, {}]
        fake_shared.call_claude.return_value = "# doc"
        tool2.generate_docs("owner", "repo", "https://run-url")
        # The prompt passed to the first call_claude should not contain 10000 x's
        first_call = fake_shared.call_claude.call_args_list[0]
        prompt_str = str(first_call)
        # 4000 x's truncated, so the prompt has at most 4000 of them
        assert "x" * 4001 not in prompt_str

    def test_max_files_limit_passed_correctly(self, tool2, fake_shared):
        """get_repo_files should be called with max_files keyword."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# doc"
        tool2.generate_docs("owner", "repo", "https://run-url")
        for c in fake_shared.get_repo_files.call_args_list:
            args, kwargs = c
            max_files_val = kwargs.get("max_files") or (args[3] if len(args) > 3 else None)
            assert max_files_val is not None

    @pytest.mark.parametrize("owner,repo", [
        ("single", "repo"),
        ("org-with-dashes", "repo-with-dashes"),
        ("UPPERCASE", "MixedCase"),
        ("o", "r"),
    ])
    def test_various_owner_repo_combinations(self, owner, repo, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# doc"
        result = tool2.generate_docs(owner, repo, "https://run-url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}


# ---------------------------------------------------------------------------
# Tests for fmt() helper (via generate_docs behaviour)
# ---------------------------------------------------------------------------

class TestFmtHelper:
    """fmt() is a