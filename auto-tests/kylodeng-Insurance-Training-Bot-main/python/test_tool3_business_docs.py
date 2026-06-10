"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter present/absent, partial responses
    - build_full_output(): structure, content inclusion, edge cases (empty gaps, long strings)
    - __main__ block behaviour: env-var wiring, success path, exception/failure path
    - Boundary values: empty strings, None-like inputs, version formats

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (frozen time)
    - sys.argv / os.environ       → monkeypatch / unittest.mock.patch.dict

TODOs:
    - TODO: Integration test with real Claude API (requires ANTHROPIC_API_KEY secret)
    - TODO: Test write_output_file returns a usable URL (requires OUTPUT_REPO config)
    - TODO: Validate generated markdown structure with a markdown parser
"""

import sys
import os
import types
import importlib
import datetime
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test with all external deps stubbed
# ---------------------------------------------------------------------------

SHARED_MODULE_PATH = os.path.join(os.path.dirname(__file__), ".github", "scripts")


def _make_shared_stub():
    """Return a minimal stub for the `shared` module."""
    stub = types.ModuleType("shared")
    stub.call_claude = MagicMock(return_value="doc content\n---GAPS---\n1. Gap question?")
    stub.get_repo_files = MagicMock(return_value={"main.py": "print('hello')"})
    stub.write_output_file = MagicMock(return_value="https://github.com/output/repo/file.md")
    stub.send_email = MagicMock()
    stub.email_html = MagicMock(return_value="<html>body</html>")
    stub.write_audit_entry = MagicMock()
    stub.OUTPUT_REPO_OWNER = "test-owner"
    stub.OUTPUT_REPO = "test-output-repo"
    return stub


def _import_module(shared_stub=None):
    """
    Import tool3_business_docs with the shared stub injected.
    Re-imports fresh each time so state does not leak between tests.
    """
    if shared_stub is None:
        shared_stub = _make_shared_stub()

    # Remove cached versions
    for key in list(sys.modules.keys()):
        if "tool3_business_docs" in key:
            del sys.modules[key]

    sys.modules["shared"] = shared_stub

    # Build the file path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    spec_path = os.path.join(script_dir, "tool3_business_docs.py")

    import importlib.util
    spec = importlib.util.spec_from_file_location("tool3_business_docs", spec_path)
    module = importlib.util.module_from_spec(spec)
    # Prevent __main__ block from running on import
    with patch.object(spec.loader, "exec_module", wraps=spec.loader.exec_module):
        try:
            spec.loader.exec_module(module)
        except SystemExit:
            pass

    return module, shared_stub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FROZEN_DATE = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def mod_and_stub():
    """Return (module, shared_stub) with a fresh import."""
    return _import_module()


@pytest.fixture()
def mod(mod_and_stub):
    module, _ = mod_and_stub
    return module


@pytest.fixture()
def stub(mod_and_stub):
    _, shared_stub = mod_and_stub
    return shared_stub


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, mod, stub):
        """Claude returns both parts separated by ---GAPS---."""
        stub.call_claude.return_value = (
            "# Solution Overview\nSome content\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the business owner?"
        )
        stub.get_repo_files.return_value = {"app.py": "x = 1", "infra.tf": "resource {}"}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            doc, gaps = mod.generate_biz_doc("myowner", "myrepo", "MyProject", "1.2.3", "https://run.url")

        assert "# Solution Overview" in doc
        assert "Some content" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the business owner?" in gaps
        # Delimiter itself must NOT appear in either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_no_delimiter_returns_full_raw_and_fallback(self, mod, stub):
        """When Claude omits ---GAPS--- the full response goes to doc and fallback to gaps."""
        stub.call_claude.return_value = "Only a doc, no delimiter here."

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "0.1.0", "url")

        assert doc == "Only a doc, no delimiter here."
        assert "could not extract gap questions" in gaps

    def test_delimiter_at_start_of_response(self, mod, stub):
        """---GAPS--- is the very first token — doc_part should be empty string."""
        stub.call_claude.return_value = "---GAPS---\n1. First question?"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        assert doc == ""
        assert "1. First question?" in gaps

    def test_delimiter_at_end_of_response(self, mod, stub):
        """---GAPS--- at the very end — gaps_part is empty string."""
        stub.call_claude.return_value = "Only doc content\n---GAPS---"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "v1", "url")

        assert "Only doc content" in doc
        assert gaps == ""

    def test_get_repo_files_called_with_correct_extensions(self, mod, stub):
        """Verify that get_repo_files is invoked with the expected extensions list."""
        stub.call_claude.return_value = "x---GAPS---y"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            mod.generate_biz_doc("owner", "repo", "Proj", "2.0", "url")

        call_args = stub.get_repo_files.call_args
        assert call_args[0][0] == "owner"
        assert call_args[0][1] == "repo"
        extensions = call_args[0][2]
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert call_args[1].get("max_files", call_args[0][3] if len(call_args[0]) > 3 else 20) == 20

    def test_call_claude_receives_project_name_in_prompt(self, mod, stub):
        """The project_name, version, and date must be interpolated into the prompt."""
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            mod.generate_biz_doc("o", "r", "InsurancePortal", "3.1.4", "url")

        prompt_arg = stub.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg
        assert "3.1.4" in prompt_arg

    def test_call_claude_receives_repo_files_in_user_message(self, mod, stub):
        """File contents must appear in the user-facing message sent to Claude."""
        stub.get_repo_files.return_value = {
            "main.py": "def handler(): pass",
            "README.md": "# Generations II",
        }
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        user_msg = stub.call_claude.call_args[0][1]
        assert "main.py" in user_msg
        assert "def handler(): pass" in user_msg
        assert "README.md" in user_msg

    def test_files_content_truncated_to_3000_chars(self, mod, stub):
        """Content longer than 3000 chars must be truncated in the user message."""
        long_content = "x" * 5000
        stub.get_repo_files.return_value = {"big_file.py": long_content}
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        user_msg = stub.call_claude.call_args[0][1]
        # The truncated portion is 3000 x's max
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_empty_repo_files_dict(self, mod, stub):
        """Empty file dict should not raise — Claude still gets called."""
        stub.get_repo_files.return_value = {}
        stub.call_claude.return_value = "doc---GAPS---gaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_multiple_delimiter_occurrences_only_first_used(self, mod, stub):
        """Only the first ---GAPS--- delimiter should split the response."""
        stub.call_claude.return_value = (
            "doc part\n---GAPS---\nfirst gap\n---GAPS---\nshould be in gaps still"
        )

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            doc, gaps = mod.generate_biz_doc("o", "r", "P", "1.0", "url")

        assert doc == "doc part"
        assert "first gap" in gaps
        assert "should be in gaps still" in gaps


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    @pytest.fixture()
    def base_args(self):
        return dict(
            doc="# Solution Overview\nSome text.",
            gaps="1. Who is the owner?\n2. What is the go-live date?",
            owner="acme-corp",
            repo="insurance-portal",
            project_name="InsurancePortal",
            version="1.0.0",
        )

    def test_full_md_contains_doc_content(self, mod, base_args):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            full_md, _ = mod.build_full_output(**base_args)

        assert "# Solution Overview" in full_md
        assert "Some text." in full_md

    def test_full_md_contains_gaps_section(self, mod, base_args):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            full_md, _ = mod.build_full_output(**base_args)

        assert "Gap Questionnaire" in full_md
        assert "1. Who is the owner?" in full_md
        assert "2. What is the go-live date?" in full_md

    def test_full_md_contains_attribution_footer(self, mod, base_args):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            full_md, _ = mod.build_full_output(**base_args)

        assert "AI Delivery Bot" in full_md
        assert "acme-corp/insurance-portal" in full_md
        assert "1.0.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, mod, base_args):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DATE
            mock_dt.utcnow.return_value.strftime = FROZEN_DATE.strftime
            _, gap_only_md = mod.build_full_output(**base_args)

        assert "InsurancePortal" in gap_only_md
        assert "1.0.0" in gap_only_md

    def test_gap_only_md_contains_gap_questions(self, mod, base_args):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value =