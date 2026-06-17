"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases (empty input, no ID, malformed blocks,
      whitespace-only blocks, multi-scenario parsing, all fields present)
    - build_test_pack_csv(): happy path, empty list, missing fields, special characters,
      CSV structure validation
    - build_test_pack_md(): happy path, version/owner/repo embedding, content presence
    - get_results_csv(): happy path, file-not-found error, base64 decoding

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
    - unittest.mock.patch for `base64.b64decode` where needed
    - shared module functions (call_claude, get_repo_files, write_output_file,
      send_email, email_html, write_audit_entry) are NOT called by the functions
      under test — no mock needed for unit-level tests
    - datetime.datetime.utcnow patched for deterministic MD output

TODOs:
    - TODO: Integration test for __main__ block requires full env var setup and
      mocked GitHub Actions context — stub provided below
    - TODO: parse_scenarios NOTES/STEPS/PRE-CONDITIONS multi-line field capture
      is not implemented in the parser; add tests once parser supports them
    - TODO: build_test_pack_csv encoding for non-ASCII (Arabic, Chinese) test data
      requires clarification on target encoding
"""

import base64
import csv
import io
import json
import sys
import os
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: insert the scripts directory so the module can be imported while
# keeping shared.py importable via a lightweight stub.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Stub out `shared` before tool5_uat imports it so we never hit real network
# calls or environment requirements.
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import tool5_uat as uat  # noqa: E402


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: username=john@example.com, password=Test1234!
STEPS:
1. Navigate to login page
2. Enter valid credentials
3. Click Submit
EXPECTED RESULT: User is logged in and redirected to dashboard
PASS CRITERIA: Dashboard is displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: Depends on auth service being available
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: First scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Screen A loads
ESTIMATED TIME: 3
NOTES: none
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Second scenario
TYPE: NEGATIVE
PERSONA: Guest
PASS CRITERIA: Error message shown
ESTIMATED TIME: 2
NOTES: none
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Something passes
ESTIMATED TIME: 1
NOTES: missing ID
"""


