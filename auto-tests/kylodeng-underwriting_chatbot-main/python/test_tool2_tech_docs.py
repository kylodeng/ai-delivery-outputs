"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls for README, ARCHITECTURE, RUNBOOK
- build_index(): builds a markdown index page with correct links and metadata
- __main__ block behaviour: success path (write files, send email, audit) and failure path

Mocks used:
- shared.call_claude          → avoids real Anthropic/Claude API calls
- shared.get_repo_files       → avoids real GitHub API calls
- shared.write_output_file    → avoids real file writes to output repo
- shared.send_email           → avoids real SMTP/SES calls
- shared.email_html           → pure string helper, mocked for isolation
- shared.write_audit_entry    → avoids real audit writes
- shared.OUTPUT_REPO_OWNER    → patched as a known constant
- shared.OUTPUT_REPO          → patched as a known constant
- datetime.datetime           → pinned for deterministic timestamp assertions
- os.environ                  → patched for __main__ env vars

TODOs:
- TODO: Integration test that wires real shared.py helpers against a test GitHub repo
- TODO: Test behaviour when get_repo_files returns files exceeding 4000-char truncation limit
"""

import importlib
import sys
import os
import types
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers to build a fake 'shared' module so we never import the real one
# (which would require credentials / network access at import time).
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake 'shared' module."""
    mod = types.ModuleType("shared")
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    mod.call_claude = MagicMock(return_value="# Generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/file.md")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject the fake shared module before every test and reload the SUT."""
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)
    # Remove the SUT from sys.modules so each test gets a fresh import
    sys.modules.pop("tool2_tech_docs", None)
    yield mod


def _import_sut():
    """Import (or re-import) the module under test."""
    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    return importlib.import_module("tool2_tech_docs")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/app.py": "def main(): pass",
    "backend/utils.py": "import os\n\ndef helper(): return 42",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "infra/variables.tf": 'variable "region" {}',
}

SAMPLE_DOCS = {
    "README.md": "# README content",
    "ARCHITECTURE.md": "# Architecture content",
    "RUNBOOK.md": "# Runbook content",
}


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_returns_string(self):
        sut = _import_sut()
        result = sut.build_index("myorg", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_repo_name_in_title(self):
        sut = _import_sut()
        result = sut.build_index("myorg", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "myorg/myrepo" in result

    def test_contains_generated_timestamp(self):
        sut = _import_sut()
        now = "2024-06-01 12:34 UTC"
        result = sut.build_index("myorg", "myrepo", SAMPLE_DOCS, now)
        assert now in result

    def test_contains_all_doc_names(self):
        sut = _import_sut()
        result = sut.build_index("myorg", "myrepo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        for name in SAMPLE_DOCS:
            assert name in result

    def test_links_point_to_correct_output_repo(self):
        sut = _import_sut()
        result = sut.build_index("myorg", "myrepo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        expected_base = f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}/blob/main/tech-docs/myorg-myrepo/"
        assert expected_base in result

    def test_links_use_owner_repo_slug(self):
        sut = _import_sut()
        result = sut.build_index("acme", "backend", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "acme-backend" in result

    def test_contains_auto_generated_footer(self):
        sut = _import_sut()
        result = sut.build_index("myorg", "myrepo", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "Auto-generated" in result

    def test_empty_docs_dict(self):
        sut = _import_sut()
        result = sut.build_index("myorg", "myrepo", {}, "2024-01-01 00:00 UTC")
        assert "myorg/myrepo" in result
        assert isinstance(result, str)

    def test_single_doc(self):
        sut = _import_sut()
        docs = {"README.md": "content"}
        result = sut.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result

    def test_special_characters_in_owner_repo(self):
        sut = _import_sut()
        result = sut.build_index("my-org", "my-repo.v2", SAMPLE_DOCS, "2024-01-01 00:00 UTC")
        assert "my-org/my-repo.v2" in result

    def test_each_doc_produces_separate_link(self):
        sut = _import_sut()
        docs = {"A.md": "a", "B.md": "b", "C.md": "c"}
        result = sut.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        assert result.count("blob/main") == 3


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:
    def test_returns_dict_with_three_keys(self, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        sut = _import_sut()
        result = sut.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        first_call_system = fake_shared.call_claude.call_args_list[0][0][0]
        assert "technical writer" in first_call_system.lower()

    def test_arch_uses_system_arch_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        second_call_system = fake_shared.call_claude.call_args_list[1][0][0]
        assert "architect" in second_call_system.lower()

    def test_runbook_uses_system_runbook_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        third_call_system = fake_shared.call_claude.call_args_list[2][0][0]
        assert "devops" in third_call_system.lower() or "runbook" in third_call_system.lower()

    def test_get_repo_files_called_for_source_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        calls = fake_shared.get_repo_files.call_args_list
        # First call should include Python/JS extensions
        first_extensions = calls[0][0][2]
        assert ".py" in first_extensions

    def test_get_repo_files_called_for_iac_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        calls = fake_shared.get_repo_files.call_args_list
        second_extensions = calls[1][0][2]
        assert ".tf" in second_extensions

    def test_owner_and_repo_included_in_user_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("acme", "backend", "https://run")
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "acme/backend" in user_prompt

    def test_returns_claude_output_as_values(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]
        sut = _import_sut()
        result = sut.generate_docs("myorg", "myrepo", "https://run")
        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_with_populated_py_files(self, fake_shared):
        def side_effect(owner, repo, extensions, max_files=15):
            if ".py" in extensions:
                return SAMPLE_PY_FILES
            return SAMPLE_IAC_FILES
        fake_shared.get_repo_files.side_effect = side_effect
        sut = _import_sut()
        result = sut.generate_docs("myorg", "myrepo", "https://run")
        assert "README.md" in result

    def test_file_content_truncated_at_4000_chars(self, fake_shared):
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        # The user prompt passed to call_claude must not contain the full 5000 chars
        # for that file — it should be truncated to 4000
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        # 4000 x's should be present but not 5000
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_no_files_uses_no_files_found_placeholder(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        readme_prompt = fake_shared.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_call_claude_raises_propagates(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("API error")
        sut = _import_sut()
        with pytest.raises(RuntimeError, match="API error"):
            sut.generate_docs("myorg", "myrepo", "https://run")

    def test_get_repo_files_called_twice(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        assert fake_shared.get_repo_files.call_count == 2

    def test_max_files_limit_for_source(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        first_call_kwargs = fake_shared.get_repo_files.call_args_list[0]
        # max_files should be 15 for source files
        max_files_value = first_call_kwargs[1].get("max_files") or first_call_kwargs[0][3]
        assert max_files_value == 15

    def test_max_files_limit_for_iac(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        sut = _import_sut()
        sut.generate_docs("myorg", "myrepo", "https://run")
        second_call_kwargs = fake_shared.get_repo_files.call_args_list[1]
        max_files_value = second_call_kwargs[1].get("max_files") or second_call_kwargs[0][3]
        assert max_files_value == 10


# ---------------------------------------------------------------------------
# Tests for fmt() helper (indirectly via generate_docs)
# ---------------------------------------------------------------------------

class TestFmtHelper: