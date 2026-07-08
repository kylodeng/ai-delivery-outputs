"""
Test suite for tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
    - build_test_pack_csv(): correct headers, row data, empty scenarios, special characters
    - build_test_pack_md(): markdown structure, version/owner/repo injection, raw content inclusion
    - get_results_csv(): successful fetch, missing content key, network/API errors
    - Module-level constants and imports from shared

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
    - unittest.mock.patch for `base64.b64decode` (indirectly via requests mock)
    - shared module functions (call_claude, get_repo_files, write_output_file, send_email,
      write_audit_entry, email_html) are stubbed to avoid real network/API calls

TODOs:
    - TODO: Integration test for __main__ block requires real environment variables and
      mocked GitHub Actions context — stubbed below
    - TODO: Test for Mode B (analyse) end-to-end flow requires mocked call_claude returning
      valid SYSTEM_ANALYSE JSON — stubbed below
    - TODO: Test for Mode A (generate) end-to-end flow requires mocked call_claude returning
      SYSTEM_GENERATE formatted output — stubbed below
    - TODO: Verify exact CSV encoding behaviour when scenarios contain Unicode/CJK characters
      from the insurance product synthetic data
"""

import base64
import csv
import io
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool5_uat without
# having the real shared.py present in every environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = lambda s: s
    shared.call_claude = MagicMock(return_value="stub")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-output-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "token test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Insert the stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Now import the module under test
import importlib
import tool5_uat  # noqa: E402  (the script lives in .github/scripts on sys.path)

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: username=test@sunlife.com, password=Valid!23
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login fails with invalid password
TYPE: NEGATIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User account exists
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: Check lockout after 5 attempts
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Login with maximum-length username
TYPE: BOUNDARY
PERSONA: Admin
PASS CRITERIA: System accepts or rejects gracefully
ESTIMATED TIME: 2
NOTES: [TESTER: verify this]
"""

TWO_SCENARIOS_RAW = MINIMAL_SCENARIO_BLOCK + "\n" + NEGATIVE_SCENARIO_BLOCK

THREE_SCENARIOS_RAW = (
    MINIMAL_SCENARIO_BLOCK + "\n"
    + NEGATIVE_SCENARIO_BLOCK + "\n"
    + BOUNDARY_SCENARIO_BLOCK
)


def _make_scenario(
    sid="UAT-X-1",
    title="Some title",
    stype="POSITIVE",
    persona="User",
    pass_criteria="System responds correctly",
    estimated_time="5",
):
    return {
        "id": sid,
        "title": title,
        "type": stype,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": "raw block text",
    }


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_happy_path_single_scenario(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Policyholder"
        assert s["pass_criteria"] == "Dashboard loads within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_happy_path_two_scenarios(self):
        result = parse_scenarios(TWO_SCENARIOS_RAW)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_happy_path_three_scenarios(self):
        result = parse_scenarios(THREE_SCENARIOS_RAW)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert ids == ["UAT-STORY1-1", "UAT-STORY1-2", "UAT-STORY1-3"]

    def test_raw_field_is_populated(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_only_delimiter_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===")
        assert result == []

    def test_block_with_no_id_is_skipped(self):
        no_id_block = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: User
PASS CRITERIA: something
ESTIMATED TIME: 1
NOTES: none
"""
        result = parse_scenarios(no_id_block)
        assert result == []

    def test_multiple_delimiters_no_id_in_some(self):
        raw = MINIMAL_SCENARIO_BLOCK + "\n===SCENARIO===\nTITLE: orphan\n"
        result = parse_scenarios(raw)
        # Only the first block has an ID
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_partial_fields_handled_gracefully(self):
        """Scenario with only ID and TITLE — other fields absent."""
        partial_block = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial scenario
"""
        result = parse_scenarios(partial_block)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Partial scenario"
        # Missing fields should not be in dict (uses .get() downstream)
        assert s.get("type") is None
        assert s.get("persona") is None
        assert s.get("pass_criteria") is None
        assert s.get("estimated_time") is None

    def test_whitespace_stripped_from_values(self):
        raw = """\
