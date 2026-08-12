"""
Test module for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates calls to get_repo_files and call_claude
    - build_index(): constructs a markdown index page from generated docs
    - __main__ block behaviour: happy path, exception/failure path
    - fmt() helper (indirectly via generate_docs)
    - Edge cases: empty file sets, missing env vars, Claude failures

Mocks used:
    - shared.call_claude          — stubbed to return deterministic markdown strings
    - shared.get_repo_files       — stubbed to return synthetic file dicts
    - shared.write_output_file    — stubbed to return fake GitHub URLs
    - shared.send_email           — stubbed (no SMTP)
    - shared.email_html           — stubbed to return a plain string
    - shared.write_audit_entry    — stubbed (no filesystem / DB writes)
    - shared.OUTPUT_REPO_OWNER    — patched to a known test value
    - shared.OUTPUT_REPO          — patched to a known test value
    - datetime.datetime.utcnow    — patched for deterministic timestamps
    - os.environ                  — patched via monkeypatch for __main__ tests

TODOs:
    - TODO: Integration test that verifies the exact Claude prompt text once
            prompt-engineering is locked down.
    - TODO: Test for rate-limiting / retry behaviour in call_claude (needs
            shared.call_claude retry logic to be exposed).
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake "shared" module so we never import the real one
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module with all symbols the SUT imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated doc")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fresh fake `shared` module before every test and reload the SUT
    so that module-level imports pick up the fake.
    """
    shared_mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)

    # Force re-import of the SUT so it binds to the freshly patched shared
    sut_name = "tool2_tech_docs"
    if sut_name in sys.modules:
        del sys.modules[sut_name]

    # Make the scripts directory importable
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also handle running tests from repo root where the file lives alongside
    alt_dir = os.path.join(os.path.dirname(__file__))
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    import tool2_tech_docs  # noqa: F401 — side-effect import
    return shared_mod


@pytest.fixture()
def sut():
    """Return the freshly-imported SUT module."""
    import tool2_tech_docs
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Synthetic file data reused across tests
# ---------------------------------------------------------------------------

SYNTHETIC_PY_FILES = {
    "backend/model_card.py": "class ModelCard:\n    pass\n",
    "backend/prompts/assessment_criterias.py": "CRITERIA = {}\n",
}

SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_lambda_function" "app" {}\n',
    "infra/vars.yaml": "region: us-east-1\n",
}


# ===========================================================================
# Tests for build_index()
# ===========================================================================


