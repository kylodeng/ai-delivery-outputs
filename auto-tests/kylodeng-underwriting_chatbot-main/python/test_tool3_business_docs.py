"""
Tests for tool3_business_docs.py
=================================
What is tested:
  - generate_biz_doc(): happy path, ---GAPS--- present, ---GAPS--- absent, Claude API variations
  - build_full_output(): full markdown structure, standalone gap questionnaire structure,
    correct insertion of doc/gaps/metadata
  - __main__ block: environment variable handling, success path, exception/failure path

Mocks used:
  - shared.call_claude          — patched to return controlled strings
  - shared.get_repo_files       — patched to return controlled file dict
  - shared.write_output_file    — patched to return a fake URL
  - shared.send_email           — patched to no-op
  - shared.email_html           — patched to return a fake HTML string
  - shared.write_audit_entry    — patched to no-op
  - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
  - TODO: Integration test against a real Claude model (requires API key + billing)
  - TODO: Test behaviour when get_repo_files returns files larger than 3000 chars (truncation)
  - TODO: Test __main__ block send_email FAILED path fully (incomplete source snippet cuts off)
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal `shared` stub so the import doesn't fail
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="Doc content\n---GAPS---\n1. A question?")
    shared.get_repo_files     = MagicMock(return_value={})
    shared.write_output_file  = MagicMock(return_value="https://github.com/output/repo/blob/main/file.md")
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-output-owner"
    shared.OUTPUT_REPO        = "test-output-repo"
    return shared


@pytest.fixture(autouse=True)
def stub_shared(monkeypatch):
    """Insert a fresh shared stub into sys.modules before every test."""
    shared = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", shared)
    return shared


@pytest.fixture()
def tool3(stub_shared):
    """Import (or reload) the module under test after the stub is in place."""
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    import importlib.util, pathlib
    source_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool3_business_docs.py"

    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]

    spec = importlib.util.spec_from_file_location("tool3_business_docs", source_path)
    mod  = importlib.util.module_from_spec(spec)
    sys.modules["tool3_business_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic output
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 10, 30, 0)
FIXED_DATE     = "2024-06-15"
FIXED_DATETIME = "2024-06-15 10:30 UTC"


@pytest.fixture()
def fixed_utcnow():
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        # Make strftime calls on the return value work correctly
        mock_dt.utcnow.side_effect = None
        mock_dt.utcnow.return_value = FIXED_DT
        yield mock_dt


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, tool3, stub_shared):
        """Claude returns a response with ---GAPS--- delimiter — both parts extracted."""
        stub_shared.get_repo_files.return_value = {
            "README.md": "# My Project\nSome description.",
            "main.py":   "print('hello')",
        }
        stub_shared.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content here."
            "\n---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the business sponsor?"
        )

        doc, gaps = tool3.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0",
            run_url="https://github.com/run/123"
        )

        assert "# Solution overview: MyProject" in doc
        assert "Some content here." in doc
        assert "---GAPS---" not in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the business sponsor?" in gaps

    def test_no_gaps_delimiter_fallback(self, tool3, stub_shared):
        """Claude returns response WITHOUT ---GAPS--- — fallback message used for gaps."""
        stub_shared.call_claude.return_value = "Just the doc, no delimiter at all."

        doc, gaps = tool3.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0",
            run_url="https://github.com/run/123"
        )

        assert doc == "Just the doc, no delimiter at all."
        assert "Claude could not extract gap questions" in gaps

    def test_gaps_delimiter_only_once(self, tool3, stub_shared):
        """Only the first ---GAPS--- is used as the split point."""
        stub_shared.call_claude.return_value = (
            "Doc part\n---GAPS---\nGap part\n---GAPS---\nExtra stuff"
        )

        doc, gaps = tool3.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0",
            run_url="https://github.com/run/123"
        )

        assert doc == "Doc part"
        # second delimiter and everything after it stays in gaps
        assert "Gap part" in gaps
        assert "Extra stuff" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, stub_shared):
        """get_repo_files is called with the expected file extensions."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc(
            owner="owner1", repo="repo1",
            project_name="Proj", version="0.1",
            run_url=""
        )

        call_args = stub_shared.get_repo_files.call_args
        assert call_args[0][0] == "owner1"
        assert call_args[0][1] == "repo1"
        extensions = call_args[0][2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions
        assert call_args[1].get("max_files") == 20 or call_args[0][3] == 20 \
               or "max_files" in call_args[1] and call_args[1]["max_files"] == 20

    def test_call_claude_receives_project_name_in_prompt(self, tool3, stub_shared):
        """The Claude prompt should contain the project_name."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc(
            owner="owner1", repo="repo1",
            project_name="InsuranceUnderwriting", version="2.3.1",
            run_url=""
        )

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert "InsuranceUnderwriting" in prompt_arg

    def test_call_claude_receives_version_in_prompt(self, tool3, stub_shared):
        """The Claude prompt should contain the version string."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc(
            owner="owner1", repo="repo1",
            project_name="Proj", version="3.1.4",
            run_url=""
        )

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert "3.1.4" in prompt_arg

    def test_call_claude_receives_repo_context(self, tool3, stub_shared):
        """The second argument to call_claude includes owner/repo."""
        stub_shared.get_repo_files.return_value = {"app.py": "x=1"}
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc(
            owner="myorg", repo="myrepo",
            project_name="Proj", version="1.0",
            run_url=""
        )

        context_arg = stub_shared.call_claude.call_args[0][1]
        assert "myorg/myrepo" in context_arg

    def test_repo_files_included_in_context(self, tool3, stub_shared):
        """File content from get_repo_files appears in the Claude context string."""
        stub_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}'
        }
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc(
            owner="acme", repo="risk-engine",
            project_name="RiskEngine", version="1.0",
            run_url=""
        )

        context_arg = stub_shared.call_claude.call_args[0][1]
        assert "backend/model_card.json" in context_arg
        assert "Underwriting Risk Classification" in context_arg

    def test_empty_repo_files(self, tool3, stub_shared):
        """Empty file dict still calls Claude (with empty files section)."""
        stub_shared.get_repo_files.return_value = {}
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc(
            owner="acme", repo="empty-repo",
            project_name="Proj", version="0.0.1",
            run_url=""
        )

        assert stub_shared.call_claude.called
        assert doc == "doc"
        assert gaps == "gaps"

    def test_output_stripped_of_whitespace(self, tool3, stub_shared):
        """Leading/trailing whitespace is stripped from both doc and gaps parts."""
        stub_shared.call_claude.return_value = (
            "   \n  doc content \n  \n---GAPS---\n  \n  gap content  \n  "
        )

        doc, gaps = tool3.generate_biz_doc(
            owner="a", repo="b", project_name="P", version="1", run_url=""
        )

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_call_claude_raises_propagates(self, tool3, stub_shared):
        """If call_claude raises, generate_biz_doc propagates the exception."""
        stub_shared.call_claude.side_effect = RuntimeError("API failure")

        with pytest.raises(RuntimeError, match="API failure"):
            tool3.generate_biz_doc(
                owner="a", repo="b", project_name="P", version="1", run_url=""
            )

    def test_date_included_in_prompt(self, tool3, stub_shared):
        """Today's date is formatted into the prompt."""
        stub_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        tool3.generate_biz_doc(
            owner="a", repo="b", project_name="P", version="1", run_url=""
        )

        prompt_arg = stub_shared.call_claude.call_args[0][0]
        assert today in prompt_arg


