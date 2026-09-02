"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file sets, partial file sets, Claude call ordering
- build_index(): correct markdown output, link format, multiple docs, empty docs dict
- __main__ block: successful run, exception/failure path
- fmt() inner function (via generate_docs): empty files, single file, content truncation at 4000 chars

Mocks used:
- shared.call_claude (all Claude API calls)
- shared.get_repo_files (GitHub file fetching)
- shared.write_output_file (output repo writes)
- shared.send_email (email sending)
- shared.email_html (HTML email builder)
- shared.write_audit_entry (audit logging)
- shared.OUTPUT_REPO_OWNER, shared.OUTPUT_REPO (constants)
- datetime.datetime.utcnow (deterministic timestamps)
- os.environ (environment variables)

TODOs:
- TODO: Integration test for actual Claude API response format validation
- TODO: Test for rate limiting / retry behaviour in call_claude
- TODO: Test for max_files boundary enforcement (requires get_repo_files implementation details)
"""

import sys
import os
import importlib
import datetime
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared patched out
# ---------------------------------------------------------------------------

SHARED_STUB_ATTRS = {
    "call_claude": MagicMock(return_value="# Generated content"),
    "get_repo_files": MagicMock(return_value={}),
    "write_output_file": MagicMock(return_value="https://github.com/output/file"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>email</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-org",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_stub():
    """Create a fresh stub module for `shared`."""
    stub = types.ModuleType("shared")
    for attr, val in SHARED_STUB_ATTRS.items():
        if callable(val):
            setattr(stub, attr, MagicMock(return_value=getattr(val, "return_value", None)))
        else:
            setattr(stub, attr, val)
    # Restore sensible defaults
    stub.call_claude.return_value = "# Generated content"
    stub.get_repo_files.return_value = {}
    stub.write_output_file.return_value = "https://github.com/output/file"
    stub.send_email.return_value = None
    stub.email_html.return_value = "<html>email</html>"
    stub.write_audit_entry.return_value = None
    return stub


@pytest.fixture(autouse=True)
def clean_module_cache():
    """Remove tool2_tech_docs from sys.modules before each test so we get a fresh import."""
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]


@pytest.fixture()
def shared_stub():
    stub = _make_shared_stub()
    with patch.dict(sys.modules, {"shared": stub}):
        yield stub


@pytest.fixture()
def module_under_test(shared_stub):
    """Import tool2_tech_docs with shared stubbed out."""
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try local directory (when running from repo root or scripts dir)
    local_dir = os.path.dirname(os.path.abspath(__file__))
    for candidate in [scripts_dir, local_dir]:
        target = os.path.join(candidate, "tool2_tech_docs.py")
        if os.path.exists(target):
            spec = importlib.util.spec_from_file_location("tool2_tech_docs", target)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["tool2_tech_docs"] = mod
            spec.loader.exec_module(mod)
            return mod

    # Fallback: plain import (works if pytest is run from scripts dir)
    import tool2_tech_docs as mod  # noqa: PLC0415
    return mod


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_happy_path_contains_repo_name(self, module_under_test):
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = module_under_test.build_index("myorg", "myrepo", docs, "2024-01-15 12:00 UTC")
        assert "myorg/myrepo" in result

    def test_happy_path_contains_timestamp(self, module_under_test):
        docs = {"README.md": "..."}
        result = module_under_test.build_index("org", "repo", docs, "2024-06-01 09:30 UTC")
        assert "2024-06-01 09:30 UTC" in result

    def test_links_use_output_repo_owner_and_repo(self, module_under_test, shared_stub):
        docs = {"README.md": "...", "RUNBOOK.md": "..."}
        result = module_under_test.build_index("src-org", "src-repo", docs, "2024-01-01 00:00 UTC")
        assert "test-org" in result
        assert "test-output-repo" in result

    def test_links_contain_correct_path_prefix(self, module_under_test):
        docs = {"README.md": "..."}
        result = module_under_test.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "tech-docs/org-repo/README.md" in result

    def test_all_doc_names_appear_as_links(self, module_under_test):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = module_under_test.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_empty_docs_dict(self, module_under_test):
        result = module_under_test.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        assert "org/repo" in result
        assert "## Documents" in result

    def test_output_contains_auto_generated_footer(self, module_under_test):
        docs = {"README.md": "..."}
        result = module_under_test.build_index("org", "repo", docs, "now")
        assert "Auto-generated" in result

    def test_link_format_is_markdown(self, module_under_test):
        docs = {"README.md": "..."}
        result = module_under_test.build_index("org", "repo", docs, "now")
        assert "[README.md](" in result

    def test_owner_and_repo_joined_with_hyphen_in_path(self, module_under_test):
        docs = {"README.md": "..."}
        result = module_under_test.build_index("my-org", "my-repo", docs, "now")
        assert "tech-docs/my-org-my-repo/" in result

    @pytest.mark.parametrize("owner,repo", [
        ("", "repo"),
        ("org", ""),
        ("", ""),
    ])
    def test_empty_owner_or_repo_strings(self, module_under_test, owner, repo):
        """build_index should not raise even with empty strings."""
        docs = {"README.md": "..."}
        result = module_under_test.build_index(owner, repo, docs, "now")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_happy_path_calls_get_repo_files_twice(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        module_under_test.generate_docs("org", "repo", "https://run.url")
        assert shared_stub.get_repo_files.call_count == 2

    def test_happy_path_calls_call_claude_three_times(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        module_under_test.generate_docs("org", "repo", "https://run.url")
        assert shared_stub.call_claude.call_count == 3

    def test_returns_dict_with_three_keys(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        result = module_under_test.generate_docs("org", "repo", "https://run.url")
        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_returns_claude_output_for_each_doc(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = ["README content", "ARCH content", "RUNBOOK content"]
        result = module_under_test.generate_docs("org", "repo", "https://run.url")
        assert result["README.md"] == "README content"
        assert result["ARCHITECTURE.md"] == "ARCH content"
        assert result["RUNBOOK.md"] == "RUNBOOK content"

    def test_py_js_files_fetched_with_correct_extensions(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        module_under_test.generate_docs("org", "repo", "https://run.url")
        first_call_args = shared_stub.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2] if len(first_call_args[0]) > 2 else first_call_args[1].get("extensions", first_call_args[0][2])
        # Accept positional or keyword
        all_args = first_call_args[0]
        assert ".py" in all_args[2]
        assert ".js" in all_args[2]
        assert ".ts" in all_args[2]

    def test_iac_files_fetched_with_correct_extensions(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        module_under_test.generate_docs("org", "repo", "https://run.url")
        second_call_args = shared_stub.get_repo_files.call_args_list[1]
        all_args = second_call_args[0]
        assert ".tf" in all_args[2]
        assert ".yaml" in all_args[2]
        assert ".yml" in all_args[2]

    def test_py_js_files_max_files_is_15(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        module_under_test.generate_docs("org", "repo", "https://run.url")
        first_call_kwargs = shared_stub.get_repo_files.call_args_list[0][1]
        assert first_call_kwargs.get("max_files") == 15

    def test_iac_files_max_files_is_10(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        module_under_test.generate_docs("org", "repo", "https://run.url")
        second_call_kwargs = shared_stub.get_repo_files.call_args_list[1][1]
        assert second_call_kwargs.get("max_files") == 10

    def test_owner_and_repo_passed_to_get_repo_files(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        module_under_test.generate_docs("myorg", "myrepo", "https://run.url")
        for c in shared_stub.get_repo_files.call_args_list:
            assert c[0][0] == "myorg"
            assert c[0][1] == "myrepo"

    def test_with_actual_source_files(self, module_under_test, shared_stub):
        """Files present in both py_js and iac buckets appear in prompts."""
        shared_stub.get_repo_files.side_effect = [
            {"src/main.py": "print('hello')"},
            {"infra/main.tf": 'resource "aws_s3_bucket" "b" {}'},
        ]
        shared_stub.call_claude.return_value = "content"
        module_under_test.generate_docs("org", "repo", "url")
        # README call should include main.py content
        readme_call = shared_stub.call_claude.call_args_list[0]
        assert "main.py" in readme_call[0][1]

    def test_content_truncated_to_4000_chars(self, module_under_test, shared_stub):
        long_content = "x" * 5000
        shared_stub.get_repo_files.side_effect = [
            {"long_file.py": long_content},
            {},
        ]
        shared_stub.call_claude.return_value = "content"
        module_under_test.generate_docs("org", "repo", "url")
        readme_call_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        # The formatted content should contain at most 4000 x's
        assert "x" * 4001 not in readme_call_prompt
        assert "x" * 4000 in readme_call_prompt

    def test_no_files_found_shows_placeholder(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        module_under_test.generate_docs("org", "repo", "url")
        readme_call_prompt = shared_stub.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_call_prompt

    def test_call_claude_raises_propagates(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("Claude unavailable")
        with pytest.raises(RuntimeError, match="Claude unavailable"):
            module_under_test.generate_docs("org", "repo", "url")

    def test_get_repo_files_raises_propagates(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.side_effect = ConnectionError("GitHub down")
        with pytest.raises(ConnectionError, match="GitHub down"):
            module_under_test.generate_docs("org", "repo", "url")

    def test_readme_system_prompt_passed_first(self, module_under_test, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "content"
        module_under_test.