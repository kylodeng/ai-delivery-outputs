"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, file-content truncation assembly
    - build_full_output(): full markdown assembly, gap-only markdown assembly, boundary values
    - __main__ block logic (via importlib / subprocess simulation patterns)
    - SYSTEM prompt template formatting

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
    - TODO: Integration test that verifies the real Claude API response shape
    - TODO: Test __main__ block FAILED branch (requires importlib runpy or subprocess)
    - TODO: Verify write_output_file receives exact byte content (needs richer fixture)
"""

import importlib
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with a fake "shared" dependency
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "ai-org"
FAKE_OUTPUT_REPO = "ai-outputs"


def _make_shared_stub():
    """Return a minimal stub module that satisfies tool3 imports."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value="https://github.com/ai-org/ai-outputs/blob/main/file.md")
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html>body</html>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    shared.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return shared


@pytest.fixture()
def shared_stub():
    """Install a fresh shared stub into sys.modules for every test."""
    stub = _make_shared_stub()
    with patch.dict(sys.modules, {"shared": stub}):
        yield stub


@pytest.fixture()
def tool3(shared_stub):
    """Return the tool3_business_docs module with shared stubbed out."""
    # Force re-import so the stub is picked up every time
    mod_name = "tool3_business_docs"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    abs_dir = os.path.abspath(script_dir)
    if abs_dir not in sys.path:
        sys.path.insert(0, abs_dir)

    import tool3_business_docs as t3
    return t3


# ---------------------------------------------------------------------------
# Fixtures – synthetic / realistic data
# ---------------------------------------------------------------------------

FAKE_FILES = {
    "src/main.py": "print('hello world')" * 10,
    "infrastructure/main.tf": 'resource "aws_s3_bucket" "data" {}',
    "README.md": "# My Project\nThis project does insurance stuff.",
}

CLAUDE_RESPONSE_WITH_DELIMITER = (
    "# Solution overview: MyProject\n"
    "Some executive summary here.\n"
    "---GAPS---\n"
    "1. What is the target go-live date?\n"
    "2. Who are the primary stakeholders?\n"
)

CLAUDE_RESPONSE_WITHOUT_DELIMITER = (
    "# Solution overview: MyProject\n"
    "Some executive summary here — no gaps section."
)


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------


