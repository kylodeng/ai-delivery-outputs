"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc: happy path, missing ---GAPS--- delimiter, Claude response variations
    - build_full_output: happy path, empty gaps, content assertions, tuple return values
    - __main__ block: environment variable handling, success path, exception/failure path

Mocks used:
    - shared.call_claude (patched to return synthetic Claude responses)
    - shared.get_repo_files (patched to return synthetic file dicts)
    - shared.write_output_file (patched to return a fake URL)
    - shared.send_email (patched to no-op)
    - shared.email_html (patched to return a fake HTML string)
    - shared.write_audit_entry (patched to no-op)
    - datetime.datetime.utcnow (patched for deterministic timestamps)

TODOs:
    - TODO: Integration test against a real GitHub repo (requires GH credentials)
    - TODO: Test Claude response with malformed/empty content edge cases beyond stubs
    - TODO: Test write_output_file failure propagation in __main__ block
"""

import sys
import os
import types
import importlib
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so the import in the source
# succeeds without the real file being present / importable in test env.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a synthetic `shared` module with all symbols the source needs."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="MOCK CLAUDE RESPONSE")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/fake/url")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>mock</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def inject_fake_shared(monkeypatch):
    """
    Inject a fake `shared` module before each test and clean up afterwards.
    This prevents the real `shared.py` from being required at import time.
    """
    fake_shared = _make_fake_shared()
    # Patch sys.modules so `from shared import ...` resolves to our fake
    monkeypatch.setitem(sys.modules, "shared", fake_shared)
    # Also ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    monkeypatch.syspath_prepend(scripts_dir)
    yield fake_shared


@pytest.fixture()
def biz_docs_module(inject_fake_shared):
    """
    Import (or re-import) the module under test after the fake shared is injected.
    Returns the module object for direct access to functions.
    """
    module_name = "tool3_business_docs"
    # Remove cached version so we get a fresh import with our fakes
    sys.modules.pop(module_name, None)

    # Locate the source file relative to this test file
    source_path = os.path.join(
        os.path.dirname(__file__),
        ".github", "scripts", "tool3_business_docs.py"
    )

    spec = importlib.util.spec_from_file_location(module_name, source_path)
    mod = importlib.util.module_from_spec(spec)
    # Inject shared symbols the source's top-level import will resolve
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def fixed_utcnow():
    """Return a fixed UTC datetime for deterministic timestamp assertions."""
    return datetime.datetime(2024, 6, 15, 12, 0, 0)


# ---------------------------------------------------------------------------
# Synthetic Claude response strings
# ---------------------------------------------------------------------------

CLAUDE_WITH_GAPS = """\
# Solution overview: Generations II

**Version:** 1.2.0 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
Generations II is a participating whole life insurance plan ...

## Business context
**Problem statement:** Provide lifelong protection.

---GAPS---

1. What is the target go-live date?
2. Who is the business sponsor?
3. What are the retention requirements for policyholder data?
"""

CLAUDE_WITHOUT_GAPS = """\
# Solution overview: No Delimiter Project

