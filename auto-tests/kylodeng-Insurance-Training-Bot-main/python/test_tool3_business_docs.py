"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), edge cases
    - build_full_output(): happy path, content assertions, boundary values

Mocks used:
    - shared.call_claude          → returns controlled Claude response strings
    - shared.get_repo_files       → returns controlled dict of file contents
    - shared.write_output_file    → no-op / returns fake URL
    - shared.send_email           → no-op
    - shared.email_html           → returns simple string
    - shared.write_audit_entry    → no-op
    - datetime.datetime.utcnow    → frozen to a known timestamp
    - os.environ                  → patched per test via monkeypatch

TODOs:
    - TODO: Integration test with real Claude API (requires ANTHROPIC_API_KEY secret)
    - TODO: Test __main__ block behaviour for missing SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars
    - TODO: Test write_output_file failure path in __main__ (requires inspecting sys.exit / exception propagation)
"""

import sys
import os
import types
import datetime
import importlib
from unittest.mock import MagicMock, patch, call
import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import the target without the
# real shared.py being present or having side-effects.
# ---------------------------------------------------------------------------

SHARED_STUB_ATTRS = {
    "call_claude": MagicMock(return_value="## Doc\n---GAPS---\n1. Question?"),
    "get_repo_files": MagicMock(return_value={"README.md": "# Hello"}),
    "write_output_file": MagicMock(return_value="https://github.com/output/repo/blob/main/doc.md"),
    "send_email": MagicMock(),
    "email_html": MagicMock(return_value="<html>OK</html>"),
    "write_audit_entry": MagicMock(),
    "OUTPUT_REPO_OWNER": "test-owner",
    "OUTPUT_REPO": "test-output-repo",
}


def _make_shared_stub():
    stub = types.ModuleType("shared")
    for k, v in SHARED_STUB_ATTRS.items():
        setattr(stub, k, v)
    return stub


# Insert stub before importing target module
shared_stub = _make_shared_stub()
sys.modules["shared"] = shared_stub

# Now import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool3_business_docs.py"

# If running from repo root the path above is canonical; support flat layout too.
if not _script_path.exists():
    _script_path = pathlib.Path(__file__).parent / "tool3_business_docs.py"

spec = importlib.util.spec_from_file_location("tool3_business_docs", _script_path)
biz_docs = importlib.util.module_from_spec(spec)

# Re-inject shared stub into the module's namespace before exec
biz_docs.shared = shared_stub  # type: ignore
# Patch sys.modules so the import inside the module resolves to our stub
sys.modules["tool3_business_docs"] = biz_docs
spec.loader.exec_module(biz_docs)  # type: ignore

generate_biz_doc = biz_docs.generate_biz_doc
build_full_output = biz_docs.build_full_output

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FROZEN_NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture(autouse=True)
def reset_mocks():
    """Reset all shared stubs between tests."""
    for k, v in SHARED_STUB_ATTRS.items():
        if isinstance(v, MagicMock):
            v.reset_mock()
    # Restore defaults
    shared_stub.call_claude.return_value = "## Doc\n---GAPS---\n1. Question?"
    shared_stub.get_repo_files.return_value = {"README.md": "# Hello"}
    shared_stub.write_output_file.return_value = (
        "https://github.com/output/repo/blob/main/doc.md"
    )
    shared_stub.email_html.return_value = "<html>OK</html>"
    yield


@pytest.fixture()
def frozen_datetime(monkeypatch):
    """Freeze datetime.datetime.utcnow() to FROZEN_NOW."""

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return FROZEN_NOW

    monkeypatch.setattr(biz_docs.datetime, "datetime", _FakeDatetime)
    return _FakeDatetime


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------


class TestGenerateBizDocHappyPath:
    def test_returns_tuple_of_two_strings(self, frozen_datetime):
        doc, gaps = generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_calls_get_repo_files_with_correct_params(self, frozen_datetime):
        generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run")
        shared_stub.get_repo_files.assert_called_once_with(
            "acme",
            "my-repo",
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_calls_call_claude_once(self, frozen_datetime):
        generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run")
        assert shared_stub.call_claude.call_count == 1

    def test_prompt_contains_project_name(self, frozen_datetime):
        generate_biz_doc("acme", "my-repo", "Generations II", "2.0.0", "https://run")
        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "Generations II" in prompt_arg

    def test_prompt_contains_version(self, frozen_datetime):
        generate_biz_doc("acme", "my-repo", "My Project", "3.5.1", "https://run")
        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "3.5.1" in prompt_arg

    def test_prompt_contains_frozen_date(self, frozen_datetime):
        generate_biz_doc("acme", "my-repo", "My Project", "1.0.0", "https://run")
        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert FROZEN_DATE_STR in prompt_arg

    def test_user_message_contains_owner_and_repo(self, frozen_datetime):
        generate_biz_doc("sun-life", "health-products", "Health", "1.0.0", "https://run")
        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "sun-life/health-products" in user_msg

    def test_user_message_contains_file_content(self, frozen_datetime):
        shared_stub.get_repo_files.return_value = {
            "main.py": "def hello(): pass",
            "README.md": "# Project",
        }
        generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "main.py" in user_msg
        assert "def hello(): pass" in user_msg

    def test_splits_on_gaps_delimiter(self, frozen_datetime):
        shared_stub.call_claude.return_value = (
            "## Solution Overview\nSome content here.\n---GAPS---\n1. Who is the owner?"
        )
        doc, gaps = generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        assert "Solution Overview" in doc
        assert "Who is the owner?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_doc_part_is_stripped(self, frozen_datetime):
        shared_stub.call_claude.return_value = (
            "   ## Doc   \n---GAPS---\n   1. Q?   "
        )
        doc, gaps = generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        assert doc == doc.strip()
        assert gaps == gaps.strip()


class TestGenerateBizDocNoGapsDelimiter:
    def test_full_response_used_as_doc_when_no_delimiter(self, frozen_datetime):
        shared_stub.call_claude.return_value = "## Full doc without delimiter"
        doc, gaps = generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        assert "Full doc without delimiter" in doc

    def test_fallback_gaps_message_when_no_delimiter(self, frozen_datetime):
        shared_stub.call_claude.return_value = "## Full doc without delimiter"
        doc, gaps = generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        assert "Claude could not extract gap questions" in gaps

    def test_only_first_delimiter_used_for_split(self, frozen_datetime):
        shared_stub.call_claude.return_value = (
            "## Doc\n---GAPS---\n1. Q?\n---GAPS---\nExtra"
        )
        doc, gaps = generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        # The second ---GAPS--- should be in the gaps section, not the doc
        assert "---GAPS---" not in doc
        assert "Extra" in gaps

    def test_empty_string_response_from_claude(self, frozen_datetime):
        shared_stub.call_claude.return_value = ""
        doc, gaps = generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)


class TestGenerateBizDocEdgeCases:
    def test_empty_files_dict(self, frozen_datetime):
        shared_stub.get_repo_files.return_value = {}
        doc, gaps = generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "Files:" in user_msg  # header still present

    def test_file_content_truncated_to_3000_chars(self, frozen_datetime):
        long_content = "x" * 5000
        shared_stub.get_repo_files.return_value = {"big.py": long_content}
        generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        user_msg = shared_stub.call_claude.call_args[0][1]
        # 3000 x's should appear, but not 5000
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_project_name_with_spaces(self, frozen_datetime):
        generate_biz_doc("acme", "repo", "My Great Project", "1.0.0", "https://run")
        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "My Great Project" in prompt_arg

    def test_version_with_prerelease_tag(self, frozen_datetime):
        generate_biz_doc("acme", "repo", "Project", "2.0.0-rc.1", "https://run")
        prompt_arg = shared_stub.call_claude.call_args[0][0]
        assert "2.0.0-rc.1" in prompt_arg

    def test_multiple_files_all_included_in_user_message(self, frozen_datetime):
        shared_stub.get_repo_files.return_value = {
            "a.py": "code_a",
            "b.tf": "resource {}",
            "c.md": "# Docs",
        }
        generate_biz_doc("acme", "repo", "Project", "1.0.0", "https://run")
        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "a.py" in user_msg
        assert "b.tf" in user_msg
        assert "c.md" in user_msg


class TestGenerateBizDocInsuranceSyntheticData:
    """Tests using the synthetic insurance product data as representative inputs."""

    @pytest.mark.parametrize(
        "project_name,version",
        [
            ("Generations II", "2.0.0"),
            ("List of Designated Hospitals in Mainland China", "1.0.0"),
            ("Global Network Hospital List for Cashless Arrangement", "1.5.0"),
            ("Mainland China VIP Medical Navigation Service", "3.0.0"),
        ],
    )
    def test_insurance_products_generate_valid_output(
        self, project_name, version, frozen_datetime
    ):
        shared_stub.call_claude.return_value = (
            f"## Solution overview: {project_name}\n"
            f"Some business content.\n"
            f"---GAPS---\n"
            f"1. What is the go-live date?\n"
            f"2. Who are the key users?"
        )
        doc, gaps = generate_biz_doc(
            "sun-life", "health-products", project_name, version, "https://run"
        )
        assert project_name in doc
        assert "go-live date" in gaps
        assert "key users" in gaps

    def test_insurance_project_name_in_user_message(self, frozen_datetime):
        project = "Generations II"
        generate_biz_doc("sun-life", "generations-ii", project, "2.0.0", "https://run")
        user_msg = shared_stub.call_claude.call_args[0][1]
        assert "sun-life/generations-ii" in user_msg


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------


class TestBuildFullOutputHappyPath:
    def test_returns_tuple_of_two_strings(self, frozen_datetime):
        full, gap_only = build_full_output(
            "## Doc", "1. Q?", "acme", "repo", "Project", "1.0.0"
        )
        assert isinstance(full, str)
        assert isinstance(gap_only, str)

    def test_full_md_contains_doc_content(self, frozen_datetime):
        full, _ = build_full_output(
            "## My Doc Content", "1. Q?", "acme", "repo", "Project", "1.0.0"
        )
        assert "## My Doc Content" in full

    def test_full_md_contains_gaps_content(self, frozen_datetime):
        full, _ = build_full_output(
            "## Doc", "1. What is the deadline?", "acme", "repo", "Project", "1.0.0"
        )
        assert "What is the deadline?" in full

    def