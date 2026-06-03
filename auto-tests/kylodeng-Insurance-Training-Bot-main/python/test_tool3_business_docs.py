"""
Test module for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude response handling
  - build_full_output(): full markdown assembly, gap-only markdown assembly, edge cases
  - __main__ block: environment variable handling, success flow, exception/failure flow

Mocks used:
  - shared.call_claude          → patched to return synthetic Claude responses
  - shared.get_repo_files       → patched to return synthetic file dictionaries
  - shared.write_output_file    → patched to return a fake URL string
  - shared.send_email           → patched to be a no-op
  - shared.email_html           → patched to return a dummy HTML string
  - shared.write_audit_entry    → patched to be a no-op
  - datetime.datetime.utcnow    → patched to return a deterministic timestamp
  - os.environ                  → manipulated via monkeypatch / unittest.mock

TODOs:
  - TODO: Integration test verifying the actual Claude prompt template interpolation
          end-to-end with a real (sandboxed) Claude API key.
  - TODO: Test the truncated source file tail (the email_html call in the except branch
          is cut off in source — add test once source is complete).
  - TODO: Add tests for write_output_file path-construction logic once its signature is
          fully known from shared.py.
"""

import sys
import os
import types
import datetime
import importlib
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so we can import the SUT
# without the real dependency being present.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO       = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module."""
    mod = types.ModuleType("shared")
    mod.call_claude       = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    mod.get_repo_files    = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/output/blob/main/doc.md")
    mod.send_email        = MagicMock()
    mod.email_html        = MagicMock(return_value="<html>ok</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO       = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fresh fake `shared` into sys.modules before every test and
    reload the SUT so it picks up the mock.
    """
    shared_mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", shared_mod)

    # Force a clean import of the SUT each test
    sut_name = "tool3_business_docs"
    if sut_name in sys.modules:
        del sys.modules[sut_name]

    # Add the scripts directory to path so the import succeeds
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        monkeypatch.syspath_prepend(scripts_dir)

    yield shared_mod


@pytest.fixture()
def sut(fake_shared):
    """Import (or re-import) the SUT and return it."""
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic assertions
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR        = "2024-06-15"
FIXED_DATETIME_STR    = "2024-06-15 12:00 UTC"


@pytest.fixture()
def patch_utcnow():
    """Patch datetime.datetime.utcnow inside the SUT module."""
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FIXED_DT
        # Make strftime delegate to the real datetime object
        mock_dt.datetime.utcnow.return_value.strftime = FIXED_DT.strftime
        yield mock_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, sut, fake_shared, patch_utcnow):
        """Claude returns a response that contains ---GAPS---; both parts split correctly."""
        fake_shared.call_claude.return_value = (
            "# Solution overview: MyProject\nSome doc text.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the sponsor?"
        )
        fake_shared.get_repo_files.return_value = {"main.py": "print('hello')"}

        doc, gaps = sut.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        assert "# Solution overview: MyProject" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the sponsor?" in gaps

    def test_happy_path_without_gaps_delimiter(self, sut, fake_shared, patch_utcnow):
        """Claude returns a response with no ---GAPS--- delimiter; fallback message used."""
        fake_shared.call_claude.return_value = "Just a plain doc with no delimiter."

        doc, gaps = sut.generate_biz_doc("owner", "repo", "ProjectX", "2.0", "https://run")

        assert doc == "Just a plain doc with no delimiter."
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared, patch_utcnow):
        """get_repo_files is called with the expected file extensions and max_files."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sut.generate_biz_doc("myowner", "myrepo", "P", "0.1", "url")

        fake_shared.get_repo_files.assert_called_once()
        args, kwargs = fake_shared.get_repo_files.call_args
        assert args[0] == "myowner"
        assert args[1] == "myrepo"
        extensions = args[2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_project_name_in_prompt(self, sut, fake_shared, patch_utcnow):
        """The formatted prompt passed to call_claude contains the project name."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sut.generate_biz_doc("o", "r", "InsuranceBot", "3.1", "url")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "InsuranceBot" in prompt_arg

    def test_call_claude_receives_version_in_prompt(self, sut, fake_shared, patch_utcnow):
        """The formatted prompt passed to call_claude contains the version."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sut.generate_biz_doc("o", "r", "P", "4.2.1", "url")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "4.2.1" in prompt_arg

    def test_call_claude_second_arg_contains_owner_repo(self, sut, fake_shared, patch_utcnow):
        """The user message arg to call_claude includes the owner/repo string."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sut.generate_biz_doc("acme", "widget-service", "W", "1.0", "url")

        user_msg_arg = fake_shared.call_claude.call_args[0][1]
        assert "acme/widget-service" in user_msg_arg

    def test_multiple_gaps_delimiters_only_first_split(self, sut, fake_shared, patch_utcnow):
        """If ---GAPS--- appears more than once, only the first occurrence is used to split."""
        fake_shared.call_claude.return_value = (
            "doc part\n---GAPS---\ngaps part\n---GAPS---\nextra stuff"
        )

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == "doc part"
        assert "gaps part" in gaps
        assert "extra stuff" in gaps  # second part goes into gaps verbatim

    def test_empty_file_list(self, sut, fake_shared, patch_utcnow):
        """generate_biz_doc handles an empty file dictionary gracefully."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1.0", "u")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_large_file_content_truncated_in_prompt(self, sut, fake_shared, patch_utcnow):
        """Files with >3000 chars are sliced before being passed to Claude."""
        big_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big.py": big_content}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sut.generate_biz_doc("o", "r", "P", "1.0", "u")

        user_msg = fake_shared.call_claude.call_args[0][1]
        # The truncated slice is 3000 chars; the full 10000-char content must NOT appear
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_gaps_only_whitespace_stripped(self, sut, fake_shared, patch_utcnow):
        """Leading/trailing whitespace is stripped from both doc and gaps."""
        fake_shared.call_claude.return_value = "  doc text  \n---GAPS---\n  gap text  \n"

        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == "doc text"
        assert gaps == "gap text"

    @pytest.mark.parametrize("project_name,version", [
        ("Generations II", "1.0.0"),
        ("Designated Hospitals", "2.3"),
        ("VIP Medical Navigation", "0.0.1"),
        ("Global Cashless Network", "10.0"),
    ])
    def test_various_project_names_and_versions(self, sut, fake_shared, patch_utcnow, project_name, version):
        """Parametrised: various project names and versions from synthetic data samples."""
        fake_shared.call_claude.return_value = f"# {project_name}\n---GAPS---\n1. Question?"

        doc, gaps = sut.generate_biz_doc("sunlife", "insurance-repo", project_name, version, "u")

        assert project_name in doc or project_name in fake_shared.call_claude.call_args[0][0]
        assert "1. Question?" in gaps


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def test_full_md_contains_doc_content(self, sut, patch_utcnow):
        """Full output markdown contains the original doc content."""
        full_md, _ = sut.build_full_output(
            "# Solution Overview\nSome text.",
            "1. A question?",
            "owner", "repo", "MyProject", "1.0.0"
        )
        assert "# Solution Overview" in full_md
        assert "Some text." in full_md

    def test_full_md_contains_gap_questionnaire_section(self, sut, patch_utcnow):
        """Full output markdown includes the Gap Questionnaire heading."""
        full_md, _ = sut.build_full_output(
            "doc", "1. Gap question?", "o", "r", "P", "1.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self, sut, patch_utcnow):
        """Full output markdown includes the gap questions themselves."""
        full_md, _ = sut.build_full_output(
            "doc", "1. Who is the sponsor?\n2. What is the go-live date?",
            "o", "r", "P", "1.0"
        )
        assert "1. Who is the sponsor?" in full_md
        assert "2. What is the go-live date?" in full_md

    def test_full_md_contains_source_attribution(self, sut, patch_utcnow):
        """Full output markdown attribution line includes owner/repo and version."""
        full_md, _ = sut.build_full_output("doc", "gaps", "myowner", "myrepo", "P", "2.5")
        assert "myowner/myrepo" in full_md
        assert "2.5" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, sut, patch_utcnow):
        """Gap-only markdown heading contains project name and version."""
        _, gap_only = sut.build_full_output(
            "doc", "1. A question?", "o", "r", "InsurancePlan", "3.0"
        )
        assert "InsurancePlan" in gap_only
        assert "3.0" in gap_only

    def test_gap_only_md_contains_gaps(self, sut, patch_utcnow):
        """Gap-only markdown contains the gap questions."""
        _, gap_only = sut.build_full_output(
            "doc", "1. What is the retention period?", "o", "r", "P", "1"
        )
        assert "1. What is the retention period?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, sut, patch_utcnow):
        """Gap-only markdown contains a link referencing the output repo constants."""
        _, gap_only = sut.build_full_output("doc", "gaps", "o", "r", "P", "1")
        assert FAKE_OUTPUT_REPO_OWNER in gap_only or FAKE_OUTPUT_REPO in gap_only

    def test_full_md_contains_auto_generated_timestamp(self, sut, patch_utcnow):
        """Full output markdown contains the fixed UTC timestamp."""
        full_md, _ = sut.build_full_output("doc", "gaps", "o", "r", "P", "1")
        assert FIXED_DATETIME_STR in full_md

    def test_gap_only_md_contains_auto_generated_timestamp(self, sut, patch_utcnow):
        """