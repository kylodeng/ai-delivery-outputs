"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets
- build_index(): correct markdown output, link generation, timestamp inclusion
- fmt() inner function behavior (via generate_docs)
- Main block execution: success path, exception/failure path
- Environment variable handling

Mocks used:
- shared.call_claude (patched to return deterministic strings)
- shared.get_repo_files (patched to return controlled dicts)
- shared.write_output_file (patched to return fake URLs)
- shared.send_email (patched to no-op)
- shared.email_html (patched to return stub HTML)
- shared.write_audit_entry (patched to no-op)
- shared.OUTPUT_REPO_OWNER, shared.OUTPUT_REPO (patched as constants)
- datetime.datetime.utcnow (patched for deterministic timestamps)

TODOs:
- TODO: Integration test against a real GitHub repo requires credentials/network — skipped
- TODO: Test for very large files truncated at 4000 chars requires real file content — stub included
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module with shared patched out before import
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub module for `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>stub</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture()
def shared_stub():
    """Inject the shared stub and reload tool2_tech_docs for each test."""
    stub = _make_shared_stub()
    # Insert both under sys.path prefix used by the module itself
    sys.modules["shared"] = stub
    # Force re-import so module-level `from shared import ...` picks up the stub
    module_name = "tool2_tech_docs"
    if module_name in sys.modules:
        del sys.modules[module_name]
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Also handle running from repo root vs. test directory
    alt_dir = os.path.join(os.path.dirname(__file__))
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    import tool2_tech_docs as m
    yield stub, m

    # Cleanup
    if module_name in sys.modules:
        del sys.modules[module_name]


# ---------------------------------------------------------------------------
# Fixtures: synthetic file data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "src/main.py": "def main():\n    pass\n",
    "src/utils.py": "def helper():\n    return 42\n",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "b" { bucket = "my-bucket" }\n',
    "infra/variables.yaml": "env: production\nregion: us-east-1\n",
}


# ---------------------------------------------------------------------------
# Tests for build_index
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_happy_path_contains_repo_info(self, shared_stub):
        _, m = shared_stub
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = m.build_index("my-org", "my-repo", docs, "2024-01-15 10:00 UTC")

        assert "my-org/my-repo" in result
        assert "2024-01-15 10:00 UTC" in result

    def test_happy_path_contains_all_doc_links(self, shared_stub):
        _, m = shared_stub
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = m.build_index("my-org", "my-repo", docs, "2024-01-15 10:00 UTC")

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

    def test_links_use_correct_output_repo(self, shared_stub):
        _, m = shared_stub
        docs = {"README.md": "content"}
        result = m.build_index("my-org", "my-repo", docs, "2024-01-15 10:00 UTC")

        expected_base = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/my-org-my-repo/README.md"
        )
        assert expected_base in result

    def test_links_correct_path_format(self, shared_stub):
        _, m = shared_stub
        docs = {"RUNBOOK.md": "x"}
        result = m.build_index("owner", "repo", docs, "now")
        # Path segment must be owner-repo (hyphen joined)
        assert "tech-docs/owner-repo/RUNBOOK.md" in result

    def test_empty_docs(self, shared_stub):
        _, m = shared_stub
        result = m.build_index("o", "r", {}, "2024-01-01 00:00 UTC")
        assert "# Tech Documentation Index" in result
        # No bullet links
        assert "- [" not in result

    def test_auto_generated_footer(self, shared_stub):
        _, m = shared_stub
        result = m.build_index("o", "r", {"README.md": ""}, "ts")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_timestamp_appears_in_output(self, shared_stub):
        _, m = shared_stub
        ts = "2099-12-31 23:59 UTC"
        result = m.build_index("o", "r", {"README.md": ""}, ts)
        assert ts in result

    def test_multiple_docs_all_present(self, shared_stub):
        _, m = shared_stub
        docs = {f"DOC_{i}.md": f"content {i}" for i in range(5)}
        result = m.build_index("o", "r", docs, "ts")
        for name in docs:
            assert name in result

    def test_special_characters_in_owner_repo(self, shared_stub):
        _, m = shared_stub
        # Hyphens are common in GitHub org/repo names
        docs = {"README.md": ""}
        result = m.build_index("my-org-123", "my-repo-456", docs, "ts")
        assert "my-org-123/my-repo-456" in result
        assert "tech-docs/my-org-123-my-repo-456/README.md" in result


# ---------------------------------------------------------------------------
# Tests for generate_docs
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_happy_path_calls_get_repo_files_twice(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        m.generate_docs("owner", "repo", "https://run.url")
        assert stub.get_repo_files.call_count == 2

    def test_happy_path_calls_get_repo_files_with_correct_extensions(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        m.generate_docs("owner", "repo", "https://run.url")

        calls = stub.get_repo_files.call_args_list
        # First call: source files
        first_extensions = calls[0][0][2]
        assert ".py" in first_extensions
        assert ".js" in first_extensions
        assert ".ts" in first_extensions
        assert ".go" in first_extensions
        # Second call: IaC files
        second_extensions = calls[1][0][2]
        assert ".tf" in second_extensions
        assert ".yaml" in second_extensions
        assert ".yml" in second_extensions

    def test_happy_path_max_files_limits(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        m.generate_docs("owner", "repo", "https://run.url")

        calls = stub.get_repo_files.call_args_list
        assert calls[0][1]["max_files"] == 15
        assert calls[1][1]["max_files"] == 10

    def test_happy_path_calls_call_claude_three_times(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc content"
        m.generate_docs("owner", "repo", "https://run.url")
        assert stub.call_claude.call_count == 3

    def test_happy_path_returns_three_docs(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc content"
        result = m.generate_docs("owner", "repo", "https://run.url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_from_claude(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = m.generate_docs("owner", "repo", "https://run.url")
        assert result["README.md"] == "README content"

    def test_architecture_content_from_claude(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = m.generate_docs("owner", "repo", "https://run.url")
        assert result["ARCHITECTURE.md"] == "ARCH content"

    def test_runbook_content_from_claude(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = m.generate_docs("owner", "repo", "https://run.url")
        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_owner_repo_appears_in_claude_prompt(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        m.generate_docs("my-owner", "my-repo", "https://run.url")
        for c in stub.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "my-owner/my-repo" in user_prompt

    def test_with_sample_py_files(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        stub.call_claude.return_value = "# Generated"
        result = m.generate_docs("owner", "repo", "https://run.url")
        # All three docs should be present
        assert len(result) == 3
        # Source file paths should appear somewhere in claude prompts
        all_prompts = " ".join(str(c) for c in stub.call_claude.call_args_list)
        assert "src/main.py" in all_prompts

    def test_with_sample_iac_files(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        stub.call_claude.return_value = "# Generated"
        m.generate_docs("owner", "repo", "https://run.url")
        all_prompts = " ".join(str(c) for c in stub.call_claude.call_args_list)
        assert "infra/main.tf" in all_prompts

    def test_empty_files_returns_no_files_found(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "# Doc"
        m.generate_docs("owner", "repo", "https://run.url")
        # The prompt should include the fallback text for empty file sets
        all_prompts = " ".join(str(c) for c in stub.call_claude.call_args_list)
        assert "_No files found_" in all_prompts

    def test_call_claude_system_prompts_used(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "x"
        m.generate_docs("o", "r", "url")
        system_prompts = [c[0][0] for c in stub.call_claude.call_args_list]
        assert any("technical writer" in p.lower() for p in system_prompts)
        assert any("architect" in p.lower() for p in system_prompts)
        assert any("devops" in p.lower() for p in system_prompts)

    def test_call_claude_raises_propagates(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.side_effect = RuntimeError("Claude API down")
        with pytest.raises(RuntimeError, match="Claude API down"):
            m.generate_docs("owner", "repo", "https://run.url")

    def test_get_repo_files_raises_propagates(self, shared_stub):
        stub, m = shared_stub
        stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            m.generate_docs("owner", "repo", "https://run.url")

    def test_file_content_truncated_at_4000_chars(self, shared_stub):
        stub, m = shared_stub
        long_content = "x" * 10000
        stub.get_repo_files.side_effect = [{"big_file.py": long_content}, {}]
        stub.call_claude.return_value = "# doc"
        m.generate_docs("o", "r", "url")
        all_prompts = " ".join(str(c) for c in stub.call_claude.call_args_list)
        # The truncated version (4000 chars) should be present, not the full 10000
        assert "x" * 4001 not in all_prompts
        assert "x" * 4000 in all_prompts


# ---------------------------------------------------------------------------
# Tests for fmt inner function (via generate_docs)
# ---------------------------------------------------------------------------


class TestFmtInnerFunction:
    """Tests for the fmt