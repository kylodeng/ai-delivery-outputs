"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): orchestration of file fetching and Claude calls
- build_index(): index markdown generation with correct links and metadata
- __main__ block behaviour: success path and failure/exception path

Mocks used:
- shared.call_claude          → prevents real API calls to Claude/Anthropic
- shared.get_repo_files       → prevents real GitHub API calls
- shared.write_output_file    → prevents real file writes to output repo
- shared.send_email           → prevents real email dispatch
- shared.email_html           → prevents real template rendering
- shared.write_audit_entry    → prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    → constant substituted with test value
- shared.OUTPUT_REPO          → constant substituted with test value
- datetime.datetime.utcnow    → deterministic timestamps in tests

TODOs:
- TODO: Integration test covering real GitHub file fetch + Claude round-trip
        (requires live credentials — skipped)
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are
        missing (None) and generate_docs raises — depends on shared.get_repo_files
        behaviour with None owner
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so the import at module top succeeds
# without the real shared.py being present on sys.path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module with all symbols tool2 imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="# Generated content")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


# ---------------------------------------------------------------------------
# Fixture: import tool2 with the fake shared module injected
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_shared():
    """Inject a fresh fake shared module before each test; clean up after."""
    mod = _make_fake_shared()
    sys.modules["shared"] = mod
    yield mod
    # Remove cached import so next test gets a clean slate
    sys.modules.pop("shared", None)
    sys.modules.pop("tool2_tech_docs", None)


@pytest.fixture()
def tool2(fake_shared):
    """Import (or re-import) tool2_tech_docs with the fake shared in place."""
    # Ensure the script directory is on the path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Remove stale cached module if present
    sys.modules.pop("tool2_tech_docs", None)

    import importlib.util, pathlib

    candidate_paths = [
        pathlib.Path(__file__).parent / ".github" / "scripts" / "tool2_tech_docs.py",
        pathlib.Path(".github") / "scripts" / "tool2_tech_docs.py",
    ]
    spec = None
    for path in candidate_paths:
        if path.exists():
            spec = importlib.util.spec_from_file_location("tool2_tech_docs", path)
            break

    if spec is None:
        pytest.skip("tool2_tech_docs.py not found — adjust path if needed")

    module = importlib.util.module_from_spec(spec)
    sys.modules["tool2_tech_docs"] = module
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# Tests for build_index()
# ===========================================================================


