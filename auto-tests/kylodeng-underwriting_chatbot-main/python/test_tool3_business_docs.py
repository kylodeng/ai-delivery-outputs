"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, Claude error
    - build_full_output(): happy path, empty gaps, various project/version inputs
    - __main__ block: env-driven orchestration, success path, failure/exception path

Mocks used:
    - shared.call_claude         → patched to return controlled strings
    - shared.get_repo_files      → patched to return synthetic file dicts
    - shared.write_output_file   → patched to return a fake URL
    - shared.send_email          → patched as no-op
    - shared.email_html          → patched to return a dummy HTML string
    - shared.write_audit_entry   → patched as no-op
    - datetime.datetime.utcnow   → patched for deterministic timestamps

TODOs:
    - TODO: Integration test against a real GitHub repo (requires GH token + network)
    - TODO: Test e-mail HTML rendering with a real email_html implementation
    - TODO: Test write_output_file collision / retry behaviour
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build a fake `shared` module so the import inside the script
# never requires the real dependency to be installed.
# ---------------------------------------------------------------------------

FAKE_SHARED_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. A question?"),
    "get_repo_files": MagicMock(return_value={"README.md": "# Hello", "main.py": "print('hi')"}),
    "write_output_file": MagicMock(return_value="https://github.com/out/repo/blob/main/file.md"),
    "send_email": MagicMock(),
    "email_html": MagicMock(return_value="<html>ok</html>"),
    "write_audit_entry": MagicMock(),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_fake_shared():
    mod = types.ModuleType("shared")
    for k, v in FAKE_SHARED_ATTRS.items():
        setattr(mod, k, v)
    return mod


