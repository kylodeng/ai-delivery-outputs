"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios: happy path, edge cases, missing fields, empty input, malformed blocks
  - build_test_pack_csv: correct CSV structure, all fields present, empty scenarios list
  - build_test_pack_md: correct Markdown structure, metadata injection
  - get_results_csv: successful fetch + base64 decode, missing content key, HTTP errors

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for `shared` module functions (call_claude, write_output_file, etc.)
  - base64 encoding helpers for synthetic GitHub API responses

TODOs:
  - TODO: Integration test for full __main__ block requires real env vars + network — stub provided
  - TODO: Test SYSTEM_GENERATE prompt adequacy requires Claude API access — stub provided
  - TODO: Test SYSTEM_ANALYSE JSON schema validation requires Claude API access — stub provided
  - TODO: build_test_pack_md timestamp is UTC-now; freeze time for deterministic assertion
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
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal `shared` stub so tool5_uat imports without error
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = lambda s: s
shared_stub.call_claude = MagicMock(return_value="")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib

tool5 = importlib.import_module(
    "tool5_uat" if "tool5_uat" in sys.modules else ".github.scripts.tool5_uat".replace("/", ".")
)

# Fallback: direct file load via importlib.util
if tool5 is None:  # pragma: no cover
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "tool5_uat",
        os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"),
    )
    tool5 = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(tool5)

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Registered policyholder
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: username=test@sunlife.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click submit
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: User sees dashboard within 3 seconds
ESTIMATED TIME: 5
NOTES: Check SSO integration
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Apply for Generations II policy - happy path
TYPE: POSITIVE
PERSONA: New applicant
PRE-CONDITIONS:
- User is authenticated
TEST DATA: product_name=Generations II, age=35, sum_assured=500000
STEPS:
1. Navigate to product page
2. Select Generations II
3. Fill application form
EXPECTED RESULT: Application submitted successfully
PASS CRITERIA: Confirmation number displayed
ESTIMATED TIME: 10
NOTES: None
===SCENARIO===
ID: UAT-GEN2-2
TITLE: Apply with invalid age (below minimum)
TYPE: NEGATIVE
PERSONA: New applicant
PRE-CONDITIONS:
- User is authenticated
TEST DATA: age=15
STEPS:
1. Enter age 15
2. Submit form
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: Boundary: minimum age check
===SCENARIO===
ID: UAT-GEN2-3
TITLE: Apply with maximum sum assured boundary value
TYPE: BOUNDARY
PERSONA: High-net-worth applicant
PRE-CONDITIONS:
- User is authenticated
TEST DATA: sum_assured=99999999
STEPS:
1. Enter maximum sum assured
2. Submit form
EXPECTED RESULT: System accepts or rejects gracefully
PASS CRITERIA: No system crash; clear message shown
ESTIMATED TIME: 5
NOTES: [TESTER: verify maximum allowed value]
"""

SCENARIO_MISSING_ID = """\
===SCENARIO===
TITLE: Orphaned scenario without ID
TYPE: POSITIVE
PERSONA: Admin
STEPS:
1. Do something
EXPECTED RESULT: Something happens
PASS CRITERIA: Pass
ESTIMATED TIME: 2
NOTES: None
"""

SCENARIO_PARTIAL_FIELDS = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial scenario
TYPE: BOUNDARY
"""


def _make_gh_api_response(content_str: str, include_content: bool = True) -> dict:
    """Build a mock GitHub API response dict for a file."""
    payload = {}
    if include_content:
        payload["content"] = base64.b64encode(content_str.encode()).decode()
    return payload


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================


