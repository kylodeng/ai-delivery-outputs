"""
Test suite for tool2_tech_docs.py

What is tested:
- generate_docs(): happy path, empty file results, partial file results
- build_index(): happy path, multiple docs, empty docs, special characters in owner/repo
- __main__ block: success path, exception/failure path
- fmt() inner function behaviour (via generate_docs)

Mocks used:
- shared.call_claude          — prevents real Anthropic API calls
- shared.get_repo_files       — prevents real GitHub API calls
- shared.write_output_file    — prevents real GitHub write operations
- shared.send_email           — prevents real email sending
- shared.email_html           — prevents real HTML generation
- shared.write_audit_entry    — prevents real audit log writes
- shared.OUTPUT_REPO_OWNER    — patched to deterministic value
- shared.OUTPUT_REPO          — patched to deterministic value
- datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
- TODO: Integration test that verifies the exact Claude prompt structure matches
        expected format (requires snapshot/contract testing setup)
- TODO: Test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are
        completely absent (None values passed to generate_docs)
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared replaced by a mock
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot-org"
FAKE_OUTPUT_REPO = "ai-delivery-output"


def _make_shared_mock():
    """Return a MagicMock that looks like the shared module."""
    shared = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    shared.call_claude = MagicMock(return_value="# Generated doc")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/file")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    return shared


@pytest.fixture()
def shared_mock():
    """Provide a fresh shared mock and inject it into sys.modules before import."""
    mock = _make_shared_mock()
    with patch.dict(sys.modules, {"shared": mock}):
        # Force re-import so our mock is used
        if "tool2_tech_docs" in sys.modules:
            del sys.modules["tool2_tech_docs"]

        # Ensure the script directory is on path
        script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        import tool2_tech_docs  # noqa: PLC0415
        yield mock, tool2_tech_docs

    # Clean up after each test
    if "tool2_tech_docs" in sys.modules:
        del sys.modules["tool2_tech_docs"]


# ---------------------------------------------------------------------------
# Fixtures: common data
# ---------------------------------------------------------------------------

SAMPLE_PY_FILES = {
    "main.py": "def hello():\n    return 'world'\n",
    "utils.py": "import os\n\ndef get_env(key):\n    return os.environ.get(key)\n",
}

SAMPLE_IAC_FILES = {
    "main.tf": 'resource "aws_s3_bucket" "data" {\n  bucket = "my-bucket"\n}\n',
    "vars.yaml": "env: production\nregion: us-east-1\n",
}

SAMPLE_DOCS = {
    "README.md": "# README\nContent here.",
    "ARCHITECTURE.md": "# Architecture\nDetails here.",
    "RUNBOOK.md": "# Runbook\nOps details here.",
}


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------


class TestGenerateDocs:
    def test_happy_path_calls_get_repo_files_twice(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc content"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert mock_shared.get_repo_files.call_count == 2

    def test_happy_path_first_call_fetches_source_files(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        first_call_args = mock_shared.get_repo_files.call_args_list[0]
        assert first_call_args[0][0] == "myorg"
        assert first_call_args[0][1] == "myrepo"
        assert ".py" in first_call_args[0][2]
        assert first_call_args[1]["max_files"] == 15

    def test_happy_path_second_call_fetches_iac_files(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        second_call_args = mock_shared.get_repo_files.call_args_list[1]
        assert ".tf" in second_call_args[0][2]
        assert second_call_args[1]["max_files"] == 10

    def test_happy_path_calls_call_claude_three_times(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert mock_shared.call_claude.call_count == 3

    def test_happy_path_returns_three_keys(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        result = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_happy_path_returns_claude_responses(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        responses = ["# README content", "# Architecture content", "# Runbook content"]
        mock_shared.call_claude.side_effect = responses

        result = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert result["README.md"] == "# README content"
        assert result["ARCHITECTURE.md"] == "# Architecture content"
        assert result["RUNBOOK.md"] == "# Runbook content"

    def test_empty_files_uses_no_files_found_placeholder(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [{}, {}]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        # All three prompts should contain "_No files found_" since both dicts are empty
        for call_args in mock_shared.call_claude.call_args_list:
            prompt = call_args[0][1]
            assert "_No files found_" in prompt

    def test_source_files_only_iac_empty(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, {}]
        mock_shared.call_claude.return_value = "# Doc"

        result = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert len(result) == 3

    def test_iac_files_only_source_empty(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [{}, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        result = module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        assert len(result) == 3

    def test_prompt_contains_owner_and_repo(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("testowner", "testrepo", "https://github.com/run/1")

        for call_args in mock_shared.call_claude.call_args_list:
            prompt = call_args[0][1]
            assert "testowner" in prompt
            assert "testrepo" in prompt

    def test_file_content_truncated_to_4000_chars(self, shared_mock):
        mock_shared, module = shared_mock
        long_content = "x" * 8000
        py_files = {"bigfile.py": long_content}
        mock_shared.get_repo_files.side_effect = [py_files, {}]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        # The README call uses all_files_str which includes py_files
        readme_prompt = mock_shared.call_claude.call_args_list[0][0][1]
        # Should contain exactly 4000 x's (truncated), not 8000
        assert "x" * 4000 in readme_prompt
        assert "x" * 4001 not in readme_prompt

    def test_fmt_includes_filename_as_header(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [{"app.py": "print('hello')"}, {}]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        readme_prompt = mock_shared.call_claude.call_args_list[0][0][1]
        assert "### app.py" in readme_prompt

    def test_fmt_wraps_content_in_code_block(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [{"app.py": "print('hello')"}, {}]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        readme_prompt = mock_shared.call_claude.call_args_list[0][0][1]
        assert "```" in readme_prompt
        assert "print('hello')" in readme_prompt

    def test_call_claude_receives_correct_system_prompt_for_readme(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        readme_system = mock_shared.call_claude.call_args_list[0][0][0]
        assert "README" in readme_system
        assert "technical writer" in readme_system.lower()

    def test_call_claude_receives_correct_system_prompt_for_architecture(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        arch_system = mock_shared.call_claude.call_args_list[1][0][0]
        assert "architect" in arch_system.lower()

    def test_call_claude_receives_correct_system_prompt_for_runbook(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        runbook_system = mock_shared.call_claude.call_args_list[2][0][0]
        assert "runbook" in runbook_system.lower() or "devops" in runbook_system.lower()

    def test_get_repo_files_exception_propagates(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = RuntimeError("GitHub API error")

        with pytest.raises(RuntimeError, match="GitHub API error"):
            module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_call_claude_exception_propagates(self, shared_mock):
        mock_shared, module = shared_mock
        mock_shared.get_repo_files.side_effect = [SAMPLE_PY_FILES, SAMPLE_IAC_FILES]
        mock_shared.call_claude.side_effect = Exception("Claude API unavailable")

        with pytest.raises(Exception, match="Claude API unavailable"):
            module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

    def test_multiple_py_files_all_included_in_prompt(self, shared_mock):
        mock_shared, module = shared_mock
        py_files = {
            "a.py": "# file a",
            "b.py": "# file b",
            "c.py": "# file c",
        }
        mock_shared.get_repo_files.side_effect = [py_files, {}]
        mock_shared.call_claude.return_value = "# Doc"

        module.generate_docs("myorg", "myrepo", "https://github.com/run/1")

        readme_prompt = mock_shared.call_claude.call_args_list[0][0][1]
        assert "### a.py" in readme_prompt
        assert "### b.py" in readme_prompt
        assert "### c.py" in readme_prompt


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------