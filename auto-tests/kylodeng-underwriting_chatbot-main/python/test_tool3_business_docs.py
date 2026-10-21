"""
Test suite for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty files, Claude errors
- build_full_output(): correct markdown assembly, delimiter handling, both returned strings
- Main block (__main__) execution paths: success and failure branches

Mocks used:
- shared.call_claude (patched via sys.modules injection)
- shared.get_repo_files
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- shared.OUTPUT_REPO_OWNER / OUTPUT_REPO constants
- datetime.datetime.utcnow (frozen for deterministic output)

TODOs:
- TODO: Integration test against a real Claude API endpoint (requires API key + network)
- TODO: Test __main__ block with real subprocess execution if CLI contract is stabilised
- TODO: Verify email HTML structure once email_html template is finalised
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
# Helpers: build a fake `shared` module so tool3 can be imported in isolation
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "output-repo"


def _make_shared_module():
    """Return a fake `shared` module with all symbols tool3 imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="Doc content\n---GAPS---\n1. Question one?")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/output-repo/file.md")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject fake shared module before each test and reload tool3."""
    shared_mod = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)
    yield shared_mod


@pytest.fixture()
def tool3(fake_shared):
    """Import (or re-import) tool3_business_docs with mocked shared."""
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Force a fresh import so the module picks up the patched sys.modules
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]

    import tool3_business_docs as t3
    return t3


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DATE = "2024-06-15"
FROZEN_DATETIME = "2024-06-15 12:00 UTC"


class _FrozenDatetime(datetime.datetime):
    @classmethod
    def utcnow(cls):
        return datetime.datetime(2024, 6, 15, 12, 0, 0)


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, tool3, fake_shared):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        fake_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "README.md": "# My project",
        }
        fake_shared.call_claude.return_value = (
            "# Solution overview: TestProject\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the business sponsor?"
        )

        doc, gaps = tool3.generate_biz_doc("acme", "my-repo", "TestProject", "1.0.0", "https://ci.example.com")

        assert "# Solution overview: TestProject" in doc
        assert "1. What is the go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_gaps_delimiter_falls_back_gracefully(self, tool3, fake_shared):
        """When Claude omits ---GAPS---, the whole response goes to doc and gaps gets a fallback."""
        fake_shared.get_repo_files.return_value = {"README.md": "# Project"}
        fake_shared.call_claude.return_value = "# Solution overview\nNo gaps section here."

        doc, gaps = tool3.generate_biz_doc("acme", "my-repo", "TestProject", "1.0.0", "https://ci.example.com")

        assert "# Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_empty_repo_files(self, tool3, fake_shared):
        """Should still call Claude even when repo has no files."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\n1. Question?"

        doc, gaps = tool3.generate_biz_doc("acme", "empty-repo", "EmptyProject", "0.1.0", "https://ci.example.com")

        fake_shared.call_claude.assert_called_once()
        assert doc == "Doc"
        assert gaps == "1. Question?"

    def test_claude_called_with_correct_repo_context(self, tool3, fake_shared):
        """The user message passed to Claude should reference the owner/repo."""
        fake_shared.get_repo_files.return_value = {"main.py": "print('hello')"}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\n1. Q?"

        tool3.generate_biz_doc("my-org", "cool-repo", "CoolProject", "2.0.0", "https://ci.example.com")

        _, user_msg = fake_shared.call_claude.call_args[0]
        assert "my-org/cool-repo" in user_msg

    def test_prompt_contains_project_name_version_date(self, tool3, fake_shared):
        """System prompt passed to Claude should be formatted with project metadata."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\n1. Q?"

        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 6, 15, 12, 0, 0)
            mock_dt.datetime.utcnow.return_value.strftime = lambda fmt: "2024-06-15"
            # Use real strftime
            mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 6, 15, 12, 0, 0)

            tool3.generate_biz_doc("org", "repo", "MyProject", "3.1.4", "https://ci.example.com")

        system_prompt, _ = fake_shared.call_claude.call_args[0]
        assert "MyProject" in system_prompt
        assert "3.1.4" in system_prompt

    def test_multiple_gaps_delimiters_only_first_split_used(self, tool3, fake_shared):
        """If Claude includes ---GAPS--- more than once, only the first split matters."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = (
            "Doc part\n---GAPS---\nQuestion 1\n---GAPS---\nExtra stuff"
        )

        doc, gaps = tool3.generate_biz_doc("org", "repo", "P", "1.0", "https://ci.example.com")

        assert doc == "Doc part"
        # Everything after first delimiter goes to gaps
        assert "Question 1" in gaps

    def test_file_content_truncated_to_3000_chars(self, tool3, fake_shared):
        """Each file's content should be capped at 3000 characters in the Claude prompt."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\n1. Q?"

        tool3.generate_biz_doc("org", "repo", "P", "1.0", "https://ci.example.com")

        _, user_msg = fake_shared.call_claude.call_args[0]
        # The truncated content (3000 x's) should appear, but not the full 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_claude_raises_exception_propagates(self, tool3, fake_shared):
        """Exceptions from call_claude should propagate out of generate_biz_doc."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            tool3.generate_biz_doc("org", "repo", "P", "1.0", "https://ci.example.com")

    def test_get_repo_files_called_with_correct_extensions(self, tool3, fake_shared):
        """get_repo_files should be asked for the specific set of extensions."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\n1. Q?"

        tool3.generate_biz_doc("org", "repo", "P", "1.0", "https://ci.example.com")

        args, kwargs = fake_shared.get_repo_files.call_args
        extensions_arg = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions_arg
        assert ".tf" in extensions_arg
        assert ".md" in extensions_arg
        assert kwargs.get("max_files") == 20 or (len(args) > 3 and args[3] == 20)

    def test_gaps_stripped_of_whitespace(self, tool3, fake_shared):
        """Leading/trailing whitespace should be stripped from both doc and gaps."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "  Doc content  \n---GAPS---\n  Gap content  "

        doc, gaps = tool3.generate_biz_doc("org", "repo", "P", "1.0", "https://ci.example.com")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_empty_claude_response(self, tool3, fake_shared):
        """Empty string from Claude should not crash; goes to doc with fallback gaps."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = ""

        doc, gaps = tool3.generate_biz_doc("org", "repo", "P", "1.0", "https://ci.example.com")

        assert isinstance(doc, str)
        assert "Claude could not extract gap questions" in gaps


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================


