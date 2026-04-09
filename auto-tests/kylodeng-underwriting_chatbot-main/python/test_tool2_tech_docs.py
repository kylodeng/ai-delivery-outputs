"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown output, link construction, timestamp inclusion
- fmt() helper (indirectly via generate_docs): empty dict, single file, multiple files, content truncation
- __main__ block: success path, exception/failure path, env-var handling

Mocks used:
- shared.call_claude          → unittest.mock.patch
- shared.get_repo_files       → unittest.mock.patch
- shared.write_output_file    → unittest.mock.patch
- shared.send_email           → unittest.mock.patch
- shared.email_html           → unittest.mock.patch
- shared.write_audit_entry    → unittest.mock.patch
- shared.OUTPUT_REPO_OWNER    → patched as module-level constant
- shared.OUTPUT_REPO          → patched as module-level constant
- datetime.datetime.utcnow    → unittest.mock.patch

TODOs:
- TODO: Integration test requiring real GitHub token + real Claude API key
- TODO: Test behaviour when get_repo_files raises a network error mid-flight
- TODO: Verify exact Claude prompt text matches expected strings (needs spec freeze)
"""

import importlib
import sys
import os
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared dependencies stubbed
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot"
FAKE_OUTPUT_REPO = "output-repo"


def _make_shared_stub():
    """Return a minimal fake 'shared' module so the import doesn't blow up."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/out")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module():
    """
    Import (or re-import) tool2_tech_docs with a fresh stub for 'shared'.
    Returns the module object.
    """
    # Remove cached copies so each test group gets a clean slate
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key or key == "shared":
            del sys.modules[key]

    stub = _make_shared_stub()
    sys.modules["shared"] = stub

    # Add the script directory to sys.path if needed
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.abspath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import tool2_tech_docs as m
    return m, stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def module_and_shared():
    """Provide (module, shared_stub) with a guaranteed clean import."""
    m, stub = _import_module()
    return m, stub


@pytest.fixture()
def module(module_and_shared):
    m, _ = module_and_shared
    return m


@pytest.fixture()
def shared_stub(module_and_shared):
    _, stub = module_and_shared
    return stub


