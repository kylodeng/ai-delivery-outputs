"""
Tests for tool5_uat.py
======================
What is tested:
  - parse_scenarios(): happy path, empty input, missing fields, multiple/single blocks
  - build_test_pack_csv(): correct CSV headers, row values, empty list
  - build_test_pack_md(): markdown structure, version/owner/repo embedding
  - get_results_csv(): successful fetch + base64 decode, missing content key, HTTP errors

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for `base64.b64decode`
  - sys.path manipulation to allow import without installed shared module
  - shared module is replaced with a MagicMock to avoid real network/env deps

TODOs:
  - TODO: Integration tests for __main__ block require real env vars and GitHub tokens
  - TODO: call_claude mock needs real prompt/response contract to test mode A/B end-to-end
  - TODO: write_output_file, send_email, write_audit_entry behaviour in __main__ path
  - TODO: Validate SYSTEM_GENERATE and SYSTEM_ANALYSE prompt strings against Claude API contract
"""

import base64
import csv
import io
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Stub out the `shared` module before importing tool5_uat so that no real
# network / environment dependencies are triggered at import time.
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="mocked claude response")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value="sha-abc")
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock(return_value="<html></html>")
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token test"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = _shared_stub

# Ensure the scripts directory is on the path so tool5_uat can be imported
_script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Also insert the directory that contains the file directly (handles running
# pytest from the project root where the file lives at .github/scripts/)
_alt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        ".github", "scripts")
for _d in [_alt_dir, _script_dir]:
    if _d not in sys.path:
        sys.path.insert(0, _d)

import tool5_uat as uat  # noqa: E402  (import after path manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SCENARIO_BLOCK_SINGLE = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Registered policyholder
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com, password=Test1234!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard page is displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

SCENARIO_BLOCK_TWO = (
    "===SCENARIO===\n"
    "ID: UAT-STORY2-1\n"
    "TITLE: Invalid password rejected\n"
    "TYPE: NEGATIVE\n"
    "PERSONA: Anonymous user\n"
    "PASS CRITERIA: Error message shown\n"
    "ESTIMATED TIME: 3\n"
    "raw: ignored\n"
    "===SCENARIO===\n"
    "ID: UAT-STORY2-2\n"
    "TITLE: Max-length input boundary\n"
    "TYPE: BOUNDARY\n"
    "PERSONA: Registered policyholder\n"
    "PASS CRITERIA: Input accepted up to 256 chars\n"
    "ESTIMATED TIME: 4\n"
)

SCENARIO_BLOCK_NO_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Something works
ESTIMATED TIME: 2
"""

SCENARIO_BLOCK_EMPTY_SECTIONS = """\
===SCENARIO===
ID: UAT-EMPTY-1
"""


# ---------------------------------------------------------------------------
# Synthetic-data-derived scenario block (Insurance domain)
# ---------------------------------------------------------------------------
SCENARIO_BLOCK_INSURANCE = """\
===SCENARIO===
ID: UAT-GENII-1
TITLE: Submit claim for Generations II whole life policy
TYPE: POSITIVE
PERSONA: Policyholder with Generations II plan
PRE-CONDITIONS:
- Policyholder account is active
- Policy number GEN2-0001 is in force
TEST DATA: policy_number=GEN2-0001, claim_type=terminal_illness, amount=500000
STEPS:
1. Log in as policyholder
2. Navigate to Claims section
3. Select claim type "Accelerated Benefit – Terminal Illness"
4. Enter policy number GEN2-0001 and requested benefit amount
5. Upload supporting medical documentation
6. Submit claim
EXPECTED RESULT: Claim reference number is issued and confirmation email sent
PASS CRITERIA: Claim reference displayed and email received within 2 minutes
ESTIMATED TIME: 10
NOTES: Verify accelerated benefit eligibility rules per product brochure
"""


# ===========================================================================
# parse_scenarios tests
# ===========================================================================

class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_all_fields(self):
        result = uat.parse_scenarios(SCENARIO_BLOCK_SINGLE)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Registered policyholder"
        assert s["pass_criteria"] == "Dashboard page is displayed within 3 seconds"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_two_scenarios_returned(self):
        result = uat.parse_scenarios(SCENARIO_BLOCK_TWO)
        assert len(result) == 2
        ids = [s["id"] for s in result]
        assert "UAT-STORY2-1" in ids
        assert "UAT-STORY2-2" in ids

    def test_scenario_without_id_excluded(self):
        """Scenarios missing an ID field must not appear in the output."""
        result = uat.parse_scenarios(SCENARIO_BLOCK_NO_ID)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_only_delimiter_returns_empty_list(self):
        result = uat.parse_scenarios("===SCENARIO===")
        assert result == []

    def test_whitespace_only_blocks_excluded(self):
        result = uat.parse_scenarios("===SCENARIO===\n   \n===SCENARIO===\n   ")
        assert result == []

    def test_partial_fields_still_parsed(self):
        """A scenario with only an ID and no other known fields still gets added."""
        result = uat.parse_scenarios(SCENARIO_BLOCK_EMPTY_SECTIONS)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-EMPTY-1"
        # Fields not present must not raise; just absent
        assert s.get("title") is None
        assert s.get("type") is None

    def test_raw_field_contains_block_text(self):
        result = uat.parse_scenarios(SCENARIO_BLOCK_SINGLE)
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_leading_delimiter_ignored(self):
        """A block before the first ===SCENARIO=== delimiter is empty and discarded."""
        raw = "Some preamble text\n" + SCENARIO_BLOCK_SINGLE
        result = uat.parse_scenarios(raw)
        assert len(result) == 1

    def test_multiple_scenarios_types(self):
        result = uat.parse_scenarios(SCENARIO_BLOCK_TWO)
        types_found = {s["type"] for s in result}
        assert "NEGATIVE" in types_found
        assert "BOUNDARY" in types_found

    def test_insurance_scenario_parsed(self):
        result = uat.parse_scenarios(SCENARIO_BLOCK_INSURANCE)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-GENII-1"
        assert "Generations II" in s["title"]
        assert s["type"] == "POSITIVE"
        assert s["estimated_time"] == "10"

    @pytest.mark.parametrize("field,expected", [
        ("id", "UAT-STORY2-1"),
        ("title", "Invalid password rejected"),
        ("type", "NEGATIVE"),
        ("persona", "Anonymous user"),
        ("pass_criteria", "Error message shown"),
        ("estimated_time", "3"),
    ])
    def test_first_scenario_fields_parametrized(self, field, expected):
        result = uat.parse_scenarios(SCENARIO_BLOCK_TWO)
        assert result[0][field] == expected

    def test_extra_whitespace_in_field_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace test   \n"
        result = uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"

    def test_scenario_count_matches_delimiter_count(self):
        # Build 5 valid scenario blocks
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-X-{i}\nTITLE: Test {i}\n" for i in range(5)
        )
        result = uat.parse_scenarios(blocks)
        assert len(result) == 5


# ===========================================================================
# build_test_pack_csv tests
# ===========================================================================

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

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
        # Only header, possibly one extra empty row from trailing newline
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == 1  # just header

    def test_single_scenario_one_data_row(self):
        scenarios = uat.parse_scenarios(SCENARIO_BLOCK_SINGLE)
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == 2  # header + 1 data row

    def test_scenario_fields_in_correct_columns(self):
        scenarios = uat.parse_scenarios(SCENARIO_BLOCK_SINGLE)
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Successful login with valid credentials"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Registered policyholder"
        assert data_row[4] == "Dashboard page is displayed within 3 seconds"
        assert data_row[5] == "5"

    def test_result_tester_notes_defect_empty(self):
        scenarios = uat.parse_scenarios(SCENARIO_BLOCK_SINGLE)
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[6] == ""   # Result
        assert data_row[7] == ""   # Tester
        assert data_row[8] == ""   # Notes
        assert data_row[9] == ""   # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = uat.parse_scenarios(SCENARIO_BLOCK_TWO)
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == 3  # header + 2 data rows

    def test_returns_string(self):
        result = uat.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_missing_fields_use_empty_string(self):
        """Scenarios with missing optional fields should not raise."""
        minimal = [{"id": "UAT-MIN-1"}]
        csv_str = uat.build_test_pack_csv(minimal)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-1"
        for col in range(1, 6):
            assert data_row[col] == ""

    @pytest.mark.parametrize("n", [0, 1, 5, 50])
    def test_row_count_parametrized(self, n):
        scenarios = [
            {"id": f"UAT-P-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "Pass", "estimated_time": "2"}
            for i in range(n)
        ]
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == n + 1  # +1 for header

    def test_insurance_scenario_in_csv(self):
        scenarios = uat.parse_scenarios(SCENARIO_BLOCK_INSURANCE)
        csv_str = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert rows[1][0] == "UAT-GENII-1"
        assert "Generations II" in rows[1][1]


# ===========================================================================
# build_test_pack_md tests
# ===========================================================================

class TestBuildTestPackM