"""
Test suite for tool2_tech_docs.py

What is tested:
  - generate_docs(): orchestrates calls to get_repo_files and call_claude
  - build_index(): constructs markdown index page with correct links and metadata
  - __main__ block: happy path (docs written, email sent, audit logged) and failure path

Mocks used:
  - shared.call_claude          → patched to return synthetic doc strings
  - shared.get_repo_files       → patched to return synthetic file dicts
  - shared.write_output_file    → patched to return synthetic URLs
  - shared.send_email           → patched (no-op)
  - shared.email_html           → patched to return a dummy HTML string
  - shared.write_audit_entry    → patched (no-op)
  - shared.OUTPUT_REPO_OWNER    → patched via monkeypatch on module attribute
  - shared.OUTPUT_REPO          → patched via monkeypatch on module attribute
  - datetime.datetime.utcnow    → patched for deterministic timestamps
  - os.environ                  → patched for __main__ block tests

TODOs:
  - TODO: Integration test that verifies actual Claude prompt content/format
    (requires real Claude API key — stub provided)
  - TODO: Test behaviour when get_repo_files returns files exceeding 4000 chars
    (truncation logic) — stub provided
  - TODO: Test retry / rate-limit handling inside call_claude interactions
    (depends on shared.py implementation details)
"""

import importlib
import sys
import os
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------

SYNTHETIC_PY_FILES = {
    "src/main.py": "def main():\n    pass\n",
    "src/utils.py": "# TODO: add error handling\ndef helper():\n    return 42\n",
}

SYNTHETIC_IAC_FILES = {
    "infra/main.tf": 'resource "aws_s3_bucket" "data" { bucket = "my-bucket" }\n',
    "infra/vars.yaml": "env: production\nregion: us-east-1\n",
}

SYNTHETIC_README   = "# My Project\n\nAuto-generated README.\n"
SYNTHETIC_ARCH_DOC = "# Architecture\n\nAuto-generated ARCHITECTURE.\n"
SYNTHETIC_RUNBOOK  = "# Runbook\n\nAuto-generated RUNBOOK.\n"

FAKE_OUTPUT_URL    = "https://github.com/output-owner/output-repo/blob/main/tech-docs/acme-myrepo/README.md"
FAKE_INDEX_URL     = "https://github.com/output-owner/output-repo/blob/main/tech-docs/acme-myrepo/INDEX.md"
FAKE_NOW           = "2024-01-15 10:30 UTC"


def _make_shared_stub():
    """Return a minimal stub module that replaces `shared` during import."""
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock()
    shared.get_repo_files     = MagicMock()
    shared.write_output_file  = MagicMock(return_value=FAKE_OUTPUT_URL)
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html>dummy</html>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "output-owner"
    shared.OUTPUT_REPO        = "output-repo"
    return shared


