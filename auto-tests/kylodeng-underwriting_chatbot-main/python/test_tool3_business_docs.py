"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing delimiter, empty files, Claude failure
    - build_full_output(): happy path, content assertions, gap counting, formatting
    - __main__ block behaviour via subprocess / env-var injection (stubbed)
    - Edge cases: empty gaps, single-line gaps, version strings, special chars in names

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch

TODOs:
    - TODO: Integration test against real GitHub API (needs PAT + test repo)
    - TODO: Test __main__ block end-to-end via subprocess with full env (needs isolated runner)
    - TODO: Test send_email failure path in __main__ (needs partial mock of send_email raising)
"""

import sys
import os
import importlib
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while stubbing `shared`
# ---------------------------------------------------------------------------

SHARED_STUB_ATTRS = dict(
    call_claude=MagicMock(),
    get_repo_files=MagicMock(),
    write_output_file=MagicMock(),
    send_email=MagicMock(),
    email_html=MagicMock(),
    write_audit_entry=MagicMock(),
    OUTPUT_REPO_OWNER="output-owner",
    OUTPUT_REPO="output-repo",
)


def _make_shared_stub():
    """Return a fresh module-like object that stands in for `shared`."""
    mod = types.ModuleType("shared")
    for k, v in SHARED_STUB_ATTRS.items():
        setattr(mod, k, v if not callable(v) else MagicMock())
    mod.OUTPUT_REPO_OWNER = "output-owner"
    mod.OUTPUT_REPO = "output-repo"
    return mod


@pytest.fixture()
def shared_stub():
    """Install a fresh shared stub into sys.modules for each test."""
    stub = _make_shared_stub()
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    with mock.patch.dict(sys.modules, {"shared": stub}):
        # Force re-import so the module picks up the patched shared
        mod_name = "tool3_business_docs"
        if mod_name in sys.modules:
            del sys.modules[mod_name]

        spec_path = os.path.join(
            os.path.dirname(__file__), ".github", "scripts", "tool3_business_docs.py"
        )
        import importlib.util

        spec = importlib.util.spec_from_file_location(mod_name, spec_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        yield mod, stub


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------

FAKE_NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)
FAKE_NOW_DATE = "2024-06-15"
FAKE_NOW_FULL = "2024-06-15 12:00 UTC"

SAMPLE_DOC = "# Solution overview: MyProject\n\n## Executive summary\nThis solves X."
SAMPLE_GAPS = "1. What is the go-live date?\n2. Who are the key users?\n3. What is the budget?"
SAMPLE_RAW_WITH_DELIMITER = f"{SAMPLE_DOC}\n---GAPS---\n{SAMPLE_GAPS}"
SAMPLE_RAW_NO_DELIMITER = "Just a big blob of text without the separator."

SAMPLE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance agent"}}',
    "README.md": "# My Project\nSome description.",
}


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------


class TestGenerateBizDoc:
    def test_happy_path_with_delimiter(self, shared_stub):
        """Claude returns well-formed response → doc and gaps split correctly."""
        mod, stub = shared_stub
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            mock_dt.utcnow.return_value.strftime = FAKE_NOW.strftime
            doc, gaps = mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        assert doc == SAMPLE_DOC.strip()
        assert gaps == SAMPLE_GAPS.strip()

    def test_happy_path_no_delimiter(self, shared_stub):
        """Claude returns response without delimiter → gaps replaced with fallback."""
        mod, stub = shared_stub
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value = SAMPLE_RAW_NO_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            doc, gaps = mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run")

        assert doc == SAMPLE_RAW_NO_DELIMITER.strip()
        assert "Claude could not extract" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, shared_stub):
        """get_repo_files must be called with the expected extensions list."""
        mod, stub = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            mod.generate_biz_doc("acme", "widget", "Widget", "2.3.1", "https://run")

        args, kwargs = stub.get_repo_files.call_args
        assert args[0] == "acme"
        assert args[1] == "widget"
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert args[2] == expected_exts
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_formatted_prompt(self, shared_stub):
        """SYSTEM prompt placeholders must be filled before calling Claude."""
        mod, stub = shared_stub
        stub.get_repo_files.return_value = {"main.py": "print('hi')"}
        stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            mock_dt.utcnow.return_value.strftime = FAKE_NOW.strftime
            mod.generate_biz_doc("owner", "repo", "InsuranceApp", "0.9.0", "https://run")

        prompt_arg = stub.call_claude.call_args[0][0]
        assert "InsuranceApp" in prompt_arg
        assert "0.9.0" in prompt_arg
        assert FAKE_NOW_DATE in prompt_arg

    def test_empty_repo_files(self, shared_stub):
        """Empty repo → call_claude still called, result still split."""
        mod, stub = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0.0", "https://run")

        assert doc == SAMPLE_DOC.strip()
        assert gaps == SAMPLE_GAPS.strip()

    def test_call_claude_raises_propagates(self, shared_stub):
        """If call_claude raises, generate_biz_doc must propagate the exception."""
        mod, stub = shared_stub
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.side_effect = RuntimeError("API timeout")

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            with pytest.raises(RuntimeError, match="API timeout"):
                mod.generate_biz_doc("o", "r", "P", "1.0.0", "https://run")

    def test_delimiter_appears_multiple_times(self, shared_stub):
        """Only the first occurrence of ---GAPS--- should be used to split."""
        mod, stub = shared_stub
        raw = f"{SAMPLE_DOC}\n---GAPS---\nQ1. First?\n---GAPS---\nExtra content"
        stub.get_repo_files.return_value = SAMPLE_FILES
        stub.call_claude.return_value = raw

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0.0", "https://run")

        assert doc == SAMPLE_DOC.strip()
        # gaps should contain everything after first delimiter
        assert "Q1. First?" in gaps
        assert "Extra content" in gaps

    def test_files_truncated_to_3000_chars(self, shared_stub):
        """File contents longer than 3000 chars must be truncated in the prompt."""
        mod, stub = shared_stub
        long_content = "x" * 5000
        stub.get_repo_files.return_value = {"big_file.py": long_content}
        stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            mod.generate_biz_doc("o", "r", "P", "1.0.0", "https://run")

        user_content_arg = stub.call_claude.call_args[0][1]
        # The truncated file should appear but not the full 5000 chars
        assert "x" * 3000 in user_content_arg
        assert "x" * 3001 not in user_content_arg

    def test_version_with_special_chars(self, shared_stub):
        """Unusual version strings should not break prompt formatting."""
        mod, stub = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = SAMPLE_RAW_WITH_DELIMITER

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1.2.3-rc.1+build.42", "https://run")

        prompt_arg = stub.call_claude.call_args[0][0]
        assert "v1.2.3-rc.1+build.42" in prompt_arg


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------


class TestBuildFullOutput:
    def test_returns_two_strings(self, shared_stub):
        mod, _ = shared_stub
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            mock_dt.utcnow.return_value.strftime = FAKE_NOW.strftime
            result = mod.build_full_output(
                SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "MyProject", "1.0.0"
            )
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_and_gaps(self, shared_stub):
        mod, _ = shared_stub
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            full_md, _ = mod.build_full_output(
                SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "MyProject", "1.0.0"
            )
        assert SAMPLE_DOC in full_md
        assert SAMPLE_GAPS in full_md

    def test_full_md_contains_source_attribution(self, shared_stub):
        mod, _ = shared_stub
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            full_md, _ = mod.build_full_output(
                SAMPLE_DOC, SAMPLE_GAPS, "myowner", "myrepo", "MyProject", "2.0.0"
            )
        assert "myowner/myrepo" in full_md
        assert "2.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_and_version(self, shared_stub):
        mod, _ = shared_stub
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            _, gap_only_md = mod.build_full_output(
                SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "InsuranceApp", "3.1.4"
            )
        assert "InsuranceApp" in gap_only_md
        assert "3.1.4" in gap_only_md
        assert SAMPLE_GAPS in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, shared_stub):
        mod, stub = shared_stub
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            _, gap_only_md = mod.build_full_output(
                SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "P", "1.0.0"
            )
        assert "output-owner" in gap_only_md
        assert "output-repo" in gap_only_md

    def test_full_md_has_gap_questionnaire_header(self, shared_stub):
        mod, _ = shared_stub
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            full_md, _ = mod.build_full_output(
                SAMPLE_DOC, SAMPLE_GAPS, "owner", "repo", "P", "1.0.0"
            )
        assert "Gap Questionnaire" in full_md

    def test_gap_only_md_instructions_present(self, shared_stub):
        mod, _ = shared_stub
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW
            _, gap_only_md = mod.build_full_output(
                SAMPLE_DOC, SAMPLE