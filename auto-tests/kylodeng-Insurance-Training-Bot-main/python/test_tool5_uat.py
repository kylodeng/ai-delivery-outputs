"""
Tests for tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, missing blocks, missing ID, empty input, multi-scenario, partial fields
  - build_test_pack_csv(): header row, data rows, empty list, special characters in fields
  - build_test_pack_md(): output structure, version/owner/repo injection, raw content inclusion
  - get_results_csv(): successful fetch + base64 decode, missing content key (FileNotFoundError), API error shapes

Mocks used:
  - requests.get (patched at tool5_uat.requests.get) — prevents real GitHub API calls
  - shared module functions (call_claude, get_repo_files, write_output_file, send_email,
    email_html, write_audit_entry) — patched to avoid side-effects
  - base64.b64decode — used directly; real implementation is exercised (no need to mock)
  - os.environ — monkeypatched per test where needed

TODOs:
  - TODO: Integration test for __main__ block requires full env var setup + mocked subprocess chain
  - TODO: Test call_claude interaction inside generate/analyse flows once those helper functions
          are extracted from the __main__ guard
  - TODO: Validate SYSTEM_GENERATE and SYSTEM_ANALYSE prompt strings produce correct Claude output
          (requires Claude API or recorded cassette)
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
# Minimal stub for the `shared` module so the import inside tool5_uat works
# without the real shared.py being importable in the test environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="stub response")
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


# Install the stub before importing the module under test.
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test.
import importlib
import tool5_uat  # type: ignore


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: End User
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: email=test@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter valid credentials
3. Click Login
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard is displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: Verify session token is set
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-CLAIM-1
TITLE: Submit a valid insurance claim
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Policy is active
TEST DATA: policy_id=GEN2-001, amount=5000
STEPS:
1. Log in
2. Navigate to Claims
3. Submit form
EXPECTED RESULT: Claim reference number returned
PASS CRITERIA: Reference number displayed on screen
ESTIMATED TIME: 10
NOTES: Uses Generations II product data
===SCENARIO===
ID: UAT-CLAIM-2
TITLE: Submit claim with missing mandatory fields
TYPE: NEGATIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Policy is active
TEST DATA: policy_id=GEN2-001, amount=
STEPS:
1. Log in
2. Navigate to Claims
3. Submit incomplete form
EXPECTED RESULT: Validation error displayed
PASS CRITERIA: Error message visible
ESTIMATED TIME: 5
NOTES: Boundary/negative path
===SCENARIO===
ID: UAT-CLAIM-3
TITLE: Submit claim at maximum allowed amount boundary
TYPE: BOUNDARY
PERSONA: Policyholder
PRE-CONDITIONS:
- Policy limit is 100000
TEST DATA: policy_id=GEN2-001, amount=100000
STEPS:
1. Log in
2. Navigate to Claims
3. Enter max amount and submit
EXPECTED RESULT: Claim accepted
PASS CRITERIA: Success message shown
ESTIMATED TIME: 7
NOTES: Check off-by-one at 100001
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
ESTIMATED TIME: 3
NOTES: This block has no ID line
"""

