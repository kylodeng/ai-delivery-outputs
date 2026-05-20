"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, missing fields,
    malformed blocks, no delimiter, multiple scenarios)
  - build_test_pack_csv(): correct CSV structure, header row, data rows,
    missing fields, empty list
  - build_test_pack_md(): correct markdown structure, version/owner/repo injection
  - get_results_csv(): successful fetch + base64 decode, missing 'content' key,
    HTTP errors

Mocks used:
  - requests.get (patched at tool5_uat.requests.get) — no real HTTP calls
  - shared module functions (call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry) — patched to prevent side effects
  - base64.b64decode is exercised with real synthetic content (no mock needed)

TODOs:
  - TODO: Integration test for __main__ block requires full env-var matrix and
    mocked subprocess/GitHub Actions context — stub tests provided below.
  - TODO: parse_scenarios multi-line fields (PRE-CONDITIONS, STEPS, NOTES)
    are stored only in `raw`; structured extraction tests skipped until
    the parser is extended.
"""

import base64
import csv
import io
import json
import sys
import os
import types
import unittest.mock as mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import doesn't fail in CI
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda x: x
_shared_stub.call_claude = MagicMock(return_value="stub")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now we can safely import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

import tool5_uat as uat  # noqa: E402  (import after path manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid underwriting application submission
TYPE: POSITIVE
PERSONA: Insurance Underwriter
PRE-CONDITIONS:
- User is logged in
- Application form is open
TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=LOW
STEPS:
1. Navigate to application form
2. Fill in all required fields
3. Submit the form
EXPECTED RESULT: Application is saved with status PENDING
PASS CRITERIA: System shows confirmation message and application ID
ESTIMATED TIME: 5
NOTES: Requires test customer CUST00000001"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: First scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: System accepts request
ESTIMATED TIME: 3
NOTES: none

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Second scenario — negative case
TYPE: NEGATIVE
PERSONA: Guest
PASS CRITERIA: System rejects request with 403
ESTIMATED TIME: 2
NOTES: none"""


def _make_scenario(overrides: dict = None) -> dict:
    base = {
        "id": "UAT-F1-1",
        "title": "Sample title",
        "type": "POSITIVE",
        "persona": "Tester",
        "pass_criteria": "All good",
        "estimated_time": "10",
        "raw": "raw block text",
    }
    if overrides:
        base.update(overrides)
    return base


# ===========================================================================
# parse_scenarios — happy paths
# ===========================================================================

