"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, file truncation behaviour
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content checks
    - __main__ block behaviour via subprocess / direct invocation with env vars patched
    - Edge cases: missing delimiter, empty gaps, empty doc, whitespace handling

Mocks used:
    - shared.call_claude          — prevents real API calls
    - shared.get_repo_files       — prevents real GitHub API calls
    - shared.write_output_file    — prevents real file/git writes
    - shared.send_email           — prevents real SMTP calls
    - shared.email_html           — prevents real template rendering
    - shared.write_audit_entry    — prevents real audit writes
    - datetime.datetime.utcnow    — deterministic timestamps

TODOs:
    - TODO: Test __main__ block end-to-end requires subprocess patching of all shared deps;
            stub tests are provided but skipped where full integration context is missing.
    - TODO: Test actual Claude response parsing with malformed/unexpected Claude outputs
            beyond the simple delimiter cases tested here.
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

def _make_shared_mock():
    """Return a mock 'shared' module with all required attributes."""
    m = types.ModuleType("shared")
    m.call_claude = MagicMock(return_value="DOC CONTENT\n---GAPS---\n1. What is the go-live date?")
    m.get_repo_files = MagicMock(return_value={
        "main.py": "print('hello')",
        "README.md": "# My project",
    })
    m.write_output_file = MagicMock(return_value="https://github.com/output/repo/blob/main/file.md")
    m.send_email = MagicMock()
    m.email_html = MagicMock(return_value="<html>body</html>")
    m.write_audit_entry = MagicMock()
    m.OUTPUT_REPO_OWNER = "test-owner"
    m.OUTPUT_REPO = "test-output-repo"
    return m


def _import_module(shared_mock=None):
    """Import (or re-import) tool3_business_docs with a mocked shared module."""
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Ensure the script directory is on sys.path (mirrors the real script)
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    sys.modules["shared"] = shared_mock

    # Remove cached version so we get a fresh import each time
    sys.modules.pop("tool3_business_docs", None)

    import importlib.util
    spec_path = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts", "tool3_business_docs.py"
    )
    spec = importlib.util.spec_from_file_location("tool3_business_docs", spec_path)
    module = importlib.util.module_from_spec(spec)
    # Prevent __main__ block from running during import
    with patch.object(spec.loader, "exec_module", wraps=spec.loader.exec_module):
        # We need to patch __name__ before exec; simplest: just exec normally
        # but guard the __main__ block via __name__ != "__main__"
        spec.loader.exec_module(module)

    return module, shared_mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_mock():
    return _make_shared_mock()


@pytest.fixture()
def module(shared_mock):
    mod, _ = _import_module(shared_mock)
    return mod


@pytest.fixture()
def module_and_shared(shared_mock):
    mod, sm = _import_module(shared_mock)
    return mod, sm


FIXED_DATE = "2024-06-15"
FIXED_DATETIME = "2024-06-15 12:00 UTC"


