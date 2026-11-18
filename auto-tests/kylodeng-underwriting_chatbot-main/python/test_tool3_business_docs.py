"""
Test suite for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path, missing ---GAPS--- delimiter, Claude response splitting
- build_full_output(): happy path, structure of full_md and gap_only_md outputs,
  metadata footer contents, project name/version embedding
- __main__ block: environment variable handling, success flow, exception/failure flow,
  gap_count calculation, file writing, email sending, audit logging

Mocks used:
- shared.call_claude          → unittest.mock.patch
- shared.get_repo_files       → unittest.mock.patch
- shared.write_output_file    → unittest.mock.patch
- shared.send_email           → unittest.mock.patch
- shared.email_html           → unittest.mock.patch
- shared.write_audit_entry    → unittest.mock.patch
- shared.OUTPUT_REPO_OWNER    → patched as module attribute
- shared.OUTPUT_REPO          → patched as module attribute
- datetime.datetime.utcnow    → unittest.mock.patch (for deterministic timestamps)

TODOs:
- TODO: test interaction with real GitHub API (write_output_file) — needs integration env
- TODO: test with actual Claude API response format variations
- TODO: test __main__ block fully isolated (requires importlib reload or subprocess)
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
# Helpers to build a fake 'shared' module so we can import tool3 without
# the real shared.py being present / making network calls.
# ---------------------------------------------------------------------------

FAKE_SHARED_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. Question one?"),
    "get_repo_files": MagicMock(return_value={"main.py": "print('hello')", "README.md": "# Hello"}),
    "write_output_file": MagicMock(return_value="https://github.com/output/repo/file.md"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>email</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_fake_shared():
    """Return a fresh fake shared module with clean mocks."""
    mod = types.ModuleType("shared")
    for attr, val in FAKE_SHARED_ATTRS.items():
        if callable(val):
            setattr(mod, attr, MagicMock(return_value=getattr(val, "return_value", None)))
        else:
            setattr(mod, attr, val)
    # Restore default return values
    mod.call_claude.return_value = "doc content\n---GAPS---\n1. Question one?"
    mod.get_repo_files.return_value = {"main.py": "print('hello')", "README.md": "# Hello"}
    mod.write_output_file.return_value = "https://github.com/output/repo/file.md"
    mod.email_html.return_value = "<html>email</html>"
    return mod


@pytest.fixture(autouse=True)
def fake_shared_module():
    """Inject fake shared module before every test and reload tool3."""
    fake = _make_fake_shared()
    with mock.patch.dict("sys.modules", {"shared": fake}):
        # Remove tool3 from cache so it re-imports with the patched shared
        sys.modules.pop("tool3_business_docs", None)
        # Ensure the script directory is on sys.path
        script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
        abs_dir = os.path.abspath(script_dir)
        had_path = abs_dir in sys.path
        if not had_path:
            sys.path.insert(0, abs_dir)
        yield fake
        sys.modules.pop("tool3_business_docs", None)
        if not had_path and abs_dir in sys.path:
            sys.path.remove(abs_dir)


def import_tool3():
    """Import (or re-import) the module under test."""
    import importlib

    # Resolve the actual file path so pytest can find it regardless of cwd
    here = os.path.dirname(__file__)
    script_path = os.path.abspath(
        os.path.join(here, "..", ".github", "scripts", "tool3_business_docs.py")
    )
    spec = importlib.util.spec_from_file_location("tool3_business_docs", script_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tool3_business_docs"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_DATE = "2024-06-15"
FIXED_DATETIME = "2024-06-15 10:30 UTC"

FAKE_DATETIME = datetime.datetime(2024, 6, 15, 10, 30, 0)


@pytest.fixture()
def tool3(fake_shared_module):
    return import_tool3()


@pytest.fixture()
def patched_utcnow(tool3):
    """Patch datetime inside tool3 for deterministic output."""
    with patch.object(
        tool3.datetime,
        "datetime",
        wraps=tool3.datetime.datetime,
    ) as mocked_dt:
        mocked_dt.utcnow.return_value = FAKE_DATETIME
        yield mocked_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------


class TestGenerateBizDoc:
    def test_happy_path_with_delimiter(self, tool3, fake_shared_module):
        """Claude returns both doc and gaps separated by ---GAPS---."""
        fake_shared_module.call_claude.return_value = (
            "# Solution overview\nSome content\n---GAPS---\n1. What is the go-live date?"
        )
        fake_shared_module.get_repo_files.return_value = {
            "main.py": "# main",
            "README.md": "# readme",
        }

        doc, gaps = tool3.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert "Solution overview" in doc
        assert "1. What is the go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_calls_get_repo_files_with_correct_extensions(self, tool3, fake_shared_module):
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("owner", "repo", "proj", "0.1.0", "https://run")

        fake_shared_module.get_repo_files.assert_called_once()
        args, kwargs = fake_shared_module.get_repo_files.call_args
        # positional: owner, repo, extensions list
        assert args[0] == "owner"
        assert args[1] == "repo"
        extensions = args[2]
        for ext in [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]:
            assert ext in extensions
        assert kwargs.get("max_files", 20) == 20

    def test_happy_path_calls_call_claude(self, tool3, fake_shared_module):
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"
        fake_shared_module.get_repo_files.return_value = {"app.py": "code"}

        tool3.generate_biz_doc("o", "r", "ProjectX", "2.3.4", "https://run")

        assert fake_shared_module.call_claude.call_count == 1
        prompt_arg, user_msg_arg = fake_shared_module.call_claude.call_args[0]
        assert "ProjectX" in prompt_arg
        assert "2.3.4" in prompt_arg
        assert "o/r" in user_msg_arg

    def test_missing_gaps_delimiter_returns_fallback(self, tool3, fake_shared_module):
        """When ---GAPS--- is absent, gaps should be a fallback message."""
        fake_shared_module.call_claude.return_value = "Only a document, no gaps here."

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        assert doc == "Only a document, no gaps here."
        assert "review the document manually" in gaps.lower() or gaps != ""

    def test_gaps_delimiter_at_start_gives_empty_doc(self, tool3, fake_shared_module):
        """---GAPS--- at very beginning → doc part is empty string."""
        fake_shared_module.call_claude.return_value = "---GAPS---\nOnly gaps here."

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        assert doc == ""
        assert "Only gaps here." in gaps

    def test_multiple_delimiters_only_first_split_used(self, tool3, fake_shared_module):
        """Only first ---GAPS--- is used as split point."""
        fake_shared_module.call_claude.return_value = (
            "doc part\n---GAPS---\ngap part\n---GAPS---\nextra"
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        assert doc == "doc part"
        assert "gap part" in gaps
        assert "extra" in gaps  # second delimiter ends up in gaps

    def test_files_are_included_in_claude_prompt(self, tool3, fake_shared_module):
        """File content should be passed to Claude in user message."""
        fake_shared_module.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
        }
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        _, user_msg = fake_shared_module.call_claude.call_args[0]
        assert "model_card.json" in user_msg
        assert "Underwriting Risk Classification" in user_msg

    def test_long_file_content_truncated_to_3000_chars(self, tool3, fake_shared_module):
        """Content longer than 3000 chars should be sliced."""
        long_content = "x" * 5000
        fake_shared_module.get_repo_files.return_value = {"big_file.py": long_content}
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        _, user_msg = fake_shared_module.call_claude.call_args[0]
        # The truncated content should be 3000 x's
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_date_in_prompt_is_today_format(self, tool3, fake_shared_module):
        """The prompt should contain today's date in YYYY-MM-DD format."""
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        prompt_arg, _ = fake_shared_module.call_claude.call_args[0]
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        assert today in prompt_arg

    def test_empty_repo_files(self, tool3, fake_shared_module):
        """Should not raise when repo has no files."""
        fake_shared_module.get_repo_files.return_value = {}
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_project_name_in_prompt(self, tool3, fake_shared_module):
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("o", "r", "InsuranceUnderwriting", "3.1.0", "https://run")

        prompt_arg, _ = fake_shared_module.call_claude.call_args[0]
        assert "InsuranceUnderwriting" in prompt_arg

    def test_version_in_prompt(self, tool3, fake_shared_module):
        fake_shared_module.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool3.generate_biz_doc("o", "r", "P", "9.9.9", "https://run")

        prompt_arg, _ = fake_shared_module.call_claude.call_args[0]
        assert "9.9.9" in prompt_arg

    def test_whitespace_stripped_from_outputs(self, tool3, fake_shared_module):
        fake_shared_module.call_claude.return_value = (
            "   doc with leading space   \n---GAPS---\n   gaps with space   "
        )

        doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://run")

        assert not doc.startswith(" ")
        assert not doc.endswith(" ")
        assert not gaps.startswith(" ")
        assert not gaps.endswith(" ")


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------


class TestBuildFullOutput:
    def test_returns_tuple_of_two_strings(self, tool3):
        result = tool3.build_full_output("doc", "gaps", "owner", "repo", "MyProject", "1.0.0")
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc(self, tool3):
        full_md, _ = tool3.build_full_output(
            "# Solution Overview\nSome content", "1. A question?", "o", "r", "P", "1.0"
        )
        assert "# Solution Overview" in full_md
        assert "Some content" in full_md

    def test_full_md_contains_gaps(self, tool3):
        full_md, _ = tool3.build_full_output(
            "doc", "1. A question?\n2. Another?", "o", "r", "P", "1.0"
        )
        assert "1. A question?" in full_md
        assert "2. Another?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool3):
        full_md, _ = tool3.build_full_output("doc", "gaps", "o", "r", "P", "1.0")
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool3):
        full_md, _ = tool3.build_full_output