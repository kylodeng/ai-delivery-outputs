"""
Test suite for .github/scripts/tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown output, link construction, timestamp embedding
- fmt() helper (via generate_docs): file truncation at 4000 chars, empty dict fallback
- __main__ block: success path, exception/failure path
- System prompt constants: existence and key content requirements

Mocks used:
- shared.call_claude (via unittest.mock.patch)
- shared.get_repo_files (via unittest.mock.patch)
- shared.write_output_file (via unittest.mock.patch)
- shared.send_email (via unittest.mock.patch)
- shared.email_html (via unittest.mock.patch)
- shared.write_audit_entry (via unittest.mock.patch)
- shared.OUTPUT_REPO_OWNER / shared.OUTPUT_REPO (patched as module-level constants)
- datetime.datetime.utcnow (patched for deterministic timestamps)
- os.environ (via monkeypatch)

TODOs:
- TODO: Integration test for real call_claude round-trip (needs API key + network)
- TODO: Test write_output_file path construction if the shared module changes signature
- TODO: Verify email HTML body content in more detail once email_html signature is stable
"""

import importlib
import sys
import os
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with the shared module stubbed out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-output-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub for the `shared` module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/file")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture()
def tool2(monkeypatch):
    """
    Import tool2_tech_docs with the shared module replaced by a stub.
    Returns (module, shared_stub) so tests can inspect / reconfigure mocks.
    """
    shared_stub = _make_shared_stub()
    # Ensure the scripts directory is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    monkeypatch.syspath_prepend(scripts_dir)

    with patch.dict(sys.modules, {"shared": shared_stub}):
        # Force a fresh import every time
        mod_name = "tool2_tech_docs"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        import importlib.util, pathlib

        spec_path = os.path.join(
            os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py"
        )
        spec = importlib.util.spec_from_file_location(mod_name, spec_path)
        module = importlib.util.module_from_spec(spec)
        # Inject the stubbed shared into the module's namespace before exec
        module.shared = shared_stub
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)

    return module, shared_stub


# ---------------------------------------------------------------------------
# Constants / system prompts
# ---------------------------------------------------------------------------


class TestSystemPrompts:
    def test_system_readme_exists(self, tool2):
        mod, _ = tool2
        assert hasattr(mod, "SYSTEM_README")
        assert isinstance(mod.SYSTEM_README, str)
        assert len(mod.SYSTEM_README) > 0

    def test_system_arch_exists(self, tool2):
        mod, _ = tool2
        assert hasattr(mod, "SYSTEM_ARCH")
        assert isinstance(mod.SYSTEM_ARCH, str)

    def test_system_runbook_exists(self, tool2):
        mod, _ = tool2
        assert hasattr(mod, "SYSTEM_RUNBOOK")
        assert isinstance(mod.SYSTEM_RUNBOOK, str)

    def test_system_readme_mentions_required_sections(self, tool2):
        mod, _ = tool2
        for section in ["README", "Tech stack", "Architecture", "Environment variables"]:
            assert section in mod.SYSTEM_README, f"SYSTEM_README missing section: {section}"

    def test_system_arch_mentions_required_sections(self, tool2):
        mod, _ = tool2
        for keyword in ["Security", "Data flow", "Resources deployed", "Deployment"]:
            assert keyword in mod.SYSTEM_ARCH

    def test_system_runbook_mentions_required_sections(self, tool2):
        mod, _ = tool2
        for keyword in ["Health checks", "Monitoring", "Deployment procedure", "Escalation"]:
            assert keyword in mod.SYSTEM_RUNBOOK

    def test_system_readme_no_invention_rule(self, tool2):
        mod, _ = tool2
        assert "Do NOT invent" in mod.SYSTEM_README

    def test_system_arch_encryption_rule(self, tool2):
        mod, _ = tool2
        assert "encryption" in mod.SYSTEM_ARCH.lower()

    def test_system_runbook_todo_placeholder(self, tool2):
        mod, _ = tool2
        assert "TODO" in mod.SYSTEM_RUNBOOK


