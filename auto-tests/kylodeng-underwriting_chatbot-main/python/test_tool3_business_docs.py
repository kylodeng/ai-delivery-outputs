"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path with ---GAPS--- delimiter, missing delimiter fallback,
      file assembly, Claude call arguments
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content
      presence checks, boundary values (empty doc/gaps, long strings)
    - __main__ block behaviour: successful run (env vars, write/email/audit calls),
      failure path (exception → audit FAILED + email)

Mocks used:
    - shared.call_claude            — avoids real Anthropic/Claude API calls
    - shared.get_repo_files         — avoids real GitHub API calls
    - shared.write_output_file      — avoids real GitHub commit/push
    - shared.send_email             — avoids real SMTP/SES calls
    - shared.email_html             — avoids template rendering side-effects
    - shared.write_audit_entry      — avoids real audit log writes
    - datetime.datetime.utcnow      — pins timestamps for deterministic assertions
    - os.environ                    — controlled via monkeypatch

TODOs:
    - TODO: Test __main__ block exception branch email body content once
      the truncated source (email_html call in except) is completed.
    - TODO: Integration test against a real repo fixture once shared helpers
      expose a test-mode flag.
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake `shared` module so the import at module
# level in tool3_business_docs.py does not require the real file on sys.path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_module():
    """Return a fake `shared` module with all symbols consumed by tool3."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="Claude response")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """Inject fake shared module before every test and reload tool3."""
    shared = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", shared)
    yield shared


@pytest.fixture()
def tool3(fake_shared):
    """Import (or reload) tool3_business_docs with the fake shared in place."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import importlib
    # Remove cached version so each test gets a fresh import
    sys.modules.pop("tool3_business_docs", None)
    mod = importlib.import_module("tool3_business_docs")
    return mod


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic timestamp assertions
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 10, 30, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 10:30 UTC"


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, tool3, fake_shared):
        """Claude returns both sections separated by ---GAPS---."""
        doc_text = "# Solution overview\nSome content"
        gaps_text = "1. What is the go-live date?\n2. Who are the stakeholders?"
        fake_shared.get_repo_files.return_value = {
            "README.md": "# My Project\nThis is a test repo.",
            "main.py": "print('hello')",
        }
        fake_shared.call_claude.return_value = f"{doc_text}\n---GAPS---\n{gaps_text}"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DT
            mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
            doc, gaps = tool3.generate_biz_doc(
                "acme", "underwriting", "Underwriting Risk Classification", "1.0.0", "https://run"
            )

        assert doc == doc_text.strip()
        assert gaps == gaps_text.strip()

    def test_missing_delimiter_fallback(self, tool3, fake_shared):
        """When Claude omits ---GAPS---, gaps gets a fallback message."""
        fake_shared.get_repo_files.return_value = {"app.py": "x = 1"}
        fake_shared.call_claude.return_value = "# Solution overview\nNo delimiter here"

        doc, gaps = tool3.generate_biz_doc(
            "acme", "risk-model", "Risk Model", "0.1.0", "https://run"
        )

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, fake_shared):
        """Correct file extensions and max_files are forwarded to get_repo_files."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "content---GAPS---questions"

        tool3.generate_biz_doc("owner", "repo", "Proj", "0.2.0", "https://run")

        fake_shared.get_repo_files.assert_called_once()
        args, kwargs = fake_shared.get_repo_files.call_args
        assert args[0] == "owner"
        assert args[1] == "repo"
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert args[2] == expected_exts
        assert kwargs.get("max_files") == 20

    def test_call_claude_receives_project_name_and_version(self, tool3, fake_shared):
        """The formatted prompt passed to call_claude embeds project_name and version."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "---GAPS---"

        tool3.generate_biz_doc("o", "r", "My Special Project", "3.2.1", "https://run")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "My Special Project" in prompt_arg
        assert "3.2.1" in prompt_arg

    def test_call_claude_user_message_contains_repo_path(self, tool3, fake_shared):
        """The user message to Claude contains owner/repo."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "---GAPS---"

        tool3.generate_biz_doc("my-org", "my-repo", "Proj", "1.0.0", "https://run")

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "my-org/my-repo" in user_msg

    def test_files_content_included_in_user_message(self, tool3, fake_shared):
        """File contents from get_repo_files are embedded in the Claude user message."""
        fake_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "main.py": "import os",
        }
        fake_shared.call_claude.return_value = "doc---GAPS---gaps"

        tool3.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "model_card.json" in user_msg
        assert "Underwriting Risk Classification" in user_msg
        assert "main.py" in user_msg

    def test_empty_repo_no_error(self, tool3, fake_shared):
        """An empty repo (no files) still produces output without raising."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "# doc\n---GAPS---\n1. Q1?"

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "0.0.1", "https://run")

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_delimiter_appears_multiple_times_splits_on_first(self, tool3, fake_shared):
        """If ---GAPS--- appears more than once, only the first split is used."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = (
            "DOC PART\n---GAPS---\nGAPS PART\n---GAPS---\nEXTRA"
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0.0", "https://run")

        assert doc == "DOC PART"
        assert "GAPS PART" in gaps
        assert "EXTRA" in gaps  # everything after first delimiter is gaps

    def test_whitespace_only_response(self, tool3, fake_shared):
        """Whitespace-only Claude response is handled gracefully."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "   \n  "

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0.0", "https://run")

        assert isinstance(doc, str)
        assert "Claude could not extract" in gaps

    def test_date_included_in_prompt(self, tool3, fake_shared):
        """Today's date (YYYY-MM-DD) is embedded in the Claude prompt."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "---GAPS---"

        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value.strftime.return_value = FIXED_DATE_STR
            tool3.generate_biz_doc("o", "r", "P", "1.0.0", "https://run")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert FIXED_DATE_STR in prompt_arg


# ===========================================================================
# Tests for build_full_output
# ===========================================================================

class TestBuildFullOutput:

    @pytest.fixture()
    def sample_inputs(self):
        return dict(
            doc="# Solution overview\nSome great content.",
            gaps="1. What is the go-live date?\n2. Who owns the budget?",
            owner="acme",
            repo="underwriting",
            project_name="Underwriting Risk Classification",
            version="1.2.3",
        )

    def test_returns_tuple_of_two_strings(self, tool3, sample_inputs):
        result = tool3.build_full_output(**sample_inputs)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_full_md_contains_doc_content(self, tool3, sample_inputs):
        full_md, _ = tool3.build_full_output(**sample_inputs)
        assert "Solution overview" in full_md
        assert "Some great content" in full_md

    def test_full_md_contains_gaps(self, tool3, sample_inputs):
        full_md, _ = tool3.build_full_output(**sample_inputs)
        assert "What is the go-live date?" in full_md
        assert "Who owns the budget?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, tool3, sample_inputs):
        full_md, _ = tool3.build_full_output(**sample_inputs)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool3, sample_inputs):
        full_md, _ = tool3.build_full_output(**sample_inputs)
        assert "acme/underwriting" in full_md
        assert "1.2.3" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3, sample_inputs):
        _, gap_only = tool3.build_full_output(**sample_inputs)
        assert "Underwriting Risk Classification" in gap_only
        assert "1.2.3" in gap_only

    def test_gap_only_md_contains_gap_questions(self, tool3, sample_inputs):
        _, gap_only = tool3.build_full_output(**sample_inputs)
        assert "What is the go-live date?" in gap_only
        assert "Who owns the budget?" in gap_only

    def test_gap_only_md_links_to_output_repo(self, tool3, sample_inputs):
        _, gap_only = tool3.build_full_output(**sample_inputs)
        assert FAKE_OUTPUT_REPO_OWNER in gap_only
        assert FAKE_OUTPUT_REPO in gap_only

    def test_timestamp_present_in_outputs(self, tool3, sample_inputs):
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value.strftime.return_value = FIXED_DATETIME_STR
            full_md, gap_only = tool3.build_full_output(**sample_inputs)

        assert FIXED_DATETIME_STR in full_md
        assert FIXED_DATETIME_STR in gap_only

    def test_empty_doc_and_gaps(self, tool3):
        """Edge case: empty strings for doc and gaps should not raise."""
        full_md, gap_only = tool3.build_full_output(
            doc="", gaps="", owner="o", repo="r",
            project_name="P", version="0.0.0"
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_very_long_doc_string(self, tool3):
        """Build full output handles very large doc strings without truncation."""
        long_doc = "# Doc\n" + ("A" * 50000)
        full_md, _ = tool3.build_full_output(
            doc=long_doc, gaps="1. Q?", owner="o", repo="r",
            project_name="LargeProject", version="1.0.0"
        )
        assert "A" * 100 in full_md  # content is preserved

    def test_special_characters_in_project_name(self, tool3):
        """Project names with special characters are embedded correctly."""
        full_md, gap_only = tool3.build_full_output(
            doc="# Doc", gaps="1. Q?", owner="o", repo="r",
            project_name="Résumé & Co. <Risk>", version="1.0.0"
        )
        assert "Résumé & Co. <Risk>" in gap_only

    @pytest.mark.parametrize("version", ["0.0.1", "1