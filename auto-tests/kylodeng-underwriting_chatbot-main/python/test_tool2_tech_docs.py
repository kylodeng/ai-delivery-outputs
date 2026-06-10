"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): happy path, empty files, partial files, Claude call arguments
- build_index(): correct markdown output, URL construction, edge cases (empty docs, many docs)
- __main__ block: success path, failure/exception path (via subprocess or importlib tricks)
- fmt() inner function behaviour (indirectly through generate_docs)

Mocks used:
- shared.call_claude            — stubbed to return deterministic strings
- shared.get_repo_files         — stubbed to return dict of file paths → content
- shared.write_output_file      — stubbed to return a fake URL string
- shared.send_email             — stubbed (no-op)
- shared.email_html             — stubbed to return a simple HTML string
- shared.write_audit_entry      — stubbed (no-op)
- shared.OUTPUT_REPO_OWNER      — patched to a fixed string
- shared.OUTPUT_REPO            — patched to a fixed string
- datetime.datetime.utcnow      — patched for deterministic timestamps

TODOs:
- TODO: Integration test calling real Claude API (requires API key + credits)
- TODO: Test actual GitHub write behaviour in write_output_file (needs GH token)
- TODO: Test that max_files limits are respected at the shared layer
"""

import importlib
import os
import sys
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so we can import the target
# without needing the real dependency installed.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a types.ModuleType that satisfies every import in tool2_tech_docs."""
    mod = types.ModuleType("shared")
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    mod.call_claude = MagicMock(return_value="# Generated doc content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    return mod


# ---------------------------------------------------------------------------
# Module-level fixture: load tool2_tech_docs with a patched shared module.
# We reload for every test session to keep isolation simple.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fake_shared():
    """Install fake shared module and yield it; tear down afterwards."""
    shared_mod = _make_fake_shared()
    sys.modules["shared"] = shared_mod

    # Make sure the scripts directory is on the path so the import works
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try relative path resolution
    alt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    yield shared_mod

    # Cleanup
    sys.modules.pop("shared", None)


@pytest.fixture()
def tool2(fake_shared):
    """Import (or reload) tool2_tech_docs with the fake shared in place."""
    # Remove cached version so we get a fresh import with current fake_shared state
    sys.modules.pop("tool2_tech_docs", None)
    # Reset all mocks on fake_shared before each test
    fake_shared.call_claude.reset_mock()
    fake_shared.call_claude.return_value = "# Generated doc content"
    fake_shared.get_repo_files.reset_mock()
    fake_shared.get_repo_files.return_value = {}
    fake_shared.write_output_file.reset_mock()
    fake_shared.write_output_file.return_value = "https://github.com/fake/url"
    fake_shared.send_email.reset_mock()
    fake_shared.email_html.reset_mock()
    fake_shared.email_html.return_value = "<html>email</html>"
    fake_shared.write_audit_entry.reset_mock()

    # Load the module
    spec_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".github", "scripts", "tool2_tech_docs.py"
    )
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: synthetic file data
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_py_files():
    return {
        "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
        "backend/app.py": "import flask\napp = flask.Flask(__name__)\n",
        "frontend/index.ts": "export const foo = () => 'bar';",
    }