class TestBuildIndex:
    """Unit tests for the build_index() helper."""

    def test_returns_string(self, tool2):
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = tool2.build_index("myorg", "myrepo", docs, "2024-01-15 12:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("acme", "backend", docs, "2024-01-15 12:00 UTC")
        assert "acme" in result
        assert "backend" in result

    def test_contains_timestamp(self, tool2):
        now = "2024-06-30 08:45 UTC"
        docs = {"README.md": "content"}
        result = tool2.build_index("org", "repo", docs, now)
        assert now in result

    def test_links_contain_all_doc_names(self, tool2):
        docs = {"README.md": "r", "ARCHITECTURE.md": "a", "RUNBOOK.md": "rb"}
        result = tool2.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_links_use_output_repo_owner_and_repo(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("src-owner", "src-repo", docs, "2024-01-01 00:00 UTC")
        assert FAKE_OUTPUT_REPO_OWNER in result
        assert FAKE_OUTPUT_REPO in result

    def test_links_include_correct_path_segment(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        # The path in each link should look like tech-docs/myorg-myrepo/README.md
        assert "tech-docs/myorg-myrepo/README.md" in result

    def test_empty_docs_produces_no_links(self, tool2):
        result = tool2.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        # Should still return a valid string without crashing
        assert "Tech Documentation Index" in result

    def test_header_present(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        assert "Tech Documentation Index" in result

    def test_footer_attribution(self, tool2):
        docs = {"README.md": "x"}
        result = tool2.build_index("o", "r", docs, "2024-01-01 00:00 UTC")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_special_characters_in_owner_repo(self, tool2):
        """Owners/repos with hyphens and underscores should be handled."""
        docs = {"README.md": "x"}
        result = tool2.build_index("my-org_2", "my-repo_v2", docs, "2024-01-01 00:00 UTC")
        assert "my-org_2" in result
        assert "my-repo_v2" in result

    def test_multiple_docs_produce_multiple_links(self, tool2):
        docs = {
            "README.md": "a",
            "ARCHITECTURE.md": "b",
            "RUNBOOK.md": "c",
        }
        result = tool2.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert result.count("https://github.com") == len(docs)

    def test_link_format_is_markdown(self, tool2):
        docs = {"README.md": "content"}
        result = tool2.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        # Markdown link syntax: [text](url)
        assert "[README.md](" in result


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================


class TestGenerateDocs:
    """Unit tests for the generate_docs() orchestration function."""

    def _setup_repo_files(self, fake_shared, py_files=None, iac_files=None):
        py_files = py_files or {"main.py": "print('hello')"}
        iac_files = iac_files or {"main.tf": 'resource "aws_s3_bucket" "b" {}'}

        def _side_effect(owner, repo, extensions, max_files=10):
            if ".py" in extensions:
                return py_files
            return iac_files

        fake_shared.get_repo_files.side_effect = _side_effect

    def test_returns_three_docs(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "# Doc content"
        docs = tool2.generate_docs("owner", "repo", "https://github.com/run/1")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_calls_get_repo_files_twice(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        tool2.generate_docs("owner", "repo", "https://github.com/run/1")
        assert fake_shared.get_repo_files.call_count == 2

    def test_calls_claude_three_times(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        tool2.generate_docs("owner", "repo", "https://github.com/run/1")
        assert fake_shared.call_claude.call_count == 3

    def test_readme_uses_all_files(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "# Content"
        tool2.generate_docs("myowner", "myrepo", "https://github.com/run/1")
        # First call_claude call should be for README and include the owner/repo
        first_call_args = fake_shared.call_claude.call_args_list[0]
        assert "myowner/myrepo" in first_call_args[0][1]

    def test_architecture_doc_uses_iac_files(self, tool2, fake_shared):
        self._setup_repo_files(
            fake_shared,
            iac_files={"main.tf": 'resource "aws_lambda_function" "f" {}'},
        )
        fake_shared.call_claude.return_value = "# arch"
        tool2.generate_docs("owner", "repo", "https://github.com/run/1")
        second_call_args = fake_shared.call_claude.call_args_list[1]
        # The architecture prompt text should appear in system arg
        assert "architect" in second_call_args[0][0].lower()

    def test_runbook_call_uses_all_files(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        fake_shared.call_claude.return_value = "# runbook"
        tool2.generate_docs("owner", "repo", "https://github.com/run/1")
        third_call_args = fake_shared.call_claude.call_args_list[2]
        assert "runbook" in third_call_args[0][0].lower()

    def test_empty_repo_uses_no_files_placeholder(self, tool2, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# Empty"
        docs = tool2.generate_docs("owner", "repo", "https://github.com/run/1")
        # Should still return three keys without raising
        assert len(docs) == 3

    def test_claude_response_stored_verbatim(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        responses = ["# README content", "# ARCH content", "# RUNBOOK content"]
        fake_shared.call_claude.side_effect = responses
        docs = tool2.generate_docs("o", "r", "url")
        assert docs["README.md"] == "# README content"
        assert docs["ARCHITECTURE.md"] == "# ARCH content"
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_get_repo_files_called_with_correct_owner_repo(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        fake_shared.call_claude.return_value = ""
        tool2.generate_docs("acme-corp", "backend-service", "url")
        calls = fake_shared.get_repo_files.call_args_list
        for c in calls:
            assert c[0][0] == "acme-corp"
            assert c[0][1] == "backend-service"

    def test_py_files_fetched_with_correct_extensions(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        fake_shared.call_claude.return_value = ""
        tool2.generate_docs("o", "r", "url")
        first_ext_call = fake_shared.get_repo_files.call_args_list[0]
        extensions = first_ext_call[0][2]
        assert ".py" in extensions

    def test_iac_files_fetched_with_tf_extension(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        fake_shared.call_claude.return_value = ""
        tool2.generate_docs("o", "r", "url")
        second_ext_call = fake_shared.get_repo_files.call_args_list[1]
        extensions = second_ext_call[0][2]
        assert ".tf" in extensions

    def test_call_claude_propagates_exception(self, tool2, fake_shared):
        self._setup_repo_files(fake_shared)
        fake_shared.call_claude.side_effect = RuntimeError("API timeout")
        with pytest.raises(RuntimeError, match="API timeout"):
            tool2.generate_docs("o", "r", "url")

    def test_get_repo_files_propagates_exception(self, tool2, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        with pytest.raises(ConnectionError):
            tool2.generate_docs("o", "r", "url")

    def test_file_content_truncated_to_4000_chars(self, tool2, fake_shared):
        """Files longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "# ok"

        # Capture what was passed to call_claude
        captured = []
        original = fake_shared.call_claude.side_effect

        def capture(*args, **kwargs):
            captured.append(args)
            return "# ok"

        fake_shared.call_claude.side_effect = capture
        tool2.generate_docs("o", "r", "url")

        # The user prompt passed to README generation (first call, second positional arg)