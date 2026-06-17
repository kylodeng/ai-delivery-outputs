"""
Tests for tool3_business_docs.py

What is tested:
- generate_biz_doc(): happy path with ---GAPS--- delimiter, missing delimiter fallback
- build_full_output(): full markdown assembly, gap-only markdown assembly, content checks
- Main block logic (via subprocess or direct function calls where possible)
- Edge cases: empty gaps, None inputs, delimiter appearing multiple times

Mocks used:
- shared.call_claude (patched to return synthetic Claude responses)
- shared.get_repo_files (patched to return synthetic file dicts)
- shared.write_output_file (patched, returns a fake URL)
- shared.send_email (patched, no-op)
- shared.email_html (patched, returns dummy HTML)
- shared.write_audit_entry (patched, no-op)
- datetime.datetime.utcnow (patched for deterministic timestamps)
- os.environ (patched per test)

TODOs:
- TODO: Integration test that calls a real Claude endpoint (requires API key and network)
- TODO: Test the __main__ block end-to-end via subprocess with mocked environment
- TODO: Test write_output_file interaction when GitHub API returns non-200 responses
"""

import sys
import os
import importlib
import datetime
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to build the module under test with shared dependencies mocked
# ---------------------------------------------------------------------------

FAKE_NOW_DT = datetime.datetime(2024, 6, 15, 12, 0, 0)
FAKE_NOW_DATE = "2024-06-15"
FAKE_NOW_FULL = "2024-06-15 12:00 UTC"
FAKE_DOC_URL = "https://github.com/output-owner/output-repo/blob/main/business-docs/owner-repo/solution-overview-v1.0.0.md"

SYNTHETIC_CLAUDE_RESPONSE_WITH_GAPS = """# Solution overview: MyProject
**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates document processing for insurance products.

## Business context
**Problem statement:** Manual document handling was slow and error-prone.
**Affected users / teams:** Operations team
**Current pain points:** [TODO: what was the manual/legacy process?]

## What this solution does
Reads insurance PDF annotations and extracts structured data automatically.

## What it does NOT do (out of scope)
- Does not handle claims processing
- Does not integrate with legacy mainframe
- Does not support real-time updates

## Data handled
| Data type | Sensitivity | Retention | Storage location |
| PDF annotations | Medium | 7 years | S3 bucket |

## Stakeholders
| Role | Name | Responsibility |
| Solution owner | [TODO] | Accountable |
| Business sponsor | [TODO] | Strategic direction |
| Tech lead | [TODO] | Technical decisions |
| Key users | [TODO] | Day-to-day usage |

## Risks and dependencies
- Single cloud region deployment detected — no DR configuration found

## Success metrics
[TODO: How will you measure if this solution is working?]

## Go-live and milestones
[TODO: Target date and key milestones]

---GAPS---

1. What is the target go-live date and are there any hard deadlines?
2. Who is the solution owner accountable for budget?
3. What was the manual process before this solution?
4. How long should PDF annotation data be retained?
5. Are there any regulatory compliance requirements?"""

SYNTHETIC_CLAUDE_RESPONSE_WITHOUT_GAPS = """# Solution overview: MyProject
**Version:** 1.0.0 | **Date:** 2024-06-15 | **Status:** Draft

## Executive summary
This solution automates document processing.

## What this solution does
Reads and processes documents automatically."""

SYNTHETIC_FILES = {
    "src/main.py": "import os\ndef main(): pass",
    "README.md": "# MyProject\nThis project processes insurance documents.",
    "infra/main.tf": 'resource "aws_s3_bucket" "docs" { bucket = "my-docs" }',
}


def _make_shared_mock():
    """Return a mock module standing in for `shared`."""
    shared = types.ModuleType("shared")
    shared.call_claude = MagicMock(return_value=SYNTHETIC_CLAUDE_RESPONSE_WITH_GAPS)
    shared.get_repo_files = MagicMock(return_value=SYNTHETIC_FILES)
    shared.write_output_file = MagicMock(return_value=FAKE_DOC_URL)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html>stub</html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "output-owner"
    shared.OUTPUT_REPO = "output-repo"
    return shared