class TestGenerateBizDoc:

    FIXED_DATE = "2024-06-15"

    @pytest.fixture(autouse=True)
    def _freeze_time(self):
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value.strftime.return_value = self.FIXED_DATE
            yield mock_dt

    def test_happy_path_with_delimiter(self, tool3, shared_stub):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        shared_stub.get_repo_files.return_value = FAKE_FILES
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER

        doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyProject", "1.2.3", "https://ci.example.com/run/1")

        assert "Solution overview" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the target go-live date?" in gaps
        assert "2. Who are the primary stakeholders?" in gaps

    def test_happy_path_without_delimiter(self, tool3, shared_stub):
        """Claude omits ---GAPS--- → fallback message is returned."""
        shared_stub.get_repo_files.return_value = FAKE_FILES
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITHOUT_DELIMITER

        doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyProject", "1.0.0", "https://ci.example.com")

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, shared_stub):
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        tool3.generate_biz_doc("owner", "repo", "Proj", "0.1.0", "url")

        shared_stub.get_repo_files.assert_called_once()
        _, kwargs = shared_stub.get_repo_files.call_args
        pos_args = shared_stub.get_repo_files.call_args.args
        # extensions must include common source types
        extensions_arg = pos_args[2] if len(pos_args) > 2 else kwargs.get("extensions", [])
        for ext in [".py", ".md", ".tf"]:
            assert ext in extensions_arg

    def test_max_files_limit_passed(self, tool3, shared_stub):
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        tool3.generate_biz_doc("owner", "repo", "Proj", "0.1.0", "url")

        call_kwargs = shared_stub.get_repo_files.call_args
        # max_files should be a keyword argument equal to 20
        assert call_kwargs.kwargs.get("max_files") == 20 or (
            len(call_kwargs.args) > 3 and call_kwargs.args[3] == 20
        )

    def test_call_claude_receives_system_prompt_with_project_name(self, tool3, shared_stub):
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        tool3.generate_biz_doc("owner", "repo", "InsuranceBot", "2.0.0", "url")

        system_prompt_arg = shared_stub.call_claude.call_args.args[0]
        assert "InsuranceBot" in system_prompt_arg

    def test_call_claude_receives_system_prompt_with_version(self, tool3, shared_stub):
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        tool3.generate_biz_doc("owner", "repo", "MyApp", "3.1.4", "url")

        system_prompt_arg = shared_stub.call_claude.call_args.args[0]
        assert "3.1.4" in system_prompt_arg

    def test_call_claude_receives_system_prompt_with_date(self, tool3, shared_stub):
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        tool3.generate_biz_doc("owner", "repo", "MyApp", "1.0.0", "url")

        system_prompt_arg = shared_stub.call_claude.call_args.args[0]
        assert self.FIXED_DATE in system_prompt_arg

    def test_call_claude_user_message_contains_owner_repo(self, tool3, shared_stub):
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER
        tool3.generate_biz_doc("sun-life", "generations-ii", "Gen II", "1.0.0", "url")

        user_msg = shared_stub.call_claude.call_args.args[1]
        assert "sun-life" in user_msg
        assert "generations-ii" in user_msg

    def test_file_content_truncated_to_3000_chars(self, tool3, shared_stub):
        """Each file content must be sliced to max 3000 chars before sending."""
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER

        tool3.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "url")

        user_msg = shared_stub.call_claude.call_args.args[1]
        # The truncated portion should appear; the full 5000-char string should not
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files(self, tool3, shared_stub):
        """No files returned → Claude still gets called (empty files string)."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = CLAUDE_RESPONSE_WITH_DELIMITER

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "Empty", "0.0.1", "url")
        assert doc  # non-empty string returned

    def test_multiple_gap_delimiter_occurrences_splits_on_first(self, tool3, shared_stub):
        """Only the first ---GAPS--- delimiter should be used for splitting."""
        shared_stub.call_claude.return_value = (
            "Doc part\n---GAPS---\nGap part one\n---GAPS---\nGap part two"
        )
        doc, gaps = tool3.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "url")

        assert "Doc part" in doc
        assert "---GAPS---" not in doc
        # Gaps should contain everything after first delimiter
        assert "Gap part one" in gaps
        assert "Gap part two" in gaps

    def test_returns_stripped_strings(self, tool3, shared_stub):
        shared_stub.call_claude.return_value = "  doc content  \n---GAPS---\n  gaps content  \n"
        doc, gaps = tool3.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------


class TestBuildFullOutput:

    FIXED_DATETIME = "2024-06-15 10:30 UTC"

    @pytest.fixture(autouse=True)
    def _freeze_time(self):
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value.strftime.return_value = self.FIXED_DATETIME
            yield mock_dt

    def test_full_md_contains_doc(self, tool3):
        full_md, _ = tool3.build_full_output(
            "## My Doc", "1. What is X?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert "## My Doc" in full_md

    def test_full_md_contains_gaps(self, tool3):
        full_md, _ = tool3.build_full_output(
            "## My Doc", "1. What is X?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert "1. What is X?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "gaps", "owner", "repo", "InsurancePlan", "2.3.0"
        )
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_ai_attribution(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "gaps", "owner", "repo", "MyProject", "1.0.0"
        )
        assert "AI Delivery Bot" in full_md

    def test_full_md_contains_source_repo(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "gaps", "acme-org", "insurance-api", "MyProject", "1.0.0"
        )
        assert "acme-org/insurance-api" in full_md

    def test_full_md_contains_version(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "gaps", "owner", "repo", "MyProject", "9.9.9"
        )
        assert "9.9.9" in full_md

    def test_gap_only_md_contains_project_name(self, tool3):
        _, gap_only = tool3.build_full_output(
            "doc", "1. Question one?", "owner", "repo", "GenerationsII", "1.0.0"
        )
        assert "GenerationsII" in gap_only

    def test_gap_only_md_contains_gap_questions(self, tool3):
        _, gap_only = tool3.build_full_output(
            "doc", "1. Question one?\n2. Question two?", "owner", "repo", "Proj", "1.0.0"
        )
        assert "1. Question one?" in gap_only
        assert "2. Question two?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, tool3):
        _, gap_only = tool3.build_full_output(
            "doc", "gaps", "owner", "repo", "Proj", "1.0.0"
        )
        assert FAKE_OUTPUT_REPO_OWNER in gap_only
        assert FAKE_OUTPUT_REPO in gap_only

    def test_gap_only_md_contains_timestamp(self, tool3):
        _, gap_only = tool3.build_full_output(
            "doc", "gaps", "owner", "repo", "Proj", "1.0.0"
        )
        assert self.FIXED_DATETIME in gap_only

    def test_returns_two_strings(self, tool3):
        result = tool3.build_full_output("doc", "gaps", "o", "r", "p", "v")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_empty_doc_and_gaps(self, tool3):
        """Should not raise even with empty strings."""
        full_