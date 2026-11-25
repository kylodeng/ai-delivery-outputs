"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
  - build_test_pack_csv(): correct headers, row content, empty list, special characters
  - build_test_pack_md(): output structure, version/owner/repo substitution, timestamp presence
  - get_results_csv(): happy path decoding, missing content key (FileNotFoundError)
  - Integration smoke tests for __main__ entry-point environment variable handling (stubbed)

Mocks used:
  - unittest.mock.patch for requests.get (get_results_csv)
  - unittest.mock.patch for shared module functions (call_claude, get_repo_files,
    write_output_file, send_email, write_audit_entry)
  - base64 encoding/decoding is exercised directly (no mock needed)
  - datetime.datetime patched for deterministic timestamp tests

TODOs:
  - TODO: Integration test for full __main__ generate flow requires a live Claude API key + GH token
  - TODO: Integration test for full __main__ analyse flow requires a live Claude API key + GH token
  - TODO: Test write_output_file and send_email interactions in __main__ once shared module is fully documented
  - TODO: Verify SYSTEM_GENERATE and SYSTEM_ANALYSE prompt text compliance with AI governance rules
"""

import base64
import csv
import io
import json
import sys
import os
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub out the `shared` module before importing tool5_uat so that
# the import-time side-effects (reading env vars, building GH_HEADERS, etc.)
# do not require real credentials.
# ---------------------------------------------------------------------------

SHARED_STUB = types.ModuleType("shared")
SHARED_STUB.clean_json = MagicMock(side_effect=lambda x: x)
SHARED_STUB.call_claude = MagicMock(return_value="claude-response")
SHARED_STUB.get_repo_files = MagicMock(return_value={"file.py": "print('hello')"})
SHARED_STUB.write_output_file = MagicMock(return_value="sha-abc")
SHARED_STUB.send_email = MagicMock()
SHARED_STUB.email_html = MagicMock(return_value="<html/>")
SHARED_STUB.write_audit_entry = MagicMock()
SHARED_STUB.OUTPUT_REPO_OWNER = "test-owner"
SHARED_STUB.OUTPUT_REPO = "test-output-repo"
SHARED_STUB.GH_HEADERS = {"Authorization": "Bearer fake-token"}
SHARED_STUB.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", SHARED_STUB)

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

import tool5_uat as uat


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Registered Customer
PRE-CONDITIONS:
- User has an active account
TEST DATA: email=user@example.com, password=Secret123
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires staging environment
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: Registered Customer
PRE-CONDITIONS:
- User has an active account
TEST DATA: email=user@example.com, password=WRONG
STEPS:
1. Navigate to login page
2. Enter wrong password
3. Click submit
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Error message shown; user NOT redirected
ESTIMATED TIME: 3
NOTES: None
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Login with max-length email
TYPE: BOUNDARY
PERSONA: Registered Customer
PRE-CONDITIONS:
- System accepts emails up to 254 chars
TEST DATA: email={"a" * 240}@example.com, password=Secret123
STEPS:
1. Enter max-length email
2. Submit
EXPECTED RESULT: Appropriate validation message or successful login
PASS CRITERIA: No server 500 error
ESTIMATED TIME: 2
NOTES: Boundary value analysis
"""

MULTI_SCENARIO_RAW = (
    MINIMAL_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK + BOUNDARY_SCENARIO_BLOCK
)

INSURANCE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GENII-1
TITLE: Verify Generations II policy creation
TYPE: POSITIVE
PERSONA: Insurance Agent
PRE-CONDITIONS:
- Agent is authenticated
- Product "Generations II" is active in system
TEST DATA: product_name=Generations II, doc_type=product_brochure, linked_product=Generations II
STEPS:
1. Navigate to new policy screen
2. Select Generations II product
3. Submit application
EXPECTED RESULT: Policy record created with correct product details
PASS CRITERIA: Policy ID returned; status=ACTIVE
ESTIMATED TIME: 10
NOTES: Linked to Sun Life product brochure data
"""


def _make_csv_result_content(rows: list[list]) -> str:
    """Helper: build a CSV string with standard UAT result headers."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
        "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
    ])
    for row in rows:
        w.writerow(row)
    return buf.getvalue()


