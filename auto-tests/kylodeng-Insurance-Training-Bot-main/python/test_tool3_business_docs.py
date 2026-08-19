"""
Test module for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, missing ---GAPS--- delimiter, Claude returning empty string
- build_full_output(): happy path, empty gaps, output structure/content verification
- __main__ block execution: env var handling, success flow, exception/failure flow

Mocks used:
- shared.call_claude (patched via sys.modules insertion)
- shared.get_repo_files
- shared.write_output_file
- shared.send_email
- shared.email_html
- shared.write_audit_entry
- shared.OUTPUT_REPO_OWNER
- shared.OUTPUT_REPO
- datetime.datetime.utcnow (controlled timestamps)

TODOs:
- TODO: Integration test with real Claude API (requires API key + live network)
- TODO: Test write_output_file path collision handling (needs real git repo or deeper mock)
- TODO: Test email body content in more detail once email_html signature is confirmed
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
# Helpers: build a fake `shared` module so we can import tool3 without the
# real `shared` being present (it depends on GitHub tokens, etc.)
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    shared.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    shared.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/file.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared_module():
    """Inject a fake `shared` module before each test and clean up after."""
    fake = _make_fake_shared()
    sys.modules["shared"] = fake
    # Force reimport of the module under test so it picks up the fake shared
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]
    yield fake
    # Teardown
    for mod_name in ["tool3_business_docs", "shared"]:
        sys.modules.pop(mod_name, None)


@pytest.fixture()
def tool3():
    """Return the freshly-imported tool3_business_docs module."""
    # Ensure scripts directory is on sys.path so the import inside the module works
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also insert the repo root so `shared` is found (we already injected fake)
    import tool3_business_docs  # noqa: PLC0415
    return tool3_business_docs


# ---------------------------------------------------------------------------
# Fixed timestamp for deterministic assertions
# ---------------------------------------------------------------------------

FIXED_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:
    """Tests for the generate_biz_doc function."""

    def test_happy_path_with_delimiter(self, tool3, fake_shared_module):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        fake_shared_module.call_claude.return_value = (
            "## Solution overview\nSome content\n---GAPS---\n1. What is the deadline?"
        )
        fake_shared_module.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# Project",
        }

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            doc, gaps = tool3.generate_biz_doc(
                "myorg", "myrepo", "MyProject", "1.0.0", "https://github.com/run/1"
            )

        assert "Solution overview" in doc
        assert "1. What is the deadline?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_delimiter_falls_back_gracefully(self, tool3, fake_shared_module):
        """When Claude does not return ---GAPS---, gaps gets a fallback message."""
        fake_shared_module.call_claude.return_value = "Just a plain document with no delimiter."

        doc, gaps = tool3.generate_biz_doc(
            "myorg", "myrepo", "MyProject", "1.0.0", "https://github.com/run/1"
        )

        assert doc == "Just a plain document with no delimiter."
        assert "Claude could not extract" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, fake_shared_module):
        """Ensures get_repo_files is called with expected file extensions."""
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "P", "0.1", "url")

        fake_shared_module.get_repo_files.assert_called_once()
        args, kwargs = fake_shared_module.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        for ext in [".py", ".md", ".tf", ".yaml"]:
            assert ext in extensions

    def test_get_repo_files_max_files_is_20(self, tool3, fake_shared_module):
        """max_files should be 20."""
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "P", "0.1", "url")

        _, kwargs = fake_shared_module.get_repo_files.call_args
        # max_files can be positional or keyword
        call_args = fake_shared_module.get_repo_files.call_args
        assert 20 in call_args.args or call_args.kwargs.get("max_files") == 20

    def test_call_claude_receives_project_info_in_prompt(self, tool3, fake_shared_module):
        """The prompt passed to call_claude should contain project_name and version."""
        fake_shared_module.call_claude.return_value = "d\n---GAPS---\ng"

        tool3.generate_biz_doc("owner", "repo", "SpecialProject", "3.2.1", "url")

        prompt_arg = fake_shared_module.call_claude.call_args.args[0]
        assert "SpecialProject" in prompt_arg
        assert "3.2.1" in prompt_arg

    def test_call_claude_user_message_contains_repo_info(self, tool3, fake_shared_module):
        """The user message to call_claude should reference owner/repo."""
        fake_shared_module.call_claude.return_value = "d\n---GAPS---\ng"
        fake_shared_module.get_repo_files.return_value = {"app.py": "x = 1"}

        tool3.generate_biz_doc("acme", "widget", "Widget", "1.0", "url")

        user_msg = fake_shared_module.call_claude.call_args.args[1]
        assert "acme/widget" in user_msg

    def test_delimiter_appears_multiple_times_uses_first_split(self, tool3, fake_shared_module):
        """Only the first ---GAPS--- delimiter should split doc from gaps."""
        fake_shared_module.call_claude.return_value = (
            "Doc text\n---GAPS---\nFirst gap\n---GAPS---\nSecond gap"
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "v", "url")

        assert "Doc text" in doc
        assert "First gap" in gaps
        # The second delimiter and beyond should remain in the gaps section
        assert "Second gap" in gaps

    def test_empty_repo_files(self, tool3, fake_shared_module):
        """Should not crash when get_repo_files returns an empty dict."""
        fake_shared_module.get_repo_files.return_value = {}
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "v", "url")
        assert doc == "doc"
        assert gaps == "gaps"

    def test_file_content_is_truncated_at_3000_chars(self, tool3, fake_shared_module):
        """File content longer than 3000 chars should be truncated."""
        long_content = "x" * 5000
        fake_shared_module.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared_module.call_claude.return_value = "d\n---GAPS---\ng"

        tool3.generate_biz_doc("o", "r", "P", "v", "url")

        user_msg = fake_shared_module.call_claude.call_args.args[1]
        # The truncated content (3000 x's) should appear but not 5000 x's
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_doc_and_gaps_are_stripped(self, tool3, fake_shared_module):
        """Leading/trailing whitespace should be stripped from both outputs."""
        fake_shared_module.call_claude.return_value = (
            "  \n  doc content  \n  ---GAPS---  \n  gap content  \n  "
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "v", "url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_call_claude_exception_propagates(self, tool3, fake_shared_module):
        """Exceptions from call_claude should propagate up."""
        fake_shared_module.call_claude.side_effect = RuntimeError("Claude is down")

        with pytest.raises(RuntimeError, match="Claude is down"):
            tool3.generate_biz_doc("o", "r", "P", "v", "url")

    def test_get_repo_files_exception_propagates(self, tool3, fake_shared_module):
        """Exceptions from get_repo_files should propagate up."""
        fake_shared_module.get_repo_files.side_effect = ConnectionError("Network error")

        with pytest.raises(ConnectionError, match="Network error"):
            tool3.generate_biz_doc("o", "r", "P", "v", "url")

    def test_date_injected_into_prompt(self, tool3, fake_shared_module):
        """The current date should appear in the prompt sent to Claude."""
        fake_shared_module.call_claude.return_value = "d\n---GAPS---\ng"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = "2024-01-01"
            tool3.generate_biz_doc("o", "r", "P", "v", "url")

        prompt_arg = fake_shared_module.call_claude.call_args.args[0]
        assert "2024-01-01" in prompt_arg


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================


class TestBuildFullOutput:
    """Tests for the build_full_output function."""

    def test_full_md_contains_doc(self, tool3):
        """Full markdown output should contain the doc section."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_DATETIME_STR
            full_md, _ = tool3.build_full_output(
                "## My Doc", "1. A question?", "owner", "repo", "MyProject", "1.0.0"
            )

        assert "## My Doc" in full_md

    def test_full_md_contains_gaps(self, tool3):
        """Full markdown output should contain the gap questionnaire section."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_DATETIME_STR
            full_md, _ = tool3.build_full_output(
                "doc", "1. Gap question?", "owner", "repo", "MyProject", "1.0.0"
            )

        assert "1. Gap question?" in full_md
        assert "Gap Questionnaire" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3):
        """Standalone gap questionnaire should reference project name and version."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_DATETIME_STR
            _, gap_only_md = tool3.build_full_output(
                "doc", "1. Question?", "owner", "repo", "SpecialProject", "2.5.0"
            )

        assert "SpecialProject" in gap_only_md
        assert "2.5.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, tool3):
        """The gap-only file should contain the gap questions."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_DATETIME_STR
            _, gap_only_md = tool3.build_full_output(
                "doc", "1. Specific question?", "owner", "repo", "P", "v"
            )

        assert "1. Specific question?" in gap_only_md

    def test_full_md_attribution_contains_source_repo(self, tool3):
        """The auto-generated attribution line should reference owner/repo."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_DATETIME_STR
            full_md, _ = tool3.build_full_output(
                "doc", "gaps", "myowner", "myrepo", "P", "1.0"
            )

        assert "myowner/myrepo" in full_md

    def test_gap_only_md_attribution_contains_output_repo_link(self, tool3):
        """Gap-only file attribution should link to the output repo."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_DATETIME_STR
            _, gap_only_md = tool3.build_full_output(
                "doc", "gaps", "owner", "repo", "P", "v"