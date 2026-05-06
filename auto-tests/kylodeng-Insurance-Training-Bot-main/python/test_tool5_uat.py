"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
  - build_test_pack_csv(): correct headers, data rows, empty list, special characters
  - build_test_pack_md(): output structure, metadata injection, empty raw string
  - get_results_csv(): success path, missing content key, HTTP errors, base64 decoding

Mocks used:
  - requests.get (patched via unittest.mock.patch) — no real HTTP calls
  - shared module imports (call_claude, get_repo_files, write_output_file, send_email,
    email_html, write_audit_entry) — patched at module level to avoid import-time side effects
  - base64.b64decode is exercised directly (no mock needed — pure stdlib)
  - os.environ patched for environment variable tests
  - datetime.datetime patched to freeze "now" in build_test_pack_md

TODOs:
  - TODO: Integration test for __main__ block requires full env setup + mocked subprocess chain
  - TODO: Test call_claude interaction inside generate/analyse flow once refactored to testable functions
  - TODO: Verify CSV round-trip with actual tester-filled data (needs sample completed CSV fixture)
"""

import base64
import csv
import io
import json
import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch, call
import importlib

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub out the `shared` module before importing tool5_uat
# so tests never touch real GitHub APIs, email, or Claude.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="mocked claude response")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now we can safely import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# Dynamically load so the sys.path manipulation inside the file runs in isolation
spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
tool5 = importlib.util.module_from_spec(spec)
# Inject the stub before exec
tool5.__dict__["shared"] = _shared_stub
sys.modules["tool5_uat"] = tool5
try:
    spec.loader.exec_module(tool5)
except SystemExit:
    pass  # __main__ guard might fire; suppress

parse_scenarios   = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md  = tool5.build_test_pack_md
get_results_csv     = tool5.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Purchase Generations II whole life policy
TYPE: POSITIVE
PERSONA: New customer
PRE-CONDITIONS:
- User is logged in
TEST DATA: product=Generations II, dob=1985-03-15, sum_assured=500000
STEPS:
1. Navigate to product page
2. Enter applicant details
3. Submit application
EXPECTED RESULT: Policy is created and confirmation email sent
PASS CRITERIA: Policy number displayed on screen
ESTIMATED TIME: 10
NOTES: Uses Sun Life test environment
"""

FULL_MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-HOSP-1
TITLE: Search designated mainland China hospital
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- User has active health product
TEST DATA: hospital=Peking Union Medical College Hospital, city=Beijing
STEPS:
1. Open hospital search
2. Enter city name
3. Select hospital from list
EXPECTED RESULT: Hospital details shown
PASS CRITERIA: Hospital address and class displayed
ESTIMATED TIME: 5
NOTES: Covers Class 3 hospitals
===SCENARIO===
ID: UAT-HOSP-2
TITLE: Search with invalid city name
TYPE: NEGATIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- User has active health product
TEST DATA: hospital=, city=INVALID_CITY_XYZ
STEPS:
1. Open hospital search
2. Enter invalid city
3. Submit
EXPECTED RESULT: Error message displayed
PASS CRITERIA: User sees "No hospitals found" message
ESTIMATED TIME: 3
NOTES: Boundary / negative test
===SCENARIO===
ID: UAT-HOSP-3
TITLE: Search with empty input
TYPE: BOUNDARY
PERSONA: Existing policyholder
PRE-CONDITIONS:
- User has active health product
TEST DATA: hospital=, city=
STEPS:
1. Open hospital search
2. Leave all fields blank
3. Submit
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Submit button disabled or error shown
ESTIMATED TIME: 2
NOTES: Empty input boundary
"""


def _make_scenario(id_="UAT-X-1", title="Test title", type_="POSITIVE",
                   persona="Tester", pass_criteria="Screen shows OK",
                   estimated_time="5"):
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": f"ID: {id_}\nTITLE: {title}",
    }


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_all_fields(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-GEN2-1"
        assert s["title"] == "Purchase Generations II whole life policy"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "New customer"
        assert s["pass_criteria"] == "Policy number displayed on screen"
        assert s["estimated_time"] == "10"

    def test_multiple_scenarios_count(self):
        scenarios = parse_scenarios(FULL_MULTI_SCENARIO_RAW)
        assert len(scenarios) == 3

    def test_multiple_scenarios_ids(self):
        scenarios = parse_scenarios(FULL_MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in scenarios]
        assert ids == ["UAT-HOSP-1", "UAT-HOSP-2", "UAT-HOSP-3"]

    def test_multiple_scenarios_types(self):
        scenarios = parse_scenarios(FULL_MULTI_SCENARIO_RAW)
        types = [s["type"] for s in scenarios]
        assert "POSITIVE" in types
        assert "NEGATIVE" in types
        assert "BOUNDARY" in types

    def test_raw_field_preserved(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "raw" in scenarios[0]
        assert "UAT-GEN2-1" in scenarios[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # Text with no ===SCENARIO=== delimiter
        raw = "ID: UAT-ORPHAN-1\nTITLE: Orphan\nTYPE: POSITIVE\n"
        assert parse_scenarios(raw) == []

    def test_block_without_id_is_skipped(self):
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Nothing
ESTIMATED TIME: 1
"""
        scenarios = parse_scenarios(raw)
        assert scenarios == []

    def test_block_with_partial_fields_still_parsed(self):
        raw = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial fields only