def _import_module(shared_mock=None):
    """Import (or re-import) tool3_business_docs with the given shared mock."""
    if shared_mock is None:
        shared_mock = _make_shared_mock()

    # Inject our mock into sys.modules before importing
    sys.modules["shared"] = shared_mock

    module_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        ".github",
        "scripts",
        "tool3_business_docs.py",
    )
    module_path = os.path.normpath(module_path)

    spec = importlib.util.spec_from_file_location("tool3_business_docs", module_path)
    mod = importlib.util.module_from_spec(spec)
    # Prevent __main__ block from running during import
    with patch.object(spec.loader, "exec_module", wraps=spec.loader.exec_module):
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
    return mod, shared_mock


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def module_and_shared():
    """Provide the imported module and its shared mock."""
    mod, shared = _import_module()
    yield mod, shared
    # Cleanup
    sys.modules.pop("shared", None)
    sys.modules.pop("tool3_business_docs", None)


@pytest.fixture()
def fixed_utcnow():
    """Patch datetime.datetime.utcnow to return a fixed value."""
    with patch("datetime.datetime") as mock_dt:
        mock_dt.utcnow.return_value = FAKE_NOW_DT
        mock_dt.strftime = datetime.datetime.strftime
        # Make strftime work on the mock instance
        mock_dt.utcnow.return_value.strftime = FAKE_NOW_DT.strftime
        yield mock_dt


# ---------------------------------------------------------------------------
# Tests for generate_biz_doc
# ---------------------------------------------------------------------------

