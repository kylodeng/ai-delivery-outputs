"""
Test module for tool2_tech_docs.py

What is tested:
    - generate_docs(): orchestrates file fetching and Claude calls to produce README, ARCHITECTURE, RUNBOOK
    - build_index(): constructs the markdown index page from doc names and metadata
    - __main__ block behaviour: success path (writes files, sends email, writes audit entry)
                                failure path (writes failed audit entry, sends failure email, re-raises)

Mocks used:
    - shared.call_claude          → avoids real Anthropic API calls
    - shared.get_repo_files       → avoids real GitHub API calls
    - shared.write_output_file    → avoids real file/GitHub writes
    - shared.send_email           → avoids real SMTP/SES calls
    - shared.email_html           → pure helper, mocked for isolation
    - shared.write_audit_entry    → avoids real audit-log writes
    - shared.OUTPUT_REPO_OWNER    → constant, patched where needed
    - shared.OUTPUT_REPO          → constant, patched where needed
    - datetime.datetime           → deterministic timestamps

TODOs:
    - TODO: Integration test against a real (sandboxed) repo once credentials available
    - TODO: Test behaviour when get_repo_files returns files whose content exceeds 4000 chars (truncation)
    - TODO: Test __main__ when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are absent (None values)
"""

import datetime
import importlib
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers to import the module under test with its `shared` dependency mocked
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_mock():
    """Return a MagicMock that looks enough like `shared` for the module to import."""
    shared = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_module(shared_mock):
    """Import (or re-import) tool2_tech_docs with the given shared mock injected."""
    # Ensure a clean import each time
    for key in list(sys.modules.keys()):
        if "tool2_tech_docs" in key:
            del sys.modules[key]

    sys.modules["shared"] = shared_mock

    # The script inserts its own directory at position 0; replicate that
    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    import importlib.util, pathlib

    # Locate the actual source file relative to this test file
    src_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool2_tech_docs.py"
    if not src_path.exists():
        # Fallback: look relative to cwd
        src_path = pathlib.Path("tool2_tech_docs.py")

    spec = importlib.util.spec_from_file_location("tool2_tech_docs", str(src_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def module(shared_mock):
    """Freshly imported tool2_tech_docs with mocked shared dependency."""
    return _import_module(shared_mock)


# ---------------------------------------------------------------------------
# Sample data (derived from synthetic data samples provided)
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "src/main.py": "def hello():\n    return 'world'\n",
    "src/utils.py": "import os\n\ndef get_env(key):\n    return os.getenv(key)\n",
}

SAMPLE_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" { bucket = "my-docs" }',
    "infra/variables.yaml": "variables:\n  env: prod\n",
}

SAMPLE_README_RESPONSE = "# My Project\nProject overview here.\n"
SAMPLE_ARCH_RESPONSE = "# Architecture\nDetails here.\n"
SAMPLE_RUNBOOK_RESPONSE = "# Runbook\nSteps here.\n"


