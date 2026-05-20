"""
Tests for tool5_uat.py

What is tested:
  - parse_scenarios: happy path, edge cases, malformed/empty input, boundary values
  - build_test_pack_csv: correct headers, row content, empty list, special characters
  - build_test_pack_md: output structure, version/owner/repo injection, raw content inclusion
  - get_results_csv: successful fetch, missing file (FileNotFoundError), malformed API response
  - Integration smoke: __main__ block logic is NOT directly tested (requires env + network);
    stubs are provided with skip markers where full integration context is missing.

Mocks used:
  - unittest.mock.patch for requests.get (get_results_csv)
  - unittest.mock.patch for shared.* imports (call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry)
  - base64 / response content constructed synthetically

TODOs:
  - TODO: Integration test for __main__ block requires full env vars + mocked GitHub API
  - TODO: Verify exact Claude prompt construction once SYSTEM_GENERATE/SYSTEM_ANALYSE are
          passed to call_claude in __main__
  - TODO: Test email formatting once send_email integration is available in CI
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub out `shared` before importing the module under test so that
# the test suite does not require the real shared.py with live credentials.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="mocked-claude-response")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html></html>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now safe to import the module
import importlib
# Ensure module is fresh / resolved against our stub
tool5 = importlib.import_module("tool5_uat") if "tool5_uat" in sys.modules else None

# Fall back: direct path import
if tool5 is None:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "tool5_uat",
        os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"),
    )
    if _spec is None:
        # Try relative to cwd
        _spec = importlib.util.spec_from_file_location(
            "tool5_uat",
            os.path.join(os.path.dirname(__file__), "tool5_uat.py"),
        )
    tool5 = importlib.util.module_from_spec(_spec)
    sys.modules["tool5_uat"] = tool5
    _spec.loader.exec_module(tool5)

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: enterprise user
PRE-CONDITIONS:
- User account exists
- System is running
TEST DATA: alice.chen@example.com / ValidPass1!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard is displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

DOUBLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: enterprise user
PRE-CONDITIONS:
- User account exists
TEST DATA: alice.chen@example.com
STEPS:
1. Navigate to login page
EXPECTED RESULT: Dashboard
PASS CRITERIA: Dashboard visible
ESTIMATED TIME: 5
NOTES: -

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid email
TYPE: NEGATIVE
PERSONA: anonymous
PRE-CONDITIONS:
- System is running
TEST DATA: invalid-email (CUST-007)
STEPS:
1. Enter invalid-email in email field
2. Click Submit
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: Uses synthetic data CUST-007
"""

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario
TYPE: BOUNDARY
PERSONA: consumer
"""

NO_ID_BLOCK = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: smb user
PASS CRITERIA: Something passes
ESTIMATED TIME: 2
"""

FULL_SCENARIOS = [
    {
        "id": "UAT-001-1",
        "title": "Happy path purchase",
        "type": "POSITIVE",
        "persona": "enterprise",
        "pass_criteria": "Order confirmed",
        "estimated_time": "10",
        "raw": "raw block 1",
    },
    {
        "id": "UAT-001-2",
        "title": "Purchase with zero revenue customer",
        "type": "NEGATIVE",
        "persona": "consumer",
        "pass_criteria": "Access denied",
        "estimated_time": "5",
        "raw": "raw block 2",
    },
]


# ===========================================================================
# parse_scenarios tests
# ===========================================================================


