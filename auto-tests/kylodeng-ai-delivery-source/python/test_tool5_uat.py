"""
Test suite for tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty/malformed input
    - build_test_pack_csv(): column headers, row content, empty list, special characters
    - build_test_pack_md(): output structure, version/owner/repo substitution, timestamp presence
    - get_results_csv(): successful fetch with base64 content, missing file error, malformed response
    - Mode A (generate) and Mode B (analyse) integration paths via __main__ block (stubbed)

Mocks used:
    - unittest.mock.patch for: requests.get, call_claude, get_repo_files,
      write_output_file, send_email, email_html, write_audit_entry, base64.b64decode
    - io.StringIO for CSV output capture
    - os.environ patched for environment variable injection

TODOs:
    - TODO: Full end-to-end integration test for __main__ block requires live GH_API/GH_HEADERS config
    - TODO: Test call_claude response with >1000 scenarios to verify performance/truncation
    - TODO: Test build_test_pack_md timezone handling when server is non-UTC
    - TODO: Verify SYSTEM_GENERATE and SYSTEM_ANALYSE prompts against actual Claude API contract
"""

import base64
import csv
import io
import json
import os
import sys
import types
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal shared stub so tool5_uat.py can be imported without the real shared
# module being present in the test environment.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda s: s)
shared_stub.call_claude = MagicMock(return_value="")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-output-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# If the file is not on disk during CI we load from the source string provided;
# either way we import as a module named tool5_uat.
if _SCRIPT_PATH.exists():
    spec = importlib.util.spec_from_file_location("tool5_uat", _SCRIPT_PATH)
    tool5 = importlib.util.module_from_spec(spec)
    sys.modules["tool5_uat"] = tool5
    spec.loader.exec_module(tool5)
else:
    # Fallback: import from the path pytest was invoked from (repo root)
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / ".github" / "scripts"))
    import tool5_uat as tool5  # type: ignore

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid customer login
TYPE: POSITIVE
PERSONA: Enterprise Admin
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: alice.chen@example.com / ValidPass1!
STEPS:
1. Navigate to /login
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Dashboard displayed
PASS CRITERIA: Redirect to /dashboard within 2 s
ESTIMATED TIME: 5
NOTES: Uses CUST-001 synthetic data"""

TWO_SCENARIO_BLOCK = SINGLE_SCENARIO_BLOCK + """

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid email rejected
TYPE: NEGATIVE
PERSONA: Consumer
PRE-CONDITIONS:
- System is online
TEST DATA: invalid-email / AnyPass1!
STEPS:
1. Navigate to /login
2. Enter invalid email
3. Click Submit
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Error message visible, no redirect
ESTIMATED TIME: 3
NOTES: Uses CUST-007 synthetic data (invalid-email)"""

BOUNDARY_SCENARIO_BLOCK = """===SCENARIO===
ID: UAT-STORY2-1
TITLE: Max annual revenue boundary
TYPE: BOUNDARY
PERSONA: Enterprise Admin
PRE-CONDITIONS:
- User authenticated
TEST DATA: annual_revenue=999999999
STEPS:
1. Submit form with max value
EXPECTED RESULT: Value accepted
PASS CRITERIA: No validation error
ESTIMATED TIME: 2
NOTES: Boundary test"""


def _make_scenario(sid="UAT-X-1", title="T", stype="POSITIVE",
                   persona="Admin", pass_criteria="Pass if OK",
                   estimated_time="5"):
    return {
        "id": sid,
        "title": title,
        "type": stype,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": "raw block",
    }


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_parsed(self):
        results = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(results) == 1
        s = results[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Valid customer login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Enterprise Admin"
        assert s["pass_criteria"] == "Redirect to /dashboard within 2 s"
        assert s["estimated_time"] == "5"

    def test_two_scenarios_parsed(self):
        results = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(results) == 2
        assert results[0]["id"] == "UAT-STORY1-1"
        assert results[1]["id"] == "UAT-STORY1-2"

    def test_boundary_scenario_type(self):
        results = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert len(results) == 1
        assert results[0]["type"] == "BOUNDARY"

    def test_negative_scenario_type(self):
        results = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert results[1]["type"] == "NEGATIVE"

    def test_raw_field_populated(self):
        results = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in results[0]
        assert len(results[0]["raw"]) > 0

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # No ===SCENARIO=== delimiter → no scenario with an id
        raw = "ID: UAT-X-1\nTITLE: Something"
        # The block before the first delimiter is ignored (no id in that block
        # unless delimiter is present).
        results = parse_scenarios(raw)
        # Without ===SCENARIO=== the whole string is one block with no id prefix
        assert results == []

    def test_delimiter_only_returns_empty_list(self):
        results = parse_scenarios("===SCENARIO===\n\n===SCENARIO===")
        assert results == []

    def test_scenario_without_id_is_skipped(self):
        raw = """===SCENARIO===
TITLE: No ID here
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Does something
ESTIMATED TIME: 3"""
        results = parse_scenarios(raw)
        assert results == []

    def test_partial_fields_still_returned(self):
        raw = """===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Only title and id"""
        results = parse_scenarios(raw)
        assert len(results) == 1
        s = results[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Only title and id"
        assert s.get("type", "") == ""

    def test_whitespace_stripped_from_values(self):
        raw = """===SCENARIO===