# ===========================================================================
# Tests for build_full_output
# ===========================================================================

class TestBuildFullOutput:

    SAMPLE_DOC  = "# Solution overview: TestProject\nSome executive summary."
    SAMPLE_GAPS = "1. What is the go-live date?\n2. Who is the business sponsor?"

    def test_full_md_contains_doc(self, tool3):
        full_md, _ = tool3.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            "acme", "my-repo", "TestProject", "1.0.0"
        )
        assert "# Solution overview: TestProject" in full_md
        assert "Some executive summary." in full_md

    def test_full_md_contains_gap_questionnaire_section(self, tool3):
        full_md, _ = tool3.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            "acme", "my-repo", "TestProject", "1.0.0"
        )
        assert "## Gap Questionnaire" in full_md
        assert self.SAMPLE_GAPS in full_md

    def test_full_md_contains_source_attribution(self, tool3):
        full_md, _ = tool3.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            "acme", "my-repo", "TestProject", "1.0.0"
        )
        assert "acme/my-repo" in full_md
        assert "1.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3):
        _, gap_only_md = tool3.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            "acme", "my-repo", "TestProject", "2.5.0"
        )
        assert "TestProject" in gap_only_md
        assert "2.5.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, tool3):
        _, gap_only_md = tool3.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            "acme", "my-repo", "TestProject", "1.0.0"
        )
        assert "1. What is the go-live date?" in gap_only_md
        assert "2. Who is the business sponsor?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, tool3):
        _, gap_only_md = tool3.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            "acme", "my-repo", "TestProject", "1.0.0"
        )
        # Should reference OUTPUT_REPO_OWNER / OUTPUT_REPO from shared stub
        assert "test-output-owner" in gap_only_md