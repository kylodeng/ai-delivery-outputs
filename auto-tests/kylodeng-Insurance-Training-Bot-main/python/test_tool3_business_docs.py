"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty file list
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content checks
    - __main__ block logic: env-driven execution, success path, exception/failure path

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (for deterministic timestamps)

TODOs:
    - TODO: Integration test against a real Claude API key (requires ANTHROPIC_API_KEY secret)
    - TODO: Test write_output_file error propagation (needs shared module internals)
    - TODO: Test send_email error propagation separately
"""

import importlib
import sys
import os
import types
from unittest.mock import patch, MagicMock, call
import pytest
import datetime


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_NOW_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FAKE_NOW_DATE = "2024-06-15"
FAKE_NOW_DATETIME = "2024-06-15 12:00 UTC"

FAKE_DOC_CONTENT = """# Solution overview: MyProject
**Version:** 1.2.3 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates document processing.

## Business context
**Problem statement:** Manual data entry was slow.
"""

FAKE_GAPS_CONTENT = """1. Who is the business sponsor?
2. What is the target go-live date?
3. What SLA is expected for uptime?
"""

FAKE_RAW_RESPONSE_WITH_DELIMITER = f"{FAKE_DOC_CONTENT}---GAPS---{FAKE_GAPS_CONTENT}"
FAKE_RAW_RESPONSE_WITHOUT_DELIMITER = FAKE_DOC_CONTENT  # no delimiter

FAKE_FILES = {
    "main.py": "def main(): pass",
    "config.yaml": "env: production",
}

OWNER = "acme-corp"
REPO = "my-repo"
PROJECT_NAME = "MyProject"
VERSION = "1.2.3"
RUN_URL = "https://github.com/actions/runs/999"
FAKE_DOC_URL = "https://github.com/output-owner/output-repo/blob/main/business-docs/acme-corp-my-repo/solution-overview-v1.2.3.md"


# ---------------------------------------------------------------------------
# Shared-module stub so we can import tool3 without the real 'shared' package
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def stub_shared_module(monkeypatch):
    """
    Create a minimal fake 'shared' module and inject it into sys.modules
    before each test so that importing tool3_business_docs works even when
    the real shared module is absent or has network side-effects.
    """
    fake_shared = types.ModuleType("shared")
    fake_shared.call_claude = MagicMock(return_value=FAKE_RAW_RESPONSE_WITH_DELIMITER)
    fake_shared.get_repo_files = MagicMock(return_value=FAKE_FILES)
    fake_shared.write_output_file = MagicMock(return_value=FAKE_DOC_URL)
    fake_shared.send_email = MagicMock()
    fake_shared.email_html = MagicMock(return_value="<html>email</html>")
    fake_shared.write_audit_entry = MagicMock()
    fake_shared.OUTPUT_REPO_OWNER = "output-owner"
    fake_shared.OUTPUT_REPO = "output-repo"

    monkeypatch.setitem(sys.modules, "shared", fake_shared)

    # Remove cached tool3 module so each test gets a clean import
    monkeypatch.delitem(sys.modules, "tool3_business_docs", raising=False)

    yield fake_shared


@pytest.fixture()
def tool3(stub_shared_module):
    """Import (or re-import) the module under test after the stub is in place."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also allow direct path resolution when running from repo root
    alt_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    import tool3_business_docs as t3
    return t3


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Patch datetime.datetime so utcnow() returns FAKE_NOW_DT."""
    fake_dt = MagicMock(wraps=datetime.datetime)
    fake_dt.utcnow.return_value = FAKE_NOW_DT
    monkeypatch.setattr("tool3_business_docs.datetime.datetime", fake_dt)
    return fake_dt


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_splits_on_delimiter(self, tool3, stub_shared_module):
        """Claude returns a response with ---GAPS--- delimiter → split correctly."""
        stub_shared_module.call_claude.return_value = FAKE_RAW_RESPONSE_WITH_DELIMITER

        doc, gaps = tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert "# Solution overview" in doc
        assert "1. Who is the business sponsor?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_delimiter_returns_full_response_as_doc(self, tool3, stub_shared_module):
        """Claude returns a response without ---GAPS--- → fallback message in gaps."""
        stub_shared_module.call_claude.return_value = FAKE_RAW_RESPONSE_WITHOUT_DELIMITER

        doc, gaps = tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, stub_shared_module):
        """get_repo_files must be called with the expected extension list."""
        tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        stub_shared_module.get_repo_files.assert_called_once()
        args, kwargs = stub_shared_module.get_repo_files.call_args
        extensions_arg = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        for ext in [".py", ".tf", ".md", ".yaml"]:
            assert ext in extensions_arg

    def test_get_repo_files_max_files_is_20(self, tool3, stub_shared_module):
        """max_files should be 20."""
        tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        _, kwargs = stub_shared_module.get_repo_files.call_args
        assert kwargs.get("max_files", None) == 20 or (
            len(stub_shared_module.get_repo_files.call_args[0]) > 3 and
            stub_shared_module.get_repo_files.call_args[0][3] == 20
        )

    def test_call_claude_prompt_contains_project_name(self, tool3, stub_shared_module):
        """The formatted prompt passed to Claude must include the project name."""
        tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        prompt_arg = stub_shared_module.call_claude.call_args[0][0]
        assert PROJECT_NAME in prompt_arg

    def test_call_claude_prompt_contains_version(self, tool3, stub_shared_module):
        """The formatted prompt passed to Claude must include the version string."""
        tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        prompt_arg = stub_shared_module.call_claude.call_args[0][0]
        assert VERSION in prompt_arg

    def test_call_claude_user_message_contains_owner_and_repo(self, tool3, stub_shared_module):
        """The user-facing message to Claude should reference owner/repo."""
        tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_msg = stub_shared_module.call_claude.call_args[0][1]
        assert OWNER in user_msg
        assert REPO in user_msg

    def test_empty_file_dict_still_calls_claude(self, tool3, stub_shared_module):
        """Even if get_repo_files returns {}, Claude should still be called."""
        stub_shared_module.get_repo_files.return_value = {}

        doc, gaps = tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        stub_shared_module.call_claude.assert_called_once()
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_file_content_truncated_to_3000_chars(self, tool3, stub_shared_module):
        """File content longer than 3000 chars should be truncated in the prompt."""
        long_content = "x" * 5000
        stub_shared_module.get_repo_files.return_value = {"big_file.py": long_content}

        tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_msg = stub_shared_module.call_claude.call_args[0][1]
        # Should not contain the full 5000 chars; truncated to 3000
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_delimiter_only_splits_on_first_occurrence(self, tool3, stub_shared_module):
        """If ---GAPS--- appears multiple times only the first split is used."""
        stub_shared_module.call_claude.return_value = (
            "DOC_PART---GAPS---GAPS_PART_A---GAPS---GAPS_PART_B"
        )

        doc, gaps = tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc.strip() == "DOC_PART"
        assert "GAPS_PART_A---GAPS---GAPS_PART_B" in gaps

    def test_doc_and_gaps_are_stripped(self, tool3, stub_shared_module):
        """Leading/trailing whitespace should be stripped from both parts."""
        stub_shared_module.call_claude.return_value = "  DOC  ---GAPS---  GAPS  "

        doc, gaps = tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == "DOC"
        assert gaps == "GAPS"

    def test_returns_tuple_of_two_strings(self, tool3, stub_shared_module):
        doc, gaps = tool3.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_full_md_contains_doc(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert FAKE_DOC_CONTENT.strip() in full_md

    def test_full_md_contains_gaps(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert FAKE_GAPS_CONTENT.strip() in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_reference(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert f"{OWNER}/{REPO}" in full_md
        assert VERSION in full_md

    def test_full_md_contains_timestamp(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert FAKE_NOW_DATETIME in full_md

    def test_gap_only_md_contains_project_name(self, tool3, frozen_datetime):
        _, gap_only = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert PROJECT_NAME in gap_only

    def test_gap_only_md_contains_version(self, tool3, frozen_datetime):
        _, gap_only = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert VERSION in gap_only

    def test_gap_only_md_contains_gap_questions(self, tool3, frozen_datetime):
        _, gap_only = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert FAKE_GAPS_CONTENT.strip() in gap_only

    def test_gap_only_md_contains_output_repo_link(self, tool3, frozen_datetime):
        _, gap_only = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "output-owner" in gap_only
        assert "output-repo" in gap_only

    def test_gap_only_md_contains_timestamp(self, tool3, frozen_datetime):
        _, gap_only = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert FAKE_NOW_DATETIME in gap_only

    def test_returns_tuple_of_two_strings(self, tool3, frozen_datetime):
        result = tool3.build_full_output(
            FAKE_DOC_CONTENT, FAKE_GAPS_CONTENT,
            OWNER, REPO, PROJECT_NAME, VERSION
        