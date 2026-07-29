"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls for README, ARCHITECTURE, RUNBOOK
- build_index(): constructs a markdown index page with correct links and metadata
- __main__ block logic: env var handling, output writing, email/audit calls, error paths

Mocks used:
- shared.call_claude          — stubbed to return deterministic strings
- shared.get_repo_files       — stubbed to return synthetic file dicts
- shared.write_output_file    — stubbed to return fake GitHub URLs
- shared.send_email           — stubbed (side-effect free)
- shared.email_html           — stubbed to return an HTML string
- shared.write_audit_entry    — stubbed (side-effect free)
- shared.OUTPUT_REPO_OWNER    — patched to a fixed value
- shared.OUTPUT_REPO          — patched to a fixed value
- datetime.datetime.utcnow    — patched to a fixed timestamp

TODOs:
- TODO: Integration test against a real GitHub repo (requires GITHUB_TOKEN)
- TODO: Test actual Claude API response parsing (requires Anthropic credentials)
- TODO: Test write_output_file conflict/retry behaviour (depends on shared.py internals)
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake 'shared' module so we can import the SUT
# without the real shared.py being present or making network calls.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a fake `shared` module injected into sys.modules."""
    mod = types.ModuleType("shared")
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    mod.call_claude = MagicMock(return_value="# Generated doc")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fresh fake shared module before every test and reload SUT."""
    fs = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fs)
    # Remove the SUT from the module cache so each test gets a clean import
    sys.modules.pop("tool2_tech_docs", None)
    yield fs


@pytest.fixture()
def sut(fake_shared):
    """Return freshly imported SUT module."""
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import tool2_tech_docs
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Synthetic file data reused across tests
# ---------------------------------------------------------------------------

SYNTHETIC_PY_FILES = {
    "backend/model_card.py": "import json\n# TODO: add versioning\nprint('hello')",
    "backend/prompts/assessment.py": "PROMPT = 'You are a finance agent'",
}

SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" { bucket = "my-docs" }',
    "infra/variables.yml": "env: production\nregion: eu-west-1",
}

SYNTHETIC_ALL_FILES = {**SYNTHETIC_PY_FILES, **SYNTHETIC_IAC_FILES}


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================


class TestGenerateDocs:
    """Tests for the generate_docs() public function."""

    def test_happy_path_returns_three_docs(self, sut, fake_shared):
        """generate_docs returns exactly README.md, ARCHITECTURE.md, RUNBOOK.md."""
        fake_shared.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,
            SYNTHETIC_IAC_FILES,
        ]
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCHITECTURE content",
            "# RUNBOOK content",
        ]

        docs = sut.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_doc_content_matches_claude_responses(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.side_effect = ["readme", "arch", "runbook"]

        docs = sut.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert docs["README.md"] == "readme"
        assert docs["ARCHITECTURE.md"] == "arch"
        assert docs["RUNBOOK.md"] == "runbook"

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        calls = fake_shared.get_repo_files.call_args_list
        assert len(calls) == 2
        # First call: source files
        first_exts = calls[0][0][2]
        assert ".py" in first_exts
        assert ".ts" in first_exts
        assert ".go" in first_exts
        # Second call: IaC files
        second_exts = calls[1][0][2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts

    def test_get_repo_files_max_files_respected(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        calls = fake_shared.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15 or calls[0][0][-1] == 15
        assert calls[1][1].get("max_files") == 10 or calls[1][0][-1] == 10

    def test_call_claude_called_three_times(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        assert fake_shared.call_claude.call_count == 3

    def test_readme_prompt_contains_owner_repo(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("myowner", "myrepo", "url")

        first_user_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "myowner/myrepo" in first_user_prompt

    def test_arch_prompt_contains_iac_content(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            {},
            {"infra/main.tf": "resource aws_s3_bucket {}"},
        ]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        arch_user_prompt = fake_shared.call_claude.call_args_list[1][0][1]
        assert "infra/main.tf" in arch_user_prompt

    def test_no_files_found_uses_placeholder(self, sut, fake_shared):
        """When no files are found, fmt() returns '_No files found_'."""
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, sut, fake_shared):
        """Files longer than 4000 chars are truncated in the prompt."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.side_effect = [{"big_file.py": long_content}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # The prompt should contain exactly 4000 x's (truncated), not 10000
        assert "x" * 4001 not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_system_prompt_used_for_readme(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        readme_system_prompt = fake_shared.call_claude.call_args_list[0][0][0]
        assert "README.md" in readme_system_prompt or "technical writer" in readme_system_prompt

    def test_system_prompt_used_for_arch(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        arch_system_prompt = fake_shared.call_claude.call_args_list[1][0][0]
        assert "architect" in arch_system_prompt.lower() or "architecture" in arch_system_prompt.lower()

    def test_system_prompt_used_for_runbook(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        runbook_system_prompt = fake_shared.call_claude.call_args_list[2][0][0]
        assert "runbook" in runbook_system_prompt.lower() or "devops" in runbook_system_prompt.lower()

    def test_call_claude_raises_propagates(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            sut.generate_docs("owner", "repo", "url")

    def test_get_repo_files_raises_propagates(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            sut.generate_docs("owner", "repo", "url")

    def test_multiple_files_all_appear_in_prompt(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("owner", "repo", "url")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        for filename in SYNTHETIC_ALL_FILES:
            assert filename in readme_prompt

    def test_owner_repo_forwarded_to_get_repo_files(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "doc"

        sut.generate_docs("special-owner", "special-repo", "url")

        for c in fake_shared.get_repo_files.call_args_list:
            assert c[0][0] == "special-owner"
            assert c[0][1] == "special-repo"


# ===========================================================================
# Tests for build_index()
# ===========================================================================


class TestBuildIndex:
    """Tests for the build_index() public function."""

    def _call(self, sut, owner="acme", repo="my-repo", docs=None, now="2024-01-15 12:00 UTC"):
        if docs is None:
            docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        return sut.build_index(owner, repo, docs, now)

    def test_returns_string(self, sut):
        result = self._call(sut)
        assert isinstance(result, str)

    def test_contains_owner_repo_in_title(self, sut):
        result = self._call(sut, owner="acme", repo="my-repo")
        assert "acme/my-repo" in result

    def test_contains_generated_timestamp(self, sut):
        result = self._call(sut, now="2024-01-15 12:00 UTC")
        assert "2024-01-15 12:00 UTC" in result

    def test_contains_links_for_all_docs(self, sut):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = self._call(sut, docs=docs)
        for name in docs:
            assert name in result

    def test_links_use_output_repo_owner_and_repo(self, sut):
        result = self._call(sut)
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_link_format_correct(self, sut):
        docs = {"README.md": "content"}
        result = self._call(sut, owner="org", repo="proj", docs=docs)
        expected_url = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/org-proj/README.md"
        )
        assert expected_url in result

    def test_contains_auto_generated_footer(self, sut):
        result = self._call(sut)
        assert "Auto-generated" in result or "AI Delivery Bot" in result

    def test_empty_docs_produces_no_links(self, sut):
        result = self._call(sut, docs={})
        assert "README.md" not in result
        assert "ARCHITECTURE.md" not in result

    def test_single_doc_produces_one_link(self, sut):
        docs = {"CUSTOM.md": "custom content"}
        result = self._call(sut, docs=docs)
        assert "CUSTOM.md" in result

    def test_special_characters_in_owner_repo(self, sut):
        """Owner/repo with h