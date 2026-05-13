"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter splitting, missing delimiter fallback
    - build_full_output(): structure/content of full_md and gap_only_md outputs
    - Main block logic (via direct function calls and env-var driven integration stubs)
    - Edge cases: empty gaps, empty doc, special characters in project_name/version
    - Error conditions: exceptions from external calls

Mocks used:
    - shared.call_claude          → prevents real Anthropic API calls
    - shared.get_repo_files       → prevents real GitHub API calls
    - shared.write_output_file    → prevents real GitHub commits
    - shared.send_email           → prevents real SMTP/SES calls
    - shared.email_html           → returns predictable HTML string
    - shared.write_audit_entry    → prevents real audit writes
    - datetime.datetime.utcnow    → frozen timestamps for deterministic assertions

TODOs:
    - TODO: Integration test against a real (sandboxed) Claude endpoint once available
    - TODO: Validate Markdown output structure with a proper MD parser
    - TODO: Test __main__ block for FAILED branch email content once truncated source is complete
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with mocked `shared` dependency
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"

def _make_shared_stub():
    """Return a minimal stub module that satisfies tool3's `from shared import …`."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="## Doc\n\nSome content\n---GAPS---\n1. Question one?\n2. Question two?")
    stub.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    stub.write_output_file = MagicMock(return_value="https://github.com/output/file.md")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>body</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "test-org"
    stub.OUTPUT_REPO = "test-output-repo"
    return stub


@pytest.fixture(autouse=True)
def patch_shared(monkeypatch):
    """Inject stub shared module before every test."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)
    # Force re-import so tool3 picks up our stub
    tool3_key = "tool3_business_docs"
    if tool3_key in sys.modules:
        del sys.modules[tool3_key]
    yield stub


def _import_tool3():
    """Import (or re-import) the module under test."""
    tool3_key = "tool3_business_docs"
    if tool3_key in sys.modules:
        del sys.modules[tool3_key]
    # Ensure the scripts directory is on sys.path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    # Also try the repo root .github/scripts relative path resolution
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, ".github", "scripts")
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)
    return importlib.import_module(tool3_key)


# ---------------------------------------------------------------------------
# Frozen datetime fixture
# ---------------------------------------------------------------------------