def _make_scenario(**overrides) -> dict:
    base = {
        "id": "UAT-S1-1",
        "title": "Test Title",
        "type": "POSITIVE",
        "persona": "Underwriter",
        "pass_criteria": "System responds correctly",
        "estimated_time": "10",
    }
    base.update(overrides)
    return base


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Successful login with valid credentials"

    def test_single_scenario_type_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Dashboard is displayed within 3 seconds"

    def test_single_scenario_estimated_time_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_present(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_two_scenarios_returns_two_items(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_two_scenarios_ids(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_two_scenarios_types(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        types = [s["type"] for s in result]
        assert "POSITIVE" in types
        assert "NEGATIVE" in types

    # Edge cases

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = uat.parse_scenarios("   \n\n   ")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = uat.parse_scenarios("ID: UAT-1\nTITLE: Something\n")
        # Without the ===SCENARIO=== delimiter the block has no ID extracted
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        result = uat.parse_scenarios(SCENARIO_WITHOUT_ID)
        assert result == []

    def test_mixed_valid_and_invalid_scenarios(self):
        raw = TWO_SCENARIO_BLOCK + SCENARIO_WITHOUT_ID
        result = uat.parse_scenarios(raw)
        assert len(result) == 2

    def test_extra_whitespace_around_values_stripped(self):
        block = "===SCENARIO===\nID:   UAT-X-1   \nTITLE:   My Title   \n"
        result = uat.parse_scenarios(block)
        assert result[0]["id"] == "UAT-X-1"
        assert result[0]["title"] == "My Title"

    def test_boundary_type_parsed(self):
        block = "===SCENARIO===\nID: UAT-B-1\nTYPE: BOUNDARY\n"
        result = uat.parse_scenarios(block)
        assert result[0]["type"] == "BOUNDARY"

    def test_delimiter_only_returns_empty_list(self):
        result = uat.parse_scenarios("===SCENARIO===")
        # Block has no ID → excluded
        assert result == []

    def test_multiple_scenarios_raw_field_unique(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["raw"] != result[1]["raw"]

    def test_fields_not_present_are_absent_from_dict(self):
        block = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = uat.parse_scenarios(block)
        assert "title" not in result[0]
        assert "type" not in result[0]

    def test_large_number_of_scenarios(self):
        blocks = "".join(
            f"===SCENARIO===\nID: UAT-PERF-{i}\nTITLE: Scenario {i}\n"
            for i in range(1, 51)
        )
        result = uat.parse_scenarios(blocks)
        assert len(result) == 50

    @pytest.mark.parametrize("raw_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_type_values(self, raw_type):
        block = f"===SCENARIO===\nID: UAT-T-1\nTYPE: {raw_type}\n"
        result = uat.parse_scenarios(block)
        assert result[0]["type"] == raw_type

    def test_underwriting_risk_scenario_ids(self):
        """Uses synthetic data: Underwriting Risk Classification context."""
        block = (
            "===SCENARIO===\n"
            "ID: UAT-RISK-1\n"
            "TITLE: Classify high-risk customer by Age\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: Age=18, Annual_Income=15000, Risk_Classification=High\n"
            "PASS CRITERIA: Model returns High risk label\n"
            "ESTIMATED TIME: 5\n"
        )
        result = uat.parse_scenarios(block)
        assert result[0]["id"] == "UAT-RISK-1"
        assert result[0]["estimated_time"] == "5"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self):
        result = uat.build_test_pack_csv([_make_scenario()])
        assert isinstance(result, str)

    def test_header_row_present(self):
        rows = self._parse_csv(uat.build_test_pack_csv([]))
        assert rows[0][0] == "Scenario ID"
        assert rows[0][1] == "Title"

    def test_header_has_ten_columns(self):
        rows = self._parse_csv(uat.build_test_pack_csv([]))
        assert len(rows[0]) == 10

    def test_empty_list_produces_header_only(self):
        rows = self._parse_csv(uat.build_test_pack_csv([]))
        assert len(rows) == 1  # header only (plus possible empty trailing newline row)

    def test_single_scenario_produces_two_rows(self):
        rows = self._parse_csv(uat.build_test_pack_csv([_make_scenario()]))
        data_rows = [r for r in rows if r and r[0] != "Scenario ID"]
        assert len(data_rows) == 1

    def test_scenario_id_in_csv(self):
        rows = self._parse_csv(uat.build_test_pack_csv([_make_scenario(id="UAT-S1-99")]))
        assert any("UAT-S1-99" in row[0] for row in rows[1:] if row)

    def test_scenario_title_in_csv(self):
        rows = self._parse_csv(uat.build_test_pack_csv([_make_scenario(title="My UAT Title")]))
        assert any("My UAT Title" in row[1] for row in rows[1:] if row)

    def test_result_column_empty_by_default(self):
        rows = self._parse_csv(uat.build_test_pack_csv([_make_scenario()]))
        data_row = [r for r in rows if r and r[0] == "UAT-S1-1"][0]
        assert data_row[6] == ""   # Result column

    def test_tester_column_empty_by_default(self):
        rows = self._parse_csv(uat.build_test_pack_csv([_make_scenario()]))
        data_row = [r for r in rows if r and r[0] == "UAT-S1-1"][0]
        assert data_row[7] == ""   # Tester column

    def test_defect_ref_column_empty_by_default(self):
        rows = self._parse_csv(uat.build_test_pack_csv([_make_scenario()]))
        data_row = [r for r in rows if r and r[0] == "UAT-S1-1"][0]
        assert data_row[9] == ""   # Defect Ref column

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(id=f"UAT-S1-{i}") for i in range(5)]
        rows = self._parse_csv(uat.build_test_pack_csv(scenarios))
        data_rows = [r for r in rows[1:] if r and r[0]]
        assert len(data_rows) == 5

    def test_missing_optional_fields_produce_empty_cells(self):
        s = {"id": "UAT-BARE-1", "raw": "some raw text"}
        rows = self._parse_csv(uat.build_test_pack_csv([s]))
        data_row = [r for r in rows if r and r[0] == "UAT-BARE-1"][0]
        assert data_row[1] == ""   # title empty
        assert data_row[2] == ""   # type empty

    def test_special_characters_in_title(self):
        """Commas and quotes in title should be properly CSV-escaped."""
        s = _make_scenario(title='Title with "quotes" and, commas')
        csv_str = uat.build_test_pack_csv([s])
        rows = self._parse_csv(csv_str)
        data_row = [r for r in rows if r and r[0] == "UAT-S1-1"][0]
        assert "quotes" in data_row[1]
        assert "commas" in data_row[1]

    def test_estimated_time_in_correct_column(self):
        s = _make_scenario(estimated_time="15")
        rows = self._parse_csv(uat.build_test_pack_csv([s]))
        data_row = [r for r in rows if r and r[0] == "UAT-S1-1"][0]
        assert data_row[5] == "15"

    def test_pass_criteria_in_correct_column(self):
        s = _make_scenario(pass_criteria="User sees confirmation")
        rows = self._parse_csv(uat.build_test_pack_csv([s]))
        data_