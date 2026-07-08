"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with ---GAPS--- delimiter), missing delimiter fallback,
      empty files dict, truncation behaviour in files_str construction
    - build_full_output(): happy path output structure, gap_only_md structure,
      correct embedding of metadata, empty gaps handling

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (frozen timestamp)
    - os.environ                  → monkeypatch / unittest.mock.patch.dict

TODOs:
    - TODO: test __main__ block with subprocess or importlib once full env is available
    - TODO: test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME are None (current code
            passes None to generate_biz_doc without guard)
    - TODO: integration test against real Claude endpoint (needs API key in CI secret)
"""

import sys
import os
import types
import datetime
import importlib
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal `shared` stub so the import of tool3 does not
# blow up when the real shared module is absent from the test environment.
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = "shared"

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude         = MagicMock(return_value="doc text\n---GAPS---\n1. Question?")
    stub.get_repo_files      = MagicMock(return_value={})
    stub.write_output_file   = MagicMock(return_value="https://github.com/output/file")
    stub.send_email          = MagicMock()
    stub.email_html          = MagicMock(return_value="<html/>")
    stub.write_audit_entry   = MagicMock()
    stub.OUTPUT_REPO_OWNER   = "output-owner"
    stub.OUTPUT_REPO         = "output-repo"
    return stub


# Insert stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Ensure .github/scripts is on the path so `tool3_business_docs` can be imported
_SCRIPTS_DIR = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, ".github", "scripts"
)
if os.path.isdir(_SCRIPTS_DIR):
    sys.path.insert(0, os.path.abspath(_SCRIPTS_DIR))

# Now import the module under test
import tool3_business_docs as t3  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FROZEN_NOW_DT = datetime.datetime(2024, 6, 15, 10, 30, 0)
FROZEN_DATE   = "2024-06-15"
FROZEN_DT_STR = "2024-06-15 10:30 UTC"

OWNER   = "acme-corp"
REPO    = "underwriting-platform"
PROJECT = "Underwriting Risk Classification"
VERSION = "1.2.0"
RUN_URL = "https://github.com/actions/runs/42"


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset all shared stub mocks between tests."""
    shared = sys.modules["shared"]
    for attr in ("call_claude", "get_repo_files", "write_output_file",
                 "send_email", "email_html", "write_audit_entry"):
        getattr(shared, attr).reset_mock()
    yield