SCENARIO_MISSING_OPTIONAL_FIELDS = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial fields only
TYPE: NEGATIVE
"""


# ---------------------------------------------------------------------------
# parse_scenarios
# ---------------------------------------------------------------------------

class TestParseScenarios:

    def test_empty_string_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("Some random text without any delimiter")
        assert result == []

    def test_single_scenario_parsed_correctly(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "End User"
        assert s["pass_criteria"] == "Dashboard is displayed within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_single_scenario_contains_raw_block(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_multiple_scenarios_all_parsed(self):
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3

    def test_multiple_scenarios_ids_correct(self):
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-CLAIM-1", "UAT-CLAIM-2", "UAT-CLAIM-3"]

    def test_multiple_scenarios_types_correct(self):
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_RAW)
        types_ = [s["type"] for s in result]
        assert types_ == ["POSITIVE", "NEGATIVE", "BOUNDARY"]

    def test_scenario_without_id_is_skipped(self):
        result = tool5_uat.parse_scenarios(SCENARIO_WITHOUT_ID)
        assert len(result) == 0

    def test_scenario_with_missing_optional_fields_still_parsed(self):
        result = tool5_uat.parse_scenarios(SCENARIO_MISSING_OPTIONAL_FIELDS)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Partial fields only"
        assert s["type"] == "NEGATIVE"
        # Optional fields should be absent (not set to empty string unless line present)
        assert "persona" not in s
        assert "estimated_time" not in s

    def test_mixed_valid_and_invalid_blocks(self):
        mixed = SINGLE_SCENARIO_BLOCK + SCENARIO_WITHOUT_ID + MULTI_SCENARIO_RAW
        result = tool5_uat.parse_scenarios(mixed)
        # SCENARIO_WITHOUT_ID is skipped; others are present
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-CLAIM-1" in ids
        assert "UAT-CLAIM-2" in ids
        assert "UAT-CLAIM-3" in ids

    def test_whitespace_only_blocks_are_skipped(self):
        raw = "===SCENARIO===\n   \n   \n===SCENARIO===\n" + SINGLE_SCENARIO_BLOCK.split("===SCENARIO===")[1]
        result = tool5_uat.parse_scenarios(raw)
        # Whitespace-only block produces no ID → skipped; valid block is kept
        valid = [s for s in result if s.get("id")]
        assert len(valid) >= 1

    def test_id_with_extra_whitespace_is_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1  \nTITLE: Whitespace Test\nTYPE: POSITIVE\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"

    def test_title_with_extra_whitespace_is_stripped(self):
        raw = "===SCENARIO===\nID: UAT-WS-2\nTITLE:   Spaces Around Title   \nTYPE: POSITIVE\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["title"] == "Spaces Around Title"

    @pytest.mark.parametrize("raw_input", [
        "===SCENARIO===\n",
        "===SCENARIO===\nID:\nTITLE:\n",
    ])
    def test_empty_id_value_is_skipped(self, raw_input):
        result = tool5_uat.parse_scenarios(raw_input)
        assert result == []

    def test_insurance_domain_synthetic_data_in_notes(self):
        """Scenario referencing Generations II synthetic data is parsed."""
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_RAW)
        claim1 = next(s for s in result if s["id"] == "UAT-CLAIM-1")
        assert "Generations II" in claim1["raw"]


# ---------------------------------------------------------------------------
# build_test_pack_csv
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_returns_string(self):
        assert isinstance(tool5_uat.build_test_pack_csv([]), str)

    def test_empty_scenarios_only_header(self):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([]))
        assert len(rows) == 1

    def test_header_columns(self):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([]))
        header = rows[0]
        assert header[0] == "Scenario ID"
        assert header[1] == "Title"
        assert header[2] == "Type"
        assert header[3] == "Persona"
        assert header[4] == "Pass Criteria"
        assert header[5] == "Est. Time (min)"
        assert header[6] == "Result (PASS/FAIL/BLOCKED)"
        assert header[7] == "Tester"
        assert header[8] == "Notes"
        assert header[9] == "Defect Ref"

    def test_single_scenario_produces_two_rows(self):
        scenarios = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        assert len(rows) == 2  # header + 1 data row

    def test_data_row_values(self):
        scenarios = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Successful login with valid credentials"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "End User"
        assert data_row[4] == "Dashboard is displayed within 3 seconds"
        assert data_row[5] == "5"

    def test_result_tester_notes_defect_ref_empty(self):
        scenarios = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        data_row = rows[1]
        # columns 6-9 should be empty strings
        for col_idx in range(6, 10):
            assert data_row[col_idx] == ""

    def test_multiple_scenarios_row_count(self):
        scenarios = tool5_uat.parse_scenarios(MULTI_SCENARIO_RAW)
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        assert len(rows) == 4  # header + 3 data rows

    def test_special_characters_in_title(self):
        scenarios = [{"id": "UAT-X-1", "title": 'Title with "quotes" and, commas',
                      "type": "POSITIVE", "persona": "Admin",
                      "pass_criteria": "OK", "estimated_time": "2"}]
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_missing_optional_scenario_fields_produce_empty_strings(self):
        # Scenario dict with only id populated
        scenarios = [{"id": "UAT-MIN-1", "raw": "ID: UAT-MIN-1"}]
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        data = rows[1]
        assert data[0] == "UAT-MIN-1"
        # All other populated columns should be empty
        for col_idx in range(1, 10):
            assert data[col_idx] == ""

    def test_csv_is_parseable_after_roundtrip(self):
        scenarios = tool5_uat.parse_scenarios(MULTI_SCENARIO_RAW)
        csv_str = tool5_uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 4

    def test_insurance_synthetic_data_in_csv(self):
        scenarios = [
            {
                "id": "UAT-INS-1",
                "title": "Generations II policyholder claim",
                "type": "POSITIVE",
                "persona": "Policyholder",
                "pass_criteria": "Claim reference shown",
                "estimated_time": "10",
            }
        ]
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        assert rows[1][1] == "Generations II policyholder claim"


# ---------------------------------------------------------------------------
# build_test_pack_md
# ---------------------------------------------------------------------------

class TestBuildTestPackM