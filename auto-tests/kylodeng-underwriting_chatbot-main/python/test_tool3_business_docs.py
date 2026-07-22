"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, file content truncation
    - build_full_output(): happy path, correct structure, gap questionnaire standalone doc
    - __main__ block: full integration via subprocess / env patching, success & failure paths
    - SYSTEM prompt template: format keys, delimiter presence

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (frozen time)

TODOs:
    - TODO: test actual subprocess execution of __main__ block end-to-end
      (requires a full environment with credentials)
    - TODO: test behaviour when get_repo_files returns binary/non-UTF8 content
    - TODO: test email send failure propagation (currently swallowed in some paths)
"""

import importlib
import sys
import os
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with its shared dependency stubbed
# ---------------------------------------------------------------------------

SHARED_STUB_ATTRS = dict(
    call_claude=MagicMock(return_value="doc content\n---GAPS---\n1. A question?"),
    get_repo_files=MagicMock(return_value={}),
    write_output_file=MagicMock(return_value="https://github.com/output/file"),
    send_email=MagicMock(),
    email_html=MagicMock(return_value="<html>email</html>"),
    write_audit_entry=MagicMock(),
    OUTPUT_REPO_OWNER="output-owner",
    OUTPUT_REPO="output-repo",
)


def _make_shared_stub():
    """Return a fresh module stub for 'shared'."""
    stub = types.ModuleType("shared")
    for k, v in SHARED_STUB_ATTRS.items():
        setattr(stub, k, v if not callable(v) else MagicMock(side_effect=v.side_effect,
                                                               return_value=v.return_value) if isinstance(v, MagicMock) else v)
    # Re-create fresh MagicMocks so tests are independent
    stub.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. A question?")
    stub.get_repo_files = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value="https://github.com/output/file")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>email</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "output-owner"
    stub.OUTPUT_REPO = "output-repo"
    return stub


@pytest.fixture()
def shared_stub():
    """Inject a fresh shared stub and import (or reload) the module under test."""
    stub = _make_shared_stub()
    sys.modules["shared"] = stub

    # Remove cached version so we get a clean import
    sys.modules.pop("tool3_business_docs", None)
    # The script lives in .github/scripts — add that to path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the directory of this test file itself (CI may run from repo root)
    test_dir = os.path.dirname(os.path.abspath(__file__))
    github_scripts = os.path.join(test_dir, ".github", "scripts")
    for p in (test_dir, github_scripts, scripts_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    import tool3_business_docs as mod
    yield mod, stub

    # Cleanup
    sys.modules.pop("tool3_business_docs", None)


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE = "2024-06-15"
FROZEN_DATETIME = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_time():
    with patch("tool3_business_docs.datetime") as mock_dt:
        mock_dt.datetime.utcnow.return_value = FROZEN_DT
        mock_dt.datetime.utcnow.return_value.strftime = FROZEN_DT.strftime
        # Re-attach strftime so format calls work
        mock_dt.datetime.utcnow.return_value = FROZEN_DT
        yield mock_dt


# ---------------------------------------------------------------------------
# SYSTEM prompt template tests (no I/O)
# ---------------------------------------------------------------------------

class TestSystemPromptTemplate:
    """Verify the SYSTEM string is well-formed and formattable."""

    def test_system_contains_delimiter_instruction(self, shared_stub):
        mod, _ = shared_stub
        assert "---GAPS---" in mod.SYSTEM

    def test_system_format_keys_present(self, shared_stub):
        mod, _ = shared_stub
        formatted = mod.SYSTEM.format(
            project_name="MyProject",
            version="1.0.0",
            date="2024-06-15",
        )
        assert "MyProject" in formatted
        assert "1.0.0" in formatted
        assert "2024-06-15" in formatted

    def test_system_no_extra_format_keys(self, shared_stub):
        """Formatting with only the three expected keys must not raise."""
        mod, _ = shared_stub
        try:
            mod.SYSTEM.format(project_name="P", version="V", date="D")
        except KeyError as exc:
            pytest.fail(f"SYSTEM has unexpected format key: {exc}")

    def test_system_contains_output1_section(self, shared_stub):
        mod, _ = shared_stub
        assert "Solution Overview Document" in mod.SYSTEM or "Solution overview" in mod.SYSTEM

    def test_system_contains_output2_section(self, shared_stub):
        mod, _ = shared_stub
        assert "gap questionnaire" in mod.SYSTEM.lower()

    def test_system_contains_rules_block(self, shared_stub):
        mod, _ = shared_stub
        assert "RULES:" in mod.SYSTEM or "Never invent" in mod.SYSTEM


# ---------------------------------------------------------------------------
# generate_biz_doc() tests
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:
    """Tests for the generate_biz_doc function."""

    # -- happy path ----------------------------------------------------------

    def test_happy_path_returns_two_parts(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "Solution Overview\n---GAPS---\n1. Who owns this?"
        stub.get_repo_files.return_value = {"README.md": "# Hello"}

        doc, gaps = mod.generate_biz_doc("acme", "myrepo", "MyProject", "1.2.3", "https://run.url")

        assert doc == "Solution Overview"
        assert gaps == "1. Who owns this?"

    def test_calls_get_repo_files_with_correct_extensions(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "doc\n---GAPS---\ngaps"

        mod.generate_biz_doc("owner", "repo", "proj", "0.1", "url")

        call_args = stub.get_repo_files.call_args
        _, kwargs = call_args if call_args[1] else (call_args[0], {})
        # positional call: get_repo_files(owner, repo, extensions, max_files=20)
        args = call_args[0]
        assert "owner" == args[0]
        assert "repo" == args[1]
        extensions = args[2]
        for ext in [".py", ".md", ".tf", ".yaml"]:
            assert ext in extensions

    def test_calls_call_claude_once(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "d\n---GAPS---\ng"

        mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert stub.call_claude.call_count == 1

    def test_prompt_contains_project_name(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "d\n---GAPS---\ng"

        mod.generate_biz_doc("o", "r", "MyUnderwritingTool", "1.0", "u")

        prompt_arg = stub.call_claude.call_args[0][0]
        assert "MyUnderwritingTool" in prompt_arg

    def test_prompt_contains_version(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "d\n---GAPS---\ng"

        mod.generate_biz_doc("o", "r", "p", "2.5.0", "u")

        prompt_arg = stub.call_claude.call_args[0][0]
        assert "2.5.0" in prompt_arg

    def test_user_message_contains_owner_repo(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "d\n---GAPS---\ng"

        mod.generate_biz_doc("acme-corp", "risk-engine", "p", "v", "u")

        user_msg = stub.call_claude.call_args[0][1]
        assert "acme-corp/risk-engine" in user_msg

    # -- delimiter absent ----------------------------------------------------

    def test_no_delimiter_returns_full_raw_as_doc(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "Full response without delimiter"

        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "Full response without delimiter"

    def test_no_delimiter_returns_fallback_gaps(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "No delimiter here"

        _, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert "manually" in gaps.lower() or "could not extract" in gaps.lower()

    def test_multiple_delimiters_splits_on_first(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "part1\n---GAPS---\npart2\n---GAPS---\npart3"

        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "part1"
        assert "part2" in gaps
        assert "part3" in gaps

    # -- file content handling -----------------------------------------------

    def test_files_are_truncated_to_3000_chars(self, shared_stub):
        mod, stub = shared_stub
        long_content = "x" * 5000
        stub.get_repo_files.return_value = {"big_file.py": long_content}
        stub.call_claude.return_value = "d\n---GAPS---\ng"

        mod.generate_biz_doc("o", "r", "p", "v", "u")

        user_msg = stub.call_claude.call_args[0][1]
        # The truncated content (3000 x's) should appear, not 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files(self, shared_stub):
        mod, stub = shared_stub
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "d\n---GAPS---\ng"

        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "d"
        assert gaps == "g"

    def test_multiple_files_joined_correctly(self, shared_stub):
        mod, stub = shared_stub
        stub.get_repo_files.return_value = {
            "model_card.json": '{"model_name": "Underwriting Risk Classification"}',
            "README.md": "# MyProject",
        }
        stub.call_claude.return_value = "d\n---GAPS---\ng"

        mod.generate_biz_doc("o", "r", "p", "v", "u")

        user_msg = stub.call_claude.call_args[0][1]
        assert "model_card.json" in user_msg
        assert "README.md" in user_msg

    # -- error propagation ---------------------------------------------------

    def test_call_claude_exception_propagates(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.side_effect = RuntimeError("Claude API down")

        with pytest.raises(RuntimeError, match="Claude API down"):
            mod.generate_biz_doc("o", "r", "p", "v", "u")

    def test_get_repo_files_exception_propagates(self, shared_stub):
        mod, stub = shared_stub
        stub.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_biz_doc("o", "r", "p", "v", "u")

    # -- stripping whitespace ------------------------------------------------

    def test_doc_and_gaps_are_stripped(self, shared_stub):
        mod, stub = shared_stub
        stub.call_claude.return_value = "  doc with spaces  \n---GAPS---\n  gaps with spaces  "

        doc, gaps = mod.generate_biz_doc("o", "r", "p", "v", "u")

        assert doc == "doc with spaces"
        assert gaps == "gaps with spaces"


# ---------------------------------------------------------------------------
# build_full_output() tests
# ---------------------------------------------------------------------------

class TestBuildFullOutput:
    """Tests for the build_full_output function."""

    @pytest.fixture(autouse=True)
    def _freeze_datetime(self, shared_stub):
        """Freeze datetime inside the already-imported module."""
        self.mod, self.stub = shared_stub
        with patch("tool3_business_docs.datetime") as mock_dt:
            mock_dt.datetime.utcnow.return_value = FROZEN_DT
            # Make strftime work on the frozen object
            class _FrozenDT:
                @staticmethod
                def utcnow():
                    return FROZEN_DT
            mock_dt.datetime = _FrozenDT
            # Also make FROZEN_DT.strftime work normally
            yield

    def _call(self, doc="## Doc", gaps="1. Gap question?",
              owner="acme", repo="risk-engine",
              project_name="RiskEngine", version="1.0.0"):
        return self.mod.build_full_output(doc, gaps, owner, repo, project_name, version)

    # -- structure of full_md ------------------------------------------------

    def test_full_md_contains_doc(self):
        full_md, _ = self._call(doc="## The Document")
        assert "## The Document" in full_md

    def test_full_md_contains_