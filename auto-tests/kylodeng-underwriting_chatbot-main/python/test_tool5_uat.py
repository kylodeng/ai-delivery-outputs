"""
Tests for tool5_uat.py

What is tested:
  - parse_scenarios: happy path, edge cases (empty input, no ID, multiple scenarios, partial fields)
  - build_test_pack_csv: correct headers, row content, empty list, special characters
  - build_test_pack_md: output structure, metadata injection, raw content embedding
  - get_results_csv: success path, missing content key (FileNotFoundError)
  - SYSTEM_GENERATE / SYSTEM_ANALYSE: constants present and non-empty

Mocks used:
  - requests.get (for get_results_csv GitHub API call)
  - base64.b64decode (via real usage with mocked response)
  - shared module imports (call_claude, get_repo_files, write_output_file, send_email,
    email_html, write_audit_entry) — patched at import boundary

TODOs:
  - TODO: Integration test for __main__ block requires full env-var setup + live shared module
  - TODO: Test call_claude interactions once shared.call_claude signature is confirmed
  - TODO: Verify CSV round-trip parsing if testers produce non-UTF-8 content
  - TODO: Confirm GH_API / GH_HEADERS values in shared module for deeper get_results_csv tests
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module before importing tool5_uat so we don't need
# the real shared.py on the path during tests.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="mocked claude response")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now safe to import the module under test
import importlib

# Ensure the script directory resolves without a real file on disk
with patch.dict(sys.modules, {"shared": shared_stub}):
    import tool5_uat as uat


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com, password=ValidPass123
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard is displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires staging environment
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Account active
TEST DATA: user=underwriter@insure.com
STEPS:
1. Open browser
2. Submit form
EXPECTED RESULT: Dashboard shown
PASS CRITERIA: 200 OK response and dashboard rendered
ESTIMATED TIME: 3
NOTES: None

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with wrong password
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Account active
TEST DATA: user=underwriter@insure.com, password=WRONG
STEPS:
1. Open browser
2. Enter wrong password
3. Submit form
EXPECTED RESULT: Error message shown
PASS CRITERIA: Error displayed, no redirect
ESTIMATED TIME: 2
NOTES: Lockout after 3 attempts

===SCENARIO===
ID: UAT-STORY1-3
TITLE: Boundary — max field length
TYPE: BOUNDARY
PERSONA: Admin
PRE-CONDITIONS:
- System at capacity
TEST DATA: Annual_Income=9999999, Age=99
STEPS:
1. Submit form with max values
EXPECTED RESULT: Accepted or rejected gracefully
PASS CRITERIA: No 500 error
ESTIMATED TIME: 4
NOTES: See model_card.json Age feature importance (34.57%)
"""


@pytest.fixture()
def single_scenario():
    return parse_and_return(SINGLE_SCENARIO_RAW)


def parse_and_return(raw):
    return uat.parse_scenarios(raw)


# ===========================================================================
# parse_scenarios
# ===========================================================================


