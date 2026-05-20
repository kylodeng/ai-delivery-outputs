"""
Tests for tool3_business_docs.py

What is tested:
    - generate_biz_doc(): happy path, missing ---GAPS--- delimiter, empty files, Claude errors
    - build_full_output(): happy path, content assertions, formatting, edge cases
    - __main__ block behaviour (env-var driven): success path, exception/failure path
    - gap_count calculation logic

Mocks used:
    - shared.call_claude          (patched via unittest.mock.patch)
    - shared.get_repo_files       (patched)
    - shared.write_output_file    (patched)
    - shared.send_email           (patched)
    - shared.email_html           (patched)
    - shared.write_audit_entry    (patched)
    - datetime.datetime.utcnow    (patched for deterministic timestamps)

TODOs:
    - TODO: Integration test that validates Claude prompt formatting with a live model
    - TODO: Test write_output_file path construction once OUTPUT_REPO_OWNER/OUTPUT_REPO are configurable
    - TODO: Test __main__ error email body truncation (source code appears to have a truncated string)
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
# Helpers to import the module under test with stubbed `shared` dependency
# ---------------------------------------------------------------------------

FAKE_SHARED_ATTRS = dict(
    call_claude=MagicMock(),
    get_repo_files=MagicMock(),
    write_output_file=MagicMock(),
    send_email=MagicMock(),
    email_html=MagicMock(),
    write_audit_entry=MagicMock(),
    OUTPUT_REPO_OWNER="test-owner",
    OUTPUT_REPO="test-repo",
)


def _make_shared_module():
    """Return a fresh fake `shared` module."""
    mod = types.ModuleType("shared")
    for k, v in FAKE_SHARED_ATTRS.items():
        setattr(mod, k, v if not callable(v) else MagicMock())
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO = "test-repo"
    return mod


@pytest.fixture(autouse=True)
def fresh_shared(monkeypatch):
    """
    Replace `shared` in sys.modules with a fresh stub before each test,
    then re-import the module under test so it picks up the stub.
    """
    shared_stub = _make_shared_module()
    monkeypatch.setitem(sys.modules, "shared", shared_stub)

    # Remove cached module so reimport picks up the stub
    for key in list(sys.modules.keys()):
        if "tool3_business_docs" in key:
            del sys.modules[key]

    yield shared_stub


@pytest.fixture()
def module(fresh_shared):
    """Import tool3_business_docs with the stubbed shared module."""
    script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
    script_dir = os.path.abspath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Fallback: if file isn't found at relative path, try direct import
    import importlib.util

    candidate = os.path.join(script_dir, "tool3_business_docs.py")
    if not os.path.exists(candidate):
        # Try same directory as this test file
        candidate = os.path.join(os.path.dirname(__file__), "tool3_business_docs.py")

    spec = importlib.util.spec_from_file_location("tool3_business_docs", candidate)
    mod = importlib.util.module_from_spec(spec)
    # Ensure shared stub is visible during exec
    sys.modules["shared"] = fresh_shared
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixed datetime for deterministic output
# ---------------------------------------------------------------------------

FIXED_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FIXED_DATE_STR = "2024-06-15"
FIXED_DATETIME_STR = "2024-06-15 12:00 UTC"


@pytest.fixture()
def mock_utcnow():
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FIXED_DT
        mock_dt.utcnow.return_value.strftime = FIXED_DT.strftime
        # Make strftime work on the mock
        mock_dt.side_effect = lambda *a, **kw: datetime.datetime(*a, **kw)
        yield mock_dt


# ---------------------------------------------------------------------------
# Synthetic test data
# ---------------------------------------------------------------------------

SAMPLE_FILES = {
    "backend/model_card.json": '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}',
    "backend/prompts/assessment_criterias.json": '{"deep": {"finance": "You are a finance assessment agent"}}',
    "README.md": "# Insurance Underwriting Platform\nThis repo contains the risk classification system.",
}

SAMPLE_CLAUDE_RESPONSE_WITH_GAPS = """\
# Solution overview: MyProject
**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates risk classification for insurance underwriting.

## Business context
**Problem statement:** Manual underwriting is slow and inconsistent.
**Affected users / teams:** Underwriters, Risk team
**Current pain points:** [TODO: what was the manual/legacy process?]

## What this solution does
Classifies insurance applicants by risk using a machine learning model.

## What it does NOT do (out of scope)
- Does not handle claims processing
- Does not integrate with legacy billing systems
- Does not provide real-time streaming predictions

## Data handled
| Data type | Sensitivity | Retention | Storage location |
| Age | Medium | 7 years | S3 |

## Stakeholders
| Role | Name | Responsibility |
| Solution owner | [TODO] | Accountable for delivery and budget |

## Risks and dependencies
- Single cloud region deployment

## Success metrics
[TODO: How will you measure if this solution is working?]

