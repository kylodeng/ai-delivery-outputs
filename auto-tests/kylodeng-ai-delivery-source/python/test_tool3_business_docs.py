"""
Test module for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, delimiter splitting, missing delimiter fallback
    - build_full_output(): content structure, markdown sections, edge cases (empty strings,
      long strings, special characters in project names/versions)
    - __main__ block behaviour via subprocess / monkeypatching (env-var driven)

Mocks used:
    - shared.call_claude          → prevents real Anthropic API calls
    - shared.get_repo_files       → prevents real GitHub API calls
    - shared.write_output_file    → prevents real GitHub output-repo writes
    - shared.send_email           → prevents real SES / SMTP calls
    - shared.email_html           → pure helper, stubbed for isolation
    - shared.write_audit_entry    → prevents real audit-log writes
    - datetime.datetime.utcnow    → frozen timestamps for deterministic assertions

TODOs:
    - TODO: test the truncated `__main__` error-path send_email call once the source
            file is complete (source is cut off mid-string at `email_html("Busin`)
    - TODO: integration test with real Claude when ANTHROPIC_API_KEY is available in CI
    - TODO: test write_output_file path slugs once slug format is confirmed stable
"""

import importlib
import os
import sys
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers: build a minimal fake `shared` module so we can import the SUT
# without any real dependencies installed.
# ---------------------------------------------------------------------------

FAKE_OUTPUT_REPO_OWNER = "test-owner"
FAKE_OUTPUT_REPO       = "test-output-repo"


