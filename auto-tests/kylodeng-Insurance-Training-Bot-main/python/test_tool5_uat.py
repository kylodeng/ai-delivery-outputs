"""
Test suite for tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, empty input, missing fields, multi-scenario blocks
    - build_test_pack_csv(): CSV structure, header row, data rows, empty scenarios list
    - build_test_pack_md(): Markdown structure, version/owner/repo embedding, raw content inclusion
    - get_results_csv(): successful fetch, missing content key, HTTP error response
    - Module-level __main__ block (stub only — requires full env wiring)

Mocks used:
    - unittest.mock.patch for requests.get (GitHub API calls)
    - unittest.mock.patch for shared module functions: call_claude, get_repo_files,
      write_output_file, send_email, email_html, write_audit_entry, clean_json
    - base64 encoding/decoding verified inline

TODOs:
    - TODO: Integration test for __main__ block requires full env var setup and GitHub credentials
    - TODO: Test parse_scenarios() against real Claude output format once prompt is stable
    - TODO: Test build_test_pack_md() date/time freezing for deterministic output comparison
    - TODO: Verify get_results_csv() pagination / large file handling (if GitHub API truncates)
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Make shared importable without the real module existing in test environment
# ---------------------------------------------------------------------------
shared_mock = MagicMock()
shared_mock.OUTPUT_REPO_OWNER = "test-owner"
shared_mock.OUTPUT_REPO = "test-output-repo"
shared_mock.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_mock.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_mock)

# Now import the module under test (patch requests at import time is not needed;
# we patch per-test via decorators)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# We need to import after setting up the shared mock
import importlib

with patch.dict("sys.modules", {"shared": shared_mock, "requests": MagicMock()}):
    import tool5_uat  # noqa: E402 — intentional deferred import

# Re-bind the module's requests reference so we can patch it properly
import requests as _requests_module  # real requests (may be installed); used as placeholder


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: user@example.com / P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

MULTI_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: View policy details
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User is logged in
TEST DATA: Policy No. 12345678
STEPS:
1. Click "My Policies"
2. Select policy
EXPECTED RESULT: Policy details page shown
PASS CRITERIA: All fields populated correctly
ESTIMATED TIME: 3
NOTES: Check Generations II product data

===SCENARIO===
ID: UAT-STORY2-2
TITLE: Access policy with invalid ID
TYPE: NEGATIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User is logged in
TEST DATA: Policy No. INVALID-999
STEPS:
1. Navigate to /policy/INVALID-999
EXPECTED RESULT: 404 error page shown
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 2
NOTES: Edge case

===SCENARIO===
ID: UAT-STORY2-3
TITLE: Maximum policy number boundary
TYPE: BOUNDARY
PERSONA: Admin
PRE-CONDITIONS:
- Admin is logged in
TEST DATA: Policy No. 99999999
STEPS:
1. Search for boundary policy ID
EXPECTED RESULT: System handles gracefully
PASS CRITERIA: No 500 error
ESTIMATED TIME: 3
NOTES: Boundary check
"""

SCENARIO_MISSING_ID = """\
===SCENARIO===
TITLE: Orphan scenario without ID
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Should be ignored
ESTIMATED TIME: 1
NOTES: No ID field
"""


def _make_scenario(**kwargs) -> dict:
    """Factory for scenario dicts used across multiple tests."""
    defaults = {
        "id": "UAT-TEST-1",
        "title": "Default test title",
        "type": "POSITIVE",
        "persona": "Policyholder",
        "pass_criteria": "System responds correctly",
        "estimated_time": "5",
        "raw": "raw block content",
    }
    defaults.update(kwargs)
    return defaults


# ===========================================================================
# parse_scenarios()
# ===========================================================================

