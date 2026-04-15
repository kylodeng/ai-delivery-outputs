"""
Tests for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path with ---GAPS--- delimiter, missing delimiter fallback,
      Claude returning empty string, file truncation behaviour.
    - build_full_output(): happy path structure, gap_only_md structure, timestamp embedding,
      edge cases (empty doc, empty gaps, special characters in project_name/version).
    - __main__ block: env-var wiring, success flow, exception/failure flow.

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (frozen clock)

TODOs:
    # TODO: Integration test that actually calls Claude – needs a real API key + network
    # TODO: Test write_output_file returns a real URL when OUTPUT_REPO_OWNER/OUTPUT_REPO are set
    # TODO: Test behaviour when get_repo_files raises a network error mid-stream
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake 'shared' module so we never import the real
# one (which would require env vars / network access).
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a fake `shared` module with all symbols tool3 imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello", "main.py": "print('hi')"})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject the fake shared module before every test and reload tool3 so the
    module-level `from shared import ...` picks up our mocks.
    """
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)

    # Also remove tool3 from sys.modules so each test gets a fresh import
    sys.modules.pop("tool3_business_docs", None)

    yield fake

    sys.modules.pop("tool3_business_docs", None)


def _import_tool3():
    """Import (or re-import) the module under test."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import tool3_business_docs
    return tool3_business_docs


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Patch datetime.datetime inside tool3_business_docs to a fixed value."""
    import tool3_business_docs as t3

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return FROZEN_DT

    monkeypatch.setattr(t3.datetime, "datetime", _FakeDatetime)
    return _FakeDatetime


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, fake_shared):
        """Claude returns a valid response with ---GAPS--- delimiter."""
        fake_shared.call_claude.return_value = (
            "# Solution overview\nSome doc text.\n---GAPS---\n1. Who owns this?\n2. Go-live date?"
        )
        fake_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "infra.tf": "resource aws_s3_bucket {}",
        }
        t3 = _import_tool3()

        doc, gaps = t3.generate_biz_doc("acme", "my-repo", "My Project", "1.2.3", "https://run.url")

        assert "# Solution overview" in doc
        assert "Some doc text." in doc
        assert "---GAPS---" not in doc
        assert "1. Who owns this?" in gaps
        assert "2. Go-live date?" in gaps

    def test_missing_delimiter_falls_back(self, fake_shared):
        """When ---GAPS--- is absent the full response becomes the doc and gaps is the fallback message."""
        fake_shared.call_claude.return_value = "Just a doc with no delimiter at all."
        t3 = _import_tool3()

        doc, gaps = t3.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run.url")

        assert doc == "Just a doc with no delimiter at all."
        assert "Claude could not extract" in gaps

    def test_delimiter_only_once_splits_correctly(self, fake_shared):
        """Only the first occurrence of ---GAPS--- is used as the split point."""
        fake_shared.call_claude.return_value = (
            "Doc part\n---GAPS---\nGap part with ---GAPS--- inside"
        )
        t3 = _import_tool3()

        doc, gaps = t3.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "Doc part"
        assert "Gap part with ---GAPS--- inside" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, fake_shared):
        """Correct file extensions and max_files are forwarded to get_repo_files."""
        t3 = _import_tool3()
        t3.generate_biz_doc("owner", "repo", "proj", "0.1", "url")

        args, kwargs = fake_shared.get_repo_files.call_args
        assert args[0] == "owner"
        assert args[1] == "repo"
        expected_exts = {".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"}
        assert set(args[2]) == expected_exts
        assert kwargs.get("max_files") == 20

    def test_call_claude_receives_formatted_prompt(self, fake_shared):
        """SYSTEM prompt is formatted with project_name, version and today's date before being sent."""
        t3 = _import_tool3()

        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value.strftime.return_value = "2024-01-01"
            t3.generate_biz_doc("owner", "repo", "AcmeApp", "3.0.0", "url")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "AcmeApp" in prompt_arg
        assert "3.0.0" in prompt_arg

    def test_files_content_truncated_to_3000_chars(self, fake_shared):
        """File content longer than 3000 chars is truncated in the prompt."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        t3 = _import_tool3()
        t3.generate_biz_doc("o", "r", "p", "v", "u")

        # The second positional arg to call_claude is the user message
        user_message = fake_shared.call_claude.call_args[0][1]
        # Only 3000 x's should appear in the snippet
        assert "x" * 3000 in user_message
        assert "x" * 3001 not in user_message

    def test_empty_repo_files(self, fake_shared):
        """Works gracefully when the repo has no matching files."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "empty doc\n---GAPS---\n1. Q?"
        t3 = _import_tool3()

        doc, gaps = t3.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "empty doc"
        assert gaps == "1. Q?"

    def test_empty_claude_response_no_delimiter(self, fake_shared):
        """Empty Claude response is handled – doc is empty string, gaps is fallback."""
        fake_shared.call_claude.return_value = ""
        t3 = _import_tool3()

        doc, gaps = t3.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == ""
        assert "Claude could not extract" in gaps

    def test_claude_response_only_delimiter(self, fake_shared):
        """Response that is exactly the delimiter produces empty doc and empty gaps."""
        fake_shared.call_claude.return_value = "---GAPS---"
        t3 = _import_tool3()

        doc, gaps = t3.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == ""
        assert gaps == ""

    def test_whitespace_stripped_from_parts(self, fake_shared):
        """Leading/trailing whitespace is stripped from both parts."""
        fake_shared.call_claude.return_value = "  \n doc \n  ---GAPS---  \n  gaps \n  "
        t3 = _import_tool3()

        doc, gaps = t3.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_multiple_files_concatenated_in_prompt(self, fake_shared):
        """All files returned by get_repo_files appear in the user message."""
        fake_shared.get_repo_files.return_value = {
            "a.py": "content_a",
            "b.tf": "content_b",
        }
        t3 = _import_tool3()
        t3.generate_biz_doc("o", "r", "p", "v", "u")

        user_message = fake_shared.call_claude.call_args[0][1]
        assert "a.py" in user_message
        assert "content_a" in user_message
        assert "b.tf" in user_message
        assert "content_b" in user_message


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def _call(self, doc="# Doc", gaps="1. Q?", owner="acme", repo="my-repo",
              project_name="MyApp", version="2.0.0"):
        t3 = _import_tool3()
        return t3.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_two_strings(self, fake_shared):
        full_md, gap_only_md = self._call()
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, fake_shared):
        full_md, _ = self._call(doc="# The Doc")
        assert "# The Doc" in full_md

    def test_full_md_contains_gaps(self, fake_shared):
        full_md, _ = self._call(gaps="1. Who is the owner?")
        assert "1. Who is the owner?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, fake_shared):
        full_md, _ = self._call()
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, fake_shared):
        full_md, _ = self._call(owner="acme", repo="my-repo", version="2.0.0")
        assert "acme/my-repo" in full_md
        assert "v2.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, fake_shared):
        _, gap_only_md = self._call(project_name="MyApp", version="3.1.4")
        assert "MyApp" in gap_only_md
        assert "3.1.4" in gap_only_md

    def test_gap_only_md_contains_gaps(self, fake_shared):
        _, gap_only_md = self._call(gaps="1. Target go-live date?")
        assert "1. Target go-live date?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, fake_shared):
        _, gap_only_md = self._call()
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_full_md_contains_timestamp(self, fake_shared, frozen_datetime):
        t3 = _import_tool3()
        full_md, _ = t3.build_full_output("doc", "gaps", "o", "r", "p", "v")
        assert FROZEN_DATETIME_STR in full_md

    def test_gap_only_md_contains_timestamp(self, fake_shared, frozen_datetime):
        t3 = _import_tool3()
        _, gap_only_md = t3.build_full_output("doc", "gaps", "o", "r", "p", "v")
        assert FROZEN_DATETIME_STR in gap_only_md

    def test_empty_doc(self, fake_shared):
        """build_full_output should not crash with an empty doc string."""
        full_md, _ = self._call(doc="")
        assert "Gap Questionnaire" in full_md

    def test_empty_gaps(self, fake_shared):
        """build_full_output should not crash with an empty gaps string."""
        full_md, gap_only_md = self._call(gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_special_characters_in_project_name(self, fake_shared):
        """Special characters in project_name don't break the output."""
        _, gap_only_md = self._call(project_name="Acme & Co / <Test>", version="1.0.0")
        assert "Acme & Co / <Test>" in gap_only_md

    def test_full_md_separator_present(self, fake_shared):
        """Horizontal rules / separators appear in the full document."""
        full_md, _ = self._call()
        assert "---" in full_md

    @pytest.mark.parametrize("version", ["0.0.1", "1.0.0-alpha", "2024.06.15", "