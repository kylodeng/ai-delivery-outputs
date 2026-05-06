"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing delimiter, Claude errors
    - build_full_output(): happy path, content checks, edge cases (empty gaps, whitespace)
    - __main__ block behaviour via subprocess / importlib (stubbed)
    - Boundary values: empty files dict, very long content, special characters in names

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (frozen time)

TODOs:
    # TODO: Integration test against a real Claude API key (needs env secret)
    # TODO: Test __main__ block end-to-end via subprocess with all env vars set
    # TODO: Verify write_output_file returns a URL — need shared module fixtures
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with shared dependencies stubbed out
# ---------------------------------------------------------------------------

SHARED_STUB_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. Question one?\n2. Question two?"),
    "get_repo_files": MagicMock(return_value={"README.md": "# Hello"}),
    "write_output_file": MagicMock(return_value="https://github.com/output/repo/blob/main/file.md"),
    "send_email": MagicMock(return_value=None),
    "email_html": MagicMock(return_value="<html>email</html>"),
    "write_audit_entry": MagicMock(return_value=None),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_stub():
    """Return a fresh stub module that replaces `shared`."""
    mod = types.ModuleType("shared")
    for attr, val in SHARED_STUB_ATTRS.items():
        setattr(mod, attr, val if not callable(val) else MagicMock(side_effect=val.side_effect,
                                                                    return_value=val.return_value))
    # Recreate proper MagicMocks so each test suite gets fresh instances
    mod.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Question one?\n2. Question two?")
    mod.get_repo_files = MagicMock(return_value={"README.md": "# Hello"})
    mod.write_output_file = MagicMock(return_value="https://github.com/output/repo/blob/main/file.md")
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    return mod


@pytest.fixture()
def shared_stub():
    """Inject the shared stub into sys.modules and return it."""
    stub = _make_shared_stub()
    sys.modules["shared"] = stub

    # Force re-import so the module under test picks up the stub
    tool_name = "tool3_business_docs"
    if tool_name in sys.modules:
        del sys.modules[tool_name]

    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try relative path from repo root
    alt_dir = os.path.join(os.path.dirname(__file__))
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    yield stub

    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop(tool_name, None)


@pytest.fixture()
def tool(shared_stub):
    """Import the module under test with shared stubbed out."""
    # Patch sys.path to include the scripts directory
    scripts_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".github", "scripts"
    )
    with patch.dict("sys.modules", {"shared": shared_stub}):
        # We need to locate the actual source file
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".github", "scripts", "tool3_business_docs.py"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "tool3_business_docs.py"),
        ]
        src_path = next((p for p in candidates if os.path.exists(p)), None)

        if src_path is None:
            pytest.skip("tool3_business_docs.py not found — adjust path if needed")

        import importlib.util
        spec = importlib.util.spec_from_file_location("tool3_business_docs", src_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["tool3_business_docs"] = mod
        spec.loader.exec_module(mod)
        yield mod


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)


