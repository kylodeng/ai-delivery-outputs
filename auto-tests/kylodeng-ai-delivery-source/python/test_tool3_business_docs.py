"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude integration
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content correctness
    - __main__ block logic (via subprocess or direct function calls with mocked env)
    - Edge cases: missing delimiter, empty gaps, empty files, special characters in project_name/version

Mocks used:
    - shared.call_claude           — stubbed to return controlled strings
    - shared.get_repo_files        — stubbed to return controlled dict
    - shared.write_output_file     — stubbed to prevent real GitHub writes
    - shared.send_email            — stubbed to prevent real email sends
    - shared.email_html            — stubbed to return dummy HTML
    - shared.write_audit_entry     — stubbed to prevent real audit writes
    - datetime.datetime.utcnow     — frozen for deterministic output
    - os.environ                   — patched via monkeypatch

TODOs:
    - TODO: Test __main__ block as a subprocess with real env injection once
            integration harness is available
    - TODO: Test truncation behaviour when a file's content exceeds 3000 chars
            (requires a fixture file of known length)
    - TODO: Validate the SYSTEM prompt template renders correctly for all
            supported locale date formats
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so the import in the SUT
# succeeds without any real dependencies installed.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a module-like object that satisfies the SUT's `from shared import ...`."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/output/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>OK</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


