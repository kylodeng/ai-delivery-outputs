"""
Test module for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, missing ---GAPS--- delimiter, Claude API failure
- build_full_output(): correct markdown assembly, gap questionnaire format,
  handling of empty/None gap strings, boundary values for version/project_name

Mocks used:
- shared.call_claude          → prevents real Anthropic API calls
- shared.get_repo_files       → prevents real GitHub API calls
- shared.write_output_file    → prevents real GitHub commits
- shared.send_email           → prevents real SES/SMTP calls
- shared.email_html           → prevents real template rendering
- shared.write_audit_entry    → prevents real audit-log writes
- datetime.datetime.utcnow    → frozen timestamps for deterministic output

TODOs:
- TODO: test __main__ block end-to-end (requires subprocess or importlib reload
        with full env-var injection — skipped below)
- TODO: test write_audit_entry call on failure path in __main__
        (source file is truncated; full exception branch not visible)
"""

import sys
import os
import types
import datetime
import importlib
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: inject a minimal 'shared' stub so the import of tool3 succeeds
# without needing the real shared.py on the path.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.call_claude        = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    mod.get_repo_files     = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file  = MagicMock(return_value="https://github.com/out/repo/blob/main/file.md")
    mod.send_email         = MagicMock()
    mod.email_html         = MagicMock(return_value="<html>body</html>")
    mod.write_audit_entry  = MagicMock()
    mod.OUTPUT_REPO_OWNER  = "test-org"
    mod.OUTPUT_REPO        = "test-output-repo"
    return mod


