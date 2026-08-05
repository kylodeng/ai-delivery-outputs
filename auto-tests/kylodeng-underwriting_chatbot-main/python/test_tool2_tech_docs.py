"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): happy path, empty docs dict, multiple docs, special characters in owner/repo
- fmt() inner function behaviour (via generate_docs integration)
- __main__ block: happy path, exception/failure path

Mocks used:
- shared.call_claude — prevents real API calls to Claude/Anthropic
- shared.get_repo_files — prevents real GitHub API calls
- shared.write_output_file — prevents real file writes to output repo
- shared.send_email — prevents real email dispatch
- shared.email_html — prevents HTML construction side-effects
- shared.write_audit_entry — prevents real audit log writes
- shared.OUTPUT_REPO_OWNER — patched to a known test value
- shared.OUTPUT_REPO — patched to a known test value
- datetime.datetime.utcnow — deterministic timestamp in __main__ tests
- os.environ — controlled via monkeypatch

TODOs:
- TODO: Integration test verifying exact Claude prompt content once prompt schema is stable
- TODO: Test that file truncation at 4000 chars is applied correctly (needs large synthetic file fixture)
- TODO: Test behaviour when get_repo_files returns files whose content contains backticks
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers — build a fake `shared` module so the import at module level works
# ---------------------------------------------------------------------------

def _make_fake_shared():
    """Return a mock module that satisfies tool2_tech_docs's top-level imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/output/repo/blob/main/file.md")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = "test-output-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    return mod


@pytest.fixture(autouse=True)
def fake_shared_module():
    """Inject fake shared module before each test and clean up afterwards."""
    fake = _make_fake_shared()
    sys.modules["shared"] = fake
    # Force re-import so module-level `from shared import …` bindings are fresh
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]
    yield fake
    # Teardown
    for key in list(sys.modules.keys()):
        if key in ("shared", "tool2_tech_docs"):
            del sys.modules[key]


@pytest.fixture()
def module(fake_shared_module):
    """Import (or re-import) tool2_tech_docs with the fake shared module active."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import tool2_tech_docs as m
    return m


# ---------------------------------------------------------------------------
# Fixtures — synthetic file data
# ---------------------------------------------------------------------------

SYNTHETIC_PY_FILES = {
    "backend/model_card.py": "class ModelCard:\n    pass\n",
    "backend/prompts/assessment_criterias.py": "CRITERIA = {}\n",
}

SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }\n',
    "infra/variables.tf": 'variable "region" { default = "us-east-1" }\n',
}


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_happy_path_calls_claude_three_times(self, module, fake_shared_module):
        """generate_docs should call call_claude once per document."""
        fake_shared_module.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,   # first call → py/js/ts/go files
            SYNTHETIC_IAC_FILES,  # second call → IaC files
        ]
        fake_shared_module.call_claude.return_value = "# Doc content"

        docs = module.generate_docs("acme", "backend", "https://github.com/run/1")

        assert fake_shared_module.call_claude.call_count == 3

    def test_happy_path_returns_three_keys(self, module, fake_shared_module):
        """generate_docs should return README.md, ARCHITECTURE.md, RUNBOOK.md."""
        fake_shared_module.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,
            SYNTHETIC_IAC_FILES,
        ]
        fake_shared_module.call_claude.return_value = "# Content"

        docs = module.generate_docs("acme", "backend", "https://github.com/run/1")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_doc_contents_match_claude_return(self, module, fake_shared_module):
        """Each document should contain exactly what call_claude returned."""
        fake_shared_module.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,
            SYNTHETIC_IAC_FILES,
        ]
        fake_shared_module.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        docs = module.generate_docs("acme", "backend", "https://github.com/run/1")

        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_get_repo_files_called_with_correct_extensions(self, module, fake_shared_module):
        """get_repo_files must be called with the correct extension lists."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("myorg", "myrepo", "http://run")

        calls = fake_shared_module.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call: source files
        first_args = calls[0][0]
        assert first_args[0] == "myorg"
        assert first_args[1] == "myrepo"
        assert set(first_args[2]) == {".py", ".js", ".ts", ".go"}

        # Second call: IaC files
        second_args = calls[1][0]
        assert set(second_args[2]) == {".tf", ".bicep", ".json", ".yaml", ".yml"}

    def test_get_repo_files_max_files_limits(self, module, fake_shared_module):
        """max_files limits should be 15 for source and 10 for IaC."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("myorg", "myrepo", "http://run")

        calls = fake_shared_module.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15 or calls[0][0][3] == 15
        assert calls[1][1].get("max_files") == 10 or calls[1][0][3] == 10

    def test_empty_file_sets_still_returns_three_docs(self, module, fake_shared_module):
        """When no files are found, generate_docs should still produce all three docs."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.return_value = "# Empty project doc"

        docs = module.generate_docs("empty-org", "empty-repo", "http://run")

        assert len(docs) == 3

    def test_empty_files_fmt_produces_no_files_found(self, module, fake_shared_module):
        """With empty dicts, the prompt sent to Claude should contain '_No files found_'."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("org", "repo", "http://run")

        # Inspect each call's user message argument
        for c in fake_shared_module.call_claude.call_args_list:
            user_msg = c[0][1]  # second positional arg is the user prompt
            assert "_No files found_" in user_msg

    def test_call_claude_receives_owner_repo_in_prompt(self, module, fake_shared_module):
        """Owner and repo name must appear in the prompts sent to Claude."""
        fake_shared_module.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,
            SYNTHETIC_IAC_FILES,
        ]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("acme-corp", "my-service", "http://run")

        for c in fake_shared_module.call_claude.call_args_list:
            user_msg = c[0][1]
            assert "acme-corp" in user_msg
            assert "my-service" in user_msg

    def test_file_content_appears_in_readme_prompt(self, module, fake_shared_module):
        """Source file content should appear in the README generation prompt."""
        fake_shared_module.get_repo_files.side_effect = [
            {"myfile.py": "def hello(): pass"},
            {},
        ]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("org", "repo", "http://run")

        readme_call = fake_shared_module.call_claude.call_args_list[0]
        user_msg = readme_call[0][1]
        assert "def hello(): pass" in user_msg

    def test_call_claude_propagates_exception(self, module, fake_shared_module):
        """If call_claude raises, generate_docs should propagate the exception."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            module.generate_docs("org", "repo", "http://run")

    def test_get_repo_files_propagates_exception(self, module, fake_shared_module):
        """If get_repo_files raises, generate_docs should propagate the exception."""
        fake_shared_module.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            module.generate_docs("org", "repo", "http://run")

    def test_file_content_truncated_in_fmt(self, module, fake_shared_module):
        """File content exceeding 4000 chars should be truncated to 4000 chars in prompts."""
        long_content = "x" * 8000
        fake_shared_module.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("org", "repo", "http://run")

        readme_call = fake_shared_module.call_claude.call_args_list[0]
        user_msg = readme_call[0][1]
        # The truncated slice should appear; the full 8000 chars should not
        assert "x" * 4000 in user_msg
        assert "x" * 4001 not in user_msg

    def test_system_prompt_used_for_readme(self, module, fake_shared_module):
        """The README system prompt should be passed as the first arg to call_claude."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("org", "repo", "http://run")

        readme_system = fake_shared_module.call_claude.call_args_list[0][0][0]
        assert "README" in readme_system or "technical writer" in readme_system.lower()

    def test_system_prompt_used_for_arch(self, module, fake_shared_module):
        """The architecture system prompt should be passed for the second Claude call."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("org", "repo", "http://run")

        arch_system = fake_shared_module.call_claude.call_args_list[1][0][0]
        assert "architect" in arch_system.lower() or "architecture" in arch_system.lower()

    def test_system_prompt_used_for_runbook(self, module, fake_shared_module):
        """The runbook system prompt should be passed for the third Claude call."""
        fake_shared_module.get_repo_files.side_effect = [{}, {}]
        fake_shared_module.call_claude.return_value = "content"

        module.generate_docs("org", "repo", "http://run")

        runbook_system = fake_shared_module.call_claude.call_args_list[2][0][0]
        assert "runbook" in runbook_system.lower() or "devops" in runbook_system.lower()


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_happy_path_contains_owner_and_repo(self, module):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content", "RUNBOOK.md": "content"}
        result = module.build_index("acme", "backend", docs, "2024-01-15 10:00 UTC")
        assert "acme" in result
        assert "backend" in result

    def test_happy_path_contains_timestamp(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("org", "repo", docs, "2024-06-01 12:00 UTC")
        assert "2024-06-01 12:00 UTC" in result

    def test_happy_path_links_all_docs(self, module):
        docs = {"README.md": "c", "ARCHITECTURE.md": "c", "RUNBOOK.md": "c"}
        result = module.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_point_to_output_repo(self, module, fake_shared_module):
        """Links should reference OUTPUT_REPO_OWNER and OUTPUT_REPO from shared."""
        docs = {"README.md": "c"}
        result = module.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert fake_shared_module.OUTPUT_REPO_OWNER in result
        assert fake_shared_module.OUTPUT_REPO in result

    def test_