class TestParseScenarios:
    def test_single_scenario_all_fields(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Valid underwriting application submission"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Insurance Underwriter"
        assert s["pass_criteria"] == "System shows confirmation message and application ID"
        assert s["estimated_time"] == "5"

    def test_single_scenario_raw_field_present(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_two_scenarios_parsed_correctly(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_scenario_types_extracted(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_personas_extracted(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Admin"
        assert result[1]["persona"] == "Guest"

    def test_pass_criteria_extracted(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "System accepts request"
        assert result[1]["pass_criteria"] == "System rejects request with 403"

    def test_estimated_time_extracted(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "3"
        assert result[1]["estimated_time"] == "2"

    def test_boundary_scenario_type(self):
        block = "===SCENARIO===\nID: UAT-B-1\nTYPE: BOUNDARY\nTITLE: Max input length\nPASS CRITERIA: ok\nESTIMATED TIME: 1\n"
        result = uat.parse_scenarios(block)
        assert result[0]["type"] == "BOUNDARY"

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        assert uat.parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # A block without the delimiter produces no id → should be filtered out
        raw = "ID: UAT-X-1\nTITLE: Something\nTYPE: POSITIVE\n"
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = uat.parse_scenarios("===SCENARIO===")
        assert result == []

    def test_scenario_missing_id_is_excluded(self):
        block = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = uat.parse_scenarios(block)
        assert result == []

    def test_scenario_missing_optional_fields_has_defaults(self):
        block = "===SCENARIO===\nID: UAT-X-99\n"
        result = uat.parse_scenarios(block)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-X-99"
        # Fields not present should not exist or be empty string via .get()
        assert s.get("title", "") == ""
        assert s.get("type", "") == ""

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-1-1\nTITLE: Real\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-1-1"

    def test_large_number_of_scenarios(self):
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-LOAD-{i}\nTITLE: Scenario {i}\nTYPE: POSITIVE\nPASS CRITERIA: ok\nESTIMATED TIME: 5\n"
            for i in range(50)
        )
        result = uat.parse_scenarios(blocks)
        assert len(result) == 50
        assert result[-1]["id"] == "UAT-LOAD-49"

    def test_extra_whitespace_around_values_stripped(self):
        block = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace test   \n"
        result = uat.parse_scenarios(block)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"

    def test_duplicate_field_last_value_wins(self):
        # If a field appears twice, the second overrides the first
        block = "===SCENARIO===\nID: UAT-DUP-1\nID: UAT-DUP-2\nTITLE: dup\n"
        result = uat.parse_scenarios(block)
        assert result[0]["id"] == "UAT-DUP-2"

    def test_unicode_content_handled(self):
        # Arabic characters from the translation file
        block = "===SCENARIO===\nID: UAT-AR-1\nTITLE: إلغاء test\nTYPE: POSITIVE\nPASS CRITERIA: ok\n"
        result = uat.parse_scenarios(block)
        assert result[0]["title"] == "إلغاء test"

    def test_raw_field_contains_full_block(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        raw = result[0]["raw"]
        assert "STEPS:" in raw
        assert "EXPECTED RESULT:" in raw
        assert "PRE-CONDITIONS:" in raw


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:
    def _read_csv(self, csv_str: str):
        return list(csv.reader(io.StringIO(csv_str)))

    def test_header_row_correct(self):
        rows = self._read_csv(uat.build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        rows = self._read_csv(uat.build_test_pack_csv([]))
        assert len(rows) == 1

    def test_single_scenario_produces_two_rows(self):
        rows = self._read_csv(uat.build_test_pack_csv([_make_scenario()]))
        assert len(rows) == 2

    def test_data_row_values_correct(self):
        s = _make_scenario()
        rows = self._read_csv(uat.build_test_pack_csv([s]))
        data = rows[1]
        assert data[0] == s["id"]
        assert data[1] == s["title"]
        assert data[2] == s["type"]
        assert data[3] == s["persona"]
        assert data[4] == s["pass_criteria"]
        assert data[5] == s["estimated_time"]

    def test_result_tester_notes_defect_are_empty(self):
        rows = self._read_csv(uat.build_test_pack_csv([_make_scenario()]))
        data = rows[1]
        assert data[6] == ""  # Result
        assert data[7] == ""  # Tester
        assert data[8] == ""  # Notes
        assert data[9] == ""  # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario({"id": f"UAT-F1-{i}"}) for i in range(5)]
        rows = self._read_csv(uat.build_test_pack_csv(scenarios))
        assert len(rows) == 6  # header + 5 data rows

    def test_missing_optional_fields_produce_empty_strings(self):
        minimal = {"id": "UAT-MIN-1", "raw": "block"}
        rows = self._read_csv(uat.build_test_pack_csv([minimal]))
        data = rows[1]
        assert data[0] == "UAT-MIN-1"
        assert data[1] == ""   # title missing
        assert data[2] == ""   # type missing

    def test_output_is_valid_csv_string(self):
        csv_str = uat.build_test_pack_csv([_make_scenario()])
        assert isinstance(csv_str, str)
        assert len(csv_str) > 0

    def test_csv_contains_newline(self):
        csv_str = uat.build_test_pack_csv([_make_scenario()])
        assert "\n" in csv_str or "\r\n" in csv_str

    def test_synthetic_data_in_csv(self):
        """Use model_card synthetic data as test data values."""
        s = _make_scenario({
            "id": "UAT-MODEL-1",
            "title": "Underwriting Risk Classification",
            "persona": "Insurance Underwriter",
            "pass_criteria": "Risk_Classification=LOW for Age=34, Annual_Income=75000",
            "estimated_time": "10",
        })
        rows = self._read_csv(uat.build_test_pack_csv([s]))
        assert "Underwriting Risk Classification" in rows[1][1]
        assert "Insurance Underwriter" in rows[1][3]

    def test_fields_with_commas_are_quoted(self):
        s = _make_scenario({"title": "First, Second, Third"})
        csv_str = uat.build_test_pack_csv([s])
        rows = self._read_csv(csv_str)
        assert rows[1][1] == "First, Second, Third"

    def test_fields_with_quotes_are_escaped(self):
        s = _make_scenario({"title": 'He said "hello"'})
        csv_str = uat.build_test_pack_csv([s])
        rows = self._read_csv(csv_str)
        assert rows[1][1] == 'He said "hello"'

    def test_ten_columns_per_row(self):
        rows = self._read_csv(uat.build_test_pack_csv([_make_scenario()]))
        for row in rows:
            assert len(row) == 10


# ===========================================================================
# build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd: