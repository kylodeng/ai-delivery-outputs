"""
Test suite for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path, delimiter present/absent, file truncation
  - build_full_output(): happy path, output format/content, edge cases (empty doc/gaps)
  - __main__ block: environment-driven orchestration (success + failure paths)

Mocks used:
  - shared.call_claude          — prevents real Anthropic API calls
  - shared.get_repo_files       — prevents real GitHub API calls
  - shared.write_output_file    — prevents real GitHub commits
  - shared.send_email           — prevents real SMTP/SES calls
  - shared.email_html           — prevents template rendering side-effects
  - shared.write_audit_entry    — prevents real audit log writes
  - datetime.datetime.utcnow    — deterministic timestamps

TODOs:
  - TODO: Integration test with a live Claude sandbox once API keys are available in CI
  - TODO: Test for very large repo file sets (>20 files) to verify max_files truncation in get_repo_files
  - TODO: Test OUTPUT_REPO_OWNER / OUTPUT_REPO constants from shared once their source is stable
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build / import the module under test with a stubbed `shared`
# ---------------------------------------------------------------------------

FIXED_NOW_DATE = "2024-06-15"
FIXED_NOW_DATETIME = "2024-06-15 12:00 UTC"

FAKE_FILES = {
    "src/main.py": "# entry point\nprint('hello')",
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}',
    "README.md": "# My Project\nA cool project.",
}

STUB_DOC = "# Solution overview: TestProject\n\n## Executive summary\nThis solves a problem."
STUB_GAPS = "1. What is the go-live date?\n2. Who is the business sponsor?"
STUB_RAW_WITH_DELIMITER = f"{STUB_DOC}\n---GAPS---\n{STUB_GAPS}"
STUB_RAW_WITHOUT_DELIMITER = "Some plain text without the separator."


def _make_shared_stub():
    """Return a MagicMock that acts like the `shared` module."""
    shared = MagicMock(name="shared")
    shared.OUTPUT_REPO_OWNER = "acme-org"
    shared.OUTPUT_REPO = "ai-output-repo"
    shared.get_repo_files.return_value = FAKE_FILES
    shared.call_claude.return_value = STUB_RAW_WITH_DELIMITER
    shared.write_output_file.return_value = "https://github.com/acme-org/ai-output-repo/blob/main/file.md"
    shared.email_html.return_value = "<html>ok</html>"
    shared.send_email.return_value = None
    shared.write_audit_entry.return_value = None
    return shared


def _import_module(shared_stub=None):
    """
    Import (or re-import) tool3_business_docs with an injected shared stub.
    Returns the module object.
    """
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Inject stub before import so the `from shared import ...` resolves correctly
    sys.modules["shared"] = shared_stub

    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    scripts_dir = os.path.abspath(scripts_dir)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try local path for environments where the file is adjacent
    local_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

    # Fallback: load by file path
    import importlib.util
    candidate_paths = [
        os.path.join(local_dir, "tool3_business_docs.py"),
        os.path.join(os.path.dirname(__file__), "tool3_business_docs.py"),
        os.path.join(
            os.path.dirname(__file__), "..", ".github", "scripts", "tool3_business_docs.py"
        ),
    ]
    spec = None
    for path in candidate_paths:
        if os.path.exists(path):
            spec = importlib.util.spec_from_file_location(mod_name, path)
            break

    if spec is None:
        pytest.skip("tool3_business_docs.py not found — adjust path in test helper")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def mod(shared_stub):
    module, _ = _import_module(shared_stub)
    return module


@pytest.fixture()
def mod_and_shared(shared_stub):
    module, stub = _import_module(shared_stub)
    return module, stub


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------


class TestGenerateBizDoc:
    """Tests for generate_biz_doc()"""

    def test_happy_path_returns_doc_and_gaps(self, mod_and_shared):
        mod, shared = mod_and_shared
        shared.call_claude.return_value = STUB_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            doc, gaps = mod.generate_biz_doc("acme", "myrepo", "TestProject", "1.0.0", "https://run")

        assert STUB_DOC.strip() in doc
        assert STUB_GAPS.strip() in gaps

    def test_get_repo_files_called_with_correct_extensions(self, mod_and_shared):
        mod, shared = mod_and_shared

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            mod.generate_biz_doc("owner", "repo", "P", "0.1", "url")

        shared.get_repo_files.assert_called_once()
        args, kwargs = shared.get_repo_files.call_args
        extensions_arg = args[2] if len(args) > 2 else kwargs.get("extensions", args[2])
        for ext in [".py", ".tf", ".md", ".yaml"]:
            assert ext in extensions_arg

    def test_get_repo_files_max_files_is_20(self, mod_and_shared):
        mod, shared = mod_and_shared

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            mod.generate_biz_doc("owner", "repo", "P", "0.1", "url")

        _, kwargs = shared.get_repo_files.call_args
        assert kwargs.get("max_files", None) == 20 or shared.get_repo_files.call_args[0][-1] == 20

    def test_call_claude_receives_formatted_system_prompt(self, mod_and_shared):
        mod, shared = mod_and_shared

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            mod.generate_biz_doc("owner", "repo", "MyProject", "2.3.4", "url")

        prompt_arg = shared.call_claude.call_args[0][0]
        assert "MyProject" in prompt_arg
        assert "2.3.4" in prompt_arg
        assert FIXED_NOW_DATE in prompt_arg

    def test_call_claude_user_message_contains_owner_and_repo(self, mod_and_shared):
        mod, shared = mod_and_shared

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            mod.generate_biz_doc("acme", "myrepo", "P", "0.1", "url")

        user_msg = shared.call_claude.call_args[0][1]
        assert "acme/myrepo" in user_msg

    def test_user_message_contains_file_contents(self, mod_and_shared):
        mod, shared = mod_and_shared
        shared.get_repo_files.return_value = {"src/main.py": "print('hello world')"}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            mod.generate_biz_doc("o", "r", "P", "v1", "url")

        user_msg = shared.call_claude.call_args[0][1]
        assert "src/main.py" in user_msg
        assert "print('hello world')" in user_msg

    def test_file_content_truncated_at_3000_chars(self, mod_and_shared):
        mod, shared = mod_and_shared
        long_content = "x" * 5000
        shared.get_repo_files.return_value = {"big_file.py": long_content}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            mod.generate_biz_doc("o", "r", "P", "v1", "url")

        user_msg = shared.call_claude.call_args[0][1]
        # The actual content in the message must not exceed 3000 'x' chars
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_no_delimiter_in_claude_response_fallback(self, mod_and_shared):
        mod, shared = mod_and_shared
        shared.call_claude.return_value = STUB_RAW_WITHOUT_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        assert doc == STUB_RAW_WITHOUT_DELIMITER.strip()
        assert "could not extract" in gaps.lower() or "manually" in gaps.lower()

    def test_delimiter_splits_exactly_once(self, mod_and_shared):
        mod, shared = mod_and_shared
        # Multiple delimiters — only first split used
        raw = "doc part\n---GAPS---\nfirst gaps\n---GAPS---\nmore text"
        shared.call_claude.return_value = raw

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        assert doc == "doc part"
        assert "first gaps\n---GAPS---\nmore text" in gaps

    def test_empty_files_dict_still_calls_claude(self, mod_and_shared):
        mod, shared = mod_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = STUB_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        shared.call_claude.assert_called_once()
        assert doc  # non-empty

    def test_whitespace_stripped_from_doc_and_gaps(self, mod_and_shared):
        mod, shared = mod_and_shared
        shared.call_claude.return_value = "  \n  doc content  \n  ---GAPS---  \n  gap content  \n  "

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATE
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        assert doc == "doc content"
        assert gaps == "gap content"


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------


class TestBuildFullOutput:
    """Tests for build_full_output()"""

    def test_returns_tuple_of_two_strings(self, mod):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATETIME
            result = mod.build_full_output(STUB_DOC, STUB_GAPS, "acme", "repo", "Proj", "1.0.0")

        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, mod):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATETIME
            full_md, _ = mod.build_full_output(STUB_DOC, STUB_GAPS, "acme", "repo", "Proj", "1.0.0")

        assert STUB_DOC in full_md

    def test_full_md_contains_gaps_content(self, mod):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATETIME
            full_md, _ = mod.build_full_output(STUB_DOC, STUB_GAPS, "acme", "repo", "Proj", "1.0.0")

        assert STUB_GAPS in full_md

    def test_full_md_contains_gap_questionnaire_section(self, mod):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATETIME
            full_md, _ = mod.build_full_output(STUB_DOC, STUB_GAPS, "acme", "repo", "Proj", "1.0.0")

        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_auto_generated_footer(self, mod):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_NOW_DATETIME
            full_md, _ = mod.build_full_output(STUB_DOC, STUB_GAPS, "acme", "repo", "Proj", "1.0.0")

        assert "AI Delivery Bot" in full_md
        assert "acme/repo" in full_md
        assert "1.0.0" in full_md

    def test_gap_only_md_contains_project_and_version(self, mod):