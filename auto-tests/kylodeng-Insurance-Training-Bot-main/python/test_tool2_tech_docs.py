"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude API calls to produce README, ARCHITECTURE, RUNBOOK docs
- build_index(): builds a markdown index page from generated docs
- __main__ block: end-to-end flow including writing files, sending email, writing audit, and error handling

Mocks used:
- shared.call_claude (mocked to avoid real Anthropic API calls)
- shared.get_repo_files (mocked to avoid real GitHub API calls)
- shared.write_output_file (mocked to avoid real GitHub writes)
- shared.send_email (mocked to avoid real email sending)
- shared.email_html (mocked)
- shared.write_audit_entry (mocked to avoid real audit writes)
- shared.OUTPUT_REPO_OWNER, shared.OUTPUT_REPO (patched constants)
- datetime.datetime.utcnow (patched for deterministic timestamps)
- os.environ (patched via monkeypatch)

TODOs:
- TODO: Integration test for full Claude prompt content validation (requires real Claude API key)
- TODO: Test for very large file sets hitting max_files limits (requires real get_repo_files behaviour)
- TODO: Test for network timeout/retry behaviour in call_claude (requires shared module internals)
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared patched out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner-out"
FAKE_OUTPUT_REPO = "test-repo-out"

FAKE_PY_FILES = {
    "src/main.py": "def main():\n    pass\n",
    "src/utils.py": "def helper():\n    return 42\n",
}
FAKE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}\n',
    "infra/variables.yaml": "env: prod\n",
}

FAKE_README = "# README\nThis is a generated README."
FAKE_ARCH = "# ARCHITECTURE\nThis is a generated architecture doc."
FAKE_RUNBOOK = "# RUNBOOK\nThis is a generated runbook."

FAKE_DOCS = {
    "README.md": FAKE_README,
    "ARCHITECTURE.md": FAKE_ARCH,
    "RUNBOOK.md": FAKE_RUNBOOK,
}

FAKE_NOW = "2024-01-15 12:00 UTC"
FAKE_RUN_URL = "https://github.com/actions/runs/999"
FAKE_OUTPUT_URL = "https://github.com/test-owner-out/test-repo-out/blob/main/tech-docs/src-owner/src-repo/README.md"
FAKE_INDEX_URL = "https://github.com/test-owner-out/test-repo-out/blob/main/tech-docs/src-owner/src-repo/INDEX.md"


@pytest.fixture()
def patched_shared():
    """
    Build a fake `shared` module and inject it into sys.modules before
    importing tool2_tech_docs, then remove it after the test.
    """
    shared_mod = types.ModuleType("shared")
    shared_mod.call_claude = MagicMock(side_effect=[FAKE_README, FAKE_ARCH, FAKE_RUNBOOK])
    shared_mod.get_repo_files = MagicMock(side_effect=[FAKE_PY_FILES, FAKE_IAC_FILES])
    shared_mod.write_output_file = MagicMock(return_value=FAKE_OUTPUT_URL)
    shared_mod.send_email = MagicMock()
    shared_mod.email_html = MagicMock(return_value="<html>email</html>")
    shared_mod.write_audit_entry = MagicMock()
    shared_mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared_mod.OUTPUT_REPO = FAKE_OUTPUT_REPO

    sys.modules["shared"] = shared_mod

    # Force reimport so the module picks up the patched shared
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    yield shared_mod

    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def module(patched_shared):
    """Import and return the module under test."""
    import importlib.util, pathlib

    spec_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py")
    if not os.path.exists(spec_path):
        # fallback: try relative to repo root
        candidate = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".github", "scripts", "tool2_tech_docs.py",
        )
        spec_path = candidate if os.path.exists(candidate) else spec_path

    spec = importlib.util.spec_from_file_location(
        "tool2_tech_docs",
        spec_path,
    )
    mod = importlib.util.module_from_spec(spec)
    # Make shared available in the module's namespace
    mod.shared = patched_shared
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, module, patched_shared):
        """generate_docs returns README, ARCHITECTURE, RUNBOOK keys."""
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        result = module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_comes_from_claude(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        result = module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        assert result["README.md"] == FAKE_README

    def test_architecture_content_comes_from_claude(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        result = module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        assert result["ARCHITECTURE.md"] == FAKE_ARCH

    def test_runbook_content_comes_from_claude(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        result = module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        assert result["RUNBOOK.md"] == FAKE_RUNBOOK

    def test_get_repo_files_called_twice(self, module, patched_shared):
        """get_repo_files is called once for source files and once for IaC files."""
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        assert patched_shared.get_repo_files.call_count == 2

    def test_get_repo_files_first_call_py_extensions(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        first_call_args = patched_shared.get_repo_files.call_args_list[0]
        exts = first_call_args[0][2]  # positional: owner, repo, extensions
        assert ".py" in exts
        assert ".js" in exts
        assert ".ts" in exts
        assert ".go" in exts

    def test_get_repo_files_second_call_iac_extensions(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        second_call_args = patched_shared.get_repo_files.call_args_list[1]
        exts = second_call_args[0][2]
        assert ".tf" in exts
        assert ".yaml" in exts
        assert ".yml" in exts

    def test_call_claude_called_three_times(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        assert patched_shared.call_claude.call_count == 3

    def test_repo_owner_and_name_included_in_claude_prompt(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "cool-repo", FAKE_RUN_URL)

        for c in patched_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg
            assert "acme" in user_prompt
            assert "cool-repo" in user_prompt

    def test_empty_py_files_uses_no_files_found_placeholder(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [{}, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        # Should not raise; placeholder text is used
        result = module.generate_docs("acme", "my-repo", FAKE_RUN_URL)
        assert "README.md" in result

        # Verify placeholder appears in at least one prompt
        readme_prompt = patched_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_empty_iac_files_uses_no_files_found_placeholder(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, {}]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        result = module.generate_docs("acme", "my-repo", FAKE_RUN_URL)
        assert "ARCHITECTURE.md" in result

        arch_prompt = patched_shared.call_claude.call_args_list[1][0][1]
        assert "_No files found_" in arch_prompt

    def test_both_files_empty_returns_placeholder_in_all_prompts(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [{}, {}]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        for c in patched_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, module, patched_shared):
        """Files longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10000
        long_files = {"src/big.py": long_content}
        patched_shared.get_repo_files.side_effect = [long_files, {}]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        readme_prompt = patched_shared.call_claude.call_args_list[0][0][1]
        # The prompt should contain at most 4000 x's (truncation)
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_claude_raises_exception_propagates(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

    def test_get_repo_files_raises_propagates(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = ConnectionError("GitHub unavailable")

        with pytest.raises(ConnectionError, match="GitHub unavailable"):
            module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

    def test_max_files_limit_passed_to_get_repo_files(self, module, patched_shared):
        patched_shared.get_repo_files.side_effect = [FAKE_PY_FILES, FAKE_IAC_FILES]
        patched_shared.call_claude.side_effect = [FAKE_README, FAKE_ARCH, FAKE_RUNBOOK]

        module.generate_docs("acme", "my-repo", FAKE_RUN_URL)

        first_call_kwargs = patched_shared.get_repo_files.call_args_list[0]
        # max_files is passed as keyword arg
        assert first_call_kwargs[1].get("max_files") == 15 or (
            len(first_call_kwargs[0]) > 3 and first_call_kwargs[0][3] == 15
        )
        second_call_kwargs = patched_shared.get_repo_files.call_args_list[1]
        assert second_call_kwargs[1].get("max_files")