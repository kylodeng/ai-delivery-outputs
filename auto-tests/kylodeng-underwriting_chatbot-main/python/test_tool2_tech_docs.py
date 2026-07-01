"""
Test module for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates calls to get_repo_files and call_claude to produce docs dict
    - build_index(): builds a markdown index page from docs dict
    - __main__ block behaviour: happy path (writes files, sends email, audits) and failure path

Mocks used:
    - shared.call_claude          — prevents real API calls to Claude/Anthropic
    - shared.get_repo_files       — prevents real GitHub API calls
    - shared.write_output_file    — prevents real file writes to output repo
    - shared.send_email           — prevents real email delivery
    - shared.email_html           — prevents real HTML rendering dependency
    - shared.write_audit_entry    — prevents real audit log writes
    - datetime.datetime           — pinned for deterministic timestamp assertions
    - os.environ                  — controlled via monkeypatch

TODOs:
    - TODO: Integration test against a real (or sandbox) GitHub repo — needs credentials
    - TODO: Test behaviour when call_claude returns None or empty string
    - TODO: Test max_files edge-case for get_repo_files when repo has > 15 source files
"""

import importlib
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers to import the module under test with all shared deps mocked out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-org"
FAKE_OUTPUT_REPO = "docs-repo"

def _make_shared_mock():
    """Return a minimal mock of the shared module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/ai-org/docs-repo/blob/main/tech-docs/owner-repo/README.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module(shared_mock=None):
    """Import tool2_tech_docs fresh, injecting a shared mock."""
    # Remove any cached version
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]

    if shared_mock is None:
        shared_mock = _make_shared_mock()

    sys.modules["shared"] = shared_mock

    # The script does sys.path.insert inside itself; we need the scripts dir on path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    spec = importlib.util.spec_from_file_location(
        "tool2_tech_docs",
        os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def module_and_shared(shared_mock):
    mod, sm = _import_module(shared_mock)
    return mod, sm


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {"src/main.py": "print('hello')"}
        sm.call_claude.return_value = "# Doc content"

        result = mod.generate_docs("my-owner", "my-repo", "https://github.com/actions/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "# content"

        mod.generate_docs("owner", "repo", "https://run-url")

        assert sm.call_claude.call_count == 3

    def test_get_repo_files_called_for_source_and_iac(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "https://run-url")

        calls = sm.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call: source files
        first_extensions = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_extensions
        assert ".js" in first_extensions
        assert ".ts" in first_extensions
        assert ".go" in first_extensions

        # Second call: IaC files
        second_extensions = calls[1][0][2]
        assert ".tf" in second_extensions
        assert ".yaml" in second_extensions
        assert ".yml" in second_extensions

    def test_max_files_limits_enforced(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "x"

        mod.generate_docs("owner", "repo", "url")

        source_call_kwargs = sm.get_repo_files.call_args_list[0]
        iac_call_kwargs = sm.get_repo_files.call_args_list[1]

        # max_files keyword or positional arg
        assert source_call_kwargs[1].get("max_files", source_call_kwargs[0][3] if len(source_call_kwargs[0]) > 3 else None) == 15
        assert iac_call_kwargs[1].get("max_files", iac_call_kwargs[0][3] if len(iac_call_kwargs[0]) > 3 else None) == 10

    def test_readme_prompt_contains_repo_name(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "readme"

        mod.generate_docs("acme", "backend", "url")

        readme_user_msg = sm.call_claude.call_args_list[0][0][1]
        assert "acme/backend" in readme_user_msg

    def test_architecture_prompt_contains_repo_name(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "arch"

        mod.generate_docs("acme", "backend", "url")

        arch_user_msg = sm.call_claude.call_args_list[1][0][1]
        assert "acme/backend" in arch_user_msg

    def test_runbook_prompt_contains_repo_name(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "runbook"

        mod.generate_docs("acme", "backend", "url")

        runbook_user_msg = sm.call_claude.call_args_list[2][0][1]
        assert "acme/backend" in runbook_user_msg

    def test_doc_content_matches_claude_return_value(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        def _side_effect(system, user):
            if "README" in system:
                return "# README content"
            if "architecture" in system:
                return "# ARCH content"
            return "# RUNBOOK content"

        sm.call_claude.side_effect = _side_effect

        result = mod.generate_docs("o", "r", "url")
        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_files_included_in_prompt(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {"src/app.py": "def main(): pass"}
        sm.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "url")

        # The readme prompt should reference the file path
        readme_prompt = sm.call_claude.call_args_list[0][0][1]
        assert "src/app.py" in readme_prompt

    def test_no_files_produces_no_files_found_placeholder(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "url")

        readme_prompt = sm.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, module_and_shared):
        mod, sm = module_and_shared
        long_content = "x" * 10_000
        sm.get_repo_files.return_value = {"big_file.py": long_content}
        sm.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "url")

        readme_prompt = sm.call_claude.call_args_list[0][0][1]
        # The prompt should contain at most 4000 x's in a row (truncation)
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_system_prompts_passed_correctly(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "c"

        mod.generate_docs("owner", "repo", "url")

        systems = [c[0][0] for c in sm.call_claude.call_args_list]
        # Each system prompt is one of the three defined constants
        assert systems[0] == mod.SYSTEM_README
        assert systems[1] == mod.SYSTEM_ARCH
        assert systems[2] == mod.SYSTEM_RUNBOOK

    def test_call_claude_raises_propagates(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            mod.generate_docs("owner", "repo", "url")

    def test_get_repo_files_raises_propagates(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        sm.call_claude.return_value = "content"

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("owner", "repo", "url")

    def test_multiple_files_all_appear_in_prompt(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {
            "src/a.py": "code_a",
            "src/b.py": "code_b",
            "src/c.go": "code_c",
        }
        sm.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "url")

        readme_prompt = sm.call_claude.call_args_list[0][0][1]
        assert "src/a.py" in readme_prompt
        assert "src/b.py" in readme_prompt
        assert "src/c.go" in readme_prompt

    def test_iac_files_appear_in_arch_prompt_but_not_exclusively_in_readme(self, module_and_shared):
        mod, sm = module_and_shared

        def fake_get_repo_files(owner, repo, extensions, max_files):
            if ".tf" in extensions:
                return {"infra/main.tf": "resource aws_s3_bucket {}"}
            return {"src/app.py": "app code"}

        sm.get_repo_files.side_effect = fake_get_repo_files
        sm.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "url")

        arch_prompt = sm.call_claude.call_args_list[1][0][1]
        assert "infra/main.tf" in arch_prompt

    @pytest.mark.parametrize("owner,repo", [
        ("my-org", "backend"),
        ("acme", "frontend"),
        ("underwriting-ai", "model-service"),
    ])
    def test_generate_docs_various_owner_repo(self, module_and_shared, owner, repo):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "generated"

        result = mod.generate_docs(owner, repo, "url")

        assert isinstance(result, dict)
        assert len(result) == 3
        for content in result.values():
            assert content == "generated"


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, module_and_shared):
        mod, _ = module_and_shared
        result = mod.build_index("owner", "repo", {"README.md": ""}, "2024-01-01 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_repo_in_heading(self, module_and_shared):
        mod, _ = module_and_shared
        result = mod.build_index("acme", "backend", {"README.md": ""}, "2024-01-01 12:00 UTC")
        assert "acme/backend" in result

    def test_contains_timestamp(self, module_and_shared):
        mod, _ = module_and_shared
        result = mod.build_index("owner", "repo", {"README.md": ""}, "2024-06-15 10:30 UTC")
        assert "2024-06-15 10:30 UTC" in result

    def test_contains_links_for_all_docs(self, module_and_shared):
        mod, _ = module_and_shared
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = mod.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_correct_github_url(self, module_and_shared):
        mod, _ = module_and_shared
        docs = {"README.md": "content"}
        result = mod.build_index("owner", "repo", docs, "2