===SCENARIO===
ID:   UAT-WS-1  
TITLE:   Whitespace test   
TYPE:  POSITIVE  
PERSONA:  Admin  
PASS CRITERIA:  Criteria met  
ESTIMATED TIME:  10  
"""
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"
        assert result[0]["type"] == "POSITIVE"
        assert result[0]["persona"] == "Admin"
        assert result[0]["pass_criteria"] == "Criteria met"
        assert result[0]["estimated_time"] == "10"

    def test_negative_type_parsed(self):
        result = parse_scenarios(NEGATIVE_SCENARIO_BLOCK)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_parsed(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_scenario_with_insurance_synthetic_data(self):
        """Use synthetic data from Generations II product brochure as test data."""
        raw = """\
===SCENARIO===
ID: UAT-INS-1
TITLE: Submit claim for Generations II policy
TYPE: POSITIVE
PERSONA: Policyholder
TEST DATA: product_name=Generations II, doc_type=product_brochure, policy_number=GEN2-00123
STEPS:
1. Login with valid credentials
2. Navigate to Claims section
3. Select policy Generations II
4. Submit claim with accidental coma benefit
EXPECTED RESULT: Claim submitted successfully with reference number
PASS CRITERIA: Confirmation screen shows claim reference
ESTIMATED TIME: 10
NOTES: Verify accelerated benefit for terminal illness
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-INS-1"
        assert result[0]["title"] == "Submit claim for Generations II policy"

    def test_scenario_with_hospital_list_data(self):
        """Scenario using Mainland China hospital list synthetic data."""
        raw = """\
===SCENARIO===
ID: UAT-HOSP-1
TITLE: Search for designated hospital in Mainland China
TYPE: POSITIVE
PERSONA: Health Product Policyholder
TEST DATA: doc_type=supplementary, linked_product=health_products, city=Shanghai
PASS CRITERIA: Hospital list displayed with Class 3 hospitals
ESTIMATED TIME: 7
NOTES: Covers all Class 3 hospitals across mainland China
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-HOSP-1"

    def test_many_scenarios_performance(self):
        """Parser should handle a large number of scenarios without error."""
        blocks = []
        for i in range(100):
            blocks.append(
                f"===SCENARIO===\nID: UAT-PERF-{i}\nTITLE: Scenario {i}\n"
                f"TYPE: POSITIVE\nPASS CRITERIA: ok\nESTIMATED TIME: 1\n"
            )
        raw = "\n".join(blocks)
        result = parse_scenarios(raw)
        assert len(result) == 100

    def test_line_not_matching_any_key_is_ignored(self):
        """Unknown lines in the block should not raise errors."""
        raw = """\
===SCENARIO===
ID: UAT-UNKNOWN-1
TITLE: Test with unknown fields
UNKNOWN_FIELD: some value
ANOTHER: thing
PASS CRITERIA: ok
ESTIMATED TIME: 2
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-UNKNOWN-1"
        assert "unknown_field" not in result[0]


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # only header

    def test_single_scenario_row(self):
        s = _make_scenario()
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data row
        data_row = rows[1]
        assert data_row[0] == "UAT-X-1"
        assert data_row[1] == "Some title"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "User"
        assert data_row[4] == "System responds correctly"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(sid=f"UAT-X-{i}") for i in range(5)]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 6  # header + 5 data rows

    def test_scenario_ids_preserved_in_order(self):
        scenarios = [
            _make_scenario(sid="UAT-A-1"),
            _make_scenario(sid="UAT-B-2"),
            _make_scenario(sid="UAT-C-3"),
        ]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert rows[1][0] == "UAT-A-1"
        assert rows[2][0] == "UAT-B-2"
        assert rows[3][0] == "UAT-C-3"

    def test_missing_optional_fields_handled_as_empty(self):
        """Scenario dict with no optional fields — .get() should return ''."""
        s = {"id": "UAT-MIN-1", "raw": "raw"}
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-1"
        assert data_row[1]