class TestBuildIndex:
    def test_contains_repo_header(self, sut):
        docs = {"README.md": "...", "ARCHITECTURE.md": "..."}
        result = sut.build_index("acme", "myrepo", docs, "2024-01-15 12:00 UTC")
        assert "acme/myrepo" in result

    def test_contains_generated_timestamp(self, sut):
        docs = {"README.md": "..."}
        result = sut.build_index("acme", "myrepo", docs, "2024-01-15 12:00 UTC")
        assert "2024-01-15 12:00 UTC" in result

    def test_links_use_output_repo_owner_and_repo(self, sut):
        docs = {"README.md": "..."}
        result = sut.build_index("acme", "myrepo", docs, "2024-01-15 12:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_all_doc_names_appear_as_links(self, sut):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "b"}
        result = sut.build_index("acme", "myrepo", docs, "2024-01-15 12:00 UTC")
        for name in docs:
            assert name in result

    def test_link_path_contains_owner_repo(self, sut):
        docs = {"README.md": "r"}
        result = sut.build_index("acme", "myrepo", docs, "2024-01-15 12:00 UTC")
        assert "tech-docs/acme-myrepo/README.md" in result

    def test_contains_auto_generated_footer(self, sut):
        docs = {"README.md": "r"}
        result = sut.build_index("acme", "myrepo", docs, "now")
        assert "Auto-generated" in result

    def test_empty_docs_dict(self, sut):
        """Edge case: no documents generated — index should still render."""
        result = sut.build_index("acme", "myrepo", {}, "2024-01-15 12:00 UTC")
        assert "acme/myrepo" in result
        # No document links, but no crash
        assert "README.md" not in result

    def test_special_characters_in_owner_repo(self, sut):
        """Repo names with hyphens / dots shouldn't break string formatting."""
        docs = {"README.md": "r"}
        result = sut.build_index("my-org", "my.repo", docs, "2024-01-15 12:00 UTC")
        assert "my-org/my.repo" in result

    def test_returns_string(self, sut):
        result = sut.build_index("o", "r", {"README.md": "x"}, "t")
        assert isinstance(result, str)


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================


class TestGenerateDocs:
    def test_calls_get_repo_files_twice(self, sut, fake_shared):
        """get_repo_files should be called once for source files, once for IaC."""
        fake_shared.get_repo_files.return_value = {}
        sut.generate_docs("acme", "myrepo", "https://run.url")
        assert fake_shared.get_repo_files.call_count == 2

    def test_get_repo_files_called_with_py_extensions(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut.generate_docs("acme", "myrepo", "https://run.url")
        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions

    def test_get_repo_files_called_with_iac_extensions(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut.generate_docs("acme", "myrepo", "https://run.url")
        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions or ".yaml" in extensions

    def test_returns_three_documents(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        docs = sut.generate_docs("acme", "myrepo", "https://run.url")
        assert len(docs) == 3

    def test_returns_readme_key(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        docs = sut.generate_docs("acme", "myrepo", "https://run.url")
        assert "README.md" in docs

    def test_returns_architecture_key(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        docs = sut.generate_docs("acme", "myrepo", "https://run.url")
        assert "ARCHITECTURE.md" in docs

    def test_returns_runbook_key(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        docs = sut.generate_docs("acme", "myrepo", "https://run.url")
        assert "RUNBOOK.md" in docs

    def test_call_claude_called_three_times(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        sut.generate_docs("acme", "myrepo", "https://run.url")
        assert fake_shared.call_claude.call_count == 3

    def test_doc_content_equals_claude_response(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# My README content"
        docs = sut.generate_docs("acme", "myrepo", "https://run.url")
        assert docs["README.md"] == "# My README content"

    def test_with_synthetic_py_files(self, sut, fake_shared):
        """Happy path: real-ish file dicts flow through without error."""
        def _side_effect(owner, repo, exts, max_files=15):
            if ".py" in exts:
                return SYNTHETIC_PY_FILES
            return SYNTHETIC_IAC_FILES

        fake_shared.get_repo_files.side_effect = _side_effect
        fake_shared.call_claude.return_value = "# Generated"
        docs = sut.generate_docs("acme", "myrepo", "https://run.url")
        assert len(docs) == 3

    def test_owner_and_repo_appear_in_claude_prompt(self, sut, fake_shared):
        """The owner/repo identifiers must be embedded in Claude prompts."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        sut.generate_docs("acme", "special-repo", "https://run.url")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg is user message
            assert "acme/special-repo" in user_prompt

    def test_claude_failure_propagates(self, sut, fake_shared):
        """If call_claude raises, generate_docs should propagate the exception."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")
        with pytest.raises(RuntimeError, match="Claude API error"):
            sut.generate_docs("acme", "myrepo", "https://run.url")

    def test_get_repo_files_failure_propagates(self, sut, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub down")
        with pytest.raises(ConnectionError):
            sut.generate_docs("acme", "myrepo", "https://run.url")

    def test_large_file_content_is_truncated_in_prompt(self, sut, fake_shared):
        """Files longer than 4000 chars should be truncated (per fmt function)."""
        large_content = "x" * 10_000
        fake_shared.get_repo_files.side_effect = [
            {"big_file.py": large_content},  # py/js files
            {},  # iac files
        ]
        fake_shared.call_claude.return_value = "# Doc"
        sut.generate_docs("acme", "myrepo", "https://run.url")
        # The user prompt passed to call_claude for README should not contain
        # the full 10 000 chars of content (capped at 4000)
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert large_content not in readme_prompt
        assert "x" * 4000 in readme_prompt

    def test_empty_files_render_no_files_found(self, sut, fake_shared):
        """When there are no files, the placeholder '_No files found_' is used."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        sut.generate_docs("acme", "myrepo", "https://run.url")
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_max_files_limit_passed_to_source_files(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        sut.generate_docs("acme", "myrepo", "https://run.url")
        first_call_kwargs = fake_shared.get_repo_files.call_args_list[0][1]
        assert first_call_kwargs.get("max_files") == 15

    def test_max_files_limit_passed_to_iac_files(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        sut.generate_docs("acme", "myrepo", "https://run.url")
        second_call_kwargs = fake_shared.get_repo_files.call_args_list[1][1]
        assert second_call_kwargs.get("max_files") == 10


# ===========================================================================
# Tests for the __main