## Go-live and milestones
[TODO: Target date and key milestones]
---GAPS---
1. What is the target go-live date?
2. Who is the solution owner?
3. What were the pain points of the legacy process?
4. What is the data retention policy?
5. Who are the key business users?
"""

SAMPLE_CLAUDE_RESPONSE_NO_GAPS = """\
# Solution overview: MyProject
**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates risk classification for insurance underwriting.
"""


# ===========================================================================
# Tests for generate_biz_doc
# ===========================================================================

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, module, fresh_shared, monkeypatch):
        """Claude returns a response containing ---GAPS--- delimiter."""
        fresh_shared.get_repo_files.return_value = SAMPLE_FILES
        fresh_shared.call_claude.return_value = SAMPLE_CLAUDE_RESPONSE_WITH_GAPS

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR

            doc, gaps = module.generate_biz_doc(
                "acme-org", "underwriting-api", "Underwriting Risk", "1.0.0", "https://github.com/run/1"
            )

        assert "# Solution overview" in doc
        assert "Executive summary" in doc
        assert "What this solution does" in doc
        assert "---GAPS---" not in doc  # delimiter should be stripped
        assert "1. What is the target go-live date?" in gaps
        assert "2. Who is the solution owner?" in gaps

    def test_response_without_gaps_delimiter(self, module, fresh_shared):
        """When Claude omits ---GAPS--- the fallback message is used for gaps."""
        fresh_shared.get_repo_files.return_value = SAMPLE_FILES
        fresh_shared.call_claude.return_value = SAMPLE_CLAUDE_RESPONSE_NO_GAPS

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR

            doc, gaps = module.generate_biz_doc(
                "acme-org", "underwriting-api", "Underwriting Risk", "1.0.0", "https://github.com"
            )

        assert "Solution overview" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_files_content_is_truncated_in_prompt(self, module, fresh_shared):
        """Files longer than 3000 chars should be truncated when building the prompt."""
        long_content = "x" * 5000
        fresh_shared.get_repo_files.return_value = {"big_file.py": long_content}
        fresh_shared.call_claude.return_value = "doc\n---GAPS---\ngaps"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR
            module.generate_biz_doc("o", "r", "p", "0.1.0", "url")

        # Inspect what was passed to call_claude
        call_args = fresh_shared.call_claude.call_args
        user_content = call_args[0][1]  # second positional arg
        assert "x" * 3001 not in user_content  # truncated at 3000

    def test_empty_repo_files(self, module, fresh_shared):
        """Empty files dict still produces output."""
        fresh_shared.get_repo_files.return_value = {}
        fresh_shared.call_claude.return_value = "doc\n---GAPS---\n1. Question one?"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR

            doc, gaps = module.generate_biz_doc("o", "r", "p", "0.1.0", "url")

        assert doc == "doc"
        assert "1. Question one?" in gaps

    def test_get_repo_files_called_with_correct_extensions(self, module, fresh_shared):
        """get_repo_files should be called with expected extension list."""
        fresh_shared.get_repo_files.return_value = {}
        fresh_shared.call_claude.return_value = "doc\n---GAPS---\nq"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR
            module.generate_biz_doc("o", "r", "p", "1.0.0", "url")

        call_kwargs = fresh_shared.get_repo_files.call_args
        extensions_arg = call_kwargs[0][2] if len(call_kwargs[0]) > 2 else call_kwargs[1].get("extensions", call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None)
        # Verify some expected extensions are present
        _, positional_args, _ = fresh_shared.get_repo_files.call_args if hasattr(fresh_shared.get_repo_files.call_args, 'args') else (None, fresh_shared.get_repo_files.call_args[0], fresh_shared.get_repo_files.call_args[1])
        assert ".py" in positional_args[2]
        assert ".md" in positional_args[2]
        assert ".tf" in positional_args[2]

    def test_call_claude_propagates_exception(self, module, fresh_shared):
        """If call_claude raises, generate_biz_doc should propagate the exception."""
        fresh_shared.get_repo_files.return_value = SAMPLE_FILES
        fresh_shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            with patch("datetime.datetime") as mock_dt:
                mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR
                module.generate_biz_doc("o", "r", "p", "1.0.0", "url")

    def test_multiple_gaps_delimiters_only_first_split_used(self, module, fresh_shared):
        """If response has multiple ---GAPS--- only split on the first one."""
        fresh_shared.get_repo_files.return_value = {}
        response = "doc content\n---GAPS---\nquestion 1\n---GAPS---\nextra stuff"
        fresh_shared.call_claude.return_value = response

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR
            doc, gaps = module.generate_biz_doc("o", "r", "p", "1.0.0", "url")

        assert doc == "doc content"
        assert "question 1" in gaps
        assert "extra stuff" in gaps  # everything after first delimiter is gaps

    def test_prompt_contains_project_name_and_version(self, module, fresh_shared):
        """The system prompt passed to Claude should contain project_name and version."""
        fresh_shared.get_repo_files.return_value = {}
        fresh_shared.call_claude.return_value = "doc\n---GAPS---\nq"

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR
            module.generate_biz_doc("o", "r", "InsuranceApp", "2.3.1", "url")

        system_prompt = fresh_shared.call_claude.call_args[0][0]
        assert "InsuranceApp" in system_prompt
        assert "2.3.1" in system_prompt

    def test_doc_and_gaps_are_stripped(self, module, fresh_shared):
        """Leading/trailing whitespace should be stripped from doc and gaps."""
        fresh_shared.get_repo_files.return_value = {}
        fresh_shared.call_claude.return_value = "  doc with spaces  \n---GAPS---\n  gaps with spaces  "

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: FIXED_DATE_STR
            doc, gaps = module.generate_biz_doc("o", "r", "p", "1.0.0", "url")

        assert doc == "doc with spaces"
        assert gaps == "gaps with spaces"


# ===========================================================================
# Tests for build_full_output
# ===========================================================================

class TestBuildFullOutput:

    def _call(self, module, doc="## Doc", gaps="1. Question?",
              owner="acme", repo="underwriting", project="MyProject", version="1.0.0"):
        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value.strftime = lambda fmt: (
                FIXED_DATE_STR if fmt == "%Y-%m-%d" else FIXED_DATETIME_STR
            )
            return module.build_full_output(doc, gaps, owner, repo, project, version)

    def test_returns_two_strings(self, module):
        full_md, gap_only_md = self._call(module)
        assert isinstance(full_md, str)
        assert isinstance(gap_only_md, str)

    def test_full_md_contains_doc_content(self, module):
        full_md, _ = self._call(module, doc="## Executive Summary\n