# ---------------------------------------------------------------------------
# build_index tests
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_returns_string(self, module):
        result = module.build_index("owner", "repo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_repo(self, module):
        result = module.build_index("myowner", "myrepo", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "myowner/myrepo" in result

    def test_contains_timestamp(self, module):
        ts = "2024-06-15 12:30 UTC"
        result = module.build_index("o", "r", {"README.md": "x"}, ts)
        assert ts in result

    def test_contains_links_for_all_docs(self, module):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = module.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_uses_output_repo_owner_and_repo(self, module):
        result = module.build_index("owner", "repo", {"README.md": "x"}, "now")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_url_format(self, module):
        result = module.build_index("myowner", "myrepo", {"README.md": "x"}, "now")
        expected_fragment = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/myowner-myrepo/README.md"
        )
        assert expected_fragment in result

    def test_auto_generated_footer(self, module):
        result = module.build_index("o", "r", {}, "now")
        assert "Auto-generated" in result

    def test_empty_docs_dict(self, module):
        result = module.build_index("o", "r", {}, "now")
        assert isinstance(result, str)
        # No links section should still produce a valid index
        assert "# Tech Documentation Index" in result

    def test_single_doc(self, module):
        result = module.build_index("o", "r", {"README.md": "content"}, "2024-01-01 00:00 UTC")
        assert "README.md" in result

    def test_special_characters_in_owner_repo(self, module):
        # GitHub org names can contain hyphens
        result = module.build_index("my-org", "my-repo", {"README.md": "x"}, "now")
        assert "my-org/my-repo" in result
        assert "my-org-my-repo" in result  # path uses dashes


# ---------------------------------------------------------------------------
# generate_docs tests
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def _setup_get_repo_files(self, shared_stub, py_files=None, iac_files=None):
        """
        get_repo_files is called twice: first for source files, then for IaC files.
        """
        py_files = py_files if py_files is not None else {}
        iac_files = iac_files if iac_files is not None else {}
        shared_stub.get_repo_files.side_effect = [py_files, iac_files]

    def test_returns_three_docs(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        shared_stub.call_claude.return_value = "# Doc"
        result = module.generate_docs("owner", "repo", "https://run")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("owner", "repo", "https://run")
        assert shared_stub.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_source_extensions(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("owner", "repo", "https://run")
        first_call_args = shared_stub.get_repo_files.call_args_list[0]
        exts = first_call_args[0][2] if len(first_call_args[0]) > 2 else first_call_args[1].get("extensions", first_call_args[0][2])
        # Positional: owner, repo, extensions, max_files
        positional = first_call_args[0]
        assert ".py" in positional[2]
        assert ".js" in positional[2]

    def test_get_repo_files_called_with_iac_extensions(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("owner", "repo", "https://run")
        second_call_args = shared_stub.get_repo_files.call_args_list[1]
        positional = second_call_args[0]
        assert ".tf" in positional[2]
        assert ".yaml" in positional[2] or ".yml" in positional[2]

    def test_calls_call_claude_three_times(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("owner", "repo", "https://run")
        assert shared_stub.call_claude.call_count == 3

    def test_readme_content_comes_from_call_claude(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        shared_stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = module.generate_docs("owner", "repo", "https://run")
        assert result["README.md"] == "README content"

    def test_arch_content_comes_from_call_claude(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        shared_stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = module.generate_docs("owner", "repo", "https://run")
        assert result["ARCHITECTURE.md"] == "ARCH content"

    def test_runbook_content_comes_from_call_claude(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        shared_stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = module.generate_docs("owner", "repo", "https://run")
        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_owner_repo_passed_to_get_repo_files(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("myowner", "myrepo", "https://run")
        for c in shared_stub.get_repo_files.call_args_list:
            assert c[0][0] == "myowner"
            assert c[0][1] == "myrepo"

    def test_owner_repo_in_claude_prompt(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("myowner", "myrepo", "https://run")
        for c in shared_stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "myowner/myrepo" in user_prompt

    def test_with_nonempty_source_files(self, module, shared_stub):
        py_files = {"main.py": "print('hello')", "utils.py": "def foo(): pass"}
        self._setup_get_repo_files(shared_stub, py_files=py_files)
        shared_stub.call_claude.return_value = "# Doc"
        result = module.generate_docs("owner", "repo", "https://run")
        assert len(result) == 3

    def test_with_nonempty_iac_files(self, module, shared_stub):
        iac_files = {"main.tf": 'resource "aws_s3_bucket" "b" {}'}
        self._setup_get_repo_files(shared_stub, iac_files=iac_files)
        shared_stub.call_claude.return_value = "# Doc"
        result = module.generate_docs("owner", "repo", "https://run")
        assert "ARCHITECTURE.md" in result

    def test_content_truncated_to_4000_chars_in_prompt(self, module, shared_stub):
        """Files with content longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10_000
        py_files = {"big_file.py": long_content}
        self._setup_get_repo_files(shared_stub, py_files=py_files)
        shared_stub.call_claude.return_value = "# Doc"
        module.generate_docs("owner", "repo", "https://run")
        # The user prompt for README includes the file — check truncation happened
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # Truncated content is 4000 chars max
        assert "x" * 4001 not in user_prompt
        assert "x" * 4000 in user_prompt

    def test_no_files_produces_no_files_found_in_prompt(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub, py_files={}, iac_files={})
        module.generate_docs("owner", "repo", "https://run")
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_readme_uses_system_readme_prompt(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("owner", "repo", "https://run")
        readme_call = shared_stub.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_arch_uses_system_arch_prompt(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("owner", "repo", "https://run")
        arch_call = shared_stub.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "IaC" in system_prompt or "architecture" in system_prompt.lower()

    def test_runbook_uses_system_runbook_prompt(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        module.generate_docs("owner", "repo", "https://run")
        runbook_call = shared_stub.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "runbook" in system_prompt.lower() or "DevOps" in system_prompt

    def test_call_claude_raises_propagates(self, module, shared_stub):
        self._setup_get_repo_files(shared_stub)
        shared_stub.call_claude.side_effect = RuntimeError("Claude API error")
        with pytest.raises(RuntimeError, match="Claude API error"):
            module.generate_docs("owner", "repo", "https://run")

    def test_get_repo_files_raises_propagates(self, module, shared_stub):
        shared_stub.get_