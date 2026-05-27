"""
Tests for tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
    - build_test_pack_csv(): happy path, empty list, partial scenario data, CSV structure
    - build_test_pack_md(): content generation, version/owner/repo interpolation
    - get_results_csv(): happy path, missing file (FileNotFoundError), malformed response
    - SYSTEM_GENERATE / SYSTEM_ANALYSE prompt constants: presence checks

Mocks used:
    - requests.get (for get_results_csv)
    - shared.call_claude (imported via tool5_uat module)
    - shared.get_repo_files
    - shared.write_output_file
    - shared.send_email
    - shared.write_audit_entry
    - base64.b64decode (via real decoding with mock content)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var setup and live GH token
    - TODO: Test email content produced by Mode A/B end-to-end (needs call_claude stub returning fixture)
    - TODO: Test write_output_file called with correct markdown and CSV paths
    - TODO: Test audit entry format written after successful generate/analyse run
"""

import base64
import csv
import io
import sys
import os
import json
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool5_uat without the
# real shared.py present in every test environment.
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
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer fake"}
    shared.GH_API = "https://api.github.com"
    return shared


# Inject stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now safe to import
import importlib
import tool5_uat  # noqa: E402  (lives in .github/scripts, added to sys.path by the module itself)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-01
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Standard Customer
PRE-CONDITIONS:
- User account exists
TEST DATA: username=cust@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to /login
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: HTTP 200 and dashboard renders
ESTIMATED TIME: 5
NOTES: Requires seeded test account
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-01
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Admin User
PASS CRITERIA: Redirect to /admin
ESTIMATED TIME: 3
NOTES: none

===SCENARIO===
ID: UAT-STORY1-02
TITLE: Login with wrong password
TYPE: NEGATIVE
PERSONA: Standard Customer
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 2
NOTES: check lockout after 5 attempts
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-01
TITLE: Max income boundary value
TYPE: BOUNDARY
PERSONA: Underwriter
PASS CRITERIA: System accepts 9999999
ESTIMATED TIME: 10
NOTES: Annual_Income boundary test; Age=34.5, Risk_Classification check
"""


@pytest.fixture()
def single_scenario_raw():
    return SINGLE_SCENARIO_BLOCK


@pytest.fixture()
def two_scenarios_raw():
    return TWO_SCENARIO_BLOCK


@pytest.fixture()
def sample_scenarios():
    return [
        {
            "id": "UAT-STORY1-01",
            "title": "Happy path login",
            "type": "POSITIVE",
            "persona": "Admin User",
            "pass_criteria": "Redirect to /admin",
            "estimated_time": "3",
            "raw": "raw block content",
        },
        {
            "id": "UAT-STORY1-02",
            "title": "Login with wrong password",
            "type": "NEGATIVE",
            "persona": "Standard Customer",
            "pass_criteria": "Error message displayed",
            "estimated_time": "2",
            "raw": "raw block 2",
        },
    ]


# ===========================================================================
# parse_scenarios()
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert result[0]["id"] == "UAT-STORY1-01"

    def test_single_scenario_title_parsed(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert result[0]["title"] == "Successful login with valid credentials"

    def test_single_scenario_type_parsed(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert result[0]["persona"] == "Standard Customer"

    def test_single_scenario_pass_criteria_parsed(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert result[0]["pass_criteria"] == "HTTP 200 and dashboard renders"

    def test_single_scenario_estimated_time_parsed(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_field_present(self, single_scenario_raw):
        result = tool5_uat.parse_scenarios(single_scenario_raw)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_two_scenarios_returns_two_items(self, two_scenarios_raw):
        result = tool5_uat.parse_scenarios(two_scenarios_raw)
        assert len(result) == 2

    def test_two_scenarios_ids_distinct(self, two_scenarios_raw):
        result = tool5_uat.parse_scenarios(two_scenarios_raw)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-STORY1-01", "UAT-STORY1-02"]

    def test_two_scenarios_types(self, two_scenarios_raw):
        result = tool5_uat.parse_scenarios(two_scenarios_raw)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_boundary_type_parsed(self):
        result = tool5_uat.parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-X-01\nTITLE: Something\nTYPE: POSITIVE\n"
        # No ===SCENARIO=== delimiter → split produces one block with no id after strip logic
        # The block before the first delimiter is discarded
        result = tool5_uat.parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        raw = "===SCENARIO==="
        result = tool5_uat.parse_scenarios(raw)
        # block after delimiter is empty → skipped
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result == []

    def test_scenario_missing_optional_fields_defaults_to_missing(self):
        raw = "===SCENARIO===\nID: UAT-X-01\nTITLE: Minimal\n"
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert "type" not in result[0] or result[0].get("type") is None or result[0].get("type") == ""
        # id must be present
        assert result[0]["id"] == "UAT-X-01"

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-X-01\nTITLE: Real\n"
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-01"

    def test_extra_whitespace_in_id_is_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-STORY3-99  \nTITLE: Test\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-STORY3-99"

    def test_leading_content_before_first_delimiter_ignored(self):
        raw = "Some preamble text\n===SCENARIO===\nID: UAT-A-01\nTITLE: First\n"
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-A-01"

    def test_multiple_scenarios_raw_field_is_individual_block(self, two_scenarios_raw):
        result = tool5_uat.parse_scenarios(two_scenarios_raw)
        # raw should not contain the other scenario's ID
        assert "UAT-STORY1-02" not in result[0]["raw"]
        assert "UAT-STORY1-01" not in result[1]["raw"]

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_are_parsed(self, scenario_type):
        raw = f"===SCENARIO===\nID: UAT-T-01\nTITLE: T\nTYPE: {scenario_type}\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["type"] == scenario_type

    def test_synthetic_underwriting_scenario(self):
        """Scenario using synthetic data from model card (Age, Risk_Classification)."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-UNDERWRITING-01\n"
            "TITLE: Underwriting risk classification for Age=34\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: Underwriter\n"
            "PASS CRITERIA: Risk_Classification returned\n"
            "ESTIMATED TIME: 8\n"
            "NOTES: Annual_Income feature importance 1.017\n"
        )
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-UNDERWRITING-01"
        assert result[0]["type"] == "BOUNDARY"


