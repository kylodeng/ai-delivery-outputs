"""
Test suite for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates fetching repo files and calling Claude for README, ARCHITECTURE, RUNBOOK
    - build_index(): constructs a markdown index page with correct links and metadata
    - __main__ block behaviour: happy path (writes files, sends email, writes audit) and failure path

Mocks used:
    - shared.call_claude           → prevents real Anthropic API calls
    - shared.get_repo_files        → prevents real GitHub API calls
    - shared.write_output_file     → prevents real GitHub write operations
    - shared.send_email            → prevents real email sending (SES/SMTP)
    - shared.email_html            → prevents real template rendering
    - shared.write_audit_entry     → prevents real audit log writes
    - shared.OUTPUT_REPO_OWNER     → constant patched to a known value
    - shared.OUTPUT_REPO           → constant patched to a known value
    - datetime.datetime            → fixed UTC timestamp for determinism
    - os.environ                   → controlled via monkeypatch

TODOs:
    - TODO: Integration test that validates the full prompt text sent to Claude against a snapshot
    - TODO: Test behaviour when get_repo_files returns files larger than 4000 chars (truncation boundary)
    - TODO: Test that the correct Claude model/config is used (requires inspecting call_claude args)
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module with shared patched out
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot-org"
FAKE_OUTPUT_REPO = "ai-delivery-output"


def _make_shared_mock():
    """Return a MagicMock that looks like the shared module."""
    shared = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_tool(shared_mock):
    """
    Import (or re-import) tool2_tech_docs with shared replaced by shared_mock.
    Returns the module object.
    """
    sys.modules["shared"] = shared_mock
    # Force re-import so the module-level 'from shared import …' picks up the mock
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]

    # Adjust sys.path so the module can be found
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try relative to the repo root
    repo_root = os.path.dirname(__file__)
    github_scripts = os.path.join(repo_root, ".github", "scripts")
    if github_scripts not in sys.path:
        sys.path.insert(0, github_scripts)

    import tool2_tech_docs
    return tool2_tech_docs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def tool(shared_mock):
    return _import_tool(shared_mock)


@pytest.fixture()
def sample_py_files():
    return {
        "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
        "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent"}}',
    }


@pytest.fixture()
def sample_iac_files():
    return {
        "infra/main.tf": 'resource "aws_lambda_function" "bot" { function_name = "ai-bot" }',
        "infra/variables.yaml": "env: prod\nregion: us-east-1",
    }


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_contains_repo_header(self, tool):
        docs = {"README.md": "...", "ARCHITECTURE.md": "..."}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-15 10:00 UTC")
        assert "Tech Documentation Index — myorg/myrepo" in result

    def test_contains_generated_timestamp(self, tool):
        docs = {"README.md": "..."}
        now = "2024-06-01 12:00 UTC"
        result = tool.build_index("org", "repo", docs, now)
        assert now in result

    def test_links_use_output_repo_constants(self, tool):
        docs = {"README.md": "content"}
        result = tool.build_index("myorg", "myrepo", docs, "2024-01-01 00:00 UTC")
        expected_url = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/myorg-myrepo/README.md"
        )
        assert expected_url in result

    def test_all_doc_names_appear_as_links(self, tool):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            assert name in result

    def test_contains_auto_generated_footer(self, tool):
        docs = {"README.md": "..."}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        assert "Auto-generated by AI Delivery Bot" in result

    def test_empty_docs_produces_valid_markdown(self, tool):
        result = tool.build_index("org", "repo", {}, "2024-01-01 00:00 UTC")
        assert "Tech Documentation Index" in result
        # No links section content but header should still be present
        assert "## Documents" in result

    def test_special_chars_in_owner_repo(self, tool):
        docs = {"README.md": "content"}
        result = tool.build_index("my-org", "my-repo.v2", docs, "2024-01-01 00:00 UTC")
        assert "my-org/my-repo.v2" in result
        assert "my-org-my-repo.v2" in result  # used in path

    def test_multiple_docs_each_get_unique_link(self, tool):
        docs = {"README.md": "a", "ARCHITECTURE.md": "b", "RUNBOOK.md": "c"}
        result = tool.build_index("org", "repo", docs, "2024-01-01 00:00 UTC")
        for name in docs:
            url = (
                f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
                f"/blob/main/tech-docs/org-repo/{name}"
            )
            assert url in result


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def _setup(self, shared_mock, sample_py_files, sample_iac_files):
        shared_mock.get_repo_files.side_effect = [
            sample_py_files,   # first call: py/js/ts/go files
            sample_iac_files,  # second call: iac files
        ]
        shared_mock.call_claude.side_effect = [
            "# README content",
            "# ARCHITECTURE content",
            "# RUNBOOK content",
        ]

    def test_returns_three_documents(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert set(docs.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_get_repo_files_called_with_correct_extensions(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        calls = shared_mock.get_repo_files.call_args_list
        assert len(calls) == 2

        first_call_exts = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_call_exts
        assert ".js" in first_call_exts
        assert ".ts" in first_call_exts
        assert ".go" in first_call_exts

        second_call_exts = calls[1][0][2]
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts
        assert ".yml" in second_call_exts

    def test_call_claude_invoked_three_times(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert shared_mock.call_claude.call_count == 3

    def test_readme_content_comes_from_call_claude(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert docs["README.md"] == "# README content"

    def test_architecture_content_comes_from_call_claude(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert docs["ARCHITECTURE.md"] == "# ARCHITECTURE content"

    def test_runbook_content_comes_from_call_claude(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        docs = tool.generate_docs("myorg", "myrepo", "https://github.com/run/1")
        assert docs["RUNBOOK.md"] == "# RUNBOOK content"

    def test_owner_repo_included_in_claude_prompt(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        tool.generate_docs("acme", "widget-service", "https://github.com/run/1")
        all_prompts = " ".join(str(c) for c in shared_mock.call_claude.call_args_list)
        assert "acme/widget-service" in all_prompts

    def test_empty_repo_files_still_generates_docs(self, tool, shared_mock):
        shared_mock.get_repo_files.side_effect = [{}, {}]
        shared_mock.call_claude.side_effect = ["readme", "arch", "runbook"]
        docs = tool.generate_docs("org", "repo", "https://github.com/run/1")
        assert "README.md" in docs
        assert "ARCHITECTURE.md" in docs
        assert "RUNBOOK.md" in docs

    def test_no_files_found_placeholder_in_prompt(self, tool, shared_mock):
        shared_mock.get_repo_files.side_effect = [{}, {}]
        shared_mock.call_claude.side_effect = ["readme", "arch", "runbook"]
        tool.generate_docs("org", "repo", "https://github.com/run/1")
        # When no files, the fmt() helper returns "_No files found_"
        all_prompts = " ".join(str(c) for c in shared_mock.call_claude.call_args_list)
        assert "_No files found_" in all_prompts

    def test_get_repo_files_max_files_respected(self, tool, shared_mock, sample_py_files, sample_iac_files):
        self._setup(shared_mock, sample_py_files, sample_iac_files)
        tool.generate_docs("org", "repo", "https://github.com/run/1")
        calls = shared_mock.get_repo_files.call_args_list
        # First call max_files=15
        assert calls[0][1].get("max_files") == 15 or calls[0][0][3] == 15
        # Second call max_files=10
        assert calls[1][1].get("max_files") == 10 or calls[1][0][3] == 10

    def test_call_claude_raises_propagates(self, tool, shared_mock, sample_py_files, sample_iac_files):
        shared_mock.get_repo_files.side_effect = [sample_py_files, sample_iac_files]
        shared_mock.call_claude.side_effect = RuntimeError("API failure")
        with pytest.raises(RuntimeError, match="API failure"):
            tool.generate_docs("org", "repo", "https://github.com/run/1")

    def test_get_repo_files_raises_propagates(self, tool, shared_mock):
        shared_mock.get_repo_files.side_effect = ConnectionError("GitHub down")
        with pytest.raises(ConnectionError, match="GitHub down"):
            tool.generate_docs("org", "repo", "https://github.com/run/1")

    def test_file_content_truncated_to_4000_chars(self, tool, shared_mock):
        long_content = "x" * 10_000
        shared_mock.get_repo_files.side_effect = [
            {"big_file.py": long_content},
            {},
        ]
        shared_mock.call_claude.side_effect = ["readme", "arch", "runbook"]
        tool.generate_docs("org", "repo", "https://github.com/run/1")
        # The fmt() function slices content[:4000]; verify the long content
        # does NOT appear verbatim (only first 4000 chars should be used)
        all_prompts = " ".join(str(c) for c in shared_mock.call_claude.call_args_list)
        assert "x" * 4001 not in all_prompts
        assert "x" * 4000 in all_prompts

    @pytest.mark.skip(reason="TODO: requires snapshot of exact prompt text sent to Claude")
    def test_readme_uses_correct_system_prompt():
        pass

    @pytest.mark.skip(reason="TODO: requires snapshot of exact prompt text sent to Claude")
    def test_architecture_uses_correct_system_prompt():
        pass

    @pytest.mark.skip(reason="TODO: requires snapshot of exact prompt text sent to Claude")
    def test_runbook_uses_correct_system_prompt():
        pass


# ---------------------------------------------------------------------------
# Tests for __main__ happy path
# ---------------------------------------------------------------------------

class TestMainHappyPath:

    def _run_main(self, tool, shared_mock, monkeypatch, env_overrides=None):
        env = {
            "SOURCE_REPO_OWNER": "testorg",
            "SOURCE_REPO_NAME": "testrepo",
            "GITHUB_RUN_URL": "https://github.com/testorg/testrepo/actions/runs/99",
        }
        if env_overrides:
            env.update(env_overrides)

        for k, v in env.items():
            monkeypatch.setenv(k,