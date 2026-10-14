"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty file dict
    - build_full_output(): happy path, content structure, metadata injection, gap-only doc
    - SYSTEM prompt formatting
    - __main__ block behaviour (env-driven orchestration)

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch

TODOs:
    - TODO: Integration test against a real Claude API key (skipped — no key available)
    - TODO: Test write_output_file return value wired through to send_email (needs output repo creds)
    - TODO: Test __main__ FAILED branch fully (truncated source prevents verifying send_email args)
"""

import sys
import os
import types
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with mocked `shared` dependency
# ---------------------------------------------------------------------------

SHARED_ATTRS = [
    "call_claude",
    "get_repo_files",
    "write_output_file",
    "send_email",
    "email_html",
    "write_audit_entry",
    "OUTPUT_REPO_OWNER",
    "OUTPUT_REPO",
]


def _make_shared_stub():
    """Return a minimal stub module for `shared`."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Gap question?")
    stub.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    stub.write_output_file = MagicMock(return_value="https://github.com/output/file")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>body</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "test-owner"
    stub.OUTPUT_REPO = "test-output-repo"
    return stub


def _import_module(shared_stub=None):
    """
    Import tool3_business_docs freshly, injecting a stub for `shared`.
    Returns the module object.
    """
    # Ensure .github/scripts is importable
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    stub = shared_stub or _make_shared_stub()
    sys.modules["shared"] = stub

    # Remove cached version so we get a clean import
    sys.modules.pop("tool3_business_docs", None)

    # The source file lives at .github/scripts/tool3_business_docs.py
    spec_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
    )
    spec = importlib.util.spec_from_file_location("tool3_business_docs", spec_path)
    mod = importlib.util.module_from_spec(spec)
    # Prevent __main__ block from running during import
    with patch.object(spec.loader, "exec_module", wraps=spec.loader.exec_module):
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
    return mod, stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def shared_stub():
    return _make_shared_stub()


@pytest.fixture()
def mod(shared_stub):
    module, _ = _import_module(shared_stub)
    return module


@pytest.fixture()
def mod_and_stub(shared_stub):
    module, stub = _import_module(shared_stub)
    return module, stub


@pytest.fixture()
def frozen_utcnow():
    """Patch datetime.datetime.utcnow inside the module to return a fixed time."""
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_NOW
        mock_dt.utcnow.return_value.strftime = FIXED_NOW.strftime
        # Make strftime calls work properly on the mock
        mock_dt.utcnow.side_effect = None
        yield mock_dt


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

SAMPLE_PROJECTS = [
    ("acme-corp", "insurance-portal", "GenerationsII", "1.0.0"),
    ("sun-life", "health-claims", "HealthClaims", "2.3.1"),
    ("test-org", "my-repo", "MyProject", "0.1.0"),
]

SAMPLE_FILES = {
    "README.md": "# Generations II\nA whole life insurance plan.",
    "main.py": "def handler(): pass",
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" {}',
}


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------


