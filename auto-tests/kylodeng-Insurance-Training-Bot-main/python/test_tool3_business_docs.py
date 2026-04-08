"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing delimiter, empty files, Claude errors
    - build_full_output(): happy path, gap count, markdown structure, edge cases
    - __main__ block behaviour (env-var driven entry point) via subprocess/monkeypatch

Mocks used:
    - shared.call_claude          — patched to return controlled strings
    - shared.get_repo_files       — patched to return controlled file dicts
    - shared.write_output_file    — patched to prevent real GitHub writes
    - shared.send_email           — patched to prevent real SMTP calls
    - shared.email_html           — patched to return a dummy HTML string
    - shared.write_audit_entry    — patched to prevent real audit writes
    - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
    - TODO: Integration test against a real (sandboxed) Claude endpoint
    - TODO: Test __main__ block's exception/FAILED path more thoroughly once
            source truncation (the file ends mid-string) is resolved
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers: build a minimal fake "shared" module so the import in
# tool3_business_docs.py succeeds without the real shared.py being present.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub module for `shared`."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A gap question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/output/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>stub</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


# ---------------------------------------------------------------------------
# Fixture: inject the stub and (re)load the module under test
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_stub():
    """Install a fresh shared stub into sys.modules before each test."""
    stub = _make_shared_stub()
    sys.modules["shared"] = stub
    yield stub
    # cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool3_business_docs", None)


@pytest.fixture()
def biz_docs(shared_stub):
    """Load (or reload) tool3_business_docs with the stub in place."""
    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try the directory that contains *this* test file's parent
    alt_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), ".github", "scripts")
    )
    for d in [script_dir, alt_dir]:
        if os.path.isdir(d) and d not in sys.path:
            sys.path.insert(0, d)

    sys.modules.pop("tool3_business_docs", None)
    mod = importlib.import_module("tool3_business_docs")
    return mod


# ---------------------------------------------------------------------------
# Deterministic datetime
# ---------------------------------------------------------------------------