@pytest.fixture()
def frozen_utcnow():
    """Patch datetime.datetime.utcnow to return a fixed timestamp."""
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FROZEN_NOW_DT
        # Preserve strftime on the return value
        mock_dt.datetime.utcnow.return_value.strftime = FROZEN_NOW_DT.strftime
        yield mock_dt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sample_files():
    return {
        "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
        "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "prompt text"}}',
        "README.md": "# Underwriting Platform\nThis repo contains the underwriting risk model.",
    }


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self):
        """Claude returns a response with ---GAPS--- delimiter; parts are split correctly."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = _sample_files()
        shared.call_claude.return_value = (
            "# Solution overview: Underwriting\n\nSome overview text."
            "\n---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the business sponsor?"
        )

        doc, gaps = t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "What is the go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_fallback(self):
        """When Claude omits ---GAPS---, gaps fall back to the standard message."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = _sample_files()
        shared.call_claude.return_value = "Only a doc, no delimiter here."

        doc, gaps = t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert doc == "Only a doc, no delimiter here."
        assert "Claude could not extract gap questions" in gaps

    def test_empty_files_dict(self):
        """No files in repo — files_str is empty; call_claude still called."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        shared.call_claude.assert_called_once()
        assert doc == "doc"
        assert gaps == "gaps"

    def test_get_repo_files_called_with_correct_extensions(self):
        """get_repo_files is invoked with the expected file extensions and max_files."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "x\n---GAPS---\ny"

        t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        args, kwargs = shared.get_repo_files.call_args
        assert args[0] == OWNER
        assert args[1] == REPO
        extensions = args[2]
        for ext in [".py", ".md", ".tf", ".yaml"]:
            assert ext in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_project_and_version_in_prompt(self):
        """The formatted prompt passed to call_claude contains project_name and version."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        prompt_arg = shared.call_claude.call_args[0][0]
        assert PROJECT in prompt_arg
        assert VERSION in prompt_arg

    def test_file_content_truncated_to_3000_chars(self):
        """File contents longer than 3000 chars are truncated in the prompt."""
        shared = sys.modules["shared"]
        long_content = "x" * 5000
        shared.get_repo_files.return_value = {"big_file.py": long_content}
        shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        user_msg = shared.call_claude.call_args[0][1]
        # The truncated slice is 3000 chars; the original 5000 should not appear fully
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_multiple_delimiter_occurrences_only_first_split(self):
        """Only the first ---GAPS--- is used as the split point."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = (
            "doc content\n---GAPS---\nfirst gaps\n---GAPS---\nextra"
        )

        doc, gaps = t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert doc == "doc content"
        assert "first gaps" in gaps
        assert "extra" in gaps  # second occurrence remains in gaps section

    def test_whitespace_stripped_from_parts(self):
        """Leading/trailing whitespace is stripped from both doc and gaps."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "  \ndoc\n  \n---GAPS---\n  \ngaps\n  "

        doc, gaps = t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        assert doc == "doc"
        assert gaps == "gaps"

    def test_call_claude_user_message_contains_owner_repo(self):
        """The user message to call_claude identifies the source repo."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "d\n---GAPS---\ng"

        t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

        user_msg = shared.call_claude.call_args[0][1]
        assert OWNER in user_msg
        assert REPO in user_msg

    def test_call_claude_raises_propagates(self):
        """If call_claude raises, the exception propagates from generate_biz_doc."""
        shared = sys.modules["shared"]
        shared.get_repo_files.return_value = {}
        shared.call_claude.side_effect = RuntimeError("Claude API error")

        with pytest.raises(RuntimeError, match="Claude API error"):
            t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)

    def test_get_repo_files_raises_propagates(self):
        """If get_repo_files raises, the exception propagates."""
        shared = sys.modules["shared"]
        shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError):
            t3.generate_biz_doc(OWNER, REPO, PROJECT, VERSION, RUN_URL)


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, doc="# Doc\nSome content", gaps="1. What is the date?"):
        return t3.build_full_output(doc, gaps, OWNER, REPO, PROJECT, VERSION)

    def test_returns_two_strings(self):
        full_md, gap_only_md = self._call()
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self):
        full_md, _ = self._call(doc="# My Doc\nContent here")
        assert "# My Doc" in full_md
        assert "Content here" in full_md

    def test_full_md_contains_gaps(self):
        full_md, _ = self._call(gaps="1. Specific question?")
        assert "1. Specific question?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self):
        full_md, _ = self._call()
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self):
        full_md, _ = self._call()
        assert OWNER in full_md
        assert REPO in full_md
        assert VERSION in full_md

    def test_gap_only_md_contains_project_and_version_in_title(self):
        _, gap_only_md = self._call()
        assert PROJECT in gap_only_md
        assert VERSION in gap_only_md

    def test_gap_only_md_contains_gaps(self):
        _, gap_only_md = self._call(gaps="2. Who owns this?")
        assert "2. Who owns this?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self):
        _, gap_only_md = self._call()
        shared = sys.modules["shared"]
        assert shared.OUTPUT_REPO_OWNER in gap_only_md
        assert shared.OUTPUT_REPO in gap_only_md

    def test_full_md_ai_bot_attribution(self):
        full_md, _ = self._call()
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_estimated_time_hint(self):
        _, gap_only_md = self._call()
        assert "10-15 minutes" in gap_only_md

    def test_empty_gaps_string(self):
        """build_full_output should not crash with an empty gaps string."""
        full_md, gap_only_md = self._call(gaps="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_empty_doc_string(self):
        """build_full_output should not crash with an empty doc string."""
        full_md, gap_only_md = self._call(doc="")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_draft_disclaimer(self):
        full_md, _ = self._call()
        assert "Draft auto-generated" in full_md

    def test_gap_only_md_contains_generated_label(self):
        _, gap_only_md = self._call()
        assert "Generated" in gap_only_md

    def test_multiline_doc_preserved(self):
        doc = "# Title\n\n## Section\nLine 1\nLine 2"
        full_md, _ = self._call(doc=doc)
        assert "## Section" in full_md
        assert "Line 1" in full_md

    def test_multiline_gaps_preserved(self):
        gaps = "1. Question one?\n2. Question two?\n3. Question three?"
        full_md, gap_only_md = self._call(gaps=gaps)
        assert "Question one?" in full_md
        assert "Question three?" in gap_only_md

    @pytest.mark.parametrize("version", ["0.1.0", "2.0.0-rc1", "10.99.999"])
    def test_version_variants(