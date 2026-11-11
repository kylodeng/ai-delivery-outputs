"""
Test module for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty file list
- build_full_output(): content assembly, metadata injection, edge cases (empty doc/gaps)
- Main block execution paths: success flow, exception/failure flow

Mocks used:
- shared.call_claude          → unittest.mock.patch
- shared.get_repo_files       → unittest.mock.patch
- shared.write_output_file    → unittest.mock.patch
- shared.send_email           → unittest.mock.patch
- shared.email_html           → unittest.mock.patch
- shared.write_audit_entry    → unittest.mock.patch
- datetime.datetime           → unittest.mock.patch (for deterministic timestamps)

TODOs:
- TODO: Integration test for the full __main__ block requires a live GitHub Actions
        environment with real env-vars; stub is marked skip below.
- TODO: Verify exact Claude prompt formatting once SYSTEM template changes are stabilised.
"""

import sys
import os
import types
import datetime
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal `shared` stub so the import succeeds without
# the real module on PYTHONPATH.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.call_claude       = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?")
    shared.get_repo_files    = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/output/file")
    shared.send_email        = MagicMock()
    shared.email_html        = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-output-owner"
    shared.OUTPUT_REPO       = "test-output-repo"
    return shared


# Insert stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

# Ensure the script directory is on the path so the import works
_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# Now import the module under test
import tool3_business_docs as biz_docs


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_NOW_STR      = "2024-06-15"
FAKE_NOW_FULL_STR = "2024-06-15 10:30 UTC"

SAMPLE_DOC_PART  = "# Solution overview: MyProject\nSome content here."
SAMPLE_GAPS_PART = "1. What is the target go-live date?\n2. Who are the key users?"