def _base64_encode_csv(csv_str: str) -> str:
    return base64.b64encode(csv_str.encode()).decode()


# ===========================================================================
# parse_scenarios() tests
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_all_fields(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Happy path login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Registered Customer"
        assert s["pass_criteria"] == "Dashboard loads within 3 seconds"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_parsed(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_scenario_types_preserved(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        types_found = {s["id"]: s["type"] for s in result}
        assert types_found["UAT-STORY1-1"] == "POSITIVE"
        assert types_found["UAT-STORY1-2"] == "NEGATIVE"
        assert types_found["UAT-STORY1-3"] == "BOUNDARY"

    def test_raw_field_contains_original_block(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "Dashboard loads within 3 seconds" in result[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_no_scenario_delimiter_returns_empty(self):
        result = uat.parse_scenarios("This is just some random text without delimiters.")
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Guest
PASS CRITERIA: Does something
ESTIMATED TIME: 1
NOTES: None
"""
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_scenario_with_id_but_missing_optional_fields(self):
        raw = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial scenario
"""
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Partial scenario"
        assert s.get("type") is None
        assert s.get("persona") is None
        assert s.get("pass_criteria") is None
        assert s.get("estimated_time") is None

    def test_delimiter_at_start_handled(self):
        raw = "===SCENARIO===\n" + MINIMAL_SCENARIO_BLOCK.lstrip("===SCENARIO===\n")
        result = uat.parse_scenarios(raw)
        assert len(result) >= 1

    def test_multiple_colons_in_value_parsed_correctly(self):
        raw = """\
===SCENARIO===
ID: UAT-COLON-1
TITLE: URL contains https://example.com
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Page loads at https://example.com/dashboard
ESTIMATED TIME: 2
NOTES: None
"""
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        # Only the part after "TITLE:" prefix should be captured
        assert "URL contains" in result[0]["title"]

    def test_insurance_scenario_parsed(self):
        result = uat.parse_scenarios(INSURANCE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-GENII-1"
        assert s["persona"] == "Insurance Agent"
        assert s["type"] == "POSITIVE"

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: Real\n"
        result = uat.parse_scenarios(raw)
        # Only the scenario with an ID should appear
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_duplicate_ids_both_included(self):
        raw = MINIMAL_SCENARIO_BLOCK + MINIMAL_SCENARIO_BLOCK
        result = uat.parse_scenarios(raw)
        assert len(result) == 2

    def test_scenario_id_with_leading_trailing_spaces_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-1   \nTITLE: Spaced\n"
        result = uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"

    def test_very_large_input(self):
        """Boundary: many scenarios should all be parsed without error."""
        single = MINIMAL_SCENARIO_BLOCK.replace("UAT-STORY1-1", "UAT-STORY-{i}")
        chunks = []
        for i in range(100):
            chunks.append(single.replace("{i}", str(i)))
        raw = "".join(chunks)
        result = uat.parse_scenarios(raw)
        assert len(result) == 100


# ===========================================================================
# build_test_pack_csv() tests
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_header_row_correct(self):
        result = uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_produces_header_only(self):
        result = uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # only header

    def test_single_scenario_produces_two_rows(self):
        scenarios = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2  # header + 1 data row

    def test_data_row_values_match_scenario(self):
        scenarios = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Happy path login"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Registered Customer"
        assert data_row[4] == "Dashboard loads within 3 seconds"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_all_rows_present(self):
        scenarios = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 4  # header + 3

    def test_missing_fields_produce_empty_strings(self):
        scenarios = [{"id": "UAT-MIN-1", "raw": "minimal"}]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-1"
        assert data_row[1] == ""  # title missing
        assert data_row[2] == ""  # type missing

    def test_special_characters_in_title_csv_escaped(self):
        scenarios = [{
            "id": "UAT-SPEC-1",
            "title": 'Title with, comma and "quotes"',
            "type": "POSITIVE",
            "persona": "Admin",
            "pass_criteria": "It works",
            "estimated_time": "2",
            "raw": ""
        }]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Title with, comma and "quotes"'

    def test_output_is_string(self):
        result = uat.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_newline_terminated(self):
        result = uat.build_test_