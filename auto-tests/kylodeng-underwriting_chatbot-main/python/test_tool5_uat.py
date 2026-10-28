"""
Tests for tool5_uat.py
======================

What is tested:
- parse_scenarios(): happy path, empty input, missing fields, multiple scenarios,
  blocks without IDs, boundary values
- build_test_pack_csv(): correct headers, row content, empty scenarios list,
  missing fields in scenario dicts
- build_test_pack_md(): correct markdown structure, version/owner/repo embedding,
  UTC timestamp presence
- get_results_csv(): successful base64 decode, missing content key (FileNotFoundError),
  HTTP-level error responses

Mocks used:
- unittest.mock.patch for `requests.get` (GitHub API calls)
- unittest.mock.patch for `base64.b64decode` where needed
- All external services (GitHub API) are fully mocked — no real network calls

TODOs:
- TODO: Integration tests for __main__ block require real env vars and GitHub tokens
- TODO: Tests for call_claude / shared module interactions need the shared module
  available in the test environment
- TODO: parse_scenarios PRE-CONDITIONS and STEPS multi-line fields are not yet
  extracted into structured keys; tests reflect current behaviour (raw block only)
"""

import base64
import csv
import io
import sys
import os
import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap – mirror what the source module does so imports resolve
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# Stub out the 'shared' module before importing tool5_uat so we never hit
# real network / filesystem calls from shared.py during import.
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules["shared"] = shared_stub

import importlib
import types

