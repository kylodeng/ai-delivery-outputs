"""
Test module for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty file list
  - build_full_output(): happy path, empty gaps, special characters in inputs,
    output content assertions, tuple return type
  - __main__ block behaviour (env-var driven orchestration): happy path, exception path

Mocks used:
  - shared.call_claude         → unittest.mock.patch
  - shared.get_repo_files      → unittest.mock.patch
  - shared.write_output_file   → unittest.mock.patch
  - shared.send_email          → unittest.mock.patch
  - shared.email_html          → unittest.mock.patch
  - shared.write_audit_entry   → unittest.mock.patch
  - datetime.datetime.utcnow   → unittest.mock.patch (frozen time)

TODOs:
  - TODO: Integration test against a real Claude API key (requires secret injection)
  - TODO: Test write_output_file and send_email with real SMTP/GitHub credentials
  - TODO: Verify audit entry schema matches downstream consumers
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal stub for the `shared` module so the import of
# tool3_business_docs succeeds without the real shared.py on sys.path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-org"
FAKE_OUTPUT_REPO = "ai-output-repo"


def _make_shared_stub():
    """Return a types.ModuleType that satisfies all names imported by tool3."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="Doc content\n---GAPS---\n1. Question one?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/ai-org/ai-output-repo/blob/main/file.md")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


# Install the stub BEFORE importing the module under test.
_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

# Now import the module under test.
import importlib.util, pathlib

_MODULE_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool3_business_docs.py"


