"""
Tests for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude integration
    - build_full_output(): markdown construction, gap questionnaire formatting, edge cases
    - __main__ block execution: env var handling, success path, failure/exception path
    - Boundary values: empty strings, missing delimiter, whitespace-only responses

Mocks used:
    - shared.call_claude          → prevents real Anthropic API calls
    - shared.get_repo_files       → prevents real GitHub API calls
    - shared.write_output_file    → prevents real file/repo writes
    - shared.send_email           → prevents real SES/SMTP calls
    - shared.email_html           → prevents template rendering side-effects
    - shared.write_audit_entry    → prevents real audit writes
    - datetime.datetime.utcnow    → deterministic timestamps
    - os.environ                  → controlled env var injection

TODOs:
    # TODO: Test the truncated __main__ exception block fully once source file is complete
    # TODO: Integration test verifying round-trip from repo files → Claude → written files
    # TODO: Test with real SYSTEM prompt formatting if project_name/version contain special chars
"""

import datetime
import importlib
import os
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers / constants
# ---------------------------------------------------------------------------

FIXED_NOW_DATE = "2024-06-15"
FIXED_NOW_DATETIME = "2024-06-15 12:00 UTC"
FIXED_UTCNOW = datetime.datetime(2024, 6, 15, 12, 0, 0)

SAMPLE_FILES = {
    "src/main.py": "def main(): pass",
    "infra/main.tf": 'resource "aws_s3_bucket" "b" {}',
    "README.md": "# My Project",
}

