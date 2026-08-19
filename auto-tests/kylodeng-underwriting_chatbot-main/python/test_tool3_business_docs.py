"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with ---GAPS--- delimiter), missing delimiter fallback,
      Claude returning empty string, file truncation logic, prompt formatting.
    - build_full_output(): happy path structure, gap-only markdown structure,
      edge cases (empty gaps, empty doc, special characters in project_name/version).
    - __main__ block execution paths: success flow, exception/failure flow.

Mocks used:
    - shared.call_claude          — avoids real Anthropic API calls
    - shared.get_repo_files       — avoids real GitHub API calls
    - shared.write_output_file    — avoids real GitHub repo writes
    - shared.send_email           — avoids real SMTP/SES calls
    - shared.email_html           — pure helper, mocked for isolation
    - shared.write_audit_entry    — avoids real audit writes
    - datetime.datetime.utcnow    — pinned to deterministic timestamp
    - os.environ                  — patched via monkeypatch

TODOs:
    - TODO: test integration with real shared.call_claude once API keys are available in CI
    - TODO: test write_output_file path segment construction end-to-end
    - TODO: add property-based tests for build_full_output with hypothesis
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so the import in the
# source file succeeds without needing the real module on PYTHONPATH.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal mock of the `shared` module."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="doc body\n---GAPS---\n1. A gap question?")
    shared.get_repo_files = MagicMock(return_value={
        "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
        "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance..."}}',
    })
    shared.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject the fake shared module before every test and reload the SUT."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    # Remove cached SUT so it picks up fresh mocks each time
    sys.modules.pop("tool3_business_docs", None)
    return fake


@pytest.fixture()
def sut(fake_shared):
    """Import (or re-import) the module under test after mocks are in place."""
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Also try current directory structure
    for path_candidate in [
        os.path.join(os.path.dirname(__file__), ".github", "scripts"),
        os.path.dirname(__file__),
    ]:
        if path_candidate not in sys.path:
            sys.path.insert(0, path_candidate)

    import tool3_business_docs as mod
    return mod


# ---------------------------------------------------------------------------
# Pinned datetime for deterministic tests
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 30, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:30 UTC"