class TestParseScenarios:
    """Tests for parse_scenarios() — the scenario-block parser."""

    def test_single_scenario_returns_one_item(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_parsed(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Successful login with valid credentials"

    def test_single_scenario_type_parsed(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Policyholder"

    def test_single_scenario_pass_criteria_parsed(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Dashboard loads within 3 seconds"

    def test_single_scenario_estimated_time_parsed(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_block_stored(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_multi_scenario_returns_three_items(self):
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_BLOCK)
        assert len(result) == 3

    def test_multi_scenario_ids_correct(self):
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-STORY2-1", "UAT-STORY2-2", "UAT-STORY2-3"]

    def test_multi_scenario_types_correct(self):
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_BLOCK)
        types = [s["type"] for s in result]
        assert "POSITIVE" in types
        assert "NEGATIVE" in types
        assert "BOUNDARY" in types

    def test_scenario_without_id_is_excluded(self):
        result = tool5_uat.parse_scenarios(SCENARIO_MISSING_ID)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("   \n\n   ")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """Block without ===SCENARIO=== delimiter produces no output."""
        result = tool5_uat.parse_scenarios("ID: UAT-PLAIN-1\nTITLE: Some title")
        # No delimiter → single block that is empty after split → excluded
        assert result == []

    def test_partial_fields_still_captured(self):
        """A scenario with only ID and TITLE (no other fields) is still included."""
        raw = "===SCENARIO===\nID: UAT-PARTIAL-1\nTITLE: Partial scenario\n"
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-1"
        assert result[0]["title"] == "Partial scenario"
        # Missing optional fields should not be present (get returns None/absent)
        assert result[0].get("persona") is None

    def test_leading_delimiter_block_skipped(self):
        """Content before first ===SCENARIO=== delimiter is ignored."""
        raw = "Some preamble text\n" + SINGLE_SCENARIO_BLOCK
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_insurance_product_test_data_in_raw(self):
        """Synthetic data (Generations II policy) referenced in NOTES appears in raw."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-GEN2-1\n"
            "TITLE: Validate Generations II product brochure access\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Policyholder\n"
            "PASS CRITERIA: Brochure PDF opens correctly\n"
            "ESTIMATED TIME: 4\n"
            "NOTES: Uses Generations II product data from annot.json\n"
        )
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert "Generations II" in result[0]["raw"]

    def test_duplicate_ids_both_returned(self):
        """Parser does not de-duplicate; both entries are returned."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-DUP-1\n"
            "TITLE: First\n"
            "===SCENARIO===\n"
            "ID: UAT-DUP-1\n"
            "TITLE: Second\n"
        )
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 2

    def test_extra_whitespace_in_field_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1  \nTITLE:   Whitespace test   \n"
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"

    def test_returns_list_type(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert isinstance(result, list)

    def test_each_item_is_dict(self):
        result = tool5_uat.parse_scenarios(MULTI_SCENARIO_BLOCK)
        for item in result:
            assert isinstance(item, dict)


# ===========================================================================
# build_test_pack_csv()
# ===========================================================================

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv() — CSV generation."""

    EXPECTED_HEADERS = [
        "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
        "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
    ]

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_returns_string(self):
        result = tool5_uat.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_empty_scenarios_has_header_only(self):
        result = tool5_uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1
        assert rows[0] == self.EXPECTED_HEADERS

    def test_header_row_correct(self):
        result = tool5_uat.build_test_pack_csv([_make_scenario()])
        rows = self._parse_csv(result)
        assert rows[0] == self.EXPECTED_HEADERS

    def test_single_scenario_produces_two_rows(self):
        result = tool5_uat.build_test_pack_csv([_make_scenario()])
        rows = self._parse_csv(result)
        assert len(rows) == 2

    def test_data_row_id_correct(self):
        s = _make_scenario(id="UAT-CSV-1")
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][0] == "UAT-CSV-1"

    def test_data_row_title_correct(self):
        s = _make_scenario(title="My Test Title")
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][1] == "My Test Title"

    def test_data_row_type_correct(self):
        s = _make_scenario(type="NEGATIVE")
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][2] == "NEGATIVE"

    def test_data_row_persona_correct(self):
        s = _make_scenario(persona="Admin")
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][3] == "Admin"

    def test_data_row_pass_criteria_correct(self):
        s = _make_scenario(pass_criteria="Dashboard visible")
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][4] == "Dashboard visible"

    def test_data_row_estimated_time_correct(self):
        s = _make_scenario(estimated_time="10")
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][5] == "10"

    def test_data_row_result_column_empty(self):
        s = _make_scenario()
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][6] == ""

    def test_data_row_tester_column_empty(self):
        s = _make_scenario()
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][7] == ""

    def test_data_row_notes_column_empty(self):
        s = _make_scenario()
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([s]))
        assert rows[1][8] == ""

    def test_data_row_defect_ref_column_empty(self):
        s = _make_scenario()
        rows