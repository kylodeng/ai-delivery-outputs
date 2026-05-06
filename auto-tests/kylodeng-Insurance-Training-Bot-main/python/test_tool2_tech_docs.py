"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestration of get_repo_files + call_claude
- build_index(): correct markdown construction with links and metadata
- fmt() inner function behaviour (via generate_docs side-effects)
- __main__ block: success path and failure/exception path

Mocks used:
- shared.call_claude          → patched to return predictable strings
- shared.get_repo_files       → patched to return controlled file dicts
- shared.write_output_file    → patched to return fake URLs
- shared.send_email           → patched (no-op)
- shared.email_html           → patched to return a dummy HTML string
- shared.write_audit_entry    → patched (no-op)
- shared.OUTPUT_REPO_OWNER    → patched constant
- shared.OUTPUT_REPO          → patched constant
- datetime.datetime.utcnow    → patched for deterministic timestamps
- os.environ                  → patched for __main__ execution

TODOs:
- TODO: Integration test requiring a real GitHub token and Claude API key
- TODO: Test behaviour when get_repo_files returns files whose content exceeds 4000 chars (truncation)
- TODO: Test concurrent / parallel doc generation if that is ever introduced
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
# Helpers to import the module under test with shared patched out
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"

def _make_shared_stub():
    """Return a minimal stub for the `shared` module."""
    stub = types.ModuleType("shared")
    stub.call_claude        = MagicMock(return_value="CLAUDE_OUTPUT")
    stub.get_repo_files     = MagicMock(return_value={})
    stub.write_output_file  = MagicMock(return_value="https://github.com/output/file")
    stub.send_email         = MagicMock()
    stub.email_html         = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry  = MagicMock()
    stub.OUTPUT_REPO_OWNER  = "test-output-owner"
    stub.OUTPUT_REPO        = "test-output-repo"
    return stub


def _import_module(shared_stub=None):
    """Import (or re-import) tool2_tech_docs with a controlled shared stub."""
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Ensure sys.path contains the scripts directory
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    sys.modules["shared"] = shared_stub

    # Remove cached version so re-import picks up new stub
    sys.modules.pop("tool2_tech_docs", None)

    # Import from the actual file path
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tool2_tech_docs",
        os.path.join(
            os.path.dirname(__file__),
            ".github", "scripts", "tool2_tech_docs.py"
        ),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def module(shared_stub):
    mod, _ = _import_module(shared_stub)
    return mod