# ===========================================================================
# build_test_pack_csv()
# ===========================================================================

class TestBuildTestPackCsv:

    def test_returns_string(self, sample_scenarios):
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        assert isinstance(result, str)

    def test_csv_has_header_row(self, sample_scenarios):
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert header[0] == "Scenario ID"
        assert "Title" in header
        assert "Type" in header
        assert "Persona" in header
        assert "Pass Criteria" in header

    def test_csv_result_column_present(self, sample_scenarios):
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert any("Result" in col for col in header)

    def test_csv_tester_column_present(self, sample_scenarios):
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "Tester" in header

    def test_csv_defect_ref_column_present(self, sample_scenarios):
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "Defect Ref" in header

    def test_csv_row_count_matches_scenarios(self, sample_scenarios):
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # header + one row per scenario
        assert len(rows) == len(sample_scenarios) + 1

    def test_csv_first_data_row_id(self, sample_scenarios):
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        first_row = next(reader)
        assert first_row[0] == "UAT-STORY1-01"

    def test_csv_data_row_result_empty(self, sample_scenarios):
        """Result column should be blank — testers fill it in."""
        result = tool5_uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        first_row = next(reader)
        header = list(csv.reader(io.StringIO(result)))[0]
        result_idx = next(i for i, h in enumerate(header) if "Result" in h)
        assert first_row[result_idx] == ""

    def test_empty_scenarios_list_returns_header_only(self):
        result = tool5_uat.build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_partial_scenario_missing_fields_uses_empty_string(self):
        partial = [{"id": "UAT-X-01", "raw": "raw"}]
        result = tool5_uat.build_test_pack_csv(partial)
        reader