class TestBuildFullOutput:

    @pytest.fixture()
    def frozen_tool3(self, tool3):
        """Provide tool3 with a frozen datetime."""
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = datetime.datetime(2024, 6, 15, 12, 0, 0)
            # Make strftime work properly on the frozen datetime
            frozen = datetime.datetime(2024, 6, 15, 12, 0, 0)
            mock_dt.datetime.utcnow.return_value = frozen
            yield tool3, mock_dt

    def test_returns_two_strings(self, tool3):
        """build_full_output must return a 2-tuple of strings."""
        result = tool3.build_full_output(
            "## Doc", "1. Gap question?", "org", "repo", "MyProject", "1.2.3"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_full_md_contains_doc(self, tool3):
        doc = "# Solution overview\nSome content."
        gaps = "1. What is the deadline?"
        full_md, _ = tool3.build_full_output(doc, gaps, "org", "repo", "MyProject", "1.0.0")
        assert "# Solution overview" in full_md
        assert "Some content." in full_md

    def test_full_md_contains_gaps(self, tool3):
        doc = "# Doc"
        gaps = "1. Gap question one?\n2. Gap question two?"
        full_md, _ = tool3.build_full_output(doc, gaps, "org", "repo", "MyProject", "1.0.0")
        assert "1. Gap question one?" in full_md
        assert "2. Gap question two?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, tool3):
        full_md, _ = tool3.build_full_output(
            "Doc", "1. Q?", "org", "repo", "MyProject", "1.0.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_attribution_footer(self, tool3):
        full_md, _ = tool3.build_full_output(
            "Doc", "1. Q?", "org", "repo", "MyProject", "1.0.0"
        )
        assert "AI Delivery Bot" in full_md
        assert "org/repo" in full_md
        assert "1.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3):
        _, gap_only = tool3.build_full_output(
            "Doc", "1. Q?", "org", "repo", "InsuranceProject", "2.5.0"
        )
        assert "InsuranceProject" in gap_only
        assert "2.5.0" in gap_only

    def test_gap_only_md_contains_gap_questions(self, tool3):
        gaps = "1. Who is the business owner?\n2. What is the retention period?"
        _, gap_only = tool3.build_full_output(
            "Doc", gaps, "org", "repo", "P", "1.0"
        )
        assert "Who is the business owner?" in gap_only
        assert "What is the retention period?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, tool3):
        _, gap_only = tool3.build_full_output(
            "Doc", "1. Q?", "org", "repo", "P", "1.0"
        )
        # Should reference the OUTPUT_REPO_OWNER/OUTPUT_REPO constants
        assert FAKE_OUTPUT_REPO_OWNER in gap_only or FAKE_OUTPUT_REPO in gap_only

    def test_gap_only_md_has_instructions(self