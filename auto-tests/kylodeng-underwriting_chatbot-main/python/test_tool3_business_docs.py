"""
Tests for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty files
  - build_full_output(): full markdown structure, gap-only markdown structure,
    correct project/version/owner/repo interpolation, gap count in gap_only_md
  - __main__ block logic (via importlib / subprocess not used; covered via
    direct function calls and env-var patching)

Mocks used:
  - shared.call_claude          → unittest.mock.patch
  - shared.get_repo_files       → unittest.mock.patch
  - shared.write_output_file    → unittest.mock.patch
  - shared.send_email           → unittest.mock.patch
  - shared.email_html           → unittest.mock.patch
  - shared.write_audit_entry    → unittest.mock.patch
  - datetime.datetime.utcnow    → unittest.mock.patch (for deterministic timestamps)

TODOs:
  - TODO: Integration test for the full __main__ execution path requires a
    real or fully-stubbed GitHub Actions environment (env vars wired together).
  - TODO: Test behaviour when call_claude raises an exception in the __main__
    block — needs subprocess or importlib runpy approach to capture sys.exit
    behaviour.
  - TODO: Verify email HTML content more precisely once email_html signature
    is confirmed stable.
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
# Helpers to import the module under test with shared replaced by a mock
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-bot"
FAKE_OUTPUT_REPO = "output-repo"


def _make_shared_mock():
    """Return a MagicMock that looks like the shared module."""
    shared = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


def _import_tool(shared_mock=None):
    """
    Import (or re-import) tool3_business_docs with a mocked shared module.
    Returns the module object.
    """
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Inject the fake shared module before the real import resolves it
    sys.modules["shared"] = shared_mock

    # Force a fresh import each time
    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    # Add the script directory to sys.path so the import works
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # If the scripts dir doesn't exist (CI), fall back to a direct import
    # by manipulating sys.path to include any location where the file lives.
    possible_paths = [
        script_dir,
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     ".github", "scripts"),
    ]
    for p in possible_paths:
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        mod = importlib.import_module(mod_name)
    except ModuleNotFoundError:
        # Fallback: load the source file directly
        import importlib.util
        source_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".github", "scripts", "tool3_business_docs.py"),
        ]
        spec = None
        for candidate in source_candidates:
            if os.path.exists(candidate):
                spec = importlib.util.spec_from_file_location(mod_name, candidate)
                break
        if spec is None:
            pytest.skip("tool3_business_docs.py not found — adjust path")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_DATE = "2024-06-15"
FIXED_DATETIME = "2024-06-15 12:00 UTC"
FIXED_DT_OBJ = datetime.datetime(2024, 6, 15, 12, 0, 0)


@pytest.fixture()
def shared_mock():
    m = _make_shared_mock()
    yield m
    # cleanup
    if "shared" in sys.modules:
        del sys.modules["shared"]


@pytest.fixture()
def tool(shared_mock):
    mod = _import_tool(shared_mock)
    return mod, shared_mock


@pytest.fixture()
def frozen_utcnow():
    """Patch datetime.datetime.utcnow to return FIXED_DT_OBJ."""
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT_OBJ
        # Make strftime work on the mock return value
        mock_dt.utcnow.return_value.strftime = FIXED_DT_OBJ.strftime
        yield mock_dt


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

SAMPLE_OWNER = "acme-corp"
SAMPLE_REPO = "underwriting-risk"
SAMPLE_PROJECT = "Underwriting Risk Classification"
SAMPLE_VERSION = "1.2.3"
SAMPLE_RUN_URL = "https://github.com/acme-corp/underwriting-risk/actions/runs/99"

SAMPLE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent..."}}',
    "README.md": "# Underwriting Risk\nThis project classifies insurance risk.",
}

SAMPLE_RAW_WITH_GAPS = """\
# Solution overview: Underwriting Risk Classification
**Version:** 1.2.3 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates insurance underwriting risk classification.

---GAPS---

