```python
"""
Test suite for .github/scripts/tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
- build_index(): constructs a markdown index page linking to generated docs
- __main__ block behaviour: env var handling, output writing, email sending, audit logging, error paths

Mocks used:
- shared.call_claude          — stubbed to return deterministic strings
- shared.get_repo_files       — stubbed to return synthetic file dicts
- shared.write_output_file    — stubbed to return fake GitHub URLs
- shared.send_email           — stubbed (no real SMTP calls)
- shared.email_html           — stubbed to return a dummy HTML string
- shared.write_audit_entry    — stubbed (no real file/DB writes)
- shared.OUTPUT_REPO_OWNER    — patched to a known constant
- shared.OUTPUT_REPO          — patched to a known constant
- datetime.datetime           — patched for deterministic timestamps

TODOs:
- TODO: Integration test that exercises the real `shared` module helpers once available
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are missing (None values)
        requires clarifying whether generate_docs should raise or gracefully handle None
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
# Helpers to load the module with a fully-mocked `shared` dependency
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-org"
FAKE_OUTPUT_REPO = "ai-output-repo"

def _make_shared_mock():
    """Return a mock `shared` module with all symbols used by tool2_tech_docs."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _load_module(shared_mock=None):
    """Import (or re-import) tool2_tech_docs with the given shared mock injected."""
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Inject mock before importing
    sys.modules["shared"] = shared_mock

    # Remove cached version so we get a fresh import each time
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Build the path to the source file
    module_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py"
    )

    import importlib.util
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def module(shared_mock):
    mod, sm = _load_module(shared_mock)
    return mod


@pytest.fixture()
def module_and_shared(shared_mock):
    mod, sm = _load_module(shared_mock)
    return mod, sm


# Synthetic file dictionaries reused across tests
SYNTHETIC_PY_FILES = {
    "app/main.py": "def main():\n    pass\n",
    "app/utils.py": "import os\n",
}
SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "infra/variables.yml": "env: production\n",
}


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_doc_keys(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        result = mod.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        mod.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert sm.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_uses_code_extensions(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "url")

        first_call_args = sm.get_repo_files.call_args_list[0]
        exts = first_call_args[0][2]  # positional arg index 2
        assert ".py" in exts
        assert ".ts" in exts
        assert ".js" in exts
        assert ".go" in exts

    def test_get_repo_files_second_call_uses_iac_extensions(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "url")

        second_call_args = sm.get_repo_files.call_args_list[1]
        exts = second_call_args[0][2]
        assert ".tf" in exts
        assert ".yaml" in exts
        assert ".yml" in exts

    def test_calls_claude_three_times(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "url")

        assert sm.call_claude.call_count == 3

    def test_readme_content_comes_from_claude(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.return_value = "CLAUDE_OUTPUT"

        result = mod.generate_docs("owner", "repo", "url")

        assert result["README.md"] == "CLAUDE_OUTPUT"

    def test_architecture_content_comes_from_claude(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        def side_effect(system, user):
            if "architecture" in system.lower() or "architect" in system.lower():
                return "ARCH_DOC"
            return "OTHER"

        sm.call_claude.side_effect = side_effect

        result = mod.generate_docs("owner", "repo", "url")
        assert result["ARCHITECTURE.md"] == "ARCH_DOC"

    def test_runbook_content_comes_from_claude(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        def side_effect(system, user):
            if "runbook" in system.lower() or "devops" in system.lower():
                return "RUNBOOK_DOC"
            return "OTHER"

        sm.call_claude.side_effect = side_effect

        result = mod.generate_docs("owner", "repo", "url")
        assert result["RUNBOOK.md"] == "RUNBOOK_DOC"

    def test_repo_owner_and_name_included_in_claude_prompt(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        mod.generate_docs("acme-corp", "payment-service", "url")

        for c in sm.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme-corp" in user_prompt
            assert "payment-service" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, module_and_shared):
        mod, sm = module_and_shared
        long_content = "x" * 10_000
        sm.get_repo_files.return_value = {"bigfile.py": long_content}

        mod.generate_docs("owner", "repo", "url")

        # Verify that the user prompt sent to Claude doesn't contain the full 10k string
        # (it should be truncated to 4000 chars per file)
        for c in sm.call_claude.call_args_list:
            user_prompt = c[0][1]
            # The sliced content should be 4000 x's followed by newline/backtick
            assert "x" * 4001 not in user_prompt

    def test_no_files_found_shows_placeholder(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "url")

        # With no files, the formatted string should be "_No files found_"
        # Verify Claude is still called (not skipped)
        assert sm.call_claude.call_count == 3
        first_user_prompt = sm.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in first_user_prompt

    def test_with_synthetic_py_files(self, module_and_shared):
        mod, sm = module_and_shared

        def get_files_side_effect(owner, repo, exts, max_files=15):
            if ".py" in exts:
                return SYNTHETIC_PY_FILES
            return SYNTHETIC_IAC_FILES

        sm.get_repo_files.side_effect = get_files_side_effect
        sm.call_claude.return_value = "# doc"

        result = mod.generate_docs("my-org", "my-repo", "url")

        assert len(result) == 3
        # File names appear in the Claude prompts
        readme_prompt = sm.call_claude.call_args_list[0][0][1]
        assert "app/main.py" in readme_prompt

    def test_with_synthetic_iac_files(self, module_and_shared):
        mod, sm = module_and_shared

        def get_files_side_effect(owner, repo, exts, max_files=15):
            if ".tf" in exts:
                return SYNTHETIC_IAC_FILES
            return {}

        sm.get_repo_files.side_effect = get_files_side_effect
        sm.call_claude.return_value = "# arch"

        mod.generate_docs("my-org", "my-repo", "url")

        arch_prompt = sm.call_claude.call_args_list[1][0][1]
        assert "infra/main.tf" in arch_prompt

    def test_max_files_limits_respected(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        mod.generate_docs("owner", "repo", "url")

        code_call = sm.get_repo_files.call_args_list[0]
        iac_call = sm.get_repo_files.call_args_list[1]

        code_max = code_call[1].get("max_files") or code_call[0][3]
        iac_max = iac_call[1].get("max_files") or iac_call[0][3]

        assert code_max == 15
        assert iac_max == 10

    def test_claude_error_propagates(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}
        sm.call_claude.side_effect = RuntimeError("Claude API failure")

        with pytest.raises(RuntimeError, match="Claude API failure"):
            mod.generate_docs("owner", "repo", "url")

    def test_get_repo_files_error_propagates(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("owner", "repo", "url")


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, module):
        result = module.build_index("owner", "repo", {"README.md": ""}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo_in_title(self, module):
        result = module.build_index("acme", "payments", {"README.md": ""}, "2024-01-01 00:00 UTC")
        assert "acme/payments" in result

    def test_contains_timestamp(self, module):
        result = module.build_index("owner", "repo", {}, "2024-06-15 12:30 UTC")
        assert "2024-06-15 12:30 UTC" in result

    def test_contains_link_for_each_doc(self, module):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "b"}
        result = module.build_index("owner", "repo", docs, "now")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, module):
        docs = {"README.md": ""}
        result = module.build_index("src-owner", "src-repo", docs, "now")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_correct_path_prefix(self, module):
        docs = {"README.md": ""}
        result = module.build_index("owner", "repo", docs, "now")
        assert "tech-docs/owner-repo/README.md" in result

    def test_links_are_github_urls(self, module):
        docs = {"README.md": ""}
        result = module.build_index("owner", "repo", docs, "now")
        assert "https://github.com/" in result

    def test_empty_docs_dict(self, module):
        result = module.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")
        assert "owner/repo" in result
        assert "2024-01-01 00:00 UTC" in result

    def test_contains_autogenerated_footer(self, module):
        result = module.build_index("owner", "repo", {}, "now")
        assert "Auto-generated" in result

    @pytest.mark.parametrize("owner,repo", [
        ("org-with-dashes", "repo-with-dashes"),
        ("