def _fixed_utcnow():
    return datetime.datetime(2024, 6, 15, 12, 0, 0)


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_returns_doc_and_gaps(self, module_and_shared):
        mod, sm = module_and_shared
        sm.call_claude.return_value = "My Doc Content\n---GAPS---\n1. What is the deadline?"
        sm.get_repo_files.return_value = {"app.py": "x = 1"}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = _fixed_utcnow()
            mock_dt.utcnow.return_value.strftime = _fixed_utcnow().strftime
            doc, gaps = mod.generate_biz_doc("my-owner", "my-repo", "MyProject", "1.0.0", "http://run")

        assert doc == "My Doc Content"
        assert gaps == "1. What is the deadline?"

    def test_delimiter_splits_correctly(self, module_and_shared):
        mod, sm = module_and_shared
        sm.call_claude.return_value = (
            "## Section 1\nContent here.\n---GAPS---\n1. Q one?\n2. Q two?"
        )
        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert "## Section 1" in doc
        assert "Content here." in doc
        assert "1. Q one?" in gaps
        assert "2. Q two?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_uses_fallback_gaps(self, module_and_shared):
        mod, sm = module_and_shared
        sm.call_claude.return_value = "Some raw output without the delimiter"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "Some raw output without the delimiter"
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, module_and_shared):
        mod, sm = module_and_shared
        mod.generate_biz_doc("owner", "repo", "Proj", "2.0", "url")

        sm.get_repo_files.assert_called_once()
        args, kwargs = sm.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_formatted_prompt(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {"a.py": "pass"}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = _fixed_utcnow()
            mock_dt.utcnow.return_value.strftime = _fixed_utcnow().strftime
            mod.generate_biz_doc("acme", "widget", "Widget", "3.1.0", "http://x")

        assert sm.call_claude.called
        call_args = sm.call_claude.call_args
        prompt_arg = call_args[0][0]
        user_msg_arg = call_args[0][1]

        assert "Widget" in prompt_arg      # project_name injected
        assert "3.1.0" in prompt_arg       # version injected
        assert "acme/widget" in user_msg_arg
        assert "a.py" in user_msg_arg

    def test_files_content_truncated_to_3000_chars(self, module_and_shared):
        mod, sm = module_and_shared
        long_content = "x" * 5000
        sm.get_repo_files.return_value = {"big.py": long_content}

        mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        user_msg = sm.call_claude.call_args[0][1]
        # The truncated content should appear — 3000 x's, not 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_multiple_files_all_included_in_prompt(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {
            "alpha.py": "alpha content",
            "beta.tf": "beta content",
            "gamma.md": "gamma content",
        }

        mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        user_msg = sm.call_claude.call_args[0][1]
        assert "alpha.py" in user_msg
        assert "beta.tf" in user_msg
        assert "gamma.md" in user_msg

    def test_empty_repo_files_still_calls_claude(self, module_and_shared):
        mod, sm = module_and_shared
        sm.get_repo_files.return_value = {}

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")
        assert sm.call_claude.called

    def test_whitespace_stripped_from_doc_and_gaps(self, module_and_shared):
        mod, sm = module_and_shared
        sm.call_claude.return_value = "  \n  doc content  \n  ---GAPS---  \n  gap content  \n  "

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "doc content"
        assert gaps == "gap content"

    def test_only_first_delimiter_is_used_for_split(self, module_and_shared):
        mod, sm = module_and_shared
        sm.call_claude.return_value = "DOC\n---GAPS---\nGAPS part one\n---GAPS---\nGAPS part two"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "DOC"
        assert "GAPS part one" in gaps
        assert "GAPS part two" in gaps  # second occurrence stays in gaps section

    def test_returns_tuple_of_two_strings(self, module_and_shared):
        mod, sm = module_and_shared
        result = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def _call(self, mod, doc="## Overview\nContent", gaps="1. Q?",
              owner="acme", repo="widget", project_name="Widget", version="1.2.3"):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = _fixed_utcnow()
            mock_dt.utcnow.return_value.strftime = _fixed_utcnow().strftime
            return mod.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_tuple_of_two_strings(self, module):
        result = self._call(module)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_full_md_contains_original_doc(self, module):
        full_md, _ = self._call(module, doc="## Executive Summary\nGreat stuff.")
        assert "## Executive Summary" in full_md
        assert "Great stuff." in full_md

    def test_full_md_contains_gap_questionnaire_header(self, module):
        full_md, _ = self._call(module)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self, module):
        full_md, _ = self._call(module, gaps="1. Who owns this?\n2. When does it go live?")
        assert "1. Who owns this?" in full_md
        assert "2. When does it go live?" in full_md

    def test_full_md_contains_source_attribution(self, module):
        full_md, _ = self._call(module, owner="acme", repo="widget", version="1.2.3")
        assert "acme/widget" in full_md
        assert "v1.2.3" in full_md

    def test_full_md_contains_ai_delivery_bot_credit(self, module):
        full_md, _ = self._call(module)
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, module):
        _, gap_only = self._call(module, project_name="MyApp", version="2.0.0")
        assert "MyApp" in gap_only
        assert "v2.0.0" in gap_only

    def test_gap_only_md_contains_gap_questions(self, module):
        _, gap_only = self._call(module, gaps="1. Target date?\n2. Budget?")
        assert "1. Target date?" in gap_only
        assert "2. Budget?" in gap_only

    def test_gap_only_md_links_to_output_repo(self, module, shared_mock):
        _, gap_only = self._call(module)
        assert "github.com" in gap_only
        assert shared_mock.OUTPUT_REPO_OWNER in gap_only or shared_mock.OUTPUT_REPO in gap_only

    def test_gap_only_md_does_not_contain_full_doc_content(self, module):
        doc = "## Executive Summary\nVery detailed technical content here."
        _, gap_only = self._call(module, doc=doc)
        # The gap-only doc should not reproduce the full doc sections
        assert "Very detailed technical content here." not in gap_only

    def test_full_md_estimated_reading_note(self, module):
        _, gap_only = self._call(module)
        assert "10-15 minutes" in gap_only

    def test_empty_gaps_string(self, module):
        full_md, gap_only = self._call(module, gaps="")
        # Should not crash; both outputs should still be strings
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_empty_doc_string(self, module):
        full_md, gap_only = self._call(module, doc="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    def test_special_characters_in_project_name(self, module):
        full_md, gap_only = self._call(module, project_name="My & Special <Project>", version="0.0.1")
        assert "My & Special <Project>" in gap_only

    def test_version_appears_in_full_md(self, module):
        full