class TestGenerateBizDoc:

    def test_happy_path_with_gaps_delimiter(self, module_and_shared):
        """Claude response contains ---GAPS--- → doc and gaps split correctly."""
        mod, shared = module_and_shared
        shared.call_claude.return_value = SYNTHETIC_CLAUDE_RESPONSE_WITH_GAPS

        doc, gaps = mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run-url")

        assert "# Solution overview: MyProject" in doc
        assert "---GAPS---" not in doc
        assert "1. What is the target go-live date" in gaps
        assert "---GAPS---" not in gaps

    def test_happy_path_without_gaps_delimiter(self, module_and_shared):
        """Claude response missing ---GAPS--- → fallback message in gaps."""
        mod, shared = module_and_shared
        shared.call_claude.return_value = SYNTHETIC_CLAUDE_RESPONSE_WITHOUT_GAPS

        doc, gaps = mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run-url")

        assert "# Solution overview: MyProject" in doc
        assert "Claude could not extract gap questions" in gaps

    def test_calls_get_repo_files_with_correct_extensions(self, module_and_shared):
        """get_repo_files called with expected file extensions and max_files."""
        mod, shared = module_and_shared

        mod.generate_biz_doc("owner", "repo", "MyProject", "1.0.0", "https://run-url")

        shared.get_repo_files.assert_called_once()
        call_args = shared.get_repo_files.call_args
        assert call_args[0][0] == "owner"
        assert call_args[0][1] == "repo"
        extensions = call_args[0][2]
        assert ".py" in extensions
        assert ".tf" in extensions
        assert ".md" in extensions
        assert call_args[1].get("max_files") == 20 or call_args[0][3] == 20

    def test_calls_call_claude_with_prompt_containing_project_name(self, module_and_shared):
        """call_claude receives a prompt containing project_name and version."""
        mod, shared = module_and_shared

        mod.generate_biz_doc("owner", "repo", "InsurancePortal", "2.3.1", "https://run-url")

        shared.call_claude.assert_called_once()
        prompt_arg = shared.call_claude.call_args[0][0]
        assert "InsurancePortal" in prompt_arg
        assert "2.3.1" in prompt_arg

    def test_call_claude_user_message_contains_repo_and_files(self, module_and_shared):
        """call_claude user message contains owner/repo and file contents."""
        mod, shared = module_and_shared

        mod.generate_biz_doc("acme", "portal", "Portal", "1.0.0", "https://run-url")

        user_msg = shared.call_claude.call_args[0][1]
        assert "acme/portal" in user_msg
        # File content from synthetic files should appear
        assert "main.py" in user_msg or "README.md" in user_msg

    def test_doc_is_stripped(self, module_and_shared):
        """Returned doc should have no leading/trailing whitespace."""
        mod, shared = module_and_shared
        shared.call_claude.return_value = "  \n  doc content  \n  ---GAPS---\n  gaps  \n  "

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "url")

        assert doc == doc.strip()
        assert gaps == gaps.strip()

    def test_gaps_is_stripped(self, module_and_shared):
        """Returned gaps should have no leading/trailing whitespace."""
        mod, shared = module_and_shared
        shared.call_claude.return_value = "\ndoc\n---GAPS---\n  question 1\n  "

        _, gaps = mod.generate_biz_doc("o", "r", "P", "1", "url")

        assert gaps == gaps.strip()

    def test_multiple_gaps_delimiter_splits_on_first(self, module_and_shared):
        """If ---GAPS--- appears more than once, split on first occurrence."""
        mod, shared = module_and_shared
        shared.call_claude.return_value = "doc part---GAPS---gaps part 1---GAPS---gaps part 2"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "url")

        assert doc == "doc part"
        assert "gaps part 1---GAPS---gaps part 2" in gaps

    def test_empty_files_dict(self, module_and_shared):
        """Empty files dict → call_claude still called, empty files string."""
        mod, shared = module_and_shared
        shared.get_repo_files.return_value = {}
        shared.call_claude.return_value = "doc---GAPS---gaps"

        doc, gaps = mod.generate_biz_doc("o", "r", "P", "1", "url")

        assert doc == "doc"
        assert gaps == "gaps"

    def test_large_file_content_truncated_in_prompt(self, module_and_shared):
        """Files with content > 3000 chars are truncated to 3000 in the prompt."""
        mod, shared = module_and_shared
        large_content = "x" * 10000
        shared.get_repo_files.return_value = {"big_file.py": large_content}
        shared.call_claude.return_value = "doc---GAPS---gaps"

        mod.generate_biz_doc("o", "r", "P", "1", "url")

        user_msg = shared.call_claude.call_args[0][1]
        # The file block should contain at most 3000 x chars
        assert "x" * 3001 not in user_msg
        assert "x" * 3000 in user_msg

    def test_prompt_contains_current_date(self, module_and_shared):
        """Prompt contains today's UTC date."""
        mod, shared = module_and_shared

        with patch("datetime.datetime") as mock_dt:
            mock_dt.utcnow.return_value = FAKE_NOW_DT
            mock_dt.utcnow.return_value.strftime = FAKE_NOW_DT.strftime

            mod.generate_biz_doc("o", "r", "P", "1.0.0", "url")

        prompt_arg = shared.call_claude.call_args[0][0]
        assert FAKE_NOW_DATE in prompt_arg

    def test_call_claude_exception_propagates(self, module_and_shared):
        """Exceptions from call_claude bubble up to caller."""
        mod, shared = module_and_shared
        shared.call_claude.side_effect = RuntimeError("API timeout")

        with pytest.raises(RuntimeError, match="API timeout"):
            mod.generate_biz_doc("o", "r", "P", "1", "url")

    def test_get_repo_files_exception_propagates(self, module_and_shared):
        """Exceptions from get_repo_files bubble up to caller."""
        mod, shared = module_and_shared
        shared.get_repo_files.side_effect = ConnectionError("GitHub unreachable")

        with pytest.raises(ConnectionError, match="GitHub unreachable"):
            mod.generate_biz_doc("o", "r", "P", "1", "url")


# ---------------------------------------------------------------------------
# Tests for build_full_output
# ---------------------------------------------------------------------------

class TestBuildFullOutput:

    SAMPLE_DOC = "# Solution overview: Generations II\nSome content."
    SAMPLE_GAPS = "1. What is the target go-live date?\n2. Who is the solution owner?"

    def test_full_md_contains_doc_content(self, module_and_shared):
        """full_md includes the original doc text."""
        mod, _ = module_and_shared
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "owner", "repo", "Generations II", "1.0.0"
        )
        assert "# Solution overview: Generations II" in full_md

    def test_full_md_contains_gap_questionnaire_section(self, module_and_shared):
        """full_md includes a ## Gap Questionnaire section."""
        mod, _ = module_and_shared
        full_md, _ = mod.build_full_output(
            self.SAMPLE_DOC, self.SAMPLE_GAPS, "owner", "repo", "Generations II", "1.0.0"
        )
        assert "## Gap Questionnaire" in full_md

    def test_full_md_contains_gaps_text(self, module_and_shared):
        """full_md includes the actual gap questions."""
        mod, _ = module_and_shared
        full_md, _ = mod.build_full_output(
            