# ===========================================================================
# generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, sut, fake_shared):
        """Claude returns properly delimited output → doc and gaps split correctly."""
        fake_shared.call_claude.return_value = (
            "# Solution overview\nThis solves X.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the sponsor?"
        )
        fake_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
        }

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyProject", "1.2.3", "https://run")

        assert "# Solution overview" in doc
        assert "This solves X." in doc
        assert "---GAPS---" not in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the sponsor?" in gaps

    def test_missing_delimiter_falls_back_gracefully(self, sut, fake_shared):
        """When ---GAPS--- is absent, doc is full response and gaps is a fallback message."""
        fake_shared.call_claude.return_value = "Just a big blob of text with no delimiter."

        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run")

        assert doc == "Just a big blob of text with no delimiter."
        assert "could not extract" in gaps.lower() or gaps  # fallback message present

    def test_get_repo_files_called_with_expected_extensions(self, sut, fake_shared):
        """get_repo_files is invoked with the correct extension list and max_files."""
        sut.generate_biz_doc("owner", "repo", "Proj", "0.1.0", "url")

        fake_shared.get_repo_files.assert_called_once()
        args, kwargs = fake_shared.get_repo_files.call_args
        extensions = args[2] if len(args) >= 3 else kwargs.get("extensions", [])
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_owner_repo_in_user_message(self, sut, fake_shared):
        """The user message passed to Claude contains the repo identifier."""
        sut.generate_biz_doc("myowner", "myrepo", "MyProj", "2.0.0", "https://run")

        _, user_msg = fake_shared.call_claude.call_args[0]
        assert "myowner/myrepo" in user_msg

    def test_file_contents_truncated_to_3000_chars(self, sut, fake_shared):
        """Files longer than 3000 chars are truncated in the prompt."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}

        sut.generate_biz_doc("o", "r", "P", "1.0", "url")

        _, user_msg = fake_shared.call_claude.call_args[0]
        # The truncated block should contain exactly 3000 x's, not 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_prompt_contains_project_name(self, sut, fake_shared):
        """The system prompt passed to Claude contains the project name."""
        sut.generate_biz_doc("o", "r", "UnderwritingRiskPlatform", "1.0", "url")

        system_prompt, _ = fake_shared.call_claude.call_args[0]
        assert "UnderwritingRiskPlatform" in system_prompt

    def test_prompt_contains_version(self, sut, fake_shared):
        """The system prompt passed to Claude contains the version."""
        sut.generate_biz_doc("o", "r", "Proj", "3.7.2", "url")

        system_prompt, _ = fake_shared.call_claude.call_args[0]
        assert "3.7.2" in system_prompt

    def test_empty_repo_files(self, sut, fake_shared):
        """Empty file dict from get_repo_files doesn't crash generate_biz_doc."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "url")
        assert doc == "doc"
        assert gaps == "gaps"

    def test_delimiter_split_uses_first_occurrence_only(self, sut, fake_shared):
        """If ---GAPS--- appears multiple times, only the first split is used."""
        fake_shared.call_claude.return_value = (
            "DOC_PART---GAPS---GAPS_PART_ONE---GAPS---GAPS_PART_TWO"
        )

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "url")
        assert doc == "DOC_PART"
        assert "GAPS_PART_ONE---GAPS---GAPS_PART_TWO" in gaps

    def test_returns_stripped_strings(self, sut, fake_shared):
        """Leading/trailing whitespace is stripped from both doc and gaps."""
        fake_shared.call_claude.return_value = "  doc  \n---GAPS---\n  gaps  \n"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "url")
        assert doc == "doc"
        assert gaps == "gaps"

    def test_call_claude_called_exactly_once(self, sut, fake_shared):
        """call_claude is invoked exactly once per generate_biz_doc call."""
        sut.generate_biz_doc("o", "r", "P", "1.0", "url")
        assert fake_shared.call_claude.call_count == 1

    def test_multiple_files_appear_in_prompt(self, sut, fake_shared):
        """All files from get_repo_files appear in the Claude user message."""
        fake_shared.get_repo_files.return_value = {
            "file_a.py": "content_a",
            "file_b.tf": "content_b",
            "file_c.md": "content_c",
        }

        sut.generate_biz_doc("o", "r", "P", "1.0", "url")
        _, user_msg = fake_shared.call_claude.call_args[0]

        assert "file_a.py" in user_msg
        assert "file_b.tf" in user_msg
        assert "file_c.md" in user_msg
        assert "content_a" in user_msg
        assert "content_b" in user_msg
        assert "content_c" in user_msg

    def test_date_in_prompt_matches_utcnow(self, sut, fake_shared):
        """The formatted date injected into the prompt matches utcnow output."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            # Allow strftime on the mock return value
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            sut.generate_biz_doc("o", "r", "P", "1.0", "url")

        system_prompt, _ = fake_shared.call_claude.call_args[0]
        assert FIXED_DATE_STR in system_prompt

    @pytest.mark.parametrize("project_name,version", [
        ("Underwriting Risk Classification", "1.0.0"),
        ("MyApp", "0.0.1"),
        ("some-project_name.v2", "99.99.99"),
        ("", "1.0.0"),  # edge: empty project name
    ])
    def test_parameterised_project_name_version(self, sut, fake_shared, project_name, version):
        """generate_biz_doc handles various project name / version combinations."""
        fake_shared.call_claude.return_value = f"doc for {project_name}---GAPS---gap"
        doc, gaps = sut.generate_biz_doc("o", "r", project_name, version, "url")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)


# ===========================================================================
# build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def test_full_md_contains_doc_content(self, sut):
        """Full markdown includes the doc part."""
        full_md, _ = sut.build_full_output(
            "# My Doc\nSome content", "1. Gap question?",
            "owner", "repo", "MyProject", "1.0.0"
        )
        assert "# My Doc" in full_md
        assert "Some content" in full_md

    def test_full_md_contains_gaps(self, sut):
        """Full markdown includes the gap questionnaire section."""
        full_md, _ = sut.build_full_output(
            "doc", "1. Gap question?",
            "owner", "repo", "MyProject", "1.0.0"
        )
        assert "Gap Questionnaire" in full_md
        assert "1. Gap question?" in full_md

    def test_gap_only_md_contains_project_name(self, sut):
        """Standalone gap doc includes the project name in its heading."""
        _, gap_only = sut.build_full_output(
            "doc", "1. A question?",
            "owner", "repo", "UnderwritingRiskPlatform", "2.5.0"
        )
        assert "UnderwritingRiskPlatform" in gap_only

    def test_gap_only_md_contains_version(self, sut):
        """Standalone gap doc includes the version."""
        _, gap_only = sut.build_full_output(
            "doc", "1. A question?",
            "owner", "repo", "Proj", "3.1.4"
        )
        assert "3.1.4" in gap_only

    def test_gap_only_md_contains_gap_questions(self, sut):
        """Standalone gap doc includes the gap questions text."""
        _, gap_only = sut.build_full_output(
            "doc", "1. What is the go-live date?\n2. Who is the sponsor?",
            "owner", "repo", "Proj", "1.0.0"
        )
        assert "What is the go-live date?" in gap_only
        assert "Who is the sponsor?" in gap_only

    def test_full_md_contains_source_attribution(self, sut):
        """Full markdown includes the source attribution footer."""
        full_md, _ = sut.build_full_output(
            "doc", "gap",
            "myowner", "myrepo", "Proj", "1.0.0