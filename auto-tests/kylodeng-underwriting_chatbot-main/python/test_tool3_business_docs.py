"""
Tests for tool3_business_docs.py
=================================
What is tested:
  - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude raw response handling
  - build_full_output(): markdown assembly, gap-only markdown assembly, content correctness
  - __main__ block execution path (via subprocess or importlib with env vars patched)

Mocks used:
  - shared.call_claude          → unittest.mock.patch
  - shared.get_repo_files       → unittest.mock.patch
  - shared.write_output_file    → unittest.mock.patch
  - shared.send_email           → unittest.mock.patch
  - shared.email_html           → unittest.mock.patch
  - shared.write_audit_entry    → unittest.mock.patch
  - datetime.datetime.utcnow    → unittest.mock.patch (frozen time)

TODOs:
  - TODO: Integration test against real Claude API (needs ANTHROPIC_API_KEY secret)
  - TODO: Test __main__ FAILED branch fully — requires inspecting write_audit_entry call args
           when generate_biz_doc raises; currently covered as a stub
  - TODO: Test write_output_file return value propagation to send_email html_body
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch, call
import datetime

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so we never import the real one
# ---------------------------------------------------------------------------

def _make_shared_stub():
    """Return a MagicMock that looks like the shared module."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Who is the owner?")
    stub.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    stub.write_output_file = MagicMock(return_value="https://github.com/output/file")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>body</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "output-owner"
    stub.OUTPUT_REPO = "output-repo"
    return stub


def _import_module(shared_stub=None):
    """
    Import (or re-import) tool3_business_docs with a controlled shared stub.
    Returns the module object.
    """
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    sys.modules["shared"] = shared_stub

    # Force re-import each time so patches are fresh
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]

    import tool3_business_docs as mod  # noqa: PLC0415
    return mod, shared_stub


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


