"""
Test module for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude integration
  - build_full_output(): happy path, content structure, timestamp formatting, edge cases
  - __main__ block: environment variable handling, orchestration, error handling

Mocks used:
  - shared.call_claude          — avoids real Anthropic API calls
  - shared.get_repo_files       — avoids real GitHub API calls
  - shared.write_output_file    — avoids real GitHub write operations
  - shared.send_email           — avoids real email delivery
  - shared.email_html           — avoids template rendering side-effects
  - shared.write_audit_entry    — avoids real audit log writes
  - datetime.datetime           — for deterministic timestamp assertions

TODOs:
  - TODO: Integration test with a real (mocked at HTTP layer) Claude response shape
  - TODO: Test the __main__ block's write_audit_entry FAILED path fully (truncated source)
  - TODO: Parameterise over different repo file extension combinations
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to isolate the module under test
# ---------------------------------------------------------------------------

# Build a minimal fake `shared` module so the import at the top of
# tool3_business_docs.py succeeds without the real package on PYTHONPATH.
def _make_fake_shared():
    shared = types.ModuleType("shared")
    shared.call_claude       = MagicMock(return_value="doc content\n---GAPS---\n1. A gap question?")
    shared.get_repo_files    = MagicMock(return_value={"README.md": "# Hello"})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/file")
    shared.send_email        = MagicMock()
    shared.email_html        = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO       = "test-output-repo"
    return shared


def _load_module(fake_shared):
    """(Re-)import tool3_business_docs with the supplied fake shared module."""
    sys.modules["shared"] = fake_shared
    # Ensure a clean import each time
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]

    spec_path = os.path.join(os.path.dirname(__file__),
                             ".github", "scripts", "tool3_business_docs.py")
    # Fallback: look relative to CWD for CI environments
    if not os.path.exists(spec_path):
        spec_path = os.path.join("tool3_business_docs.py")

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tool3_business_docs",
        spec_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_shared():
    shared = _make_fake_shared()
    yield shared
    # Cleanup so other tests get a fresh shared
    if "shared" in sys.modules:
        del sys.modules["shared"]
    if "tool3_business_docs" in sys.modules:
        del sys.modules["tool3_business_docs"]


@pytest.fixture()
def mod(fake_shared):
    return _load_module(fake_shared)


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:
    """Tests for generate_biz_doc()."""

    FIXED_DATE = "2024-06-01"

    @pytest.fixture(autouse=True)
    def _patch_datetime(self):
        """Pin utcnow() so date assertions are deterministic."""
        fixed = datetime.datetime(2024, 6, 1, 12, 0, 0)
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = fixed
            mock_dt.datetime.utcnow.return_value.strftime = fixed.strftime
            # Make strftime work properly on the returned object
            mock_dt.datetime.utcnow = lambda: fixed
            yield mock_dt

    def test_happy_path_with_gaps_delimiter(self, mod, fake_shared):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        fake_shared.call_claude.return_value = (
            "# Solution Overview\nSome content.\n---GAPS---\n1. What is the go-live date?"
        )
        doc, gaps = mod.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run.url")

        assert "Solution Overview" in doc
        assert "go-live date" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_gaps_delimiter(self, mod, fake_shared):
        """Claude returns only a document with no delimiter — gaps fallback message used."""
        fake_shared.call_claude.return_value = "# Solution Overview\nOnly the doc."
        doc, gaps = mod.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run.url")

        assert "Solution Overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, mod, fake_shared):
        """get_repo_files is called with the expected extension list and max_files."""
        mod.generate_biz_doc("owner", "repo", "proj", "0.2.0", "url")

        fake_shared.get_repo_files.assert_called_once()
        args, kwargs = fake_shared.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_formatted_prompt(self, mod, fake_shared):
        """System prompt should be formatted with project_name, version, and date."""
        mod.generate_biz_doc("owner", "repo", "Insurance Portal", "2.3.1", "url")

        call_args = fake_shared.call_claude.call_args
        prompt_arg = call_args[0][0]  # first positional arg
        assert "Insurance Portal" in prompt_arg
        assert "2.3.1" in prompt_arg

    def test_call_claude_user_message_contains_repo(self, mod, fake_shared):
        """The user message passed to Claude should reference the repo."""
        mod.generate_biz_doc("sun-life", "generations-ii", "Generations II", "1.0.0", "url")

        call_args = fake_shared.call_claude.call_args
        user_msg = call_args[0][1]  # second positional arg
        assert "sun-life/generations-ii" in user_msg

    def test_multiple_gaps_delimiter_only_first_split_used(self, mod, fake_shared):
        """If ---GAPS--- appears more than once, split only on the first occurrence."""
        fake_shared.call_claude.return_value = (
            "Doc part.\n---GAPS---\nGaps part.\n---GAPS---\nExtra stuff."
        )
        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "url")

        assert "Doc part." in doc
        assert "Gaps part." in gaps
        assert "Extra stuff." in gaps  # everything after the first delimiter

    def test_doc_and_gaps_are_stripped(self, mod, fake_shared):
        """Leading/trailing whitespace is removed from both parts."""
        fake_shared.call_claude.return_value = (
            "  \n  Doc content  \n  ---GAPS---  \n  Gap content  \n  "
        )
        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_empty_files_dict(self, mod, fake_shared):
        """Works gracefully when get_repo_files returns an empty dict."""
        fake_shared.get_repo_files.return_value = {}
        # Should not raise
        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "url")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_large_file_content_truncated_in_prompt(self, mod, fake_shared):
        """Each file's content is sliced to 3000 chars before building the prompt."""
        big_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"main.py": big_content}
        mod.generate_biz_doc("o", "r", "p", "v", "url")

        _, user_msg = fake_shared.call_claude.call_args[0]
        # The truncated block should contain exactly 3000 x's
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_synthetic_insurance_project(self, mod, fake_shared):
        """Smoke-test with synthetic insurance data project metadata."""
        fake_shared.call_claude.return_value = (
            "# Solution overview: Generations II\n"
            "**Version:** 1.0.0 | **Date:** 2024-06-01 | **Status:** Draft\n"
            "---GAPS---\n"
            "1. What is the retention period for policyholder data?\n"
            "2. Who is the business sponsor for this project?\n"
        )
        doc, gaps = mod.generate_biz_doc(
            "sun-life", "insurance-portal", "Generations II", "1.0.0", "https://ci.run"
        )
        assert "Generations II" in doc
        assert "retention period" in gaps
        assert "business sponsor" in gaps


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:
    """Tests for build_full_output()."""

    SAMPLE_DOC = "# Solution Overview\nSome content about the system."
    SAMPLE_GAPS = "1. What is the go-live date?\n2. Who owns the data?"

    @pytest.fixture(autouse=True)
    def _patch_datetime(self):
        fixed = datetime.datetime(2024, 6, 1, 9, 30, 0)
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow = lambda: fixed
            yield mock_dt

    def test_returns_tuple_of_two_strings(self, mod):
        result = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, mod):
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert self.SAMPLE_DOC in full_md

    def test_full_md_contains_gaps(self, mod):
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert self.SAMPLE_GAPS in full_md

    def test_full_md_contains_gap_questionnaire_header(self, mod):
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_attribution_footer(self, mod):
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert "AI Delivery Bot" in full_md
        assert "acme/repo" in full_md
        assert "1.2.3" in full_md

    def test_gap_only_md_contains_project_and_version(self, mod):
        _, gap_only_md = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert "Project X" in gap_only_md
        assert "v1.2.3" in gap_only_md

    def test_gap_only_md_contains_gaps(self, mod):
        _, gap_only_md = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert self.SAMPLE_GAPS in gap_only_md

    def test_gap_only_md_references_output_repo(self, mod, fake_shared):
        _, gap_only_md = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        # Should contain a link to the output repository
        assert "test-owner" in gap_only_md or "github.com" in gap_only_md

    def test_gap_only_md_does_not_contain_solution_overview_section(self, mod):
        """The standalone questionnaire should NOT duplicate the full doc body."""
        _, gap_only_md = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "acme", "repo", "Project X", "1.2.3"
        )
        assert "Some content about the system." not in gap_only_md

    def test_empty_gaps_string(self, mod):
        """Edge case: gaps is an empty string."""
        full_md, gap_only_md = mod.build_full_output(
            self.SAMPLE_DOC, "", "acme", "repo", "Project X", "0.1.0"
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_empty_doc_string(self, mod):
        """Edge case: doc is an empty string."""
        full_md, gap_only_md = mod.build_full_output(
            "", self.SAMPLE_GAPS, "acme", "repo", "Project X", "0.1.0"
        )
        assert self.SAMPLE_GAPS in full_md
        assert self.SAMPLE_GAPS in gap_only_md

    def test_version_with_special_chars(self, mod):
        """Version strings like '0.1.0-rc.1' should not break formatting."""
        full_md, gap_only_md = mod.build_full_output(
            self.SAMPLE_