def _load_module():
    """Load tool3_business_docs fresh each time (avoids state leakage)."""
    spec = importlib.util.spec_from_file_location("tool3_business_docs", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Make sure our shared stub is visible during exec
    mod.__dict__["__builtins__"] = __builtins__
    sys.modules["shared"] = _shared_stub
    spec.loader.exec_module(mod)
    return mod


# Load once for most tests; reload inside tests that need a clean state.
t3 = _load_module()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FROZEN_NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mock call counts before each test."""
    for attr in ("call_claude", "get_repo_files", "write_output_file",
                 "send_email", "email_html", "write_audit_entry"):
        getattr(_shared_stub, attr).reset_mock()
    yield


@pytest.fixture()
def frozen_datetime():
    """Patch datetime.datetime so utcnow() is deterministic."""
    with patch(f"tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FROZEN_NOW
        mock_dt.datetime.utcnow.return_value.strftime = FROZEN_NOW.strftime
        # Make strftime work via the real frozen object
        mock_dt.datetime.utcnow = lambda: FROZEN_NOW
        yield mock_dt


# ---------------------------------------------------------------------------
# generate_biz_doc – happy path
# ---------------------------------------------------------------------------

class TestGenerateBizDocHappyPath:

    def test_returns_tuple_of_two_strings(self):
        _shared_stub.get_repo_files.return_value = {"app.py": "print('hello')"}
        _shared_stub.call_claude.return_value = "Solution Overview\n---GAPS---\n1. Who is the owner?"

        doc, gaps = t3.generate_biz_doc("acme", "my-repo", "My Project", "1.2.0", "https://run.url")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_doc_part_is_text_before_delimiter(self):
        _shared_stub.get_repo_files.return_value = {"main.tf": "resource {}"}
        _shared_stub.call_claude.return_value = "BEFORE\n---GAPS---\nAFTER"

        doc, gaps = t3.generate_biz_doc("acme", "repo", "P", "v1", "https://u")

        assert doc == "BEFORE"
        assert gaps == "AFTER"

    def test_gaps_part_is_text_after_delimiter(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "Doc\n---GAPS---\n1. Q1?\n2. Q2?"

        _, gaps = t3.generate_biz_doc("o", "r", "P", "v1", "u")

        assert "Q1?" in gaps
        assert "Q2?" in gaps

    def test_strips_whitespace_from_both_parts(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "  Doc  \n---GAPS---\n  Gaps  "

        doc, gaps = t3.generate_biz_doc("o", "r", "P", "v1", "u")

        assert doc == "Doc"
        assert gaps == "Gaps"

    def test_calls_get_repo_files_with_expected_extensions(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("owner", "repo", "P", "v2", "url")

        _shared_stub.get_repo_files.assert_called_once()
        args, kwargs = _shared_stub.get_repo_files.call_args
        # first two positional args are owner, repo
        assert args[0] == "owner"
        assert args[1] == "repo"
        # extensions list must include at least py and md
        exts = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in exts
        assert ".md" in exts

    def test_calls_call_claude_once(self):
        _shared_stub.get_repo_files.return_value = {"f.py": "x"}
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert _shared_stub.call_claude.call_count == 1

    def test_prompt_contains_project_name(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("o", "r", "InsurancePortal", "1.0", "u")

        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg

    def test_prompt_contains_version(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("o", "r", "P", "3.7.1", "u")

        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert "3.7.1" in prompt_arg

    def test_user_message_contains_owner_and_repo(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("sun-life", "generations-ii", "P", "1.0", "u")

        user_msg = _shared_stub.call_claude.call_args[0][1]
        assert "sun-life" in user_msg
        assert "generations-ii" in user_msg

    def test_multiple_files_concatenated_in_user_message(self):
        _shared_stub.get_repo_files.return_value = {
            "app.py": "print('a')",
            "infra.tf": "resource {}",
        }
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("o", "r", "P", "1", "u")

        user_msg = _shared_stub.call_claude.call_args[0][1]
        assert "app.py" in user_msg
        assert "infra.tf" in user_msg


# ---------------------------------------------------------------------------
# generate_biz_doc – missing delimiter
# ---------------------------------------------------------------------------

class TestGenerateBizDocMissingDelimiter:

    def test_returns_full_response_as_doc_when_no_delimiter(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "Only a document, no separator here."

        doc, gaps = t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == "Only a document, no separator here."

    def test_returns_fallback_message_as_gaps_when_no_delimiter(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "No delimiter here"

        _, gaps = t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert "manually" in gaps.lower() or "could not" in gaps.lower()

    def test_empty_claude_response_handled(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = ""

        doc, gaps = t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_delimiter_at_start_gives_empty_doc(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "---GAPS---\n1. Only gaps here"

        doc, gaps = t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == ""
        assert "Only gaps here" in gaps

    def test_delimiter_at_end_gives_empty_gaps(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "Full doc text\n---GAPS---"

        doc, gaps = t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == "Full doc text"
        assert gaps == ""

    def test_multiple_delimiters_only_first_split_used(self):
        """split(..., 1) means only the first delimiter is honoured."""
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "Part A\n---GAPS---\nPart B\n---GAPS---\nPart C"

        doc, gaps = t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == "Part A"
        assert "Part B" in gaps
        assert "Part C" in gaps


# ---------------------------------------------------------------------------
# generate_biz_doc – edge cases with file contents
# ---------------------------------------------------------------------------

class TestGenerateBizDocFileEdgeCases:

    def test_empty_file_dict_still_calls_claude(self):
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert _shared_stub.call_claude.call_count == 1

    def test_file_content_truncated_to_3000_chars(self):
        long_content = "x" * 5000
        _shared_stub.get_repo_files.return_value = {"big.py": long_content}
        _shared_stub.call_claude.return_value = "d---GAPS---g"

        t3.generate_biz_doc("o", "r", "P", "1.0", "u")

        user_msg = _shared_stub.call_claude.call_args[0][1]
        # The 3000-char slice means the user message must NOT contain 5000 x's
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_insurance_product_file_included_in_prompt(self):
        """Synthetic data: insurance product files should be processed normally."""
        _shared_stub.get_repo_files.return_value = {
            "Generations-II_PB_EN.pdf.annot.json": '{"product_name": "Generations II"}',
            "List_of_hospitals.pdf.annot.json": '{"product_name": "Hospital List"}',
        }
        _shared_stub.call_claude.return_value = "Overview\n---GAPS---\n1. Who owns this?"

        doc, gaps = t3.generate_biz_doc("sun-life", "insurance-docs", "InsurancePlatform", "2.0", "u")

        user_msg = _shared_stub.call_claude.call_args[0][1]
        assert "Generations-II_PB_EN.pdf.annot.json" in user_msg
        assert "InsurancePlatform" in _shared_stub.call_claude.call_args[0][0]


# ---------------------------------------------------------------------------
# build_full_output – happy path
# ---------------------------------------------------------------------------

class TestBuildFullOutputHappyPath:

    def test_returns_tuple_of_two_strings(self):
        result = t3.build_full_output("Doc", "1. Q?", "o", "r", "Project", "1.0")
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains