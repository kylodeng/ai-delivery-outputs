"""
Test module for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates fetching repo files and calling Claude for README, ARCHITECTURE, RUNBOOK
    - build_index(): builds a markdown index page with links to generated docs
    - __main__ block behaviour: success path and failure/exception path

Mocks used:
    - shared.call_claude          — mocked to return deterministic strings
    - shared.get_repo_files       — mocked to return synthetic file dictionaries
    - shared.write_output_file    — mocked to return a fake GitHub URL
    - shared.send_email           — mocked (no real SMTP calls)
    - shared.email_html           — mocked to return a dummy HTML string
    - shared.write_audit_entry    — mocked (no real I/O)
    - shared.OUTPUT_REPO_OWNER    — patched as module-level constant
    - shared.OUTPUT_REPO          — patched as module-level constant
    - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
    - TODO: Integration test against a real GitHub repo once credentials are available
    - TODO: Test behaviour when Claude returns malformed/empty strings (needs Claude contract spec)
    - TODO: Test write_output_file retry/backoff behaviour (depends on shared.py implementation)
"""

import importlib
import sys
import os
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake 'shared' module before tool2_tech_docs is imported
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake 'shared' module so we never import the real one."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>mock</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject fake shared module before every test and reload tool2_tech_docs."""
    shared_mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)
    # Remove cached tool2 module so it re-imports with our fake shared
    sys.modules.pop("tool2_tech_docs", None)
    yield shared_mod
    sys.modules.pop("tool2_tech_docs", None)


def _import_tool2():
    """Import (or re-import) the module under test."""
    import importlib
    import tool2_tech_docs as m
    return m


# ---------------------------------------------------------------------------
# Synthetic file fixtures
# ---------------------------------------------------------------------------

SYNTHETIC_PY_FILES = {
    "backend/model_card.py": "# model card placeholder\nclass ModelCard: pass",
    "backend/prompts/assessment.py": "SYSTEM_PROMPT = 'You are an agent'",
}

SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }',
    "infra/variables.yaml": "region: us-east-1\nenv: prod",
}