@pytest.fixture()
def module_and_shared():
    stub = _make_shared_stub()
    mod, _ = _import_module(stub)
    return mod, stub


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_contains_repo_header(self, module):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content2"}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "# Tech Documentation Index — acme/my-repo" in result

    def test_happy_path_contains_generated_timestamp(self, module):
        docs = {"README.md": "x"}
        result = module.build_index("acme", "my-repo", docs, "2024-06-01 12:00 UTC")
        assert "**Generated:** 2024-06-01 12:00 UTC" in result

    def test_happy_path_links_use_output_repo_constants(self, module, shared_stub):
        docs = {"README.md": "x"}
        result = module.build_index("acme", "my-repo", docs, "now")
        assert "test-output-owner" in result
        assert "test-output-repo" in result

    def test_happy_path_each_doc_has_link(self, module):
        docs = {
            "README.md": "a",
            "ARCHITECTURE.md": "b",
            "RUNBOOK.md": "c",
        }
        result = module.build_index("owner", "repo", docs, "ts")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_format_includes_correct_path_segment(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("myowner", "myrepo", docs, "ts")
        assert "tech-docs/myowner-myrepo/README.md" in result

    def test_footer_attribution_present(self, module):
        docs = {"README.md": "x"}
        result = module.build_index("o", "r", docs, "t")
        assert "_Auto-generated by AI Delivery Bot_" in result

    def test_empty_docs_dict(self, module):
        result = module.build_index("o", "r", {}, "t")
        # Should still produce a valid index with no link lines
        assert "# Tech Documentation Index" in result
        assert "README.md" not in result

    def test_special_characters_in_owner_repo(self, module):
        docs = {"README.md": "x"}
        result = module.build_index("my-org", "my.repo", docs, "t")
        assert "my-org" in result
        assert "my.repo" in result

    def test_multiple_docs_all_linked(self, module):
        docs = {f"doc{i}.md": f"content{i}" for i in range(5)}
        result = module.build_index("o", "r", docs, "t")
        for i in range(5):
            assert f"doc{i}.md" in result

    def test_link_is_github_url(self, module):
        docs = {"README.md": "x"}
        result = module.build_index("o", "r", docs, "t")
        assert "https://github.com/" in result


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_documents(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "Generated content"
        result = mod.generate_docs("owner", "repo", "http://run-url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_for_source_files(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run-url")
        calls = shared.get_repo_files.call_args_list
        # First call should request py/js/ts/go files
        first_exts = calls[0][0][2]
        assert ".py" in first_exts
        assert ".js" in first_exts
        assert ".ts" in first_exts
        assert ".go" in first_exts

    def test_calls_get_repo_files_for_iac_files(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run-url")
        calls = shared.get_repo_files.call_args_list
        second_exts = calls[1][0][2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts
        assert ".yml" in second_exts

    def test_calls_get_repo_files_with_correct_owner_repo(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        mod.generate_docs("myowner", "myrepo", "http://run-url")
        for c in shared.get_repo_files.call_args_list:
            assert c[0][0] == "myowner"
            assert c[0][1] == "myrepo"

    def test_get_repo_files_max_files_source(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "url")
        first_call_kwargs = shared.get_repo_files.call_args_list[0]
        # max_files is passed as keyword argument
        assert first_call_kwargs[1].get("max_files") == 15 or \
               (len(first_call_kwargs[0]) > 3 and first_call_kwargs[0][3] == 15)

    def test_get_repo_files_max_files_iac(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "url")
        second_call = shared.get_repo_files.call_args_list[1]
        assert second_call[1].get("max_files") == 10 or \
               (len(second_call[0]) > 3 and second_call[0][3] == 10)

    def test_call_claude_called_three_times(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "url")
        assert shared.call_claude.call_count == 3

    def test_call_claude_readme_uses_correct_system(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "content"
        mod.generate_docs("o", "r", "url")
        first_system = shared.call_claude.call_args_list[0][0][0]
        assert "README" in first_system or "technical writer" in first_system.lower()

    def test_call_claude_arch_uses_correct_system(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "content"
        mod.generate_docs("o", "r", "url")
        second_system = shared.call_claude.call_args_list[1][0][0]
        assert "architect" in second_system.lower() or "architecture" in second_system.lower()

    def test_call_claude_runbook_uses_correct_system(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "content"
        mod.generate_docs("o", "r", "url")
        third_system = shared.call_claude.call_args_list[2][0][0]
        assert "runbook" in third_system.lower() or "devops" in third_system.lower()

    def test_returns_claude_output_verbatim(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.side_effect = [
            "README_CONTENT",
            "ARCH_CONTENT",
            "RUNBOOK_CONTENT",
        ]
        result = mod.generate_docs("o", "r", "url")
        assert result["README.md"] == "README_CONTENT"
        assert result["ARCHITECTURE.md"] == "ARCH_CONTENT"
        assert result["RUNBOOK.md"] == "RUNBOOK_CONTENT"

    def test_files_included_in_claude_prompt(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.side_effect = [
            {"main.py": "print('hello')"},
            {"infra.tf": "resource {}"},
        ]
        shared.call_claude.return_value = "content"
        mod.generate_docs("o", "r", "url")
        readme_prompt = shared.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_prompt
        assert "print('hello')" in readme_prompt

    def test_no_files_produces_no_files_found_message(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "content"
        mod.generate_docs("o", "r", "url")
        readme_prompt = shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, module_and_shared):
        mod, shared = module_and_shared
        long_content = "x" * 10_000
        shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        shared.call_claude.return_value = "content"
        mod.generate_docs("o", "r", "url")
        readme_prompt = shared.call_claude.call_args_list[0][0][1]
        # The truncated content is at most 4000 chars per file
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_call_claude_raises_propagates(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.side_effect = RuntimeError("Claude API failure")
        with pytest.raises(RuntimeError, match="Claude API failure"):
            mod.generate_docs("o", "r", "url")

    def test_owner_repo_in_claude_prompt(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "content"
        mod.generate_docs("specific-owner", "specific-repo", "url")
        readme_prompt = shared.call_claude.call_args_list[0][0][1]
        assert "specific-owner/specific-repo" in readme_prompt

    def test_iac_files_in_arch_prompt(self, module_and_shared):
        mod, shared = module_and_shared
        shared.get_repo_files.side_effect = [
            {"app.py": "code"},
            {"main