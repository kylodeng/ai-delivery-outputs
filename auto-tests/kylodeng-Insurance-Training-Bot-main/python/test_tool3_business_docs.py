"""
Test module for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), error propagation
  - build_full_output(): markdown composition, gap questionnaire formatting, boundary values
  - __main__ block behaviour via subprocess / direct invocation helpers
  - SYSTEM prompt template formatting sanity

Mocks used:
  - shared.call_claude          (prevents real API calls)
  - shared.get_repo_files       (prevents real GitHub calls)
  - shared.write_output_file    (prevents real file/git operations)
  - shared.send_email           (prevents real email sending)
  - shared.email_html           (prevents real template rendering)
  - shared.write_audit_entry    (prevents real audit writes)
  - datetime.datetime.utcnow    (deterministic timestamps)

TODOs:
  - TODO: Integration test with a real Claude API key (requires secret injection)
  - TODO: Test __main__ block end-to-end via subprocess (needs full env fixture)
  - TODO: Test failure path in __main__ block (send_email on exception)
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool3 without the
# real dependency being installed.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.call_claude       = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    stub.get_repo_files    = MagicMock(return_value={"README.md": "# hello"})
    stub.write_output_file = MagicMock(return_value="https://github.com/output/file")
    stub.send_email        = MagicMock()
    stub.email_html        = MagicMock(return_value="<html>ok</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "test-owner"
    stub.OUTPUT_REPO       = "test-output-repo"
    return stub


@pytest.fixture(autouse=True)
def inject_shared_stub(monkeypatch):
    """Inject a fresh shared stub before every test and reload tool3."""
    stub = _make_shared_stub()
    monkeypatch.setitem(sys.modules, "shared", stub)
    # Also patch the path insertion so it does not blow up on CI
    monkeypatch.syspath_prepend(os.path.dirname(__file__))
    yield stub


@pytest.fixture()
def tool3(inject_shared_stub):
    """Return a freshly imported tool3 module."""
    # Remove any cached version
    sys.modules.pop("tool3_business_docs", None)
    # Adjust import path to find the source file
    scripts_dir = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts"
    )
    scripts_dir = os.path.normpath(scripts_dir)
    # Allow the file to be imported from its canonical location OR from a
    # test-local copy; we use importlib so we can control the path.
    spec_path = os.path.join(scripts_dir, "tool3_business_docs.py")
    if os.path.exists(spec_path):
        import importlib.util
        spec = importlib.util.spec_from_file_location("tool3_business_docs", spec_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tool3_business_docs"] = mod
        spec.loader.exec_module(mod)
    else:
        # Fallback: assume it is on sys.path already
        import tool3_business_docs as mod  # noqa: F401
    return mod


# ---------------------------------------------------------------------------
# Fixed timestamp helper
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 10, 30, 0)
FIXED_DATE_STR   = "2024-06-15"
FIXED_DT_STR     = "2024-06-15 10:30 UTC"


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Freeze datetime.datetime.utcnow to FIXED_DT in the tool3 module."""
    mock_dt = MagicMock(wraps=datetime.datetime)
    mock_dt.utcnow.return_value = FIXED_DT
    return mock_dt


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, tool3, inject_shared_stub, frozen_datetime):
        """Claude returns both parts separated by ---GAPS---."""
        inject_shared_stub.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the business sponsor?"
        )
        inject_shared_stub.get_repo_files.return_value = {
            "README.md": "# MyProject",
            "main.py":   "print('hello')",
        }

        with patch.object(tool3.datetime, "datetime", frozen_datetime):
            doc, gaps = tool3.generate_biz_doc(
                "acme", "my-repo", "MyProject", "1.2.3", "https://github.com/run/1"
            )

        assert "# Solution overview: MyProject" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the business sponsor?" in gaps
        # Delimiter itself must NOT appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_no_gaps_delimiter(self, tool3, inject_shared_stub):
        """When Claude omits the delimiter, gaps gets the fallback message."""
        inject_shared_stub.call_claude.return_value = "Just a plain document with no delimiter."
        inject_shared_stub.get_repo_files.return_value = {"app.py": "x = 1"}

        doc, gaps = tool3.generate_biz_doc(
            "acme", "my-repo", "MyProject", "0.1.0", "https://github.com/run/2"
        )

        assert "Just a plain document with no delimiter." in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, inject_shared_stub):
        """Verifies the correct file extensions are requested."""
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"
        inject_shared_stub.get_repo_files.return_value = {}

        tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        call_args = inject_shared_stub.get_repo_files.call_args
        extensions = call_args[0][2]  # positional arg 3
        for ext in [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]:
            assert ext in extensions, f"Extension {ext} not requested"

    def test_get_repo_files_max_files_limit(self, tool3, inject_shared_stub):
        """max_files=20 must be passed."""
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"
        inject_shared_stub.get_repo_files.return_value = {}

        tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        call_kwargs = inject_shared_stub.get_repo_files.call_args[1]
        assert call_kwargs.get("max_files") == 20

    def test_claude_prompt_contains_project_name(self, tool3, inject_shared_stub, frozen_datetime):
        """The formatted prompt passed to call_claude must contain the project name."""
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"
        inject_shared_stub.get_repo_files.return_value = {}

        with patch.object(tool3.datetime, "datetime", frozen_datetime):
            tool3.generate_biz_doc("owner", "repo", "InsurancePortal", "2.0.0", "url")

        prompt_arg = inject_shared_stub.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg

    def test_claude_prompt_contains_version(self, tool3, inject_shared_stub, frozen_datetime):
        """The formatted prompt passed to call_claude must contain the version."""
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"
        inject_shared_stub.get_repo_files.return_value = {}

        with patch.object(tool3.datetime, "datetime", frozen_datetime):
            tool3.generate_biz_doc("owner", "repo", "P", "3.1.4", "url")

        prompt_arg = inject_shared_stub.call_claude.call_args[0][0]
        assert "3.1.4" in prompt_arg

    def test_claude_prompt_contains_date(self, tool3, inject_shared_stub, frozen_datetime):
        """The formatted prompt must embed today's date."""
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"
        inject_shared_stub.get_repo_files.return_value = {}

        with patch.object(tool3.datetime, "datetime", frozen_datetime):
            tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        prompt_arg = inject_shared_stub.call_claude.call_args[0][0]
        assert FIXED_DATE_STR in prompt_arg

    def test_file_contents_truncated_to_3000_chars(self, tool3, inject_shared_stub):
        """Files longer than 3000 chars must be truncated in the user message."""
        long_content = "x" * 5000
        inject_shared_stub.get_repo_files.return_value = {"big_file.py": long_content}
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        user_msg = inject_shared_stub.call_claude.call_args[0][1]
        # The truncated content should be at most 3000 x's
        assert "x" * 3001 not in user_msg

    def test_multiple_files_concatenated_in_user_message(self, tool3, inject_shared_stub):
        """All returned files must appear in the user message sent to Claude."""
        inject_shared_stub.get_repo_files.return_value = {
            "a.py": "alpha",
            "b.tf": "beta",
        }
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        user_msg = inject_shared_stub.call_claude.call_args[0][1]
        assert "a.py"  in user_msg
        assert "alpha" in user_msg
        assert "b.tf"  in user_msg
        assert "beta"  in user_msg

    def test_empty_repo_files(self, tool3, inject_shared_stub):
        """An empty file set should not crash; call_claude is still called."""
        inject_shared_stub.get_repo_files.return_value = {}
        inject_shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        inject_shared_stub.call_claude.assert_called_once()
        assert doc == "doc"
        assert gaps == "gaps"

    def test_strips_whitespace_from_parts(self, tool3, inject_shared_stub):
        """Leading/trailing whitespace around doc and gaps must be stripped."""
        inject_shared_stub.get_repo_files.return_value = {}
        inject_shared_stub.call_claude.return_value = (
            "  \n  doc with spaces  \n  ---GAPS---  \n  gap line  \n  "
        )

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        assert doc == "doc with spaces"
        assert gaps == "gap line"

    def test_multiple_gaps_delimiters_only_first_split(self, tool3, inject_shared_stub):
        """Only the first ---GAPS--- delimiter should be used for splitting."""
        inject_shared_stub.get_repo_files.return_value = {}
        inject_shared_stub.call_claude.return_value = (
            "doc part\n---GAPS---\ngaps part\n---GAPS---\nextra stuff"
        )

        doc, gaps = tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

        assert "doc part" in doc
        assert "gaps part" in gaps
        assert "extra stuff" in gaps  # second split stays in gaps

    def test_call_claude_exception_propagates(self, tool3, inject_shared_stub):
        """If call_claude raises, generate_biz_doc must propagate the exception."""
        inject_shared_stub.get_repo_files.return_value = {}
        inject_shared_stub.call_claude.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")

    def test_get_repo_files_exception_propagates(self, tool3, inject_shared_stub):
        """If get_repo_files raises, generate_biz_doc must propagate the exception."""
        inject_shared_stub.get_repo_files.side_effect = ConnectionError("GitHub down")

        with pytest.raises(ConnectionError, match="GitHub down"):
            tool3.generate_biz_doc("owner", "repo", "P", "1.0", "url")


# ===========================================================================
# Tests for build_full_output
# ===========================================================================

class TestBuildFullOutput:

    @pytest.fixture()
    def sample_inputs(self):
        return dict(
            doc="# Solution overview: Generations II\nContent here.",
            gaps="1. What is the go-live date?\n2. Who is the sponsor?",
            owner="sunlife",
            repo="generations-ii",
            project_name="Generations II",
            version="1.0.0",
        )

    def test_returns_two_strings(self, tool3, sample_inputs, frozen_datetime):
        with patch.object(tool3.datetime, "datetime", frozen_datetime):
            result = tool3.build_full_output(**sample_inputs)
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, tool3, sample_inputs, frozen_datetime):
        with patch.object(tool3.datetime, "datetime", frozen_datetime):
            full_md, _ = tool3.build_full_output(**sample_inputs)
        assert "# Solution overview: Generations II" in full_md
        assert "Content here." in full_md

    def test_full_md_contains_gap_questions(self, tool3, sample_inputs, frozen_datetime):
        with patch.object(tool3.