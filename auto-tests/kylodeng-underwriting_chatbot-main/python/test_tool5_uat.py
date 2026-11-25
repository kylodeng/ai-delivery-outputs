"""
Tests for .github/scripts/tool5_uat.py

What is tested:
- parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
- build_test_pack_csv(): correct headers, row content, empty list, special characters
- build_test_pack_md(): correct markdown structure, timestamp format, content inclusion
- get_results_csv(): happy path with base64 content, missing file error, API error shapes
- Integration between parse_scenarios and build_test_pack_csv

Mocks used:
- unittest.mock.patch for requests.get (GitHub API calls)
- unittest.mock.patch for shared module imports (call_claude, get_repo_files,
  write_output_file, send_email, email_html, write_audit_entry)
- base64 encoding/decoding tested directly

TODOs:
- TODO: Full __main__ block integration test requires env vars + all shared stubs wired together
- TODO: test for call_claude interactions requires knowing exact prompt construction in __main__
- TODO: test for write_output_file and send_email flows requires __main__ refactor into callable fn
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
# Stub out the `shared` module before importing tool5_uat so that the import
# at the top of the source file does not fail in the test environment.
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
_shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Ensure the scripts directory is on the path so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))

# Now import the module under test
import importlib

# We need to import from the actual file path; use importlib for safety
_script_path = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
)

# If the file exists, load it; otherwise fall back to a plain import
# (CI will always have the file present)
if os.path.exists(_script_path):
    import importlib.util

    _spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
    tool5_uat = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(tool5_uat)
else:
    import tool5_uat  # type: ignore

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriting submission
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is online
- User is authenticated
TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=Low
STEPS:
1. Navigate to submission form
2. Enter valid customer data
3. Click Submit
EXPECTED RESULT: Risk classification is returned within 5 seconds
PASS CRITERIA: Status 200 and Risk_Classification field present
ESTIMATED TIME: 5
NOTES: Requires model endpoint to be running
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path submission
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Risk returned
ESTIMATED TIME: 3
NOTES: none

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Unauthorised access attempt
TYPE: NEGATIVE
PERSONA: Anonymous User
PASS CRITERIA: 401 returned
ESTIMATED TIME: 2
NOTES: none
"""

MISSING_ID_BLOCK = """\
===SCENARIO===
TITLE: Missing ID scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Should be skipped
ESTIMATED TIME: 1
NOTES: no id here
"""

BOUNDARY_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Max income boundary
TYPE: BOUNDARY
PERSONA: Senior Underwriter
PASS CRITERIA: System accepts max value without error
ESTIMATED TIME: 4
NOTES: Use Annual_Income=9999999
"""


# ---------------------------------------------------------------------------
# parse_scenarios — happy path
# ---------------------------------------------------------------------------


class TestParseScenarios:
    def test_single_scenario_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful underwriting submission"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Status 200 and Risk_Classification field present"
        assert s["estimated_time"] == "5"

    def test_single_scenario_contains_raw(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "STEPS:" in result[0]["raw"]

    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_scenario_types_preserved(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_boundary_type_scenario(self):
        result = parse_scenarios(BOUNDARY_BLOCK)
        assert len(result) == 1
        assert result[0]["type"] == "BOUNDARY"

    def test_scenario_missing_id_is_excluded(self):
        """Scenarios without an ID must not appear in the output list."""
        result = parse_scenarios(MISSING_ID_BLOCK)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """Text with no ===SCENARIO=== delimiter produces no results."""
        result = parse_scenarios("Just some random text without any scenario markers.")
        assert result == []

    def test_only_delimiter_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===")
        assert result == []

    def test_whitespace_only_between_delimiters_excluded(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-X-1\nTITLE: Real\n"
        result = parse_scenarios(raw)
        # Only the scenario with an ID should be returned
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"

    def test_missing_optional_fields_default_to_missing_key(self):
        """Fields not present in the block should simply be absent (not raise)."""
        minimal = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = parse_scenarios(minimal)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        assert "title" not in s
        assert "type" not in s

    def test_multiple_scenarios_mixed_validity(self):
        raw = TWO_SCENARIO_BLOCK + MISSING_ID_BLOCK
        result = parse_scenarios(raw)
        assert len(result) == 2  # MISSING_ID_BLOCK excluded

    def test_id_with_extra_whitespace_is_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE: Whitespace test\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"

    def test_title_with_extra_whitespace_is_stripped(self):
        raw = "===SCENARIO===\nID: UAT-WS-2\nTITLE:   Lots of spaces   \n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Lots of spaces"

    def test_scenario_with_all_fields(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        s = result[0]
        expected_keys = {"id", "title", "type", "persona", "pass_criteria", "estimated_time", "raw"}
        assert expected_keys.issubset(s.keys())

    @pytest.mark.parametrize("type_val", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_accepted(self, type_val):
        raw = f"===SCENARIO===\nID: UAT-T-1\nTYPE: {type_val}\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == type_val

    def test_many_scenarios_count(self):
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BIG-{i}\nTITLE: Scenario {i}\n"
            for i in range(20)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 20

    def test_raw_field_contains_all_lines(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        raw = result[0]["raw"]
        assert "PRE-CONDITIONS:" in raw
        assert "STEPS:" in raw
        assert "EXPECTED RESULT:" in raw

    def test_non_field_lines_do_not_raise(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-SAFE-1\n"
            "Some arbitrary text that is not a field\n"
            "Another random line\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SAFE-1"


# ---------------------------------------------------------------------------
# build_test_pack_csv
# ---------------------------------------------------------------------------


class TestBuildTestPackCsv:
    def _parse_csv(self, csv_text: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_text)))

    def test_header_row_correct(self):
        csv_text = build_test_pack_csv([])
        rows = self._parse_csv(csv_text)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_empty_scenarios_produces_header_only(self):
        csv_text = build_test_pack_csv([])
        rows = self._parse_csv(csv_text)
        assert len(rows) == 1  # only header

    def test_single_scenario_row(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        assert len(rows) == 2  # header + 1 data row
        data = rows[1]
        assert data[0] == "UAT-STORY1-1"
        assert data[1] == "Successful underwriting submission"
        assert data[2] == "POSITIVE"
        assert data[3] == "Underwriter"

    def test_result_tester_notes_defect_columns_are_empty(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        data_row = rows[1]
        # Columns 6-9 (Result, Tester, Notes, Defect Ref) must be empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_two_scenarios_produce_two_data_rows(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        assert len(rows) == 3

    def test_scenario_with_missing_fields_uses_empty_string(self):
        scenarios = [{"id": "UAT-X-1"}]  # only id, rest missing
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        data = rows[1]
        assert data[0] == "UAT-X-1"
        assert data[1] == ""  # title missing → empty
        assert data[5] == ""  # estimated_time missing → empty

    def test_special_characters_in_title(self):
        scenarios = [{"id": "UAT-SC-1", "title": 'Title with "quotes" and, commas'}]
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_unicode_in_persona(self):
        scenarios = [{"id": "UAT-AR-1", "persona": "مستخدم عربي"}]
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        assert rows[1][3] == "مستخدم عربي"

    def test_estimated_time_preserved(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        assert rows[1][5] == "5"

    def test_csv_output_is_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_pass_criteria_preserved(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        csv_text = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_text)
        assert rows[1][4] == "Status 200 and Risk_Classification field present"

    def test_large_scenario_list(self):
        scenarios = [
            {"id": f"UAT-BIG-{i}", "title": f"Scenario {i}", "type": "POSITIVE