class TestGenerateBizDoc:
    """Tests for generate_biz_doc()."""

    def test_happy_path_splits_on_delimiter(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value = (
            "# Solution overview\nsome content\n---GAPS---\n1. What is the go-live date?"
        )

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_NOW
            FIXED_NOW.strftime = datetime.datetime.strftime.__get__(FIXED_NOW)
            doc, gaps = mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "http://run")

        assert "# Solution overview" in doc
        assert "1. What is the go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_delimiter_returns_full_response_and_fallback(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "Some document without a delimiter at all."

        doc, gaps = mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "http://run")

        assert doc == "Some document without a delimiter at all."
        assert "manually" in gaps.lower()

    def test_delimiter_appears_only_once(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "Part A\n---GAPS---\nPart B"

        doc, gaps = mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "http://run")

        assert doc == "Part A"
        assert gaps == "Part B"

    def test_multiple_delimiters_splits_on_first(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "A\n---GAPS---\nB\n---GAPS---\nC"

        doc, gaps = mod.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "http://run")

        assert doc == "A"
        assert "B" in gaps
        assert "C" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        mod.generate_biz_doc("myowner", "myrepo", "P", "1.0", "http://run")

        stub.get_repo_files.assert_called_once()
        call_args = stub.get_repo_files.call_args
        assert call_args[0][0] == "myowner"
        assert call_args[0][1] == "myrepo"
        exts = call_args[0][2]
        for ext in [".py", ".md", ".tf"]:
            assert ext in exts
        assert call_args[1].get("max_files", call_args[0][3] if len(call_args[0]) > 3 else 20) == 20

    def test_call_claude_receives_formatted_prompt(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        mod.generate_biz_doc("owner", "repo", "InsurancePortal", "3.2.1", "http://run")

        prompt_arg = stub.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg
        assert "3.2.1" in prompt_arg

    def test_files_content_truncated_to_3000_chars(self, mod_and_stub):
        mod, stub = mod_and_stub
        long_content = "x" * 5000
        stub.get_repo_files.return_value = {"big_file.py": long_content}
        stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        mod.generate_biz_doc("owner", "repo", "P", "1.0", "http://run")

        user_msg = stub.call_claude.call_args[0][1]
        # The content block for big_file.py must not exceed 3000 chars
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_empty_repo_files(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_stripped_whitespace_in_results(self, mod_and_stub):
        mod, stub = mod_and_stub
        stub.call_claude.return_value = "   doc with spaces   \n---GAPS---\n   gaps text   "

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        assert doc == "doc with spaces"
        assert gaps == "gaps text"

    @pytest.mark.parametrize("owner,repo,project_name,version", SAMPLE_PROJECTS)
    def test_parametrised_project_inputs(self, mod_and_stub, owner, repo, project_name, version):
        mod, stub = mod_and_stub
        stub.call_claude.return_value = f"# {project_name}\n---GAPS---\n1. Question?"

        doc, gaps = mod.generate_biz_doc(owner, repo, project_name, version, "http://ci")

        assert project_name in doc
        assert "1. Question?" in gaps


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------


class TestBuildFullOutput:
    """Tests for build_full_output()."""

    def _call(self, mod, doc="# Doc", gaps="1. Gap?", owner="owner", repo="repo",
              project_name="MyProject", version="1.2.3"):
        return mod.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_two_strings(self, mod):
        result = self._call(mod)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(r, str) for r in result)

    def test_full_md_contains_doc_content(self, mod):
        full_md, _ = self._call(mod, doc="## Executive Summary\nSome content.")
        assert "## Executive Summary" in full_md
        assert "Some content." in full_md

    def test_full_md_contains_gaps_content(self, mod):
        full_md, _ = self._call(mod, gaps="1. What is the budget?\n2. Who owns this?")
        assert "1. What is the budget?" in full_md
        assert "2. Who owns this?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, mod):
        full_md, _ = self._call(mod)
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_metadata(self, mod):
        full_md, _ = self._call(mod, owner="acme", repo="portal", version="2.0.0")
        assert "acme/portal" in full_md
        assert "v2.0.0" in full_md

    def test_gap_only_md_contains_project_name(self, mod):
        _, gap_only = self._call(mod, project_name="GenerationsII", version="1.0.0")
        assert "GenerationsII" in gap_only
        assert "1.0.0" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, mod):
        _, gap_only = self._call(mod)
        # Should link to the output repo (OUTPUT_REPO_OWNER / OUTPUT_REPO from shared stub)
        assert "github.com" in gap_only

    def test_gap_only_md_contains_gaps_text(self, mod):
        _, gap_only = self._call(mod, gaps="1. First question?\n2. Second question?")
        assert "1. First question?" in gap_only
        assert "2. Second question?" in gap_only

    def test_full_md_ai_attribution_footer(self, mod):
        full_md, _ = self._call(mod)
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_generated_label(self, mod):
        _, gap_only = self._call(mod)
        assert "Generated" in gap_only

    def test_full_md_instructions_for_human(self, mod):
        full_md, _ = self._call(mod)
        assert "finalised" in full_md or "fill" in full_md.lower()

    def test_empty_doc_still_builds(self, mod):
        full_md, gap_only = self._call(mod, doc="", gaps="1. Only gap?")
        assert "1. Only gap?" in full_md
        assert "1. Only gap?" in gap_only

    def test_empty_gaps_still_builds(self, mod):
        full_md, gap_only = self._call(mod, gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only, str)

    @pytest.mark.parametrize("owner,repo,project_name,version", SAMPLE_PROJECTS)
    def test_parametrised_metadata_injection(self, mod, owner, repo, project_name, version):
        full_md, gap_only = mod.build_full_output(
            "doc", "1. Q?",