@pytest.fixture()
def shared_stub():
    """Inject a fresh shared stub and (re)load tool2_tech_docs against it."""
    stub = _make_shared_stub()
    with mock.patch.dict(sys.modules, {"shared": stub}):
        # Remove cached module so the import picks up our stub
        sys.modules.pop("tool2_tech_docs", None)
        # Ensure the script directory is on the path
        script_dir = os.path.join(os.path.dirname(__file__),
                                  ".github", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        import tool2_tech_docs as m
        yield m, stub
    sys.modules.pop("tool2_tech_docs", None)


# ---------------------------------------------------------------------------
# Tests for generate_docs()
# ---------------------------------------------------------------------------

class TestGenerateDocs:

    def test_happy_path_returns_three_docs(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        result = module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert set(result.keys()) == {"README.md", "ARCHITECTURE.md", "RUNBOOK.md"}

    def test_readme_content_matches_claude_response(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        result = module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["README.md"] == SYNTHETIC_README

    def test_arch_doc_content_matches_claude_response(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        result = module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["ARCHITECTURE.md"] == SYNTHETIC_ARCH_DOC

    def test_runbook_content_matches_claude_response(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        result = module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert result["RUNBOOK.md"] == SYNTHETIC_RUNBOOK

    def test_get_repo_files_called_with_correct_extensions(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        first_call_exts  = stub.get_repo_files.call_args_list[0][0][2]
        second_call_exts = stub.get_repo_files.call_args_list[1][0][2]

        assert ".py" in first_call_exts
        assert ".ts" in first_call_exts
        assert ".tf" in second_call_exts
        assert ".yaml" in second_call_exts or ".yml" in second_call_exts

    def test_get_repo_files_max_files_limits(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        first_kwargs  = stub.get_repo_files.call_args_list[0][1]
        second_kwargs = stub.get_repo_files.call_args_list[1][1]

        assert first_kwargs.get("max_files") == 15
        assert second_kwargs.get("max_files") == 10

    def test_call_claude_called_three_times(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert stub.call_claude.call_count == 3

    def test_owner_repo_included_in_claude_prompt(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        for idx, c in enumerate(stub.call_claude.call_args_list):
            user_prompt = c[0][1]
            assert "acme" in user_prompt, f"call {idx}: owner not in prompt"
            assert "myrepo" in user_prompt, f"call {idx}: repo not in prompt"

    def test_no_py_files_uses_no_files_found_placeholder(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [{}, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        result = module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = stub.call_claude.call_args_list[0][0][1]
        assert "_No files found_" in readme_prompt

    def test_no_iac_files_uses_no_files_found_placeholder(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, {}]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        arch_prompt = stub.call_claude.call_args_list[1][0][1]
        assert "_No files found_" in arch_prompt

    def test_both_empty_returns_docs_with_no_files_found(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [{}, {}]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        result = module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        assert len(result) == 3

    def test_claude_exception_propagates(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            module.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_get_repo_files_exception_propagates(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            module.generate_docs("acme", "myrepo", "https://github.com/run/1")

    def test_file_content_truncated_to_4000_chars_in_prompt(self, shared_stub):
        module, stub = shared_stub
        long_content = "x" * 10_000
        stub.get_repo_files.side_effect = [{"src/big.py": long_content}, {}]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_prompt = stub.call_claude.call_args_list[0][0][1]
        # The fmt function slices content [:4000], so prompt must not contain
        # more than 4000 x-chars in a contiguous run
        assert "x" * 4001 not in readme_prompt

    def test_correct_system_prompt_used_for_readme(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        readme_system = stub.call_claude.call_args_list[0][0][0]
        assert "README" in readme_system or "technical writer" in readme_system.lower()

    def test_correct_system_prompt_used_for_arch(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        arch_system = stub.call_claude.call_args_list[1][0][0]
        assert "architect" in arch_system.lower() or "architecture" in arch_system.lower()

    def test_correct_system_prompt_used_for_runbook(self, shared_stub):
        module, stub = shared_stub
        stub.get_repo_files.side_effect = [SYNTHETIC_PY_FILES, SYNTHETIC_IAC_FILES]
        stub.call_claude.side_effect = [SYNTHETIC_README, SYNTHETIC_ARCH_DOC, SYNTHETIC_RUNBOOK]

        module.generate_docs("acme", "myrepo", "https://github.com/run/1")

        runbook_system = stub.call_claude.call_args_list[2][0][0]
        assert "runbook" in runbook_system.lower() or "devops" in runbook_system.lower()


# ---------------------------------------------------------------------------
# Tests for build_index()
# ---------------------------------------------------------------------------

class TestBuildIndex:

    def test_returns_string(self, shared_stub):
        module, stub = shared_stub
        docs = {"README.md": SYNTHETIC_README, "ARCHITECTURE.md": SYNTHETIC_ARCH_DOC}
        result = module.build_index("acme", "myrepo", docs, FAKE_NOW)
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self, shared_stub):
        module, stub = shared_stub
        docs = {"README.md": SYNTHETIC_README}
        result = module.build_index("acme",