"""
Tests for tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input, no delimiter, multiple scenarios
  - build_test_pack_csv(): correct headers, row count, field mapping, empty scenarios list
  - build_test_pack_md(): content structure, version/owner/repo substitution, timestamp presence
  - get_results_csv(): happy path (base64 content), missing file (FileNotFoundError)

Mocks used:
  - requests.get (for get_results_csv)
  - shared module functions: call_claude, get_repo_files, write_output_file, send_email,
    email_html, write_audit_entry, clean_json (imported transitively)
  - os.environ patched where needed

TODOs:
  - Integration test for __main__ block requires full env setup and real GitHub tokens
  - Tests for Mode A / Mode B full pipeline (call_claude round-trip) require a real or
    deeply-mocked Claude client
"""

import base64
import csv
import io
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, Mock

# ---------------------------------------------------------------------------
# Stub out the 'shared' module so we don't need the real file on import
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="")
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

# Now safe to import the module under test
import importlib
import tool5_uat as uut


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Admin User
PRE-CONDITIONS:
- System is running
- User has valid credentials
TEST DATA: username=admin@example.com, password=Passw0rd!
STEPS:
1. Navigate to /login
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Redirect to dashboard
PASS CRITERIA: Dashboard page loads with user name displayed
ESTIMATED TIME: 3
NOTES: None
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Valid application submission
TYPE: POSITIVE
PERSONA: Insurance Applicant
PRE-CONDITIONS:
- Application form loaded
TEST DATA: Annual_Income=75000, Age=34, Employment_Status=permanent
STEPS:
1. Fill form
2. Submit
EXPECTED RESULT: Confirmation message shown
PASS CRITERIA: Confirmation code returned
ESTIMATED TIME: 5
NOTES: Uses synthetic underwriting data
===SCENARIO===
ID: UAT-STORY2-2
TITLE: Missing mandatory field
TYPE: NEGATIVE
PERSONA: Insurance Applicant
PRE-CONDITIONS:
- Application form loaded
TEST DATA: Annual_Income=, Age=34
STEPS:
1. Leave income blank
2. Submit
EXPECTED RESULT: Validation error displayed
PASS CRITERIA: Error message references income field
ESTIMATED TIME: 2
NOTES: Boundary test
===SCENARIO===
ID: UAT-STORY2-3
TITLE: Maximum income boundary
TYPE: BOUNDARY
PERSONA: Insurance Applicant
PRE-CONDITIONS:
- Application form loaded
TEST DATA: Annual_Income=9999999999, Age=34
STEPS:
1. Enter max value
2. Submit
EXPECTED RESULT: System accepts or gracefully rejects
PASS CRITERIA: No unhandled exception
ESTIMATED TIME: 2
NOTES: Check model_card.json feature importance for Annual_Income
"""

SCENARIO_NO_ID = """\
===SCENARIO===
TITLE: Orphan scenario without ID
TYPE: POSITIVE
PERSONA: Admin
STEPS:
1. Do something
EXPECTED RESULT: Something happens
PASS CRITERIA: It happened
ESTIMATED TIME: 1
NOTES: No ID line present
"""


def _make_scenario(**kwargs):
    base = {
        "id": "UAT-X-1",
        "title": "Sample Title",
        "type": "POSITIVE",
        "persona": "Tester",
        "pass_criteria": "It works",
        "estimated_time": "5",
        "raw": "raw block text",
    }
    base.update(kwargs)
    return base


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_parsed(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["title"] == "Happy path login"

    def test_single_scenario_type_parsed(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["persona"] == "Admin User"

    def test_single_scenario_pass_criteria_parsed(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["pass_criteria"] == "Dashboard page loads with user name displayed"

    def test_single_scenario_estimated_time_parsed(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["estimated_time"] == "3"

    def test_single_scenario_raw_field_present(self):
        result = uut.parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "raw" in result[0]
        assert "Happy path login" in result[0]["raw"]

    def test_multiple_scenarios_count(self):
        result = uut.parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3

    def test_multiple_scenarios_ids(self):
        result = uut.parse_scenarios(MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-STORY2-1", "UAT-STORY2-2", "UAT-STORY2-3"]

    def test_multiple_scenarios_types(self):
        result = uut.parse_scenarios(MULTI_SCENARIO_RAW)
        types_ = [s["type"] for s in result]
        assert "POSITIVE" in types_
        assert "NEGATIVE" in types_
        assert "BOUNDARY" in types_

    def test_scenario_without_id_is_excluded(self):
        result = uut.parse_scenarios(SCENARIO_NO_ID)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = uut.parse_scenarios("")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = uut.parse_scenarios("   \n\t\n  ")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-X-1\nTITLE: Something\nTYPE: POSITIVE"
        result = uut.parse_scenarios(raw)
        # Without the delimiter the block has no ID-bearing scenario after split
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = uut.parse_scenarios("===SCENARIO===\n\n===SCENARIO===\n")
        # Both blocks are empty after strip — no id → excluded
        assert result == []

    def test_scenario_missing_optional_fields_still_included(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal\n"
        result = uut.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert result[0].get("type") is None

    def test_leading_text_before_first_delimiter_ignored(self):
        raw = "Some preamble text\n" + SINGLE_SCENARIO_RAW
        result = uut.parse_scenarios(raw)
        assert len(result) == 1

    def test_extra_whitespace_around_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace test   \n"
        result = uut.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"

    def test_synthetic_data_in_raw_preserved(self):
        """Synthetic underwriting data should survive in the raw field."""
        result = uut.parse_scenarios(MULTI_SCENARIO_RAW)
        raw_text = result[0]["raw"]
        assert "Annual_Income" in raw_text or "UAT-STORY2-1" in raw_text

    def test_duplicate_ids_both_included(self):
        raw = (
            "===SCENARIO===\nID: UAT-DUP-1\nTITLE: First\n"
            "===SCENARIO===\nID: UAT-DUP-1\nTITLE: Second\n"
        )
        result = uut.parse_scenarios(raw)
        assert len(result) == 2

    def test_non_matching_lines_do_not_crash(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-EXTRA-1\n"
            "TITLE: Extra lines\n"
            "RANDOM LINE: something\n"
            "ANOTHER: value\n"
        )
        result = uut.parse_scenarios(raw)
        assert len(result) == 1

    def test_large_input_performance(self):
        """100 scenarios should parse without error."""
        blocks = []
        for i in range(100):
            blocks.append(
                f"===SCENARIO===\n"
                f"ID: UAT-PERF-{i}\n"
                f"TITLE: Scenario {i}\n"
                f"TYPE: POSITIVE\n"
                f"PERSONA: User\n"
                f"PASS CRITERIA: Criteria {i}\n"
                f"ESTIMATED TIME: 2\n"
            )
        raw = "\n".join(blocks)
        result = uut.parse_scenarios(raw)
        assert len(result) == 100


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_returns_string(self):
        result = uut.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_correct(self):
        rows = self._parse_csv(uut.build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        rows = self._parse_csv(uut.build_test_pack_csv([]))
        assert len(rows) == 1

    def test_single_scenario_produces_two_rows(self):
        scenarios = [_make_scenario()]
        rows = self._parse_csv(uut.build_test_pack_csv(scenarios))
        assert len(rows) == 2

    def test_scenario_fields_mapped_correctly(self):
        s = _make_scenario(
            id="UAT-MAP-1",
            title="Mapping Test",
            type="NEGATIVE",
            persona="End User",
            pass_criteria="Fields match",
            estimated_time="7",
        )
        rows = self._parse_csv(uut.build_test_pack_csv([s]))
        data_row = rows[1]
        assert data_row[0] == "UAT-MAP-1"
        assert data_row[1] == "Mapping Test"
        assert data_row[2] == "NEGATIVE"
        assert data_row[3] == "End User"
        assert data_row[4] == "Fields match"
        assert data_row[5] == "7"

    def test_result_tester_notes_defect_columns_empty(self):
        scenarios = [_make_scenario()]
        rows = self._parse_csv(uut.build_test_pack_csv(scenarios))
        data_row = rows[1]
        # Columns 6-9 should be empty (tester-fill)
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(id=f"UAT-{i}-1") for i in range(5)]
        rows = self._parse_csv(uut.build_test_pack_csv(scenarios))
        assert len(rows) == 6  # header + 5 data rows

    def test_missing_keys_default_to_empty_string(self):
        """Scenario dict with no keys should not raise."""
        rows = self._parse_csv(uut.build_test_pack_csv([{}]))
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == ""
        assert data_row[1] == ""

    def test_csv_contains_ten_columns(self):
        scenarios = [_make_scenario()]
        rows = self._parse_csv(uut.build_test_pack_csv(scenarios))
        for row in rows:
            assert len(row) == 10

    def test_synthetic_underwriting_scenario(self):
        """Use synthetic data scenario as input."""
        s = _make_scenario(
            id="UAT-STORY2-1",
            title="Valid application submission",
            type="POSITIVE",
            persona="Insurance Applicant",
            pass_criteria="Confirmation code returned",
            estimated_time="5",
        )
        rows = self._parse_csv(uut.build_test_pack_csv([s]))
        assert rows[1][0] == "UAT-STORY2-1"
        assert rows[1][3] == "Insurance Applicant"

    def test_csv_is_parseable_with_commas_in_fields(self):
        s = _make_scenario(
            title="Title, with, commas",
            pass_criteria="Comma, in, criteria",
        )
        rows = self._parse_csv(uut.build_test_pack_csv([s]))