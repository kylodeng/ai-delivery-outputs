"""
Tests for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude response parsing
  - build_full_output(): full markdown assembly, gap-only markdown assembly, content verification
  - __main__ block behaviour: env vars, file writing, email sending, audit entry, error handling

Mocks used:
  - shared.call_claude          → unittest.mock.patch
  - shared.get_repo_files       → unittest.mock.patch
  - shared.write_output_file    → unittest.mock.patch
  - shared.send_email           → unittest.mock.patch
  - shared.email_html           → unittest.mock.patch
  - shared.write_audit_entry    → unittest.mock.patch
  - datetime.datetime.utcnow    → unittest.mock.patch
  - os.environ                  → monkeypatch / unittest.mock.patch.dict

TODOs:
  - TODO: Integration test against a real Claude endpoint (requires API key + live credentials)
  - TODO: Test write_output_file return value shapes once output repo structure is confirmed
  - TODO: Test truncation behaviour when individual repo files exceed 3000 chars (needs shared impl detail)
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test without executing __main__
# ---------------------------------------------------------------------------

MODULE_PATH = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
)

# We stub out the `shared` module so the import doesn't fail in CI
def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="doc\n---GAPS---\ngaps")
    shared.get_repo_files     = MagicMock(return_value={})
    shared.write_output_file  = MagicMock(return_value="https://github.com/output/file")
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-owner"
    shared.OUTPUT_REPO        = "test-output-repo"
    return shared


@pytest.fixture(autouse=True)
def shared_stub(monkeypatch):
    """Inject a fresh shared stub before every test."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)
    yield stub


@pytest.fixture()
def module(shared_stub):
    """Import (or re-import) tool3_business_docs with the stubbed shared module."""
    # Force reload so each test gets a clean module state
    spec = importlib.util.spec_from_file_location("tool3_business_docs", MODULE_PATH)
    mod  = importlib.util.module_from_spec(spec)
    # Patch sys.modules so relative imports inside the file resolve to the stub
    sys.modules["tool3_business_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Synthetic / fixture data
# ---------------------------------------------------------------------------

FAKE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent"}}',
    "backend/tmp/customer_similarity_dict.json": '{"CUST00000001": ["CUST00006151"]}',
}

FAKE_DOC   = "# Solution overview: MyProject\n\nSome content here."
FAKE_GAPS  = "1. What is the go-live date?\n2. Who is the business sponsor?"
FAKE_RAW   = f"{FAKE_DOC}\n---GAPS---\n{FAKE_GAPS}"

FIXED_NOW  = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE = "2024-06-15"
FIXED_TS   = "2024-06-15 12:00 UTC"


