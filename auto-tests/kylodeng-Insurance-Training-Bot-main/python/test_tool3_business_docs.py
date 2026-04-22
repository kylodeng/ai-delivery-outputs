"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), error propagation
    - build_full_output(): full markdown assembly, gap-only markdown assembly, content correctness
    - __main__ block logic (via importlib / subprocess patching) — entry-point integration
    - Edge cases: missing delimiter in Claude response, empty gaps, special characters in inputs

Mocks used:
    - shared.call_claude          — prevents real Anthropic API calls
    - shared.get_repo_files       — prevents real GitHub API calls
    - shared.write_output_file    — prevents real file/repo writes
    - shared.send_email           — prevents real SMTP/SES calls
    - shared.email_html           — pure helper, mocked for isolation
    - shared.write_audit_entry    — prevents real audit writes
    - datetime.datetime.utcnow    — frozen for deterministic output
    - os.environ                  — patched per-test via monkeypatch

TODOs:
    - TODO: Integration test against a real GitHub repo + Claude API (needs credentials)
    - TODO: Test the truncated __main__ block exception path fully (source file is cut off)
    - TODO: Validate email HTML structure once email_html signature is confirmed
"""

import sys
import os
import importlib
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers — build a fake `shared` module so we can import the target module
# without needing the real shared.py on the path.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO = "test-output-repo"


def _make_fake_shared():
    """Return a MagicMock that looks enough like `shared` to satisfy imports."""
    fake = types.ModuleType("shared")
    fake.call_claude = MagicMock(return_value="# Doc\n---GAPS---\n1. Question one?")
    fake.get_repo_files = MagicMock(return_value={"main.py": "print('hello')", "README.md": "# Readme"})
    fake.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    fake.send_email = MagicMock()
    fake.email_html = MagicMock(return_value="<html>email</html>")
    fake.write_audit_entry = MagicMock()
    fake.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    fake.OUTPUT_REPO = FAKE_OUTPUT_REPO
    return fake


@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fake `shared` module before each test so the module under test
    always gets our stubs regardless of import order.
    """
    fs = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", fs)
    # Remove cached version of the module under test so each test gets a
    # fresh import that picks up the latest `shared` mock.
    sys.modules.pop("tool3_business_docs", None)
    yield fs


@pytest.fixture()
def module(fake_shared):
    """Import (or re-import) the module under test."""
    import importlib
    # Ensure .github/scripts is on the path so the import works.
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also handle running from within the scripts dir itself
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    import tool3_business_docs as m
    return m


# ---------------------------------------------------------------------------
# Frozen datetime helper
# ---------------------------------------------------------------------------

FROZEN_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FROZEN_DATE_STR = "2024-06-15"
FROZEN_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def frozen_now(monkeypatch, module):
    """Patch datetime.datetime inside the module under test."""
    fake_dt = MagicMock(wraps=datetime.datetime)
    fake_dt.utcnow.return_value = FROZEN_DT
    monkeypatch.setattr(module.datetime, "datetime", fake_dt)
    return fake_dt


# ===========================================================================
# Tests for generate_biz_doc()
# ===========================================================================


