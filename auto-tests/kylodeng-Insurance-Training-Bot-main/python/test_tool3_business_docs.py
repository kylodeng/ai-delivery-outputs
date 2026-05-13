"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path with ---GAPS--- delimiter, missing delimiter fallback
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content checks
    - __main__ block behaviour via subprocess / importlib (stubbed)
    - Edge cases: empty files, empty gaps, missing env vars, Claude returning unexpected content

Mocks used:
    - shared.call_claude            — avoids real Anthropic API calls
    - shared.get_repo_files         — avoids real GitHub API calls
    - shared.write_output_file      — avoids real GitHub commits
    - shared.send_email             — avoids real SMTP/SES calls
    - shared.email_html             — avoids rendering dependency
    - shared.write_audit_entry      — avoids real audit-log writes
    - datetime.datetime.utcnow      — deterministic timestamps
    - os.environ                    — controlled environment variables

TODOs:
    - TODO: test the __main__ block end-to-end (needs importlib reload + env patching)
    - TODO: test write_output_file return value used as doc_url in email
    - TODO: test gap_count calculation when gaps contain Windows-style line endings
"""

import importlib
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a minimal fake `shared` module so the import of
# tool3_business_docs.py never tries to reach real external services.
# ---------------------------------------------------------------------------

FAKE_SHARED_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. A gap question?"),
    "get_repo_files": MagicMock(return_value={"README.md": "# Hello"}),
    "write_output_file": MagicMock(return_value="https://github.com/out/repo/blob/main/file.md"),
    "send_email": MagicMock(),
    "email_html": MagicMock(return_value="<html>email</html>"),
    "write_audit_entry": MagicMock(),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_fake_shared():
    """Return a fresh types.ModuleType pretending to be `shared`."""
    mod = types.ModuleType("shared")
    for attr, val in FAKE_SHARED_ATTRS.items():
        # Give each test a fresh MagicMock so call counts don't bleed between tests
        if isinstance(val, MagicMock):
            setattr(mod, attr, MagicMock(wraps=None))
        else:
            setattr(mod, attr, val)
    # restore non-mock defaults that need specific return values
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A gap question?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/out/repo/blob/main/file.md")
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    return mod


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fake `shared` module before every test and reload the module
    under test so it picks up the patched dependency.
    """
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)

    # Remove any previously-loaded tool3 so importlib gives us a clean slate
    sys.modules.pop("tool3_business_docs", None)

    yield mod


@pytest.fixture()
def tool3(fake_shared):
    """Import (or re-import) the module under test after shared is mocked."""
    import tool3_business_docs as t3
    return t3


# ---------------------------------------------------------------------------
# Fixed timestamp for deterministic assertions
# ---------------------------------------------------------------------------

FIXED_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


