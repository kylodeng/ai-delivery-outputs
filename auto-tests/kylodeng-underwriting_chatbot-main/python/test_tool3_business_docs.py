"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, file assembly
    - build_full_output(): markdown assembly, gap-only document, edge cases

Mocks used:
    - shared.call_claude           (patched via unittest.mock.patch)
    - shared.get_repo_files        (patched via unittest.mock.patch)
    - shared.write_output_file     (patched via unittest.mock.patch)
    - shared.send_email            (patched via unittest.mock.patch)
    - shared.email_html            (patched via unittest.mock.patch)
    - shared.write_audit_entry     (patched via unittest.mock.patch)
    - datetime.datetime.utcnow     (patched to return a fixed timestamp)

TODOs:
    - TODO: Integration test with a real Claude API key (requires secret injection)
    - TODO: Test __main__ block behaviour (requires subprocess or importlib reload)
    - TODO: Test write_output_file path construction for non-ASCII project names
"""

import sys
import os
import types
import importlib
from unittest.mock import patch, MagicMock, call
import pytest
import datetime

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import the script without
# the real dependency being installed.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude        = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    shared.get_repo_files     = MagicMock(return_value={"README.md": "# Hello"})
    shared.write_output_file  = MagicMock(return_value="https://github.com/output/file")
    shared.send_email         = MagicMock()
    shared.email_html         = MagicMock(return_value="<html>email</html>")
    shared.write_audit_entry  = MagicMock()
    shared.OUTPUT_REPO_OWNER  = "test-owner"
    shared.OUTPUT_REPO        = "test-repo"
    return shared

# Insert the stub BEFORE importing the module under test
_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

# Now import the module under test
import importlib.util, pathlib

_SCRIPT = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool3_business_docs.py"
_spec   = importlib.util.spec_from_file_location("tool3_business_docs", _SCRIPT)
_mod    = importlib.util.module_from_spec(_spec)
# Re-inject stub into the module's namespace after load
_spec.loader.exec_module(_mod)

generate_biz_doc  = _mod.generate_biz_doc
build_full_output = _mod.build_full_output

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_NOW_STR  = "2024-06-15"
FIXED_DATETIME = datetime.datetime(2024, 6, 15, 10, 30, 0)


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared-module mocks before every test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.email_html.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()

    # Default sensible return values
    _shared_stub.call_claude.return_value      = "doc content\n---GAPS---\n1. A question?"
    _shared_stub.get_repo_files.return_value   = {"README.md": "# Hello"}
    _shared_stub.write_output_file.return_value = "https://github.com/output/file"
    yield


@pytest.fixture()
def fixed_utcnow():
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FIXED_DATETIME
        # Preserve strftime behaviour
        mock_dt.datetime.utcnow.return_value.strftime = FIXED_DATETIME.strftime
        yield mock_dt


# ---------------------------------------------------------------------------
# Helper data
# ---------------------------------------------------------------------------

OWNER        = "acme-corp"
REPO         = "underwriting-risk"
PROJECT_NAME = "Underwriting Risk Classification"
VERSION      = "1.2.3"
RUN_URL      = "https://github.com/acme-corp/underwriting-risk/actions/runs/42"

SAMPLE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent..."}}',
    "README.md": "# Underwriting Risk Platform\nThis repo contains the ML pipeline.",
}

# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, fixed_utcnow):
        """Claude returns well-formed response with ---GAPS--- delimiter."""
        _shared_stub.get_repo_files.return_value = SAMPLE_FILES
        _shared_stub.call_claude.return_value = (
            "# Solution overview: Underwriting Risk Classification\nSome content."
            "\n---GAPS---\n"
            "1. What is the target go-live date?\n2. Who is the business sponsor?"
        )

        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the target go-live date?" in gaps
        assert "2. Who is the business sponsor?" in gaps

    def test_missing_delimiter_falls_back_gracefully(self, fixed_utcnow):
        """When Claude omits ---GAPS---, doc is whole response and gaps is fallback message."""
        _shared_stub.call_claude.return_value = "Just a document with no delimiter at all."

        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == "Just a document with no delimiter at all."
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_expected_extensions(self, fixed_utcnow):
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        call_args = _shared_stub.get_repo_files.call_args
        positional = call_args[0]
        assert positional[0] == OWNER
        assert positional[1] == REPO
        extensions = positional[2]
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions
        assert call_args[1].get("max_files", call_args[0][3] if len(call_args[0]) > 3 else None) == 20 or \
               call_args[0][3] == 20 or call_args[1].get("max_files") == 20

    def test_call_claude_receives_formatted_prompt(self, fixed_utcnow):
        """SYSTEM prompt is formatted with project_name, version, date before sending."""
        _shared_stub.get_repo_files.return_value = {"a.py": "print('hello')"}
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        claude_call = _shared_stub.call_claude.call_args
        prompt_arg  = claude_call[0][0]   # first positional arg = formatted SYSTEM
        assert PROJECT_NAME in prompt_arg
        assert VERSION in prompt_arg
        # Date from fixed_utcnow
        assert FIXED_NOW_STR in prompt_arg

    def test_call_claude_receives_files_in_user_message(self, fixed_utcnow):
        _shared_stub.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "test"}'
        }
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_message = _shared_stub.call_claude.call_args[0][1]
        assert f"Repo: {OWNER}/{REPO}" in user_message
        assert "backend/model_card.json" in user_message
        assert '{"model_name": "test"}' in user_message

    def test_large_file_content_is_truncated_to_3000_chars(self, fixed_utcnow):
        """File content longer than 3000 chars is sliced before being sent to Claude."""
        _shared_stub.get_repo_files.return_value = {
            "big_file.py": "x" * 5000
        }
        generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_message = _shared_stub.call_claude.call_args[0][1]
        # The snippet embedded in the user message must not exceed 3000 x's
        assert "x" * 3001 not in user_message
        assert "x" * 3000 in user_message

    def test_empty_repo_files_handled(self, fixed_utcnow):
        """No files returned — function should still call Claude and return results."""
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = "empty doc\n---GAPS---\n1. Question?"

        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == "empty doc"
        assert "1. Question?" in gaps

    def test_only_one_split_on_first_delimiter_occurrence(self, fixed_utcnow):
        """If ---GAPS--- appears multiple times only split on first occurrence."""
        _shared_stub.call_claude.return_value = (
            "doc\n---GAPS---\ngaps section\n---GAPS---\nextra stuff"
        )

        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == "doc"
        assert "gaps section" in gaps
        assert "extra stuff" in gaps  # second delimiter not split, stays in gaps
        assert "---GAPS---" not in doc

    def test_return_type_is_tuple_of_two_strings(self, fixed_utcnow):
        result = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_whitespace_stripped_from_doc_and_gaps(self, fixed_utcnow):
        _shared_stub.call_claude.return_value = (
            "   \n  doc content   \n\n---GAPS---\n\n  1. A gap question?  \n  "
        )

        doc, gaps = generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    @pytest.mark.parametrize("project_name,version", [
        ("My Project", "0.1.0"),
        ("Underwriting Risk Classification", "1.2.3"),
        ("FinanceApp", "99.0.0-beta"),
        ("αβγ Project", "2.0.0"),          # Unicode project name
        ("", "0.0.1"),                     # Empty project name edge case
    ])
    def test_various_project_name_version_combinations(self, project_name, version, fixed_utcnow):
        """Parametrised: generate_biz_doc should not raise for varied inputs."""
        _shared_stub.call_claude.return_value = f"doc for {project_name}\n---GAPS---\n1. Q?"

        doc, gaps = generate_biz_doc(OWNER, REPO, project_name, version, RUN_URL)

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_call_claude_exception_propagates(self, fixed_utcnow):
        """Errors from call_claude should propagate unhandled."""
        _shared_stub.call_claude.side_effect = RuntimeError("API failure")

        with pytest.raises(RuntimeError, match="API failure"):
            generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    def test_get_repo_files_exception_propagates(self, fixed_utcnow):
        _shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unavailable")

        with pytest.raises(ConnectionError, match="GitHub unavailable"):
            generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    SAMPLE_DOC  = "# Solution overview: Underwriting Risk Classification\nSome content here."
    SAMPLE_GAPS = "1. What is the go-live date?\n2. Who is the business sponsor?\n3. Who are the key users?"

    def test_happy_path_returns_two_strings(self, fixed_utcnow):
        full_md, gap_only_md = build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, fixed_utcnow):
        full_md, _ = build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "Solution overview: Underwriting Risk Classification" in full_md
        assert "Some content here." in full_md

    def test_full_md_contains_gap_section(self, fixed_utcnow):
        full_md, _ = build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "Gap Questionnaire" in full_md
        assert "1. What is the go-live date?" in full_md

    def test_full_md_contains_source_attribution(self, fixed_utcnow):
        full_md, _ = build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert f"{OWNER}/{REPO}" in full_md
        assert VERSION in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_and_version(self, fixed_utcnow):
        _, gap_only_md = build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert PROJECT_NAME in gap_only_md
        assert VERSION in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, fixed_utcnow):
        _, gap_only_md = build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS,
            OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "1. What is the go-live date?" in gap