class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, module, fake_shared, frozen_now):
        """Claude returns a response with ---GAPS--- → both parts split correctly."""
        fake_shared.call_claude.return_value = (
            "# Solution Overview\nSome content here.\n"
            "---GAPS---\n"
            "1. What is the go-live date?\n2. Who is the sponsor?"
        )
        fake_shared.get_repo_files.return_value = {"app.py": "x = 1"}

        doc, gaps = module.generate_biz_doc("acme", "my-repo", "MyProject", "1.2.3", "https://run.url")

        assert "# Solution Overview" in doc
        assert "Some content here" in doc
        assert "1. What is the go-live date?" in gaps
        assert "2. Who is the sponsor?" in gaps
        # Delimiter itself must not leak into either part
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_happy_path_without_gaps_delimiter(self, module, fake_shared, frozen_now):
        """When Claude omits ---GAPS--- the gaps part gets the fallback message."""
        fake_shared.call_claude.return_value = "# Overview only, no delimiter here"

        doc, gaps = module.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run.url")

        assert "# Overview only" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_only_first_delimiter_is_used(self, module, fake_shared, frozen_now):
        """If ---GAPS--- appears more than once, only the first occurrence splits."""
        fake_shared.call_claude.return_value = (
            "Part A\n---GAPS---\nPart B\n---GAPS---\nPart C"
        )

        doc, gaps = module.generate_biz_doc("o", "r", "P", "0.1", "http://u")

        assert "Part A" in doc
        assert "Part B" in gaps
        assert "Part C" in gaps
        assert "---GAPS---" not in doc

    def test_get_repo_files_called_with_correct_extensions(self, module, fake_shared, frozen_now):
        """Ensure get_repo_files is invoked with the expected extension list and max_files."""
        module.generate_biz_doc("owner", "repo", "Proj", "0.0.1", "http://u")

        fake_shared.get_repo_files.assert_called_once()
        args, kwargs = fake_shared.get_repo_files.call_args
        extensions = args[2] if len(args) > 2 else kwargs.get("extensions", [])
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert kwargs.get("max_files", args[3] if len(args) > 3 else None) == 20

    def test_call_claude_receives_formatted_prompt(self, module, fake_shared, frozen_now):
        """The SYSTEM prompt passed to call_claude should contain project_name and version."""
        module.generate_biz_doc("owner", "repo", "InsuranceBot", "3.0.0", "http://u")

        prompt_arg = fake_shared.call_claude.call_args[0][0]
        assert "InsuranceBot" in prompt_arg
        assert "3.0.0" in prompt_arg
        assert FROZEN_DATE_STR in prompt_arg

    def test_call_claude_user_message_contains_files(self, module, fake_shared, frozen_now):
        """The user message to Claude should embed file contents."""
        fake_shared.get_repo_files.return_value = {"policy.py": "POLICY_CODE"}

        module.generate_biz_doc("owner", "repo", "Proj", "1.0", "http://u")

        user_msg = fake_shared.call_claude.call_args[0][1]
        assert "owner/repo" in user_msg
        assert "policy.py" in user_msg
        assert "POLICY_CODE" in user_msg

    def test_empty_repo_files(self, module, fake_shared, frozen_now):
        """An empty file dict should not crash; Claude still gets called."""
        fake_shared.get_repo_files.return_value = {}
        fake_shared.call_claude.return_value = "Doc\n---GAPS---\n1. Q?"

        doc, gaps = module.generate_biz_doc("o", "r", "P", "1.0", "http://u")

        assert doc  # Non-empty
        fake_shared.call_claude.assert_called_once()

    def test_call_claude_raises_propagates(self, module, fake_shared, frozen_now):
        """If call_claude raises, generate_biz_doc should propagate the exception."""
        fake_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            module.generate_biz_doc("o", "r", "P", "1.0", "http://u")

    def test_get_repo_files_raises_propagates(self, module, fake_shared, frozen_now):
        """If get_repo_files raises, generate_biz_doc should propagate the exception."""
        fake_shared.get_repo_files.side_effect = ConnectionError("GitHub down")

        with pytest.raises(ConnectionError, match="GitHub down"):
            module.generate_biz_doc("o", "r", "P", "1.0", "http://u")

    def test_file_content_truncated_at_3000_chars(self, module, fake_shared, frozen_now):
        """Files longer than 3000 chars are sliced before being sent to Claude."""
        long_content = "A" * 5000
        fake_shared.get_repo_files.return_value = {"big.py": long_content}

        module.generate_biz_doc("o", "r", "P", "1.0", "http://u")

        user_msg = fake_shared.call_claude.call_args[0][1]
        # The slice is `c[:3000]` so at most 3000 A's should appear
        assert "A" * 3000 in user_msg
        assert "A" * 3001 not in user_msg

    @pytest.mark.parametrize("project_name,version", [
        ("Generations II", "2.0.0"),
        ("Mainland VIP Hospital Network", "1.0.0"),
        ("Global Cashless Arrangement", "0.9.0"),
        ("Sun Life Health Products", "3.1.4"),
    ])
    def test_parametrized_project_names(self, module, fake_shared, frozen_now, project_name, version):
        """Various project names from synthetic data should be handled without error."""
        fake_shared.call_claude.return_value = f"# {project_name}\n---GAPS---\n1. Who owns this?"

        doc, gaps = module.generate_biz_doc("sunlife", "hk-repo", project_name, version, "http://u")

        assert project_name in doc
        assert "Who owns this?" in gaps

    def test_whitespace_stripped_from_parts(self, module, fake_shared, frozen_now):
        """Leading/trailing whitespace in either part should be stripped."""
        fake_shared.call_claude.return_value = "  \n  Doc content \n  ---GAPS---  \n  Gap content  \n  "

        doc, gaps = module.generate_biz_doc("o", "r", "P", "1.0", "http://u")

        assert doc == doc.strip()
        assert gaps == gaps.strip()


# ===========================================================================
# Tests for build_full_output()
# ===========================================================================


class TestBuildFullOutput:

    def test_returns_tuple_of_two_strings(self, module, frozen_now):
        result = module.build_full_output("Doc", "1. Q?", "owner", "repo", "MyProject", "1.0.0")
        assert isinstance(result, tuple)
        assert len(result) == 2
        full_md, gap_only_md = result
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, module, frozen_now):
        full_md, _ = module.build_full_output(
            "# Executive Summary\nContent.", "1. Q?", "owner", "repo", "Proj", "1.0"
        )
        assert "# Executive Summary" in full_md
        assert "Content." in full_md

    def test_full_md_contains_gaps(self, module, frozen_now):
        full_md, _ = module.build_full_output(
            "Doc", "1. What is the deadline?\n2. Who is sponsor?", "o", "r", "P", "1.0"
        )
        assert "What is the deadline?" in full_md
        assert "Who is sponsor?" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, module, frozen_now):
        full_md, _ = module.build_full_output("Doc", "1. Q?", "o", "r", "P", "1.0")
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_attribution_footer(self, module, frozen_now):
        full_md, _ = module.build_full_output("Doc", "Gaps", "owner", "repo", "P", "2.0")
        assert "AI Delivery Bot" in full_md
        assert "owner/repo" in full_md
        assert "2.0" in full_md

    def test_full_md_contains_frozen_datetime(self, module, frozen_now):
        full_md, _ = module.build_full_output("Doc", "Gaps", "o", "r", "P", "1.0")
        assert FROZEN_DATETIME_STR in full_md

    def test_gap_only_md_contains_project_and_version(self, module, frozen_now):
        _, gap_only_md = module.build_full_output("Doc", "1. Q?", "o", "r", "MyInsuranceProject", "4.2.1")
        assert "MyInsuranceProject" in gap_only_md
        assert "4.2.1" in gap_only_md

    def test_gap_only_md_contains_gaps(self, module, frozen_now):
        _, gap_only_md = module.build_full_output("Doc", "1. Q?\n2. Q2?", "o", "r", "P", "1.0")
        assert "1. Q?" in gap_only_md
        assert "2. Q2?" in gap_only_md

    def test_gap_only_md_contains_output_repo_link(self, module, frozen_now):
        _, gap_only_md = module.build_full_output("Doc", "Gaps", "o", "r", "P", "1.0")
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_gap_only_md_contains_frozen_datetime(self