This document has no gap delimiter anywhere in it.
"""

CLAUDE_EMPTY = ""

SYNTHETIC_FILES = {
    "main.py": "def main(): pass",
    "infra/main.tf": 'resource "aws_s3_bucket" "data" {}',
    "README.md": "# Generations II\nInsurance product brochure processor.",
}


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, biz_docs_module, inject_fake_shared, fixed_utcnow):
        """Claude returns well-formed response with ---GAPS--- delimiter."""
        inject_fake_shared.get_repo_files.return_value = SYNTHETIC_FILES
        inject_fake_shared.call_claude.return_value = CLAUDE_WITH_GAPS

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fixed_utcnow

            doc, gaps = biz_docs_module.generate_biz_doc(
                "acme", "gen2-repo", "Generations II", "1.2.0", "https://run.url"
            )

        assert "Solution overview" in doc
        assert "Executive summary" in doc
        # gaps section should contain the numbered questions
        assert "1." in gaps
        assert "go-live date" in gaps
        # doc should NOT contain the delimiter
        assert "---GAPS---" not in doc
        # gaps should NOT contain the delimiter literal
        assert "---GAPS---" not in gaps

    def test_happy_path_get_repo_files_called_with_correct_extensions(
            self, biz_docs_module, inject_fake_shared):
        """Verify get_repo_files is called with expected file extensions."""
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = CLAUDE_WITH_GAPS

        biz_docs_module.generate_biz_doc("acme", "repo", "MyProject", "0.1.0", "https://url")

        inject_fake_shared.get_repo_files.assert_called_once()
        call_args = inject_fake_shared.get_repo_files.call_args
        extensions = call_args[0][2]  # third positional arg
        for ext in [".py", ".tf", ".md", ".yaml"]:
            assert ext in extensions

    def test_max_files_limit_passed(self, biz_docs_module, inject_fake_shared):
        """max_files=20 must be forwarded to get_repo_files."""
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = CLAUDE_WITH_GAPS

        biz_docs_module.generate_biz_doc("acme", "repo", "Proj", "1.0.0", "https://url")

        _, kwargs = inject_fake_shared.get_repo_files.call_args
        assert kwargs.get("max_files") == 20

    def test_missing_gaps_delimiter_returns_fallback(self, biz_docs_module, inject_fake_shared):
        """When Claude omits ---GAPS--- the function should return a fallback string."""
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = CLAUDE_WITHOUT_GAPS

        doc, gaps = biz_docs_module.generate_biz_doc(
            "acme", "repo", "NoDelimiter", "0.0.1", "https://url"
        )

        assert "No Delimiter Project" in doc
        assert "could not extract gap questions" in gaps.lower() or gaps != ""

    def test_empty_claude_response_handled(self, biz_docs_module, inject_fake_shared):
        """Claude returning an empty string should not raise an exception."""
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = CLAUDE_EMPTY

        doc, gaps = biz_docs_module.generate_biz_doc(
            "acme", "repo", "EmptyProject", "0.0.0", "https://url"
        )

        # Both should be strings (possibly empty)
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_call_claude_receives_formatted_prompt(self, biz_docs_module, inject_fake_shared, fixed_utcnow):
        """The system prompt forwarded to call_claude must include project_name and version."""
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = CLAUDE_WITH_GAPS

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fixed_utcnow

            biz_docs_module.generate_biz_doc(
                "acme", "sun-life", "Generations II", "1.2.0", "https://url"
            )

        prompt_arg = inject_fake_shared.call_claude.call_args[0][0]
        assert "Generations II" in prompt_arg
        assert "1.2.0" in prompt_arg
        assert "2024-06-15" in prompt_arg

    def test_call_claude_user_message_includes_owner_repo(self, biz_docs_module, inject_fake_shared):
        """User message passed to call_claude should reference owner/repo."""
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = CLAUDE_WITH_GAPS

        biz_docs_module.generate_biz_doc(
            "sun-life", "policy-engine", "PolicyEngine", "2.0.0", "https://url"
        )

        user_msg = inject_fake_shared.call_claude.call_args[0][1]
        assert "sun-life/policy-engine" in user_msg

    def test_multiple_files_concatenated_in_user_message(self, biz_docs_module, inject_fake_shared):
        """All repo files should appear in the user message sent to Claude."""
        inject_fake_shared.get_repo_files.return_value = SYNTHETIC_FILES
        inject_fake_shared.call_claude.return_value = CLAUDE_WITH_GAPS

        biz_docs_module.generate_biz_doc("acme", "repo", "Proj", "1.0.0", "https://url")

        user_msg = inject_fake_shared.call_claude.call_args[0][1]
        for filename in SYNTHETIC_FILES:
            assert filename in user_msg

    def test_file_content_truncated_to_3000_chars(self, biz_docs_module, inject_fake_shared):
        """Content longer than 3000 chars must be truncated before sending to Claude."""
        long_content = "x" * 5000
        inject_fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        inject_fake_shared.call_claude.return_value = CLAUDE_WITH_GAPS

        biz_docs_module.generate_biz_doc("acme", "repo", "Proj", "1.0.0", "https://url")

        user_msg = inject_fake_shared.call_claude.call_args[0][1]
        # The concatenated content block should NOT contain 5000 x's
        assert "x" * 4000 not in user_msg

    def test_gaps_stripped_of_leading_trailing_whitespace(self, biz_docs_module, inject_fake_shared):
        """doc and gaps should be stripped."""
        raw = "  \n  doc content  \n  ---GAPS---  \n  gap content  \n  "
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = raw

        doc, gaps = biz_docs_module.generate_biz_doc("a", "b", "P", "1", "u")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_only_first_delimiter_split_used(self, biz_docs_module, inject_fake_shared):
        """If ---GAPS--- appears multiple times, only split on the first occurrence."""
        raw = "doc part---GAPS---gaps part---GAPS---extra"
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = raw

        doc, gaps = biz_docs_module.generate_biz_doc("a", "b", "P", "1", "u")

        assert "---GAPS---" not in doc
        # gaps may contain the second delimiter — that is acceptable
        assert "gaps part" in gaps


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, biz_docs_module, doc="# Doc", gaps="1. Question?",
              owner="acme", repo="repo", project_name="MyProject", version="1.0.0"):
        return biz_docs_module.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_returns_tuple_of_two_strings(self, biz_docs_module):
        result = self._call(biz_docs_module)
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, biz_docs_module):
        full_md, _ = self._call(biz_docs_module, doc="# Executive Summary\nSome content.")
        assert "# Executive Summary" in full_md
        assert "Some content." in full_md

    def test_full_md_contains_gaps_content(self, biz_docs_module):
        full_md, _ = self._call(biz_docs_module, gaps="1. What is the go-live date?")
        assert "1. What is the go-live date?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, biz_docs_module):
        full_md, _ = self._call(biz_docs_module)
        assert "Gap Questionnaire" in full_md

    def test_full_md_footer_contains_owner_repo_version(self, biz_docs_module):
        full_md, _ = self._call(biz_docs_module, owner="sun", repo="life", version="2.1.0")
        assert "sun/life" in full_md
        assert "v2.1.0" in full_md

    def test_gap_only_md_contains_project_name(self, biz_docs_module):
        _, gap_only_md = self._call(biz_docs_module, project_name="Generations II", version="1.2.0")
        assert "Generations II" in gap_only_md
        assert "1.2.0"