"""
Tests for tool3_business_docs.py
=================================
What is tested:
  - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude error propagation
  - build_full_output(): full markdown assembly, gap-only markdown assembly, content correctness
  - __main__ block execution via subprocess / monkeypatching env vars + module-level calls
  - Edge cases: missing delimiter, empty gaps, whitespace-only parts, special characters in names

Mocks used:
  - shared.call_claude          → unittest.mock.patch
  - shared.get_repo_files       → unittest.mock.patch
  - shared.write_output_file    → unittest.mock.patch
  - shared.send_email           → unittest.mock.patch
  - shared.email_html           → unittest.mock.patch
  - shared.write_audit_entry    → unittest.mock.patch
  - datetime.datetime.utcnow    → unittest.mock.patch (fixed timestamp)
  - os.environ                  → monkeypatch (pytest fixture)

TODOs:
  - TODO: Integration test against a real GitHub repo (requires GH_TOKEN secret)
  - TODO: Test __main__ exit-code behaviour when write_output_file raises (needs refactor for testability)
"""

import sys
import os
import importlib
import types
from unittest.mock import patch, MagicMock, call
import datetime

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake `shared` module so the import in
# tool3_business_docs.py doesn't blow up when the real shared.py isn't present
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-repo"


def _make_fake_shared():
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="Claude response")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>body</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject a fake `shared` module before every test and reload the SUT."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)

    # Ensure the scripts directory is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        monkeypatch.syspath_prepend(scripts_dir)

    # Remove any previously cached import of the SUT
    sys.modules.pop("tool3_business_docs", None)

    yield fake


@pytest.fixture()
def sut(fake_shared):
    """Return the (freshly imported) module under test."""
    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic output
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_NOW_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def fixed_datetime():
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FIXED_DT
        mock_dt.datetime.utcnow.return_value.strftime = FIXED_DT.strftime
        # Make strftime work correctly on the mock
        mock_dt.datetime.utcnow = MagicMock(return_value=FIXED_DT)
        yield mock_dt


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

SAMPLE_DOC = """# Solution overview: Underwriting Risk Classification
**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution classifies insurance underwriting risk using a CatBoost model.
"""

SAMPLE_GAPS = """1. What is the target go-live date?
2. Who is the business sponsor?
3. What is the data retention policy?"""

