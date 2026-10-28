"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing delimiter, Claude errors
    - build_full_output(): markdown construction, gap questionnaire, metadata embedding
    - __main__ block behaviour (env-var driven entry point)

Mocks used:
    - shared.call_claude          → prevents real Anthropic API calls
    - shared.get_repo_files       → prevents real GitHub API calls
    - shared.write_output_file    → prevents real GitHub commit/push
    - shared.send_email           → prevents real SES/SMTP calls
    - shared.email_html           → returns deterministic HTML string
    - shared.write_audit_entry    → prevents real audit-log writes
    - datetime.datetime.utcnow    → frozen to 2024-06-15 12:00:00 for determinism

TODOs:
    - TODO: test __main__ FAILED branch end-to-end (requires importlib.reload with mocked env)
    - TODO: test write_output_file return-value propagation to send_email
    - TODO: test behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME are None (unset env)
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake "shared" module so we can import the target
# without having the real shared.py on the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-org"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a types.ModuleType that satisfies all imports in the target file."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="CLAUDE_OUTPUT")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-org/test-output-repo/blob/main/file.md")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>mock</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return mod


# ---------------------------------------------------------------------------
# Fixture: import the module under test with a fresh fake shared each time.
# ---------------------------------------------------------------------------

@pytest.fixture()
def biz_docs_module():
    """Import (or re-import) tool3_business_docs with a controlled fake shared module."""
    fake_shared = _make_fake_shared()
    with patch.dict(sys.modules, {"shared": fake_shared}):
        # Make sure the scripts directory is on the path so the file can be found
        scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)

        # Remove any previously cached version so imports are clean
        sys.modules.pop("tool3_business_docs", None)

        spec_path = os.path.join(
            os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
        )

        import importlib.util
        spec = importlib.util.spec_from_file_location("tool3_business_docs", spec_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tool3_business_docs"] = mod
        spec.loader.exec_module(mod)

    return mod, fake_shared


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


class _FakeDatetime(datetime.datetime):
    @classmethod
    def utcnow(cls):
        return FROZEN_DT


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, biz_docs_module):
        """Claude returns both sections separated by ---GAPS---."""
        mod, shared = biz_docs_module

        repo_files = {
            "main.py": "print('hello')",
            "README.md": "# My project",
        }
        shared.get_repo_files.return_value = repo_files
        shared.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content."
            "\n---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the sponsor?"
        )

        with patch("datetime.datetime", _FakeDatetime):
            doc, gaps = mod.generate_biz_doc("my-org", "my-repo", "MyProject", "1.0.0", "https://run-url")

        assert "Solution overview: MyProject" in doc
        assert "Some content." in doc
        assert "What is the go-live date?" in gaps
        assert "Who is the sponsor?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_falls_back_gracefully(self, biz_docs_module):
        """When Claude omits ---GAPS---, doc_part = full response, gaps_part = fallback message."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {"app.ts": "const x = 1;"}
        shared.call_claude.return_value = "Just a plain document with no delimiter."

        doc, gaps = mod.generate_biz_doc("org", "repo", "Proj", "0.0.1", "https://run")

        assert doc == "Just a plain document with no delimiter."
        assert "Claude could not extract gap questions" in gaps

    def test_empty_repo_files(self, biz_docs_module):
        """No files returned from repo — call_claude still called with empty files section."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "Doc\n---GAPS---\n1. Question?"

        doc, gaps = mod.generate_biz_doc("org", "repo", "Proj", "1.2.3", "https://run")

        assert doc == "Doc"
        assert "Question?" in gaps

        # Verify call_claude was called
        shared.call_claude.assert_called_once()
        call_args = shared.call_claude.call_args
        assert "org/repo" in call_args[0][1]

    def test_file_content_truncated_to_3000_chars(self, biz_docs_module):
        """File content longer than 3000 chars should be truncated in the prompt."""
        mod, shared = biz_docs_module

        long_content = "x" * 5000
        shared.get_repo_files.return_value = {"big_file.py": long_content}
        shared.call_claude.return_value = "Doc\n---GAPS---\n1. Q?"

        mod.generate_biz_doc("org", "repo", "P", "1.0", "url")

        call_args = shared.call_claude.call_args
        files_str_arg = call_args[0][1]
        # The truncated content slice [:3000] means "x"*3000, not 5000
        assert "x" * 3001 not in files_str_arg
        assert "x" * 3000 in files_str_arg

    def test_get_repo_files_called_with_correct_extensions(self, biz_docs_module):
        """get_repo_files must be called with the expected extension list."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "D\n---GAPS---\nQ"

        mod.generate_biz_doc("owner", "repo", "P", "v1", "url")

        shared.get_repo_files.assert_called_once()
        call_args = shared.get_repo_files.call_args
        extensions = call_args[0][2]
        for ext in [".py", ".js", ".ts", ".tf", ".yaml", ".md"]:
            assert ext in extensions

    def test_prompt_contains_project_name_version_date(self, biz_docs_module):
        """The formatted SYSTEM prompt sent to Claude must contain project_name, version, date."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "D\n---GAPS---\nQ"

        with patch("datetime.datetime", _FakeDatetime):
            mod.generate_biz_doc("org", "repo", "InsuranceApp", "2.3.4", "url")

        system_prompt = shared.call_claude.call_args[0][0]
        assert "InsuranceApp" in system_prompt
        assert "2.3.4" in system_prompt
        assert FROZEN_DATE_STR in system_prompt

    def test_multiple_files_joined_in_prompt(self, biz_docs_module):
        """Multiple repo files should each appear in the user message."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {
            "file_a.py": "code_a",
            "file_b.tf": "resource {}",
        }
        shared.call_claude.return_value = "D\n---GAPS---\nQ"

        mod.generate_biz_doc("org", "repo", "P", "1", "url")

        user_msg = shared.call_claude.call_args[0][1]
        assert "file_a.py" in user_msg
        assert "file_b.tf" in user_msg
        assert "code_a" in user_msg

    def test_only_first_delimiter_is_used(self, biz_docs_module):
        """Only the first ---GAPS--- should split the response."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = (
            "Part one\n---GAPS---\nGap section\n---GAPS---\nExtra junk"
        )

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == "Part one"
        assert "Extra junk" in gaps  # everything after first split stays in gaps

    def test_whitespace_stripped_from_parts(self, biz_docs_module):
        """Leading/trailing whitespace in both parts should be stripped."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "   Doc content   \n---GAPS---\n   Gap content   \n"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "u")

        assert doc == "Doc content"
        assert gaps == "Gap content"

    def test_call_claude_raises_propagates(self, biz_docs_module):
        """If call_claude raises, generate_biz_doc should propagate the exception."""
        mod, shared = biz_docs_module

        shared.get_repo_files.return_value = {}
        shared.call_claude.side_effect = RuntimeError("API quota exceeded")

        with pytest.raises(RuntimeError, match="API quota exceeded"):
            mod.generate_biz_doc("o", "r", "P", "1", "u")

    def test_get_repo_files_raises_propagates(self, biz_docs_module):
        """If get_repo_files raises, generate_biz_doc should propagate the exception."""
        mod, shared = biz_docs_module

        shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_biz_doc("o", "r", "P", "1", "u")


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    @pytest.fixture()
    def sample_inputs(self):
        return dict(
            doc="# Solution overview: TestProject\nSome body text.",
            gaps="1. What is the go-live date?\n2. Who is the business sponsor?",
            owner="my-org",
            repo="my-repo",
            project_name="TestProject",
            version="1.0.0",
        )

    def test_full_md_contains_doc_content(self, biz_docs_module, sample_inputs):
        mod, _ = biz_docs_module
        with patch("datetime.datetime", _FakeDatetime):
            full_md, _ = mod.build_full_output(**sample_inputs)
        assert "Solution overview: TestProject" in full_md
        assert "Some body text." in full_md

    def test_full_md_contains_gaps(self, biz_docs_module, sample_inputs):
        mod, _ = biz_docs_module
        with patch("datetime.datetime", _FakeDatetime):
            full_md, _ = mod.build_full_output(**sample_inputs)
        assert "What is the go-live date?" in full_md
        assert "Who is the business sponsor?" in full_md

    def test_full_md_contains_metadata_footer(self, biz_docs_module, sample_inputs):
        mod, _ = biz_docs_module
        with patch("datetime.datetime", _FakeDatetime):
            full_md, _ = mod.build_full_output(**sample_inputs)
        assert "my-org/my-repo" in full_md
        assert "1.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_and_version(self, biz_docs_module, sample_inputs):
        mod, _ = biz_docs_module
        with patch("datetime.datetime", _FakeDatetime):
            _, gap_only_md = mod.build_full_output(**sample_inputs)
        assert "TestProject" in gap_only_md
        assert "v1.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, biz_docs_module, sample_inputs):
        mod, _ = biz_docs_module
        with patch("datetime.datetime", _FakeDatetime):
            _, gap_only_md = mod.build_full_output(**sample_inputs)
        assert "What is the go-live date?" in gap_only_md
        assert "Who is the business sponsor?" in gap_only_md

    def test_gap_only_md_links_to_output_repo(self, biz_docs_module, sample_inputs):
        """The standalone gap questionnaire should link back to the output repo."""
        mod, _ = biz_docs_module
        with patch("datetime.datetime", _FakeDatetime):
            _, gap_only_md = mod.build_full_output(**sample_inputs)
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_full_md_contains_gap_questionnaire_section_header(self, biz_docs_module, sample_inputs):
        mod, _ = biz_docs_module
        with patch("datetime.datetime", _FakeDatetime):
            full_md, _ = mod.build_full_output(**sample_inputs)
        assert "Gap Questionnaire" in full_md

    def test_