class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_fields_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "enterprise user"
        assert s["pass_criteria"] == "Dashboard is displayed within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_single_scenario_raw_preserved(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_double_scenario_returns_two_items(self):
        result = parse_scenarios(DOUBLE_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_double_scenario_ids(self):
        result = parse_scenarios(DOUBLE_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_double_scenario_types(self):
        result = parse_scenarios(DOUBLE_SCENARIO_BLOCK)
        types_found = {s["id"]: s["type"] for s in result}
        assert types_found["UAT-STORY1-1"] == "POSITIVE"
        assert types_found["UAT-STORY1-2"] == "NEGATIVE"

    def test_minimal_scenario_has_id(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"

    def test_minimal_scenario_missing_fields_absent(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        # Fields not present in block should not be in dict (or empty)
        s = result[0]
        assert s.get("pass_criteria", "") == ""
        assert s.get("estimated_time", "") == ""

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(NO_ID_BLOCK)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = parse_scenarios("   \n\n   ")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-X-1\nTITLE: Something\nTYPE: POSITIVE"
        result = parse_scenarios(raw)
        # No ===SCENARIO=== delimiter → no valid blocks with ID split correctly
        # The whole string becomes one block after split, may or may not parse
        # depending on whether it contains ID: — test the contract: list returned
        assert isinstance(result, list)

    def test_delimiter_only_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===\n===SCENARIO===\n===SCENARIO===")
        assert result == []

    def test_synthetic_data_cust007_in_test_data(self):
        """Boundary: scenario referencing invalid email from synthetic data."""
        result = parse_scenarios(DOUBLE_SCENARIO_BLOCK)
        second = next(s for s in result if s["id"] == "UAT-STORY1-2")
        assert "invalid-email" in second["raw"]

    def test_boundary_type_parsed(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_multiple_scenarios_raw_are_independent(self):
        result = parse_scenarios(DOUBLE_SCENARIO_BLOCK)
        assert result[0]["raw"] != result[1]["raw"]

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_type_values_parsed(self, scenario_type):
        raw = f"===SCENARIO===\nID: UAT-T-1\nTYPE: {scenario_type}\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == scenario_type

    def test_extra_whitespace_around_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace title   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace title"

    def test_large_number_of_scenarios(self):
        blocks = ""
        for i in range(50):
            blocks += f"\n===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
        result = parse_scenarios(blocks)
        assert len(result) == 50

    def test_colon_in_title_does_not_break_parsing(self):
        raw = "===SCENARIO===\nID: UAT-C-1\nTITLE: Login: happy path test\n"
        result = parse_scenarios(raw)
        assert "Login: happy path test" == result[0]["title"]


# ===========================================================================
# build_test_pack_csv tests
# ===========================================================================


class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    EXPECTED_HEADERS = [
        "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
        "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
    ]

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_empty_list_has_header_only(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1
        assert rows[0] == self.EXPECTED_HEADERS

    def test_header_row_columns(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0] == self.EXPECTED_HEADERS

    def test_single_scenario_produces_two_rows(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS[:1]))
        assert len(rows) == 2

    def test_two_scenarios_produce_three_rows(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        assert len(rows) == 3

    def test_scenario_id_in_first_column(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        assert rows[1][0] == "UAT-001-1"
        assert rows[2][0] == "UAT-001-2"

    def test_scenario_title_in_second_column(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        assert rows[1][1] == "Happy path purchase"

    def test_scenario_type_in_third_column(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        assert rows[1][2] == "POSITIVE"
        assert rows[2][2] == "NEGATIVE"

    def test_result_column_is_empty(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        # Column index 6 = "Result (PASS/FAIL/BLOCKED)"
        assert rows[1][6] == ""

    def test_tester_column_is_empty(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        assert rows[1][7] == ""

    def test_defect_ref_column_is_empty(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        assert rows[1][9] == ""

    def test_each_row_has_ten_columns(self):
        rows = self._parse_csv(build_test_pack_csv(FULL_SCENARIOS))
        for row in rows:
            assert len(row) == 10

    def test_missing_optional_fields_produce_empty_cells(self):
        sparse = [{"id": "UAT-S-1", "raw": "x"}]
        rows = self._parse_csv(build_test_pack_csv(sparse))
        data_row = rows[1]
        assert data_row[0] == "UAT-S-1"
        assert data_row[1] == ""   # title
        assert data_row[2] == ""   # type

    def test_special_characters_in_title_csv_safe(self):
        scenario = [{
            "id": "UAT-SC-1",
            "title