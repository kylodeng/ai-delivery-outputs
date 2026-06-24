"""
Test suite for tool3_business_docs.py

What is tested:
  - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), Claude response handling
  - build_full_output(): full markdown assembly, gap-only markdown assembly, content correctness
  - __main__ block logic (via subprocess or direct invocation with env vars)
  - Edge cases: missing delimiter, empty gaps, special characters in project_name/version
  - Error conditions: exceptions from call_claude, get_repo_files

Mocks used:
  - shared.call_claude           (patched to return controlled strings)
  - shared.get_repo_files        (patched to return controlled file dicts)
  - shared.write_output_file     (patched to avoid real GitHub writes)
  - shared.send_email            (patched to avoid real email sends)
  - shared.email_html            (patched)
  - shared.write_audit_entry     (patched to avoid real audit writes)
  - datetime.datetime.utcnow     (patched for deterministic timestamps)

TODOs:
  - TODO: Integration test against a real Claude API response shape (requires API key)
  - TODO: Test __main__ block with full env-var matrix via subprocess once CI secrets available
"""

import sys
import os
import importlib
import datetime
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module without executing __main__
# ---------------------------------------------------------------------------

MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    ".github", "scripts", "tool3_business_docs.py",
)

def _load_module(extra_env: dict | None = None):
    """
    Import tool3_business_docs with a patched 'shared' dependency so we never
    hit network calls during import.  Returns the module object.
    """
    env_patch = {
        "SOURCE_REPO_OWNER": "test-owner",
        "SOURCE_REPO_NAME":  "test-repo",
        "PROJECT_NAME":      "TestProject",
        "RELEASE_VERSION":   "1.2.3",
        "GITHUB_RUN_URL":    "https://github.com/runs/99",
        **(extra_env or {}),
    }

    fake_shared = types.ModuleType("shared")
    fake_shared.call_claude       = MagicMock(return_value="doc\n---GAPS---\ngaps")
    fake_shared.get_repo_files    = MagicMock(return_value={})
    fake_shared.write_output_file = MagicMock(return_value="https://github.com/out")
    fake_shared.send_email        = MagicMock()
    fake_shared.email_html        = MagicMock(return_value="<html/>")
    fake_shared.write_audit_entry = MagicMock()
    fake_shared.OUTPUT_REPO_OWNER = "output-owner"
    fake_shared.OUTPUT_REPO       = "output-repo"

    with mock.patch.dict("sys.modules", {"shared": fake_shared}), \
         mock.patch.dict(os.environ, env_patch):

        spec   = importlib.util.spec_from_file_location("tool3_business_docs", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        # Prevent __main__ execution on import
        module.__name__ = "tool3_business_docs_test_import"
        spec.loader.exec_module(module)

    return module, fake_shared


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_NOW_FULL  = "2024-06-15 12:00 UTC"
FIXED_NOW_DATE  = "2024-06-15"
FIXED_DT        = datetime.datetime(2024, 6, 15, 12, 0, 0)


@pytest.fixture()
def module_and_shared():
    mod, shared = _load_module()
    return mod, shared


@pytest.fixture()
def patched_utcnow():
    """Freeze datetime.datetime.utcnow() to a known value."""
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        # Make strftime work on the return value
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, module_and_shared):
        """Claude returns both parts separated by ---GAPS---."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {
            "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "README.md": "# My Project",
        }
        shared.call_claude.return_value = (
            "# Solution overview: TestProject\nSome content"
            "\n---GAPS---\n"
            "1. What is the target go-live date?\n2. Who are the key users?"
        )

        doc, gaps = mod.generate_biz_doc(
            "test-owner", "test-repo", "TestProject", "1.2.3", "https://run"
        )

        assert "# Solution overview: TestProject" in doc
        assert "Some content" in doc
        assert "1. What is the target go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_missing_delimiter_falls_back(self, module_and_shared):
        """When Claude omits ---GAPS---, the whole response becomes doc, gaps is fallback text."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "Only a document, no delimiter present."

        doc, gaps = mod.generate_biz_doc(
            "test-owner", "test-repo", "TestProject", "1.0.0", "https://run"
        )

        assert doc == "Only a document, no delimiter present."
        assert "Claude could not extract gap questions" in gaps

    def test_delimiter_present_multiple_times_splits_on_first(self, module_and_shared):
        """Only the first ---GAPS--- is used as a split point."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = (
            "Doc content\n---GAPS---\nGap line\n---GAPS---\nExtra"
        )

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "v", "u")

        assert doc == "Doc content"
        assert "Gap line" in gaps
        assert "Extra" in gaps     # second occurrence stays in gaps

    def test_files_are_passed_to_claude(self, module_and_shared):
        """File contents end up in the Claude user message."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {
            "backend/prompts/assessment_criterias.json": '{"deep": "finance prompt"}',
        }
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("owner", "repo", "MyProj", "2.0.0", "url")

        _, user_msg = shared.call_claude.call_args[0]
        assert "assessment_criterias.json" in user_msg
        assert "finance prompt" in user_msg

    def test_get_repo_files_called_with_correct_extensions(self, module_and_shared):
        """get_repo_files is called with the expected extension list and max_files."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("o", "r", "P", "v", "u")

        args, kwargs = shared.get_repo_files.call_args
        assert args[0] == "o"
        assert args[1] == "r"
        expected_exts = [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"]
        assert args[2] == expected_exts
        assert kwargs.get("max_files") == 20

    def test_project_name_and_version_appear_in_prompt(self, module_and_shared):
        """SYSTEM prompt is formatted with project_name, version, and date."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("o", "r", "UnderwritingRisk", "3.4.5", "u")

        system_prompt = shared.call_claude.call_args[0][0]
        assert "UnderwritingRisk" in system_prompt
        assert "3.4.5" in system_prompt

    def test_file_content_truncated_to_3000_chars(self, module_and_shared):
        """File contents longer than 3000 characters are truncated in the prompt."""
        mod, shared = module_and_shared
        long_content = "x" * 5000
        shared.get_repo_files.return_value = {"bigfile.py": long_content}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("o", "r", "P", "v", "u")

        _, user_msg = shared.call_claude.call_args[0]
        # Only 3000 x's should appear
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files(self, module_and_shared):
        """Works gracefully when the repo has no matching files."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "v", "u")

        assert doc == "doc"
        assert gaps == "gap"

    def test_call_claude_exception_propagates(self, module_and_shared):
        """Exceptions from call_claude bubble up to the caller."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            mod.generate_biz_doc("o", "r", "P", "v", "u")

    def test_get_repo_files_exception_propagates(self, module_and_shared):
        """Exceptions from get_repo_files bubble up to the caller."""
        mod, shared = module_and_shared
        shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_biz_doc("o", "r", "P", "v", "u")

    def test_gaps_whitespace_stripped(self, module_and_shared):
        """Leading/trailing whitespace is stripped from both doc and gaps."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "  doc text  \n---GAPS---\n  gap text  "

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "v", "u")

        assert doc == "doc text"
        assert gaps == "gap text"

    def test_owner_and_repo_in_user_message(self, module_and_shared):
        """The user message includes the owner/repo reference."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc\n---GAPS---\ngap"

        mod.generate_biz_doc("acme-org", "insurance-app", "P", "v", "u")

        _, user_msg = shared.call_claude.call_args[0]
        assert "acme-org/insurance-app" in user_msg


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def _call(self, mod, doc="# Doc", gaps="1. A question?",
              owner="test-owner", repo="test-repo",
              project_name="TestProject", version="1.2.3"):
        return mod.build_full_output(doc, gaps, owner, repo, project_name, version)

    def test_full_md_contains_doc(self, module_and_shared):
        mod, _ = module_and_shared
        full_md, _ = self._call(mod, doc="# Solution overview: TestProject")
        assert "# Solution overview: TestProject" in full_md

    def test_full_md_contains_gaps(self, module_and_shared):
        mod, _ = module_and_shared
        full_md, _ = self._call(mod, gaps="1. Who are the stakeholders?")
        assert "1. Who are the stakeholders?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self, module_and_shared):
        mod, _ = module_and_shared
        full_md, _ = self._call(mod)
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, module_and_shared):
        mod, _ = module_and_shared
        full_md, _ = self._call(mod, owner="acme", repo="myapp", version="2.0.0")
        assert "acme/myapp" in full_md
        assert "v2.0.0" in full_md

    def test_gap_only_md_contains_project_and_version(self, module_and_shared):
        mod, _ = module_and_shared
        _, gap_only = self._call(mod, project_name="UnderwritingRisk", version="3.0.0")
        assert "UnderwritingRisk" in gap_only
        assert "v3.0.0" in gap_only

    def test_gap_only_md_contains_gap_content(self, module_and_shared):
        mod, _ = module_and_shared
        _, gap_only = self._call(mod, gaps="1. What is the go-live date?")
        assert "1. What is the go-live date?" in gap_only

    def test_gap_only_md_contains_output_repo_link(self, module_and_shared):
        mod, shared = module_and_shared
        _, gap_only = self._call(mod)
        # The link uses OUTPUT_REPO_OWNER and OUTPUT_REPO from shared
        assert "output-owner" in gap_only or "output-repo" in gap_only

    def test_gap_only_md_has_preamble_instructions(self, module_and_shared):
        mod, _ = module_and_shared
        _, gap_only = self._call(mod)
        assert "Please answer these questions" in gap_only
        assert "10-15 minutes" in gap_only

    def test_returns_tuple_of_two_strings(self, module_and_shared):
        mod, _ = module_and_shared
        result = self._call(mod)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], str)

    def test_full_md_contains_ai_delivery_bot_attribution(self, module_and_shared):
        mod, _ = module_and_shared