FIXED_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def mock_utcnow():
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DATE
        mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
        # Let strftime still work via the real datetime
        mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
        yield mock_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_returns_doc_and_gaps(self, biz_docs, shared_stub):
        """Claude returns well-formed response with ---GAPS--- delimiter."""
        shared_stub.call_claude.return_value = (
            "# Solution overview\nSome content\n---GAPS---\n1. Who owns this?"
        )
        shared_stub.get_repo_files.return_value = {"README.md": "# My Project"}

        doc, gaps = biz_docs.generate_biz_doc(
            "acme", "my-repo", "My Project", "1.0.0", "https://ci.example.com/run/1"
        )

        assert "# Solution overview" in doc
        assert "Some content" in doc
        assert "1. Who owns this?" in gaps

    def test_no_delimiter_falls_back_gracefully(self, biz_docs, shared_stub):
        """When Claude omits ---GAPS--- the whole response becomes the doc."""
        shared_stub.call_claude.return_value = "Just a plain document with no gaps section."

        doc, gaps = biz_docs.generate_biz_doc(
            "acme", "my-repo", "My Project", "1.0.0", "https://ci.example.com/run/1"
        )

        assert doc == "Just a plain document with no gaps section."
        assert "could not extract gap questions" in gaps

    def test_delimiter_splits_on_first_occurrence_only(self, biz_docs, shared_stub):
        """Only the first ---GAPS--- is used as the split point."""
        shared_stub.call_claude.return_value = (
            "Doc part\n---GAPS---\nGap1\n---GAPS---\nGap2"
        )

        doc, gaps = biz_docs.generate_biz_doc(
            "acme", "my-repo", "P", "0.1.0", "url"
        )

        assert doc == "Doc part"
        assert "Gap1" in gaps
        assert "Gap2" in gaps  # second delimiter becomes part of gaps section

    def test_get_repo_files_called_with_correct_extensions(self, biz_docs, shared_stub):
        """Verifies the correct file extensions are requested."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        biz_docs.generate_biz_doc("owner", "repo", "proj", "v1", "url")

        call_args = shared_stub.get_repo_files.call_args
        extensions = call_args[0][2]  # positional arg 3
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions

    def test_get_repo_files_max_files_20(self, biz_docs, shared_stub):
        """max_files is capped at 20."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        biz_docs.generate_biz_doc("owner", "repo", "proj", "v1", "url")

        call_kwargs = shared_stub.get_repo_files.call_args[1]
        assert call_kwargs.get("max_files") == 20

    def test_call_claude_receives_prompt_with_project_name(self, biz_docs, shared_stub):
        """Project name is interpolated into the prompt."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        biz_docs.generate_biz_doc("owner", "repo", "InsuranceBot", "2.0.0", "url")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "InsuranceBot" in prompt_arg

    def test_call_claude_receives_prompt_with_version(self, biz_docs, shared_stub):
        """Version is interpolated into the prompt."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        biz_docs.generate_biz_doc("owner", "repo", "proj", "3.1.4", "url")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "3.1.4" in prompt_arg

    def test_call_claude_user_message_contains_owner_repo(self, biz_docs, shared_stub):
        """User message passed to Claude mentions owner/repo."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        biz_docs.generate_biz_doc("sun-life", "generations-ii", "proj", "v1", "url")

        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "sun-life/generations-ii" in user_msg

    def test_multiple_files_joined_in_prompt(self, biz_docs, shared_stub):
        """Multiple repo files are concatenated into the user message."""
        shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "infra.tf": 'resource "aws_s3_bucket" "b" {}',
        }
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        biz_docs.generate_biz_doc("owner", "repo", "proj", "v1", "url")

        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "main.py" in user_msg
        assert "infra.tf" in user_msg

    def test_empty_repo_files_still_calls_claude(self, biz_docs, shared_stub):
        """Empty file dict should not crash; Claude is still called."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        doc, gaps = biz_docs.generate_biz_doc("owner", "repo", "proj", "v1", "url")

        assert shared_stub.call_claude.called
        assert doc == "doc"

    def test_file_content_truncated_to_3000_chars(self, biz_docs, shared_stub):
        """Files longer than 3000 chars are truncated in the prompt."""
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big.py": long_content}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        biz_docs.generate_biz_doc("owner", "repo", "proj", "v1", "url")

        user_msg = shared_stub.call_claude.call_args[0][1]
        # The truncated block should contain at most 3000 x's
        assert "x" * 3001 not in user_msg

    def test_doc_and_gaps_are_stripped(self, biz_docs, shared_stub):
        """Leading/trailing whitespace is stripped from both outputs."""
        shared_stub.call_claude.return_value = "  doc content  \n---GAPS---\n  gap content  "

        doc, gaps = biz_docs.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "doc content"
        assert gaps == "gap content"

    def test_claude_exception_propagates(self, biz_docs, shared_stub):
        """If Claude raises, the exception bubbles up."""
        shared_stub.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            biz_docs.generate_biz_doc("owner", "repo", "proj", "v1", "url")

    @pytest.mark.parametrize("project_name,version", [
        ("Generations II", "2.0.0"),
        ("Hospital Network", "1.5.3"),
        ("Cashless Arrangement", "0.9.1"),
    ])
    def test_parametrized_project_names(self, biz_docs, shared_stub, project_name, version):
        """Synthetic data: various insurance product names interpolated correctly."""
        shared_stub.call_claude.return_value = f"# {project_name}\n---GAPS---\n1. Gap?"

        doc, gaps = biz_docs.generate_biz_doc("sun-life", "insurance", project_name, version, "url")

        assert project_name in doc
        assert "1. Gap?" in gaps


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def test_returns_two_strings(self, biz_docs):
        full_md, gap_only_md = biz_docs.build_full_output(
            "# Doc", "1. Gap?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, biz_docs):
        full_md, _ = biz_docs.build_full_output(
            "# Solution Overview\nContent here", "1. Q?",
            "acme", "my-repo", "Proj", "1.0.0"
        )
        assert "# Solution Overview" in full_md
        assert "Content here" in full_md

    def test_full_md_contains_gaps(self, biz_docs):
        full_md, _ = biz_docs.build_full_output(
            "doc", "1. Who is the owner?",
            "acme", "my-repo", "Proj", "1.0.0"
        )
        assert "1. Who is the owner?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, biz_docs):
        full_md, _ = biz_docs.build_full_output(
            "doc", "1. Q?", "owner", "repo", "Proj", "1.0.0"
        )
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, biz_docs):
        full_md, _ = biz_docs.build_full_output(
            "doc", "gaps", "acme", "prod-repo", "Proj", "2.3.1"
        )
        assert "acme/prod-repo" in full_md
        assert "v2.3.1" in full_md

    def test_gap_only_md_contains_project_name(self, biz_docs):
        _, gap_only_md = biz_docs.build_full_output(
            "doc", "1. Q?", "owner", "repo", "InsuranceBot", "1.0.0"
        )
        assert "InsuranceBot" in gap_only_md

    def test_gap_only_md_contains_version(self, biz_docs):
        _, gap_only_md = biz_docs.build_full_output(
            "doc", "1. Q?", "owner", "repo", "Proj", "3.0.0"
        )
        assert "v3.0.0" in gap_only_md

    def test_gap_only_md_contains_