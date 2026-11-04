"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown output, link construction, timestamp embedding
- __main__ block logic: success path, failure/exception path
- fmt() inner function behaviour (via generate_docs integration)

Mocks used:
- shared.call_claude          → prevents real Anthropic API calls
- shared.get_repo_files       → prevents real GitHub API calls
- shared.write_output_file    → prevents real GitHub writes
- shared.send_email           → prevents real email sending
- shared.email_html           → prevents template side-effects
- shared.write_audit_entry    → prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    → patched as a constant string
- shared.OUTPUT_REPO          → patched as a constant string
- datetime.datetime.utcnow    → deterministic timestamps

TODOs:
- TODO: Integration test for real GitHub API round-trip (needs live tokens)
- TODO: Test Claude response truncation / content > 4000 chars per file
- TODO: Test behaviour when OUTPUT_REPO_OWNER / OUTPUT_REPO env vars are absent at import time
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we never import the real one
# ---------------------------------------------------------------------------

def _make_fake_shared(**overrides):
    """Return a minimal fake `shared` module with sensible defaults."""
    fake = types.ModuleType("shared")
    fake.call_claude         = mock.MagicMock(return_value="CLAUDE_OUTPUT")
    fake.get_repo_files      = mock.MagicMock(return_value={})
    fake.write_output_file   = mock.MagicMock(return_value="https://github.com/out/file")
    fake.send_email          = mock.MagicMock(return_value=None)
    fake.email_html          = mock.MagicMock(return_value="<html>email</html>")
    fake.write_audit_entry   = mock.MagicMock(return_value=None)
    fake.OUTPUT_REPO_OWNER   = "test-owner"
    fake.OUTPUT_REPO         = "test-output-repo"
    for k, v in overrides.items():
        setattr(fake, k, v)
    return fake


@pytest.fixture(autouse=True)
def patch_shared(monkeypatch):
    """
    Inject a fresh fake `shared` into sys.modules before every test, then
    reload tool2_tech_docs so it picks up the fake module.
    """
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Remove cached version of the module under test so it re-imports cleanly
    sys.modules.pop("tool2_tech_docs", None)
    yield fake


def _load_module():
    """Import (or re-import) tool2_tech_docs after sys.modules is patched."""
    import tool2_tech_docs as m
    return m


# ---------------------------------------------------------------------------
# build_index tests
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_happy_path_contains_title(self):
        m = _load_module()
        result = m.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "# Tech Documentation Index — acme/my-repo" in result

    def test_happy_path_contains_generated_timestamp(self):
        m = _load_module()
        result = m.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "2024-01-15 10:00 UTC" in result

    def test_happy_path_links_use_output_repo_constants(self, patch_shared):
        patch_shared.OUTPUT_REPO_OWNER = "docs-owner"
        patch_shared.OUTPUT_REPO       = "docs-repo"
        sys.modules.pop("tool2_tech_docs", None)
        m = _load_module()
        result = m.build_index("acme", "svc", {"README.md": "x"}, "2024-01-01 00:00 UTC")
        assert "docs-owner" in result
        assert "docs-repo" in result

    def test_happy_path_link_per_document(self):
        m = _load_module()
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = m.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_link_format_contains_path(self):
        m = _load_module()
        result = m.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "tech-docs/acme-my-repo/README.md" in result

    def test_link_is_github_url(self):
        m = _load_module()
        result = m.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "https://github.com/" in result

    def test_empty_docs_dict(self):
        m = _load_module()
        result = m.build_index("acme", "my-repo", {}, "2024-01-15 10:00 UTC")
        # Should still produce a valid index without crashing
        assert "Tech Documentation Index" in result
        assert "acme/my-repo" in result

    def test_footer_attribution(self):
        m = _load_module()
        result = m.build_index("acme", "my-repo", {"README.md": "x"}, "2024-01-15 10:00 UTC")
        assert "AI Delivery Bot" in result

    def test_owner_repo_in_path(self):
        m = _load_module()
        result = m.build_index("sun-life", "hk-portal", {"README.md": "x"}, "2024-06-01 09:00 UTC")
        assert "sun-life-hk-portal" in result

    def test_multiple_docs_each_have_link(self):
        m = _load_module()
        docs = {"README.md": "", "ARCHITECTURE.md": "", "RUNBOOK.md": ""}
        result = m.build_index("o", "r", docs, "now")
        for name in docs:
            assert f"[{name}]" in result


