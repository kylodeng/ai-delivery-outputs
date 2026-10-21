"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases (empty input, missing fields, no delimiter, multiple scenarios)
    - build_test_pack_csv(): happy path, empty scenarios, partial/missing fields, CSV structure/headers
    - build_test_pack_md(): happy path, content inclusion, timestamp format
    - get_results_csv(): happy path, missing file (FileNotFoundError), malformed response

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls)
    - unittest.mock.patch for shared module functions: call_claude, get_repo_files,
      write_output_file, send_email, email_html, write_audit_entry
    - base64 encoding/decoding exercised directly (no mock needed)

TODOs:
    - TODO: Integration test for full __main__ execution requires env vars + live GitHub token
    - TODO: Test build_test_pack_md timestamp accuracy (requires time freezing via freezegun)
    - TODO: Test parse_scenarios with Claude's real output format once prompt is finalised
    - TODO: Test get_results_csv with pagination or large files (GitHub API 1MB limit)
"""

import base64
import csv
import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: ensure the scripts directory is importable without a real `shared`
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")

# Create a minimal fake `shared` module so tool5_uat imports without error
import types

fake_shared = types.ModuleType("shared")
fake_shared.clean_json = lambda x: x
fake_shared.call_claude = MagicMock()
fake_shared.get_repo_files = MagicMock()
fake_shared.write_output_file = MagicMock()
fake_shared.send_email = MagicMock()
fake_shared.email_html = MagicMock()
fake_shared.write_audit_entry = MagicMock()
fake_shared.OUTPUT_REPO_OWNER = "test-owner"
fake_shared.OUTPUT_REPO = "test-output-repo"
fake_shared.GH_HEADERS = {"Authorization": "Bearer fake-token"}
fake_shared.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", fake_shared)

# Now import the module under test
sys.path.insert(0, SCRIPTS_DIR)
import tool5_uat as uat


# ===========================================================================
# Fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful policy lookup
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User is authenticated
- Policy exists in the system
TEST DATA: policy_id=GEN2-001, dob=1980-01-01
STEPS:
1. Navigate to policy lookup screen
2. Enter policy ID
3. Click Search
EXPECTED RESULT: Policy details are displayed
PASS CRITERIA: Policy summary screen loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires Generations II product data
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Reject invalid policy ID
TYPE: NEGATIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User is authenticated
TEST DATA: policy_id=INVALID-999
STEPS:
1. Enter invalid policy ID
2. Click Search
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Inline validation error shown
ESTIMATED TIME: 3
NOTES: Edge case
===SCENARIO===
ID: UAT-STORY2-2
TITLE: Empty policy ID boundary
TYPE: BOUNDARY
PERSONA: Policyholder
PRE-CONDITIONS:
- User is authenticated
TEST DATA: policy_id=
STEPS:
1. Leave policy ID blank
2. Click Search
EXPECTED RESULT: Validation prevents submission
PASS CRITERIA: Submit button disabled or error shown
ESTIMATED TIME: 2
NOTES: None
"""

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario
TYPE: POSITIVE
"""


# ===========================================================================
# Tests for parse_scenarios()
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_parsed_correctly(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful policy lookup"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Policyholder"
        assert s["pass_criteria"] == "Policy summary screen loads within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_single_scenario_raw_field_populated(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_two_scenarios_parsed(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        ids = [s["id"] for s in result]
        assert "UAT-STORY2-1" in ids
        assert "UAT-STORY2-2" in ids

    def test_scenario_types_captured(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        types_found = {s["type"] for s in result}
        assert "NEGATIVE" in types_found
        assert "BOUNDARY" in types_found

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-X-1\nTITLE: Something\n"
        result = uat.parse_scenarios(raw)
        # No ===SCENARIO=== delimiter → splits into one block that is not empty
        # but has no ID line in an identifiable block after splitting on delimiter
        # The block before first delimiter has no ID → should be filtered
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_minimal_scenario_included_when_id_present(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"

    def test_missing_optional_fields_default_absent(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        s = result[0]
        # persona, pass_criteria, estimated_time not in block → key absent
        assert "persona" not in s
        assert "pass_criteria" not in s
        assert "estimated_time" not in s

    def test_whitespace_only_blocks_ignored(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: Real\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_leading_delimiter_not_double_counted(self):
        """Raw output often starts with ===SCENARIO=== before first scenario."""
        raw = TWO_SCENARIO_BLOCK  # already starts with ===SCENARIO===
        result = uat.parse_scenarios(raw)
        assert len(result) == 2

    def test_many_scenarios(self):
        blocks = ""
        for i in range(10):
            blocks += f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
        result = uat.parse_scenarios(blocks)
        assert len(result) == 10

    def test_insurance_synthetic_data_in_raw(self):
        """Ensure synthetic data values (Generations II) survive round-trip."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-GEN2-1\n"
            "TITLE: Verify Generations II policy benefits\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Policyholder\n"
            "TEST DATA: product=Generations II, doc_type=product_brochure\n"
            "PASS CRITERIA: Benefit summary matches PDF annotation\n"
            "ESTIMATED TIME: 10\n"
        )
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert "Generations II" in result[0]["raw"]

    def test_id_with_extra_spaces_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SP-1   \nTITLE: Spaced\n"
        result = uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SP-1"

    def test_title_with_extra_spaces_stripped(self):
        raw = "===SCENARIO===\nID: UAT-T-1\nTITLE:   My Title   \n"
        result = uat.parse_scenarios(raw)
        assert result[0]["title"] == "My Title"


