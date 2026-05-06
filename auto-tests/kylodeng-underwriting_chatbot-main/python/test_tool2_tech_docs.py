"""
Test module for tool2_tech_docs.py

What is tested:
    - generate_docs(): happy path, empty file sets, partial file sets
    - build_index(): correct markdown output, link formatting, timestamp inclusion
    - __main__ block logic via subprocess / monkeypatching env vars
    - fmt() inner function behaviour (via generate_docs integration)
    - All branching in the __main__ try/except block

Mocks used:
    - shared.call_claude          → returns canned markdown strings
    - shared.get_repo_files       → returns synthetic file dicts
    - shared.write_output_file    → returns fake URLs
    - shared.send_email           → no-op
    - shared.email_html           → returns placeholder HTML string
    - shared.write_audit_entry    → no-op
    - shared.OUTPUT_REPO_OWNER    → patched to "test-owner"
    - shared.OUTPUT_REPO          → patched to "test-repo"
    - datetime.datetime.utcnow    → fixed timestamp for deterministic tests

TODOs:
    - TODO: Integration test that verifies real Claude API response shape
    - TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO are unset at import time
    - TODO: Test concurrent/parallel doc generation if threading is added later
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we never import the real one
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-output-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module with all symbols needed by tool2."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated doc")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fresh fake `shared` module before every test and reload tool2."""
    shared_mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)

    # Remove any cached version of the module under test
    sys.modules.pop("tool2_tech_docs", None)

    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        monkeypatch.syspath_prepend(scripts_dir)

    yield shared_mod

    # Cleanup
    sys.modules.pop("tool2_tech_docs", None)


def _import_tool2():
    """Import (or re-import) tool2_tech_docs and return the module."""
    # Locate the actual source file relative to this test file
    scripts_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".github",
        "scripts",
    )
    spec = importlib.util.spec_from_file_location(
        "tool2_tech_docs",
        os.path.join(scripts_dir, "tool2_tech_docs.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/app.py": "import flask\napp = flask.Flask(__name__)",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" {}',
    "infra/variables.yaml": "env: production\nregion: us-east-1",
}

SAMPLE_DOCS = {
    "README.md": "# README\nProject overview here.",
    "ARCHITECTURE.md": "# Architecture\nDetails here.",
    "RUNBOOK.md": "# Runbook\nOperational info here.",
}


# ---------------------------------------------------------------------------
# Tests: build_index
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_contains_repo_header(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("acme", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "Tech Documentation Index — acme/myrepo" in result

    def test_contains_timestamp(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("acme", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "2024-01-15 10:00 UTC" in result

    def test_contains_all_doc_links(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("acme", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("acme", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        expected_base = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/acme-myrepo/"
        )
        assert expected_base in result

    def test_links_point_to_correct_path_structure(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("acme", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "tech-docs/acme-myrepo/README.md" in result

    def test_empty_docs_dict_produces_valid_markdown(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("acme", "myrepo", {}, "2024-01-15 10:00 UTC")
        assert "Tech Documentation Index — acme/myrepo" in result
        # No document links should appear
        assert "blob/main" not in result

    def test_footer_attribution(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("acme", "myrepo", SAMPLE_DOCS, "2024-01-15 10:00 UTC")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_owner_with_special_chars_in_path(self, fake_shared):
        """Hyphenated owner/repo names should appear correctly in links."""
        tool2 = _import_tool2()
        result = tool2.build_index("my-org", "my-service", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "tech-docs/my-org-my-service/README.md" in result

    def test_returns_string(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("a", "b", SAMPLE_DOCS, "now")
        assert isinstance(result, str)

    def test_generated_label_present(self, fake_shared):
        tool2 = _import_tool2()
        result = tool2.build_index("a", "b", SAMPLE_DOCS, "now")
        assert "**Generated:**" in result


# ---------------------------------------------------------------------------
# Tests: generate_docs
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_calls_get_repo_files_for_source_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        calls = fake_shared.get_repo_files.call_args_list
        extensions_called = [c.args[2] for c in calls]
        assert [".py", ".js", ".ts", ".go"] in extensions_called

    def test_calls_get_repo_files_for_iac_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        calls = fake_shared.get_repo_files.call_args_list
        extensions_called = [c.args[2] for c in calls]
        assert [".tf", ".bicep", ".json", ".yaml", ".yml"] in extensions_called

    def test_calls_call_claude_three_times(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        assert fake_shared.call_claude.call_count == 3

    def test_returns_dict_with_three_keys(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        result = tool2.generate_docs("acme", "myrepo", "https://run.url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_uses_system_readme_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        first_call_system = fake_shared.call_claude.call_args_list[0].args[0]
        assert "README" in first_call_system or "technical writer" in first_call_system.lower()

    def test_arch_doc_uses_system_arch_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        second_call_system = fake_shared.call_claude.call_args_list[1].args[0]
        assert "architect" in second_call_system.lower() or "architecture" in second_call_system.lower()

    def test_runbook_uses_system_runbook_prompt(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        third_call_system = fake_shared.call_claude.call_args_list[2].args[0]
        assert "runbook" in third_call_system.lower() or "devops" in third_call_system.lower()

    def test_claude_return_value_stored_in_result(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]
        tool2 = _import_tool2()
        result = tool2.generate_docs("acme", "myrepo", "https://run.url")
        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_source_files_included_in_readme_prompt(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        readme_user_msg = fake_shared.call_claude.call_args_list[0].args[1]
        assert "backend/model_card.json" in readme_user_msg or "backend/app.py" in readme_user_msg

    def test_iac_files_included_in_arch_prompt(self, fake_shared):
        fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        arch_user_msg = fake_shared.call_claude.call_args_list[1].args[1]
        assert "infra/main.tf" in arch_user_msg

    def test_empty_files_produce_no_files_found_placeholder(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Doc"
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        readme_user_msg = fake_shared.call_claude.call_args_list[0].args[1]
        assert "_No files found_" in readme_user_msg

    def test_max_files_limit_passed_for_source_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        source_call = fake_shared.get_repo_files.call_args_list[0]
        assert source_call.kwargs.get("max_files") == 15 or source_call.args[-1] == 15

    def test_max_files_limit_passed_for_iac_files(self, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        tool2 = _import_tool2()
        tool2.generate_docs("acme", "myrepo", "https://run.url")
        iac_call = fake_shared.get_repo_files.call_args_list[1]
        assert iac_call.kwargs.get("max_files") == 10 or iac_call.args[-1] == 10

    def test_file_content_truncated_to_4000_chars(self, fake_shared):
        long_