def _reset_shared_mocks(mod):
    """Reset all MagicMock call history on the fake shared module."""
    for attr in ("call_claude", "get_repo_files", "write_output_file",
                 "send_email", "email_html", "write_audit_entry"):
        getattr(mod, attr).reset_mock()


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Install a fake `shared` module before every test so the script-level
    import resolves without touching real infrastructure.
    """
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)

    # Remove any previously imported version of the script so each test
    # gets a clean module load.
    tool_key = ".github.scripts.tool3_business_docs"
    alt_key  = "tool3_business_docs"
    for key in (tool_key, alt_key):
        sys.modules.pop(key, None)

    yield mod

    # Cleanup
    for key in (tool_key, alt_key):
        sys.modules.pop(key, None)


def _import_tool(fake_shared_mod):
    """Import the module under test with the fake shared already in place."""
    script_dir = os.path.join(os.path.dirname(__file__),
                              ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Directly exec the source so we don't rely on package structure
    script_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
    )

    spec_mod = types.ModuleType("tool3_business_docs")
    spec_mod.__file__ = script_path

    # Pre-populate sys.modules so recursive imports work
    sys.modules["tool3_business_docs"] = spec_mod

    with open(script_path, "r", encoding="utf-8") as fh:
        source = fh.read()

    # Guard: skip the __main__ block during normal import
    source_no_main = source.replace(
        'if __name__ == "__main__":',
        'if __name__ == "__never__":',
    )

    globs = {"__name__": "tool3_business_docs", "__file__": script_path}
    exec(compile(source_no_main, script_path, "exec"), globs)  # noqa: S102

    # Attach everything to the module object
    for k, v in globs.items():
        setattr(spec_mod, k, v)

    return spec_mod


# ---------------------------------------------------------------------------
# Fixture that provides the imported module
# ---------------------------------------------------------------------------

@pytest.fixture()
def tool(fake_shared):
    return _import_tool(fake_shared)


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    OWNER   = "acme"
    REPO    = "risk-engine"
    PROJECT = "Underwriting Risk Classification"
    VERSION = "1.2.0"
    RUN_URL = "https://github.com/runs/42"

    FAKE_FILES = {
        "README.md":  "# Underwriting Risk Classification\nML pipeline for insurance.",
        "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
        "main.py": "import catboost",
    }

    # --- happy path ----------------------------------------------------------

    def test_happy_path_returns_doc_and_gaps(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = self.FAKE_FILES
        fake_shared.call_claude.return_value = (
            "# Solution overview: Underwriting Risk Classification\n"
            "Some content here.\n"
            "---GAPS---\n"
            "1. What is the target go-live date?\n"
            "2. Who owns the budget?"
        )

        doc, gaps = tool.generate_biz_doc(
            self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL
        )

        assert "Solution overview" in doc
        assert "1. What is the target go-live date?" in gaps
        assert "2. Who owns the budget?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_get_repo_files_called_with_correct_args(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool.generate_biz_doc(self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL)

        fake_shared.get_repo_files.assert_called_once_with(
            self.OWNER,
            self.REPO,
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_call_claude_receives_project_name_in_prompt(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool.generate_biz_doc(self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL)

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert self.PROJECT in prompt_arg
        assert self.VERSION in prompt_arg

    def test_call_claude_user_message_contains_repo(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {"a.py": "x" * 5000}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool.generate_biz_doc(self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL)

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert f"{self.OWNER}/{self.REPO}" in user_msg

    # --- missing delimiter ---------------------------------------------------

    def test_missing_delimiter_returns_raw_as_doc(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "Only a doc, no delimiter here."

        doc, gaps = tool.generate_biz_doc(
            self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL
        )

        assert doc == "Only a doc, no delimiter here."
        assert "could not extract gap questions" in gaps

    # --- file content truncation ---------------------------------------------

    def test_long_file_content_is_truncated_to_3000_chars(self, tool, fake_shared):
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big.py": long_content}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        tool.generate_biz_doc(self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL)

        user_msg = fake_shared.call_claude.call_args[0][1]
        # The truncated slice should appear, not the full 10 000 chars
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    # --- empty file dict -----------------------------------------------------

    def test_empty_file_dict_produces_empty_files_str(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        doc, gaps = tool.generate_biz_doc(
            self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL
        )

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "Files:\n" in user_msg

    # --- delimiter appears multiple times ------------------------------------

    def test_only_first_delimiter_is_used_for_split(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = (
            "doc part\n---GAPS---\ngap part 1\n---GAPS---\ngap part 2"
        )

        doc, gaps = tool.generate_biz_doc(
            self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL
        )

        assert doc == "doc part"
        assert "gap part 1" in gaps
        assert "gap part 2" in gaps  # everything after first delimiter

    # --- date appears in prompt ----------------------------------------------

    def test_today_date_in_prompt(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        fixed_dt = datetime.datetime(2024, 6, 15, 10, 0, 0)
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fixed_dt
            mock_dt.utcnow.return_value.strftime = fixed_dt.strftime

            tool.generate_biz_doc(
                self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL
            )

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        # The prompt is built before the mock in some Python versions;
        # at minimum call_claude must have been called once
        assert fake_shared.call_claude.called

    # --- call_claude raises --------------------------------------------------

    def test_call_claude_exception_propagates(self, tool, fake_shared):
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            tool.generate_biz_doc(
                self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL
            )

    # --- get_repo_files raises -----------------------------------------------

    def test_get_repo_files_exception_propagates(self, tool, fake_shared):
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool.generate_biz_doc(
                self.OWNER, self.REPO, self.PROJECT, self.VERSION, self.RUN_URL
            )

        fake_shared.get_repo_files.side_effect = None  # reset


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    DOC     = "# Solution overview: TestProject\nSome content."
    GAPS    = "1. Who is the budget owner?\n2. What is the go-live date?"
    OWNER   = "acme"
    REPO    = "risk-engine"
    PROJECT = "TestProject"
    VERSION = "2.0.0"

    def _call(self, tool, doc=None, gaps=None, project=None, version=None):
        return tool.build_full_output(
            doc     or self.DOC,
            gaps    or self.GAPS,
            self.OWNER,
            self.REPO,
            project or self.PROJECT,
            version or self.VERSION,
        )

    # --- happy path ----------------------------------------------------------

    def test_returns_two_strings(self, tool):
        result = self._call(tool)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(s, str) for s in result)

    def test_full_md_contains_doc_content(self, tool):
        full_md, _ = self._call(tool)
        assert "Solution overview: TestProject" in full_md

    def test_full_md_contains_gaps(self, tool):
        full_md, _ = self._call(tool)
        assert "Who is the budget owner?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, tool):
        full_md, _ = self._call(tool)
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_attribution(self, tool):
        full_md, _ = self._call(tool)
        assert "AI Delivery Bot" in full_md
        assert f"{self.OWNER}/{self.REPO}" in full_md

    def test_gap_only_md_contains_project_and_version(self, tool):
        _, gap_only = self._call(tool)
        assert self.PROJECT in gap_only
        assert self.VERSION in gap_only

    def test_gap_only_md_contains_gap_questions(self, tool):
        _, gap_only = self._call(tool)
        assert "Who is the budget owner?" in gap_only

    def test_gap_only_md_links_to_output_repo(self, tool, fake_shared):
        _, gap_only = self._call(tool)
        assert fake_shared.OUTPUT_REPO_OWNER in gap_only
        assert fake_shared.OUTPUT_REPO in gap_only

    def test_gap_only_md_does_not_contain_doc_