# ---------------------------------------------------------------------------
# generate_biz_doc tests
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, module, shared_stub):
        """Claude returns a well-formed response containing ---GAPS---."""
        shared_stub.get_repo_files.return_value = FAKE_FILES
        shared_stub.call_claude.return_value = FAKE_RAW

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_NOW
            mock_dt.utcnow.return_value.strftime = FIXED_NOW.strftime
            doc, gaps = module.generate_biz_doc("acme", "widget", "Widget", "1.0.0", "https://run")

        assert "Solution overview" in doc
        assert "go-live date" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_response_without_delimiter(self, module, shared_stub):
        """When Claude omits ---GAPS---, doc gets the full text, gaps gets fallback message."""
        shared_stub.get_repo_files.return_value = FAKE_FILES
        shared_stub.call_claude.return_value = "Just a document with no separator."

        doc, gaps = module.generate_biz_doc("acme", "widget", "Widget", "1.0.0", "https://run")

        assert doc == "Just a document with no separator."
        assert "manually" in gaps.lower() or "could not extract" in gaps.lower()

    def test_delimiter_splits_on_first_occurrence(self, module, shared_stub):
        """Only the first ---GAPS--- is used as the split point."""
        raw = f"DocPart\n---GAPS---\nGapPart1\n---GAPS---\nGapPart2"
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = raw

        doc, gaps = module.generate_biz_doc("a", "b", "B", "0.1", "url")

        assert doc == "DocPart"
        assert "GapPart1" in gaps
        assert "GapPart2" in gaps  # everything after first split ends up in gaps

    def test_get_repo_files_called_with_correct_extensions(self, module, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "x\n---GAPS---\ny"

        module.generate_biz_doc("owner", "repo", "Proj", "2.0.0", "url")

        args, kwargs = shared_stub.get_repo_files.call_args
        extensions = args[2] if len(args) >= 3 else kwargs.get("extensions", [])
        for ext in [".py", ".md", ".tf", ".yaml"]:
            assert ext in extensions

    def test_call_claude_receives_owner_repo_in_user_prompt(self, module, shared_stub):
        shared_stub.get_repo_files.return_value = {"a.py": "print('hi')"}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        module.generate_biz_doc("myowner", "myrepo", "Proj", "1.0", "url")

        _, user_msg = shared_stub.call_claude.call_args[0]
        assert "myowner/myrepo" in user_msg

    def test_project_name_interpolated_in_system_prompt(self, module, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "d\n---GAPS---\ng"

        module.generate_biz_doc("o", "r", "SpecialProjectName", "1.0", "url")

        system_prompt, _ = shared_stub.call_claude.call_args[0]
        assert "SpecialProjectName" in system_prompt

    def test_version_interpolated_in_system_prompt(self, module, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "d\n---GAPS---\ng"

        module.generate_biz_doc("o", "r", "P", "3.4.5", "url")

        system_prompt, _ = shared_stub.call_claude.call_args[0]
        assert "3.4.5" in system_prompt

    def test_date_interpolated_in_system_prompt(self, module, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "d\n---GAPS---\ng"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = "2099-01-01"
            module.generate_biz_doc("o", "r", "P", "1.0", "url")

        system_prompt, _ = shared_stub.call_claude.call_args[0]
        assert "2099-01-01" in system_prompt

    def test_empty_repo_files(self, module, shared_stub):
        """Empty repo should not raise; call_claude still invoked."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = module.generate_biz_doc("o", "r", "P", "1.0", "url")
        assert shared_stub.call_claude.called

    def test_files_truncated_to_3000_chars_in_prompt(self, module, shared_stub):
        """Each file's content is capped at 3000 characters when building the user message."""
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        shared_stub.call_claude.return_value = "d\n---GAPS---\ng"

        module.generate_biz_doc("o", "r", "P", "1.0", "url")

        _, user_msg = shared_stub.call_claude.call_args[0]
        # The file block should contain at most 3000 x's
        assert "x" * 3001 not in user_msg

    def test_returns_stripped_strings(self, module, shared_stub):
        """Leading/trailing whitespace is stripped from both parts."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "  doc content  \n---GAPS---\n  gap content  "

        doc, gaps = module.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_call_claude_exception_propagates(self, module, shared_stub):
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            module.generate_biz_doc("o", "r", "P", "1.0", "url")


# ---------------------------------------------------------------------------
# build_full_output tests
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, module, doc=FAKE_DOC, gaps=FAKE_GAPS,
              owner="acme", repo="widget", project="Widget", version="1.2.3"):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime.return_value = FIXED_TS
            return module.build_full_output(doc, gaps, owner, repo, project, version)

    def test_returns_two_strings(self, module):
        full_md, gap_only_md = self._call(module)
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, module):
        full_md, _ = self._call(module)
        assert FAKE_DOC in full_md

    def test_full_md_contains_gaps(self, module):
        full_md, _ = self._call(module)
        assert FAKE_GAPS in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, module):
        full_md, _ = self._call(module)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, module):
        full_md, _ = self._call(module)
        assert "acme/widget" in full_md
        assert "1.2.3" in full_md

    def test_gap_only_md_contains_project_name(self, module):
        _, gap_only_md = self._call(module)
        assert "Widget" in gap_only_md

    def test_gap_only_md_contains_version(self, module):
        _, gap_only_md = self._call(module)
        assert "1.2.3" in gap_only_md

    def test_gap_only_md_contains_gaps(self, module):
        _, gap_only_md = self._call(module)
        assert FAKE_GAPS in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, module):
        _, gap_only_md = self._call(module)
        # Should reference the output repo for the "View full draft" link
        assert "github.com" in gap_only_md

    def test_full_md_contains_ai_delivery_attribution(self, module):
        full_md, _ = self._call(module)
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_generated_timestamp(self, module):
        _, gap_only_md = self._call(module)
        assert "Generated" in gap_only_md

    def test_empty_doc_string(self, module):
        full_md, gap_only_md = self._call(module, doc="")
        assert isinstance(full_md, str)
        assert FAKE_GAPS in full_md

    def test_empty_gaps_string(self, module):
        full_md, gap_only_md = self._call(module, gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_special_characters_in_project_name(self, module):
        full_md, gap_only_md = self._call(module, project="My & Great <Project>")
        assert "My & Great <Project>" in gap_only_md

    def test_different_versions(self, module):
        for ver in ["0.0.1", "1.0.0", "10.20.300", "v2.0.0-rc1"]:
            full_md, gap_only_md = self._call(module, version=ver)
            assert ver in full_md
            assert ver in gap_only_md

    def test_full_md_does_not_equal_gap_only_md(self, module):
        full_md, gap_only_md = self._call(module)
        assert full_md != gap_only_md


# ---------------------------------------------------------------------------
# __main__ block tests (happy path + error path)
# ---------------------------------------------------------------------------

class TestMainBlock:
    """Tests for the __main__ entry-point logic, run by re-executing the module."""

    DEFAULT_ENV = {
        "SOURCE_REPO_OWNER": "acme",
        "SOURCE_REPO_NAME":  "widget",
        "PROJECT_NAME