class TestParseScenarios:
    """Unit tests for parse_scenarios()."""

    def test_happy_path_single_scenario(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Registered policyholder"
        assert s["pass_criteria"] == "User sees dashboard within 3 seconds"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_happy_path_multiple_scenarios(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-GEN2-1" in ids
        assert "UAT-GEN2-2" in ids
        assert "UAT-GEN2-3" in ids

    def test_types_correctly_parsed(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        type_map = {s["id"]: s["type"] for s in result}
        assert type_map["UAT-GEN2-1"] == "POSITIVE"
        assert type_map["UAT-GEN2-2"] == "NEGATIVE"
        assert type_map["UAT-GEN2-3"] == "BOUNDARY"

    def test_persona_correctly_parsed(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        personas = {s["id"]: s["persona"] for s in result}
        assert personas["UAT-GEN2-3"] == "High-net-worth applicant"

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(SCENARIO_MISSING_ID)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("Some random text without any delimiter")
        assert result == []

    def test_partial_fields_scenario(self):
        """Scenario with ID but missing most fields is included with defaults."""
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Partial scenario"
        assert s["type"] == "BOUNDARY"
        # Missing fields should not be present (or be absent)
        assert s.get("persona") is None or "persona" not in s

    def test_raw_field_contains_original_block(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "Successful login with valid credentials" in result[0]["raw"]
        assert "STEPS:" in result[0]["raw"]

    def test_extra_whitespace_around_delimiter(self):
        raw = "\n\n===SCENARIO===\nID: UAT-WS-1\nTITLE: Whitespace test\nTYPE: POSITIVE\nPERSONA: Tester\nPASS CRITERIA: Passes\nESTIMATED TIME: 1\n\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_delimiter_only_no_content(self):
        result = parse_scenarios("===SCENARIO===")
        # Block after split is empty / no ID
        assert result == []

    def test_multiple_scenarios_raw_field_preserved(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        for s in result:
            assert len(s["raw"]) > 0

    @pytest.mark.parametrize("scenario_id,expected_time", [
        ("UAT-GEN2-1", "10"),
        ("UAT-GEN2-2", "3"),
        ("UAT-GEN2-3", "5"),
    ])
    def test_estimated_time_parametrized(self, scenario_id, expected_time):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        time_map = {s["id"]: s["estimated_time"] for s in result}
        assert time_map[scenario_id] == expected_time

    def test_insurance_synthetic_data_scenario(self):
        """Use synthetic insurance product data as test data values in scenario."""
        raw = """\
===SCENARIO===
ID: UAT-INS-1
TITLE: Retrieve Generations II product brochure
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Product database loaded with Generations II data
TEST DATA: product_name=Generations II, doc_type=product_brochure, linked_product=Generations II
STEPS:
1. Search for Generations II product
2. Request brochure
EXPECTED RESULT: Brochure PDF returned
PASS CRITERIA: PDF download initiated
ESTIMATED TIME: 3
NOTES: Uses Sun Life Hong Kong product catalogue
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-INS-1"
        assert result[0]["persona"] == "Policyholder"

    def test_hospital_list_scenario(self):
        """Synthetic data: designated hospitals list scenario."""
        raw = """\
===SCENARIO===
ID: UAT-HOSP-1
TITLE: Search designated hospital in mainland China
TYPE: POSITIVE
PERSONA: Health policyholder
PRE-CONDITIONS:
- Mainland China hospital list loaded
TEST DATA: product_name=List of Designated Hospitals in Mainland China, doc_type=supplementary, linked_product=health_products
STEPS:
1. Navigate to hospital search
2. Enter city name
3. Submit search
EXPECTED RESULT: List of Class 3 hospitals returned
PASS CRITERIA: At least one result shown
ESTIMATED TIME: 4
NOTES: Should cover all Class 3 hospitals
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["type"] == "POSITIVE"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    """Unit tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_header_row_correct(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1

    def test_single_scenario_row(self):
        scenarios = [parse_scenarios(SINGLE_SCENARIO_BLOCK)[0]]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 2  # header + 1 data row
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Successful login with valid credentials"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Registered policyholder"
        assert data_row[4] == "User sees dashboard within 3 seconds"
        assert data_row[5] == "5"

    def test_result_tester_defect_columns_empty(self):
        """Columns for tester to fill in should be blank."""
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[6] == ""   # Result
        assert data_row[7] == ""   # Tester
        assert data_row[8] == ""   # Notes
        assert data_row[9] == ""   # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = parse_scenarios(MULTI_SCENARIO_RAW)
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 4  # header + 3

    def test_missing_fields_default_to_empty_string(self):
        """Scenario with missing optional fields should not raise KeyError."""
        scenarios = [{"id": "UAT-X-1", "raw": "block"}]  # minimal dict
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-X-1"
        # All other fields default to empty string
        assert data_row[1] == ""
        assert data_row[2] == ""

    def test_special_characters_in_fields(self):
        """CSV writer should handle commas and quotes in field values."""
        scenarios =