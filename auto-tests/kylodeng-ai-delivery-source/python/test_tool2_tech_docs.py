"""
Tests for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets, Claude error propagation
- build_index(): happy path, empty docs dict, multiple docs, URL construction, timestamp inclusion
- __main__ block: success path, exception/failure path, env-var handling
- fmt() inner function behaviour (via generate_docs integration)

Mocks used:
- shared.call_claude          — prevents real Anthropic API calls
- shared.get_repo_files       — prevents real GitHub API calls
- shared.write_output_file    — prevents real GitHub writes
- shared.send_email           — prevents real SMTP/SES calls
- shared.email_html           — prevents real template rendering
- shared.write_audit_entry    — prevents real audit writes
- shared.OUTPUT_REPO_OWNER    — patched to deterministic test value
- shared.OUTPUT_REPO          — patched to deterministic test value
- datetime.datetime.utcnow    — deterministic timestamps in __main__ tests

TODOs:
- TODO: Integration test against a real (sandbox) GitHub repo requires credentials
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME are None (currently passed raw to generate_docs)
- TODO: Verify exact Claude prompt strings if prompt-contract tests are required
"""

import importlib
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared patched out
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"

def _make_shared_stub():
    """Return a stub 'shared' module so the import in tool2_tech_docs succeeds."""
    stub = types.ModuleType("shared")
    stub.call_claude       = MagicMock(return_value="mocked doc content")
    stub.get_repo_files    = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value="https://github.com/out/repo/blob/main/file.md")
    stub.send_email        = MagicMock()
    stub.email_html        = MagicMock(return_value="<html>mock</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "test-output-owner"
    stub.OUTPUT_REPO       = "test-output-repo"
    return stub


def _import_module(shared_stub=None):
    """(Re-)import tool2_tech_docs with the given shared stub injected."""
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Remove cached versions so we get a fresh import each time
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]

    sys.modules["shared"] = shared_stub

    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import importlib.util
    spec_path = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts", "tool2_tech_docs.py"
    )
    spec_path = os.path.normpath(spec_path)
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", spec_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, shared_stub


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
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_contains_repo_name(self, module_and_stub):
        mod, stub = module_and_stub
        result = mod.build_index("myowner", "myrepo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "myowner/myrepo" in result

    def test_happy_path_contains_timestamp(self, module_and_stub):
        mod, stub = module_and_stub
        result = mod.build_index("o", "r", {"README.md": "x"}, "2024-06-01 09:30 UTC")
        assert "2024-06-01 09:30 UTC" in result

    def test_happy_path_links_all_docs(self, module_and_stub):
        mod, stub = module_and_stub
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = mod.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_uses_output_repo_owner_and_repo(self, module_and_stub):
        mod, stub = module_and_stub
        # OUTPUT_REPO_OWNER and OUTPUT_REPO come from the stub ("test-output-owner"/"test-output-repo")
        result = mod.build_index("src-owner", "src-repo", {"README.md": "x"}, "now")
        assert "test-output-owner" in result
        assert "test-output-repo" in result

    def test_link_path_includes_owner_and_repo(self, module_and_stub):
        mod, stub = module_and_stub
        result = mod.build_index("alice", "project", {"README.md": "x"}, "now")
        assert "tech-docs/alice-project/README.md" in result

    def test_empty_docs_dict_returns_string(self, module_and_stub):
        mod, stub = module_and_stub
        result = mod.build_index("o", "r", {}, "2024-01-01 00:00 UTC")
        assert isinstance(result, str)
        assert "o/r" in result

    def test_contains_auto_generated_footer(self, module_and_stub):
        mod, stub = module_and_stub
        result = mod.build_index("o", "r", {"README.md": "x"}, "now")
        assert "Auto-generated" in result

    def test_multiple_docs_each_produce_a_link(self, module_and_stub):
        mod, stub = module_and_stub
        docs = {f"DOC_{i}.md": f"content {i}" for i in range(5)}
        result = mod.build_index("o", "r", docs, "ts")
        for name in docs:
            assert name in result

    def test_owner_with_special_chars_in_path(self, module_and_stub):
        """Hyphens in owner/repo names are common on GitHub."""
        mod, stub = module_and_stub
        result = mod.build_index("my-org", "my-repo", {"README.md": "x"}, "ts")
        assert "tech-docs/my-org-my-repo/README.md" in result

    def test_return_type_is_str(self, module_and_stub):
        mod, stub = module_and_stub
        result = mod.build_index("o", "r", {"A.md": "b"}, "ts")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {"main.py": "print('hello')"}
        stub.call_claude.return_value = "# Generated doc"

        result = mod.generate_docs("owner", "repo", "https://run.url")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_invoked_three_times(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {"app.py": "code"}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("owner", "repo", "https://run.url")

        assert stub.call_claude.call_count == 3

    def test_get_repo_files_called_for_code_and_iac(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("owner", "repo", "https://run.url")

        # Called twice: once for code files, once for IaC files
        assert stub.get_repo_files.call_count == 2

    def test_code_files_extensions_passed(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("owner", "repo", "url")

        first_call_extensions = stub.get_repo_files.call_args_list[0][0][2]
        assert ".py" in first_call_extensions
        assert ".js" in first_call_extensions
        assert ".ts" in first_call_extensions
        assert ".go" in first_call_extensions

    def test_iac_extensions_passed(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("owner", "repo", "url")

        second_call_extensions = stub.get_repo_files.call_args_list[1][0][2]
        assert ".tf" in second_call_extensions
        assert ".yaml" in second_call_extensions
        assert ".yml" in second_call_extensions

    def test_doc_values_equal_claude_return_value(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {"file.py": "code"}
        stub.call_claude.return_value = "SPECIFIC_CONTENT"

        result = mod.generate_docs("owner", "repo", "url")

        for content in result.values():
            assert content == "SPECIFIC_CONTENT"

    def test_empty_repo_produces_no_files_found_string(self, module_and_stub):
        """When no files are returned the fmt helper emits '_No files found_'."""
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("owner", "repo", "url")

        # Verify that call_claude was called (even with empty content)
        assert stub.call_claude.call_count == 3
        # The prompt passed to call_claude should contain the no-files marker
        for c in stub.call_claude.call_args_list:
            prompt_arg = c[0][1]  # positional arg 2 (user prompt)
            assert "_No files found_" in prompt_arg

    def test_claude_error_propagates(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            mod.generate_docs("owner", "repo", "url")

    def test_file_content_truncated_to_4000_chars(self, module_and_stub):
        """Files longer than 4000 chars should be truncated in the prompt."""
        mod, stub = module_and_stub
        long_content = "x" * 10_000
        stub.get_repo_files.return_value = {"big_file.py": long_content}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("owner", "repo", "url")

        # Inspect what was passed to call_claude for README
        readme_prompt = stub.call_claude.call_args_list[0][0][1]
        # The full 10k content must NOT appear; only up to 4000 chars of it
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_owner_and_repo_appear_in_prompts(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("acme-corp", "killer-app", "url")

        for c in stub.call_claude.call_args_list:
            prompt = c[0][1]
            assert "acme-corp/killer-app" in prompt

    def test_correct_system_prompts_used(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("o", "r", "url")

        system_prompts = [c[0][0] for c in stub.call_claude.call_args_list]
        assert mod.SYSTEM_README  in system_prompts
        assert mod.SYSTEM_ARCH    in system_prompts
        assert mod.SYSTEM_RUNBOOK in system_prompts

    def test_get_repo_files_called_with_correct_owner_repo(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("myowner", "myrepo", "url")

        for c in stub.get_repo_files.call_args_list:
            assert c[0][0] == "myowner"
            assert c[0][1] == "myrepo"

    def test_max_files_limits_passed(self, module_and_stub):
        mod, stub = module_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc"

        mod.generate_docs("o", "r", "url")

        calls = stub.get_repo_files.call_args_list
        # First call: code files max_files=15
        assert calls[0][1].get("max_files") == 15 or calls[0][0][3] == 15
        # Second call: IaC files max_files=10
        # Accept either kwargs or positional
        second_max = calls[1][1].get("max_files") if "max_files" in calls[1][1] else calls[1][0][3]
        assert second_max == 10


# ---------------------------------------------------------------------------
# Tests for the __main__ block (success path)
# ---------------------------------------------------------------------------

class TestMainSuccess:

    def _run_main(self, stub, env_overrides=None):
        """Execute the __main__ block with controlled environment."""
        env = {
            "SOURCE_REPO_OWNER": "test-owner",
            "SOURCE_REPO_NAME":  "test-repo",
            "GITHUB_RUN_URL":    "https://github.com/runs/42",
        }
        if env_overrides:
            env.update(env_overrides)

        stub.get_repo_files.return_value = {"app.py": "code"}
        stub.call_claude.return_value    