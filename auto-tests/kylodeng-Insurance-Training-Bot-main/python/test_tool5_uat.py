"""
Test suite for tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, malformed/empty input, multiple scenarios
  - build_test_pack_csv(): structure, headers, data rows, empty input
  - build_test_pack_md(): content assembly, version/owner/repo substitution
  - get_results_csv(): successful fetch, missing content key, HTTP/decode errors
  - __main__ block integration (via subprocess or env-patching): skipped where
    full integration requires secrets

Mocks used:
  - unittest.mock.patch for requests.get (get_results_csv)
  - unittest.mock.patch for shared module functions (call_claude, get_repo_files,
    write_output_file, send_email, write_audit_entry)
  - base64 decode behaviour tested inline

TODOs:
  - TODO: Integration test for __main__ generate mode requires ANTHROPIC_API_KEY + GH_TOKEN
  - TODO: Integration test for __main__ analyse mode requires real CSV upload to GH repo
  - TODO: Test write_output_file call assertions once full __main__ flow is extractable
  - TODO: Verify email HTML format for UAT defect report (requires email template access)
"""

import base64
import csv
import io
import json
import os
import sys
import types
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal 'shared' stub so tool5_uat.py can be imported
# without real GitHub credentials or the actual shared module on sys.path.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.clean_json = lambda s: s
    stub.call_claude = MagicMock(return_value="stub response")
    stub.get_repo_files = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value=None)
    stub.send_email = MagicMock(return_value=None)
    stub.email_html = MagicMock(return_value="<html/>")
    stub.write_audit_entry = MagicMock(return_value=None)
    stub.OUTPUT_REPO_OWNER = "test-owner"
    stub.OUTPUT_REPO = "test-output-repo"
    stub.GH_HEADERS = {"Authorization": "token fake"}
    stub.GH_API = "https://api.github.com"
    return stub


# Inject the stub before importing the module under test
shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", shared_stub)