# ===========================================================================
# Tests for build_test_pack_csv()
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

    def test_empty_scenarios_returns_header_only(self):
        result = uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # just header

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-STORY1-1",
                "title": "Successful policy lookup",
                "type": "POSITIVE",
                "persona": "Policyholder",
                "pass_criteria": "Loads within 3s",
                "estimated_time": "5",
            }
        ]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Successful policy lookup"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Policyholder"
        assert data_row[4] == "Loads within 3s"
        assert data_row[5] == "5"

    def test_result_tester_defect_columns_empty(self):
        scenarios = [{"id": "UAT-1", "title": "T", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "C", "estimated_time": "1"}]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[6] == ""   # Result
        assert data_row[7] == ""   # Tester
        assert data_row[8] == ""   # Notes
        assert data_row[9] == ""   # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [{"id": f"UAT-{i}", "title": f"T{i}"} for i in range(5)]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_fields_produce_empty_strings(self):
        scenarios = [{"id": "UAT-X-1"}]  # only id, all others missing
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-X-1"
        assert data_row[1] == ""
        assert data_row[2] == ""

    def test_returns_string(self):
        result = uat.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_csv_is_valid_parseable(self):
        scenarios = [
            {"id": "UAT-CSV-1", "title": "Comma, in title", "type": "NEGATIVE",
             "persona": "Admin", "pass_criteria": 'Error "shown"', "estimated_time": "4"}
        ]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        # CSV library should handle quoting correctly
        assert rows[1][1] == "Comma, in title"
        assert rows[1][4] == 'Error "shown"'

    def test_row_has_ten_columns(self):
        scenarios = [{"id": "UAT-COL-1", "title": "T", "type": "POSITIVE",
                      "persona": "User", "pass_criteria": "C", "estimated_time": "2"}]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows[1]) == 10

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_scenario_types_written(self, scenario_type):
        scenarios = [{"id": "UAT-TYPE-1", "type": scenario_type}]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][2] == scenario_type

    def test_insurance_product_persona(self):
        """Synthetic data: Generations II policyholder persona."""
        scenarios = [
            {
                "id": "UAT-GEN2-1",
                "title": "Verify Generations II double bonus",
                "type": "POSITIVE",
                "persona": "Generations II Policyholder",
                "pass_criteria": "Double bonus amount matches policy schedule",
                "estimated_time": "8",
            }
        ]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][3] == "Generations II Policyholder"


# ===========================================================================
# Tests for build_test_pack_md()
# ===========================================================================

class TestBuildTestPackMd:

    def test_returns_string(self):
        result = uat.build_test_pack_md("Some raw content", "owner", "repo", "1.0.0")
        assert isinstance(result, str)

    def test_contains_repo_owner(self):
        result = uat.build_test_pack_md("raw", "my-owner", "my-repo", "2.0.0")
        assert "my-owner/my-repo