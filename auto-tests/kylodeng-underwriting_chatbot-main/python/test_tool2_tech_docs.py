"""
Test suite for .github/scripts/tool2_tech_docs.py

What is tested:
- generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK docs
- build_index(): constructs a markdown index page with correct links, metadata, and formatting
- __main__ block behaviour: happy-path run writes files, sends email, writes audit entry;
  failure path writes audit entry, sends failure email, and re-raises

Mocks used:
- shared.call_claude          — stubbed to return deterministic doc strings
- shared.get_repo_files       — stubbed to return synthetic file dicts
- shared.write_output_file    — stubbed to return a fake GitHub URL string
- shared.send_email           — stubbed (no-op)
- shared.email_html           — stubbed to return a simple HTML string
- shared.write_audit_entry    — stubbed (no-op)
- shared.OUTPUT_REPO_OWNER    — patched to a known constant
- shared.OUTPUT_REPO          — patched to a known constant
- datetime.datetime.utcnow    — patched to a fixed instant for deterministic timestamps

TODOs:
- TODO: Integration test that exercises the real call_claude path (needs Anthropic credentials)
- TODO: Test behaviour when get_repo_files returns files larger than 4 000 characters (truncation)
- TODO: Test __main__ with missing SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while keeping `shared` fully mocked
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-delivery-org"
FAKE_OUTPUT_REPO = "ai-delivery-output"


def _make_shared_mock():
    """Return a mock module that satisfies every name imported from shared."""
    m = MagicMock()
    m.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    m.OUTPUT_REPO = FAKE_OUTPUT_REPO
    m.call_claude = MagicMock(side_effect=lambda system, user: f"CLAUDE_RESPONSE::{system[:20]}")
    m.get_repo_files = MagicMock(return_value={})
    m.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    m.send_email = MagicMock()
    m.email_html = MagicMock(return_value="<html>ok</html>")
    m.write_audit_entry = MagicMock()
    return m


def _import_module(shared_mock):
    """
    Import (or re-import) tool2_tech_docs with the supplied shared mock injected
    into sys.modules so that the `from shared import …` statement resolves to it.
    """
    # Ensure a clean re-import every time
    sys.modules.pop("tool2_tech_docs", None)
    sys.modules["shared"] = shared_mock

    # The script does sys.path.insert(0, os.path.dirname(__file__)) — we need
    # to point __file__ to the actual location of the script.
    script_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", ".github", "scripts"
    )
    script_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py")
    )

    # Fallback: load from source text directly so tests run from any working dir
    source_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".github", "scripts", "tool2_tech_docs.py",
    )
    if not os.path.exists(source_path):
        # Try relative to repo root
        source_path = os.path.join(".github", "scripts", "tool2_tech_docs.py")

    spec = importlib.util.spec_from_file_location("tool2_tech_docs", source_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def shared_mock():
    mock = _make_shared_mock()
    yield mock
    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def mod(shared_mock):
    return _import_module(shared_mock)


# ---------------------------------------------------------------------------
# Synthetic file data (derived from the sample data provided)
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/app.py": "def main():\n    pass\n",
    "backend/model.py": "import catboost\nclass RiskClassifier:\n    pass\n",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" { bucket = "my-bucket" }',
    "infra/variables.yml": "variables:\n  env: production\n",
}

SAMPLE_MODEL_CARD = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
}


# ===========================================================================
# Tests for build_index()
# ===========================================================================


class TestBuildIndex:
    def test_happy_path_contains_all_doc_links(self, mod):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = mod.build_index("myowner", "myrepo", docs, "2024-01-15 12:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_point_to_correct_output_repo(self, mod):
        docs = {"README.md": "content"}
        result = mod.build_index("acme", "widget", docs, "2024-01-15 12:00 UTC")

        expected_base = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/acme-widget/README.md"
        )
        assert expected_base in result

    def test_header_contains_owner_and_repo(self, mod):
        docs = {"README.md": "x"}
        result = mod.build_index("acme", "widget", docs, "2024-06-01 09:30 UTC")
        assert "acme/widget" in result

    def test_generated_timestamp_appears_in_output(self, mod):
        docs = {"README.md": "x"}
        now = "2099-12-31 23:59 UTC"
        result = mod.build_index("o", "r", docs, now)
        assert now in result

    def test_footer_present(self, mod):
        docs = {"README.md": "x"}
        result = mod.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_empty_docs_produces_valid_output(self, mod):
        result = mod.build_index("o", "r", {}, "2024-01-01 00:00 UTC")
        assert "# Tech Documentation Index" in result
        # No document links should appear
        assert "blob/main" not in result

    def test_multiple_docs_all_linked(self, mod):
        docs = {f"DOC{i}.md": f"content{i}" for i in range(5)}
        result = mod.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_path_uses_owner_repo_slug(self, mod):
        """The path segment should be owner-repo (hyphen separated)."""
        docs = {"README.md": "x"}
        result = mod.build_index("my-org", "my-repo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/my-org-my-repo/README.md" in result

    def test_return_type_is_string(self, mod):
        result = mod.build_index("o", "r", {"README.md": "x"}, "now")
        assert isinstance(result, str)

    def test_special_characters_in_owner_repo(self, mod):
        """Should not raise even with unusual owner/repo names."""
        docs = {"README.md": "x"}
        result = mod.build_index("owner_123", "repo.name", docs, "2024-01-01 00:00 UTC")
        assert "owner_123" in result
        assert "repo.name" in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================


class TestGenerateDocs:
    def test_returns_three_documents(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = SAMPLE_PY_FILES
        result = mod.generate_docs("myowner", "myrepo", "https://run-url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "https://x")
        assert shared_mock.call_claude.call_count == 3

    def test_get_repo_files_called_for_source_and_iac(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "https://x")
        assert shared_mock.get_repo_files.call_count == 2

        first_call_exts = shared_mock.get_repo_files.call_args_list[0][0][2]
        second_call_exts = shared_mock.get_repo_files.call_args_list[1][0][2]

        assert ".py" in first_call_exts
        assert ".tf" in second_call_exts or ".yaml" in second_call_exts

    def test_readme_prompt_includes_owner_repo(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("acme", "platform", "https://x")

        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "acme/platform" in user_prompt

    def test_arch_doc_prompt_includes_iac_section(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = SAMPLE_IAC_FILES
        mod.generate_docs("o", "r", "https://x")

        arch_call = shared_mock.call_claude.call_args_list[1]
        user_prompt = arch_call[0][1]
        assert "IaC files" in user_prompt

    def test_runbook_prompt_includes_files(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = SAMPLE_PY_FILES
        mod.generate_docs("o", "r", "https://x")

        runbook_call = shared_mock.call_claude.call_args_list[2]
        user_prompt = runbook_call[0][1]
        assert "Files" in user_prompt

    def test_values_come_from_call_claude(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.side_effect = ["readme_content", "arch_content", "runbook_content"]
        result = mod.generate_docs("o", "r", "https://x")

        assert result["README.md"] == "readme_content"
        assert result["ARCHITECTURE.md"] == "arch_content"
        assert result["RUNBOOK.md"] == "runbook_content"

    def test_empty_files_uses_no_files_found_placeholder(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "https://x")

        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        assert "_No files found_" in user_prompt

    def test_call_claude_propagates_exception(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        shared_mock.call_claude.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            mod.generate_docs("o", "r", "https://x")

    def test_get_repo_files_source_max_files_15(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "https://x")
        first_call_kwargs = shared_mock.get_repo_files.call_args_list[0]
        # max_files may be positional or keyword
        args, kwargs = first_call_kwargs
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 15

    def test_get_repo_files_iac_max_files_10(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "https://x")
        second_call = shared_mock.get_repo_files.call_args_list[1]
        args, kwargs = second_call
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 10

    def test_file_content_truncated_to_4000_chars(self, mod, shared_mock):
        """Files with content > 4000 chars should be truncated in the prompt."""
        long_content = "x" * 5000
        shared_mock.get_repo_files.return_value = {"big_file.py": long_content}
        mod.generate_docs("o", "r", "https://x")

        readme_call = shared_mock.call_claude.call_args_list[0]
        user_prompt = readme_call[0][1]
        # The truncated version (4000 x's) appears; the full 5000 should not
        assert "x" * 4000 in user_prompt
        assert "x" * 4001 not in user_prompt

    def test_system_readme_prompt_used_for_readme(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "https://x")
        readme_call = shared_mock.call_claude.call_args_list[0]
        system_prompt = readme_call[0][0]
        assert "README" in system_prompt or "technical writer" in system_prompt.lower()

    def test_system_arch_prompt_used_for_arch(self, mod, shared_mock):
        shared_mock.get_repo_files.return_value = {}
        mod.generate_docs("o", "r", "https://x")
        arch_call = shared_mock.call_claude.call_args_list[1]
        system_prompt = arch_call[0][0]
        assert "architect" in system_prompt.lower() or "architecture" in system_prompt.lower()

    def test_system_runbook_prompt_used_for_runbook