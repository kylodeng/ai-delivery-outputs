"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input, multiple scenarios
  - build_test_pack_csv(): happy path, empty list, missing fields, special characters
  - build_test_pack_md(): happy path, version/owner/repo rendering, timestamp presence
  - get_results_csv(): happy path, missing file (FileNotFoundError), malformed response
  - Module-level __main__ block: NOT tested here (requires full env wiring)

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
  - unittest.mock.patch for `base64.b64decode` where needed
  - All shared module imports (clean_json, call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry) are patched at import time via sys.modules

TODOs:
  - TODO: Integration test for __main__ block requires full env var wiring and mocked GH API
  - TODO: parse_scenarios() — verify multi-line PRE-CONDITIONS and STEPS are preserved in raw
  - TODO: build_test_pack_md() — verify exact UTC timestamp format against real datetime output
  - TODO: get_results_csv() — test pagination / large file base64 decoding
  - TODO: Test call_claude / shared interactions once shared module contract is stable
"""

import base64
import csv
import io
import sys
import os
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module so we can import tool5_uat without side-effects
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock()
_shared_stub.call_claude = MagicMock()
_shared_stub.get_repo_files = MagicMock()
_shared_stub.write_output_file = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock()
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now we can safely import the module under test
import importlib
import tool5_uat as uat  # noqa: E402  (import after sys.modules manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Registered Policyholder
PRE-CONDITIONS:
- User account exists
- Password is correct
TEST DATA: username=test@sunlife.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires test account to be pre-created
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Policy lookup by valid policy number
TYPE: POSITIVE
PERSONA: Agent
PRE-CONDITIONS:
- Policy exists in system
TEST DATA: policy_number=GEN2-00001
STEPS:
1. Open policy search
2. Enter policy number
3. Click Search
EXPECTED RESULT: Policy details returned
PASS CRITERIA: Policy data visible within 2 seconds
ESTIMATED TIME: 3
NOTES: None
===SCENARIO===
ID: UAT-STORY2-2
TITLE: Policy lookup with non-existent policy number
TYPE: NEGATIVE
PERSONA: Agent
PRE-CONDITIONS:
- Policy does NOT exist
TEST DATA: policy_number=GEN2-99999
STEPS:
1. Open policy search
2. Enter invalid policy number
3. Click Search
EXPECTED RESULT: Error message shown
PASS CRITERIA: "Policy not found" message displayed
ESTIMATED TIME: 2
NOTES: Edge case
"""

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-MINIMAL-1
TITLE: Minimal scenario
TYPE: BOUNDARY
PERSONA: [TESTER: verify this]
PRE-CONDITIONS:
TEST DATA: empty
STEPS:
1. Do something
EXPECTED RESULT: Something happens
PASS CRITERIA: Something happened
ESTIMATED TIME: 1
NOTES: none
"""


def _make_scenario(**kwargs) -> dict:
    """Return a scenario dict with sensible defaults, overrideable via kwargs."""
    base = {
        "id": "UAT-TEST-1",
        "title": "Test scenario title",
        "type": "POSITIVE",
        "persona": "Policyholder",
        "pass_criteria": "Screen loads",
        "estimated_time": "5",
        "raw": "raw block content",
    }
    base.update(kwargs)
    return base


# ===========================================================================
# parse_scenarios() tests
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_parsed_correctly(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Registered Policyholder"
        assert s["pass_criteria"] == "Dashboard loads within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_two_scenarios_parsed_correctly(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY2-1"
        assert result[1]["id"] == "UAT-STORY2-2"

    def test_scenario_type_negative(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[1]["type"] == "NEGATIVE"

    def test_scenario_type_boundary(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_raw_block_stored(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-STORY1-1" in result[0]["raw"]
        assert "Dashboard loads within 3 seconds" in result[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_no_scenario_delimiters_returns_empty_list(self):
        result = uat.parse_scenarios("This is some random text with no scenario markers.")
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = uat.parse_scenarios("===SCENARIO===")
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        block = """\
===SCENARIO===
TITLE: Missing ID scenario
TYPE: POSITIVE
PERSONA: Agent
PASS CRITERIA: Something works
ESTIMATED TIME: 2
NOTES: none
"""
        result = uat.parse_scenarios(block)
        assert result == []

    def test_scenario_with_only_id_is_included(self):
        block = "===SCENARIO===\nID: UAT-ONLY-1\n"
        result = uat.parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-ONLY-1"

    def test_multiple_scenarios_with_one_missing_id(self):
        combined = SINGLE_SCENARIO_BLOCK + """\
