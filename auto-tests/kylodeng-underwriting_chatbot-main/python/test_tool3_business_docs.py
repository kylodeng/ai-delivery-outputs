"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter-present and delimiter-absent Claude responses,
      file truncation handling, prompt formatting.
    - build_full_output(): full markdown assembly, standalone gap questionnaire assembly,
      content checks, edge cases (empty gaps, long content).
    - __main__ block: environment variable reading, success flow, exception / audit-failure flow.

Mocks used:
    - shared.call_claude          — avoids real Anthropic API calls
    - shared.get_repo_files       — avoids real GitHub API calls
    - shared.write_output_file    — avoids real Git commits
    - shared.send_email           — avoids real SMTP/SES calls
    - shared.email_html           — pure helper, mocked for isolation
    - shared.write_audit_entry    — avoids real file-system / Git writes
    - datetime.datetime.utcnow    — frozen for deterministic output
    - os.environ                  — patched per-test via monkeypatch

TODOs:
    - TODO: Integration test that wires a real (sandboxed) call_claude stub through
      the full pipeline once a test-double for the Anthropic SDK is available.
    - TODO: Test the truncated __main__ block send_email failure branch — the source
      file is syntactically incomplete (email_html string is cut off), so a full
      end-to-end __main__ error-path test cannot be reliably executed without the
      complete source.
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so the import in
# tool3_business_docs.py succeeds without the real shared.py on the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/repo/blob/main/file.md")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>body</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject fake shared module before every test and reload the SUT."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Remove any cached version of the SUT so each test gets a fresh module
    sys.modules.pop("tool3_business_docs", None)
    yield fake


