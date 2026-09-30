"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestration of get_repo_files + call_claude calls
- build_index(): markdown index generation with correct links, timestamps, and doc names
- fmt() helper (via generate_docs output): empty files, single file, multiple files, truncation hint
- __main__ block: happy-path execution, exception handling, audit + email on failure
- Environment variable consumption in __main__

Mocks used:
- shared.call_claude (prevents real Anthropic API calls)
- shared.get_repo_files (prevents real GitHub API calls)
- shared.write_output_file (prevents real GitHub write calls)
- shared.send_email (prevents real SES/SMTP calls)
- shared.email_html (HTML builder utility)
- shared.write_audit_entry (prevents real audit writes)
- shared.OUTPUT_REPO_OWNER / shared.OUTPUT_REPO constants
- datetime.datetime.utcnow (deterministic timestamps)

TODOs:
- TODO: Integration test against a real (sandboxed) GitHub repo once credentials available
- TODO: Test Claude response validation if schema enforcement is added
- TODO: Test file truncation at 4000 chars inside fmt() more directly once it is a public function
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with the shared module stubbed out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-org"
FAKE_OUTPUT_REPO = "ai-output-repo"


def _make_shared_stub():
    """Return a minimal stub for the `shared` module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/ai-org/ai-output-repo/blob/main/some/path")
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module(shared_stub=None):
    """
    Import (or re-import) tool2_tech_docs with a controlled shared stub.
    Uses sys.modules patching to avoid real network calls.
    """
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Insert stub before import so the module's `from shared import …` resolves it
    sys.modules["shared"] = shared_stub

    # Force a fresh import each time
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Add the scripts directory to path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the directory where this test file lives (CI may run from repo root)
    src_dir = os.path.join(os.path.dirname(__file__))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    import tool2_tech_docs
    return tool2_tech_docs, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_sys_modules():
    """Ensure tool2_tech_docs is re-imported fresh for each test."""
    yield
    for key in list(sys.modules.keys()):
        if key in ("tool2_tech_docs", "shared"):
            del sys.modules[key]


@pytest.fixture
def shared_stub():
    return _make_shared_stub()


@pytest.fixture
def module(shared_stub):
    mod, _ = _import_module(shared_stub)
    return mod, shared_stub


# ---------------------------------------------------------------------------
# build_index tests
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_happy_path_contains_repo_header(self, module):
        mod, stub = module
        docs = {"README.md": "content", "ARCHITECTURE.md": "content2"}
        result = mod.build_index("acme", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert "Tech Documentation Index — acme/myrepo" in result

    def test_happy_path_contains_generated_timestamp(self, module):
        mod, stub = module
        docs = {"README.md": "x"}
        result = mod.build_index("acme", "myrepo", docs, "2024-06-01 12:30 UTC")
        assert "2024-06-01 12:30 UTC" in result

    def test_links_point_to_output_repo(self, module):
        mod, stub = module
        docs = {"README.md": "x", "RUNBOOK.md": "y"}
        result = mod.build_index("ownerX", "repoY", docs, "now")
        assert f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}/blob/main/tech-docs/ownerX-repoY/README.md" in result
        assert f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}/blob/main/tech-docs/ownerX-repoY/RUNBOOK.md" in result

    def test_all_doc_names_appear_as_link_labels(self, module):
        mod, stub = module
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = mod.build_index("o", "r", docs, "t")
        for name in docs:
            assert f"[{name}]" in result

    def test_empty_docs_dict(self, module):
        mod, stub = module
        result = mod.build_index("o", "r", {}, "t")
        assert "Tech Documentation Index" in result
        # No links section content
        assert "blob/main" not in result

    def test_footer_attribution(self, module):
        mod, stub = module
        result = mod.build_index("o", "r", {"README.md": ""}, "t")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_owner_repo_separator_in_links(self, module):
        """Links must use owner-repo (hyphen) not owner/repo (slash) in path."""
        mod, stub = module
        docs = {"README.md": "c"}
        result = mod.build_index("myowner", "myrepo", docs, "t")
        assert "tech-docs/myowner-myrepo/README.md" in result

    def test_special_characters_in_owner_repo(self, module):
        """Dots and hyphens in org/repo names should pass through."""
        mod, stub = module
        docs = {"README.md": "c"}
        result = mod.build_index("my.org", "cool-repo", docs, "t")
        assert "my.org-cool-repo" in result

    @pytest.mark.parametrize("now", [
        "2024-01-01 00:00 UTC",
        "2099-12-31 23:59 UTC",
        "2000-02-29 12:00 UTC",
    ])
    def test_various_timestamps(self, module, now):
        mod, stub = module
        result = mod.build_index("o", "r", {"README.md": ""}, now)
        assert now in result


# ---------------------------------------------------------------------------
# generate_docs tests
# ---------------------------------------------------------------------------

class TestGenerateDocs:
    def test_calls_get_repo_files_for_source_files(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run")
        calls = stub.get_repo_files.call_args_list
        extensions_fetched = [c[0][2] for c in calls]  # positional arg index 2
        assert any(".py" in exts for exts in extensions_fetched)

    def test_calls_get_repo_files_for_iac_files(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run")
        calls = stub.get_repo_files.call_args_list
        extensions_fetched = [c[0][2] for c in calls]
        assert any(".tf" in exts for exts in extensions_fetched)

    def test_calls_claude_three_times(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run")
        assert stub.call_claude.call_count == 3

    def test_returns_three_docs(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        docs = mod.generate_docs("owner", "repo", "http://run")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_is_claude_output(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# My README"
        docs = mod.generate_docs("owner", "repo", "http://run")
        assert docs["README.md"] == "# My README"

    def test_owner_and_repo_passed_to_claude_prompts(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("myowner", "myrepo", "http://run")
        for c in stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "myowner/myrepo" in user_prompt

    def test_no_source_files_uses_placeholder(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run")
        # At least one call should contain _No files found_
        all_prompts = [c[0][1] for c in stub.call_claude.call_args_list]
        assert any("_No files found_" in p for p in all_prompts)

    def test_source_files_included_in_readme_prompt(self, module):
        mod, stub = module
        stub.get_repo_files.side_effect = [
            {"main.py": "print('hello')"},  # first call: py/js/ts/go
            {},                              # second call: iac
        ]
        mod.generate_docs("owner", "repo", "http://run")
        readme_call = stub.call_claude.call_args_list[0]
        assert "main.py" in readme_call[0][1]

    def test_iac_files_included_in_architecture_prompt(self, module):
        mod, stub = module
        stub.get_repo_files.side_effect = [
            {},                                  # first call: py/js/ts/go
            {"main.tf": "resource \"aws\" {}"},  # second call: iac
        ]
        mod.generate_docs("owner", "repo", "http://run")
        arch_call = stub.call_claude.call_args_list[1]
        assert "main.tf" in arch_call[0][1]

    def test_file_content_truncated_to_4000_chars(self, module):
        mod, stub = module
        long_content = "x" * 10_000
        stub.get_repo_files.side_effect = [
            {"bigfile.py": long_content},
            {},
        ]
        mod.generate_docs("owner", "repo", "http://run")
        readme_call = stub.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        # The truncated content (4000 x's) should appear, not the full 10000
        assert "x" * 4000 in prompt
        assert "x" * 4001 not in prompt

    def test_multiple_source_files_all_included(self, module):
        mod, stub = module
        stub.get_repo_files.side_effect = [
            {"app.py": "code1", "utils.ts": "code2"},
            {},
        ]
        mod.generate_docs("owner", "repo", "http://run")
        readme_call = stub.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        assert "app.py" in prompt
        assert "utils.ts" in prompt

    def test_max_files_limit_respected_for_source(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run")
        source_call = stub.get_repo_files.call_args_list[0]
        assert source_call[1].get("max_files") == 15 or source_call[0][3] == 15

    def test_max_files_limit_respected_for_iac(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run")
        iac_call = stub.get_repo_files.call_args_list[1]
        assert iac_call[1].get("max_files") == 10 or iac_call[0][3] == 10

    def test_claude_called_with_correct_system_prompts(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        mod.generate_docs("owner", "repo", "http://run")
        system_prompts = [c[0][0] for c in stub.call_claude.call_args_list]
        assert any("technical writer" in p for p in system_prompts)
        assert any("solutions architect" in p or "cloud" in p.lower() for p in system_prompts)
        assert any("DevOps" in p or "runbook" in p.lower() for p in system_prompts)

    def test_call_claude_error_propagates(self, module):
        mod, stub = module
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = RuntimeError("Claude API down")
        with pytest.raises(RuntimeError, match="Claude API down"):
            mod.generate_docs("owner", "repo", "http://run")

    def test_get_repo_files_error_propagates(self, module):
        mod, stub = module
        stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("owner", "repo", "http://run")


# ---------------------------------------------------------------------------
# __main__ block tests
# ---------------------------------------------------------------------------

class TestMainBlock:
    """Tests for the __main__ execution path via runpy."""

    def _run_main(self, shared_stub, env_overrides=None):
        """Execute the __main__ block with controlled environment and stubs."""
        import runpy

        env = {
            "SOURCE_REPO_OWNER": "test-owner",
            "SOURCE_REPO_NAME": "test-repo",
            "GITHUB_RUN_URL": "https://github.com/actions/run/123",
        }
        if env_overrides:
            env