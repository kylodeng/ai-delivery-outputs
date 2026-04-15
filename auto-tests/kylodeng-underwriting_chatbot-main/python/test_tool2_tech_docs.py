"""
Tests for tool2_tech_docs.py
============================
What is tested:
  - generate_docs(): orchestration of get_repo_files + call_claude calls
  - build_index(): correct markdown output, link construction, timestamp embedding
  - __main__ block: success path (docs written, email sent, audit entry written)
  - __main__ block: failure path (exception handling, failure audit/email)
  - fmt() helper (via generate_docs side-effects)
  - Edge cases: empty file dicts, missing env vars, Claude returning empty strings

Mocks used:
  - shared.call_claude          — prevents real Anthropic API calls
  - shared.get_repo_files       — prevents real GitHub API calls
  - shared.write_output_file    — prevents real GitHub commits
  - shared.send_email           — prevents real email sending
  - shared.email_html           — prevents template rendering side-effects
  - shared.write_audit_entry    — prevents real audit writes
  - shared.OUTPUT_REPO_OWNER    — controlled constant
  - shared.OUTPUT_REPO          — controlled constant
  - datetime.datetime.utcnow    — deterministic timestamps

TODOs:
  # TODO: Integration test against a real (sandbox) GitHub repo once credentials available
  # TODO: Test behaviour when call_claude raises an anthropic.APIError subclass
  # TODO: Test concurrent generate_docs calls (thread-safety of shared state)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared stubbed out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal fake 'shared' module so tool2_tech_docs can be imported."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output-url")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module(shared_stub=None):
    """Import (or re-import) tool2_tech_docs with the given shared stub in sys.modules."""
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Ensure a clean import each time
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]

    sys.modules["shared"] = shared_stub

    # The script does: sys.path.insert(0, os.path.dirname(__file__))
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)

    # Import by file path
    import importlib.util

    script_path = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts", "tool2_tech_docs.py"
    )
    script_path = os.path.normpath(script_path)

    spec = importlib.util.spec_from_file_location("tool2_tech_docs", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def module_and_stub():
    mod, stub = _import_module()
    return mod, stub


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_contains_owner_and_repo(self, module_and_stub):
        mod, _ = module_and_stub
        result = mod.build_index("myorg", "myrepo", {"README.md": "content"}, "2024-01-15 10:00 UTC")
        assert "myorg/myrepo" in result

    def test_contains_timestamp(self, module_and_stub):
        mod, _ = module_and_stub
        result = mod.build_index("myorg", "myrepo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "2024-01-15 10:00 UTC" in result

    def test_link_format(self, module_and_stub):
        mod, _ = module_and_stub
        docs = {"README.md": "r", "ARCHITECTURE.md": "a"}
        result = mod.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        expected_base = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/myorg-myrepo/"
        )
        assert f"{expected_base}README.md" in result
        assert f"{expected_base}ARCHITECTURE.md" in result

    def test_all_doc_names_present(self, module_and_stub):
        mod, _ = module_and_stub
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = mod.build_index("o", "r", docs, "2024-06-01 12:00 UTC")
        for name in docs:
            assert name in result

    def test_empty_docs_dict(self, module_and_stub):
        mod, _ = module_and_stub
        result = mod.build_index("o", "r", {}, "2024-06-01 12:00 UTC")
        # Should still produce a valid index without doc links
        assert "Tech Documentation Index" in result
        assert "## Documents" in result

    def test_auto_generated_footer(self, module_and_stub):
        mod, _ = module_and_stub
        result = mod.build_index("o", "r", {"README.md": ""}, "2024-06-01 12:00 UTC")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_hyphenated_repo_in_url(self, module_and_stub):
        mod, _ = module_and_stub
        result = mod.build_index("my-org", "my-repo", {"README.md": ""}, "2024-06-01 12:00 UTC")
        assert "my-org-my-repo" in result

    def test_special_characters_in_owner_repo(self, module_and_stub):
        mod, _ = module_and_stub
        # Should not raise
        result = mod.build_index("org123", "repo_456", {"README.md": ""}, "2024-06-01 12:00 UTC")
        assert "org123" in result
        assert "repo_456" in result


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_returns_three_doc_keys(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {"file.py": "print('hello')"}
        stub.call_claude.side_effect = ["readme content", "arch content", "runbook content"]
        docs = mod.generate_docs("owner", "repo", "https://run-url")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("owner", "repo", "https://run-url")
        assert stub.call_claude.call_count == 3

    def test_get_repo_files_called_for_code_files(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("owner", "repo", "https://run-url")
        calls = stub.get_repo_files.call_args_list
        # First call: py/js/ts/go
        first_call_extensions = calls[0][0][2]
        assert ".py" in first_call_extensions
        assert ".js" in first_call_extensions

    def test_get_repo_files_called_for_iac_files(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("owner", "repo", "https://run-url")
        calls = stub.get_repo_files.call_args_list
        second_call_extensions = calls[1][0][2]
        assert ".tf" in second_call_extensions
        assert ".yaml" in second_call_extensions

    def test_readme_uses_system_readme_prompt(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("acme", "backend", "https://run-url")
        readme_call = stub.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_arch_doc_uses_system_arch_prompt(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("acme", "backend", "https://run-url")
        arch_call = stub.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_runbook_uses_system_runbook_prompt(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("acme", "backend", "https://run-url")
        runbook_call = stub.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "runbook" in system_prompt.lower() or "devops" in system_prompt.lower()

    def test_doc_content_comes_from_call_claude(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]
        docs = mod.generate_docs("owner", "repo", "https://run-url")
        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_no_files_found_fallback(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        # Should not raise; Claude receives "_No files found_"
        docs = mod.generate_docs("owner", "repo", "https://run-url")
        user_prompt = stub.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, module_and_stub):
        mod, stub = module_and_stub
        long_content = "x" * 10_000
        stub.get_repo_files.return_value = {"big_file.py": long_content}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("owner", "repo", "https://run-url")
        # The user prompt must not contain more than 4000 x's in a single block
        user_prompt = stub.call_claude.call_args_list[0][0][1]
        # The truncated content appears inside a code block
        assert "x" * 4001 not in user_prompt
        assert "x" * 4000 in user_prompt

    def test_owner_and_repo_in_user_prompts(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("myorg", "myrepo", "https://run-url")
        for c in stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "myorg/myrepo" in user_prompt

    def test_max_files_code_15(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("owner", "repo", "https://run-url")
        code_call = stub.get_repo_files.call_args_list[0]
        assert code_call[1].get("max_files") == 15 or (
            len(code_call[0]) > 3 and code_call[0][3] == 15
        )

    def test_max_files_iac_10(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("owner", "repo", "https://run-url")
        iac_call = stub.get_repo_files.call_args_list[1]
        assert iac_call[1].get("max_files") == 10 or (
            len(iac_call[0]) > 3 and iac_call[0][3] == 10
        )

    def test_call_claude_raises_propagates(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = RuntimeError("Claude API error")
        with pytest.raises(RuntimeError, match="Claude API error"):
            mod.generate_docs("owner", "repo", "https://run-url")

    def test_multiple_files_formatted_in_prompt(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {
            "main.py": "import os",
            "utils.py": "def helper(): pass",
        }
        stub.call_claude.return_value = "# Doc"
        mod.generate_docs("owner", "repo", "https://run-url")
        user_prompt = stub.call_claude.call_args_list[0][0][1]
        assert "main.py" in user_prompt
        assert "utils.py" in user_prompt


# ---------------------------------------------------------------------------
# Tests: fmt helper (