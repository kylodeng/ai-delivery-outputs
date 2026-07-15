"""
Tests for tool5_uat.py
======================

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, missing fields, multiple scenarios)
  - build_test_pack_csv(): CSV structure, header row, data rows, empty scenario list
  - build_test_pack_md(): Markdown structure, version/owner/repo interpolation
  - get_results_csv(): successful fetch, file-not-found error, base64 decoding

Mocks used:
  - requests.get (for get_results_csv GitHub API calls)
  - shared module functions: call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry
  - os.environ patching for environment variables

TODOs:
  - TODO: Integration test for __main__ block requires full env setup and live GitHub token
  - TODO: Tests for SYSTEM_GENERATE / SYSTEM_ANALYSE prompt content validation
    (need Claude API key)
  - TODO: End-to-end mode A (generate) and mode B (analyse) tests require
    mocking the full call_claude response chain
"""

import base64
import csv
import io
import json
import sys
import os
import types
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we don't need real credentials
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value='{"stub": true}')
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Install stub before importing the module under test
shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# We import via exec into a fresh module namespace so we can patch `requests`
# inside the module without affecting the global namespace.
_module_src = _script_path.read_text() if _script_path.exists() else None

def _load_tool5():
    """Load tool5_uat as a module, patching `requests` at load time."""
    with patch.dict(sys.modules, {"shared": shared_stub}):
        spec = importlib.util.spec_from_file_location("tool5_uat", str(_script_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# Skip everything if source file is missing
_SOURCE_MISSING = not _script_path.exists()
skip_if_missing = pytest.mark.skipif(
    _SOURCE_MISSING,
    reason=f"Source file not found at {_script_path}"
)


@pytest.fixture(scope="module")
def tool5():
    if _SOURCE_MISSING:
        pytest.skip("Source file not found")
    return _load_tool5()


# ===========================================================================
# Helpers / Fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Standard User
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to /login
2. Enter credentials
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 s
ESTIMATED TIME: 5
NOTES: Requires seeded DB
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid underwriting submission
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Logged in as underwriter
TEST DATA: Annual_Income=75000, Age=35, Risk_Classification=Low
STEPS:
1. Open new application form
2. Fill all mandatory fields
3. Submit
EXPECTED RESULT: Application created with status PENDING
PASS CRITERIA: HTTP 201 returned and record visible in dashboard
ESTIMATED TIME: 10
NOTES: Uses synthetic customer CUST00000001

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid income — negative value
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Logged in as underwriter
TEST DATA: Annual_Income=-1, Age=35
STEPS:
1. Open new application form
2. Enter Annual_Income=-1
3. Submit
EXPECTED RESULT: Validation error displayed
PASS CRITERIA: Error message shown; no record created
ESTIMATED TIME: 5
NOTES: Boundary/negative scenario

===SCENARIO===
ID: UAT-STORY1-3
TITLE: Max age boundary
TYPE: BOUNDARY
PERSONA: Underwriter
PRE-CONDITIONS:
- Logged in as underwriter
TEST DATA: Age=150
STEPS:
1. Enter Age=150
2. Submit
EXPECTED RESULT: System rejects with validation error
PASS CRITERIA: Error message "Age out of range"
ESTIMATED TIME: 5
NOTES: [TESTER: verify this]
"""


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    @skip_if_missing
    def test_empty_string_returns_empty_list(self, tool5):
        result = tool5.parse_scenarios("")
        assert result == []

    @skip_if_missing
    def test_no_delimiter_returns_empty_list(self, tool5):
        """Block without the ===SCENARIO=== delimiter and no ID should be ignored."""
        result = tool5.parse_scenarios("Some random text without delimiter")
        assert result == []

    @skip_if_missing
    def test_single_scenario_parsed(self, tool5):
        result = tool5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Happy path login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Standard User"
        assert s["pass_criteria"] == "Dashboard loads within 3 s"
        assert s["estimated_time"] == "5"

    @skip_if_missing
    def test_single_scenario_raw_preserved(self, tool5):
        result = tool5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-STORY1-1" in result[0]["raw"]

    @skip_if_missing
    def test_multiple_scenarios_parsed(self, tool5):
        result = tool5.parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    @skip_if_missing
    def test_scenario_types_preserved(self, tool5):
        result = tool5.parse_scenarios(MULTI_SCENARIO_RAW)
        types_found = {s["id"]: s["type"] for s in result}
        assert types_found["UAT-STORY1-1"] == "POSITIVE"
        assert types_found["UAT-STORY1-2"] == "NEGATIVE"
        assert types_found["UAT-STORY1-3"] == "BOUNDARY"

    @skip_if_missing
    def test_scenario_missing_id_is_excluded(self, tool5):
        """Blocks without an ID line should be dropped."""
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = tool5.parse_scenarios(raw)
        assert result == []

    @skip_if_missing
    def test_scenario_with_only_id(self, tool5):
        """A block with only ID should still be included."""
        raw = "===SCENARIO===\nID: UAT-X-1\n"
        result = tool5.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"
        assert result[0].get("title") is None

    @skip_if_missing
    def test_leading_junk_before_first_delimiter(self, tool5):
        """Text before the first delimiter is ignored."""
        raw = "Some preamble text\n" + SINGLE_SCENARIO_BLOCK
        result = tool5.parse_scenarios(raw)
        assert len(result) == 1

    @skip_if_missing
    def test_whitespace_only_blocks_ignored(self, tool5):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-OK-1\n"
        result = tool5.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-OK-1"

    @skip_if_missing
    def test_synthetic_data_in_raw_field(self, tool5):
        """Synthetic test data values appear in the raw field."""
        result = tool5.parse_scenarios(MULTI_SCENARIO_RAW)
        raws = " ".join(s["raw"] for s in result)
        assert "CUST00000001" in raws
        assert "Annual_Income=75000" in raws
        assert "Risk_Classification=Low" in raws

    @skip_if_missing
    @pytest.mark.parametrize("delimiter_variant", [
        "===SCENARIO===",
        "===SCENARIO===\n",
        "\n===SCENARIO===\n",
    ])
    def test_delimiter_variants(self, tool5, delimiter_variant):
        raw = f"{delimiter_variant}ID: UAT-DV-1\nTITLE: Test\n"
        result = tool5.parse_scenarios(raw)
        assert len(result) == 1

    @skip_if_missing
    def test_large_number_of_scenarios(self, tool5):
        """Parse 50 scenarios without errors."""
        blocks = []
        for i in range(1, 51):
            blocks.append(
                f"===SCENARIO===\n"
                f"ID: UAT-LOAD-{i}\n"
                f"TITLE: Scenario {i}\n"
                f"TYPE: POSITIVE\n"
                f"PERSONA: Tester\n"
                f"PASS CRITERIA: Pass\n"
                f"ESTIMATED TIME: 3\n"
            )
        result = tool5.parse_scenarios("\n".join(blocks))
        assert len(result) == 50


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    @skip_if_missing
    def test_returns_string(self, tool5):
        result = tool5.build_test_pack_csv([])
        assert isinstance(result, str)

    @skip_if_missing
    def test_header_row_present(self, tool5):
        result = tool5.build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "Scenario ID" in header
        assert "Title" in header
        assert "Type" in header
        assert "Persona" in header
        assert "Pass Criteria" in header
        assert "Est. Time (min)" in header
        assert "Result (PASS/FAIL/BLOCKED)" in header
        assert "Tester" in header
        assert "Notes" in header
        assert "Defect Ref" in header

    @skip_if_missing
    def test_empty_scenarios_only_header(self, tool5):
        result = tool5.build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # header only

    @skip_if_missing
    def test_single_scenario_one_data_row(self, tool5):
        scenarios = [{"id": "UAT-1-1", "title": "Test", "type": "POSITIVE",
                      "persona": "Admin", "pass_criteria": "Works",
                      "estimated_time": "5"}]
        result = tool5.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data row

    @skip_if_missing
    def test_data_row_values_correct(self, tool5):
        scenarios = [{"id": "UAT-STORY1-2", "title": "Invalid income",
                      "type": "NEGATIVE", "persona": "Underwriter",
                      "pass_criteria": "Error shown", "estimated_time": "5"}]
        result = tool5.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        data = next(reader)
        assert data[0] == "UAT-STORY1-2"
        assert data[1] == "Invalid income"
        assert data[2] == "NEGATIVE"
        assert data[3] == "Underwriter"
        assert data[4] == "Error shown"
        assert data[5] == "5"
        # Result, Tester, Notes, Defect Ref should be empty
        assert data[6] == ""
        assert data[7] == ""
        assert data[8] == ""
        assert data[9] == ""

    @skip_if_missing
    def test_multiple_scenarios_correct_row_count(self, tool5):
        scenarios_input = tool5.parse_scenarios(MULTI_SCENARIO_RAW)
        result = tool5.build_test_pack_csv(scenarios_input)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1 + len(scenarios_input)

    @skip_if_missing
    def test_missing_keys_use_empty_string(self, tool5):
        """Scenario dict with no keys should not raise; uses empty strings."""
        scenarios = [{}]
        result = tool5.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2
        data = rows[1]
        assert data[0] == ""
        assert data[1] == ""

    @skip_if_missing
    def test_special_characters_in_csv(self, tool5):
        """Values with commas and quotes should be properly escaped."""
        scenarios = [{"id": 'UAT-SPEC-1', "title": 'Title with "quotes" and, comma',
                      "type": "POSITIVE", "persona": "User, Admin",
                      "pass_criteria": "OK", "estimated_time": "2"}]
        result = tool5.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        data = next(reader)
        assert data[1] == 'Title with "quotes" and, comma'
        assert data[3] == "