# ---------------------------------------------------------------------------
# We load the SUT module lazily so we can inject fakes before import.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Install a fake `shared` module in sys.modules before each test so the
    `from shared import ...` inside tool3_business_docs never touches real code.
    """
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Also ensure the SUT module is (re)loaded fresh each test so mutations
    # from one test don't bleed into the next.
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]
    yield fake
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]


@pytest.fixture()
def sut(fake_shared):
    """Import the SUT after fakes are in place and return the module."""
    # Make sure the scripts directory is on sys.path so the relative import works.
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import tool3_business_docs
    return tool3_business_docs


FROZEN_NOW_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Patch datetime.datetime inside the SUT to a known value."""
    # We patch at the module level after import.
    fake_dt = MagicMock(wraps=datetime.datetime)
    fake_dt.utcnow.return_value = FROZEN_NOW_DATE
    return fake_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, sut, fake_shared, monkeypatch):
        """Claude returns output with ---GAPS--- delimiter → both parts split correctly."""
        fake_shared.call_claude.return_value = (
            "## Solution Overview\nSome content.\n---GAPS---\n1. What is the go-live date?"
        )
        fake_shared.get_repo_files.return_value = {"main.py": "print('hello')"}

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            doc, gaps = sut.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://ci.example.com/run/1")

        assert "Solution Overview" in doc
        assert "---GAPS---" not in doc
        assert "What is the go-live date?" in gaps

    def test_happy_path_without_delimiter(self, sut, fake_shared, monkeypatch):
        """Claude returns output without ---GAPS--- delimiter → fallback message used."""
        fake_shared.call_claude.return_value = "Some undivided content from Claude."
        fake_shared.get_repo_files.return_value = {"app.ts": "const x = 1;"}

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            doc, gaps = sut.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://ci.example.com")

        assert doc == "Some undivided content from Claude."
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared, monkeypatch):
        """Verify the file extension filter list passed to get_repo_files."""
        fake_shared.call_claude.return_value = "content---GAPS---questions"

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            sut.generate_biz_doc("owner", "repo", "proj", "0.1.0", "url")

        args, kwargs = fake_shared.get_repo_files.call_args
        assert args[0] == "owner"
        assert args[1] == "repo"
        extensions = args[2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_formatted_prompt(self, sut, fake_shared, monkeypatch):
        """Prompt passed to Claude must include project_name, version, and date."""
        fake_shared.call_claude.return_value = "x---GAPS---y"
        fake_shared.get_repo_files.return_value = {}

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            sut.generate_biz_doc("owner", "repo", "SuperProject", "3.2.1", "url")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "SuperProject" in prompt_arg
        assert "3.2.1" in prompt_arg
        assert FROZEN_DATE_STR in prompt_arg

    def test_files_content_truncated_to_3000_chars_in_prompt(self, sut, fake_shared, monkeypatch):
        """Each file's content is sliced to 3000 chars when building the prompt."""
        long_content = "A" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "doc---GAPS---gap"

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            sut.generate_biz_doc("o", "r", "p", "v", "u")

        user_msg = fake_shared.call_claude.call_args[0][1]
        # The slice [:3000] means at most 3000 A's should appear in the message
        assert "A" * 3001 not in user_msg
        assert "A" * 3000 in user_msg

    def test_empty_repo_files(self, sut, fake_shared, monkeypatch):
        """No files → empty files_str; call_claude still called; result still split correctly."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "empty doc---GAPS---empty gaps"

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            doc, gaps = sut.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "empty doc"
        assert gaps == "empty gaps"

    def test_multiple_gaps_delimiter_splits_on_first(self, sut, fake_shared, monkeypatch):
        """If ---GAPS--- appears more than once, only the first occurrence is used as the split."""
        fake_shared.call_claude.return_value = (
            "doc part---GAPS---gap part one---GAPS---gap part two"
        )
        fake_shared.get_repo_files.return_value = {}

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            doc, gaps = sut.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "doc part"
        assert "gap part one" in gaps
        assert "gap part two" in gaps

    def test_call_claude_raises_exception(self, sut, fake_shared, monkeypatch):
        """If call_claude raises, generate_biz_doc propagates the exception."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("API failure")

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            with pytest.raises(RuntimeError, match="API failure"):
                sut.generate_biz_doc("o", "r", "p", "v", "u")

    def test_user_message_contains_owner_repo(self, sut, fake_shared, monkeypatch):
        """The user message passed to Claude should reference owner/repo."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "d---GAPS---g"

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            sut.generate_biz_doc("myowner", "myrepo", "p", "v", "u")

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "myowner/myrepo" in user_msg

    @pytest.mark.parametrize("project_name,version", [
        ("", ""),
        ("Project With Spaces", "1.0.0-beta.1"),
        ("Proj/Slash", "2.0"),
        ("Unicode 🚀", "0.0.1"),
    ])
    def test_special_project_name_version_values(self, sut, fake_shared, monkeypatch, project_name, version):
        """generate_biz_doc handles unusual project names and version strings."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = f"doc for {project_name}---GAPS---gaps"

        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            doc, gaps = sut.generate_biz_doc("o", "r", project_name, version, "u")

        assert doc == f"doc for {project_name}"
        assert gaps == "gaps"


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def _call(self, sut, doc="## Doc", gaps="1. Question?",
              owner="acme", repo="my-repo",
              project_name="My Project", version="1.2.3"):
        with patch.object(sut.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_NOW_DATE
            return sut.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_tuple_of_two_strings(self, sut):
        full_md, gap_only_md = self._call(sut)
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, sut):
        full_md, _ = self._call(sut, doc="## My Great Doc")
        assert "## My Great Doc" in full_md

    def test_full_md_contains_gaps_section_header(self, sut):
        full_md, _ = self._call(sut, gaps="1. Who owns this?")
        assert "Gap Questionnaire" in full_md
        assert "1. Who owns this?" in full_md

    def test_full_md_contains_ai_attribution_with_frozen_time(self, sut):
        full_md, _ = self._call(sut)
        assert FROZEN_DATETIME_STR in full_md

    def test_full_md_contains_source_owner_repo_version(self, sut):
        full_md, _ = self._call(sut, owner="acme", repo="widget-svc", version="2.0.0")
        assert "acme/widget-svc" in full_md
        assert "v2.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, sut):
        _, gap_only_md = self._call(sut, project_name="AwesomeApp", version="3.0.0")
        assert "AwesomeApp" in gap_only_md
        assert "v3.0.0" in gap_only_md

    def test_gap_only_md_contains_gaps_content(self, sut):
        _, gap_only