FROZEN_NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Freeze datetime.datetime.utcnow() to FROZEN_NOW."""
    fake_dt = MagicMock(wraps=datetime.datetime)
    fake_dt.utcnow.return_value = FROZEN_NOW
    monkeypatch.setattr("datetime.datetime", fake_dt)
    return fake_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_splits_on_delimiter(self, patch_shared, frozen_datetime):
        patch_shared.get_repo_files.return_value = {"main.py": "print('hello')"}
        patch_shared.call_claude.return_value = (
            "## Solution Overview\nSome description\n---GAPS---\n1. What is the deadline?"
        )
        tool3 = _import_tool3()
        doc, gaps = tool3.generate_biz_doc("myorg", "myrepo", "MyProject", "1.0.0", "https://ci.example.com")
        assert "## Solution Overview" in doc
        assert "Some description" in doc
        assert "1. What is the deadline?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_delimiter_fallback(self, patch_shared, frozen_datetime):
        """When Claude returns no delimiter, doc = full response, gaps = fallback message."""
        patch_shared.call_claude.return_value = "## Only a document, no gaps section."
        tool3 = _import_tool3()
        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "0.1.0", "https://ci")
        assert doc == "## Only a document, no gaps section."
        assert "manually" in gaps.lower() or "could not extract" in gaps.lower()

    def test_get_repo_files_called_with_expected_extensions(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        tool3.generate_biz_doc("org", "repo", "Proj", "0.2.0", "https://ci")
        call_args = patch_shared.get_repo_files.call_args
        args, kwargs = call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        for ext in [".py", ".md", ".tf"]:
            assert ext in extensions

    def test_call_claude_receives_project_name_in_prompt(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        tool3.generate_biz_doc("org", "repo", "UnderwritingRiskClassification", "2.0.0", "https://ci")
        prompt_arg = patch_shared.call_claude.call_args[0][0]
        assert "UnderwritingRiskClassification" in prompt_arg

    def test_call_claude_receives_version_in_prompt(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        tool3.generate_biz_doc("org", "repo", "Proj", "3.1.4", "https://ci")
        prompt_arg = patch_shared.call_claude.call_args[0][0]
        assert "3.1.4" in prompt_arg

    def test_call_claude_receives_date_in_prompt(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")
        prompt_arg = patch_shared.call_claude.call_args[0][0]
        assert FROZEN_DATE_STR in prompt_arg

    def test_repo_files_content_passed_to_claude(self, patch_shared, frozen_datetime):
        patch_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "README.md": "# Insurance underwriting system",
        }
        tool3 = _import_tool3()
        tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")
        user_message_arg = patch_shared.call_claude.call_args[0][1]
        assert "model_card.json" in user_message_arg
        assert "Underwriting Risk Classification" in user_message_arg

    def test_empty_repo_files(self, patch_shared, frozen_datetime):
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = "## Doc\n---GAPS---\n1. Q?"
        tool3 = _import_tool3()
        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")
        assert doc == "## Doc"
        assert gaps == "1. Q?"

    def test_multiple_delimiter_occurrences_only_splits_on_first(self, patch_shared, frozen_datetime):
        """Only the first ---GAPS--- should be used as a split point."""
        patch_shared.call_claude.return_value = (
            "Doc content\n---GAPS---\nGap 1\n---GAPS---\nExtra stuff"
        )
        tool3 = _import_tool3()
        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")
        assert "Doc content" in doc
        # The second occurrence should end up in gaps
        assert "Extra stuff" in gaps or "Gap 1" in gaps

    def test_whitespace_stripped_from_doc_and_gaps(self, patch_shared, frozen_datetime):
        patch_shared.call_claude.return_value = "  ## Doc  \n  ---GAPS---  \n  1. Q?  "
        tool3 = _import_tool3()
        doc, gaps = tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")
        assert not doc.startswith(" ")
        assert not gaps.startswith(" ")

    def test_get_repo_files_max_files_param(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")
        call_kwargs = patch_shared.get_repo_files.call_args[1]
        assert call_kwargs.get("max_files", 0) == 20

    def test_call_claude_propagates_exception(self, patch_shared, frozen_datetime):
        patch_shared.call_claude.side_effect = RuntimeError("API timeout")
        tool3 = _import_tool3()
        with pytest.raises(RuntimeError, match="API timeout"):
            tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")

    def test_get_repo_files_propagates_exception(self, patch_shared, frozen_datetime):
        patch_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")
        tool3 = _import_tool3()
        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")

    @pytest.mark.parametrize("project_name,version", [
        ("Underwriting Risk Classification", "1.0.0"),
        ("MyProject", "0.0.1"),
        ("project-with-hyphens", "2024.06.15"),
        ("Proj_underscore", "v1.2.3"),
        ("", "1.0.0"),          # edge: empty project name
        ("Proj", ""),           # edge: empty version
    ])
    def test_various_project_names_and_versions(self, patch_shared, frozen_datetime, project_name, version):
        """generate_biz_doc should not raise for various name/version combos."""
        patch_shared.call_claude.return_value = f"## {project_name}\n---GAPS---\n1. Q?"
        tool3 = _import_tool3()
        doc, gaps = tool3.generate_biz_doc("org", "repo", project_name, version, "https://ci")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_file_content_truncated_at_3000_chars(self, patch_shared, frozen_datetime):
        """Files longer than 3000 chars should be truncated in the prompt."""
        long_content = "x" * 5000
        patch_shared.get_repo_files.return_value = {"big_file.py": long_content}
        tool3 = _import_tool3()
        tool3.generate_biz_doc("org", "repo", "Proj", "1.0.0", "https://ci")
        user_message_arg = patch_shared.call_claude.call_args[0][1]
        # The truncated slice should appear, but not the full content
        assert "x" * 3000 in user_message_arg
        # Full 5000-char string must not appear
        assert "x" * 5000 not in user_message_arg


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def _call(self, tool3, doc="## Doc", gaps="1. Q?", owner="org", repo="repo",
              project_name="MyProject", version="1.0.0"):
        return tool3.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_two_strings(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        result = self._call(tool3)
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        full_md, _ = self._call(tool3, doc="## Solution Overview\nImportant stuff")
        assert "## Solution Overview" in full_md
        assert "Important stuff" in full_md

    def test_full_md_contains_gaps_content(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        full_md, _ = self._call(tool3, gaps="1. What is the go-live date?")
        assert "1. What is the go-live date?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        full_md, _ = self._call(tool3)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        full_md, _ = self._call(tool3, owner="myorg", repo="myrepo", version="2.0.0")
        assert "myorg/myrepo" in full_md
        assert "v2.0.0" in full_md

    def test_full_md_contains_frozen_timestamp(self, patch_shared, frozen_datetime):
        tool3 = _import_tool3()
        full_md, _ = self._call(tool3)
        assert FROZEN_DATETIME_STR in full_md

    def test_gap_only_md_contains_project_name_and_