"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases (empty input, no ID, multiple scenarios,
      partial fields, delimiters without content)
    - build_test_pack_csv(): structure, header row, data rows, empty list, special characters
    - build_test_pack_md(): markdown structure, version/owner/repo embedding, raw content inclusion
    - get_results_csv(): successful fetch + base64 decode, missing 'content' key (FileNotFoundError),
      HTTP error response shapes

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
    - unittest.mock.patch for `base64.b64decode` where needed
    - No real network calls, no real GitHub API calls

TODOs:
    - TODO: Integration tests for __main__ block require full env-var setup + mocked shared imports
    - TODO: Tests for call_claude, write_output_file, send_email (from shared module) need shared.py
    - TODO: SYSTEM_GENERATE / SYSTEM_ANALYSE prompt content validation against real Claude responses
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
# Stub out the `shared` module before importing tool5_uat so that the import
# does not fail when shared.py is unavailable in the test environment.
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="stub")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now safe to import the module under test
import importlib

# Ensure a clean import even if previously cached
if "tool5_uat" in sys.modules:
    del sys.modules["tool5_uat"]

# Insert the script directory so relative imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

tool5_uat = importlib.import_module("tool5_uat")

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-001
TITLE: Underwriting risk classification happy path
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is authenticated
- Application metadata is available
TEST DATA: Age=35, Annual_Income=75000, Risk_Classification=LOW
STEPS:
1. Navigate to the underwriting portal
2. Submit a new application with the provided test data
3. Review the classification result
EXPECTED RESULT: The system returns Risk_Classification=LOW
PASS CRITERIA: Classification matches expected LOW label
ESTIMATED TIME: 5
NOTES: Depends on CatBoostClassifier model being deployed
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-001
TITLE: Happy path risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Classification matches expected
ESTIMATED TIME: 5
NOTES: none

