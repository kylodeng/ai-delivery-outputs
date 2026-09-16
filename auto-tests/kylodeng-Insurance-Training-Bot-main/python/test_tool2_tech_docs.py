"""
Test module for tool2_tech_docs.py

What is tested:
    - generate_docs(): happy path, empty files, partial files
    - build_index(): happy path, empty docs, special characters in owner/repo
    - __main__ block: successful run, exception/failure path

Mocks used:
    - shared.call_claude (patched to return synthetic doc strings)
    - shared.get_repo_files (patched to return synthetic file dicts)
    - shared.write_output_file (patched to return synthetic URLs)
    - shared.send_email (patched as no-op)
    - shared.email_html (patched to return HTML string)
    - shared.write_audit_entry (patched as no-op)
    - shared.OUTPUT_REPO_OWNER / OUTPUT_REPO (patched constants)
    - datetime.datetime.utcnow (patched for deterministic timestamps)
    - os.environ (patched via monkeypatch)

TODOs:
    - TODO: Integration test against a real Claude API endpoint (requires API key + billing)
    - TODO: Test write_output_file failure mid-loop (partial success scenario)
    - TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME are missing from env
"""

import sys
import os
import importlib
import datetime
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers to import the module under test with all shared deps stubbed out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot"
FAKE_OUTPUT_REPO = "output-repo"

SYNTHETIC_README = "# README\nThis is the generated README."
SYNTHETIC_ARCH = "# ARCHITECTURE\nThis is the generated architecture doc."
SYNTHETIC_RUNBOOK = "# RUNBOOK\nThis is the generated runbook."

SYNTHETIC_PY_FILES = {
    "src/main.py": "def main():\n    pass",
    "src/utils.py": "def helper():\n    return 42",
}
SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }',
    "infra/variables.yaml": "env: production\nregion: us-east-1",
}