# ---------------------------------------------------------------------------
# generate_docs tests
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_returns_three_docs(self, patch_shared):
        patch_shared.get_repo_files.return_value = {"main.py": "print('hello')"}
        m = _load_module()
        docs = m.generate_docs("acme", "my-repo", "https://run-url")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_call_claude_called_three_times(self, patch_shared):
        patch_shared.get_repo_files.return_value = {"main.py": "code"}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        assert patch_shared.call_claude.call_count == 3

    def test_readme_uses_system_readme_prompt(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        first_call_system_prompt = patch_shared.call_claude.call_args_list[0][0][0]
        assert "senior technical writer" in first_call_system_prompt

    def test_architecture_uses_arch_prompt(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        second_call_system_prompt = patch_shared.call_claude.call_args_list[1][0][0]
        assert "cloud solutions architect" in second_call_system_prompt

    def test_runbook_uses_runbook_prompt(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        third_call_system_prompt = patch_shared.call_claude.call_args_list[2][0][0]
        assert "DevOps engineer" in third_call_system_prompt

    def test_get_repo_files_called_for_py_js(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        calls = patch_shared.get_repo_files.call_args_list
        extensions_used = [set(c[0][2]) for c in calls]
        # First call should cover py/js/ts/go
        assert {".py", ".js", ".ts", ".go"} in extensions_used

    def test_get_repo_files_called_for_iac(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        calls = patch_shared.get_repo_files.call_args_list
        extensions_used = [set(c[0][2]) for c in calls]
        assert {".tf", ".bicep", ".json", ".yaml", ".yml"} in extensions_used

    def test_max_files_respected_for_py(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        first_call_kwargs = patch_shared.get_repo_files.call_args_list[0]
        # max_files passed as keyword or positional
        all_args = list(first_call_kwargs[0]) + list(first_call_kwargs[1].values())
        assert 15 in all_args

    def test_max_files_respected_for_iac(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        second_call = patch_shared.get_repo_files.call_args_list[1]
        all_args = list(second_call[0]) + list(second_call[1].values())
        assert 10 in all_args

    def test_empty_files_produces_no_files_found(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        # All three call_claude user prompts should contain "_No files found_"
        for call in patch_shared.call_claude.call_args_list:
            user_prompt = call[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, patch_shared):
        long_content = "x" * 10_000
        patch_shared.get_repo_files.return_value = {"big_file.py": long_content}
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        # Verify the user prompt does not contain the full 10 000 chars of x's
        for call in patch_shared.call_claude.call_args_list:
            user_prompt = call[0][1]
            # Content block is at most 4000 x's
            assert "x" * 4001 not in user_prompt

    def test_docs_values_come_from_call_claude(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.side_effect = ["README_CONTENT", "ARCH_CONTENT", "RUNBOOK_CONTENT"]
        m = _load_module()
        docs = m.generate_docs("acme", "my-repo", "https://run-url")
        assert docs["README.md"]       == "README_CONTENT"
        assert docs["ARCHITECTURE.md"] == "ARCH_CONTENT"
        assert docs["RUNBOOK.md"]      == "RUNBOOK_CONTENT"

    def test_owner_and_repo_appear_in_readme_prompt(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("sun-life", "hk-portal", "https://run-url")
        readme_user_prompt = patch_shared.call_claude.call_args_list[0][0][1]
        assert "sun-life" in readme_user_prompt
        assert "hk-portal" in readme_user_prompt

    def test_call_claude_raises_propagates(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.side_effect = RuntimeError("API error")
        m = _load_module()
        with pytest.raises(RuntimeError, match="API error"):
            m.generate_docs("acme", "my-repo", "https://run-url")

    def test_multiple_files_all_included_in_prompt(self, patch_shared):
        files = {"a.py": "code_a", "b.py": "code_b"}
        patch_shared.get_repo_files.side_effect = [files, {}]
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        readme_prompt = patch_shared.call_claude.call_args_list[0][0][1]
        assert "a.py" in readme_prompt
        assert "b.py" in readme_prompt


# ---------------------------------------------------------------------------
# fmt inner function (tested indirectly via generate_docs)
# ---------------------------------------------------------------------------

class TestFmtInnerFunction:

    def test_fmt_includes_filename_as_header(self, patch_shared):
        patch_shared.get_repo_files.side_effect = [{"deploy.tf": "resource {}"}, {}]
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        arch_prompt = patch_shared.call_claude.call_args_list[1][0][1]
        assert "### deploy.tf" in arch_prompt

    def test_fmt_wraps_content_in_code_fence(self, patch_shared):
        patch_shared.get_repo_files.side_effect = [{"app.py": "import os"}, {}]
        m = _load_module()
        m.generate_docs("acme", "my-repo", "https://run-url")
        readme_prompt = patch_shared.call_claude.call_args_list[0][0][1]
        assert "```" in readme_prompt
        assert "import os" in readme_prompt

    def test_fmt_empty_dict_returns_no_files_found(self, patch_shared):
        patch_shared.get_repo_files.return_value = {}
        m = _load_module()
        m.generate_docs("acme", "my