EMPTY_FILES: dict = {}


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    """Tests for the generate_docs() function."""

    def test_returns_three_docs_on_happy_path(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            SYNTHETIC_PY_FILES,
            SYNTHETIC_IAC_FILES,
        ]
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCHITECTURE content",
            "# RUNBOOK content",
        ]
        m = _import_tool2()
        result = m.generate_docs("my-org", "my-repo", "https://github.com/actions/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_comes_from_first_call_claude(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.side_effect = ["README_BODY", "ARCH_BODY", "RUNBOOK_BODY"]
        m = _import_tool2()
        result = m.generate_docs("owner", "repo", "http://run-url")
        assert result["README.md"] == "README_BODY"

    def test_architecture_content_comes_from_second_call_claude(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.side_effect = ["README_BODY", "ARCH_BODY", "RUNBOOK_BODY"]
        m = _import_tool2()
        result = m.generate_docs("owner", "repo", "http://run-url")
        assert result["ARCHITECTURE.md"] == "ARCH_BODY"

    def test_runbook_content_comes_from_third_call_claude(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.side_effect = ["README_BODY", "ARCH_BODY", "RUNBOOK_BODY"]
        m = _import_tool2()
        result = m.generate_docs("owner", "repo", "http://run-url")
        assert result["RUNBOOK.md"] == "RUNBOOK_BODY"

    def test_get_repo_files_called_twice_with_correct_extensions(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [EMPTY_FILES, EMPTY_FILES]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        m.generate_docs("owner", "repo", "http://run-url")

        calls = fake_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        first_call_extensions = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_call_extensions
        assert ".js" in first_call_extensions
        assert ".ts" in first_call_extensions
        assert ".go" in first_call_extensions

        second_call_extensions = calls[1][0][2]
        assert ".tf" in second_call_extensions
        assert ".yaml" in second_call_extensions
        assert ".yml" in second_call_extensions

    def test_get_repo_files_max_files_limits(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [EMPTY_FILES, EMPTY_FILES]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        m.generate_docs("owner", "repo", "http://run-url")

        calls = fake_shared.get_repo_files.call_args_list
        assert calls[0][1]["max_files"] == 15
        assert calls[1][1]["max_files"] == 10

    def test_call_claude_called_three_times(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        m.generate_docs("owner", "repo", "http://run-url")
        assert fake_shared.call_claude.call_count == 3

    def test_call_claude_receives_owner_repo_in_prompt(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        m.generate_docs("acme-org", "special-repo", "http://run-url")

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg
            assert "acme-org/special-repo" in user_prompt

    def test_empty_files_produces_no_files_found_placeholder(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [EMPTY_FILES, EMPTY_FILES]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        # Should not raise; the fmt helper returns "_No files found_"
        result = m.generate_docs("owner", "repo", "http://run-url")
        assert "README.md" in result

        # Verify Claude was called with the no-files placeholder
        all_prompts = " ".join(str(c) for c in fake_shared.call_claude.call_args_list)
        assert "_No files found_" in all_prompts

    def test_file_content_truncated_to_4000_chars(self, fake_shared):
        long_content = "x" * 10_000
        py_files = {"big_file.py": long_content}
        fake_shared.get_repo_files.side_effect = [py_files, EMPTY_FILES]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        m.generate_docs("owner", "repo", "http://run-url")

        # The prompt passed to the README call should not contain all 10000 'x' chars
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "x" * 10_000 not in readme_prompt
        # But it should contain (up to) 4000 chars
        assert "x" * 4000 in readme_prompt

    def test_call_claude_raises_propagates(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        fake_shared.call_claude.side_effect = RuntimeError("Claude timeout")
        m = _import_tool2()
        with pytest.raises(RuntimeError, match="Claude timeout"):
            m.generate_docs("owner", "repo", "http://run-url")

    def test_get_repo_files_raises_propagates(self, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        m = _import_tool2()
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            m.generate_docs("owner", "repo", "http://run-url")

    def test_multiple_py_and_iac_files_all_included_in_prompts(self, fake_shared):
        py_files = {
            "app.py": "print('hello')",
            "utils.ts": "export const x = 1;",
        }
        iac_files = {
            "main.tf": 'resource "aws_lambda_function" "fn" {}',
            "vars.yml": "region: eu-west-1",
        }
        fake_shared.get_repo_files.side_effect = [py_files, iac_files]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        m.generate_docs("owner", "repo", "http://run-url")

        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "app.py" in readme_prompt
        assert "utils.ts" in readme_prompt
        assert "main.tf" in readme_prompt
        assert "vars.yml" in readme_prompt

    @pytest.mark.parametrize("owner,repo", [
        ("simple-owner", "simple-repo"),
        ("owner-with-dashes", "repo.with.dots"),
        ("UPPERCASE_OWNER", "UPPERCASE_REPO"),
    ])
    def test_generate_docs_various_owner_repo_names(self, owner, repo, fake_shared):
        fake_shared.get_repo_files.side_effect = [EMPTY_FILES, EMPTY_FILES]
        fake_shared.call_claude.return_value = "content"
        m = _import_tool2()
        result = m.generate_docs(owner, repo, "http://run-url")
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    """Tests for the build_index() function."""

    def _call(self, owner="my-org", repo="my-repo", docs=None, now="2024-01-15 12:00 UTC"):
        if docs is None:
            docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        m = _import_tool2()
        return m.build_index(owner, repo, docs, now)

    def test_contains_owner_repo_in_heading(self, fake_shared):
        result = self._call(owner="acme", repo="platform")
        assert "acme/platform" in result

    def test_contains_generated_timestamp(self, fake_shared):
        result = self._call(now="2024-06-01 09:30 UTC")
        assert "2024-06-01 09:30 UTC" in result

    def test_contains_links_for_all_docs(self, fake_shared):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "b"}
        result = self._call(docs=docs)
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_point_to_output_repo(self, fake_shared):
        # OUTPUT_REPO_OWNER and OUTPUT_REPO are imported constants in the module
        result = self._call(owner="src-owner", repo="src-repo")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_correct_path_format(self, fake_shared):
        result = self._call(owner="org", repo="proj")
        assert "tech-docs/org-proj/README.md" in result

    def test_empty_docs_produces_empty_links_section(self, fake_shared):
        result = self._call(docs={})
        # No document links, but heading and timestamp still present
        assert "Tech Documentation Index" in result
        assert "2024-01-15 12:00 UTC" in result

    def test_single_doc_produces_single_link(self, fake_shared):
        result = self._call(docs={"CUSTOM.md": "content"})
        assert "CUSTOM.md" in result
        # Should contain exactly one list item link
        link_lines = [line for line in result.splitlines() if line.strip().startswith("- [")]
        assert len(link_lines) == 1

    def test_auto_generated_footer_present(self, fake_shared):
        result = self._call()
        assert "Auto-generated by AI Delivery Bot" in result

    def test_returns_string(self, fake_shared):
        result = self._call()
        assert isinstance(