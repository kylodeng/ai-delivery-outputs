"""
Tests for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path with/without ---GAPS--- delimiter, Claude response handling
    - build_full_output(): markdown construction, delimiter presence, metadata embedding
    - __main__ block: environment variable handling, file writing, email sending, audit logging,
      error/exception handling

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (frozen time)
    - os.environ                  → monkeypatch / unittest.mock.patch.dict

TODOs:
    - TODO: Integration test against a real Claude API (requires API key + network)
    - TODO: Test the truncated email_html call at the bottom of the source (source is incomplete)
    - TODO: Test get_repo_files filtering logic (lives in shared.py, not in scope here)
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
# Helpers to build a minimal `shared` stub so we can import the module
# without the real shared.py on the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_shared_stub():
    """Return a minimal stub module for `shared`."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Gap question?")
    stub.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    stub.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>body</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    stub.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return stub


# ---------------------------------------------------------------------------
# Fixture: import the module under test with a fresh shared stub each time
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared_stub():
    """Install a fresh shared stub into sys.modules and return it."""
    stub = _make_shared_stub()
    sys.modules["shared"] = stub
    yield stub
    # Teardown: remove both the stub and the cached tool module so each test
    # starts with a clean slate.
    sys.modules.pop("shared", None)
    sys.modules.pop("tool3_business_docs", None)


@pytest.fixture()
def tool(shared_stub):
    """Import tool3_business_docs with the shared stub in place."""
    # Ensure the script directory is on the path so the relative import works.
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    original_path = sys.path[:]
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try the actual location relative to this test file
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(here, ".github", "scripts")
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

    # Patch datetime inside the module so we get a deterministic timestamp
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = datetime.datetime(2024, 6, 15, 12, 0, 0)
        mock_dt.utcnow.return_value.strftime = datetime.datetime(2024, 6, 15, 12, 0, 0).strftime
        import tool3_business_docs as mod
        yield mod

    sys.path[:] = original_path


# ---------------------------------------------------------------------------
# Frozen-time helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


def freeze_utcnow(monkeypatch, module):
    """Patch datetime.datetime.utcnow inside `module` to return FROZEN_DT."""
    fake_dt = MagicMock(wraps=datetime.datetime)
    fake_dt.utcnow.return_value = FROZEN_DT
    monkeypatch.setattr(module.datetime, "datetime", fake_dt)


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, shared_stub):
        """Claude returns well-formed response with ---GAPS--- delimiter."""
        shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# MyProject",
        }
        shared_stub.call_claude.return_value = (
            "# Solution overview: MyProject\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who are the key users?"
        )

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT

            doc, gaps = mod.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert "Solution overview" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who are the key users?" in gaps

    def test_response_without_gaps_delimiter(self, shared_stub):
        """When Claude omits ---GAPS--- the fallback message is returned."""
        shared_stub.get_repo_files.return_value = {"app.py": "# code"}
        shared_stub.call_claude.return_value = "Just a document, no delimiter here."

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT

            doc, gaps = mod.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert doc == "Just a document, no delimiter here."
        assert "Claude could not extract" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, shared_stub):
        """Verifies the expected file extensions are requested."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mod.generate_biz_doc("o", "r", "P", "0.1", "url")

        call_args = shared_stub.get_repo_files.call_args
        extensions = call_args[0][2]  # positional arg index 2
        for ext in [".py", ".js", ".ts", ".tf", ".md", ".yaml"]:
            assert ext in extensions

    def test_call_claude_receives_prompt_with_project_and_version(self, shared_stub):
        """The prompt passed to Claude contains project_name and version."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mod.generate_biz_doc("owner", "repo", "InsuranceApp", "2.3.4", "url")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "InsuranceApp" in prompt_arg
        assert "2.3.4" in prompt_arg

    def test_files_are_truncated_to_3000_chars(self, shared_stub):
        """File content longer than 3000 chars must be sliced in the prompt."""
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big.py": long_content}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mod.generate_biz_doc("o", "r", "P", "v", "url")

        user_message = shared_stub.call_claude.call_args[0][1]
        # The content appears in the user message; it should be capped at 3000 x's
        assert "x" * 3001 not in user_message
        assert "x" * 3000 in user_message

    def test_empty_repo_files(self, shared_stub):
        """generate_biz_doc handles an empty repo gracefully."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "minimal doc\n---GAPS---\n1. Question?"

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v", "url")

        assert "minimal doc" in doc
        assert "1. Question?" in gaps

    def test_multiple_gaps_delimiters_splits_on_first(self, shared_stub):
        """Only the first ---GAPS--- delimiter should be used to split."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = (
            "doc part\n---GAPS---\ngap part\n---GAPS---\nextra stuff"
        )

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v", "url")

        assert "doc part" in doc
        assert "gap part" in gaps
        assert "extra stuff" in gaps  # everything after the first split

    def test_call_claude_propagates_exception(self, shared_stub):
        """If call_claude raises, generate_biz_doc propagates it."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.side_effect = RuntimeError("Claude API error")

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with pytest.raises(RuntimeError, match="Claude API error"):
            with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
                mock_dt.utcnow.return_value = FROZEN_DT
                mod.generate_biz_doc("o", "r", "P", "v", "url")

    def test_user_message_contains_owner_and_repo(self, shared_stub):
        """The user message sent to Claude references the owner/repo."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod

        with patch.object(mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mod.generate_biz_doc("my-org", "cool-repo", "P", "v", "url")

        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "my-org" in user_msg
        assert "cool-repo" in user_msg


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    @pytest.fixture(autouse=True)
    def _setup(self, shared_stub):
        sys.modules["shared"] = shared_stub
        sys.modules.pop("tool3_business_docs", None)
        import tool3_business_docs as mod
        self.mod = mod

    def _call(self, doc="# Doc\nContent", gaps="1. Q?", owner="acme",
              repo="my-repo", project_name="MyProject", version="1.2.3"):
        with patch.object(self.mod.datetime, "datetime", wraps=datetime.datetime) as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            return self.mod.build_full_output(doc, gaps, owner, repo, project_name, version)

    # --- full_md checks ---

    def test_full_md_contains_doc_content(self):
        full_md, _ = self._call(doc="# My Solution\nThis is great.")
        assert "# My Solution" in full_md
        assert "This is great." in full_md

    def test_full_md_contains_gaps(self):
        full_md, _ = self._call(gaps="1. Who owns it?\n2. When?")
        assert "1. Who owns it?" in full_md
        assert "2. When?" in full_md

    def test_full_md_contains_gap_questionnaire_header(self):
        full_md, _ = self._call()
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_auto_generated_footer(self):
        full_md, _ = self._call(owner="acme", repo="my-repo", version="1.2.3")
        assert "AI Delivery Bot" in full_md
        assert "acme/my-