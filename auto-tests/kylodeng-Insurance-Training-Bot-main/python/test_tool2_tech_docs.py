"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK docs
- build_index(): produces a markdown index page with correct links and metadata
- __main__ block behaviour: env var reading, successful doc generation flow, error/failure flow

Mocks used:
- shared.call_claude — stubbed to return deterministic strings
- shared.get_repo_files — stubbed to return synthetic file dicts
- shared.write_output_file — stubbed to return a fake URL
- shared.send_email — stubbed (no-op)
- shared.email_html — stubbed to return a plain string
- shared.write_audit_entry — stubbed (no-op)
- shared.OUTPUT_REPO_OWNER / shared.OUTPUT_REPO — patched constants
- datetime.datetime.utcnow — patched for deterministic timestamps

TODOs:
- TODO: Integration test with a real (sandbox) Claude API key — skipped here
- TODO: Integration test with a real GitHub output repo — skipped here
- TODO: Test actual system prompt content correctness (needs product-owner sign-off)
"""

import sys
import os
import importlib
import runpy
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers & constants
# ---------------------------------------------------------------------------

FAKE_OWNER = "acme"
FAKE_REPO = "my-service"
FAKE_RUN_URL = "https://github.com/acme/my-service/actions/runs/999"
FAKE_README = "# README content"
FAKE_ARCH = "# ARCHITECTURE content"
FAKE_RUNBOOK = "# RUNBOOK content"
FAKE_OUTPUT_URL = "https://github.com/output-org/output-repo/blob/main/tech-docs/acme-my-service/README.md"
FAKE_INDEX_URL = "https://github.com/output-org/output-repo/blob/main/tech-docs/acme-my-service/INDEX.md"

FAKE_PY_FILES = {
    "src/main.py": "def main(): pass",
    "src/utils.py": "def helper(): return 42",
}
FAKE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "infra/variables.yaml": "var: value",
}
FAKE_EMPTY_FILES: dict = {}

NOW_STR = "2024-06-01 12:00 UTC"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_shared(monkeypatch):
    """
    Patch every symbol imported from `shared` inside tool2_tech_docs, as well
    as the module-level constants, before each test.
    """
    # We need tool2_tech_docs imported with patched shared symbols.
    # Easiest: patch the names as they appear in the tool module's namespace.
    import tool2_tech_docs as mod

    monkeypatch.setattr(mod, "call_claude", _make_call_claude())
    monkeypatch.setattr(mod, "get_repo_files", _make_get_repo_files())
    monkeypatch.setattr(mod, "write_output_file", MagicMock(return_value=FAKE_OUTPUT_URL))
    monkeypatch.setattr(mod, "send_email", MagicMock())
    monkeypatch.setattr(mod, "email_html", MagicMock(return_value="<html>body</html>"))
    monkeypatch.setattr(mod, "write_audit_entry", MagicMock())
    monkeypatch.setattr(mod, "OUTPUT_REPO_OWNER", "output-org")
    monkeypatch.setattr(mod, "OUTPUT_REPO", "output-repo")


def _make_call_claude():
    """Return a mock for call_claude that maps system prompts to doc strings."""

    def _call_claude(system_prompt: str, user_prompt: str) -> str:
        if "README" in system_prompt:
            return FAKE_README
        if "architect" in system_prompt.lower():
            return FAKE_ARCH
        if "runbook" in system_prompt.lower() or "DevOps" in system_prompt:
            return FAKE_RUNBOOK
        return "# Generic doc"

    return MagicMock(side_effect=_call_claude)


def _make_get_repo_files(py_files=None, iac_files=None):
    """Return a mock for get_repo_files with configurable return values."""
    py_files = py_files if py_files is not None else FAKE_PY_FILES
    iac_files = iac_files if iac_files is not None else FAKE_IAC_FILES

    call_count = {"n": 0}

    def _get_repo_files(owner, repo, extensions, max_files=10):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call → py/js/ts/go files
            return py_files
        else:
            # Second call → IaC files
            return iac_files

    return MagicMock(side_effect=_get_repo_files)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_returns_three_docs(self, monkeypatch):
        import tool2_tech_docs as mod

        docs = mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, monkeypatch):
        import tool2_tech_docs as mod

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        assert mod.get_repo_files.call_count == 2

    def test_first_get_repo_files_call_uses_code_extensions(self, monkeypatch):
        import tool2_tech_docs as mod

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        first_call_args = mod.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2] if first_call_args[0] else first_call_args[1]["extensions"]
        # Normalise — positional or keyword
        args, kwargs = first_call_args
        ext_arg = args[2] if len(args) > 2 else kwargs.get("extensions", args)
        assert ".py" in ext_arg
        assert ".ts" in ext_arg

    def test_second_get_repo_files_call_uses_iac_extensions(self, monkeypatch):
        import tool2_tech_docs as mod

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        second_call_args = mod.get_repo_files.call_args_list[1]
        args, kwargs = second_call_args
        ext_arg = args[2] if len(args) > 2 else kwargs.get("extensions", args)
        assert ".tf" in ext_arg
        assert ".yaml" in ext_arg

    def test_calls_call_claude_three_times(self, monkeypatch):
        import tool2_tech_docs as mod

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        assert mod.call_claude.call_count == 3

    def test_readme_content_comes_from_claude(self, monkeypatch):
        import tool2_tech_docs as mod

        docs = mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        assert docs["README.md"] == FAKE_README

    def test_architecture_content_comes_from_claude(self, monkeypatch):
        import tool2_tech_docs as mod

        docs = mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        assert docs["ARCHITECTURE.md"] == FAKE_ARCH

    def test_runbook_content_comes_from_claude(self, monkeypatch):
        import tool2_tech_docs as mod

        docs = mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        assert docs["RUNBOOK.md"] == FAKE_RUNBOOK

    def test_owner_and_repo_appear_in_claude_user_prompt(self, monkeypatch):
        import tool2_tech_docs as mod

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        for c in mod.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg
            assert FAKE_OWNER in user_prompt
            assert FAKE_REPO in user_prompt

    def test_empty_py_files_uses_no_files_found_placeholder(self, monkeypatch):
        """When get_repo_files returns empty, fmt() should return '_No files found_'."""
        import tool2_tech_docs as mod

        monkeypatch.setattr(mod, "get_repo_files", _make_get_repo_files(
            py_files={}, iac_files={}
        ))

        docs = mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        # Claude is still called; the user prompts contain the placeholder
        for c in mod.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, monkeypatch):
        """Files longer than 4000 chars must be truncated in the prompt."""
        import tool2_tech_docs as mod

        long_content = "x" * 8000
        big_py = {"src/big.py": long_content}

        monkeypatch.setattr(mod, "get_repo_files", _make_get_repo_files(
            py_files=big_py, iac_files={}
        ))

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        for c in mod.call_claude.call_args_list:
            user_prompt = c[0][1]
            # The truncated content should be 4000 x's, not 8000
            assert "x" * 4001 not in user_prompt

    def test_iac_files_appear_in_arch_prompt_not_readme_prompt(self, monkeypatch):
        """IaC file paths should appear in the architecture user prompt."""
        import tool2_tech_docs as mod

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        # find the architecture call — it uses SYSTEM_ARCH
        arch_call = None
        for c in mod.call_claude.call_args_list:
            system_prompt = c[0][0]
            if "architect" in system_prompt.lower():
                arch_call = c
                break

        assert arch_call is not None
        user_prompt = arch_call[0][1]
        assert "main.tf" in user_prompt

    def test_get_repo_files_receives_max_files_limits(self, monkeypatch):
        import tool2_tech_docs as mod

        mod.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

        call_list = mod.get_repo_files.call_args_list
        # First call max_files=15, second max_files=10
        _, kwargs1 = call_list[0]
        args1, _ = call_list[0]
        _, kwargs2 = call_list[1]
        args2, _ = call_list[1]

        max1 = kwargs1.get("max_files") if "max_files" in kwargs1 else (args1[3] if len(args1) > 3 else None)
        max2 = kwargs2.get("max_files") if "max_files" in kwargs2 else (args2[3] if len(args2) > 3 else None)

        assert max1 == 15
        assert max2 == 10


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def _docs(self):
        return {"README.md": FAKE_README, "ARCHITECTURE.md": FAKE_ARCH, "RUNBOOK.md": FAKE_RUNBOOK}

    def test_returns_string(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        assert isinstance(result, str)

    def test_contains_owner_and_repo_in_header(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        assert FAKE_OWNER in result
        assert FAKE_REPO in result

    def test_contains_generated_timestamp(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        assert NOW_STR in result

    def test_contains_links_for_all_docs(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        for name in self._docs():
            assert name in result

    def test_links_use_output_repo_owner_and_repo(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        assert "output-org" in result
        assert "output-repo" in result

    def test_links_contain_correct_path_segment(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        assert f"tech-docs/{FAKE_OWNER}-{FAKE_REPO}" in result

    def test_links_are_markdown_formatted(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        # Every doc name should appear as a markdown link label
        for name in self._docs():
            assert f"[{name}](" in result

    def test_contains_auto_generated_footer(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, self._docs(), NOW_STR)

        assert "Auto-generated" in result

    def test_empty_docs_dict_produces_no_links(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, {}, NOW_STR)

        assert "README.md" not in result
        assert "ARCHITECTURE.md" not in result

    def test_single_doc_produces_one_link(self):
        import tool2_tech_docs as mod

        result = mod.build_index(FAKE_OWNER, FAKE_REPO, {"README.md": FAKE_README}, NOW_STR)

        assert result.count("[README.md]") == 1
        assert "ARCHITECTURE.md" not in result

    @pytest.mark.parametrize("owner,repo", [
        ("org-with-dashes", "repo-with-dashes"),
        ("OrgUpperCase", "RepoUpperCase"),