"""
Test suite for .github/scripts/tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty files, partial files, Claude call behaviour
- build_index(): happy path, empty docs, special characters in owner/repo, timestamp format
- fmt() helper (indirectly via generate_docs)
- __main__ block: success path, exception/failure path

Mocks used:
- shared.call_claude          — prevents real Anthropic API calls
- shared.get_repo_files       — prevents real GitHub API calls
- shared.write_output_file    — prevents real GitHub write calls
- shared.send_email           — prevents real SES/SMTP calls
- shared.email_html           — prevents template rendering side-effects
- shared.write_audit_entry    — prevents real audit-log writes
- shared.OUTPUT_REPO_OWNER    — constant override
- shared.OUTPUT_REPO          — constant override
- datetime.datetime           — deterministic "now" timestamps
- sys.argv / os.environ       — environment variable injection

TODOs:
- TODO: integration test verifying the exact Markdown structure Claude is expected to return
- TODO: test verifying retry / back-off behaviour if call_claude raises a transient error
- TODO: test verifying behaviour when write_output_file returns None / empty URL
"""

import importlib
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so the import in tool2_tech_docs
# does not attempt to pull the real module (which has its own dependencies).
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated Content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fake `shared` module before every test and reload
    tool2_tech_docs so it picks up the fresh mock.
    """
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)

    # Remove cached tool2_tech_docs so each test gets a fresh import
    sys.modules.pop("tool2_tech_docs", None)

    # Also ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        monkeypatch.setattr(sys, "path", [script_dir] + sys.path)

    yield fake


@pytest.fixture()
def module(fake_shared):
    """Import (or reimport) the module under test."""
    import importlib.util, pathlib

    script_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool2_tech_docs.py"
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", script_path)
    mod = importlib.util.module_from_spec(spec)
    # Patch sys.path so `from shared import …` resolves to our fake
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# build_index tests
# ===========================================================================

class TestBuildIndex:
    """Tests for the build_index() function."""

    def test_happy_path_contains_owner_repo(self, module):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content2"}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 12:00 UTC")

        assert "acme/my-repo" in result
        assert "2024-01-15 12:00 UTC" in result

    def test_happy_path_contains_all_doc_links(self, module):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 12:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_constants(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("owner", "repo", docs, "2024-01-15 00:00 UTC")

        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_format_correct(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("owner", "repo", docs, "2024-01-15 00:00 UTC")

        expected_url = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/owner-repo/README.md"
        )
        assert expected_url in result

    def test_empty_docs_produces_no_links(self, module):
        result = module.build_index("owner", "repo", {}, "2024-01-15 00:00 UTC")

        assert "## Documents" in result
        # No markdown link bullets expected
        assert "- [" not in result

    def test_special_characters_in_owner_repo(self, module):
        """owner/repo with hyphens and numbers should not break the index."""
        docs = {"README.md": "x"}
        result = module.build_index("my-org-123", "cool_repo-v2", docs, "2024-01-15 00:00 UTC")

        assert "my-org-123/cool_repo-v2" in result

    def test_auto_generated_footer_present(self, module):
        docs = {"README.md": "x"}
        result = module.build_index("owner", "repo", docs, "now")

        assert "Auto-generated" in result

    def test_generated_timestamp_in_output(self, module):
        timestamp = "2099-12-31 23:59 UTC"
        result = module.build_index("owner", "repo", {"README.md": "x"}, timestamp)

        assert timestamp in result

    def test_multiple_docs_all_appear_as_list_items(self, module):
        docs = {f"DOC{i}.md": f"content{i}" for i in range(5)}
        result = module.build_index("o", "r", docs, "2024-01-01 00:00 UTC")

        for name in docs:
            assert f"[{name}]" in result


# ===========================================================================
# generate_docs tests
# ===========================================================================

class TestGenerateDocs:
    """Tests for the generate_docs() function."""

    def _setup_files(self, fake_shared, py_files=None, iac_files=None):
        """Configure get_repo_files to return given dicts on successive calls."""
        py_files = py_files or {}
        iac_files = iac_files or {}
        fake_shared.get_repo_files.side_effect = [py_files, iac_files]

    def test_happy_path_calls_call_claude_three_times(self, module, fake_shared):
        self._setup_files(
            fake_shared,
            py_files={"main.py": "print('hello')"},
            iac_files={"main.tf": 'resource "aws_s3_bucket" "b" {}'},
        )
        fake_shared.call_claude.return_value = "# Doc"

        docs = module.generate_docs("owner", "repo", "https://github.com/run/1")

        assert fake_shared.call_claude.call_count == 3

    def test_happy_path_returns_three_documents(self, module, fake_shared):
        self._setup_files(fake_shared, {"app.py": "x"}, {"main.tf": "y"})
        fake_shared.call_claude.return_value = "# Content"

        docs = module.generate_docs("owner", "repo", "https://run")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_get_repo_files_called_with_correct_extensions(self, module, fake_shared):
        self._setup_files(fake_shared)
        fake_shared.call_claude.return_value = "# x"

        module.generate_docs("owner", "repo", "https://run")

        calls = fake_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        first_call_exts = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_call_exts
        assert ".ts" in first_call_exts
        assert ".js" in first_call_exts
        assert ".go" in first_call_exts

        second_call_exts = calls[1][0][2]
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts
        assert ".yml" in second_call_exts

    def test_get_repo_files_called_with_owner_and_repo(self, module, fake_shared):
        self._setup_files(fake_shared)
        fake_shared.call_claude.return_value = "# x"

        module.generate_docs("my-owner", "my-repo", "https://run")

        for c in fake_shared.get_repo_files.call_args_list:
            assert c[0][0] == "my-owner"
            assert c[0][1] == "my-repo"

    def test_empty_files_produces_no_files_found_placeholder(self, module, fake_shared):
        """When no files are found, fmt() should return '_No files found_'."""
        self._setup_files(fake_shared, {}, {})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "https://run")

        # All three Claude calls should have been made with the placeholder text
        for c in fake_shared.call_claude.call_args_list:
            prompt = c[0][1]  # second positional arg is the user prompt
            assert "_No files found_" in prompt

    def test_file_content_truncated_at_4000_chars(self, module, fake_shared):
        """Files longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 8000
        self._setup_files(fake_shared, {"big_file.py": long_content}, {})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "https://run")

        readme_call = fake_shared.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        # The truncated content should appear (first 4000 chars), not full 8000
        assert "x" * 4000 in prompt
        assert "x" * 4001 not in prompt

    def test_readme_uses_system_readme_prompt(self, module, fake_shared):
        self._setup_files(fake_shared, {"a.py": "pass"}, {})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "https://run")

        readme_call = fake_shared.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_architecture_uses_system_arch_prompt(self, module, fake_shared):
        self._setup_files(fake_shared, {"a.py": "pass"}, {"main.tf": "x"})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "https://run")

        arch_call = fake_shared.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_runbook_uses_system_runbook_prompt(self, module, fake_shared):
        self._setup_files(fake_shared, {"a.py": "pass"}, {})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "https://run")

        runbook_call = fake_shared.call_claude.call_args_list[2]
        system_prompt = runbook_call[0][0]
        assert "runbook" in system_prompt.lower() or "devops" in system_prompt.lower()

    def test_owner_and_repo_appear_in_claude_prompt(self, module, fake_shared):
        self._setup_files(fake_shared, {"a.py": "pass"}, {})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("acme-corp", "backend-api", "https://run")

        for c in fake_shared.call_claude.call_args_list:
            prompt = c[0][1]
            assert "acme-corp/backend-api" in prompt

    def test_call_claude_exception_propagates(self, module, fake_shared):
        self._setup_files(fake_shared, {"a.py": "pass"}, {})
        fake_shared.call_claude.side_effect = RuntimeError("Claude unavailable")

        with pytest.raises(RuntimeError, match="Claude unavailable"):
            module.generate_docs("owner", "repo", "https://run")

    def test_multiple_py_files_all_appear_in_prompt(self, module, fake_shared):
        py_files = {
            "app.py": "print('app')",
            "models.py": "class Model: pass",
            "utils.ts": "export const x = 1;",
        }
        self._setup_files(fake_shared, py_files, {})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "https://run")

        readme_call = fake_shared.call_claude.call_args_list[0]
        prompt = readme_call[0][1]
        for filename in py_files:
            assert filename in prompt

    def test_iac_files_used_in_architecture_prompt(self, module, fake_shared):
        iac_files = {"main.tf": 'resource "aws_lambda" "fn" {}', "vars.yaml": "key: value"}
        self._setup_files(fake_shared, {}, iac_files)
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner", "repo", "https://run")

        arch_call = fake_shared.call_claude.call_args_list[1]
        prompt = arch_call[0][1]
        for filename in iac_files:
            assert filename in prompt

    def test_max_files_limits_passed_correctly(self, module, fake_shared):
        self._setup_files(fake_shared, {}, {})
        fake_shared.call_claude.return_value = "# Doc"

        module.generate_docs("owner",