def _make_fake_shared():
    """Return a fake `shared` module with all symbols the SUT needs."""
    mod = types.ModuleType("shared")
    mod.call_claude       = MagicMock(return_value="doc content\n---GAPS---\n1. A gap question?")
    mod.get_repo_files    = MagicMock(return_value={"README.md": "# Hello world"})
    mod.write_output_file = MagicMock(return_value="https://github.com/test-owner/test-output-repo/blob/main/file.md")
    mod.send_email        = MagicMock()
    mod.email_html        = MagicMock(return_value="<html>mock</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = FAKE_OUTPUT_REPO_OWNER
    mod.OUTPUT_REPO       = FAKE_OUTPUT_REPO
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fake_shared(monkeypatch):
    """
    Inject a fresh fake `shared` module before every test and reload the SUT
    so each test starts with clean mocks.
    """
    mod = _make_fake_shared()
    monkeypatch.setitem(sys.modules, "shared", mod)
    # Remove cached SUT so the fresh `shared` is picked up on import
    sys.modules.pop("tool3_business_docs", None)
    yield mod


@pytest.fixture()
def sut():
    """Import (or re-import) the module under test after fake_shared is set up."""
    import importlib.util, pathlib
    script_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool3_business_docs.py"
    # Allow running from repo root or from the scripts directory itself
    candidates = [
        pathlib.Path(__file__).parent / ".github" / "scripts" / "tool3_business_docs.py",
        pathlib.Path(__file__).parent / "tool3_business_docs.py",
    ]
    found = next((p for p in candidates if p.exists()), None)
    if found is None:
        pytest.skip("tool3_business_docs.py not found on expected paths")

    spec = importlib.util.spec_from_file_location("tool3_business_docs", found)
    module = importlib.util.module_from_spec(spec)
    # Ensure the fake shared module is resolvable during exec
    sys.modules["tool3_business_docs"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def frozen_now(monkeypatch):
    """Freeze datetime.datetime.utcnow to a known value."""
    fixed = datetime.datetime(2024, 6, 15, 12, 0, 0)

    class _FakeDatetime(datetime.datetime):
        @classmethod
        def utcnow(cls):
            return fixed

    monkeypatch.setattr(datetime, "datetime", _FakeDatetime)
    return fixed


# ---------------------------------------------------------------------------
# Tests: generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_splits_on_delimiter(self, sut, fake_shared):
        """Claude returns both parts separated by ---GAPS---; each part is returned."""
        fake_shared.call_claude.return_value = (
            "# Solution overview\nSome content\n---GAPS---\n1. What is the go-live date?"
        )
        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run")

        assert "# Solution overview" in doc
        assert "Some content" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the go-live date?" in gaps
        assert "---GAPS---" not in gaps

    def test_no_delimiter_falls_back_gracefully(self, sut, fake_shared):
        """When Claude omits ---GAPS---, doc gets the full response; gaps gets fallback text."""
        fake_shared.call_claude.return_value = "Just a document with no delimiter."
        doc, gaps = sut.generate_biz_doc("acme", "my-repo", "MyProject", "1.0.0", "https://run")

        assert "Just a document with no delimiter." in doc
        assert "could not extract" in gaps.lower() or "review" in gaps.lower()

    def test_calls_get_repo_files_with_correct_extensions(self, sut, fake_shared):
        sut.generate_biz_doc("org", "repo", "Proj", "2.0", "https://run")
        call_args = fake_shared.get_repo_files.call_args
        _, kwargs  = call_args if call_args.kwargs else (call_args.args, {})
        # Positional args: owner, repo, extensions list, max_files
        args = call_args.args
        assert args[0] == "org"
        assert args[1] == "repo"
        extensions = args[2]
        for ext in [".py", ".tf", ".md", ".yaml"]:
            assert ext in extensions

    def test_max_files_is_twenty(self, sut, fake_shared):
        sut.generate_biz_doc("org", "repo", "Proj", "2.0", "https://run")
        args = fake_shared.get_repo_files.call_args.args
        assert args[3] == 20

    def test_call_claude_receives_formatted_prompt(self, sut, fake_shared):
        sut.generate_biz_doc("org", "repo", "AwesomeProject", "3.1.4", "https://run")
        prompt_arg = fake_shared.call_claude.call_args.args[0]
        assert "AwesomeProject" in prompt_arg
        assert "3.1.4" in prompt_arg

    def test_call_claude_receives_repo_in_user_message(self, sut, fake_shared):
        sut.generate_biz_doc("myorg", "myrepo", "Proj", "1.0", "https://run")
        user_msg = fake_shared.call_claude.call_args.args[1]
        assert "myorg/myrepo" in user_msg

    def test_file_content_truncated_to_3000_chars(self, sut, fake_shared):
        long_content = "x" * 10_000
        fake_shared.get_repo_files.return_value = {"big_file.py": long_content}
        sut.generate_biz_doc("o", "r", "P", "1", "https://run")
        user_msg = fake_shared.call_claude.call_args.args[1]
        # The truncated slice should appear in the message
        assert "x" * 3000 in user_msg
        assert "x" * 3001 not in user_msg

    def test_multiple_files_all_included_in_prompt(self, sut, fake_shared):
        fake_shared.get_repo_files.return_value = {
            "main.py": "print('hello')",
            "infra.tf": 'resource "aws_s3_bucket" {}',
        }
        sut.generate_biz_doc("o", "r", "P", "1", "https://run")
        user_msg = fake_shared.call_claude.call_args.args[1]
        assert "main.py" in user_msg
        assert "infra.tf" in user_msg

    def test_empty_repo_files(self, sut, fake_shared):
        """No files found should not crash; prompt still sent."""
        fake_shared.get_repo_files.return_value = {}
        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1", "https://run")
        assert isinstance(doc, str)
        assert isinstance(gaps, str)

    def test_delimiter_only_splits_on_first_occurrence(self, sut, fake_shared):
        """If ---GAPS--- appears twice, only the first split is used."""
        fake_shared.call_claude.return_value = (
            "doc part\n---GAPS---\ngaps part\n---GAPS---\nextra stuff"
        )
        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1", "https://run")
        assert "doc part" in doc
        assert "gaps part" in gaps
        assert "extra stuff" in gaps          # everything after first delimiter stays in gaps
        assert "---GAPS---" not in doc

    def test_stripped_whitespace_on_doc_and_gaps(self, sut, fake_shared):
        fake_shared.call_claude.return_value = "   doc   \n---GAPS---\n   gaps   "
        doc, gaps = sut.generate_biz_doc("o", "r", "P", "1", "https://run")
        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_date_injected_into_prompt(self, sut, fake_shared, frozen_now):
        sut.generate_biz_doc("o", "r", "P", "1", "https://run")
        prompt_arg = fake_shared.call_claude.call_args.args[0]
        assert "2024-06-15" in prompt_arg

    def test_special_chars_in_project_name(self, sut, fake_shared):
        """Project names with spaces and slashes should not crash."""
        doc, gaps = sut.generate_biz_doc("o", "r", "My Project / v2", "1.0", "https://run")
        assert isinstance(doc, str)

    @pytest.mark.parametrize("version", ["0.0.1", "1.0.0", "10.20.30", "v2024.06.15"])
    def test_various_version_strings(self, sut, fake_shared, version):
        doc, gaps = sut.generate_biz_doc("o", "r", "P", version, "https://run")
        prompt_arg = fake_shared.call_claude.call_args.args[0]
        assert version in prompt_arg


# ---------------------------------------------------------------------------
# Tests: build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    def test_returns_two_strings(self, sut):
        full_md, gap_only_md = sut.build_full_output(
            "## Doc", "1. A question?", "owner", "repo", "MyProject", "1.0.0"
        )
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, sut):
        full_md, _ = sut.build_full_output(
            "## Solution overview", "1. Question?", "o", "r", "P", "1.0"
        )
        assert "## Solution overview" in full_md

    def test_full_md_contains_gap_questionnaire_heading(self, sut):
        full_md, _ = sut.build_full_output("doc", "1. Q?", "o", "r", "P", "1.0")
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_content(self, sut):
        full_md, _ = sut.build_full_output("doc", "1. What is scope?", "o", "r", "P", "1.0")
        assert "1. What is scope?" in full_md

    def test_full_md_contains_source_attribution(self, sut):
        full_md, _ = sut.build_full_output("doc", "gaps", "myorg", "myrepo", "P", "2.0")
        assert "myorg/myrepo" in full_md
        assert "2.0" in full_md

    def test_gap_only_md_contains_project_name_and_version(self, sut):
        _, gap_only_md = sut.build_full_output("doc", "1. Q?", "o", "r", "CoolProject", "3.5.0")
        assert "CoolProject" in gap_only_md
        assert "3.5.0" in gap_only_md

    def test_gap_only_md_contains_questions(self, sut):
        _, gap_only_md = sut.build_full_output("doc", "1. Who are the stakeholders?", "o", "r", "P", "1.0")
        assert "1. Who are the stakeholders?" in gap_only_md

    def test_gap_only_md_links_to_output_repo(self, sut):
        _, gap_only_md = sut.build_full_output("doc", "gaps", "o", "r", "P", "1.0")
        assert FAKE_OUTPUT_REPO_OWNER in gap_only_md
        assert FAKE_OUTPUT_REPO in gap_only_md

    def test_full_md_contains_ai_delivery_bot_attribution(self, sut):
        full_md, _ = sut.build_full_output("doc", "gaps", "o", "r", "P", "1.0")
        assert "AI Delivery Bot" in full_md

    def test_gap_only_md_contains_time_estimate(self, sut):
        _, gap_only_md = sut.build_full_output("doc", "gaps", "o", "r", "P", "1.0")
        assert "10-15 minutes" in gap_only_md

    def test_frozen_timestamp_appears_in_outputs(self, sut, frozen_now):
        full_md, gap_only_md = sut.build_full_output("doc", "gaps", "o", "r", "P", "1.0")
        assert "2024-06-15" in full_md
        assert "2024-06-15" in gap_only_md

    def test_empty_doc_string(self, sut):
        full_md, gap_only_md = sut.build_full_output("", "1. Q?", "o", "r", "P", "1.0")
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test