class _FrozenDatetime(datetime.datetime):
    @classmethod
    def utcnow(cls):
        return FROZEN_DT


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
def mod_and_stub(shared_stub):
    module, stub = _import_module(shared_stub)
    return module, stub


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, mod_and_stub):
        """Claude returns both sections separated by ---GAPS---."""
        mod, stub = mod_and_stub
        stub.call_claude.return_value = (
            "# Solution Overview\nSome content.\n---GAPS---\n1. Gap question one?\n2. Gap question two?"
        )
        stub.get_repo_files.return_value = {
            "README.md": "# My Project",
            "main.py": "print('hello')",
        }

        with patch("datetime.datetime", _FrozenDatetime):
            doc, gaps = mod.generate_biz_doc("my-org", "my-repo", "My Project", "1.0.0", "https://run.url")

        assert "# Solution Overview" in doc
        assert "Some content." in doc
        assert "Gap question one?" in gaps
        assert "Gap question two?" in gaps
        # delimiter itself should NOT appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_no_gaps_delimiter(self, mod_and_stub):
        """Claude returns a response without the ---GAPS--- delimiter."""
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "# Solution Overview\nOnly the doc, no gaps section."

        with patch("datetime.datetime", _FrozenDatetime):
            doc, gaps = mod.generate_biz_doc("org", "repo", "Proj", "0.1.0", "https://run")

        assert "# Solution Overview" in doc
        assert gaps == "_Claude could not extract gap questions — review the document manually._"

    def test_get_repo_files_called_with_correct_extensions(self, mod_and_stub):
        """Ensures get_repo_files is invoked with expected file extensions and max_files."""
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime", _FrozenDatetime):
            mod.generate_biz_doc("owner", "repo", "P", "1", "url")

        stub.get_repo_files.assert_called_once()
        args, kwargs = stub.get_repo_files.call_args
        # Positional: owner, repo, extensions list
        assert args[0] == "owner"
        assert args[1] == "repo"
        exts = args[2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in exts
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_formatted_prompt(self, mod_and_stub):
        """SYSTEM prompt is formatted with project_name, version, date before being sent."""
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime", _FrozenDatetime):
            mod.generate_biz_doc("owner", "repo", "Underwriting Risk", "2.3.1", "url")

        system_prompt_arg = stub.call_claude.call_args[0][0]
        assert "Underwriting Risk" in system_prompt_arg
        assert "2.3.1" in system_prompt_arg
        assert FROZEN_DATE_STR in system_prompt_arg

    def test_call_claude_receives_file_contents_in_user_message(self, mod_and_stub):
        """File contents are concatenated and sent as the user message to Claude."""
        mod, stub = mod_and_stub
        stub.get_repo_files.return_value = {
            "app.py": "def main(): pass",
            "README.md": "# Docs",
        }
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime", _FrozenDatetime):
            mod.generate_biz_doc("owner", "repo", "P", "1", "url")

        user_msg = stub.call_claude.call_args[0][1]
        assert "owner/repo" in user_msg
        assert "app.py" in user_msg
        assert "def main(): pass" in user_msg
        assert "README.md" in user_msg

    def test_empty_repo_files(self, mod_and_stub):
        """Works even when get_repo_files returns an empty dict."""
        mod, stub = mod_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc only---GAPS---gap line"

        with patch("datetime.datetime", _FrozenDatetime):
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == "doc only"
        assert gaps == "gap line"

    def test_multiple_gaps_delimiters_only_first_split(self, mod_and_stub):
        """If Claude includes ---GAPS--- more than once, only the first split is used."""
        mod, stub = mod_and_stub
        stub.call_claude.return_value = (
            "doc part---GAPS---first gaps---GAPS---second gaps"
        )

        with patch("datetime.datetime", _FrozenDatetime):
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == "doc part"
        # Everything after first delimiter goes into gaps
        assert "first gaps" in gaps
        assert "second gaps" in gaps

    def test_whitespace_stripped_from_parts(self, mod_and_stub):
        """Leading/trailing whitespace is stripped from both returned parts."""
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "  doc with spaces  ---GAPS---   gaps with spaces   "

        with patch("datetime.datetime", _FrozenDatetime):
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == "doc with spaces"
        assert gaps == "gaps with spaces"

    def test_call_claude_propagates_exception(self, mod_and_stub):
        """If call_claude raises, generate_biz_doc propagates the exception."""
        mod, stub = mod_and_stub
        stub.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            with patch("datetime.datetime", _FrozenDatetime):
                mod.generate_biz_doc("o", "r", "P", "1", "u")

    def test_get_repo_files_propagates_exception(self, mod_and_stub):
        """If get_repo_files raises, generate_biz_doc propagates the exception."""
        mod, stub = mod_and_stub
        stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            with patch("datetime.datetime", _FrozenDatetime):
                mod.generate_biz_doc("o", "r", "P", "1", "u")

    def test_large_file_contents_truncated_at_3000_chars(self, mod_and_stub):
        """File contents are truncated to 3000 characters in the user message."""
        mod, stub = mod_and_stub
        long_content = "x" * 5000
        stub.get_repo_files.return_value = {"big.py": long_content}
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime", _FrozenDatetime):
            mod.generate_biz_doc("o", "r", "P", "1", "u")

        user_msg = stub.call_claude.call_args[0][1]
        # The truncated content should appear (3000 x's), not the full 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    @pytest.fixture()
    def sample_doc(self):
        return "# Solution Overview\nThis is the document body."

    @pytest.fixture()
    def sample_gaps(self):
        return "1. Who owns this project?\n2. What is the go-live date?"

    def test_full_md_contains_doc_content(self, mod, sample_doc, sample_gaps):
        with patch("datetime.datetime", _FrozenDatetime):
            full_md, _ = mod.build_full_output(
                sample_doc, sample_gaps, "owner", "repo", "MyProject", "1.0.0"
            )
        assert "# Solution Overview" in full_md
        assert "This is the document body." in full_md

    def test_full_md_contains_gaps_section(self, mod, sample_doc, sample_gaps):
        with patch("datetime.datetime", _FrozenDatetime):
            full_md, _ = mod.build_full_output(
                sample_doc, sample_gaps, "owner", "repo", "MyProject", "1.0.0"
            )
        assert "## Gap Questionnaire" in full_md
        assert "Who owns this project?" in full_md
        assert "What is the go-live date?" in full_md

    def test_full_md_contains_source_attribution(self, mod, sample_doc, sample_gaps):
        with patch("datetime.datetime", _FrozenDatetime):
            full_md, _ = mod.build_full_output(
                sample_doc, sample_gaps, "owner", "repo", "MyProject", "1.0.0"
            )
        assert "owner/repo" in full_md
        assert "v1.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_full_md_contains_frozen_timestamp(self, mod, sample_doc, sample_gaps):
        with patch("datetime.datetime", _FrozenDatetime):
            full_md, _ = mod.build_full_output(
                sample_doc, sample_gaps, "owner", "repo", "MyProject", "1.0.0"
            )
        assert FROZEN_DATETIME_STR in full_md

    def test_gap_only_md_contains_project_name_and_version(self, mod, sample_doc, sample_gaps):
        with patch("datetime.datetime", _FrozenDatetime):
            _, gap_only_md = mod.build_full_output(
                sample_doc, sample_gaps, "owner", "repo", "MyProject", "1.0.0"
            )
        assert "MyProject" in gap_only_md
        assert "v1.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, mod, sample_doc, sample_gaps):
        with patch("datetime.datetime", _FrozenDatetime):
            _, gap_only_md = mod.build_full_output(
                sample_doc, sample_gaps, "owner", "repo", "MyProject", "1.0.0"
            )
        assert "Who owns this project?" in gap_only_md
        assert "What is the go-live date?" in gap_only_md

    def test_gap_only_md_links_to_output_repo(self, mod, sample_doc, sample_gaps):
        with patch("datetime.datetime", _FrozenDatetime):
            _, gap_only_md = mod.build_full_output(
                sample_doc, sample_gaps, "owner", "repo", "MyProject", "1.0.0"
            )
        # Uses OUTPUT_REPO_OWNER / OUTPUT_REPO from shared stub
        assert "output-owner" in gap_only_md
        assert "output-repo" in gap_only_md

    def