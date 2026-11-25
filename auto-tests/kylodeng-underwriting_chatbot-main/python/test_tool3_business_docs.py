"""
Test suite for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path with/without ---GAPS--- delimiter, edge cases
  - build_full_output(): markdown structure, content correctness, edge cases
  - __main__ block behaviour (env vars, success path, exception/failure path)

Mocks used:
  - shared.call_claude          → patched to return synthetic responses
  - shared.get_repo_files       → patched to return synthetic file dict
  - shared.write_output_file    → patched to return a fake URL
  - shared.send_email           → patched (no-op)
  - shared.email_html           → patched to return a dummy string
  - shared.write_audit_entry    → patched (no-op)
  - datetime.datetime.utcnow    → patched for deterministic timestamps

TODOs:
  - TODO: Integration test with a real Claude API key (requires ANTHROPIC_API_KEY secret)
  - TODO: Test actual __main__ subprocess execution end-to-end
  - TODO: Verify email HTML structure produced by real email_html helper
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_NOW_STR = "2024-06-15"
FAKE_NOW_FULL = "2024-06-15 12:00 UTC"
FAKE_DOC_URL = "https://github.com/output-owner/output-repo/blob/main/business-docs/owner-repo/solution-overview-v1.2.3.md"

SYNTHETIC_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent..."}}',
    "frontend/.chainlit/translations/ar-SA.json": '{"common": {"actions": {"cancel": "إلغاء"}}}',
}

CLAUDE_RESPONSE_WITH_GAPS = (
    "# Solution overview: MyProject\n\n## Executive summary\nThis solves things.\n\n"
    "---GAPS---\n"
    "1. What is the target go-live date?\n2. Who is the solution owner?\n3. What are the success metrics?"
)

CLAUDE_RESPONSE_WITHOUT_GAPS = (
    "# Solution overview: MyProject\n\n## Executive summary\nThis solves things but no delimiter here."
)

CLAUDE_RESPONSE_EMPTY = ""

CLAUDE_RESPONSE_MULTIPLE_GAPS_DELIMITERS = (
    "# Solution overview: MyProject\n\n## Executive summary\nSolves stuff.\n\n"
    "---GAPS---\n"
    "1. Question one?\n"
    "---GAPS---\n"
    "2. Question two? (should not appear in doc_part via split maxsplit=1)"
)


def _make_shared_module():
    """Create a minimal fake 'shared' module so the import in tool3 doesn't fail."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value=CLAUDE_RESPONSE_WITH_GAPS)
    shared.get_repo_files = MagicMock(return_value=SYNTHETIC_FILES)
    shared.write_output_file = MagicMock(return_value=FAKE_DOC_URL)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "output-owner"
    shared.OUTPUT_REPO = "output-repo"
    return shared


@pytest.fixture(autouse=True)
def inject_shared(monkeypatch):
    """Inject a fake 'shared' module before every test and reload tool3."""
    fake_shared = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", fake_shared)
    yield fake_shared


@pytest.fixture()
def tool3(inject_shared):
    """Import (or reload) the module under test after shared is injected."""
    # Remove cached version so each test gets a fresh import
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]

    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try the directory relative to this test file
    alt_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    # Patch sys.path so the module can find 'shared' via its own sys.path.insert
    with patch("sys.path", sys.path):
        import tool3_business_docs as t3
    return t3


