"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file results, partial failures
- build_index(): correct markdown output, multiple docs, empty docs dict, special chars in owner/repo
- fmt() helper (via generate_docs integration): empty files, single file, truncation at 4000 chars
- __main__ block: success path, exception/failure path, env var handling

Mocks used:
- shared.call_claude (patched at tool2_tech_docs module level)
- shared.get_repo_files (patched at tool2_tech_docs module level)
- shared.write_output_file (patched at tool2_tech_docs module level)
- shared.send_email (patched at tool2_tech_docs module level)
- shared.email_html (patched at tool2_tech_docs module level)
- shared.write_audit_entry (patched at tool2_tech_docs module level)
- shared.OUTPUT_REPO_OWNER (patched as module attribute)
- shared.OUTPUT_REPO (patched as module attribute)
- datetime.datetime.utcnow (patched for deterministic output)

TODOs:
- TODO: Integration test against a real GitHub repo requires credentials — stubbed below
- TODO: Test Claude API rate-limiting / retry behaviour — requires shared.call_claude internals
- TODO: Test write_output_file returning non-URL values — depends on shared module contract
"""

import sys
import os
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with mocked shared dependencies
# ---------------------------------------------------------------------------

SHARED_MOCK_ATTRS = {
    "call_claude": MagicMock(return_value="# Generated content"),
    "get_repo_files": MagicMock(return_value={}),
    "write_output_file": MagicMock(return_value="https://github.com/out/repo/blob/main/file.md"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>body</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-output-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_mock():
    """Return a fresh MagicMock that simulates the shared module."""
    m = MagicMock()
    for attr, val in SHARED_MOCK_ATTRS.items():
        setattr(m, attr, val if not callable(val) else MagicMock(return_value=val.return_value if isinstance(val, MagicMock) else val))
    m.OUTPUT_REPO_OWNER = "test-output-owner"
    m.OUTPUT_REPO = "test-output-repo"
    return m


def _import_module(shared_mock=None):
    """
    Import (or re-import) tool2_tech_docs with a fake 'shared' module injected.
    Returns (module, shared_mock).
    """
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    with patch.dict("sys.modules", {"shared": shared_mock}):
        # Force re-import
        if "tool2_tech_docs" in sys.modules:
            del sys.modules["tool2_tech_docs"]

        # Adjust path so the script can be found
        target = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py")
        spec = importlib.util.spec_from_file_location("tool2_tech_docs", target)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["shared"] = shared_mock
        sys.modules["tool2_tech_docs"] = mod
        spec.loader.exec_module(mod)

    return mod, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_sys_modules():
    """Clean up tool2_tech_docs from sys.modules after each test."""
    yield
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules.pop("shared", None)


@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def module_and_mock():
    """Returns (module, shared_mock) with default happy-path stubs."""
    shared = _make_shared_mock()
    shared.get_repo_files.return_value = {
        "backend/app.py": "print('hello')",
        "main.tf": "resource \"aws_s3_bucket\" \"b\" {}",
    }
    shared.call_claude.return_value = "# AI Generated Doc"
    mod, _ = _import_module(shared)
    return mod, shared


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_single_doc(self, module_and_mock):
        mod, shared = module_and_mock
        docs = {"README.md": "# content"}
        result = mod.build_index("myowner", "myrepo", docs, "2024-01-15 10:00 UTC")

        assert "Tech Documentation Index — myowner/myrepo" in result
        assert "**Generated:** 2024-01-15 10:00 UTC" in result
        assert "README.md" in result
        assert "https://github.com/test-output-owner/test-output-repo/blob/main/tech-docs/myowner-myrepo/README.md" in result
        assert "_Auto-generated by AI Delivery Bot_" in result

    def test_happy_path_multiple_docs(self, module_and_mock):
        mod, shared = module_and_mock
        docs = {
            "README.md": "content1",
            "ARCHITECTURE.md": "content2",
            "RUNBOOK.md": "content3",
        }
        result = mod.build_index("owner", "repo", docs, "2024-06-01 00:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result
        # All three links present
        assert result.count("https://github.com") == 3

    def test_empty_docs_dict(self, module_and_mock):
        mod, _ = module_and_mock
        result = mod.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")

        assert "Tech Documentation Index — owner/repo" in result
        # No links section — just empty lines between Documents header and footer
        assert "## Documents" in result

    def test_owner_repo_with_hyphens_and_dots(self, module_and_mock):
        mod, _ = module_and_mock
        docs = {"README.md": "x"}
        result = mod.build_index("my-org", "my.repo", docs, "2024-01-01 00:00 UTC")

        assert "my-org/my.repo" in result
        assert "tech-docs/my-org-my.repo/README.md" in result

    def test_link_format_correct(self, module_and_mock):
        mod, shared = module_and_mock
        docs = {"ARCHITECTURE.md": "arch"}
        result = mod.build_index("acme", "platform", docs, "2024-03-10 08:30 UTC")

        expected_link = (
            "- [ARCHITECTURE.md](https://github.com/test-output-owner/test-output-repo"
            "/blob/main/tech-docs/acme-platform/ARCHITECTURE.md)"
        )
        assert expected_link in result

    def test_generated_timestamp_in_output(self, module_and_mock):
        mod, _ = module_and_mock
        now_str = "2099-12-31 23:59 UTC"
        result = mod.build_index("o", "r", {"README.md": ""}, now_str)
        assert now_str in result

    def test_uses_output_repo_constants(self, module_and_mock):
        """build_index must reference OUTPUT_REPO_OWNER and OUTPUT_REPO from shared."""
        mod, shared = module_and_mock
        # Override constants on the module itself (they were imported at load time)
        original_owner = mod.OUTPUT_REPO_OWNER
        original_repo = mod.OUTPUT_REPO
        try:
            mod.OUTPUT_REPO_OWNER = "custom-owner"
            mod.OUTPUT_REPO = "custom-repo"
            docs = {"README.md": "x"}
            result = mod.build_index("o", "r", docs, "now")
            assert "custom-owner" in result
            assert "custom-repo" in result
        finally:
            mod.OUTPUT_REPO_OWNER = original_owner
            mod.OUTPUT_REPO = original_repo


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, module_and_mock):
        mod, shared = module_and_mock
        shared.get_repo_files.return_value = {"src/main.py": "print('hi')"}
        shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        result = mod.generate_docs("owner", "repo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}
        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_calls_get_repo_files_twice(self, module_and_mock):
        mod, shared = module_and_mock
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "content"

        mod.generate_docs("owner", "repo", "https://github.com/run/1")

        assert shared.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_correct_extensions(self, module_and_mock):
        mod, shared = module_and_mock
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc"

        mod.generate_docs("myorg", "myrepo", "https://github.com")

        calls = shared.get_repo_files.call_args_list
        # First call: source code files
        first_call_args = calls[0][0]
        assert first_call_args[0] == "myorg"
        assert first_call_args[1] == "myrepo"
        assert ".py" in first_call_args[2]
        assert ".ts" in first_call_args[2]
        assert ".go" in first_call_args[2]

        # Second call: IaC files
        second_call_args = calls[1][0]
        assert ".tf" in second_call_args[2]
        assert ".yaml" in second_call_args[2]
        assert ".yml" in second_call_args[2]

    def test_get_repo_files_max_files_params(self, module_and_mock):
        mod, shared = module_and_mock
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc"

        mod.generate_docs("o", "r", "url")

        calls = shared.get_repo_files.call_args_list
        # py/js/ts/go → max_files=15
        assert calls[0][1].get("max_files") == 15
        # iac → max_files=10
        assert calls[1][1].get("max_files") == 10

    def test_call_claude_called_three_times(self, module_and_mock):
        mod, shared = module_and_mock
        shared.call_claude.return_value = "content"

        mod.generate_docs("o", "r", "url")

        assert shared.call_claude.call_count == 3

    def test_call_claude_receives_owner_repo_in_prompt(self, module_and_mock):
        mod, shared = module_and_mock
        shared.get_repo_files.return_value = {"file.py": "code"}
        shared.call_claude.return_value = "doc"

        mod.generate_docs("acme", "platform", "url")

        for c in shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg is the user prompt
            assert "acme/platform" in user_prompt

    def test_empty_repo_files_uses_no_files_found(self, module_and_mock):
        mod, shared = module_and_mock
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "generated"

        mod.generate_docs("o", "r", "url")

        for c in shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, module_and_mock):
        mod, shared = module_and_mock
        long_content = "x" * 10_000
        shared.get_repo_files.return_value = {"big_file.py": long_content}
        shared.call_claude.return_value = "result"

        mod.generate_docs("o", "r", "url")

        # The prompt passed to call_claude must NOT contain 10000 x's; it should cap at 4000
        for c in shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            # We expect "x" * 4000 to appear at most, not the full 10000
            assert "x" * 10_000 not in user_prompt
            assert "x" * 4_000 in user_prompt

    def test_call_claude_exception_propagates(self, module_and_mock):
        mod, shared = module_and_mock
        shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            mod.generate_docs("o", "r", "url")

    def test_get_repo_files_exception_propagates(self, module_and_mock):
        mod, shared = module_and_mock
        shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("o", "r", "url")

    def test_system_prompts_passed_to_call_claude(self, module_and_mock):
        mod, shared = module_and_mock
        shared.call_claude.return_value = "content"

        mod.generate_docs("o", "r", "url")

        system_prompts = [c[0][0] for c in shared.call_claude.call_args_list]
        # Each call must receive a non-empty system prompt string
        for sp in system_prompts:
            assert isinstance(sp, str)
            assert len(sp) > 0

        # Verify the system prompts are distinct (README, ARCH, RUNBOOK)
        assert len(set(system_prompts)) == 3

    def test_iac_files_sent_to_arch_doc(self, module_and_mock):
        mod, shared = module_and_mock
        py_files = {"app.py":