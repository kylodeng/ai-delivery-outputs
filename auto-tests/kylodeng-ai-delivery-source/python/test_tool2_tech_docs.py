"""
Test suite for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates calls to get_repo_files and call_claude
    - build_index(): constructs a markdown index page with correct links and metadata
    - __main__ block: environment-driven entry point, success and failure paths

Mocks used:
    - shared.call_claude          — avoids real Anthropic/Claude API calls
    - shared.get_repo_files       — avoids real GitHub API calls
    - shared.write_output_file    — avoids real GitHub write operations
    - shared.send_email           — avoids real SMTP/SES calls
    - shared.email_html           — avoids dependency on shared helper HTML builder
    - shared.write_audit_entry    — avoids real audit-log writes
    - datetime.datetime.utcnow    — pins timestamps for deterministic assertions
    - os.environ                  — controlled via monkeypatch

TODOs:
    - TODO: Integration test exercising the real shared.call_claude when a live API key is available
    - TODO: Test file-content truncation boundary (files > 4000 chars) once shared helpers are testable
    - TODO: Verify exact commit message format accepted by write_output_file
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared replaced by a mock
# ---------------------------------------------------------------------------

OUTPUT_REPO_OWNER_VAL = "test-output-owner"
OUTPUT_REPO_VAL = "test-output-repo"


def _make_shared_mock():
    """Return a mock module that stands in for `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/url")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = OUTPUT_REPO_OWNER_VAL
    shared.OUTPUT_REPO = OUTPUT_REPO_VAL
    return shared


def _import_module(shared_mock=None):
    """
    Import (or reimport) tool2_tech_docs with the supplied shared mock injected.
    Returns the module object.
    """
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Ensure .github/scripts is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also accept that the file lives next to this test in the same directory
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    sys.modules["shared"] = shared_mock

    # Remove cached copy so reimport picks up new mock
    sys.modules.pop("tool2_tech_docs", None)

    import tool2_tech_docs as mod
    return mod, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    mock = _make_shared_mock()
    yield mock
    # Cleanup
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules.pop("shared", None)


@pytest.fixture()
def mod(shared_mock):
    module, _ = _import_module(shared_mock)
    return module


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:
    """Tests for build_index()"""

    def test_returns_string(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_repo_in_header(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": "x"}, "2024-06-01 12:00 UTC")
        assert "acme/myrepo" in result

    def test_contains_generated_timestamp(self, mod):
        result = mod.build_index("acme", "myrepo", {"README.md": "x"}, "2024-06-01 12:00 UTC")
        assert "2024-06-01 12:00 UTC" in result

    def test_contains_all_doc_links(self, mod):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = mod.build_index("acme", "myrepo", docs, "2024-06-01 12:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_point_to_output_repo(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "myrepo", docs, "2024-06-01 12:00 UTC")
        expected_fragment = f"https://github.com/{OUTPUT_REPO_OWNER_VAL}/{OUTPUT_REPO_VAL}/blob/main/tech-docs/acme-myrepo/README.md"
        assert expected_fragment in result

    def test_link_uses_owner_repo_path_segment(self, mod):
        docs = {"RUNBOOK.md": "r"}
        result = mod.build_index("my-owner", "my-repo", docs, "now")
        assert "my-owner-my-repo/RUNBOOK.md" in result

    def test_contains_auto_generated_footer(self, mod):
        result = mod.build_index("o", "r", {"README.md": "c"}, "t")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_empty_docs_produces_no_links(self, mod):
        result = mod.build_index("o", "r", {}, "now")
        # Should still produce a valid string without crashing
        assert "# Tech Documentation Index" in result

    def test_multiple_docs_each_have_link(self, mod):
        docs = {f"DOC{i}.md": f"content{i}" for i in range(5)}
        result = mod.build_index("o", "r", docs, "now")
        for name in docs:
            assert name in result

    @pytest.mark.parametrize("owner,repo", [
        ("alice", "project"),
        ("org-name", "repo-name"),
        ("UPPER", "CASE"),
    ])
    def test_parameterised_owner_repo(self, mod, owner, repo):
        result = mod.build_index(owner, repo, {"README.md": "x"}, "2024-01-01")
        assert f"{owner}/{repo}" in result


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:
    """Tests for generate_docs()"""

    def test_returns_dict_with_three_keys(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = "# Doc"

        result = mod.generate_docs("owner", "repo", "https://run.url")

        assert isinstance(result, dict)
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        assert shared_mock.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_py_extensions(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        calls = shared_mock.get_repo_files.call_args_list
        py_call_extensions = calls[0][0][2]  # positional arg index 2
        assert ".py" in py_call_extensions

    def test_get_repo_files_called_with_iac_extensions(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        calls = shared_mock.get_repo_files.call_args_list
        iac_call_extensions = calls[1][0][2]
        assert ".tf" in iac_call_extensions

    def test_calls_call_claude_three_times(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        assert shared_mock.call_claude.call_count == 3

    def test_readme_content_comes_from_call_claude(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.return_value = "# Custom README"
        docs = mod.generate_docs("owner", "repo", "https://run.url")
        assert docs["README.md"] == "# Custom README"

    def test_architecture_content_comes_from_call_claude(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        responses = ["README content", "ARCH content", "RUNBOOK content"]
        shared_mock.call_claude.side_effect = responses
        docs = mod.generate_docs("owner", "repo", "https://run.url")
        assert docs["ARCHITECTURE.md"] == "ARCH content"

    def test_runbook_content_comes_from_call_claude(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        responses = ["README content", "ARCH content", "RUNBOOK content"]
        shared_mock.call_claude.side_effect = responses
        docs = mod.generate_docs("owner", "repo", "https://run.url")
        assert docs["RUNBOOK.md"] == "RUNBOOK content"

    def test_owner_repo_included_in_claude_prompts(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("acme", "myapp", "https://run.url")
        for c in shared_mock.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme/myapp" in user_prompt

    def test_file_content_included_in_readme_prompt(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {"main.py": "print('hello')"}
        mod.generate_docs("owner", "repo", "https://run.url")
        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "main.py" in user_prompt

    def test_iac_content_included_in_arch_prompt(self, mod, shared_mock):
        def fake_get_repo_files(owner, repo, exts, max_files=10):
            if ".tf" in exts:
                return {"main.tf": "resource aws_s3_bucket {}"}
            return {}
        shared_mock.get_repo_files.side_effect = fake_get_repo_files
        mod.generate_docs("owner", "repo", "https://run.url")
        arch_call = shared_mock.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "main.tf" in user_prompt

    def test_no_files_produces_no_files_found_string(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_call_claude_raises_propagates(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.side_effect = RuntimeError("Claude API error")
        with pytest.raises(RuntimeError, match="Claude API error"):
            mod.generate_docs("owner", "repo", "https://run.url")

    def test_get_repo_files_raises_propagates(self, mod, shared_mock):
        shared_mock.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("owner", "repo", "https://run.url")

    def test_file_content_truncated_to_4000_chars(self, mod, shared_mock):
        long_content = "x" * 5000
        shared_mock.get_repo_files.return_value = {"big.py": long_content}
        mod.generate_docs("owner", "repo", "https://run.url")
        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The prompt should NOT contain 5000 x's — it must be truncated
        assert "x" * 4001 not in user_prompt
        assert "x" * 4000 in user_prompt

    def test_max_files_limit_passed_for_source_files(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        source_call = shared_mock.get_repo_files.call_args_list[0]
        assert source_call[1].get("max_files") == 15 or source_call[0][-1] == 15

    def test_max_files_limit_passed_for_iac_files(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "https://run.url")
        iac_call = shared_mock.get_repo_files.call_args_list[1]
        assert iac_call[1].get("max_files") == 10 or iac_call[0][-1] == 10


# ---------------------------------------------------------------------------
# Tests: fmt helper (via generate_docs side effects)
# ---------------------------------------------------------------------------

class TestFmtHelper:
    """Indirectly test the internal fmt() closure through generate_docs."""

    def test_multiple_files_joined_with_double_newline(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {
            "a.py": "content_a",
            "b.py": "content_b",
        }
        mod.generate_docs("owner", "repo", "https://run.url")
        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "### a.py" in user_prompt
        assert "### b.py" in user_prompt

    def test_file_path_appears_as_header(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {"src/main.go": "package main"}
        mod.generate_docs("owner", "repo", "https://run.url")
        readme_call