@pytest.fixture()
def sample_iac_files():
    return {
        "infra/main.tf": 'resource "aws_s3_bucket" "data" { bucket = "my-bucket" }',
        "infra/variables.yaml": "env: production\nregion: eu-west-1\n",
    }


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_happy_path_calls_get_repo_files_twice(self, tool2, fake_shared, sample_py_files, sample_iac_files):
        """get_repo_files should be called once for source files and once for IaC."""
        fake_shared.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert fake_shared.get_repo_files.call_count == 2

    def test_happy_path_calls_get_repo_files_with_correct_extensions(self, tool2, fake_shared):
        """Source-file call uses .py/.js/.ts/.go; IaC call uses .tf/.bicep/.json/.yaml/.yml."""
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        calls = fake_shared.get_repo_files.call_args_list
        src_exts = calls[0][0][2]  # positional arg[2] = extensions list
        iac_exts = calls[1][0][2]

        assert set(src_exts) == {".py", ".js", ".ts", ".go"}
        assert set(iac_exts) == {".tf", ".bicep", ".json", ".yaml", ".yml"}

    def test_happy_path_calls_get_repo_files_with_correct_max_files(self, tool2, fake_shared):
        """max_files=15 for source, max_files=10 for IaC."""
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        calls = fake_shared.get_repo_files.call_args_list
        assert calls[0][1]["max_files"] == 15
        assert calls[1][1]["max_files"] == 10

    def test_happy_path_returns_three_docs(self, tool2, fake_shared, sample_py_files, sample_iac_files):
        """generate_docs must return exactly README.md, ARCHITECTURE.md, RUNBOOK.md."""
        fake_shared.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        docs = tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_call_claude_called_three_times(self, tool2, fake_shared, sample_py_files, sample_iac_files):
        """call_claude should be invoked once per document."""
        fake_shared.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_correct_system_prompt(self, tool2, fake_shared):
        """README generation must use SYSTEM_README as the system prompt."""
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        readme_call = fake_shared.call_claude.call_args_list[0]
        system_prompt_used = readme_call[0][0]
        assert system_prompt_used == tool2.SYSTEM_README

    def test_architecture_uses_correct_system_prompt(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        arch_call = fake_shared.call_claude.call_args_list[1]
        assert arch_call[0][0] == tool2.SYSTEM_ARCH

    def test_runbook_uses_correct_system_prompt(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        runbook_call = fake_shared.call_claude.call_args_list[2]
        assert runbook_call[0][0] == tool2.SYSTEM_RUNBOOK

    def test_readme_user_prompt_contains_owner_and_repo(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("acme", "special-repo", "https://github.com/run/1")

        readme_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "acme" in readme_user_prompt
        assert "special-repo" in readme_user_prompt

    def test_architecture_user_prompt_contains_iac_content(self, tool2, fake_shared, sample_iac_files):
        """IaC file content should appear in the architecture doc prompt."""
        fake_shared.get_repo_files.side_effect = [{}, sample_iac_files]

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        arch_user_prompt = fake_shared.call_claude.call_args_list[1][0][1]
        # At least one IaC filename should appear
        assert any(fname in arch_user_prompt for fname in sample_iac_files)

    def test_docs_values_are_claude_return_values(self, tool2, fake_shared):
        """Each doc value should be exactly what call_claude returned."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        docs = tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert docs["README.md"] == "README content"
        assert docs["ARCHITECTURE.md"] == "ARCH content"
        assert docs["RUNBOOK.md"] == "RUNBOOK content"

    def test_empty_files_uses_no_files_found_placeholder(self, tool2, fake_shared):
        """When get_repo_files returns {}, the placeholder '_No files found_' is used."""
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        # Check all user prompts contain the placeholder
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_at_4000_chars(self, tool2, fake_shared):
        """File content longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.side_effect = [
            {"bigfile.py": long_content},
            {},
        ]

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The truncated version (4000 x's) must appear, but the full 10000 must not
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_call_claude_exception_propagates(self, tool2, fake_shared):
        """If call_claude raises, generate_docs should not swallow the exception."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

    def test_get_repo_files_exception_propagates(self, tool2, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

    def test_multiple_files_all_appear_in_prompt(self, tool2, fake_shared, sample_py_files, sample_iac_files):
        """All fetched filenames should appear in the README prompt."""
        fake_shared.get_repo_files.side_effect = [sample_py_files, sample_iac_files]

        tool2.generate_docs("acme", "my-repo", "https://github.com/run/1")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        for fname in sample_py_files:
            assert fname in readme_prompt
        for fname in sample_iac_files:
            assert fname in readme_prompt

    def test_owner_repo_forwarded_to_get_repo_files(self, tool2, fake_shared):
        """The owner and repo strings must be forwarded to get_repo_files."""
        fake_shared.get_repo_files.return_value = {}

        tool2.generate_docs("myorg", "coolrepo", "https://github.com/run/42")

        for c in fake_shared.get_repo_files.call_args_list:
            assert c[0][0] == "myorg"
            assert c[0][1] == "coolrepo"

    def test_special_characters_in_owner_repo(self, tool2, fake_shared):
        """Owner/repo with hyphens and underscores should not cause errors."""
        fake_shared.get_repo_files.return_value = {}

        docs = tool2.generate_