ID:   UAT-WS-1   
TITLE:   Whitespace test   
TYPE:   POSITIVE   
PERSONA:   Power User   
PASS CRITERIA:   Whitespace stripped   
ESTIMATED TIME:   10   """
        results = parse_scenarios(raw)
        assert results[0]["id"] == "UAT-WS-1"
        assert results[0]["title"] == "Whitespace test"
        assert results[0]["persona"] == "Power User"

    def test_multiple_scenarios_all_have_raw(self):
        results = parse_scenarios(TWO_SCENARIO_BLOCK)
        for s in results:
            assert "raw" in s
            assert isinstance(s["raw"], str)

    @pytest.mark.parametrize("raw_input", [
        "===SCENARIO===\n",
        "===SCENARIO===\n   \n",
    ])
    def test_empty_scenario_block_skipped(self, raw_input):
        results = parse_scenarios(raw_input)
        assert results == []

    def test_synthetic_invalid_email_scenario(self):
        """CUST-007 invalid-email should appear in a NEGATIVE scenario."""
        raw = """===SCENARIO===
ID: UAT-AUTH-2
TITLE: Invalid email rejected at login
TYPE: NEGATIVE
PERSONA: Consumer
PRE-CONDITIONS:
- System online
TEST DATA: invalid-email / AnyPass1!
STEPS:
1. Navigate to /login
2. Enter invalid-email
3. Submit
EXPECTED RESULT: Validation error
PASS CRITERIA: Error message visible
ESTIMATED TIME: 3
NOTES: CUST-007"""
        results = parse_scenarios(raw)
        assert results[0]["type"] == "NEGATIVE"
        assert "invalid-email" in results[0]["raw"]


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _read_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_header_row_correct(self):
        rows = self._read_csv(build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        rows = self._read_csv(build_test_pack_csv([]))
        assert len(rows) == 1

    def test_single_scenario_one_data_row(self):
        rows = self._read_csv(build_test_pack_csv([_make_scenario()]))
        assert len(rows) == 2  # header + 1 data row

    def test_scenario_fields_mapped_correctly(self):
        s = _make_scenario(
            sid="UAT-1-1", title="Login test", stype="POSITIVE",
            persona="Enterprise Admin", pass_criteria="Redirect happens",
            estimated_time="5"
        )
        rows = self._read_csv(build_test_pack_csv([s]))
        data = rows[1]
        assert data[0] == "UAT-1-1"
        assert data[1] == "Login test"
        assert data[2] == "POSITIVE"
        assert data[3] == "Enterprise Admin"
        assert data[4] == "Redirect happens"
        assert data[5] == "5"

    def test_result_tester_notes_defect_empty(self):
        rows = self._read_csv(build_test_pack_csv([_make_scenario()]))
        data = rows[1]
        assert data[6] == ""   # Result
        assert data[7] == ""   # Tester
        assert data[8] == ""   # Notes
        assert data[9] == ""   # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(sid=f"UAT-X-{i}") for i in range(5)]
        rows = self._read_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 6  # 1 header + 5 data rows

    def test_missing_keys_produce_empty_strings(self):
        s = {"raw": "something"}  # no id/title/etc.
        rows = self._read_csv(build_test_pack_csv([s]))
        data = rows[1]
        assert data[0] == ""
        assert data[1] == ""

    def test_special_characters_in_title(self):
        s = _make_scenario(title='Title with "quotes" and, commas')
        result = build_test_pack_csv([s])
        rows = self._read_csv(result)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    @pytest.mark.parametrize("stype", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_scenario_types_written(self, stype):
        s = _make_scenario(stype=stype)
        rows = self._read_csv(build_test_pack_csv([s]))
        assert rows[1][2] == stype

    def test_synthetic_customers_produce_rows(self):
        """Simulate scenarios built from synthetic customer data."""
        synthetic_scenarios = [
            _make_scenario(sid=f"UAT-CUST-{i}", title=f"Customer {cid} scenario",
                           persona=seg)
            for i, (cid, seg) in enumerate([
                ("CUST-001", "enterprise"),
                ("CUST-002", "smb"),
                ("CUST-004", "consumer"),
                ("CUST-007", "consumer"),  # invalid email
            ])
        ]
        rows = self._read_csv(build_test_pack_csv(synthetic_scenarios))
        assert len(rows) == 5  # header + 4


# ===========================================================================
# build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd:

    def test_contains_owner_repo_version(self):
        result = build_test_pack_md("raw content", "my-org", "my-repo", "1.2.3")
        assert "my-org/my-repo" in result
        assert "1.2.3" in result

    def test_contains_raw_content(self):
        result = build_test_pack_md("===SCENARIO===\nID: UAT-X-1", "o", "r", "v1")
        assert "===SCENARIO===" in result
        assert "ID: UAT-X-1" in result

    def test_contains_generated_timestamp(self):
        import datetime
        with patch("tool5_uat.datetime") if hasattr(tool5, "datetime") else