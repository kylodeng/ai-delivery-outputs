"""
Test suite for tool2_tech_docs.py

What is tested:
    - generate_docs(): happy path, empty file results, partial file results
    - build_index(): happy path, empty docs dict, special characters in owner/repo,
      correct URL construction, multiple documents
    - __main__ block: success path, exception/failure path (via subprocess or importlib)
    - fmt() inner function behaviour (via generate_docs integration)
    - Output path construction
    - Email and audit calls on success and failure

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - shared.OUTPUT_REPO_OWNER    → unittest.mock.patch
    - shared.OUTPUT_REPO          → unittest.mock.patch
    - datetime.datetime           → unittest.mock.patch

TODOs:
    # TODO: Integration test requiring real GitHub API credentials
    # TODO: Integration test requiring real Anthropic/Claude API credentials
    # TODO: Test __main__ block in isolation without subprocess (needs refactor to main() fn)
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake 'shared' module so we can import the
# module under test without needing the real shared.py on the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-output-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake shared module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="# Generated Content")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/url")
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module_with_fake_shared(fake_shared=None):
    """
    Import (or re-import) tool2_tech_docs with a fake shared module injected.
    Returns the imported module.
    """
    if fake_shared is None:
        fake_shared = _make_fake_shared()

    # Insert the fake shared module before importing
    sys.modules["shared"] = fake_shared

    # Force a clean re-import
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try the directory relative to this test file
    alt_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    alt_dir = os.path.normpath(alt_dir)
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    import tool2_tech_docs  # noqa: PLC0415
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_shared():
    """Inject a fresh fake shared module for every test."""
    shared = _make_fake_shared()
    sys.modules["shared"] = shared
    yield shared
    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def module(fake_shared):
    """Return a freshly imported tool2_tech_docs module."""
    return _import_module_with_fake_shared(fake_shared)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "src/main.py": "def main():\n    pass\n",
    "src/utils.py": "import os\n\ndef helper():\n    return os.getcwd()\n",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "bucket" {\n  bucket = "my-bucket"\n}\n',
    "infra/variables.yaml": "env: production\nregion: us-east-1\n",
}

SAMPLE_MIXED_FILES = {**SAMPLE_PY_FILES, **SAMPLE_IAC_FILES}


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:
    def test_happy_path_single_doc(self, module):
        docs = {"README.md": "# Hello"}
        now = "2024-01-15 12:00 UTC"
        result = module.build_index("myowner", "myrepo", docs, now)

        assert "Tech Documentation Index" in result
        assert "myowner/myrepo" in result
        assert "2024-01-15 12:00 UTC" in result
        assert "README.md" in result
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_happy_path_multiple_docs(self, module):
        docs = {
            "README.md": "content1",
            "ARCHITECTURE.md": "content2",
            "RUNBOOK.md": "content3",
        }
        now = "2024-06-01 09:30 UTC"
        result = module.build_index("owner", "repo", docs, now)

        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result
        assert result.count("- [") == 3

    def test_empty_docs(self, module):
        docs = {}
        now = "2024-01-01 00:00 UTC"
        result = module.build_index("owner", "repo", docs, now)

        assert "Tech Documentation Index" in result
        assert "owner/repo" in result
        # No list items when docs is empty
        assert "- [" not in result

    def test_url_format_correct(self, module):
        docs = {"README.md": "content"}
        now = "2024-01-01 00:00 UTC"
        result = module.build_index("acme", "widget", docs, now)

        expected_url = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/acme-widget/README.md"
        )
        assert expected_url in result

    def test_special_characters_in_owner_repo(self, module):
        docs = {"README.md": "content"}
        now = "2024-01-01 00:00 UTC"
        result = module.build_index("my-org", "my-repo-123", docs, now)

        assert "my-org/my-repo-123" in result
        assert "my-org-my-repo-123" in result

    def test_footer_present(self, module):
        docs = {"README.md": "content"}
        now = "2024-01-01 00:00 UTC"
        result = module.build_index("owner", "repo", docs, now)

        assert "Auto-generated by AI Delivery Bot" in result

    def test_generated_timestamp_in_output(self, module):
        docs = {"README.md": "content"}
        now = "2099-12-31 23:59 UTC"
        result = module.build_index("owner", "repo", docs, now)

        assert "2099-12-31 23:59 UTC" in result

    def test_returns_string(self, module):
        result = module.build_index("owner", "repo", {"README.md": "x"}, "2024-01-01")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:
    def test_happy_path_calls_get_repo_files_twice(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES

        module.generate_docs("owner", "repo", "https://run-url")

        assert fake_shared.get_repo_files.call_count == 2

    def test_happy_path_fetches_code_files(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}

        module.generate_docs("owner", "repo", "https://run-url")

        first_call_args = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".js" in extensions
        assert ".ts" in extensions
        assert ".go" in extensions

    def test_happy_path_fetches_iac_files(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}

        module.generate_docs("owner", "repo", "https://run-url")

        second_call_args = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_call_args[0][2]
        assert ".tf" in extensions
        assert ".yaml" in extensions
        assert ".yml" in extensions
        assert ".json" in extensions

    def test_happy_path_calls_call_claude_three_times(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = SAMPLE_PY_FILES
        fake_shared.call_claude.return_value = "# Doc content"

        module.generate_docs("owner", "repo", "https://run-url")

        assert fake_shared.call_claude.call_count == 3

    def test_returns_dict_with_three_docs(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Content"

        result = module.generate_docs("owner", "repo", "https://run-url")

        assert isinstance(result, dict)
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result
        assert len(result) == 3

    def test_doc_content_comes_from_call_claude(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = [
            "# README content",
            "# ARCH content",
            "# RUNBOOK content",
        ]

        result = module.generate_docs("owner", "repo", "https://run-url")

        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# ARCH content"
        assert result["RUNBOOK.md"] == "# RUNBOOK content"

    def test_passes_owner_repo_to_claude_prompts(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("acme-corp", "my-service", "https://run-url")

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]  # second positional arg
            assert "acme-corp/my-service" in user_prompt

    def test_empty_files_uses_no_files_found_placeholder(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "https://run-url")

        # All three calls should receive the "_No files found_" placeholder
        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            assert "_No files found_" in user_prompt

    def test_file_content_truncated_to_4000_chars(self, module, fake_shared):
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "content"

        module.generate_docs("owner", "repo", "https://run-url")

        for c in fake_shared.call_claude.call_args_list:
            user_prompt = c[0][1]
            # The truncated content should appear (4000 x's) but not 10000
            if "big_file.py" in user_prompt:
                assert "x" * 4000 in user_prompt
                assert "x" * 4001 not in user_prompt

    def test_call_claude_exception_propagates(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            module.generate_docs("owner", "repo", "https://run-url")

    def test_get_repo_files_exception_propagates(self, module, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            module.generate_docs("owner", "repo", "https://run-url")

    def test_max_files_code_is_15(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}

        module.generate_docs("owner", "repo", "https://run")

        first_call = fake_shared.get_repo_files.call_args_list[0]
        kwargs = first_call[1] if first_call[1] else {}
        args = first_call[0]
        # max_files can be positional (index 3) or keyword
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 15

    def test_max_files_iac_is_10(self, module, fake_shared):
        fake_shared.get_repo_files.return_value = {}

        module.generate_docs("owner", "repo", "https://run")

        second_call = fake_shared.get_repo_files.call_args_list[1]
        kwargs = second_call[1] if second_call[1] else {}
        args = second_call[0]
        max_files = kwargs.get("max_files", args[3] if len(args) > 3 else None)
        assert max_files == 10

    def test_iac_files_only_passed_to_arch_doc(self, module, fake_shared):
        call_count = [0]
        captured_prompts = {}

        def side_effect(system, user):
            idx = call_count[0]
            call_count[0] += 1
            captured_prompts[idx] = (system, user)
            return f"content_{idx}"

        fake_shared.call_claude.side_effect = side_effect

        iac_only_content = "iac_marker_xyz"
        code_only_content = "code_marker_abc"

        def get_files(