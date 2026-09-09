"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude output splitting logic
    - build_full_output(): correct markdown assembly for full doc and gap-only doc
    - __main__ block behaviour: successful run, exception handling path
    - Environment variable handling (defaults, overrides)
    - Edge cases: empty gaps, missing delimiter, whitespace stripping, gap counting

Mocks used:
    - shared.call_claude          — patched to return controlled strings
    - shared.get_repo_files       — patched to return synthetic file dict
    - shared.write_output_file    — patched to avoid GitHub API calls
    - shared.send_email           — patched to avoid SMTP calls
    - shared.email_html           — patched to return dummy HTML
    - shared.write_audit_entry    — patched to avoid filesystem/API writes
    - datetime.datetime.utcnow    — patched for deterministic timestamps

TODOs:
    # TODO: Integration test against a real Claude API key (requires secret injection)
    # TODO: Test __main__ subprocess execution path end-to-end once CI env vars available
    # TODO: Test write_output_file actual commit path (needs GitHub token + repo)
"""

import sys
import os
import importlib
import datetime
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Helpers – build a minimal fake "shared" module so the import in
# tool3_business_docs does not fail even if the real shared.py is absent.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a fake `shared` module with all symbols tool3 imports."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/output/file")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>mock</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


@pytest.fixture(autouse=True)
def inject_fake_shared(monkeypatch):
    """Inject fake shared module before each test and reload tool3."""
    fake = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fake)
    yield fake


@pytest.fixture()
def tool3(inject_fake_shared):
    """Import (or reload) tool3_business_docs with the fake shared in place."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Remove cached version so we get a fresh import each test
    sys.modules.pop("tool3_business_docs", None)

    # If the file doesn't exist on disk, create a shim from the source text.
    # This allows the test suite to run in CI where the script is available.
    import importlib.util
    spec = importlib.util.find_spec("tool3_business_docs")
    if spec is None:
        pytest.skip("tool3_business_docs.py not found on sys.path – skipping")

    return importlib.import_module("tool3_business_docs")


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic output comparison
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Patch datetime.datetime.utcnow to return a fixed value."""
    mock_dt = MagicMock(wraps=datetime.datetime)
    mock_dt.utcnow.return_value = FIXED_DT
    monkeypatch.setattr("datetime.datetime", mock_dt)
    return mock_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, tool3, inject_fake_shared, frozen_datetime):
        """Claude returns properly delimited output → doc and gaps split correctly."""
        inject_fake_shared.call_claude.return_value = (
            "## Solution overview\nSome doc text.\n---GAPS---\n1. Who is the sponsor?\n2. Go-live date?"
        )
        inject_fake_shared.get_repo_files.return_value = {
            "README.md": "# My Project",
            "main.py": "print('hello')",
        }

        doc, gaps = tool3.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://gh.run")

        assert "## Solution overview" in doc
        assert "Some doc text." in doc
        assert "---GAPS---" not in doc
        assert "1. Who is the sponsor?" in gaps
        assert "2. Go-live date?" in gaps

    def test_happy_path_without_delimiter(self, tool3, inject_fake_shared, frozen_datetime):
        """Claude omits the delimiter → entire response becomes doc, fallback gap text used."""
        inject_fake_shared.call_claude.return_value = "Just a plain doc with no delimiter."

        doc, gaps = tool3.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://gh.run")

        assert doc == "Just a plain doc with no delimiter."
        assert "Claude could not extract gap questions" in gaps

    def test_files_are_passed_to_claude(self, tool3, inject_fake_shared, frozen_datetime):
        """get_repo_files result is embedded in the user message sent to Claude."""
        inject_fake_shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
        }
        inject_fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://gh.run")

        _, user_message = inject_fake_shared.call_claude.call_args[0]
        assert "backend/model_card.json" in user_message
        assert "Underwriting Risk Classification" in user_message

    def test_system_prompt_contains_project_and_version(self, tool3, inject_fake_shared, frozen_datetime):
        """System prompt is formatted with project_name, version, and today's date."""
        inject_fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("acme", "my-repo", "Underwriting Risk", "2.3.1", "https://gh.run")

        system_prompt = inject_fake_shared.call_claude.call_args[0][0]
        assert "Underwriting Risk" in system_prompt
        assert "2.3.1" in system_prompt
        assert FIXED_DATE_STR in system_prompt

    def test_get_repo_files_called_with_correct_extensions(self, tool3, inject_fake_shared, frozen_datetime):
        """get_repo_files is invoked with the expected extension list and max_files."""
        inject_fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("org", "repo", "proj", "0.1.0", "https://run")

        inject_fake_shared.get_repo_files.assert_called_once()
        call_kwargs = inject_fake_shared.get_repo_files.call_args
        extensions = call_kwargs[0][2] if len(call_kwargs[0]) > 2 else call_kwargs[1].get("extensions") or call_kwargs[0][2]
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions

    def test_delimiter_only_splits_on_first_occurrence(self, tool3, inject_fake_shared, frozen_datetime):
        """When ---GAPS--- appears multiple times, only the first split is used."""
        inject_fake_shared.call_claude.return_value = (
            "doc text\n---GAPS---\nfirst gap\n---GAPS---\nsecond gap"
        )

        doc, gaps = tool3.generate_biz_doc("acme", "repo", "proj", "1.0", "url")

        assert "doc text" in doc
        assert "first gap" in gaps
        # The second delimiter and content should remain in gaps (not in doc)
        assert "second gap" in gaps
        assert "---GAPS---" not in doc

    def test_whitespace_stripped_from_doc_and_gaps(self, tool3, inject_fake_shared, frozen_datetime):
        """Leading/trailing whitespace is stripped from both returned strings."""
        inject_fake_shared.call_claude.return_value = (
            "  \n  doc text  \n  ---GAPS---  \n  gap text  \n  "
        )

        doc, gaps = tool3.generate_biz_doc("acme", "repo", "proj", "1.0", "url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_empty_files_dict(self, tool3, inject_fake_shared, frozen_datetime):
        """Empty repo files dict → Claude still called with empty files section."""
        inject_fake_shared.get_repo_files.return_value = {}
        inject_fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc("acme", "repo", "proj", "1.0", "url")

        assert doc == "doc"
        assert gaps == "gaps"
        _, user_message = inject_fake_shared.call_claude.call_args[0]
        assert "Files:" in user_message

    def test_large_file_content_truncated_in_user_message(self, tool3, inject_fake_shared, frozen_datetime):
        """File content longer than 3000 chars is sliced in the user message."""
        long_content = "x" * 5000
        inject_fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        inject_fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("acme", "repo", "proj", "1.0", "url")

        _, user_message = inject_fake_shared.call_claude.call_args[0]
        # The truncated version (3000 x's) should appear, not the full 5000
        assert "x" * 3000 in user_message
        assert "x" * 3001 not in user_message

    def test_call_claude_raises_propagates(self, tool3, inject_fake_shared, frozen_datetime):
        """If call_claude raises, the exception propagates out of generate_biz_doc."""
        inject_fake_shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            tool3.generate_biz_doc("acme", "repo", "proj", "1.0", "url")


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================


class TestBuildFullOutput:

    def test_full_md_contains_doc_content(self, tool3, frozen_datetime):
        doc = "## Solution overview\nThis is the doc."
        gaps = "1. What is the go-live date?"
        full_md, _ = tool3.build_full_output(doc, gaps, "acme", "repo", "My Project", "1.0.0")

        assert "## Solution overview" in full_md
        assert "This is the doc." in full_md

    def test_full_md_contains_gaps(self, tool3, frozen_datetime):
        doc = "Doc content."
        gaps = "1. Who is the sponsor?\n2. What are the success metrics?"
        full_md, _ = tool3.build_full_output(doc, gaps, "acme", "repo", "proj", "1.0.0")

        assert "1. Who is the sponsor?" in full_md
        assert "2. What are the success metrics?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output("doc", "gaps", "acme", "repo", "proj", "1.0.0")
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output("doc", "gaps", "acme", "repo", "proj", "2.1.0")
        assert "acme/repo" in full_md
        assert "v2.1.0" in full_md

    def test_full_md_contains_timestamp(self, tool3, frozen_datetime):
        full_md, _ = tool3.build_full_output("doc", "gaps", "acme", "repo", "proj", "1.0.0")
        assert FIXED_DATETIME_STR in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3, frozen_datetime):
        _, gap_only_md = tool3.build_full_output(
            "doc", "1. A question?", "acme", "repo", "Underwriting Risk Classification", "3.0.0"
        )
        assert "Underwriting Risk Classification" in gap_only_md
        assert "v3.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, tool3, frozen_datetime):
        gaps = "1. Question one?\n2. Question two?"
        _, gap_only_md = tool3.build_full_output("doc", gaps, "acme", "repo", "proj", "1.0.0")
        assert "1. Question one?" in gap_only_md
        assert "2. Question two?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, tool3, frozen_datetime):
        _, gap_only_md = tool3.build_full_output("doc", "gaps", "acme", "repo", "proj", "1.0.0")
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_contains_timestamp(self, tool3, frozen_datetime):
        _, gap_only_md = tool3.build_full_output("doc", "gaps", "acme", "repo", "proj", "1.0.0")
        assert FIXED_DATETIME_STR in gap_only_md

    def test_returns_tuple_of_two_strings(self, tool3, frozen_datetime):
        result = tool3.build_full_output("doc", "gaps", "acme", "repo", "proj", "1.0.0")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_empty_doc_string(self, tool3, frozen_datetime):
        full_md, gap