"""
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Partial fields only"
        # Missing fields should not be present (or be empty)
        assert s.get("type", "") == ""

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: Valid\n"
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-WS-1"

    def test_delimiter_at_start_with_leading_newlines(self):
        raw = "\n\n===SCENARIO===\nID: UAT-NL-1\nTITLE: Newline prefix\n"
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-NL-1"

    def test_persona_with_spaces(self):
        raw = """\
===SCENARIO===
ID: UAT-PERSONA-1
TITLE: Cashless claim
TYPE: POSITIVE
PERSONA: Senior policyholder above 65
PASS CRITERIA: Claim approved
ESTIMATED TIME: 8
"""
        scenarios = parse_scenarios(raw)
        assert scenarios[0]["persona"] == "Senior policyholder above 65"

    def test_estimated_time_as_string(self):
        raw = """\
===SCENARIO===
ID: UAT-TIME-1
TITLE: Timing test
ESTIMATED TIME: 15 minutes
"""
        scenarios = parse_scenarios(raw)
        assert scenarios[0]["estimated_time"] == "15 minutes"

    def test_duplicate_ids_both_returned(self):
        raw = """\
===SCENARIO===
ID: UAT-DUP-1
TITLE: First
===SCENARIO===
ID: UAT-DUP-1
TITLE: Second
"""
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 2

    @pytest.mark.parametrize("raw_input", [
        None,
    ])
    def test_non_string_input_raises(self, raw_input):
        with pytest.raises((AttributeError, TypeError)):
            parse_scenarios(raw_input)

    def test_insurance_synthetic_data_id_format(self):
        """Scenario IDs referencing insurance product names are parsed correctly."""
        raw = """\
===SCENARIO===
ID: UAT-GEN2-HOSP-1
TITLE: Verify Generations II policyholder can search designated hospitals
TYPE: POSITIVE
PERSONA: Generations II policyholder
PASS CRITERIA: Hospital list loads within 3 seconds
ESTIMATED TIME: 5
"""
        scenarios = parse_scenarios(raw)
        assert scenarios[0]["id"] == "UAT-GEN2-HOSP-1"

    def test_very_large_number_of_scenarios(self):
        """Performance / boundary: 100 scenarios parsed without error."""
        blocks = ""
        for i in range(100):
            blocks += f"\n===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
        scenarios = parse_scenarios(blocks)
        assert len(scenarios) == 100

    def test_special_characters_in_title(self):
        raw = """\
===SCENARIO===
ID: UAT-SPECIAL-1
TITLE: Verify <script>alert('xss')</script> is escaped
"""
        scenarios = parse_scenarios(raw)
        assert "xss" in scenarios[0]["title"]


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_header_row_correct(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1

    def test_single_scenario_produces_two_rows(self):
        s = _make_scenario()
        result = build_test_pack_csv([s])
        rows = self._parse_csv(result)
        assert len(rows) == 2

    def test_data_row_values_correct(self):
        s = _make_scenario(
            id_="UAT-GEN2-1",
            title="Purchase Generations II policy",
            type_="POSITIVE",
            persona="New customer",
            pass_criteria="Policy confirmed",
            estimated_time="10"
        )
        result = build_test_pack_csv([s])
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-GEN2-1"
        assert data_row[1] == "Purchase Generations II policy"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "New customer"
        assert data_row[4] == "Policy confirmed"
        assert data_row[5] == "10"

    def test_result_tester_notes_defect_empty(self):
        s = _make_scenario()
        result = build_test_pack_csv([s])
        rows = self._parse_csv(result)
        data_row = rows[1]
        # Columns 6-9 should be blank (tester fills them in)
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_row_count(self):
        scenarios = [_make_scenario(id_=f"UAT-X-{i}") for i in range(5)]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 6  # header + 5 data

    def test_missing_optional_fields_use_empty_string(self):
        s = {"id": "UAT-MIN-1", "raw": "raw text"}
        result = build_test_pack_csv([s])
        rows = self._parse_csv(result)
        assert rows[1][0] == "UAT-MIN-1"
        assert rows[1][1] == ""  # title missing
        assert rows[1][2] == ""  # type missing

    def test_special_characters_csv_escaped(self):
        s = _make