===SCENARIO===
TITLE: No ID here
TYPE: NEGATIVE
PERSONA: Unknown
PASS CRITERIA: Fails gracefully
ESTIMATED TIME: 1
NOTES: none
"""
        result = uat.parse_scenarios(combined)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_leading_whitespace_stripped_from_values(self):
        block = "===SCENARIO===\nID:   UAT-SPACE-1  \nTITLE:   Spaces everywhere   \nTYPE:  POSITIVE  \nPERSONA:  Admin  \nPASS CRITERIA:  Looks fine  \nESTIMATED TIME:  10  \nNOTES: none\n"
        result = uat.parse_scenarios(block)
        assert result[0]["id"] == "UAT-SPACE-1"
        assert result[0]["title"] == "Spaces everywhere"
        assert result[0]["type"] == "POSITIVE"
        assert result[0]["persona"] == "Admin"
        assert result[0]["pass_criteria"] == "Looks fine"
        assert result[0]["estimated_time"] == "10"

    def test_many_scenarios_count(self):
        chunk = """\
===SCENARIO===
ID: UAT-BULK-{n}
TITLE: Bulk scenario {n}
TYPE: POSITIVE
PERSONA: User
PASS CRITERIA: Passes
ESTIMATED TIME: 1
NOTES: none
"""
        raw = "".join(chunk.replace("{n}", str(i)) for i in range(10))
        result = uat.parse_scenarios(raw)
        assert len(result) == 10

    def test_insurance_synthetic_data_in_raw(self):
        """Verify that synthetic insurance test data is preserved in the raw block."""
        block = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Generations II policy lookup
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Generations II policy GEN2-00001 exists
TEST DATA: policy_number=GEN2-00001, product=Generations II
STEPS:
1. Login as policyholder
2. Search for policy GEN2-00001
3. View policy details
EXPECTED RESULT: Generations II policy details displayed
PASS CRITERIA: Policy name "Generations II" visible on screen
ESTIMATED TIME: 5
NOTES: Uses Sun Life synthetic test policy
"""
        result = uat.parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-GEN2-1"
        assert "GEN2-00001" in result[0]["raw"]
        assert "Generations II" in result[0]["raw"]

    def test_tester_placeholder_in_persona(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "[TESTER: verify this]" in result[0]["persona"]

    def test_parse_does_not_mutate_input_string(self):
        original = SINGLE_SCENARIO_BLOCK
        copy = SINGLE_SCENARIO_BLOCK
        uat.parse_scenarios(original)
        assert original == copy


# ===========================================================================
# build_test_pack_csv() tests
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_header_row_present(self):
        result = uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_list_produces_header_only(self):
        result = uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1

    def test_single_scenario_produces_two_rows(self):
        scenarios = [_make_scenario()]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2

    def test_data_row_values_correct(self):
        scenarios = [_make_scenario(
            id="UAT-TEST-1",
            title="Happy path test",
            type="POSITIVE",
            persona="Policyholder",
            pass_criteria="Login works",
            estimated_time="5",
        )]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-TEST-1"
        assert data_row[1] == "Happy path test"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Policyholder"
        assert data_row[4] == "Login works"
        assert data_row[5] == "5"

    def test_tester_result_notes_defect_cols_are_empty(self):
        scenarios = [_make_scenario()]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[6] == ""  # Result
        assert data_row[7] == ""  # Tester
        assert data_row[8] == ""  # Notes
        assert data_row[9] == ""  # Defect Ref

    def test_multiple_scenarios_produce_correct_row_count(self):
        scenarios = [_make_scenario(id=f"UAT-TEST-{i}") for i in range(5)]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 6  # header + 5 data rows

    def test_missing_keys_produce_empty_cells(self):
        """Scenario dict with no keys — all cells should be empty strings."""
        result = uat.build_test_pack_csv([{}])
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == ""
        assert data_row[1] == ""

    def test_special_characters_in_title(self):
        scenarios = [_make_scenario(title='Test with "quotes" and, commas')]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Test with "quotes" and, commas'

    def test_newline_in_pass_criteria_handled(self):
        scenarios = [_make_scenario(pass_criteria="Line one\nLine two")]
        result = uat.build_test_pack_csv(scenarios)
        # csv.writer wraps fields with newlines in quotes; csv.reader recovers them
        rows = self._parse_csv(result)
        assert "Line one" in rows[1][4]

    def test_unicode_content_preserved(self):
        scenarios = [_make_scenario(title="保險測試 — Generations II")]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == "保險測試 — Generations II"

    def test_insurance_scenario_csv_round_trip(self):
        """End-to-end: parse a realistic insurance scenario block, then build CSV."""
        parsed = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        csv_out = uat.build_test