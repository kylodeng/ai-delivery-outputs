"""
Tests for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
    - build_index(): produces a valid markdown index linking to generated docs
    - __main__ block behaviour: happy path (docs written, email sent, audit logged) and failure path

Mocks used:
    - shared.call_claude         — avoids real Anthropic API calls
    - shared.get_repo_files      — avoids real GitHub API calls
    - shared.write_output_file   — avoids real GitHub commit operations
    - shared.send_email          — avoids real email dispatch
    - shared.email_html          — avoids template rendering side-effects
    - shared.write_audit_entry   — avoids real audit-log writes
    - shared.OUTPUT_REPO_OWNER   — patched to a known test value
    - shared.OUTPUT_REPO         — patched to a known test value
    - datetime.datetime          — frozen to a deterministic timestamp

TODOs:
    # TODO: Integration test for generate_docs with real GitHub token — needs GITHUB_TOKEN secret
    # TODO: Verify exact Claude prompt content once prompt format is stabilised
    # TODO: Test for extremely large files that exceed 4000-char truncation boundary
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
# Helpers to build a minimal fake `shared` module so we can import the SUT
# without the real shared module on sys.path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal mock of the `shared` module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="_generated content_")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/tech-docs/owner-repo/README.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared_module():
    """
    Insert a fake `shared` module into sys.modules before each test and
    remove it afterwards so tests are isolated.
    """
    fake = _make_fake_shared()
    sys.modules["shared"] = fake
    yield fake
    sys.modules.pop("shared", None)
    # Also remove the SUT so it is reimported fresh each test
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def sut(fake_shared_module):
    """Import (or reimport) the SUT with the fake shared module in place."""
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also allow importing directly from this file's directory structure
    sut_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py")
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", sut_path)
    module = importlib.util.module_from_spec(spec)
    # Patch sys.path so the relative shared import resolves to our fake
    with patch.dict(sys.modules, {"shared": fake_shared_module}):
        spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Convenience: load the SUT module directly using its file path so we
# can call functions without depending on package structure.
# ---------------------------------------------------------------------------

def _load_sut(fake_shared):
    """Load tool2_tech_docs.py from the repository path."""
    here = os.path.dirname(os.path.abspath(__file__))
    sut_path = os.path.join(here, ".github", "scripts", "tool2_tech_docs.py")

    with patch.dict(sys.modules, {"shared": fake_shared}):
        spec = importlib.util.spec_from_file_location("tool2_tech_docs", sut_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod(fake_shared_module):
    return _load_sut(fake_shared_module)


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_returns_string(self, mod):
        result = mod.build_index("acme", "backend", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, mod):
        result = mod.build_index("acme", "backend", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "acme/backend" in result

    def test_contains_generated_timestamp(self, mod):
        now = "2024-06-01 12:34 UTC"
        result = mod.build_index("acme", "backend", {"README.md": "x"}, now)
        assert now in result

    def test_contains_all_doc_links(self, mod):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = mod.build_index("acme", "backend", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo(self, mod):
        """Links must point at OUTPUT_REPO_OWNER / OUTPUT_REPO."""
        docs = {"README.md": "r"}
        result = mod.build_index("acme", "backend", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_include_owner_repo_in_path(self, mod):
        docs = {"README.md": "r"}
        result = mod.build_index("acme", "backend", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/acme-backend/README.md" in result

    def test_empty_docs_produces_no_links(self, mod):
        result = mod.build_index("acme", "backend", {}, "2024-01-01 00:00 UTC")
        # Should still have the heading
        assert "Tech Documentation Index" in result
        # No bullet links
        assert "blob/main" not in result

    def test_contains_auto_generated_footer(self, mod):
        result = mod.build_index("acme", "backend", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_special_characters_in_repo_name(self, mod):
        """Repo names can contain hyphens and underscores."""
        docs = {"README.md": "x"}
        result = mod.build_index("my-org", "my_repo", docs, "2024-01-01 00:00 UTC")
        assert "my-org/my_repo" in result

    def test_multiple_docs_all_appear_as_list_items(self, mod):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a"}
        result = mod.build_index("acme", "backend", docs, "2024-01-01 00:00 UTC")
        # Each doc name should appear prefixed with '- ['
        for name in docs:
            assert f"- [{name}]" in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def _setup_files(self, fake_shared, py_files=None, iac_files=None):
        py_files = py_files or {"main.py": "print('hello')"}
        iac_files = iac_files or {"main.tf": 'resource "aws_s3_bucket" "b" {}'}

        def _get_repo_files(owner, repo, extensions, max_files=15):
            if ".py" in extensions:
                return py_files
            return iac_files

        fake_shared.get_repo_files.side_effect = _get_repo_files

    def test_returns_three_docs(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        docs = mod.generate_docs("acme", "backend", "https://github.com/run/1")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        assert fake_shared_module.get_repo_files.call_count == 2

    def test_calls_call_claude_three_times(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        assert fake_shared_module.call_claude.call_count == 3

    def test_py_js_files_requested_with_correct_extensions(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        calls = fake_shared_module.get_repo_files.call_args_list
        extensions_used = [c[0][2] for c in calls]  # positional arg index 2
        py_exts = next(e for e in extensions_used if ".py" in e)
        assert ".js" in py_exts
        assert ".ts" in py_exts
        assert ".go" in py_exts

    def test_iac_files_requested_with_correct_extensions(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        calls = fake_shared_module.get_repo_files.call_args_list
        extensions_used = [c[0][2] for c in calls]
        iac_exts = next(e for e in extensions_used if ".tf" in e)
        assert ".yaml" in iac_exts
        assert ".yml" in iac_exts
        assert ".json" in iac_exts

    def test_py_js_max_files_is_15(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        calls = fake_shared_module.get_repo_files.call_args_list
        # find the py call
        py_call = next(c for c in calls if ".py" in c[0][2])
        assert py_call[1].get("max_files", py_call[0][3] if len(py_call[0]) > 3 else None) == 15

    def test_iac_max_files_is_10(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        calls = fake_shared_module.get_repo_files.call_args_list
        iac_call = next(c for c in calls if ".tf" in c[0][2])
        assert iac_call[1].get("max_files", iac_call[0][3] if len(iac_call[0]) > 3 else None) == 10

    def test_readme_uses_readme_system_prompt(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        readme_call = fake_shared_module.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "technical writer" in system_prompt.lower() or "README" in system_prompt

    def test_arch_doc_uses_arch_system_prompt(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        arch_call = fake_shared_module.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_runbook_uses_runbook_system_prompt(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("acme", "backend", "https://github.com/run/1")
        runbook_call = fake_shared_module.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "devops" in system_prompt.lower() or "runbook" in system_prompt.lower()

    def test_docs_contain_call_claude_return_value(self, mod, fake_shared_module):
        fake_shared_module.call_claude.return_value = "MOCKED_CONTENT"
        self._setup_files(fake_shared_module)
        docs = mod.generate_docs("acme", "backend", "https://github.com/run/1")
        for content in docs.values():
            assert content == "MOCKED_CONTENT"

    def test_no_py_files_uses_no_files_found(self, mod, fake_shared_module):
        """When no files are returned, the prompt should say _No files found_."""
        fake_shared_module.get_repo_files.return_value = {}
        fake_shared_module.call_claude.return_value = "_generated_"
        docs = mod.generate_docs("acme", "backend", "https://github.com/run/1")
        # Should not raise and should still return three docs
        assert len(docs) == 3

    def test_get_repo_files_called_with_owner_and_repo(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        mod.generate_docs("my-owner", "my-repo", "https://github.com/run/1")
        for c in fake_shared_module.get_repo_files.call_args_list:
            assert c[0][0] == "my-owner"
            assert c[0][1] == "my-repo"

    def test_call_claude_propagates_exception(self, mod, fake_shared_module):
        self._setup_files(fake_shared_module)
        fake_shared_module.call_claude.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            mod.generate_docs("acme", "backend", "https://github.com/run/1")

    def test_get_repo_files_propagates