===SCENARIO===
ID: UAT-RISK-002
TITLE: Unauthorised access attempt
TYPE: NEGATIVE
PERSONA: Anonymous user
PASS CRITERIA: System returns 403 Forbidden
ESTIMATED TIME: 3
NOTES: Security test
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: Missing ID scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Does something
ESTIMATED TIME: 2
NOTES: No ID line present
"""

SCENARIO_PARTIAL_FIELDS = """\
===SCENARIO===
ID: UAT-PARTIAL-001
TITLE: Partially filled scenario
TYPE: BOUNDARY
"""


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenariosSingleBlock:
    def test_returns_list(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert isinstance(result, list)

    def test_single_scenario_count(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-RISK-001"

    def test_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Underwriting risk classification happy path"

    def test_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Underwriter"

    def test_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "LOW" in result[0]["pass_criteria"]

    def test_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_raw_field_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0


class TestParseScenariosTwoBlocks:
    def test_count(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_first_id(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-RISK-001"

    def test_second_id(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[1]["id"] == "UAT-RISK-002"

    def test_types_distinct(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = {s["type"] for s in result}
        assert "POSITIVE" in types_
        assert "NEGATIVE" in types_

    def test_each_has_raw(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        for s in result:
            assert s.get("raw")


# ===========================================================================
# parse_scenarios — edge cases
# ===========================================================================

class TestParseScenariosMissingId:
    def test_scenario_without_id_excluded(self):
        """Scenarios that have no ID line must be excluded from the result."""
        result = parse_scenarios(SCENARIO_WITHOUT_ID)
        assert result == []


class TestParseScenariosEmptyInput:
    def test_empty_string(self):
        result = parse_scenarios("")
        assert result == []

    def test_whitespace_only(self):
        result = parse_scenarios("   \n\n   ")
        assert result == []

    def test_delimiter_only(self):
        result = parse_scenarios("===SCENARIO===")
        assert result == []


class TestParseScenariosPartialFields:
    def test_partial_scenario_included_when_has_id(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-001"

    def test_missing_keys_absent(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        scenario = result[0]
        # Only id, title, type, and raw should be present
        assert "persona" not in scenario
        assert "pass_criteria" not in scenario
        assert "estimated_time" not in scenario

    def test_type_boundary(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        assert result[0]["type"] == "BOUNDARY"


class TestParseScenariosMultipleDelimiters:
    def test_leading_delimiter(self):
        raw = "===SCENARIO===\n" + SINGLE_SCENARIO_BLOCK
        result = parse_scenarios(raw)
        # Should not create an extra empty entry
        assert all(s.get("id") for s in result)

    def test_trailing_delimiter(self):
        raw = SINGLE_SCENARIO_BLOCK + "\n===SCENARIO===\n"
        result = parse_scenarios(raw)
        # The trailing empty block should be skipped
        assert len(result) == 1


class TestParseScenariosFieldValues:
    @pytest.mark.parametrize("field,expected_key,raw_block", [
        ("ID: UAT-X-001\n", "id", "ID: UAT-X-001\nTITLE: Test\n"),
        ("TITLE: My Title\n", "title", "ID: UAT-X-002\nTITLE: My Title\n"),
        ("TYPE: NEGATIVE\n", "type", "ID: UAT-X-003\nTYPE: NEGATIVE\n"),
        ("PERSONA: Admin\n", "persona", "ID: UAT-X-004\nPERSONA: Admin\n"),
        ("PASS CRITERIA: User sees success\n", "pass_criteria",
         "ID: UAT-X-005\nPASS CRITERIA: User sees success\n"),
        ("ESTIMATED TIME: 10\n", "estimated_time",
         "ID: UAT-X-006\nESTIMATED TIME: 10\n"),
    ])
    def test_field_parsing(self, field, expected_key, raw_block):
        full_block = f"===SCENARIO===\n{raw_block}"
        result = parse_scenarios(full_block)
        assert len(result) == 1
        expected_value = field.split(":", 1)[1].strip()
        assert result[0][expected_key] == expected_value


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsvStructure:
    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0][0] == "Scenario ID"
        assert rows[0][1] == "Title"

    def test_header_has_ten_columns(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows[0]) == 10

    def test_header_result_column(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert "Result (PASS/FAIL/BLOCKED)" in rows[0]

    def test_empty_scenarios_only_header(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # just the header

    def test_single_scenario_row(self):
        scenarios = [{"id": "UAT-001", "title": "Test", "type": "POSITIVE",
                      "persona": "Admin", "pass_criteria": "Works",
                      "estimated_time": "5"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 2  # header + 1 data row

    def test_scenario_id_in_first_column(self):
        scenarios = [{"id": "UAT-001", "title": "T", "type": "POSITIVE",
                      "persona": "Admin", "pass_criteria": "OK",
                      "estimated_time": "3"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-001"

    def test_result_column_empty_for_new_scenario(self):
        scenarios = [{"id": "UAT-001", "title": "T", "type": "POSITIVE",
                      "persona": "Admin", "pass_criteria": "OK",
                      "estimated_time": "3"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        # Column index 6 is "Result (PASS/FAIL/BLOCKED)"
        assert rows[1][6] == ""

    def test_tester_column_empty(self):
        scenarios = [{"id": "UAT-002"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][7] == ""

    def test_defect_ref_column_empty(self):
        scenarios = [{"id": "UAT-002"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][9] == ""

    def test_multiple_scenarios_correct_count(self):
        scenarios = [{"id": f"UAT-{i:03d}"} for i in range(5)]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 6  # header + 5

    def test_missing_optional_fields_use_empty_string(self):
        """Scenario with only 'id' should not raise and fills empty strings."""
        scenarios = [{"id": "UAT-MISSING"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][1] == ""  # title
        assert rows[1][2] == ""  # type

    def test_special_characters_in_title(self):
        scenarios = [{"id": "UAT-SC-001",
                      "title": 'Title with "quotes" and, commas',
                      "type": "POSITIVE", "persona": "Tester",
                      "pass_criteria": "ok", "estimated_time": "1"}]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_synthetic_data_customer_segment(self):
        """Use synthetic-data-like values as test data fields."""
        scenarios = [
            {"id": "UAT-RISK-001",
             "title": "Underwriting Risk Classification — CatBoostClassifier",
             "type": "POSITIVE",
             "persona": "Underwriter",
             "pass_criteria": "Risk_Classification=LOW returned",
             "estimated_time": "5"},
            {"id": "UAT-RISK-002",
             "title": "Unauthorised customer access",
             "type": "NEGATIVE",
             "persona": "Anonymous",
             "pass_criteria": "403 Forbidden",