# Now safe to import
import importlib
import tool5_uat as uat  # noqa: E402  (import after sys.modules manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Regular User
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com password=Passw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 s
ESTIMATED TIME: 5
NOTES: None
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with wrong password
TYPE: NEGATIVE
PERSONA: Regular User
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com password=WRONG
STEPS:
1. Navigate to login page
2. Enter wrong password
3. Click submit
EXPECTED RESULT: Error message shown
PASS CRITERIA: Error message displayed within 3 s
ESTIMATED TIME: 3
NOTES: Check lockout after 5 attempts
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Login with empty password
TYPE: BOUNDARY
PERSONA: Regular User
PRE-CONDITIONS:
- Login page loaded
TEST DATA: username=test@example.com password=
STEPS:
1. Navigate to login page
2. Leave password blank
3. Click submit
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Inline validation error appears
ESTIMATED TIME: 2
NOTES: Empty input boundary
"""


def make_raw_scenarios(*blocks):
    """Join scenario blocks with the delimiter."""
    return "".join(blocks)


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_happy_path(self):
        raw = make_raw_scenarios(MINIMAL_SCENARIO_BLOCK)
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Happy path login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Regular User"
        assert s["pass_criteria"] == "Dashboard loads within 3 s"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_parsed(self):
        raw = make_raw_scenarios(
            MINIMAL_SCENARIO_BLOCK,
            NEGATIVE_SCENARIO_BLOCK,
            BOUNDARY_SCENARIO_BLOCK,
        )
        result = uat.parse_scenarios(raw)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_types_captured_correctly(self):
        raw = make_raw_scenarios(
            MINIMAL_SCENARIO_BLOCK,
            NEGATIVE_SCENARIO_BLOCK,
            BOUNDARY_SCENARIO_BLOCK,
        )
        result = uat.parse_scenarios(raw)
        types_found = {s["id"]: s["type"] for s in result}
        assert types_found["UAT-STORY1-1"] == "POSITIVE"
        assert types_found["UAT-STORY1-2"] == "NEGATIVE"
        assert types_found["UAT-STORY1-3"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """Input without the delimiter produces no scenarios."""
        raw = "ID: UAT-X-1\nTITLE: Something\n"
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = uat.parse_scenarios("===SCENARIO===")
        assert result == []

    def test_scenario_without_id_excluded(self):
        """Blocks missing ID should be silently dropped."""
        block_no_id = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: something
ESTIMATED TIME: 1
"""
        raw = make_raw_scenarios(block_no_id, MINIMAL_SCENARIO_BLOCK)
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_raw_field_contains_original_block(self):
        raw = make_raw_scenarios(MINIMAL_SCENARIO_BLOCK)
        result = uat.parse_scenarios(raw)
        assert "UAT-STORY1-1" in result[0]["raw"]
        assert "Happy path login" in result[0]["raw"]

    def test_whitespace_only_blocks_ignored(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\n" + MINIMAL_SCENARIO_BLOCK.lstrip("===SCENARIO===\n")
        # First block is whitespace-only; only real scenario should appear
        result = uat.parse_scenarios(raw)
        # Only the scenario with an ID survives
        for s in result:
            assert s.get("id")

    def test_missing_optional_fields_default_absent(self):
        """Scenarios missing optional fields simply lack those keys."""
        sparse_block = """\
===SCENARIO===
ID: UAT-SPARSE-1
TITLE: Sparse scenario
"""
        result = uat.parse_scenarios(sparse_block)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-SPARSE-1"
        assert s["title"] == "Sparse scenario"
        assert "type" not in s
        assert "persona" not in s
        assert "pass_criteria" not in s
        assert "estimated_time" not in s

    def test_extra_whitespace_in_values_stripped(self):
        block = """\
===SCENARIO===
ID:   UAT-WS-1  
TITLE:   Whitespace test   
TYPE:   POSITIVE   
PERSONA:   Admin User   
PASS CRITERIA:   Passes   
ESTIMATED TIME:   10   
"""
        result = uat.parse_scenarios(block)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-WS-1"
        assert s["title"] == "Whitespace test"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Admin User"
        assert s["pass_criteria"] == "Passes"
        assert s["estimated_time"] == "10"

    def test_large_number_of_scenarios(self):
        """Stress-test with 50 scenario blocks."""
        blocks = []
        for i in range(50):
            blocks.append(f"""\
===SCENARIO===
ID: UAT-BULK-{i}
TITLE: Bulk scenario {i}
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: ok
ESTIMATED TIME: 1
""")
        raw = "".join(blocks)
        result = uat.parse_scenarios(raw)
        assert len(result) == 50
        assert result[0]["id"] == "UAT-BULK-0"
        assert result[49]["id"] == "UAT-BULK-49"

    def test_insurance_synthetic_data_in_raw(self):
        """Ensure synthetic insurance data embedded in TEST DATA survives in raw."""
        block = """\
===SCENARIO===
ID: UAT-INS-1
TITLE: Generations II policy purchase
TYPE: POSITIVE
PERSONA: Policyholder
TEST DATA: product_name=Generations II, doc_type=product_brochure
STEPS:
1. Login as policyholder
2. Select Generations II plan
3. Complete application
EXPECTED RESULT: Policy issued
PASS CRITERIA: Policy number generated
ESTIMATED TIME: 15
NOTES: [TESTER: verify premium table]
"""
        result = uat.parse_scenarios(block)
        assert len(result) == 1
        assert "Generations II" in result[0]["raw"]
        assert "product_brochure" in result[0]["raw"]


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_header_row_correct(self):
        csv_out = uat.build_test_pack_csv([])
        rows = self._parse_csv(csv_out)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_produces_header_only(self):
        csv_out = uat.build_test_pack_csv([])
        rows = self._parse_csv(csv_out)
        assert len(rows) == 1  # header only

    def test_single_scenario_row(self):
        scenarios = [{
            "id": "UAT-STORY1-1",
            "title": "Happy path login",
            "type": "POSITIVE",
            "persona": "Regular User",
            "pass_criteria": "Dashboard loads within 3 s",
            "estimated_time": "5",
            "raw": "...",
        }]
        csv_out = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        assert len(rows) == 2
        assert rows[1][0] == "UAT-STORY1-1"
        assert rows[1][1] == "Happy path login"
        assert rows[1][2] == "POSITIVE"
        assert rows[1][3] == "Regular User"
        assert rows[1][4] == "Dashboard loads within 3 s"
        assert rows[1][5] == "5"
        # Result, Tester, Notes, Defect Ref should be empty
        assert rows[1][6] == ""
        assert rows[1][7] == ""
        assert rows[1][8] == ""
        assert rows[1][9] == ""

    def test_multiple_scenario_rows(self):
        scenarios = [
            {"id": f"UAT-X-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "ok", "estimated_time": str(i)}
            for i in range(5)
        ]
        csv_out = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        assert len(rows) == 6  # header + 5 data rows
        for i, row in enumerate(rows[1:]):
            assert row[0] == f"UAT-X-{i}"

    def test_missing_fields_use_empty_string(self):
        scenarios = [{"id": "UAT-MISS-1", "raw": "something"}]
        csv_out = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        assert rows[1][0] == "UAT-MISS-1"
        assert rows[1][1] == ""   # title missing
        assert rows[1][2] == ""   # type missing

    def test_output_is_string(self):
        csv_out = uat.build_test_pack_csv([])
        assert isinstance(csv_out, str)

    def test_csv_has_ten_columns_per_row(self):
        scenarios = [{"id": "UAT-COL-1", "title": "T", "type": "POSITIVE",
                      "persona": "U", "pass_criteria": "P", "estimated_time": "2"}]
        csv_out = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        for row in rows:
            assert len(row) == 10

    def test_values_with_commas_are_quoted_correctly(self):
        """CSV writer must handle commas inside values."""
        scenarios = [{"id": "UAT-CMM-1",
                      "title": "Check values: A, B, C",
                      "type": "POSITIVE",
                      "persona": "User, Admin",
                      "pass_criteria": "ok",
                      "estimated_time": "5"}]
        csv_out = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        assert rows[1][1] == "Check values: A, B, C"
        assert rows[1][3] == "User, Admin"

    def test_insurance_scenario_csv_row(self):
        scenarios = [
            {
                "id": "UAT-INS-1