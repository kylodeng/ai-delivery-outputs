"""
Test suite for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
    - build_index(): produces a correctly formatted markdown index page
    - __main__ block behaviour: success path (writes docs, sends email, audits) and failure path (audits, emails, re-raises)

Mocks used:
    - shared.call_claude            — prevents real Anthropic API calls
    - shared.get_repo_files         — prevents real GitHub API calls
    - shared.write_output_file      — prevents real GitHub file writes
    - shared.send_email             — prevents real email sending
    - shared.email_html             — prevents real HTML template rendering
    - shared.write_audit_entry      — prevents real audit log writes
    - shared.OUTPUT_REPO_OWNER      — patched to deterministic test value
    - shared.OUTPUT_REPO            — patched to deterministic test value
    - datetime.datetime             — patched for deterministic timestamps in some tests

TODOs:
    - TODO: Integration test that verifies round-trip from real GitHub repo → real Claude → real output repo
            (requires live credentials; skipped below)
    - TODO: Verify exact Claude prompt strings if prompt regression testing is required
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake 'shared' module so we can import tool2_tech_docs
# without the real shared.py being present or making network calls.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a MagicMock that masquerades as the shared module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/some-file")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>mock</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fresh fake shared module before every test and reload the SUT."""
    shared_mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)

    # Force a fresh import of the SUT so module-level constants pick up the fake
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        monkeypatch.syspath_prepend(script_dir)

    yield shared_mod


@pytest.fixture()
def sut(fake_shared):
    """Return the freshly imported SUT module."""
    import importlib
    import tool2_tech_docs as m
    return m