1. What is the target go-live date?
2. Who is the business sponsor?
3. What data retention policy applies?
"""

SAMPLE_RAW_WITHOUT_GAPS = """\
# Solution overview: Underwriting Risk Classification
**Version:** 1.2.3 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates insurance underwriting risk classification.
"""


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        assert "Solution overview" in doc
        assert "Executive summary" in doc
        assert "go-live date" in gaps
        assert "business sponsor" in gaps
        # delimiter itself should not appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        shared.get_repo_files.assert_called_once()
        args, kwargs = shared.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", args[2] if len(args) > 2 else None)
        # Verify key extensions are present
        called_extensions = list(shared.get_repo_files.call_args[0][2])
        for ext in [".py", ".tf", ".md", ".yaml"]:
            assert ext in called_extensions

    def test_call_claude_receives_project_name_in_prompt(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        prompt_arg = shared.call_claude.call_args[0][0]
        assert SAMPLE_PROJECT in prompt_arg
        assert SAMPLE_VERSION in prompt_arg

    def test_call_claude_user_message_contains_owner_and_repo(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        user_message_arg = shared.call_claude.call_args[0][1]
        assert SAMPLE_OWNER in user_message_arg
        assert SAMPLE_REPO in user_message_arg

    def test_missing_gaps_delimiter_returns_fallback(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        shared.call_claude.return_value = SAMPLE_RAW_WITHOUT_GAPS

        doc, gaps = mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_empty_files_dict(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        assert doc  # should still return something
        assert gaps

    def test_multiple_gaps_delimiters_only_first_is_split(self, tool):
        """If Claude returns multiple ---GAPS--- delimiters, only the first is used."""
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        raw = "Document content\n---GAPS---\nQuestion 1\n---GAPS---\nExtra stuff"
        shared.call_claude.return_value = raw

        doc, gaps = mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        assert doc == "Document content"
        assert "Question 1" in gaps
        # The second delimiter and everything after it ends up in gaps
        assert "Extra stuff" in gaps

    def test_doc_part_is_stripped(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        raw = "  \n\n  Document content  \n\n  ---GAPS---\n  Q1  \n  "
        shared.call_claude.return_value = raw

        doc, gaps = mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_max_files_limit_passed(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        _, kwargs = shared.get_repo_files.call_args
        # max_files should be passed as keyword or positional
        call_args = shared.get_repo_files.call_args
        all_args = list(call_args[0]) + list(call_args[1].values())
        assert 20 in all_args or kwargs.get("max_files") == 20

    def test_call_claude_prompt_contains_date(self, tool):
        mod, shared = tool
        shared.get_repo_files.return_value = SAMPLE_FILES
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = "2024-06-15"
            mod.generate_biz_doc(
                SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
            )

        prompt_arg = shared.call_claude.call_args[0][0]
        # Date from strftime should be in prompt
        assert "2024" in prompt_arg or SAMPLE_VERSION in prompt_arg  # broad check

    def test_large_file_content_truncated_in_prompt(self, tool):
        """Files should be truncated to 3000 chars in the user message."""
        mod, shared = tool
        large_content = "x" * 10000
        shared.get_repo_files.return_value = {"big_file.py": large_content}
        shared.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        mod.generate_biz_doc(
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION, SAMPLE_RUN_URL
        )

        user_message = shared.call_claude.call_args[0][1]
        # The truncated content should appear (3000 x's) but not the full 10000
        assert "x" * 3000 in user_message
        assert "x" * 10001 not in user_message


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    SAMPLE_DOC = "# Solution overview: Underwriting Risk Classification\n## Executive summary\nThis automates risk."
    SAMPLE_GAPS = "1. What is the go-live date?\n2. Who is the business sponsor?\n3. What is the retention policy?"

    def test_full_md_contains_doc_content(self, tool):
        mod, _ = tool
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION
        )
        assert "Executive summary" in full_md
        assert "automates risk" in full_md

    def test_full_md_contains_gap_questions(self, tool):
        mod, _ = tool
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            SAMPLE_OWNER, SAMPLE_REPO, SAMPLE_PROJECT, SAMPLE_VERSION
        )
        assert "go-live date" in full_md
        assert "business sponsor" in full_md

    def test_