class TestParseScenarios:

    def test_single_scenario_returns_one_item(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert len(result) == 1

    def test_single_scenario_id(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["title"] == "Successful login with valid credentials"

    def test_single_scenario_type(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "Dashboard is displayed" in result[0]["pass_criteria"]

    def test_single_scenario_estimated_time(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_field_contains_original_block(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_multiple_scenarios_count(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3

    def test_multiple_scenarios_ids(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-STORY1-1", "UAT-STORY1-2", "UAT-STORY1-3"]

    def test_multiple_scenarios_types(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        types_ = [s["type"] for s in result]
        assert "POSITIVE" in types_
        assert "NEGATIVE" in types_
        assert "BOUNDARY" in types_

    def test_multiple_scenarios_personas(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        personas = [s["persona"] for s in result]
        assert "Underwriter" in personas
        assert "Admin" in personas

    # --- Edge cases ---

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = uat.parse_scenarios("This is just some text with no scenario delimiter.")
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = uat.parse_scenarios("===SCENARIO===")
        # block after split will be empty string — no ID, so nothing appended
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Whatever
ESTIMATED TIME: 1
NOTES: none
"""
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_scenario_with_partial_fields(self):
        raw = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial scenario
"""
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-1"
        assert result[0]["title"] == "Partial scenario"
        # Optional fields absent → not in dict
        assert "persona" not in result[0]
        assert "type" not in result[0]

    def test_scenario_with_extra_whitespace_around_values(self):
        raw = """\
===SCENARIO===
ID:    UAT-SPACE-1   
TITLE:   Spaced Title   
TYPE:  NEGATIVE  
PERSONA:   Admin User  
PASS CRITERIA:  Spaced criteria  
ESTIMATED TIME:  10  
NOTES: none
"""
        result = uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"
        assert result[0]["title"] == "Spaced Title"
        assert result[0]["type"] == "NEGATIVE"
        assert result[0]["persona"] == "Admin User"
        assert result[0]["pass_criteria"] == "Spaced criteria"
        assert result[0]["estimated_time"] == "10"

    def test_mixed_valid_and_invalid_scenarios(self):
        raw = """\
===SCENARIO===
TITLE: No ID here
TYPE: POSITIVE

===SCENARIO===
ID: UAT-VALID-1
TITLE: This one is valid
TYPE: POSITIVE
PERSONA: User
PASS CRITERIA: OK
ESTIMATED TIME: 2
NOTES: none
"""
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-VALID-1"

    def test_scenario_raw_content_stored(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        for s in result:
            assert "raw" in s
            assert len(s["raw"]) > 0

    def test_synthetic_data_in_scenario_raw(self):
        """Verify synthetic data values (Age=99, Annual_Income=9999999) survive parse."""
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        boundary = next(s for s in result if s["type"] == "BOUNDARY")
        assert "Age=99" in boundary["raw"]
        assert "Annual_Income=9999999" in boundary["raw"]

    def test_many_scenarios_performance(self):
        """100 scenarios should parse without error."""
        block = """\
===SCENARIO===
ID: UAT-PERF-{n}
TITLE: Performance test scenario {n}
TYPE: POSITIVE
PERSONA: User
PASS CRITERIA: OK
ESTIMATED TIME: 1
NOTES: none
"""
        raw = "".join(block.format(n=i) for i in range(100))
        result = uat.parse_scenarios(raw)
        assert len(result) == 100


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:

    @pytest.fixture()
    def parsed(self):
        return uat.parse_scenarios(MULTI_SCENARIO_RAW)

    def test_returns_string(self, parsed):
        result = uat.build_test_pack_csv(parsed)
        assert isinstance(result, str)

    def test_header_row_present(self, parsed):
        result = uat.build_test_pack_csv(parsed)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert header[0] == "Scenario ID"
        assert "Title" in header
        assert "Type" in header
        assert "Persona" in header
        assert "Pass Criteria" in header
        assert "Est. Time (min)" in header
        assert "Result (PASS/FAIL/BLOCKED)" in header
        assert "Tester" in header
        assert "Notes" in header
        assert "Defect Ref" in header

    def test_header_has_ten_columns(self, parsed):
        result = uat.build_test_pack_csv(parsed)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert len(header) == 10

    def test_row_count_matches_scenario_count(self, parsed):
        result = uat.build_test_pack_csv(parsed)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # 1 header + 3 data rows
        assert len(rows) == 4

    def test_scenario_id_in_first_data_row(self, parsed):
        result = uat.build_test_pack_csv(parsed)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        first_row = next(reader)
        assert first_row[0] == "UAT-STORY1-1"

    def test_result_column_empty_for_tester_to_fill(self, parsed):
        result = uat.build_test_pack_csv(parsed)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        for row in reader:
            assert row[6] == ""   # Result column
            assert row[7] == ""   # Tester column
            assert row[8] == ""   # Notes column
            assert row[9] == ""   # Defect Ref column

    def test_empty_scenario_list(self):
        result = uat.build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # only header

    def test_scenario_with_missing_optional_fields(self):
        scenarios = [{"id": "UAT-MIN-1", "raw": "ID: UAT-MIN-1"}]
        result = uat.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[0] == "UAT-MIN-1"
        # All missing optional fields should be empty strings
        for col in row[1:6]:
            assert col == ""

    def test_special_characters_in_title(self):
        scenarios = [{
            "id": "UAT-SPECIAL-1",
            "title": 'Title with "quotes" and, commas',
            "type": "POSITIVE",
            "persona": "Tester",
            "pass_criteria": "OK",
            "estimated_time": "1",
            "raw": "...",
        }]
        result = uat.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert 'quotes' in row[1]
        assert 'commas' in row[1]

    def test_unicode_characters_in_persona(self):
        """Arabic/Unicode persona names should survive CSV round-trip."""
        scenarios = [{
            "id": "UAT-UNICODE-1",
            "title": "Arabic persona test",
            "type": "POSITIVE",
            "