@pytest.fixture(autouse=True)
def shared_stub(monkeypatch):
    """Install a fresh shared stub before every test."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)
    # Force tool3 to re-import with the stub in place
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]
    yield stub


@pytest.fixture()
def tool3(shared_stub):
    """Return the freshly-imported tool3 module."""
    # Insert the scripts directory so the relative import works
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    import tool3_business_docs as t3
    return t3


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)

class _FakeDatetime(datetime.datetime):
    @classmethod
    def utcnow(cls):
        return FROZEN_DT


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_returns_doc_and_gaps(self, tool3, shared_stub):
        """When Claude returns a response with ---GAPS--- delimiter, both parts
        are returned correctly."""
        shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        shared_stub.call_claude.return_value = (
            "# Solution overview: TestProject\n\nSome content."
            "\n---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the sponsor?"
        )

        doc, gaps = tool3.generate_biz_doc("my-org", "my-repo", "TestProject", "1.0.0", "https://run")

        assert "# Solution overview: TestProject" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the sponsor?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_gaps_delimiter_returns_full_raw_as_doc(self, tool3, shared_stub):
        """When Claude omits ---GAPS---, the whole response becomes the doc and
        a fallback message is used for gaps."""
        shared_stub.call_claude.return_value = "# Just the doc, no delimiter."

        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "0.1.0", "")

        assert doc == "# Just the doc, no delimiter."
        assert "Claude could not extract gap questions" in gaps

    def test_gaps_delimiter_at_start(self, tool3, shared_stub):
        """Delimiter at the very start → empty doc part, all content in gaps."""
        shared_stub.call_claude.return_value = "---GAPS---\n1. Only gaps here."

        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "0.1.0", "")

        assert doc == ""
        assert "1. Only gaps here." in gaps

    def test_gaps_delimiter_at_end(self, tool3, shared_stub):
        """Delimiter at the very end → full doc, empty gaps."""
        shared_stub.call_claude.return_value = "Full doc text.\n---GAPS---"

        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "0.1.0", "")

        assert doc == "Full doc text."
        assert gaps == ""

    def test_multiple_gaps_delimiters_splits_on_first(self, tool3, shared_stub):
        """Only the first ---GAPS--- delimiter should be used for splitting."""
        shared_stub.call_claude.return_value = (
            "Doc part.\n---GAPS---\nGaps part.\n---GAPS---\nExtra stuff."
        )

        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "0.1.0", "")

        assert doc == "Doc part."
        assert "Gaps part." in gaps
        assert "Extra stuff." in gaps  # second delimiter is NOT split further

    def test_get_repo_files_called_with_correct_extensions(self, tool3, shared_stub):
        """Verifies the correct file extensions are requested."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "P", "v1", "")

        shared_stub.get_repo_files.assert_called_once_with(
            "owner",
            "repo",
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_call_claude_receives_project_name_in_prompt(self, tool3, shared_stub):
        """The formatted prompt passed to call_claude must contain the project name."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("org", "repo", "InsurancePortal", "2.3.4", "")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg

    def test_call_claude_receives_version_in_prompt(self, tool3, shared_stub):
        """The formatted prompt must contain the version string."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("org", "repo", "Proj", "3.1.4", "")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "3.1.4" in prompt_arg

    def test_call_claude_receives_today_date_in_prompt(self, tool3, shared_stub):
        """The prompt must include the UTC date at time of invocation."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime", _FakeDatetime):
            tool3.generate_biz_doc("org", "repo", "Proj", "1.0", "")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "2024-06-15" in prompt_arg

    def test_repo_files_content_passed_to_claude(self, tool3, shared_stub):
        """File contents retrieved from the repo must appear in the Claude user message."""
        shared_stub.get_repo_files.return_value = {
            "app.py": "def main(): pass",
            "infra.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("org", "repo", "P", "v1", "")

        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "app.py" in user_msg
        assert "def main(): pass" in user_msg
        assert "infra.tf" in user_msg

    def test_long_file_content_truncated_to_3000_chars(self, tool3, shared_stub):
        """Files longer than 3 000 characters must be truncated before being sent."""
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("org", "repo", "P", "v1", "")

        user_msg = shared_stub.call_claude.call_args[0][1]
        # The embedded content block should not exceed 3000 x's
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files_handled_gracefully(self, tool3, shared_stub):
        """An empty file dict should not raise; Claude still gets called."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc("org", "repo", "P", "v1", "")

        assert shared_stub.call_claude.called
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_call_claude_exception_propagates(self, tool3, shared_stub):
        """If call_claude raises, the exception must propagate from generate_biz_doc."""
        shared_stub.call_claude.side_effect = RuntimeError("Claude is down")

        with pytest.raises(RuntimeError, match="Claude is down"):
            tool3.generate_biz_doc("org", "repo", "P", "v1", "")

    def test_get_repo_files_exception_propagates(self, tool3, shared_stub):
        """If get_repo_files raises, the exception must propagate."""
        shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool3.generate_biz_doc("org", "repo", "P", "v1", "")

    @pytest.mark.parametrize("owner,repo,project,version", [
        ("", "", "", ""),
        ("a", "b", "c", "0.0.0"),
        ("UPPER-ORG", "UPPER-REPO", "My Project Name With Spaces", "10.20.30"),
    ])
    def test_various_input_combinations_do_not_crash(self, tool3, shared_stub, owner, repo, project, version):
        """Parametrised smoke test: different owner/repo/project/version combos."""
        shared_stub.call_claude.return_value = "d\n---GAPS---\ng"
        doc, gaps = tool3.generate_biz_doc(owner, repo, project, version, "")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def _call(self, tool3, doc="# Doc", gaps="1. Question?",
              owner="org", repo="repo", project="Proj", version="1.0.0"):
        return tool3.build_full_output(doc, gaps, owner, repo, project, version)

    def test_returns_tuple_of_two_strings(self, tool3):
        full_md, gap_only = self._call(tool3)
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_full_md_contains_doc_content(self, tool3):
        full_md, _ = self._call(tool3, doc="# My Solution Overview")
        assert "# My Solution Overview" in full_md

    def test_full_md_contains_gaps(self, tool3):
        full_md, _ = self._call(tool3, gaps="1. What is the deadline?")
        assert "1. What is the deadline?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool3):
        full_md, _ = self._call(tool3)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool3):
        full_md, _ = self._call(tool3, owner="acme", repo="billing", version="2.0.0")
        assert "acme/billing" in full_md
        assert "v2.0.0" in full_md

    def test_gap_only_contains_project_name_and_version(self, tool3):
        _, gap_only = self._call(tool3, project="InsurancePortal", version="3.5.1")
        assert "InsurancePortal" in gap_only
        assert "v3.5.1" in gap_only

    def test_gap_only_contains_gap_questions(self, tool3):
        _, gap_only = self._call(tool3, gaps="1. Who is the sponsor?\n2. What is the go-live?")
        assert "1. Who is the sponsor?" in gap_only
        assert "2. What is the go-live?" in gap_only

    def test_gap_only_contains_output_repo_link(self, tool3, shared_stub):
        """The standalone gap doc should link to the output repo."""
        _, gap_only = self._call(tool3)
        assert shared_stub.OUTPUT_REPO_OWNER in gap_only
        assert shared_stub.OUTPUT_REPO in gap_only

    def test_full_md_contains_frozen_timestamp(self, tool3):
        with patch("datetime.datetime", _FakeDatetime):
            full_md, gap_only = self._call(tool3)
        assert "2024-06-15 12:00 UTC" in full_md
        assert "2024-06-15 12:00 UTC" in gap_only

    def test_empty_gaps_string(self, tool3):
        """Empty gaps should not raise; output is still valid."""
        full_md, gap_only = self._call(tool3, gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_empty_doc_string(self, tool