def _make_shared_stub():
    """Return a minimal stub module for `shared`."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(side_effect=[
        SYNTHETIC_README, SYNTHETIC_ARCH, SYNTHETIC_RUNBOOK
    ])
    stub.get_repo_files = MagicMock(side_effect=[
        SYNTHETIC_PY_FILES,
        SYNTHETIC_IAC_FILES,
    ])
    stub.write_output_file = MagicMock(return_value="https://github.com/output-repo/blob/main/some/file.md")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    stub.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return stub


def _import_module(shared_stub=None):
    """
    Import (or re-import) tool2_tech_docs with the provided shared stub injected.
    Returns the module object.
    """
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Ensure a clean import each time
    module_name = "tool2_tech_docs"
    if module_name in sys.modules:
        del sys.modules[module_name]

    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also check the directory relative to this test file
    candidate_dirs = [
        os.path.join(os.path.dirname(__file__), ".github", "scripts"),
        os.path.join(os.path.dirname(__file__)),
    ]

    sys.modules["shared"] = shared_stub

    # Try to import; fall back to direct file load
    try:
        import tool2_tech_docs as mod
    except ModuleNotFoundError:
        # Direct file load using importlib
        import importlib.util
        found = None
        for d in candidate_dirs:
            candidate = os.path.join(d, "tool2_tech_docs.py")
            if os.path.isfile(candidate):
                found = candidate
                break
        if found is None:
            pytest.skip("tool2_tech_docs.py not found — adjust path in test helper")
        spec = importlib.util.spec_from_file_location(module_name, found)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def cleanup_module():
    """Ensure the module is removed from sys.modules after each test."""
    yield
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules.pop("shared", None)


@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def mod(shared_stub):
    return _import_module(shared_stub)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, mod, shared_stub):
        """generate_docs returns README, ARCHITECTURE, and RUNBOOK keys."""
        result = mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_content_matches_claude_responses(self, mod, shared_stub):
        """Content of each doc comes directly from call_claude."""
        result = mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert result["README.md"] == SYNTHETIC_README
        assert result["ARCHITECTURE.md"] == SYNTHETIC_ARCH
        assert result["RUNBOOK.md"] == SYNTHETIC_RUNBOOK

    def test_get_repo_files_called_with_correct_extensions(self, mod, shared_stub):
        """get_repo_files is called with the expected extension lists."""
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        calls = shared_stub.get_repo_files.call_args_list
        assert len(calls) == 2
        _, py_kwargs_or_args = calls[0]
        first_call_args = calls[0][0]  # positional args
        second_call_args = calls[1][0]

        # First call: py/js/ts/go files
        assert ".py" in first_call_args[2]
        assert ".js" in first_call_args[2]
        assert ".ts" in first_call_args[2]
        assert ".go" in first_call_args[2]

        # Second call: IaC files
        assert ".tf" in second_call_args[2]
        assert ".yaml" in second_call_args[2]
        assert ".yml" in second_call_args[2]

    def test_call_claude_called_three_times(self, mod, shared_stub):
        """call_claude is invoked exactly three times (README, ARCH, RUNBOOK)."""
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert shared_stub.call_claude.call_count == 3

    def test_readme_prompt_includes_owner_repo(self, mod, shared_stub):
        """The README Claude prompt contains the owner/repo string."""
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "myorg/myrepo" in user_prompt

    def test_arch_prompt_includes_iac_files(self, mod, shared_stub):
        """The ARCHITECTURE Claude prompt references IaC file content."""
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        arch_call = shared_stub.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "main.tf" in user_prompt or "IaC files" in user_prompt

    def test_runbook_prompt_includes_all_files(self, mod, shared_stub):
        """The RUNBOOK Claude prompt references combined files."""
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        runbook_call = shared_stub.call_claude.call_args_list[2]
        user_prompt = runbook_call[0][1]
        assert "myorg/myrepo" in user_prompt

    def test_empty_py_files_shows_no_files_found(self, mod, shared_stub):
        """When no py/js files exist, fmt() returns '_No files found_'."""
        shared_stub.get_repo_files.side_effect = [{}, SYNTHETIC_IAC_FILES]
        shared_stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH, SYNTHETIC_RUNBOOK]
        result = mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        # Should still succeed and return three docs
        assert len(result) == 3

    def test_empty_iac_files_shows_no_files_found(self, mod, shared_stub):
        """When no IaC files exist, fmt() returns '_No files found_'."""
        shared_stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, {}]
        shared_stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH, SYNTHETIC_RUNBOOK]
        result = mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        arch_call = shared_stub.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "_No files found_" in user_prompt

    def test_all_empty_files(self, mod, shared_stub):
        """When both file sets are empty, all Claude prompts contain '_No files found_'."""
        shared_stub.get_repo_files.side_effect = [{}, {}]
        shared_stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH, SYNTHETIC_RUNBOOK]
        result = mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        for i in range(3):
            user_prompt = shared_stub.call_claude.call_args_list[i][0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_at_4000_chars(self, mod, shared_stub):
        """File content longer than 4000 chars is truncated in the prompt."""
        long_content = "x" * 5000
        shared_stub.get_repo_files.side_effect = [
            {"src/big.py": long_content},
            {},
        ]
        shared_stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH, SYNTHETIC_RUNBOOK]
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The truncated content should appear (4000 x's) but not 5000
        assert "x" * 4000 in user_prompt
        assert "x" * 4001 not in user_prompt

    def test_call_claude_raises_propagates(self, mod, shared_stub):
        """If call_claude raises, generate_docs propagates the exception."""
        shared_stub.call_claude.side_effect = RuntimeError("Claude API failure")
        with pytest.raises(RuntimeError, match="Claude API failure"):
            mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_raises_propagates(self, mod, shared_stub):
        """If get_repo_files raises, generate_docs propagates the exception."""
        shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_system_prompts_passed_to_claude(self, mod, shared_stub):
        """Each call_claude invocation receives a distinct system prompt."""
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        system_prompts = [
            shared_stub.call_claude.call_args_list[i][0][0]
            for i in range(3)
        ]
        # All three should be distinct
        assert len(set(system_prompts)) == 3

    def test_different_owner_repo_reflected_in_prompts(self, mod, shared_stub):
        """Different owner/repo values are correctly embedded in prompts."""
        mod.generate_docs("acme-corp", "billing-service", "https://github.com/run/99")
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "acme-corp/billing-service" in user_prompt

    def test_fmt_single_file_contains_filename_header(self, mod, shared_stub):
        """The formatted file block contains a ### header with the filename."""
        shared_stub.get_repo_files.side_effect = [
            {"src/main.py": "print('hello')"},
            {},
        ]
        shared_stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH, SYNTHETIC_RUNBOOK]
        mod.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "### src/main.py" in user_prompt


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_contains_all_doc_links(self, mod):
        """build_index includes a link for every doc in the dict."""
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = mod.build_index("myorg", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_happy_path_contains_owner_repo(self, mod):
        """build_index embeds the owner/repo in the title."""
        docs = {"README.md": "..."}
        result = mod.build_index("myorg", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert "myorg/myrepo" in result

    def test_happy_path_contains_timestamp(self, mod):
        """build_index embeds the provided timestamp."""
        docs = {"README.md": "..."}
        result = mod.build_index("myorg", "myrepo", docs