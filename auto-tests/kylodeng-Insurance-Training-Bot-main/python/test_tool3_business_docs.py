"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty files
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content checks
    - __main__ block logic (via subprocess or direct import simulation)
    - Edge cases: version strings, special characters in project names, empty gaps

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (frozen time)
    - os.environ                  → monkeypatch (pytest fixture)

TODOs:
    # TODO: Integration test for full __main__ execution requires a live GitHub token
    # TODO: Test actual Claude response parsing with real API — stub only here
"""

import sys
import os
import importlib
import datetime
import types
from unittest.mock import patch, MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test while controlling its dependencies
# ---------------------------------------------------------------------------

FAKE_SHARED_ATTRS = {
    "call_claude": MagicMock(return_value="doc content\n---GAPS---\n1. Question one?"),
    "get_repo_files": MagicMock(return_value={"README.md": "# Hello"}),
    "write_output_file": MagicMock(return_value="https://github.com/output/file"),
    "send_email": MagicMock(),
    "email_html": MagicMock(return_value="<html>body</html>"),
    "write_audit_entry": MagicMock(),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_module():
    """Create a fake `shared` module so we can import tool3 without real deps."""
    mod = types.ModuleType("shared")
    for k, v in FAKE_SHARED_ATTRS.items():
        setattr(mod, k, v)
    return mod


def _reset_shared_mocks(shared_mod):
    """Reset all MagicMock attributes on the fake shared module."""
    for k, v in vars(shared_mod).items():
        if isinstance(v, MagicMock):
            v.reset_mock()
    # Restore default return values after reset
    shared_mod.call_claude.return_value = "doc content\n---GAPS---\n1. Question one?"
    shared_mod.get_repo_files.return_value = {"README.md": "# Hello"}
    shared_mod.write_output_file.return_value = "https://github.com/output/file"
    shared_mod.email_html.return_value = "<html>body</html>"


@pytest.fixture(scope="module")
def shared_mod():
    mod = _make_shared_module()
    sys.modules["shared"] = mod
    yield mod
    sys.modules.pop("shared", None)


@pytest.fixture()
def tool3(shared_mod):
    """Import (or re-import) the module under test fresh for each test."""
    _reset_shared_mocks(shared_mod)
    # Remove cached version so we can re-import cleanly
    sys.modules.pop("tool3_business_docs", None)
    scripts_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the current directory structure
    for candidate in [
        scripts_dir,
        os.path.join(os.path.dirname(__file__), ".github", "scripts"),
        os.path.join(os.path.dirname(__file__)),
    ]:
        if os.path.isfile(os.path.join(candidate, "tool3_business_docs.py")):
            if candidate not in sys.path:
                sys.path.insert(0, candidate)
            break

    import tool3_business_docs as t3
    return t3


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


def frozen_utcnow():
    return FROZEN_DT


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_delimiter(self, tool3, shared_mod):
        """Claude returns well-formed response with ---GAPS--- delimiter."""
        shared_mod.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "README.md": "# My Project",
        }
        shared_mod.call_claude.return_value = (
            "# Solution overview: MyApp\nSome content.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who are the key users?"
        )

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

        assert "Solution overview" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who are the key users?" in gaps

    def test_missing_gaps_delimiter_uses_fallback(self, tool3, shared_mod):
        """When ---GAPS--- is absent, gaps should contain fallback message."""
        shared_mod.call_claude.return_value = "Just a document with no delimiter at all."

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc("acme", "myrepo", "MyApp", "1.0.0", "https://run.url")

        assert doc == "Just a document with no delimiter at all."
        assert "could not extract gap questions" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, tool3, shared_mod):
        """Verify that get_repo_files is called with the expected file extensions."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            tool3.generate_biz_doc("owner", "repo", "proj", "0.1.0", "https://x")

        call_args = shared_mod.get_repo_files.call_args
        extensions = call_args[0][2]  # positional arg index 2
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert ".yaml" in extensions
        assert call_args[1]["max_files"] == 20

    def test_call_claude_receives_formatted_prompt(self, tool3, shared_mod):
        """Ensure the prompt passed to call_claude contains project_name and version."""
        shared_mod.get_repo_files.return_value = {"app.py": "x = 1"}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            tool3.generate_biz_doc("acme", "testrepo", "InsuranceApp", "2.3.4", "https://run")

        system_arg = shared_mod.call_claude.call_args[0][0]
        assert "InsuranceApp" in system_arg
        assert "2.3.4" in system_arg

    def test_empty_repo_files(self, tool3, shared_mod):
        """generate_biz_doc should handle an empty files dict gracefully."""
        shared_mod.get_repo_files.return_value = {}
        shared_mod.call_claude.return_value = "doc\n---GAPS---\n1. Question?"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc("o", "r", "EmptyProject", "0.0.1", "https://x")

        assert doc == "doc"
        assert "1. Question?" in gaps

    def test_delimiter_at_start_of_response(self, tool3, shared_mod):
        """Edge case: delimiter appears immediately at start (empty doc part)."""
        shared_mod.call_claude.return_value = "---GAPS---\n1. Only a question."

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://x")

        assert doc == ""
        assert "1. Only a question." in gaps

    def test_delimiter_at_end_of_response(self, tool3, shared_mod):
        """Edge case: delimiter appears at end (empty gaps part)."""
        shared_mod.call_claude.return_value = "Full document content here.\n---GAPS---"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://x")

        assert doc == "Full document content here."
        assert gaps == ""

    def test_multiple_delimiters_only_first_split(self, tool3, shared_mod):
        """Only first ---GAPS--- should be used as split point."""
        shared_mod.call_claude.return_value = (
            "Doc part\n---GAPS---\n1. Q one?\n---GAPS---\n2. Q two?"
        )

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc("o", "r", "P", "1.0", "https://x")

        assert doc == "Doc part"
        assert "1. Q one?" in gaps
        assert "---GAPS---" in gaps  # second delimiter is now inside gaps

    def test_file_content_truncated_to_3000_chars(self, tool3, shared_mod):
        """Files with content > 3000 chars should be truncated in the prompt."""
        long_content = "x" * 5000
        shared_mod.get_repo_files.return_value = {"big_file.py": long_content}

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            tool3.generate_biz_doc("o", "r", "P", "1.0", "https://x")

        user_message = shared_mod.call_claude.call_args[0][1]
        # The content in the message should not exceed 3000 'x' chars for that file
        assert "x" * 3001 not in user_message
        assert "x" * 3000 in user_message

    def test_special_characters_in_project_name(self, tool3, shared_mod):
        """Project names with special chars should not break formatting."""
        shared_mod.call_claude.return_value = "doc\n---GAPS---\n1. Q?"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc(
                "owner", "repo", "My Project & Partners <v2>", "1.0.0-beta+001", "https://x"
            )

        assert doc == "doc"
        assert gaps == "1. Q?"

    @pytest.mark.parametrize("project_name,version", [
        ("Generations II", "1.0.0"),
        ("Health Products", "2.3.4"),
        ("VIP Medical Navigation", "0.1.0-alpha"),
        ("Global Cashless Network", "10.0.0"),
    ])
    def test_insurance_product_names(self, tool3, shared_mod, project_name, version):
        """Parameterised test with insurance product names from synthetic data."""
        shared_mod.call_claude.return_value = f"# {project_name}\ncontent\n---GAPS---\n1. Date?"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            doc, gaps = tool3.generate_biz_doc("sunlife", "docs", project_name, version, "https://x")

        assert project_name in doc
        assert "1. Date?" in gaps


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================

class TestBuildFullOutput:

    def test_full_md_contains_doc_and_gaps(self, tool3, shared_mod):
        """Full markdown should contain both the doc part and the gaps section."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            full_md, gap_only_md = tool3.build_full_output(
                "## My Doc", "1. What is the date?",
                "acme", "repo", "MyProject", "1.2.3"
            )

        assert "## My Doc" in full_md
        assert "1. What is the date?" in full_md
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, tool3, shared_mod):
        """Full markdown should reference the source repo and version."""
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FROZEN_DT
            mock_dt.utcnow.return_value.strftime = FROZEN_DT.strftime
            full_md, _ = tool3.build_full_output(
                "doc", "gaps", "acme", "myrepo", "Project", "3.0.0"
            )

        assert "acme/myrepo" in full_md
        assert "3.0.0" in full_md
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_project_name_and_version(self