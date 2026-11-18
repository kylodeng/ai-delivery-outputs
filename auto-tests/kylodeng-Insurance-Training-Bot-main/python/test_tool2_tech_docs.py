"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown output, link construction, timestamp embedding
- fmt() (inner function behaviour via generate_docs)
- __main__ block: success path, exception/failure path, env-var handling

Mocks used:
- shared.call_claude          → unittest.mock.patch
- shared.get_repo_files       → unittest.mock.patch
- shared.write_output_file    → unittest.mock.patch
- shared.send_email           → unittest.mock.patch
- shared.email_html           → unittest.mock.patch
- shared.write_audit_entry    → unittest.mock.patch
- shared.OUTPUT_REPO_OWNER    → patched as module attribute
- shared.OUTPUT_REPO          → patched as module attribute
- datetime.datetime.utcnow    → unittest.mock.patch

TODOs:
- TODO: Integration test against a real GitHub repo (requires GITHUB_TOKEN + live repo)
- TODO: Test actual Claude response parsing if call_claude ever returns structured data
- TODO: Verify email HTML body content in more detail once email_html signature is stable
"""

import importlib
import os
import sys
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake `shared` module so we never import the real
# one (which may require credentials / network).
# ---------------------------------------------------------------------------

def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="MOCK_DOC_CONTENT")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/mock/url")
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>mock</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "mock-owner"
    shared.OUTPUT_REPO = "mock-output-repo"
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fresh fake shared module before every test."""
    shared = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared)
    # Remove cached tool2 module so it re-imports with the fake shared
    sys.modules.pop("tool2_tech_docs", None)
    yield shared