# ---------------------------------------------------------------------------
# generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def _setup_files(self, shared_stub, py_files=None, iac_files=None):
        py_files = py_files or {}
        iac_files = iac_files or {}

        def _side_effect(owner, repo, extensions, max_files=10):
            if ".py" in extensions:
                return py_files
            return iac_files

        shared_stub.get_repo_files.side_effect = _side_effect

    def test_happy_path_calls_claude_three_times(self, tool2):
        mod, shared_stub = tool2
        self._setup_files(
            shared_stub,
            py_files={"app.py": "print('hello')"},
            iac_files={"main.tf": "resource {}"},
        )
        shared_stub.call_claude.return_value = "# Doc content"

        result = mod.generate_docs("my-owner", "my-repo", "https://run.url")

        assert shared_stub.call_claude.call_count == 3
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_returns_claude_output(self, tool2):
        mod, shared_stub = tool2
        self._setup_files(shared_stub, py_files={"src.py": "x=1"})
        shared_stub.call_claude.side_effect = ["readme_content", "arch_content", "runbook_content"]

        result = mod.generate_docs("owner", "repo", "https://run")

        assert result["README.md"] == "readme_content"
        assert result["ARCHITECTURE.md"] == "arch_content"
        assert result["RUNBOOK.md"] == "runbook_content"

    def test_empty_files_uses_fallback_string(self, tool2):
        mod, shared_stub = tool2
        self._setup_files(shared_stub)  # all empty

        mod.generate_docs("owner", "repo", "https://run")

        # All three prompts should still be built; call_claude still called 3 times
        assert shared_stub.call_claude.call_count == 3
        # The prompt should contain the fallback text
        first_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in first_prompt

    def test_get_repo_files_called_with_correct_extensions(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.return_value = {}

        mod.generate_docs("my-owner", "my-repo", "https://run")

        calls = shared_stub.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call: source files
        first_exts = calls[0][0][2]
        assert ".py" in first_exts
        assert ".ts" in first_exts
        assert ".js" in first_exts
        assert ".go" in first_exts

        # Second call: IaC files
        second_exts = calls[1][0][2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts
        assert ".yml" in second_exts

    def test_get_repo_files_max_files_respected(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "https://run")

        calls = shared_stub.get_repo_files.call_args_list
        # py/js call max_files=15
        assert calls[0][1].get("max_files") == 15 or calls[0][0][3] == 15
        # iac call max_files=10
        assert calls[1][1].get("max_files") == 10 or calls[1][0][3] == 10

    def test_file_content_truncated_at_4000_chars(self, tool2):
        mod, shared_stub = tool2
        long_content = "x" * 8000
        self._setup_files(shared_stub, py_files={"big.py": long_content})

        mod.generate_docs("owner", "repo", "https://run")

        # The prompt sent to Claude should not exceed 4000 chars of file content
        readme_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        # The truncated content should appear, but not the full 8000
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_file_content_exactly_4000_chars_not_truncated(self, tool2):
        mod, shared_stub = tool2
        exact_content = "y" * 4000
        self._setup_files(shared_stub, py_files={"exact.py": exact_content})

        mod.generate_docs("owner", "repo", "https://run")

        readme_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        assert "y" * 4000 in readme_prompt

    def test_owner_and_repo_embedded_in_prompt(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.return_value = {}

        mod.generate_docs("acme-corp", "awesome-service", "https://run")

        readme_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        assert "acme-corp/awesome-service" in readme_prompt

    def test_readme_uses_system_readme_prompt(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "https://run")

        readme_system = shared_stub.call_claude.call_args_list[0][0][0]
        assert readme_system == mod.SYSTEM_README

    def test_arch_uses_system_arch_prompt(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "https://run")

        arch_system = shared_stub.call_claude.call_args_list[1][0][0]
        assert arch_system == mod.SYSTEM_ARCH

    def test_runbook_uses_system_runbook_prompt(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "https://run")

        runbook_system = shared_stub.call_claude.call_args_list[2][0][0]
        assert runbook_system == mod.SYSTEM_RUNBOOK

    def test_arch_prompt_contains_iac_files(self, tool2):
        mod, shared_stub = tool2
        self._setup_files(
            shared_stub,
            py_files={},
            iac_files={"main.tf": "terraform {}"},
        )

        mod.generate_docs("owner", "repo", "https://run")

        arch_prompt = shared_stub.call_claude.call_args_list[1][0][1]
        assert "main.tf" in arch_prompt
        assert "terraform {}" in arch_prompt

    def test_multiple_py_files_all_included_in_readme_prompt(self, tool2):
        mod, shared_stub = tool2
        self._setup_files(
            shared_stub,
            py_files={"a.py": "code_a", "b.ts": "code_b"},
        )

        mod.generate_docs("owner", "repo", "https://run")

        readme_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        assert "a.py" in readme_prompt
        assert "b.ts" in readme_prompt

    def test_call_claude_exception_propagates(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            mod.generate_docs("owner", "repo", "https://run")

    def test_get_repo_files_exception_propagates(self, tool2):
        mod, shared_stub = tool2
        shared_stub.get_repo_files.side_effect = ConnectionError("network")

        with pytest.raises(ConnectionError, match="network"):
            mod.generate_docs("owner", "repo", "https://run")


# ---------------------------------------------------------------------------
# build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_returns_string(self, tool2):
        mod, _ = tool2
        result = mod.build_index("owner", "repo", {"README.md": "content"}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo_in_title(self, tool2):
        mod, _ = tool2
        result = mod.build_index("my-owner", "my-repo", {"README.md": ""}, "2024-01-01 00:00 UTC")
        assert "my-owner/my-repo" in result

    def test_contains_timestamp(self, tool2):
        mod, _ = tool2
        result = mod.build_index("o", "r", {}, "2024-06-15 12:30 UTC")
        assert "2024-06-15 12:30 UTC" in result

    def test_generates_link_for_each_doc(self, tool2):
        mod, _ = tool2
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "b"}
        result = mod.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_link_points_to_