# Now safe to import the module under test
import tool5_uat as uat


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Standard User
PASS CRITERIA: User is redirected to dashboard
ESTIMATED TIME: 5
STEPS:
1. Navigate to login page
2. Enter valid credentials
3. Click Submit
EXPECTED RESULT: Dashboard is shown
NOTES: None
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Standard User
PASS CRITERIA: User is redirected to dashboard
ESTIMATED TIME: 5
NOTES: None
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid credentials
TYPE: NEGATIVE
PERSONA: Standard User
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: Check error wording
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Max length username
TYPE: BOUNDARY
PERSONA: Admin User
PASS CRITERIA: System accepts or rejects gracefully
ESTIMATED TIME: 2
NOTES: Use 255-char string
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Guest
PASS CRITERIA: Something happens
ESTIMATED TIME: 1
NOTES: This has no ID line
"""


def _make_gh_content_response(text: str) -> dict:
    """Return a dict mimicking a GitHub Contents API response."""
    encoded = base64.b64encode(text.encode()).decode()
    return {"content": encoded, "encoding": "base64"}


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_parsed_correctly(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Happy path login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Standard User"
        assert s["pass_criteria"] == "User is redirected to dashboard"
        assert s["estimated_time"] == "5"

    def test_single_scenario_raw_field_present(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "Happy path login" in result[0]["raw"]

    def test_two_scenarios_parsed(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_two_scenarios_types(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_boundary_scenario_type(self):
        result = uat.parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["type"] == "BOUNDARY"

    def test_scenario_without_id_excluded(self):
        result = uat.parse_scenarios(SCENARIO_WITHOUT_ID)
        assert len(result) == 0

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = uat.parse_scenarios("   \n\n  ")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-X-1\nTITLE: Some test\nTYPE: POSITIVE"
        # Without ===SCENARIO=== delimiter the block is not recognised
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_mixed_valid_and_invalid_blocks(self):
        raw = SCENARIO_WITHOUT_ID + TWO_SCENARIO_BLOCK
        result = uat.parse_scenarios(raw)
        assert len(result) == 2  # only the two with IDs

    def test_extra_whitespace_around_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace test   \n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"

    def test_missing_optional_fields_not_in_dict(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s.get("type") is None
        assert s.get("persona") is None

    def test_multiple_scenarios_all_have_raw(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        for s in result:
            assert "raw" in s
            assert len(s["raw"]) > 0

    def test_large_batch_of_scenarios(self):
        """Boundary: many scenarios in a single raw string."""
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\n"
                f"ID: UAT-BULK-{i}\n"
                f"TITLE: Bulk test {i}\n"
                f"TYPE: POSITIVE\n"
            )
        raw = "\n".join(blocks)
        result = uat.parse_scenarios(raw)
        assert len(result) == 50
        assert result[0]["id"] == "UAT-BULK-0"
        assert result[49]["id"] == "UAT-BULK-49"

    def test_synthetic_data_customer_ids_in_test_data(self):
        """Use synthetic customer IDs in scenario test data field."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-CUST-1\n"
            "TITLE: Customer similarity lookup\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: CUST00000001 → similar to CUST00006151\n"
            "PASS CRITERIA: Similar customers returned\n"
            "ESTIMATED TIME: 3\n"
        )
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert "CUST00000001" in result[0]["raw"]

    def test_scenario_id_with_special_chars(self):
        raw = "===SCENARIO===\nID: UAT-RISK_CLASS-01\nTITLE: Risk classification\n"
        result = uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-RISK_CLASS-01"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_header_row_correct(self):
        csv_str = uat.build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        csv_str = uat.build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # header only

    def test_single_scenario_row(self):
        scenarios = [{
            "id": "UAT-STORY1-1",
            "title": "Happy path login",
            "type": "POSITIVE",
            "persona": "Standard User",
            "pass_criteria": "User is redirected to dashboard",
            "estimated_time": "5",
        }]
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Happy path login"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Standard User"
        assert data_row[4] == "User is redirected to dashboard"
        assert data_row[5] == "5"

    def test_result_tester_notes_defect_ref_empty(self):
        scenarios = [{"id": "UAT-1", "title": "t", "type": "POSITIVE",
                      "persona": "User", "pass_criteria": "OK", "estimated_time": "2"}]
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        # columns 6-9 should be empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "1"}
            for i in range(10)
        ]
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 11  # 1 header + 10 data

    def test_missing_fields_default_to_empty_string(self):
        """Scenario dict with no keys — all fields should be empty strings."""
        csv_str = uat.build_test_pack_csv([{}])
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        for i in range(6):
            assert data_row[i] == ""

    def test_title_with_commas_handled_correctly(self):
        """CSV writer must quote fields containing commas."""
        scenarios = [{"id": "UAT-C-1", "title": "Login, Register, Logout",
                      "type": "POSITIVE", "persona": "User",
                      "pass_criteria": "OK", "estimated_time": "5"}]
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == "Login, Register, Logout"

    def test_synthetic_underwriting_scenario(self):
        """Use synthetic model card data in a scenario."""
        scenarios = [{
            "id": "UAT-RISK-1",
            "title": "Underwriting Risk Classification — Age boundary",
            "type": "BOUNDARY",
            "persona": "Underwriter",
            "pass_criteria": "Risk_Classification returned for Age=34",
            "estimated_time": "3",
        }]
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert rows[1][0] == "UAT-RISK-1"
        assert "Age" in rows[1][4]

    def test_output_is_string(self):
        csv_str = uat.build_test_pack_csv([])
        assert isinstance(csv_str, str)

    def test_output_ends_with_newline(self):
        """csv.writer always terminates the last row with \\r\\n or \\n."""
        csv_str = uat.build_test_pack_csv([])
        assert csv_str.endswith("\r\n") or csv_str.endswith("\n")

    def test_pass_criteria_with_pipe_char(self):
        scenarios = [{"id": "UAT-P-1", "title": "Pipe test",
                      "type": "POSITIVE", "persona": "User",
                      "pass_criteria": "Status == PASS|FAIL", "estimated_time": "1"}]
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert "PASS|FAIL" in rows[1][4]


# ===========================================================================
# build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd:

    def test_returns_string(self):
        result = uat.build_test_pack_md("some raw content", "owner", "repo", "1.0.0")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self):
        result = uat.build_test_pack_md("content", "acme-corp", "my-repo", "2.3.1")
        assert "acme-corp/my-repo" in result

    def test_contains_version(self):
        result = uat.build_test_pack_md("content", "owner", "repo", "3.0.0-rc1")
        assert "3.0.0-rc1