SAMPLE_RAW_WITH_GAPS    = f"{SAMPLE_DOC_PART}\n---GAPS---\n{SAMPLE_GAPS_PART}"
SAMPLE_RAW_WITHOUT_GAPS = "# Solution overview: MyProject\nSome content without delimiter."


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stubs before every test."""
    _shared_stub.call_claude.reset_mock()
    _shared_stub.get_repo_files.reset_mock()
    _shared_stub.write_output_file.reset_mock()
    _shared_stub.send_email.reset_mock()
    _shared_stub.email_html.reset_mock()
    _shared_stub.write_audit_entry.reset_mock()

    # Restore sensible defaults
    _shared_stub.call_claude.return_value       = SAMPLE_RAW_WITH_GAPS
    _shared_stub.get_repo_files.return_value    = {}
    _shared_stub.write_output_file.return_value = "https://github.com/output/file"
    _shared_stub.email_html.return_value        = "<html>body</html>"
    yield


@pytest.fixture()
def fixed_datetime():
    """Patch datetime.datetime so utcnow() returns a known value."""
    fixed = datetime.datetime(2024, 6, 15, 10, 30, 0)
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = fixed
        mock_dt.datetime.utcnow.return_value.strftime = fixed.strftime
        # Make strftime callable on the mock's return value properly
        mock_dt.datetime.utcnow = lambda: fixed
        yield fixed


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self):
        """Claude returns a response with ---GAPS--- delimiter; both parts extracted correctly."""
        _shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        _shared_stub.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = biz_docs.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0",
            run_url="https://github.com/run/1"
        )

        assert "Solution overview" in doc
        assert doc.strip() == SAMPLE_DOC_PART
        assert "What is the target go-live date?" in gaps
        assert "Who are the key users?" in gaps

    def test_missing_gaps_delimiter_falls_back_gracefully(self):
        """When Claude omits ---GAPS---, doc_part = full response and gaps = fallback message."""
        _shared_stub.call_claude.return_value = SAMPLE_RAW_WITHOUT_GAPS

        doc, gaps = biz_docs.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0",
            run_url="https://github.com/run/1"
        )

        assert doc.strip() == SAMPLE_RAW_WITHOUT_GAPS.strip()
        assert "Claude could not extract gap questions" in gaps
        assert "manually" in gaps

    def test_get_repo_files_called_with_correct_extensions(self):
        """Verify the correct file extensions and max_files are passed to get_repo_files."""
        biz_docs.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="Proj", version="2.0",
            run_url="https://github.com/run/2"
        )

        call_args = _shared_stub.get_repo_files.call_args
        args, kwargs = call_args
        assert args[0] == "acme"
        assert args[1] == "my-repo"
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert args[2] == expected_exts
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_project_name_in_prompt(self):
        """The formatted SYSTEM prompt sent to Claude contains the project name."""
        biz_docs.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="InsurancePortal", version="3.1",
            run_url="https://github.com/run/3"
        )

        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg

    def test_call_claude_receives_version_in_prompt(self):
        """The formatted SYSTEM prompt sent to Claude contains the version."""
        biz_docs.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="Proj", version="4.2.1",
            run_url="https://github.com/run/4"
        )

        prompt_arg = _shared_stub.call_claude.call_args[0][0]
        assert "4.2.1" in prompt_arg

    def test_call_claude_receives_repo_files_in_user_message(self):
        """File contents are embedded in the user message passed to call_claude."""
        _shared_stub.get_repo_files.return_value = {
            "app.py": "def main(): pass",
        }

        biz_docs.generate_biz_doc(
            owner="acme", repo="my-repo",
            project_name="Proj", version="1.0",
            run_url="https://github.com"
        )

        user_msg = _shared_stub.call_claude.call_args[0][1]
        assert "app.py" in user_msg
        assert "def main(): pass" in user_msg

    def test_empty_repo_no_files(self):
        """An empty repo (no files) should still produce a valid doc/gaps pair."""
        _shared_stub.get_repo_files.return_value = {}
        _shared_stub.call_claude.return_value = SAMPLE_RAW_WITH_GAPS

        doc, gaps = biz_docs.generate_biz_doc(
            owner="acme", repo="empty-repo",
            project_name="EmptyProj", version="0.0.1",
            run_url="https://github.com"
        )

        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_multiple_gaps_delimiters_only_first_split_used(self):
        """Only the first ---GAPS--- delimiter is used for splitting."""
        raw = "Doc part\n---GAPS---\nGaps part\n---GAPS---\nExtra stuff"
        _shared_stub.call_claude.return_value = raw

        doc, gaps = biz_docs.generate_biz_doc(
            owner="acme", repo="repo", project_name="P", version="1",
            run_url="https://github.com"
        )

        assert "Doc part" in doc
        assert "Gaps part" in gaps
        assert "Extra stuff" in gaps  # remainder goes into gaps

    def test_file_content_truncated_to_3000_chars(self):
        """Files longer than 3000 chars are truncated in the user message."""
        long_content = "x" * 5000
        _shared_stub.get_repo_files.return_value = {"big_file.py": long_content}

        biz_docs.generate_biz_doc(
            owner="acme", repo="repo", project_name="P", version="1",
            run_url="https://github.com"
        )

        user_msg = _shared_stub.call_claude.call_args[0][1]
        # The embedded slice is [:3000] so we should not find 5000 x's
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    @pytest.mark.parametrize("project_name,version", [
        ("Generations II", "1.0.0"),
        ("List of Designated Hospitals in Mainland China", "2.3"),
        ("Global Network Hospital List for Cashless Arrangement", "0.1.0-beta"),
    ])
    def test_various_project_names_and_versions(self, project_name, version):
        """Parametrised: different project names/versions from synthetic data."""
        _shared_stub.call_claude.return_value = (
            f"# Solution overview: {project_name}\nContent.\n---GAPS---\n1. A question?"
        )

        doc, gaps = biz_docs.generate_biz_doc(
            owner="sunlife", repo="insurance-portal",
            project_name=project_name, version=version,
            run_url="https://github.com"
        )

        assert project_name in doc or "Solution overview" in doc
        assert "A question?" in gaps

    def test_gaps_part_stripped_of_leading_trailing_whitespace(self):
        """Gaps part has leading/trailing whitespace stripped."""
        raw = "Doc\n---GAPS---\n\n  1. Question?  \n\n"
        _shared_stub.call_claude.return_value = raw

        _, gaps = biz_docs.generate_biz_doc(
            owner="a", repo="b", project_name="P", version="1",
            run_url="https://github.com"
        )

        assert gaps == gaps.strip()

    def test_doc_part_stripped_of_leading_trailing_whitespace(self):
        """Doc part has leading/trailing whitespace stripped."""
        raw = "   Doc content   \n---GAPS---\n1. Q?"
        _shared_stub.call_claude.return_value = raw

        doc, _ = biz_docs.generate_biz_doc(
            owner="a", repo="b", project_name="P", version="1",
            run_url="https://github.com"
        )

        assert doc == doc.strip()


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_full_md_contains_doc_content(self):
        """Full markdown output must contain the doc part."""
        full_md, _ = biz_docs.build_full_output(
            doc=SAMPLE_DOC_PART, gaps=SAMPLE_GAPS_PART,
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0"
        )
        assert SAMPLE_DOC_PART in full_md

    def test_full_md_contains_gaps_content(self):
        """Full markdown output must contain the gaps part."""
        full_md, _ = biz_docs.build_full_output(
            doc=SAMPLE_DOC_PART, gaps=SAMPLE_GAPS_PART,
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0"
        )
        assert SAMPLE_GAPS_PART in full_md

    def test_full_md_contains_gap_questionnaire_header(self):
        """Full markdown includes the Gap Questionnaire section header."""
        full_md, _ = biz_docs.build_full_output(
            doc=SAMPLE_DOC_PART, gaps=SAMPLE_GAPS_PART,
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_attribution_footer(self):
        """Full markdown includes the AI Delivery Bot attribution line."""
        full_md, _ = biz_docs.build_full_output(
            doc=SAMPLE_DOC_PART, gaps=SAMPLE_GAPS_PART,
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0"
        )
        assert "AI Delivery Bot" in full_md
        assert "acme/my-repo" in full_md
        assert "v1.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self):
        """Standalone gap questionnaire includes the project name and version."""
        _, gap_only_md = biz_docs.build_full_output(
            doc=SAMPLE_DOC_PART, gaps=SAMPLE_GAPS_PART,
            owner="acme", repo="my-repo",
            project_name="MyProject", version="1.0.0"
        )
        assert "MyProject" in gap_only_md
        assert "v1.0.0" in gap_only_md

    def test_gap_only