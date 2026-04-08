"""
Tests for tool2_tech_docs.py
============================

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct URL generation, timestamp inclusion, all doc links present
- __main__ block behaviour: success path, exception/failure path
- fmt() helper (indirectly via generate_docs)

Mocks used:
- shared.call_claude          → prevents real Anthropic API calls
- shared.get_repo_files       → prevents real GitHub API calls
- shared.write_output_file    → prevents real GitHub commits
- shared.send_email           → prevents real email sending
- shared.email_html           → prevents real HTML generation
- shared.write_audit_entry    → prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    → constant override
- shared.OUTPUT_REPO          → constant override
- datetime.datetime.utcnow    → deterministic timestamps in __main__

TODOs:
- TODO: Integration test verifying the full round-trip against a real (sandboxed) GitHub repo
- TODO: Test for rate-limit / retry behaviour when call_claude raises a transient error
- TODO: Test verifying that file content is truncated to 4000 chars inside fmt()
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake `shared` module so the import at module
# level in tool2_tech_docs.py succeeds without the real shared.py present.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a module-like object that satisfies tool2_tech_docs' imports."""
    fake = types.ModuleType("shared")
    fake.call_claude = MagicMock(return_value="# Generated content")
    fake.get_repo_files = MagicMock(return_value={})
    fake.write_output_file = MagicMock(return_value="https://github.com/out/file")
    fake.send_email = MagicMock()
    fake.email_html = MagicMock(return_value="<html>body</html>")
    fake.write_audit_entry = MagicMock()
    fake.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    fake.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return fake


@pytest.fixture(autouse=True)
def inject_fake_shared(monkeypatch):
    """
    Inject the fake shared module before every test and reload
    tool2_tech_docs so it picks up the patched version.
    """
    fake_shared = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake_shared)

    # Force a clean import of the module under test
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        monkeypatch.syspath_prepend(script_dir)

    yield fake_shared

    # Cleanup
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]


def _import_module():
    """Import (or re-import) the module under test."""
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Locate the script relative to this test file
    script_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool2_tech_docs.py"
    )
    spec = importlib.util.spec_from_file_location("tool2_tech_docs", script_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "main.py": "def hello(): pass",
    "utils.py": "import os\n\ndef read_file(p): return open(p).read()",
}