@pytest.fixture()
def fixed_datetime():
    """Freeze datetime.datetime.utcnow() to a known value."""
    fixed = datetime.datetime(2024, 6, 15, 12, 0, 0)
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = fixed
        mock_dt.datetime.utcnow.return_value.strftime = fixed.strftime
        # Make strftime work properly on the mock
        mock_dt.datetime.utcnow = lambda: fixed
        yield fixed


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, tool3, inject_shared):
        """Claude returns a response with ---GAPS--- → splits correctly."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS
        inject_shared.get_repo_files.return_value = SYNTHETIC_FILES

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run.url")

        assert "# Solution overview: MyProject" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the target go-live date?" in gaps
        assert "2. Who is the solution owner?" in gaps
        assert "3. What are the success metrics?" in gaps

    def test_response_without_gaps_delimiter(self, tool3, inject_shared):
        """Claude returns a response without ---GAPS--- → fallback message used."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITHOUT_GAPS

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run.url")

        assert "Executive summary" in doc
        assert gaps == "_Claude could not extract gap questions — review the document manually._"

    def test_response_empty_string(self, tool3, inject_shared):
        """Claude returns empty string → no crash, fallback for gaps."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_EMPTY

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run.url")

        assert doc == ""
        assert gaps == "_Claude could not extract gap questions — review the document manually._"

    def test_only_delimiter_returned(self, tool3, inject_shared):
        """Claude returns only the delimiter."""
        inject_shared.call_claude.return_value = "---GAPS---"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run.url")

        assert doc == ""
        assert gaps == ""

    def test_multiple_gaps_delimiters_only_first_used(self, tool3, inject_shared):
        """split with maxsplit=1 means only the first delimiter matters."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_MULTIPLE_GAPS_DELIMITERS

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run.url")

        # Second ---GAPS--- should end up inside gaps, not doc
        assert "---GAPS---" not in doc
        assert "---GAPS---" in gaps  # the second one lands in gaps_part
        assert "1. Question one?" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, inject_shared):
        """Verify get_repo_files is called with the correct file extensions and max_files."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        tool3.generate_biz_doc("acme", "widget-service", "Widget", "2.0.0", "https://run")

        inject_shared.get_repo_files.assert_called_once()
        args, kwargs = inject_shared.get_repo_files.call_args
        assert args[0] == "acme"
        assert args[1] == "widget-service"
        assert ".py" in args[2]
        assert ".tf" in args[2]
        assert ".md" in args[2]
        assert kwargs.get("max_files") == 20

    def test_call_claude_receives_project_and_version(self, tool3, inject_shared):
        """The prompt passed to call_claude must include project_name and version."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        tool3.generate_biz_doc("owner", "repo", "InsuranceBot", "3.1.4", "https://run")

        args, _ = inject_shared.call_claude.call_args
        prompt = args[0]
        assert "InsuranceBot" in prompt
        assert "3.1.4" in prompt

    def test_call_claude_user_message_contains_repo_files(self, tool3, inject_shared):
        """The user message passed to call_claude should contain file content."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS
        inject_shared.get_repo_files.return_value = {"app.py": "print('hello')"}

        tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        _, user_msg = inject_shared.call_claude.call_args[0]
        assert "app.py" in user_msg
        assert "print('hello')" in user_msg

    def test_empty_repo_files(self, tool3, inject_shared):
        """Empty file dict → no crash, call_claude still called."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS
        inject_shared.get_repo_files.return_value = {}

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        inject_shared.call_claude.assert_called_once()
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_file_content_truncated_to_3000_chars(self, tool3, inject_shared):
        """Files with >3000 chars should be sliced to 3000 in the prompt."""
        long_content = "x" * 5000
        inject_shared.get_repo_files.return_value = {"big_file.py": long_content}
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        tool3.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        _, user_msg = inject_shared.call_claude.call_args[0]
        # The truncated content should appear, not 5000 x's
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_whitespace_stripped_from_doc_and_gaps(self, tool3, inject_shared):
        """Surrounding whitespace should be stripped from both parts."""
        inject_shared.call_claude.return_value = "  \n doc content \n  ---GAPS---  \n  gap content \n  "

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "P", "v1", "url")

        assert doc == "doc content"
        assert gaps == "gap content"

    def test_call_claude_propagates_exception(self, tool3, inject_shared):
        """If call_claude raises, generate_biz_doc should propagate the exception."""
        inject_shared.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            tool3.generate_biz_doc("owner", "repo", "P", "v1", "url")

    def test_get_repo_files_propagates_exception(self, tool3, inject_shared):
        """If get_repo_files raises, generate_biz_doc should propagate."""
        inject_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool3.generate_biz_doc("owner", "repo", "P", "v1", "url")

    @pytest.mark.parametrize("project_name,version", [
        ("Underwriting Risk Classification", "0.1.0"),
        ("InsuranceBot", "2.5.1"),
        ("", "1.0.0"),
        ("My Project", ""),
    ])
    def test_parameterised_project_and_version(self, tool3, inject_shared, project_name, version):
        """Various project name / version combinations should not cause crashes."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        doc, gaps = tool3.generate_biz_doc("owner", "repo", project_name, version, "url")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_date_in_prompt_is_utc_today(self, tool3, inject_shared):
        """The date injected into the prompt must be today's UTC date in YYYY-MM-DD."""
        inject_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        with patch("tool3_business_docs.datetime") as mock_dt:
            fixed = datetime.datetime(2024, 6, 15, 12, 0, 0)
            mock_dt.datetime.utcnow.return_value = fixed

            tool3.generate_biz_doc("owner", "repo", "P", "v1", "url")

        args, _ = inject_shared.call_claude.call_args
        prompt = args[0]
        assert "2024-06-15" in prompt


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    DOC = "# Solution overview\n\n## Executive summary\nSolves things."
    GAPS = "1. What is the go-live date?\n2. Who is the solution owner?"

    def _freeze_datetime(self):
        fixed = datetime.datetime(2024, 6, 15, 12, 0, 0)
        return patch("tool3_business_docs.datetime",
                     **{"datetime.utcnow.return_value": fixed})

    def test_happy_path_full_md_contains_doc(self, tool3):
        """full_md should contain the original doc content."""
        full_md, _ = tool3.build_full_output(
            self.DOC, self.GAPS, "owner", "repo", "MyProject", "1