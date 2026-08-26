"""
Test suite for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path (with/without ---GAPS--- delimiter), error propagation
    - build_full_output(): markdown structure, content inclusion, edge cases (empty gaps,
      special characters, long content)
    - __main__ block: environment variable handling, success path, exception/failure path
    - Helper integration points: correct arguments passed to shared helpers

Mocks used:
    - shared.call_claude          → unittest.mock.patch
    - shared.get_repo_files       → unittest.mock.patch
    - shared.write_output_file    → unittest.mock.patch
    - shared.send_email           → unittest.mock.patch
    - shared.email_html           → unittest.mock.patch
    - shared.write_audit_entry    → unittest.mock.patch
    - datetime.datetime.utcnow    → unittest.mock.patch (fixed timestamp)

TODOs:
    - TODO: Integration test against a real GitHub repository (requires PAT + network)
    - TODO: Test SYSTEM prompt template rendering with non-ASCII project names (needs locale fixture)
    - TODO: Verify Claude token-limit handling when files_str exceeds context window
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
# Fixtures & helpers
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"

SAMPLE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent"}}',
    "backend/tmp/customer_similarity_dict.json": '{"CUST00000001": ["CUST00006151"]}',
    "frontend/.chainlit/translations/ar-SA.json": '{"common": {"actions": {"cancel": "إلغاء"}}}',
}

CLAUDE_RESPONSE_WITH_GAPS = (
    "# Solution overview: MyProject\n**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft\n\n"
    "## Executive summary\nThis solves risk classification.\n"
    "---GAPS---\n"
    "1. What is the target go-live date?\n"
    "2. Who are the primary stakeholders?\n"
    "3. What is the retention policy?\n"
)

CLAUDE_RESPONSE_WITHOUT_GAPS = (
    "# Solution overview: MyProject\n"
    "Some content without the delimiter.\n"
)

OWNER = "acme-org"
REPO = "underwriting-tool"
PROJECT_NAME = "Underwriting Risk Classification"
VERSION = "1.2.3"
RUN_URL = "https://github.com/actions/runs/999"
DOC_URL = "https://github.com/output-owner/output-repo/blob/main/business-docs/acme-org-underwriting-tool/solution-overview-v1.2.3.md"


def make_shared_mock():
    """Return a mock module that replaces 'shared'."""
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value=CLAUDE_RESPONSE_WITH_GAPS)
    mod.get_repo_files = MagicMock(return_value=SAMPLE_FILES)
    mod.write_output_file = MagicMock(return_value=DOC_URL)
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html>email</html>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = "output-owner"
    mod.OUTPUT_REPO = "output-repo"
    return mod


@pytest.fixture(autouse=True)
def patch_shared(monkeypatch):
    """
    Inject a mock 'shared' module before importing tool3_business_docs so that
    the real network calls are never made.
    """
    shared_mock = make_shared_mock()
    monkeypatch.setitem(sys.modules, "shared", shared_mock)
    # Remove cached version of the module under test so imports are fresh per test
    sys.modules.pop("tool3_business_docs", None)
    yield shared_mock


@pytest.fixture()
def module(patch_shared):
    """Import (or re-import) the module under test with mocked shared."""
    import importlib
    # Ensure the scripts directory is on the path
    scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # Also try the directory relative to this test file
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, ".github", "scripts"),
        os.path.join(here),
    ]
    for c in candidates:
        if c not in sys.path:
            sys.path.insert(0, c)

    import tool3_business_docs as m
    return m


@pytest.fixture()
def fixed_utcnow():
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc()
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_returns_doc_and_gaps(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        doc, gaps = module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "What is the target go-live date?" in gaps
        assert "---GAPS---" not in doc
        assert "---GAPS---" not in gaps

    def test_get_repo_files_called_with_correct_args(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        patch_shared.get_repo_files.assert_called_once_with(
            OWNER, REPO,
            [".py", ".js", ".ts", ".tf", ".bicep", ".md", ".yaml"],
            max_files=20,
        )

    def test_call_claude_receives_formatted_prompt(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        args, kwargs = patch_shared.call_claude.call_args
        prompt_arg = args[0]
        user_arg = args[1]

        assert PROJECT_NAME in prompt_arg
        assert VERSION in prompt_arg
        assert FIXED_DATE_STR in prompt_arg
        assert f"Repo: {OWNER}/{REPO}" in user_arg

    def test_files_included_in_claude_user_message(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS
        patch_shared.get_repo_files.return_value = SAMPLE_FILES

        module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        _, kwargs = patch_shared.call_claude.call_args
        user_msg = patch_shared.call_claude.call_args[0][1]
        # At least one filename should appear in the user message
        assert any(fname in user_msg for fname in SAMPLE_FILES)

    def test_no_gaps_delimiter_returns_fallback(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITHOUT_GAPS

        doc, gaps = module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_gaps_stripped_of_whitespace(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = (
            "Doc content\n---GAPS---\n\n   1. Question one?   \n"
        )
        doc, gaps = module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_multiple_gaps_delimiters_splits_on_first(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = (
            "Doc part---GAPS---Gaps part---GAPS---Extra"
        )
        doc, gaps = module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        assert doc == "Doc part"
        assert "Gaps part---GAPS---Extra" in gaps

    def test_empty_files_dict_still_calls_claude(self, module, patch_shared, fixed_utcnow):
        patch_shared.get_repo_files.return_value = {}
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        doc, gaps = module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)
        patch_shared.call_claude.assert_called_once()
        assert doc  # non-empty

    def test_claude_exception_propagates(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.side_effect = RuntimeError("API failure")

        with pytest.raises(RuntimeError, match="API failure"):
            module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    def test_get_repo_files_exception_propagates(self, module, patch_shared, fixed_utcnow):
        patch_shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

    def test_file_content_truncated_at_3000_chars(self, module, patch_shared, fixed_utcnow):
        long_content = "x" * 5000
        patch_shared.get_repo_files.return_value = {"big_file.py": long_content}
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, VERSION, RUN_URL)

        user_msg = patch_shared.call_claude.call_args[0][1]
        # The file content in the message should be at most 3000 chars for this file
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_version_appears_in_prompt(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS
        custom_version = "9.8.7-rc1"

        module.generate_biz_doc(OWNER, REPO, PROJECT_NAME, custom_version, RUN_URL)

        prompt = patch_shared.call_claude.call_args[0][0]
        assert custom_version in prompt

    def test_project_name_with_spaces(self, module, patch_shared, fixed_utcnow):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS
        name = "Underwriting Risk Classification"

        doc, gaps = module.generate_biz_doc(OWNER, REPO, name, VERSION, RUN_URL)

        prompt = patch_shared.call_claude.call_args[0][0]
        assert name in prompt

    @pytest.mark.parametrize("owner,repo", [
        ("org-a", "repo-b"),
        ("UPPERCASE-ORG", "MixedCase-Repo"),
        ("org_underscore", "repo.with.dots"),
    ])
    def test_various_owner_repo_combinations(self, module, patch_shared, fixed_utcnow, owner, repo):
        patch_shared.call_claude.return_value = CLAUDE_RESPONSE_WITH_GAPS

        doc, gaps = module.generate_biz_doc(owner, repo, "ProjectX", "0.0.1", RUN_URL)

        user_msg = patch_shared.call_claude.call_args[0][1]
        assert f"Repo: {owner}/{repo}" in user_msg


# ---------------------------------------------------------------------------
# Tests for build_full_output()
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    @pytest.fixture()
    def sample_doc(self):
        return (
            "# Solution overview: Underwriting Risk Classification\n"
            "**Version:** 1.2.3 | **Date:** 2024-06-15 | **Status:** Draft\n\n"
            "## Executive summary\nSolves underwriting risk.\n"
        )

    @pytest.fixture()
    def sample_gaps(self):
        return (
            "1. What is the target go-live date?\n"
            "2. Who are the primary stakeholders?\n"
            "3. What is the data retention policy?\n"
        )

    def test_full_md_contains_doc_content(self, module, fixed_utcnow, sample_doc, sample_gaps):
        full_md, _ = module.build_full_output(
            sample_doc, sample_gaps, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "Solution overview" in full_md

    def test_full_md_contains_gaps(self, module, fixed_utcnow, sample_doc, sample_gaps):
        full_md, _ = module.build_full_output(
            sample_doc, sample_gaps, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "What is the target go-live date?" in full_md

    def test_full_md_contains_gap_questionnaire_section(self, module, fixed_utcnow, sample_doc, sample_gaps):
        full_md, _ = module.build_full_output(
            sample_doc, sample_gaps, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert "Gap Questionnaire" in full_md

    def test_full_md_contains_source_attribution(self, module, fixed_utcnow, sample_doc, sample_gaps):
        full_md, _ = module.build_full_output(
            sample_doc, sample_gaps, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert f"{OWNER}/{REPO}" in full_md
        assert f"v{VERSION}" in full_md

    def test_full_md_contains_timestamp(self, module, fixed_utcnow, sample_doc, sample_gaps):
        full_md, _ = module.build_full_output(
            sample_doc, sample_gaps, OWNER, REPO, PROJECT_NAME, VERSION
        )
        assert FIXED_DATETIME_STR in full_md

    def test_gap_only_md_contains_project_name(self, module,