class _FrozenDatetime(datetime.datetime):
    @classmethod
    def utcnow(cls):
        return FROZEN_DT


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:
    """Tests for generate_biz_doc()."""

    def test_happy_path_splits_on_delimiter(self, tool, shared_stub):
        """Claude returns properly delimited output → doc and gaps returned."""
        shared_stub.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        shared_stub.call_claude.return_value = (
            "# Solution overview\nSome content\n"
            "---GAPS---\n"
            "1. Who is the business owner?\n"
            "2. What is the target go-live date?"
        )

        doc, gaps = tool.generate_biz_doc(
            owner="acme", repo="underwriting", project_name="Underwriting Risk Classification",
            version="1.0.0", run_url="https://github.com/runs/1"
        )

        assert "Solution overview" in doc
        assert "---GAPS---" not in doc
        assert "1. Who is the business owner?" in gaps
        assert "2. What is the target go-live date?" in gaps

    def test_no_delimiter_falls_back_gracefully(self, tool, shared_stub):
        """Claude returns output without delimiter → gaps fallback message."""
        shared_stub.call_claude.return_value = "Just a document with no delimiter at all."

        doc, gaps = tool.generate_biz_doc(
            owner="acme", repo="underwriting", project_name="Test Project",
            version="0.1.0", run_url="https://github.com/runs/1"
        )

        assert "Just a document" in doc
        assert "manually" in gaps.lower() or "could not extract" in gaps.lower()

    def test_get_repo_files_called_with_correct_extensions(self, tool, shared_stub):
        """get_repo_files is called with expected file extensions."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

        call_args = shared_stub.get_repo_files.call_args
        extensions = call_args[0][2] if len(call_args[0]) >= 3 else call_args[1].get("extensions", [])
        assert ".py" in extensions
        assert ".md" in extensions
        assert ".tf" in extensions

    def test_get_repo_files_max_files_limit(self, tool, shared_stub):
        """get_repo_files is called with max_files=20."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

        call_kwargs = shared_stub.get_repo_files.call_args[1]
        assert call_kwargs.get("max_files") == 20

    def test_call_claude_receives_project_name_in_prompt(self, tool, shared_stub):
        """The Claude prompt contains project_name and version."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        tool.generate_biz_doc("o", "r", "Underwriting Risk Classification", "2.3.1", "http://url")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "Underwriting Risk Classification" in prompt_arg
        assert "2.3.1" in prompt_arg

    def test_call_claude_receives_repo_files_in_user_message(self, tool, shared_stub):
        """File contents are forwarded to Claude's user message."""
        shared_stub.get_repo_files.return_value = {"main.py": "# important code"}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        tool.generate_biz_doc("owner", "myrepo", "P", "1.0", "http://url")

        user_message = shared_stub.call_claude.call_args[0][1]
        assert "main.py" in user_message
        assert "important code" in user_message

    def test_empty_files_dict(self, tool, shared_stub):
        """Empty files dictionary is handled without error."""
        shared_stub.get_repo_files.return_value = {}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

        assert doc == "doc"
        assert gaps == "gap"

    def test_multiple_delimiter_occurrences_splits_on_first(self, tool, shared_stub):
        """Only the first ---GAPS--- delimiter is used to split."""
        shared_stub.call_claude.return_value = (
            "Doc content\n"
            "---GAPS---\n"
            "1. First question?\n"
            "---GAPS---\n"
            "2. Should not be in doc"
        )

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

        assert "---GAPS---" not in doc
        # Second delimiter appears in gaps section (not the doc)
        assert "First question?" in gaps

    def test_doc_and_gaps_are_stripped(self, tool, shared_stub):
        """Returned strings have leading/trailing whitespace stripped."""
        shared_stub.call_claude.return_value = "  \n  doc with spaces  \n  ---GAPS---  \n  gaps  \n  "

        doc, gaps = tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_claude_raises_exception_propagates(self, tool, shared_stub):
        """Exceptions from call_claude bubble up to the caller."""
        shared_stub.call_claude.side_effect = RuntimeError("Claude API unreachable")

        with pytest.raises(RuntimeError, match="Claude API unreachable"):
            tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

    def test_get_repo_files_raises_exception_propagates(self, tool, shared_stub):
        """Exceptions from get_repo_files bubble up."""
        shared_stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

    @patch("datetime.datetime", _FrozenDatetime)
    def test_date_formatted_correctly_in_prompt(self, tool, shared_stub):
        """The date in the prompt matches %Y-%m-%d format of utcnow."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "2024-06-15" in prompt_arg

    def test_large_file_content_truncated_in_user_message(self, tool, shared_stub):
        """Files with >3000 chars are truncated before being sent to Claude."""
        long_content = "x" * 10_000
        shared_stub.get_repo_files.return_value = {"bigfile.py": long_content}
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        tool.generate_biz_doc("o", "r", "P", "1.0", "http://url")

        user_message = shared_stub.call_claude.call_args[0][1]
        # The truncated content should appear but not the full 10k chars
        assert "x" * 3000 in user_message
        assert "x" * 3001 not in user_message

    def test_special_characters_in_project_name(self, tool, shared_stub):
        """Special characters in project_name do not break formatting."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        doc, gaps = tool.generate_biz_doc(
            "o", "r", "Project: <Risk & Classification> v2", "1.0", "http://url"
        )
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_owner_and_repo_appear_in_user_message(self, tool, shared_stub):
        """Owner/repo path is referenced in Claude's user message."""
        shared_stub.call_claude.return_value = "doc\n---GAPS---\ngap"

        tool.generate_biz_doc("myowner", "myrepo", "P", "1.0", "http://url")

        user_message = shared_stub.call_claude.call_args[0][1]
        assert "myowner" in user_message
        assert "myrepo" in user_message


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:
    """Tests for build_full_output()."""

    DOC = "# Solution overview\n\nSome great content."
    GAPS = "1. Who owns this?\n2. What is the go-live date?"

    def test_returns_tuple_of_two_strings(self, tool):
        result = tool.build_full_output(
            self.DOC, self.GAPS, "acme", "underwriting",
            "Underwriting Risk Classification", "1.0.0"