# ---------------------------------------------------------------------------
# Fixtures – synthetic repo data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance agent"}}',
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" {}',
    "infra/variables.yml": "env: production\n",
}


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, sut, fake_shared):
        """generate_docs returns a dict with README.md, ARCHITECTURE.md, RUNBOOK.md."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "# Doc content"

        result = sut.generate_docs("my-org", "my-repo", "https://github.com/actions/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, sut, fake_shared):
        """generate_docs must call call_claude exactly three times (one per doc)."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "content"

        sut.generate_docs("my-org", "my-repo", "https://github.com/run/1")

        assert fake_shared.call_claude.call_count == 3

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared):
        """generate_docs fetches source files and IaC files with correct extension lists."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.return_value = "content"

        sut.generate_docs("owner", "repo", "url")

        calls = fake_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        first_call_exts = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_call_exts
        assert ".ts" in first_call_exts

        second_call_exts = calls[1][0][2]
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts

    def test_readme_doc_uses_readme_system_prompt(self, sut, fake_shared):
        """The first call_claude invocation uses the SYSTEM_README prompt."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        captured_system_prompts = []
        fake_shared.call_claude.side_effect = lambda sys_p, usr_p: (
            captured_system_prompts.append(sys_p) or "content"
        )

        sut.generate_docs("owner", "repo", "url")

        assert "technical writer" in captured_system_prompts[0].lower()

    def test_architecture_doc_uses_arch_system_prompt(self, sut, fake_shared):
        """The second call_claude invocation uses the SYSTEM_ARCH prompt."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        captured = []
        fake_shared.call_claude.side_effect = lambda s, u: (captured.append(s) or "content")

        sut.generate_docs("owner", "repo", "url")

        assert "architect" in captured[1].lower()

    def test_runbook_doc_uses_runbook_system_prompt(self, sut, fake_shared):
        """The third call_claude invocation uses the SYSTEM_RUNBOOK prompt."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        captured = []
        fake_shared.call_claude.side_effect = lambda s, u: (captured.append(s) or "content")

        sut.generate_docs("owner", "repo", "url")

        assert "devops" in captured[2].lower() or "runbook" in captured[2].lower()

    def test_each_doc_contains_distinct_content(self, sut, fake_shared):
        """Each generated doc stores whatever call_claude returned for that call."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]

        result = sut.generate_docs("owner", "repo", "url")

        assert result["README.md"] == "README content"
        assert result["ARCHITECTURE.md"] == "ARCH content"
        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_no_source_files_uses_placeholder(self, sut, fake_shared):
        """When get_repo_files returns empty dicts, the prompt includes '_No files found_'."""
        fake_shared.get_repo_files.side_effect = [{}, {}]
        captured_user_prompts = []
        fake_shared.call_claude.side_effect = lambda s, u: (
            captured_user_prompts.append(u) or "content"
        )

        sut.generate_docs("owner", "repo", "url")

        assert any("_No files found_" in p for p in captured_user_prompts)

    def test_file_content_truncated_to_4000_chars(self, sut, fake_shared):
        """File contents longer than 4000 chars are truncated in the formatted string."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        captured_user_prompts = []
        fake_shared.call_claude.side_effect = lambda s, u: (
            captured_user_prompts.append(u) or "content"
        )

        sut.generate_docs("owner", "repo", "url")

        # The truncated content is 4000 x's — confirm none of the prompts contains 10000 x's
        for prompt in captured_user_prompts:
            assert "x" * 10_000 not in prompt

    def test_owner_and_repo_appear_in_user_prompt(self, sut, fake_shared):
        """Owner and repo name must be included in the user prompt sent to Claude."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        captured = []
        fake_shared.call_claude.side_effect = lambda s, u: (captured.append(u) or "content")

        sut.generate_docs("acme-corp", "widget-service", "url")

        assert all("acme-corp/widget-service" in p for p in captured)

    def test_get_repo_files_max_files_respected(self, sut, fake_shared):
        """generate_docs passes max_files limits to get_repo_files."""
        fake_shared.get_repo_files.side_effect = [{}, {}]
        fake_shared.call_claude.return_value = "content"

        sut.generate_docs("owner", "repo", "url")

        calls = fake_shared.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15 or calls[0][0][-1] == 15
        assert calls[1][1].get("max_files") == 10 or calls[1][0][-1] == 10

    def test_call_claude_raises_propagates(self, sut, fake_shared):
        """If call_claude raises, generate_docs propagates the exception."""
        fake_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        fake_shared.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            sut.generate_docs("owner", "repo", "url")

    def test_get_repo_files_raises_propagates(self, sut, fake_shared):
        """If get_repo_files raises, generate_docs propagates the exception."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            sut.generate_docs("owner", "repo", "url")


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:

    def test_returns_string(self, sut):
        docs = {"README.md": "content", "ARCHITECTURE.md": "content"}
        result = sut.build_index("owner", "repo", docs, "2024-01-15 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo_in_title(self, sut):
        result = sut.build_index("acme", "widget", {"README.md": ""}, "2024-01-01 00:00 UTC")
        assert "acme/widget" in result

    def test_contains_generated_timestamp(self, sut):
        result = sut.build_index("owner", "repo", {"README.md": ""}, "2024-06-30 09:45 UTC")
        assert "2024-06-30 09:45 UTC" in result

    def test_contains_links_for_all_docs(self, sut):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "ru"}
        result = sut.build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, sut):
        """Links must point to OUTPUT_REPO_OWNER/OUTPUT_REPO, not the source repo."""
        docs = {"README.md": "content"}
        result = sut.build_index("src-owner", "src-repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_correct_path_structure(self, sut):
        docs = {"README.md": "content"}
        result = sut.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/myorg-myrepo/README.md" in result

    def test_empty_docs_produces_no_links_section(self, sut):
        result = sut.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")
        # Should still return a string without crashing
        assert "Tech Documentation Index" in result

    def test_auto_generated_footer(self, sut):
        result = sut.build_index("owner", "repo", {}, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_single_doc(self, sut):
        result = sut.build_index("org", "proj", {"RUNBOOK.md": "content"}, "2024-03-10 08:00 UTC")
        assert "RUNBOOK.md" in result
        assert "README.md" not in result

    def test_special_characters_in_owner_repo(self, sut):
        """Owner/repo names with hyphens and underscores should not break output."""
        docs = {"README.md": "content"}
        result = sut.build_index("my-org_v2", "cool-repo_x", docs, "2024-01-01 00:00 UTC")
        assert "my-org_v2/cool-repo_x" in result


# ---------------------------------------------------------------------------
# Tests for the __main__ block (success path)
# ---------------------------------------------------------------------------


class TestMain