# ===========================================================================
# Tests for generate_docs()
# ===========================================================================

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, module, shared_mock):
        """generate_docs should return README, ARCHITECTURE, RUNBOOK keys."""
        shared_mock.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        result = module.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}
        assert result["README.md"] == SAMPLE_README_RESPONSE
        assert result["ARCHITECTURE.md"] == SAMPLE_ARCH_RESPONSE
        assert result["RUNBOOK.md"] == SAMPLE_RUNBOOK_RESPONSE

    def test_get_repo_files_called_with_correct_extensions(self, module, shared_mock):
        """get_repo_files should be called twice: once for code, once for IaC."""
        shared_mock.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        module.generate_docs("acme", "my-repo", "https://github.com/run/1")

        calls = shared_mock.get_repo_files.call_args_list
        assert len(calls) == 2

        # First call: code files
        first_call_exts = calls[0][0][2]  # positional arg index 2
        assert ".py" in first_call_exts
        assert ".js" in first_call_exts
        assert ".ts" in first_call_exts
        assert ".go" in first_call_exts

        # Second call: IaC files
        second_call_exts = calls[1][0][2]
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts
        assert ".yml" in second_call_exts

    def test_call_claude_called_three_times(self, module, shared_mock):
        """call_claude should be invoked exactly three times (README, ARCH, RUNBOOK)."""
        shared_mock.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        module.generate_docs("acme", "my-repo", "https://github.com/run/1")

        assert shared_mock.call_claude.call_count == 3

    def test_readme_prompt_contains_owner_and_repo(self, module, shared_mock):
        """The README Claude prompt should mention the owner/repo pair."""
        shared_mock.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        module.generate_docs("acme", "special-repo", "https://github.com/run/1")

        readme_user_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "acme" in readme_user_prompt
        assert "special-repo" in readme_user_prompt

    def test_empty_file_sets_produce_no_files_found_placeholder(self, module, shared_mock):
        """When no files are returned, fmt() should yield '_No files found_'."""
        shared_mock.get_repo_files.side_effect = [{}, {}]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        result = module.generate_docs("acme", "empty-repo", "https://github.com/run/1")

        # All three docs should still be returned (Claude was called)
        assert "README.md" in result
        assert "ARCHITECTURE.md" in result
        assert "RUNBOOK.md" in result

        # The user prompt to Claude should contain the placeholder
        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_file_content_truncated_to_4000_chars(self, module, shared_mock):
        """Content longer than 4000 chars should be truncated in the prompt."""
        long_content = "x" * 6000
        shared_mock.get_repo_files.side_effect = [
            {"src/big.py": long_content},
            {},
        ]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        module.generate_docs("acme", "big-repo", "https://github.com/run/1")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        # The truncated content block should contain exactly 4000 x's
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_call_claude_raises_propagates(self, module, shared_mock):
        """If call_claude raises, generate_docs should propagate the exception."""
        shared_mock.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        shared_mock.call_claude.side_effect = RuntimeError("Claude API unavailable")

        with pytest.raises(RuntimeError, match="Claude API unavailable"):
            module.generate_docs("acme", "my-repo", "https://github.com/run/1")

    def test_get_repo_files_raises_propagates(self, module, shared_mock):
        """If get_repo_files raises, generate_docs should propagate the exception."""
        shared_mock.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            module.generate_docs("acme", "my-repo", "https://github.com/run/1")

    def test_max_files_limits_are_passed(self, module, shared_mock):
        """get_repo_files must be called with max_files=15 (code) and max_files=10 (IaC)."""
        shared_mock.get_repo_files.side_effect = [{}, {}]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        module.generate_docs("acme", "my-repo", "https://github.com/run/1")

        calls = shared_mock.get_repo_files.call_args_list
        assert calls[0][1].get("max_files") == 15 or calls[0][0][-1] == 15
        assert calls[1][1].get("max_files") == 10 or calls[1][0][-1] == 10

    def test_iac_files_used_in_architecture_prompt(self, module, shared_mock):
        """Architecture prompt should include IaC file content."""
        shared_mock.get_repo_files.side_effect = [
            SAMPLE_PY_FILES,
            {"infra/main.tf": 'resource "aws_lambda_function" "fn" {}'},
        ]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        module.generate_docs("acme", "infra-repo", "https://github.com/run/1")

        arch_prompt = shared_mock.call_claude.call_args_list[1][0][1]
        assert "aws_lambda_function" in arch_prompt

    def test_multiple_files_formatted_correctly(self, module, shared_mock):
        """fmt() should produce one section per file, with fenced code blocks."""
        shared_mock.get_repo_files.side_effect = [SAMPLE_PY_FILES, {}]
        shared_mock.call_claude.side_effect = [
            SAMPLE_README_RESPONSE,
            SAMPLE_ARCH_RESPONSE,
            SAMPLE_RUNBOOK_RESPONSE,
        ]

        module.generate_docs("acme", "my-repo", "https://github.com/run/1")

        readme_prompt = shared_mock.call_claude.call_args_list[0][0][1]
        assert "### src/main.py" in readme_prompt
        assert "### src/utils.py" in readme_prompt
        assert "```" in readme_prompt


# ===========================================================================
# Tests for build_index()
# ===========================================================================

class TestBuildIndex:

    def test_returns_string(self, module):
        docs = {"README.md": "...", "ARCHITECTURE.md": "...", "RUNBOOK.md": "..."}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert isinstance(result, str)

    def test_contains_owner_and_repo_in_title(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "acme/my-repo" in result

    def test_contains_generated_timestamp(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "2024-01-15 10:00 UTC" in result

    def test_all_doc_names_appear_as_links(self, module):
        docs = {
            "README.md": "r",
            "ARCHITECTURE.md": "a",
            "RUNBOOK.md": "rb",
        }
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        assert "[README.md]" in result
        assert "[ARCHITECTURE.md]" in result
        assert "[RUNBOOK.md]" in result

    def test_links_point_to_correct_output_repo(self, module):
        docs = {"README.md": "content"}
        result = module.build_index("acme", "my-repo", docs, "2024-01-15 10:00 UTC")
        expected_fragment = (
            f"https://github.com/{FAKE_OUTPUT_REPO_OWNER}/{FAKE_OUTPUT_REPO}"
            f"/blob/main/tech-docs/acme-my-repo/README.md"
        )
        assert expected_fragment in result

    def test_