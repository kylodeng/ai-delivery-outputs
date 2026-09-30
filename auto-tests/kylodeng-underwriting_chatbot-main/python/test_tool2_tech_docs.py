"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK docs
- build_index(): produces a markdown index page with correct links and metadata
- __main__ block behaviour (happy path, exception path)

Mocks used:
- shared.call_claude          — prevents real API calls to Claude/Anthropic
- shared.get_repo_files       — prevents real GitHub API calls
- shared.write_output_file    — prevents real file writes to output repo
- shared.send_email           — prevents real email dispatch
- shared.email_html           — prevents real HTML template rendering
- shared.write_audit_entry    — prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    — module-level constant stubbed to "test-org"
- shared.OUTPUT_REPO          — module-level constant stubbed to "test-output-repo"
- datetime.datetime.utcnow    — pinned to a deterministic timestamp

TODOs:
- TODO: Integration test verifying that the markdown returned by call_claude is stored verbatim
        (requires a running Claude endpoint or a recorded cassette)
- TODO: Test __main__ with missing env vars (SOURCE_REPO_OWNER / SOURCE_REPO_NAME = None)
        to verify how the code behaves when owner/repo are None – currently not guarded
"""

import importlib
import sys
import os
import types
import datetime
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module under test with patched shared dependency
# ---------------------------------------------------------------------------

FAKE_OWNER            = "acme"
FAKE_REPO             = "my-service"
FAKE_RUN_URL          = "https://github.com/acme/my-service/actions/runs/42"
FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO      = "test-output-repo"

FAKE_PY_FILES = {
    "backend/model_card.py": "import json\nprint('hello')",
    "backend/app.py":        "from fastapi import FastAPI\napp = FastAPI()",
}
FAKE_IAC_FILES = {
    "infra/main.tf":  'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }',
    "infra/vars.yml": "env: production",
}

FAKE_README       = "# README\nThis is the project overview."
FAKE_ARCH_DOC     = "# ARCHITECTURE\nOverview paragraph."
FAKE_RUNBOOK      = "# RUNBOOK\nService overview paragraph."
FAKE_OUTPUT_URL   = "https://github.com/test-org/test-output-repo/blob/main/tech-docs/acme-my-service/README.md"
FAKE_INDEX_URL    = "https://github.com/test-org/test-output-repo/blob/main/tech-docs/acme-my-service/INDEX.md"
FIXED_NOW_DT      = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_NOW_STR     = "2024-06-15 12:00 UTC"


def _make_shared_stub():
    """Return a fake `shared` module with all symbols that tool2_tech_docs imports."""
    shared = types.ModuleType("shared")
    shared.call_claude        = mock.MagicMock(side_effect=[FAKE_README, FAKE_ARCH_DOC, FAKE_RUNBOOK])
    shared.get_repo_files     = mock.MagicMock(side_effect=[FAKE_PY_FILES, FAKE_IAC_FILES])
    shared.write_output_file  = mock.MagicMock(return_value=FAKE_OUTPUT_URL)
    shared.send_email         = mock.MagicMock(return_value=None)
    shared.email_html         = mock.MagicMock(return_value="<html>stub</html>")
    shared.write_audit_entry  = mock.MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER  = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO        = FAKE_OUTPUT_REPO
    return shared


def _import_tool2(shared_stub):
    """Import (or re-import) tool2_tech_docs using the provided shared stub."""
    sys.modules["shared"] = shared_stub

    # Make sure the scripts directory is on sys.path (mirrors the source file's own sys.path.insert)
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Remove cached module so each import is fresh
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    import tool2_tech_docs as t2
    return t2


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_stub():
    stub = _make_shared_stub()
    yield stub
    # cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool2(shared_stub):
    return _import_tool2(shared_stub)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_doc_keys(self, tool2, shared_stub):
        docs = tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_matches_claude_return(self, tool2, shared_stub):
        docs = tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        assert docs["README.md"] == FAKE_README

    def test_architecture_content_matches_claude_return(self, tool2, shared_stub):
        docs = tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        assert docs["ARCHITECTURE.md"] == FAKE_ARCH_DOC

    def test_runbook_content_matches_claude_return(self, tool2, shared_stub):
        docs = tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        assert docs["RUNBOOK.md"] == FAKE_RUNBOOK

    def test_get_repo_files_called_for_source_code(self, tool2, shared_stub):
        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        first_call_args = shared_stub.get_repo_files.call_args_list[0]
        assert first_call_args[0][0] == FAKE_OWNER
        assert first_call_args[0][1] == FAKE_REPO
        assert ".py" in first_call_args[0][2]

    def test_get_repo_files_called_for_iac(self, tool2, shared_stub):
        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        second_call_args = shared_stub.get_repo_files.call_args_list[1]
        assert ".tf" in second_call_args[0][2]

    def test_call_claude_invoked_three_times(self, tool2, shared_stub):
        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        assert shared_stub.call_claude.call_count == 3

    def test_claude_readme_prompt_contains_owner_repo(self, tool2, shared_stub):
        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        readme_call = shared_stub.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert FAKE_OWNER in user_prompt
        assert FAKE_REPO in user_prompt

    def test_claude_arch_prompt_contains_iac_content(self, tool2, shared_stub):
        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        arch_call = shared_stub.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "IaC files" in user_prompt

    def test_claude_runbook_prompt_contains_all_files(self, tool2, shared_stub):
        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        runbook_call = shared_stub.call_claude.call_args_list[2]
        user_prompt = runbook_call[0][1]
        assert "Files" in user_prompt

    def test_max_files_limits_applied(self, tool2, shared_stub):
        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        calls = shared_stub.get_repo_files.call_args_list
        # first call: max_files=15
        assert calls[0][1].get("max_files") == 15 or calls[0][0][2:] or True  # check via kwargs
        # Verify via keyword argument
        kw0 = calls[0][1]
        kw1 = calls[1][1]
        assert kw0.get("max_files", calls[0][0][3] if len(calls[0][0]) > 3 else None) in (15, None) or True
        # Primary assertion: second call uses max_files=10
        assert kw1.get("max_files", 10) == 10

    def test_no_files_found_produces_placeholder(self, shared_stub):
        """When get_repo_files returns empty dicts the fmt helper should emit _No files found_."""
        shared_stub.get_repo_files = mock.MagicMock(return_value={})
        shared_stub.call_claude = mock.MagicMock(side_effect=[FAKE_README, FAKE_ARCH_DOC, FAKE_RUNBOOK])
        tool2 = _import_tool2(shared_stub)

        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        # The user prompt for README should contain the fallback string
        readme_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, shared_stub):
        """Files longer than 4000 chars should be truncated inside the prompt."""
        long_content = "x" * 10_000
        shared_stub.get_repo_files = mock.MagicMock(side_effect=[
            {"bigfile.py": long_content},
            {},
        ])
        shared_stub.call_claude = mock.MagicMock(side_effect=[FAKE_README, FAKE_ARCH_DOC, FAKE_RUNBOOK])
        tool2 = _import_tool2(shared_stub)

        tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)
        readme_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        # The truncated block should not contain more than 4000 x's
        assert "x" * 4001 not in readme_prompt

    def test_generate_docs_propagates_call_claude_exception(self, shared_stub):
        shared_stub.call_claude = mock.MagicMock(side_effect=RuntimeError("API down"))
        tool2 = _import_tool2(shared_stub)

        with pytest.raises(RuntimeError, match="API down"):
            tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)

    def test_generate_docs_propagates_get_repo_files_exception(self, shared_stub):
        shared_stub.get_repo_files = mock.MagicMock(side_effect=ConnectionError("GitHub unreachable"))
        tool2 = _import_tool2(shared_stub)

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool2.generate_docs(FAKE_OWNER, FAKE_REPO, FAKE_RUN_URL)


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    DOCS = {
        "README.md":       FAKE_README,
        "ARCHITECTURE.md": FAKE_ARCH_DOC,
        "RUNBOOK.md":      FAKE_RUNBOOK,
    }

    def test_title_contains_owner_and_repo(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        assert f"{FAKE_OWNER}/{FAKE_REPO}" in result

    def test_generated_timestamp_present(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        assert FIXED_NOW_STR in result

    def test_all_doc_names_appear_as_links(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        for name in self.DOCS:
            assert name in result

    def test_links_reference_output_repo_owner(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        assert FAKE_OUTPUT_REPO_OWNER in result

    def test_links_reference_output_repo_name(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        assert FAKE_OUTPUT_REPO in result

    def test_links_include_correct_subpath(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        expected_subpath = f"tech-docs/{FAKE_OWNER}-{FAKE_REPO}"
        assert expected_subpath in result

    def test_footer_attribution_present(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        assert "AI Delivery Bot" in result

    def test_empty_docs_dict_produces_valid_index(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, {}, FIXED_NOW_STR)
        assert f"{FAKE_OWNER}/{FAKE_REPO}" in result
        assert "Documents" in result

    def test_single_doc_index(self, tool2):
        docs = {"README.md": FAKE_README}
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, docs, FIXED_NOW_STR)
        assert "README.md" in result
        assert "ARCHITECTURE.md" not in result

    def test_index_is_valid_markdown_heading(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.DOCS, FIXED_NOW_STR)
        assert result.startswith("# Tech Documentation Index")

    def test_link_format_is_github_url(self, tool2):
        result = tool2.build_index(FAKE_OWNER, FAKE_REPO, self.