# ===========================================================================
# generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, tool3, fake_shared):
        """Claude returns ---GAPS--- delimiter — doc and gaps split correctly."""
        fake_shared.get_repo_files.return_value = {
            "README.md": "# My project",
            "main.py": "print('hello')",
        }
        fake_shared.call_claude.return_value = (
            "# Solution overview: MyApp\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the sponsor?"
        )

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://ci.example.com")

        assert "Solution overview" in doc
        assert "go-live date" in gaps
        assert "sponsor" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_fallback(self, tool3, fake_shared):
        """When Claude omits ---GAPS--- the fallback message is used for gaps."""
        fake_shared.call_claude.return_value = "# Solution overview\nJust a plain document with no delimiter."

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://ci.example.com")

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, fake_shared):
        """get_repo_files must be called with the expected extension list."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            tool3.generate_biz_doc("owner", "repo", "Proj", "2.0.0", "https://run")

        fake_shared.get_repo_files.assert_called_once()
        _, kwargs_or_args = fake_shared.get_repo_files.call_args[0], fake_shared.get_repo_files.call_args
        positional = fake_shared.get_repo_files.call_args[0]
        assert positional[0] == "owner"
        assert positional[1] == "repo"
        exts = positional[2]
        for ext in [".py", ".md", ".tf", ".yaml"]:
            assert ext in exts

    def test_call_claude_receives_formatted_prompt(self, tool3, fake_shared):
        """SYSTEM prompt must have project_name, version, date substituted."""
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            tool3.generate_biz_doc("owner", "repo", "InsuranceApp", "3.1.0", "https://run")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "InsuranceApp" in prompt_arg
        assert "3.1.0" in prompt_arg

    def test_empty_repo_files(self, tool3, fake_shared):
        """Empty file dict should not crash; files_str will be empty."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            doc, gaps = tool3.generate_biz_doc("owner", "repo", "EmptyProject", "0.1.0", "https://run")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_large_file_content_is_truncated_in_files_str(self, tool3, fake_shared):
        """Files with content > 3000 chars must be sliced before passed to Claude."""
        large_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big_file.py": large_content}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            tool3.generate_biz_doc("owner", "repo", "BigProj", "1.0.0", "https://run")

        user_content_arg = fake_shared.call_claude.call_args[0][1]
        # The slice [:3000] means at most 3000 'x' chars in the code fence
        assert "x" * 3001 not in user_content_arg

    def test_multiple_gap_delimiters_only_first_split_used(self, tool3, fake_shared):
        """split(..., 1) ensures only the first delimiter is used."""
        fake_shared.call_claude.return_value = (
            "doc part\n---GAPS---\nfirst gaps\n---GAPS---\nsecond gaps"
        )

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            doc, gaps = tool3.generate_biz_doc("owner", "repo", "Proj", "1.0.0", "https://run")

        assert "---GAPS---" not in doc
        assert "second gaps" in gaps

    def test_doc_and_gaps_are_stripped(self, tool3, fake_shared):
        """Leading/trailing whitespace must be stripped from both parts."""
        fake_shared.call_claude.return_value = (
            "   \n doc content \n   \n---GAPS---\n   \n gaps content \n   "
        )

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1", "url")

        assert doc == "doc content"
        assert gaps == "gaps content"


# ===========================================================================
# build_full_output
# ===========================================================================

class TestBuildFullOutput:

    @pytest.fixture()
    def sample_inputs(self):
        return {
            "doc": "# Solution overview: TestApp\nSome content.",
            "gaps": "1. What is the target date?\n2. Who is the sponsor?",
            "owner": "acme",
            "repo": "insurancebot",
            "project_name": "TestApp",
            "version": "1.2.3",
        }

    def test_full_md_contains_doc(self, tool3, sample_inputs):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            full_md, _ = tool3.build_full_output(**sample_inputs)

        assert "# Solution overview: TestApp" in full_md

    def test_full_md_contains_gaps_section(self, tool3, sample_inputs):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            full_md, _ = tool3.build_full_output(**sample_inputs)

        assert "Gap Questionnaire" in full_md
        assert "What is the target date?" in full_md

    def test_full_md_contains_source_attribution(self, tool3, sample_inputs):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            full_md, _ = tool3.build_full_output(**sample_inputs)

        assert "acme/insurancebot" in full_md
        assert "1.2.3" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, tool3, sample_inputs):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            _, gap_only_md = tool3.build_full_output(**sample_inputs)

        assert "TestApp" in gap_only_md
        assert "1.2.3" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, tool3, sample_inputs):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            _, gap_only_md = tool3.build_full_output(**sample_inputs)

        assert "What is the target date?" in gap_only_md
        assert "Who is the sponsor?" in gap_only_md

    def test_gap_only_md_links_to_output_repo(self, tool3, sample_inputs):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            _, gap_only_md = tool3.build_full_output(**sample_inputs)

        # OUTPUT_REPO_OWNER and OUTPUT_REPO are set in fake_shared
        assert "test-owner" in gap_only_md
        assert "test-output-repo" in gap_only_md

    def test_returns_tuple_of_two_strings(self, tool3, sample_inputs):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FIXED_DATE
            mock_dt.utcnow.return_value.strftime = FIXED_DATE.strftime
            result = tool3.build_full_output(**sample_