@pytest.fixture()
def sut(fake_shared):
    """Return freshly-imported SUT module."""
    # Ensure the script directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import tool3_business_docs
    return tool3_business_docs


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE = "2024-06-15"
FROZEN_DATETIME = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_utcnow():
    """Patch datetime.datetime.utcnow to return a fixed value."""
    with patch("tool3_business_docs.datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FROZEN_DT
        mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
        # Allow strftime to work correctly on the mock
        mock_dt.utcnow.side_effect = None
        mock_dt.utcnow.return_value = FROZEN_DT
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:
    """Tests for the generate_biz_doc() function."""

    def test_happy_path_returns_doc_and_gaps(self, sut, fake_shared):
        """Claude returns a properly delimited response → both parts extracted."""
        fake_shared.call_claude.return_value = (
            "# Solution overview: MyApp\nSome content.\n---GAPS---\n1. What is the go-live date?"
        )
        fake_shared.get_repo_files.return_value = {"main.py": "print('hello')"}

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        assert "# Solution overview: MyApp" in doc
        assert "Some content." in doc
        assert "1. What is the go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_delimiter_returns_full_raw_as_doc(self, sut, fake_shared):
        """When Claude omits ---GAPS--- the entire response becomes the doc."""
        raw_no_delimiter = "# Solution overview\nAll content, no gaps section."
        fake_shared.call_claude.return_value = raw_no_delimiter

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        assert doc == raw_no_delimiter.strip()
        assert "Claude could not extract gap questions" in gaps

    def test_multiple_delimiter_occurrences_splits_on_first(self, sut, fake_shared):
        """Only the first ---GAPS--- delimiter is used for splitting."""
        fake_shared.call_claude.return_value = (
            "Doc part\n---GAPS---\nGaps part\n---GAPS---\nExtra content"
        )

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        assert doc == "Doc part"
        assert "Gaps part" in gaps
        assert "Extra content" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared):
        """get_repo_files must be invoked with the expected extension list."""
        sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        args, kwargs = fake_shared.get_repo_files.call_args
        extensions = args[2] if len(args) >= 3 else kwargs.get("extensions", args[1] if len(args) >= 2 else None)
        # Accept either positional or keyword
        call_kwargs = fake_shared.get_repo_files.call_args
        all_args = call_kwargs[0]
        all_kwargs = call_kwargs[1]
        ext_arg = all_args[2] if len(all_args) > 2 else all_kwargs.get("extensions", [])
        assert ".py" in ext_arg
        assert ".tf" in ext_arg
        assert ".md" in ext_arg

    def test_get_repo_files_max_files_is_20(self, sut, fake_shared):
        """max_files must be capped at 20."""
        sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        call_kwargs = fake_shared.get_repo_files.call_args
        all_kwargs = call_kwargs[1]
        all_args = call_kwargs[0]
        max_files = all_kwargs.get("max_files", all_args[3] if len(all_args) > 3 else None)
        assert max_files == 20

    def test_call_claude_receives_owner_repo_in_user_message(self, sut, fake_shared):
        """User message to Claude must contain repo identification."""
        sut.generate_biz_doc("my-owner", "my-repo", "MyApp", "2.0.0", "https://run-url")

        _, user_msg = fake_shared.call_claude.call_args[0]
        assert "my-owner/my-repo" in user_msg

    def test_file_content_truncated_to_3000_chars(self, sut, fake_shared):
        """Files longer than 3000 chars must be truncated in the prompt."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}

        sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        _, user_msg = fake_shared.call_claude.call_args[0]
        # The truncated slice is 3000 chars; check that the full 5000-char string is absent
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_empty_repo_files_produces_empty_files_str(self, sut, fake_shared):
        """An empty repo dict should not cause a crash."""
        fake_shared.get_repo_files.return_value = {}

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_prompt_contains_project_name(self, sut, fake_shared):
        """System prompt passed to Claude must include the project name."""
        sut.generate_biz_doc("acme", "my-repo", "InsuranceBot", "3.1.4", "https://run-url")

        system_prompt = fake_shared.call_claude.call_args[0][0]
        assert "InsuranceBot" in system_prompt

    def test_prompt_contains_version(self, sut, fake_shared):
        """System prompt passed to Claude must include the version string."""
        sut.generate_biz_doc("acme", "my-repo", "MyApp", "42.0.0", "https://run-url")

        system_prompt = fake_shared.call_claude.call_args[0][0]
        assert "42.0.0" in system_prompt

    def test_doc_and_gaps_are_stripped(self, sut, fake_shared):
        """Leading/trailing whitespace should be stripped from both parts."""
        fake_shared.call_claude.return_value = "  \n  doc body  \n  ---GAPS---  \n  gap body  \n  "

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_delimiter_only_response(self, sut, fake_shared):
        """Response is just the delimiter — both parts should be empty strings."""
        fake_shared.call_claude.return_value = "---GAPS---"

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyApp", "1.0.0", "https://run-url")

        assert doc == ""
        assert gaps == ""

    @pytest.mark.parametrize("project_name,version", [
        ("Underwriting Risk Classification", "0.1.0"),
        ("InsuranceApp", "2.3.1"),
        ("customer-similarity", "1.0.0-beta"),
        ("AR-SA Frontend", "0.0.1"),
    ])
    def test_various_project_names_and_versions(self, sut, fake_shared, project_name, version):
        """Parametrised: various project names and versions don't break generation."""
        fake_shared.call_claude.return_value = f"Doc for {project_name}\n---GAPS---\n1. Question?"

        doc, gaps = sut.generate_biz_doc("owner", "repo", project_name, version, "https://run")

        assert project_name in doc or doc  # doc exists
        assert isinstance(gaps, str)


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:
    """Tests for the build_full_output() function."""

    def test_full_md_contains_doc_content(self, sut):
        """The full markdown must include the doc part verbatim."""
        doc = "# Solution overview: Acme\nSome description."
        gaps = "1. What is the go-live date?"

        full_md, _ = sut.build_full_output(doc, gaps, "acme", "repo", "Acme", "1.0.0")

        assert doc in full_md

    def test_full_md_contains_gaps_content(self, sut):
        """The full markdown must include gap questions."""
        doc = "# Solution overview"
        gaps = "1. Who owns this system?"

        full_md, _ = sut.build_full_output(doc, gaps, "acme", "repo", "Acme", "1.0.0")

        assert gaps in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, sut):
        """The assembled doc must have a Gap Questionnaire section."""
        full_md, _ = sut.build_full_output("doc", "gaps", "acme", "repo", "Acme", "1.0.0")

        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, sut):
        """Footer must mention the source repo and version."""
        full_md, _ = sut.build_full_output("doc", "gaps", "acme", "repo", "Acme", "2.5.0")

        assert "acme/repo" in full_md
        assert "2.5.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, sut):
        """Standalone gap questionnaire heading must name the project and version."""
        _, gap_only = sut.build_full_output("doc", "1. A gap?", "acme", "repo", "MyProject", "3.0.0")

        assert "MyProject" in gap_only
        assert "3.0.0" in gap_only

    def test_gap_only_md_contains_gap_questions(self, sut):
        """Standalone gap questionnaire must contain the questions."""
        gaps = "1. Who is the sponsor?\n2. What is the deadline?"
        _, gap_only = sut.build_full_output("doc", gaps, "acme", "repo", "MyProject", "1.0.0")

        assert "1. Who is the sponsor?" in gap_only
        assert "2. What is the deadline?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, sut):
        """Standalone questionnaire must link to the output repo."""
        _, gap_only = sut.build_full_output("doc", "gaps", "acme", "repo", "MyProject", "1.0.0")

        assert FAKE_OUTPUT_REPO_OWNER in gap_only
        assert FAKE_OUTPUT_REPO in gap_only

    def test