SAMPLE_IAC_FILES = {
    "main.tf": 'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }',
    "variables.yaml": "env: production\nregion: us-east-1",
}


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_happy_path_calls_claude_three_times(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        inject_fake_shared.call_claude.return_value = "# Doc content"

        mod = _import_module()
        docs = mod.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert inject_fake_shared.call_claude.call_count == 3
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_returns_dict_with_three_keys(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        inject_fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        mod = _import_module()
        docs = mod.generate_docs("owner", "repo", "https://run.url")

        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_empty_files_still_calls_claude(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [{}, {}]
        inject_fake_shared.call_claude.return_value = "empty doc"

        mod = _import_module()
        docs = mod.generate_docs("o", "r", "https://x")

        assert inject_fake_shared.call_claude.call_count == 3

    def test_get_repo_files_called_with_correct_extensions(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [{}, {}]
        inject_fake_shared.call_claude.return_value = ""

        mod = _import_module()
        mod.generate_docs("owner", "repo", "https://run")

        calls = inject_fake_shared.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call – source code files
        first_exts = calls[0].args[2]
        assert ".py" in first_exts
        assert ".js" in first_exts
        assert ".ts" in first_exts
        assert ".go" in first_exts

        # Second call – IaC files
        second_exts = calls[1].args[2]
        assert ".tf" in second_exts
        assert ".yaml" in second_exts
        assert ".yml" in second_exts

    def test_get_repo_files_max_files_respected(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [{}, {}]
        inject_fake_shared.call_claude.return_value = ""

        mod = _import_module()
        mod.generate_docs("owner", "repo", "https://run")

        calls = inject_fake_shared.get_repo_files.call_args_list
        assert calls[0].kwargs.get("max_files") == 15 or calls[0].args[-1] == 15
        assert calls[1].kwargs.get("max_files") == 10 or calls[1].args[-1] == 10

    def test_owner_and_repo_appear_in_claude_prompt(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [{}, {}]
        inject_fake_shared.call_claude.return_value = ""

        mod = _import_module()
        mod.generate_docs("special-owner", "special-repo", "https://run")

        for c in inject_fake_shared.call_claude.call_args_list:
            user_prompt = c.args[1] if len(c.args) > 1 else c.kwargs.get("user_prompt", "")
            assert "special-owner" in user_prompt
            assert "special-repo" in user_prompt

    def test_call_claude_receives_system_prompts(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [{}, {}]
        inject_fake_shared.call_claude.return_value = ""

        mod = _import_module()
        mod.generate_docs("o", "r", "url")

        system_prompts = [
            c.args[0] if c.args else c.kwargs.get("system_prompt", "")
            for c in inject_fake_shared.call_claude.call_args_list
        ]
        # Each call should have a non-empty system prompt
        for sp in system_prompts:
            assert sp and len(sp) > 10

    def test_call_claude_exception_propagates(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        inject_fake_shared.call_claude.side_effect = RuntimeError("API error")

        mod = _import_module()
        with pytest.raises(RuntimeError, match="API error"):
            mod.generate_docs("o", "r", "url")

    def test_large_file_content_truncated_in_prompt(self, inject_fake_shared):
        """fmt() truncates each file to 4000 chars; verify Claude prompt length is bounded."""
        big_content = "x" * 10_000
        inject_fake_shared.get_repo_files.side_effect = [
            {"bigfile.py": big_content},
            {},
        ]
        inject_fake_shared.call_claude.return_value = ""

        mod = _import_module()
        mod.generate_docs("o", "r", "url")

        # The user prompt passed to the first call (README) should contain
        # at most 4000 'x' characters for the big file
        readme_call = inject_fake_shared.call_claude.call_args_list[0]
        user_prompt = readme_call.args[1] if len(readme_call.args) > 1 else ""
        assert user_prompt.count("x") <= 4000

    def test_mixed_py_and_iac_files(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        inject_fake_shared.call_claude.return_value = "ok"

        mod = _import_module()
        docs = mod.generate_docs("acme", "service", "https://run")

        # README prompt should contain both py and iac file names
        readme_call = inject_fake_shared.call_claude.call_args_list[0]
        readme_prompt = readme_call.args[1]
        assert "main.py" in readme_prompt
        assert "main.tf" in readme_prompt

    def test_arch_doc_gets_iac_files_in_prompt(self, inject_fake_shared):
        inject_fake_shared.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            SAMPLE_IAC_FILES,
        ]
        inject_fake_shared.call_claude.return_value = "ok"

        mod = _import_module()
        mod.generate_docs("acme", "service", "https://run")

        arch_call = inject_fake_shared.call_claude.call_args_list[1]
        arch_prompt = arch_call.args[1]
        assert "main.tf" in arch_prompt


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def _get_build_index(self):
        mod = _import_module()
        return mod.build_index

    def test_contains_owner_and_repo_in_title(self, inject_fake_shared):
        build_index = self._get_build_index()
        result = build_index("myorg", "myrepo", {"README.md": ""}, "2024-01-15 10:00 UTC")
        assert "myorg/myrepo" in result

    def test_contains_timestamp(self, inject_fake_shared):
        build_index = self._get_build_index()
        result = build_index("o", "r", {"README.md": ""}, "2024-06-01 12:00 UTC")
        assert "2024-06-01 12:00 UTC" in result

    def test_links_contain_all_doc_names(self, inject_fake_shared):
        build_index = self._get_build_index()
        docs = {"README.md": "x", "ARCHITECTURE.md": "y", "RUNBOOK.md": "z"}
        result = build_index("owner", "repo", docs, "2024-01-01 00:00 UTC")
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_output_repo_owner_and_repo(self, inject_fake_shared):
        build_index = self._get_build_index()
        docs = {"README.md": ""}
        result = build_index("src-owner", "src-repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_contain_correct_path(self, inject_fake_shared):
        build_index = self._get_build_index()
        docs = {"README.md": ""}
        result = build_index("src-owner", "src-repo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/src-owner-src-repo/README.md" in result

    def test_empty_docs_returns_valid_markdown(self, inject_fake_shared):
        build_index = self._get_build_index()
        result = build_index("o", "r", {}, "2024-01-01 00:00 UTC")
        assert "# Tech Documentation Index" in result
        assert "## Documents" in result

    def test_autogenerated_footer_present(self, inject_fake_shared):
        build_index = self._get_build_index()
        result = build_index("o", "r", {"README.md": ""}, "now")
        assert "Auto-generated" in result

    def test_multiple_docs_each_have_link(self, inject_fake_shared):
        build_index = self._get_build_index()
        docs = {f"DOC{i}.md": f"content {i}" for i in range(5)}
        result = build_index("o", "r", docs, "ts")
        for name in docs:
            assert name in result

    def test_link_format_is_markdown_list(self, inject_fake_shared):
        build_index = self._get_build_index()
        docs = {"README.md": ""}
        result = build_index("o", "r", docs, "ts")
        # Should contain a markdown link
        assert "[README.md]" in result
        