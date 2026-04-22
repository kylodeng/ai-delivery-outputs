"""
Tests for tool2_tech_docs.py

What is tested:
    - generate_docs(): happy path, empty files, partial files
    - build_index(): happy path, empty docs, multiple docs, URL construction
    - __main__ block: success path, exception/failure path
    - fmt() inner function behaviour (via generate_docs)

Mocks used:
    - shared.call_claude (prevents real API calls to Claude/Anthropic)
    - shared.get_repo_files (prevents real GitHub API calls)
    - shared.write_output_file (prevents real GitHub writes)
    - shared.send_email (prevents real email sending)
    - shared.email_html (prevents template rendering side-effects)
    - shared.write_audit_entry (prevents real audit log writes)
    - datetime.datetime.utcnow (deterministic timestamps)
    - os.environ (controlled environment variables)

TODOs:
    - TODO: Integration test requiring a real Claude API key + GitHub token
    - TODO: Test for rate-limiting / retry behaviour in call_claude (needs shared internals)
    - TODO: Test for max_files truncation behaviour (needs get_repo_files internals)
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
# Helpers to import the module under test with mocked shared dependencies
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-output-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_mock():
    """Return a MagicMock that looks like the `shared` module."""
    shared = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_tool(shared_mock=None):
    """
    Import (or re-import) tool2_tech_docs with a mocked `shared` module.
    Returns (module, shared_mock).
    """
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Patch sys.path insertion so we don't need the real shared module on disk
    with patch.dict("sys.modules", {"shared": shared_mock}):
        # Force a fresh import every time
        if "tool2_tech_docs" in sys.modules:
            del sys.modules["tool2_tech_docs"]

        # The file lives in .github/scripts/ — add it to path temporarily
        scripts_dir = os.path.join(
            os.path.dirname(__file__), "..", ".github", "scripts"
        )
        scripts_dir = os.path.abspath(scripts_dir)

        original_path = sys.path[:]
        sys.path.insert(0, scripts_dir)
        try:
            import tool2_tech_docs as module
        finally:
            sys.path[:] = original_path

    return module, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def tool(shared_mock):
    """Provides (module, shared_mock) with fresh import."""
    with patch.dict("sys.modules", {"shared": shared_mock}):
        scripts_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
        )
        original_path = sys.path[:]
        sys.path.insert(0, scripts_dir)
        try:
            if "tool2_tech_docs" in sys.modules:
                del sys.modules["tool2_tech_docs"]
            import tool2_tech_docs as mod
        finally:
            sys.path[:] = original_path
        yield mod, shared_mock


# ---------------------------------------------------------------------------
# Tests: generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    """Tests for the generate_docs() public function."""

    def test_happy_path_calls_claude_three_times(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = [
            {"src/main.py": "print('hello')"},   # py/js/ts/go files
            {"infra/main.tf": "resource {}"},     # iac files
        ]
        smock.call_claude.side_effect = [
            "# README content",
            "# ARCHITECTURE content",
            "# RUNBOOK content",
        ]

        result = mod.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert smock.call_claude.call_count == 3
        assert result == {
            "README.md": "# README content",
            "ARCHITECTURE.md": "# ARCHITECTURE content",
            "RUNBOOK.md": "# RUNBOOK content",
        }

    def test_get_repo_files_called_with_correct_extensions(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = [{}, {}]
        smock.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "http://example.com")

        first_call_args = smock.get_repo_files.call_args_list[0]
        second_call_args = smock.get_repo_files.call_args_list[1]

        # First call: source file extensions
        assert first_call_args[0][2] == [".py", ".js", ".ts", ".go"]
        assert first_call_args[1]["max_files"] == 15

        # Second call: IaC extensions
        assert second_call_args[0][2] == [".tf", ".bicep", ".json", ".yaml", ".yml"]
        assert second_call_args[1]["max_files"] == 10

    def test_empty_files_produces_no_files_found_string(self, tool):
        """When get_repo_files returns empty dicts, fmt() should return '_No files found_'."""
        mod, smock = tool

        smock.get_repo_files.side_effect = [{}, {}]
        smock.call_claude.return_value = "generated"

        mod.generate_docs("owner", "repo", "http://run")

        # All three claude calls should receive '_No files found_' in user prompt
        for c in smock.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_files_are_truncated_to_4000_chars(self, tool):
        mod, smock = tool

        long_content = "x" * 10_000
        smock.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        smock.call_claude.return_value = "ok"

        mod.generate_docs("owner", "repo", "http://run")

        # The user prompt must NOT contain more than 4000 chars of the file content
        readme_prompt = smock.call_claude.call_args_list[0][0][1]
        # Content should be truncated: only 4000 x's appear inside the code block
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_correct_owner_and_repo_in_prompts(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = [{}, {}]
        smock.call_claude.return_value = "ok"

        mod.generate_docs("my-owner", "my-repo", "http://run")

        for c in smock.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "my-owner/my-repo" in user_prompt

    def test_correct_system_prompts_passed(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = [{}, {}]
        smock.call_claude.return_value = "ok"

        mod.generate_docs("owner", "repo", "http://run")

        system_prompts = [c[0][0] for c in smock.call_claude.call_args_list]
        assert mod.SYSTEM_README in system_prompts
        assert mod.SYSTEM_ARCH in system_prompts
        assert mod.SYSTEM_RUNBOOK in system_prompts

    def test_multiple_source_files_all_included_in_prompt(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = [
            {"a.py": "code_a", "b.py": "code_b"},
            {},
        ]
        smock.call_claude.return_value = "ok"

        mod.generate_docs("owner", "repo", "http://run")

        readme_prompt = smock.call_claude.call_args_list[0][0][1]
        assert "a.py" in readme_prompt
        assert "b.py" in readme_prompt
        assert "code_a" in readme_prompt
        assert "code_b" in readme_prompt

    def test_call_claude_exception_propagates(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = [{}, {}]
        smock.call_claude.side_effect = RuntimeError("Claude unavailable")

        with pytest.raises(RuntimeError, match="Claude unavailable"):
            mod.generate_docs("owner", "repo", "http://run")

    def test_get_repo_files_exception_propagates(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("owner", "repo", "http://run")

    def test_iac_files_used_in_architecture_prompt(self, tool):
        mod, smock = tool

        smock.get_repo_files.side_effect = [
            {"src/app.py": "app code"},
            {"infra/main.tf": "terraform code"},
        ]
        smock.call_claude.return_value = "ok"

        mod.generate_docs("owner", "repo", "http://run")

        arch_prompt = smock.call_claude.call_args_list[1][0][1]
        assert "terraform code" in arch_prompt
        assert "infra/main.tf" in arch_prompt

    def test_run_url_parameter_accepted(self, tool):
        """run_url is accepted by generate_docs without error (currently unused inside)."""
        mod, smock = tool

        smock.get_repo_files.side_effect = [{}, {}]
        smock.call_claude.return_value = "ok"

        # Should not raise
        result = mod.generate_docs("owner", "repo", "https://custom.run/url/123")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Tests: build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    """Tests for the build_index() public function."""

    def test_happy_path_contains_all_doc_links(self, tool):
        mod, smock = tool

        docs = {
            "README.md": "...",
            "ARCHITECTURE.md": "...",
            "RUNBOOK.md": "...",
        }
        result = mod.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, tool):
        mod, smock = tool

        docs = {"README.md": "content"}
        result = mod.build_index("source-owner", "source-repo", docs, "2024-01-15 10:00 UTC")

        expected_base = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}/blob/main/"
            "tech-docs/source-owner-source-repo/README.md"
        )
        assert expected_base in result

    def test_timestamp_included_in_output(self, tool):
        mod, smock = tool

        now = "2024-06-30 23:59 UTC"
        result = mod.build_index("owner", "repo", {"README.md": ""}, now)

        assert now in result

    def test_owner_and_repo_in_title(self, tool):
        mod, smock = tool

        result = mod.build_index("my-owner", "my-repo", {"README.md": ""}, "now")

        assert "my-owner/my-repo" in result

    def test_auto_generated_footer_present(self, tool):
        mod, smock = tool

        result = mod.build_index("o", "r", {"README.md": ""}, "now")

        assert "Auto-generated" in result

    def test_empty_docs_produces_empty_links_section(self, tool):
        mod, smock = tool

        result = mod.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")

        # Should still produce a valid markdown file, just with no links
        assert "# Tech Documentation Index" in result
        assert "2024-01-01 00:00 UTC" in result

    def test_multiple_docs_produce_multiple_links(self, tool):
        mod, smock = tool

        docs = {
            "README.md": "",
            "ARCHITECTURE.md": "",
            "RUNBOOK.md": "",
        }
        result = mod.build_index("owner", "repo", docs, "now")

        assert result.count("https://github.com") == 3

    def test_path_uses_owner_dash_repo_format(self, tool):
        mod, smock = tool

        result = mod.build_index("alpha", "beta", {"README.md": ""}, "now")

        assert "tech-docs/alpha-beta/README.md" in result

    def test_returns_string(self, tool):
        mod, smock = tool

        result = mod.build_index("o", "r", {"README.md": ""}, "now")
        assert isinstance(result, str)

    @pytest.mark.parametrize("owner,repo", [
        ("simple", "repo"),
        ("org-with-dashes", "repo-with-dashes"),
        ("UPPER", "CASE"),
        ("123numeric", "456repo"),
    ])
    def test_various_owner_repo_formats(self, tool, owner, repo):
        mod, smock = tool

        result = mod.build_index(owner, repo, {"README.md": ""}, "now")
        assert owner in result
        assert repo in result


# ---------------------------------------------------------------------------
# Tests: SYSTEM_* prompt constants
# ---------------------------------------------------------------------------


class TestSystemPromptConstants:
    """Sanity checks on the system prompt strings."""

    def test_system_readme_is_non_empty_string(self, tool):
        mod, _ = tool
        assert isinstance(mod.SYSTEM_README, str)
        assert len(mod.SYSTEM_README) > 50

    def test_system_arch_is_non_empty_string(self, tool):
        mod, _ = tool
        assert isinstance(mod.SYSTEM_ARCH, str)
        assert len(mod.SYSTEM_ARCH) > 50

    def test_system_runbook_is_non_empty_string(self, tool):
        mod, _ = tool
        assert isinstance(mod.SYSTEM_RUNBOOK, str)
        assert len(mod.SYSTEM_RUNBOOK) > 50

    def test_system_readme_contains_required_sections(self, tool):
        mod, _ = tool
        for keyword in ["README", "Tech stack", "Architecture", "Environment variables"]:
            assert keyword in mod.SYSTEM_README

    def test_system_arch_contains_required_sections(self, tool):
        mod, _ = tool
        for keyword in ["Resources deployed", "Data flow", "Security", "Deployment"