SAMPLE_RAW_WITH_DELIMITER = f"{SAMPLE_DOC}---GAPS---{SAMPLE_GAPS}"
SAMPLE_RAW_WITHOUT_DELIMITER = f"{SAMPLE_DOC}\n\nNo delimiter here."


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, sut, fake_shared):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        fake_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "README.md": "# My Project",
        }
        fake_shared.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        doc, gaps = sut.generate_biz_doc("acme", "risk-engine", "Risk Engine", "1.0.0", "https://run")

        assert "Solution overview" in doc
        assert "What is the target go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_delimiter(self, sut, fake_shared):
        """When Claude omits ---GAPS---, doc gets the full raw output and gaps gets fallback message."""
        fake_shared.get_repo_files.return_value = {"README.md": "# Project"}
        fake_shared.call_claude.return_value = SAMPLE_RAW_WITHOUT_DELIMITER

        doc, gaps = sut.generate_biz_doc("acme", "risk-engine", "Risk Engine", "1.0.0", "https://run")

        assert "No delimiter here." in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, sut, fake_shared):
        """get_repo_files is invoked with the expected file extensions."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"

        sut.generate_biz_doc("owner", "repo", "proj", "0.1.0", "https://run")

        args, kwargs = fake_shared.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_project_info_in_prompt(self, sut, fake_shared):
        """The prompt passed to Claude includes project_name and version."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"

        sut.generate_biz_doc("owner", "repo", "MyProject", "2.3.4", "https://run")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "MyProject" in prompt_arg
        assert "2.3.4" in prompt_arg

    def test_call_claude_receives_file_contents_in_user_message(self, sut, fake_shared):
        """File contents appear in the user-facing message to Claude."""
        fake_shared.get_repo_files.return_value = {
            "model_card.json": '{"model_name": "Underwriting Risk Classification"}'
        }
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"

        sut.generate_biz_doc("owner", "repo", "proj", "1.0.0", "https://run")

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "model_card.json" in user_msg
        assert "Underwriting Risk Classification" in user_msg

    def test_doc_and_gaps_are_stripped(self, sut, fake_shared):
        """Leading/trailing whitespace is stripped from both outputs."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "  \n  doc content  \n  ---GAPS---  \n  gaps content  \n  "

        doc, gaps = sut.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "doc content"
        assert gaps == "gaps content"

    def test_empty_repo_files(self, sut, fake_shared):
        """No files in repo — Claude still called, response still parsed."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "Some doc---GAPS---Some gaps"

        doc, gaps = sut.generate_biz_doc("o", "r", "p", "1.0", "u")

        assert doc == "Some doc"
        assert gaps == "Some gaps"

    def test_multiple_delimiter_occurrences_splits_on_first(self, sut, fake_shared):
        """Only the first ---GAPS--- is used as split point."""
        fake_shared.get_repo_files.return_value = {}
        raw = "doc part---GAPS---first gaps---GAPS---second gaps"
        fake_shared.call_claude.return_value = raw

        doc, gaps = sut.generate_biz_doc("o", "r", "p", "1.0", "u")

        assert doc == "doc part"
        assert "first gaps---GAPS---second gaps" in gaps

    def test_claude_exception_propagates(self, sut, fake_shared):
        """Exceptions raised by call_claude bubble up to the caller."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("Claude API failure")

        with pytest.raises(RuntimeError, match="Claude API failure"):
            sut.generate_biz_doc("o", "r", "p", "1.0", "u")

    def test_get_repo_files_exception_propagates(self, sut, fake_shared):
        """Exceptions raised by get_repo_files bubble up."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub timeout")

        with pytest.raises(ConnectionError, match="GitHub timeout"):
            sut.generate_biz_doc("o", "r", "p", "1.0", "u")

    def test_large_file_content_truncated_to_3000_chars(self, sut, fake_shared):
        """Files longer than 3000 chars are sliced before being sent to Claude."""
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"

        sut.generate_biz_doc("o", "r", "p", "1.0", "u")

        user_msg = fake_shared.call_claude.call_args[0][1]
        # The content in the message must be at most 3000 x-chars
        x_count = user_msg.count("x")
        assert x_count <= 3000

    def test_special_characters_in_project_name(self, sut, fake_shared):
        """Special characters in project_name don't cause format errors."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"

        # Should not raise
        doc, gaps = sut.generate_biz_doc("o", "r", "Project & Co. <Test>", "1.0", "u")
        assert doc == "doc"

    @pytest.mark.skip(reason="TODO: need a real GitHub token to test against live repo")
    def test_integration_with_real_github_repo(self):
        """TODO: Integration test — requires GH_TOKEN and network access."""
        pass


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_full_md_contains_doc_section(self, sut):
        full_md, _ = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert "Solution overview" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, sut):
        full_md, _ = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self, sut):
        full_md, _ = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert "What is the target go-live date?" in full_md

    def test_full_md_contains_source_attribution(self, sut):
        full_md, _ = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert "acme/risk" in full_md
        assert "v1.0.0" in full_md

    def test_full_md_contains_ai_delivery_bot_footer(self, sut):
        full_md, _ = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, sut):
        _, gap_only = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert "Risk Engine" in gap_only
        assert "v1.0.0" in gap_only

    def test_gap_only_md_contains_gaps_content(self, sut):
        _, gap_only = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert "What is the target go-live date?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, sut):
        _, gap_only = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        )
        assert FAKE_OUTPUT_REPO_OWNER in gap_only
        assert FAKE_OUTPUT_REPO in gap_only

    def test_gap_only_md_does_not_contain_solution_overview_body(self, sut):
        """The gap-only document should not re-include the full solution overview."""
        _, gap_only = sut.build_full_output(
            SAMPLE_DOC, SAMPLE_GAPS, "acme", "risk", "Risk Engine", "1.0.0"
        