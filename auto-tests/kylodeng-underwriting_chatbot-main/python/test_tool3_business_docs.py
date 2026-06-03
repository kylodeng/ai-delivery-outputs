"""
Tests for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path, delimiter present/absent, truncation of file content
  - build_full_output(): structure/content of full_md and gap_only_md outputs

Mocks used:
  - shared.call_claude          (patched at tool3_business_docs.call_claude)
  - shared.get_repo_files       (patched at tool3_business_docs.get_repo_files)
  - shared.write_output_file    (patched at tool3_business_docs.write_output_file)
  - shared.send_email           (patched at tool3_business_docs.send_email)
  - shared.email_html           (patched at tool3_business_docs.email_html)
  - shared.write_audit_entry    (patched at tool3_business_docs.write_audit_entry)
  - datetime.datetime.utcnow    (patched to return a fixed timestamp)

TODOs:
  - TODO: Integration test for __main__ block requires subprocess execution + live env vars
  - TODO: Test email failure handling path (send_email raises) once error-branch code is confirmed complete
  - TODO: Validate SYSTEM prompt formatting more thoroughly once stakeholder field rules are confirmed
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test after injecting a fake `shared`
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a minimal fake `shared` module so the import doesn't fail."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="doc text\n---GAPS---\n1. Question one?")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/url")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture()
def module_under_test():
    """
    Import (or re-import) tool3_business_docs with a patched `shared` module.
    Yields the module and the fake shared for assertion purposes.
    """
    fake_shared = _make_fake_shared()

    # Make sure sys.path contains the scripts directory so the real import
    # path (os.path.insert) logic in the module is satisfied.
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    with patch.dict(sys.modules, {"shared": fake_shared}):
        # Force fresh import
        module_name = "tool3_business_docs"
        if module_name in sys.modules:
            del sys.modules[module_name]

        spec_path = os.path.join(
            os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
        )
        spec = importlib.util.spec_from_file_location(module_name, spec_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        spec.loader.exec_module(mod)

        yield mod, fake_shared

    # Cleanup
    sys.modules.pop(module_name, None)


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic tests
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================


class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, module_under_test):
        """Claude returns a response containing ---GAPS--- delimiter."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "README.md": "# My project",
        }
        fake_shared.call_claude.return_value = (
            "# Solution overview: TestProj\nSome doc content.\n"
            "---GAPS---\n"
            "1. What is the target go-live date?\n"
            "2. Who are the key users?\n"
        )

        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_DT
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_DT.strftime
            doc, gaps = mod.generate_biz_doc("myorg", "myrepo", "TestProj", "1.0.0", "https://run")

        assert "Solution overview" in doc
        assert "1. What is the target go-live date?" in gaps
        assert "2. Who are the key users?" in gaps
        # Delimiter itself must NOT appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_no_delimiter(self, module_under_test):
        """Claude returns a response WITHOUT ---GAPS--- — fallback message applied."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {"app.py": "print('hello')"}
        fake_shared.call_claude.return_value = "Just a plain document with no gaps marker."

        doc, gaps = mod.generate_biz_doc("o", "r", "NoDelimProj", "0.1.0", "https://run")

        assert doc == "Just a plain document with no gaps marker."
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, module_under_test):
        """Ensures get_repo_files is invoked with expected extension list and max_files."""
        mod, fake_shared = module_under_test
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        args, kwargs = fake_shared.get_repo_files.call_args
        assert args[0] == "owner"
        assert args[1] == "repo"
        assert ".py" in args[2]
        assert ".tf" in args[2]
        assert ".md" in args[2]
        assert kwargs.get("max_files") == 20

    def test_file_content_truncated_to_3000_chars(self, module_under_test):
        """File content longer than 3000 chars must be sliced before being sent to Claude."""
        mod, fake_shared = module_under_test
        long_content = "x" * 5000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        _, prompt_files_str = fake_shared.call_claude.call_args[0]
        # The truncated version should appear, not the full 5000-char version
        assert "x" * 3000 in prompt_files_str
        assert "x" * 3001 not in prompt_files_str

    def test_prompt_contains_project_name_version_date(self, module_under_test):
        """The system prompt passed to call_claude must be formatted with project_name, version, date."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_DT
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_DT.strftime
            mod.generate_biz_doc("o", "r", "UnderwritingTool", "2.3.1", "url")

        system_prompt = fake_shared.call_claude.call_args[0][0]
        assert "UnderwritingTool" in system_prompt
        assert "2.3.1" in system_prompt
        assert FIXED_DATE_STR in system_prompt

    def test_call_claude_user_message_contains_owner_repo(self, module_under_test):
        """The user message passed to call_claude must contain owner/repo context."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("acme-corp", "risk-engine", "P", "1.0", "url")

        user_message = fake_shared.call_claude.call_args[0][1]
        assert "acme-corp" in user_message
        assert "risk-engine" in user_message

    def test_empty_repo_files(self, module_under_test):
        """Empty file list should still produce a valid call to Claude."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "doc"
        assert gaps == "gap"

    def test_delimiter_only_splits_on_first_occurrence(self, module_under_test):
        """If ---GAPS--- appears multiple times, split on first occurrence only."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = (
            "doc content\n---GAPS---\nfirst gap\n---GAPS---\nsecond gap"
        )

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "doc content"
        assert "first gap" in gaps
        assert "second gap" in gaps  # second occurrence stays in gaps section

    def test_whitespace_stripped_from_parts(self, module_under_test):
        """Leading/trailing whitespace should be stripped from doc and gaps."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "  doc  \n---GAPS---\n  gaps  \n"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_call_claude_exception_propagates(self, module_under_test):
        """If call_claude raises, the exception should propagate out of generate_biz_doc."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("API failure")

        with pytest.raises(RuntimeError, match="API failure"):
            mod.generate_biz_doc("o", "r", "P", "1.0", "url")

    def test_get_repo_files_exception_propagates(self, module_under_test):
        """If get_repo_files raises, the exception should propagate."""
        mod, fake_shared = module_under_test
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_biz_doc("o", "r", "P", "1.0", "url")


# ===========================================================================
# Tests for build_full_output
# ===========================================================================


class TestBuildFullOutput:

    def _call(self, mod, doc="# Doc", gaps="1. Question?",
              owner="myorg", repo="myrepo", project="TestProj", version="1.2.3"):
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FIXED_DT
            mock_dt.datetime.utcnow.return_value.strftime = FIXED_DT.strftime
            return mod.build_full_output(doc, gaps, owner, repo, project, version)

    def test_returns_two_strings(self, module_under_test):
        mod, _ = module_under_test
        result = self._call(mod)
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_original_doc(self, module_under_test):
        mod, _ = module_under_test
        full_md, _ = self._call(mod, doc="# Solution overview: TestProj")
        assert "# Solution overview: TestProj" in full_md

    def test_full_md_contains_gaps(self, module_under_test):
        mod, _ = module_under_test
        full_md, _ = self._call(mod, gaps="1. What is the go-live date?")
        assert "1. What is the go-live date?" in full_md

    def test_full_md_contains_gap_questionnaire_section_header(self, module_under_test):
        mod, _ = module_under_test
        full_md, _ = self._call(mod)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, module_under_test):
        mod, _ = module_under_test
        full_md, _ = self._call(mod, owner="acme", repo="engine", version="3.0.0")
        assert "acme/engine" in full_md
        assert "3.0.0" in full_md

    def test_full_md_contains_ai_delivery_bot_footer(self, module_under_test):
        mod, _ = module_under_test
        full_md, _ = self._call(mod)
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, module_under_test):
        mod, _ = module_under_test
        _, gap_only_md = self._call(mod, project="RiskEngine", version="2.0.0")
        assert "RiskEngine" in gap_only_md
        assert "2.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, module_under_test):
        mod, _ = module_under_test
        _, gap_only_md = self._call(mod, gaps="1. Who are the stakeholders?\n2. What is the budget?")
        assert "Who are the stakeholders?" in gap_only_md
        assert "What is the budget?" in gap_only_md

    def test_gap_