@pytest.fixture()
def tool2(fake_shared):
    """Import (or re-import) the module under test."""
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Also try the local directory in case tests run from .github/scripts
    local_dir = os.path.dirname(__file__)
    if local_dir not in sys.path:
        sys.path.insert(0, local_dir)

    import tool2_tech_docs
    return tool2_tech_docs


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:
    def test_happy_path_contains_all_doc_names(self, tool2, fake_shared):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = tool2.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_happy_path_header(self, tool2, fake_shared):
        docs = {"README.md": "content"}
        result = tool2.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")

        assert "# Tech Documentation Index" in result
        assert "acme/my-repo" in result

    def test_generated_timestamp_embedded(self, tool2, fake_shared):
        docs = {"README.md": "content"}
        now = "2024-06-30 23:59 UTC"
        result = tool2.build_index("owner", "repo", docs, now)

        assert now in result

    def test_link_format_uses_output_repo_constants(self, tool2, fake_shared):
        fake_shared.OUTPUT_REPO_OWNER = "docs-owner"
        fake_shared.OUTPUT_REPO = "docs-repo"
        # Re-import so the module picks up new constant values
        sys.modules.pop("tool2_tech_docs", None)
        import tool2_tech_docs as t2

        docs = {"README.md": "content"}
        result = t2.build_index("src-owner", "src-repo", docs, "2024-01-01 00:00 UTC")

        assert "docs-owner" in result
        assert "docs-repo" in result
        assert "tech-docs/src-owner-src-repo/README.md" in result

    def test_link_format_includes_full_github_url(self, tool2, fake_shared):
        docs = {"RUNBOOK.md": "rb"}
        result = tool2.build_index("o", "r", docs, "2024-01-01 00:00 UTC")

        assert "https://github.com/" in result

    def test_empty_docs_dict(self, tool2, fake_shared):
        result = tool2.build_index("o", "r", {}, "2024-01-01 00:00 UTC")

        # Should still return a valid string header
        assert "# Tech Documentation Index" in result
        assert "o/r" in result

    def test_auto_generated_footer(self, tool2, fake_shared):
        docs = {"README.md": "x"}
        result = tool2.build_index("o", "r", docs, "2024-01-01 00:00 UTC")

        assert "Auto-generated" in result

    def test_multiple_docs_each_have_own_link(self, tool2, fake_shared):
        docs = {
            "README.md": "r",
            "ARCHITECTURE.md": "a",
            "RUNBOOK.md": "rb",
            "EXTRA.md": "e",
        }
        result = tool2.build_index("o", "r", docs, "2024-01-01 00:00 UTC")

        for name in docs:
            assert name in result

    def test_owner_repo_separator_in_path(self, tool2, fake_shared):
        """Path component must be owner-repo (hyphen), not owner/repo."""
        docs = {"README.md": "content"}
        result = tool2.build_index("my-org", "my-service", docs, "2024-01-01 00:00 UTC")

        assert "my-org-my-service" in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:
    def _setup_get_repo_files(self, fake_shared, py_files=None, iac_files=None):
        py_files = py_files or {}
        iac_files = iac_files or {}

        def side_effect(owner, repo, extensions, max_files=15):
            py_exts = {".py", ".js", ".ts", ".go"}
            iac_exts = {".tf", ".bicep", ".json", ".yaml", ".yml"}
            if set(extensions) == py_exts:
                return py_files
            if set(extensions) == iac_exts:
                return iac_files
            return {}

        fake_shared.get_repo_files.side_effect = side_effect

    def test_returns_three_docs(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "CONTENT"

        result = tool2.generate_docs("owner", "repo", "https://run.url")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "CONTENT"

        tool2.generate_docs("owner", "repo", "https://run.url")

        assert fake_shared.get_repo_files.call_count == 2

    def test_calls_call_claude_three_times(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "CONTENT"

        tool2.generate_docs("owner", "repo", "https://run.url")

        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "DOC"

        tool2.generate_docs("owner", "repo", "https://run.url")

        first_call_system = fake_shared.call_claude.call_args_list[0][0][0]
        assert "technical writer" in first_call_system.lower() or "README" in first_call_system

    def test_readme_doc_contains_call_claude_result(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "UNIQUE_README_RESULT"

        result = tool2.generate_docs("owner", "repo", "https://run.url")

        assert result["README.md"] == "UNIQUE_README_RESULT"

    def test_each_doc_contains_distinct_call_claude_result(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.side_effect = ["README_CONTENT", "ARCH_CONTENT", "RUNBOOK_CONTENT"]

        result = tool2.generate_docs("owner", "repo", "https://run.url")

        assert result["README.md"] == "README_CONTENT"
        assert result["ARCHITECTURE.md"] == "ARCH_CONTENT"
        assert result["RUNBOOK.md"] == "RUNBOOK_CONTENT"

    def test_owner_repo_in_prompt(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "DOC"

        tool2.generate_docs("my-org", "my-service", "https://run.url")

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "my-org/my-service" in user_prompt

    def test_with_py_files(self, tool2, fake_shared):
        py_files = {"main.py": "print('hello')", "utils.ts": "export const x = 1;"}
        self._setup_get_repo_files(fake_shared, py_files=py_files)
        fake_shared.call_claude.return_value = "DOC"

        result = tool2.generate_docs("owner", "repo", "https://run.url")

        # call_claude should be called; check that file content appears in a prompt
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "main.py" in readme_prompt

    def test_with_iac_files(self, tool2, fake_shared):
        iac_files = {"main.tf": 'resource "aws_s3_bucket" "b" {}'}
        self._setup_get_repo_files(fake_shared, iac_files=iac_files)
        fake_shared.call_claude.return_value = "DOC"

        tool2.generate_docs("owner", "repo", "https://run.url")

        arch_prompt = fake_shared.call_claude.call_args_list[1][0][1]
        assert "main.tf" in arch_prompt

    def test_no_files_produces_no_files_found_placeholder(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared, py_files={}, iac_files={})
        fake_shared.call_claude.return_value = "DOC"

        tool2.generate_docs("owner", "repo", "https://run.url")

        # At least one prompt should contain the placeholder
        all_prompts = [c[0][1] for c in fake_shared.call_claude.call_args_list]
        assert any("_No files found_" in p for p in all_prompts)

    def test_file_content_truncated_at_4000_chars(self, tool2, fake_shared):
        long_content = "x" * 10_000
        py_files = {"big_file.py": long_content}
        self._setup_get_repo_files(fake_shared, py_files=py_files)
        fake_shared.call_claude.return_value = "DOC"

        tool2.generate_docs("owner", "repo", "https://run.url")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The embedded content block should not exceed 4000 x's
        assert long_content not in readme_prompt  # full 10k string absent
        assert "x" * 4000 in readme_prompt        # exactly 4000 chars present

    def test_call_claude_propagates_exception(self, tool2, fake_shared):
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            tool2.generate_docs("owner", "repo", "https://run.url")

    def test_get_repo_files_propagates_exception(self, tool2, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("network error")

        with pytest.raises(ConnectionError, match="network error"):
            tool2.generate_docs("owner", "repo", "https://run.url")

    def test_py_files_max_files_limit(self, tool2, fake_shared):
        """get_repo_files for source files must pass max_files=15."""
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "DOC"

        tool2.generate_docs("owner", "repo", "https://run.url")

        source_call = fake_shared.get_repo_files.call_args_list[0]
        assert source_call[1].get("max_files") == 15 or source_call[0][3] == 15

    def test_iac_files_max_files_limit(self, tool2, fake_shared):
        """get_repo_files for IaC files must pass max_files=10."""
        self._setup_get_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "DOC"

        tool2.generate_docs("owner", "repo", "https://run.url")

        iac_call = fake_shared.get_repo_files.call_args_list[1]
        assert iac_call[1].get("max_files") == 10 or iac_call[0][3] == 10


# ===========================================================================
# Tests for the fmt() inner function behaviour (tested via generate_docs)
# ===========================================================================

class TestFmtInnerFunction:
    def test_fmt_produces_fenced_code_block(self, tool2, fake_shared):