SAMPLE_DOC = "# Solution overview: MyProject\n**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft\n\n## Executive summary\nThis solves problems."
SAMPLE_GAPS = "1. What is the target go-live date?\n2. Who are the primary stakeholders?\n3. What are the SLA requirements?"
SAMPLE_RAW_WITH_DELIMITER = f"{SAMPLE_DOC}\n---GAPS---\n{SAMPLE_GAPS}"
SAMPLE_RAW_WITHOUT_DELIMITER = f"{SAMPLE_DOC}\nNo delimiter here."


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def stub_shared_module():
    """
    Insert a fully-mocked 'shared' module into sys.modules before the
    tool module is imported so that `from shared import ...` resolves to
    our stubs.
    """
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value=SAMPLE_RAW_WITH_DELIMITER)
    shared.get_repo_files = MagicMock(return_value=SAMPLE_FILES)
    shared.write_output_file = MagicMock(return_value="https://github.com/output/repo/blob/main/file.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "output-owner"
    shared.OUTPUT_REPO = "output-repo"
    sys.modules["shared"] = shared
    yield shared
    sys.modules.pop("shared", None)


@pytest.fixture()
def biz_docs(stub_shared_module):
    """Import (or re-import) the module under test with the stub shared in place."""
    mod_name = "tool3_business_docs"
    sys.modules.pop(mod_name, None)
    # Ensure the script directory is on path
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # We load from the actual file path
    import importlib.util
    src_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py")
    if not os.path.exists(src_path):
        # Fallback: assume tests run from repo root
        src_path = os.path.join(".github", "scripts", "tool3_business_docs.py")

    spec = importlib.util.spec_from_file_location(mod_name, src_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Utility: re-import helper
# ---------------------------------------------------------------------------

def reimport_module(stub_shared):
    """Re-import tool3_business_docs using the provided stub_shared fixture."""
    mod_name = "tool3_business_docs"
    sys.modules.pop(mod_name, None)
    src_path = os.path.join(".github", "scripts", "tool3_business_docs.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location(mod_name, src_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, biz_docs, stub_shared_module):
        """Claude returns response with ---GAPS--- delimiter — parts split correctly."""
        stub_shared_module.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER
        stub_shared_module.get_repo_files.return_value = SAMPLE_FILES

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_UTCNOW
            mock_dt.utcnow.return_value.strftime = MagicMock(return_value=FIXED_NOW_DATE)
            doc, gaps = biz_docs.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        assert "Solution overview" in doc
        assert "go-live" in gaps
        # Ensure doc does NOT contain the delimiter itself
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_delimiter(self, biz_docs, stub_shared_module):
        """Claude returns response WITHOUT ---GAPS--- — fallback gap message used."""
        stub_shared_module.call_claude.return_value = SAMPLE_RAW_WITHOUT_DELIMITER

        doc, gaps = biz_docs.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_calls_get_repo_files_with_correct_extensions(self, biz_docs, stub_shared_module):
        """get_repo_files is called with the expected file extensions."""
        biz_docs.generate_biz_doc("myowner", "myrepo", "Proj", "2.0.0", "https://run")

        stub_shared_module.get_repo_files.assert_called_once()
        args, kwargs = stub_shared_module.get_repo_files.call_args
        extensions = args[2]
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files") == 20

    def test_calls_call_claude_with_formatted_prompt(self, biz_docs, stub_shared_module):
        """call_claude receives the formatted prompt containing project_name and version."""
        biz_docs.generate_biz_doc("owner", "repo", "InsuranceApp", "3.1.4", "https://run")

        stub_shared_module.call_claude.assert_called_once()
        prompt_arg = stub_shared_module.call_claude.call_args[0][0]
        assert "InsuranceApp" in prompt_arg
        assert "3.1.4" in prompt_arg

    def test_call_claude_user_message_contains_repo_and_files(self, biz_docs, stub_shared_module):
        """The user message passed to Claude includes owner/repo and file contents."""
        stub_shared_module.get_repo_files.return_value = {"src/main.py": "print('hello')"}

        biz_docs.generate_biz_doc("acme", "widget", "Widget", "1.0.0", "https://run")

        user_msg = stub_shared_module.call_claude.call_args[0][1]
        assert "acme/widget" in user_msg
        assert "src/main.py" in user_msg
        assert "print('hello')" in user_msg

    def test_files_content_truncated_to_3000_chars(self, biz_docs, stub_shared_module):
        """File contents longer than 3000 chars are truncated in the prompt."""
        long_content = "x" * 5000
        stub_shared_module.get_repo_files.return_value = {"big_file.py": long_content}

        biz_docs.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        user_msg = stub_shared_module.call_claude.call_args[0][1]
        # The truncated content should appear; full 5000 chars should not
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files(self, biz_docs, stub_shared_module):
        """generate_biz_doc handles empty file dict gracefully."""
        stub_shared_module.get_repo_files.return_value = {}
        stub_shared_module.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        doc, gaps = biz_docs.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert doc != ""
        assert gaps != ""

    def test_delimiter_appears_multiple_times_splits_on_first(self, biz_docs, stub_shared_module):
        """If ---GAPS--- appears more than once, split on the first occurrence."""
        raw = f"Doc content\n---GAPS---\nFirst gaps\n---GAPS---\nExtra content"
        stub_shared_module.call_claude.return_value = raw

        doc, gaps = biz_docs.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert doc.strip() == "Doc content"
        assert "First gaps" in gaps
        assert "Extra content" in gaps  # everything after first delimiter goes into gaps

    def test_whitespace_only_gaps_after_delimiter(self, biz_docs, stub_shared_module):
        """Whitespace-only gaps part is stripped to empty string."""
        raw = "Doc part\n---GAPS---\n   \n   "
        stub_shared_module.call_claude.return_value = raw

        doc, gaps = biz_docs.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert doc.strip() == "Doc part"
        assert gaps == ""  # strip() of whitespace

    def test_whitespace_only_doc_before_delimiter(self, biz_docs, stub_shared_module):
        """Whitespace-only doc part is stripped to empty string."""
        raw = "   \n---GAPS---\nGap questions here"
        stub_shared_module.call_claude.return_value = raw

        doc, gaps = biz_docs.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert doc == ""
        assert gaps.strip() == "Gap questions here"

    def test_returns_tuple_of_two_strings(self, biz_docs, stub_shared_module):
        """Return type is always a 2-tuple of strings."""
        result = biz_docs.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_get_repo_files_called_with_owner_and_repo(self, biz_docs, stub_shared_module):
        """get_repo_files is invoked with the correct owner and repo."""
        biz_docs.generate_biz_doc("specific-owner", "specific-repo", "P", "1.0", "u")

        args = stub_shared_module.get_repo_files.call_args[0]
        assert args[0] == "specific-owner"
        assert args[1] == "specific-repo"


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_happy_path_returns_two_strings(self, biz_docs):
        """build_full_output returns a 2-tuple of non-empty strings."""
        result = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "MyProject", "1.0.0"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str) and len(full_md) > 0
        assert isinstance(gap_only_md, str) and len(gap_only_md) > 0

    def test_full_md_contains_doc_content(self, biz_docs):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "MyProject", "1.0.0"
        )
        assert "Executive summary" in full_md

    def test_full_md_contains_gaps(self, biz_docs):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "MyProject", "1.0.0"
        )
        assert "go-live date" in full_md
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_ai_delivery_bot_footer(self, biz_docs):
        full_md, _ = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "MyProject", "1.0.0"
        )
        assert "AI Delivery Bot" in full_md
        assert "owner/repo" in full_md
        assert "1.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, biz_docs):
        _, gap_only_md = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "InsuranceApp", "2.3.1"
        )
        assert "InsuranceApp" in gap_only_md
        assert "2.3.1" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, biz_docs):
        _, gap_only_md = biz_docs.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "MyProject", "1.0.0"
        )
        assert "go-