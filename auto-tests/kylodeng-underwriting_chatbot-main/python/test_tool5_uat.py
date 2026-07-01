"""
Tests for tool5_uat.py (.github/scripts/tool5_uat.py)

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, missing fields, partial blocks)
  - build_test_pack_csv(): structure, headers, row content, empty scenario list
  - build_test_pack_md(): markdown structure, version/owner/repo interpolation
  - get_results_csv(): successful fetch + base64 decode, missing file (FileNotFoundError)

Mocks used:
  - requests.get (patched via unittest.mock.patch) — never makes real HTTP calls
  - shared module functions (call_claude, get_repo_files, write_output_file, send_email,
    email_html, write_audit_entry) are patched at import time via sys.modules stubs so
    the script's top-level imports succeed without real credentials
  - base64 decoding exercised with real base64 strings

TODOs:
  - TODO: Integration test for __main__ block requires real env vars + all shared deps wired
  - TODO: parse_scenarios PRE-CONDITIONS / STEPS / NOTES / EXPECTED RESULT multi-line
          block parsing is not implemented in the source; add tests once implemented
  - TODO: build_test_pack_md timestamp is non-deterministic; consider injecting clock
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module so tool5_uat.py can be imported in isolation
# without real GitHub tokens / AWS creds / etc.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="STUB")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = _shared_stub

# Now we can safely import the module under test
import importlib

# Ensure the scripts directory is on the path so the relative import works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
# Re-import with the stub already in place
from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)

# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

FULL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: End User
PRE-CONDITIONS:
- User account exists
- System is running
TEST DATA: username=testuser@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to /login
2. Enter credentials
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard page loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires test user seeded in DB
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password is rejected
TYPE: NEGATIVE
PERSONA: End User
PRE-CONDITIONS:
- User account exists
TEST DATA: username=testuser@example.com, password=wrongpassword
STEPS:
1. Navigate to /login
2. Enter invalid password
3. Click Submit
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Error message appears and user is NOT redirected
ESTIMATED TIME: 3
NOTES: None
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Login with max-length username
TYPE: BOUNDARY
PERSONA: End User
PRE-CONDITIONS:
- System is running
TEST DATA: username=a*255@example.com, password=P@ssw0rd!
STEPS:
1. Enter 255-char username
2. Click Submit
EXPECTED RESULT: System accepts or gracefully rejects
PASS CRITERIA: No 500 error
ESTIMATED TIME: 2
NOTES: Boundary check
"""

TWO_SCENARIOS_RAW = FULL_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK


# ---------------------------------------------------------------------------
# parse_scenarios — happy path
# ---------------------------------------------------------------------------

class TestParseScenarios:
    def test_single_scenario_parsed_correctly(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "End User"
        assert s["pass_criteria"] == "Dashboard page loads within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_raw_field_preserved(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        assert "raw" in scenarios[0]
        assert "Navigate to /login" in scenarios[0]["raw"]

    def test_multiple_scenarios_returned(self):
        scenarios = parse_scenarios(TWO_SCENARIOS_RAW)
        assert len(scenarios) == 2

    def test_multiple_scenarios_ids(self):
        scenarios = parse_scenarios(TWO_SCENARIOS_RAW)
        ids = [s["id"] for s in scenarios]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_three_scenarios(self):
        raw = FULL_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK + BOUNDARY_SCENARIO_BLOCK
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 3

    def test_all_types_parsed(self):
        raw = FULL_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK + BOUNDARY_SCENARIO_BLOCK
        types_ = {s["type"] for s in parse_scenarios(raw)}
        assert types_ == {"POSITIVE", "NEGATIVE", "BOUNDARY"}

    # Edge cases
    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_scenarios("   \n\n   ") == []

    def test_no_delimiter_returns_empty_list(self):
        # A block with no ===SCENARIO=== delimiter and no ID is ignored
        result = parse_scenarios("ID: UAT-X-1\nTITLE: Some test\n")
        assert result == []

    def test_block_without_id_is_skipped(self):
        no_id = """\
===SCENARIO===
TITLE: No ID here
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Something
ESTIMATED TIME: 1
"""
        assert parse_scenarios(no_id) == []

    def test_partial_block_missing_optional_fields(self):
        minimal = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario
"""
        scenarios = parse_scenarios(minimal)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-MIN-1"
        assert s["title"] == "Minimal scenario"
        # Optional fields should be absent or falsy
        assert s.get("type", "") == ""
        assert s.get("persona", "") == ""

    def test_extra_whitespace_in_id_stripped(self):
        block = "===SCENARIO===\nID:   UAT-WS-1  \nTITLE: Whitespace test\n"
        scenarios = parse_scenarios(block)
        assert scenarios[0]["id"] == "UAT-WS-1"

    def test_extra_whitespace_in_title_stripped(self):
        block = "===SCENARIO===\nID: UAT-WS-2\nTITLE:   My Title   \n"
        scenarios = parse_scenarios(block)
        assert scenarios[0]["title"] == "My Title"

    def test_multiple_delimiters_only_returns_valid(self):
        raw = "===SCENARIO===\n\n===SCENARIO===\nID: UAT-X-1\nTITLE: T\n===SCENARIO===\n\n"
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-X-1"

    def test_type_values_preserved_exactly(self):
        scenarios = parse_scenarios(NEGATIVE_SCENARIO_BLOCK)
        assert scenarios[0]["type"] == "NEGATIVE"

    def test_boundary_type_preserved(self):
        scenarios = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert scenarios[0]["type"] == "BOUNDARY"

    def test_estimated_time_is_string(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        assert isinstance(scenarios[0]["estimated_time"], str)

    def test_pass_criteria_multiword(self):
        block = (
            "===SCENARIO===\n"
            "ID: UAT-PC-1\n"
            "TITLE: T\n"
            "PASS CRITERIA: User sees a success banner and is logged in\n"
        )
        scenarios = parse_scenarios(block)
        assert scenarios[0]["pass_criteria"] == "User sees a success banner and is logged in"


# ---------------------------------------------------------------------------
# build_test_pack_csv
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:
    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_returns_string(self):
        assert isinstance(build_test_pack_csv([]), str)

    def test_header_row_present(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_produces_header_only(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # header only (trailing newline may add empty row)

    def test_single_scenario_row_count(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        # 1 header + 1 data row (+ possible trailing empty)
        data_rows = [r for r in rows[1:] if any(r)]
        assert len(data_rows) == 1

    def test_scenario_id_in_row(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-STORY1-1"

    def test_scenario_title_in_row(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][1] == "Successful login with valid credentials"

    def test_result_column_empty(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][6] == ""

    def test_tester_column_empty(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][7] == ""

    def test_notes_column_empty(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][8] == ""

    def test_defect_ref_column_empty(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        raw = FULL_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK + BOUNDARY_SCENARIO_BLOCK
        scenarios = parse_scenarios(raw)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        data_rows = [r for r in rows[1:] if any(r)]
        assert len(data_rows) == 3

    def test_row_has_ten_columns(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows[1]) == 10

    def test_missing_fields_produce_empty_strings(self):
        minimal = [{"id": "UAT-X-1", "raw": ""}]
        rows = self._parse_csv(build_test_pack_csv(minimal))
        data = rows[1]
        assert data[0] == "UAT-X-1"
        # title/type/persona/pass_criteria/estimated_time all blank
        assert data[1] == ""
        assert data[2] == ""

    def test_type_preserved_in_csv(self):
        scenarios = parse_scenarios(NEGATIVE_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][2] == "NEGATIVE"

    def test_persona_preserved_in_csv(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][3] == "End User"

    def test_estimated_time_preserved(self):
        scenarios = parse_scenarios(FULL_SCENARIO_BLOCK)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][5] == "5"

    def test_csv_is_valid_utf8(self):
        raw = FULL_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK
        scenarios = parse_scenarios(raw)
        result = build_test_pack_csv(scenarios)
        result.encode("utf-8")  # should not raise

    def test_csv_fields_with_comma_quoted(self):
        """CSV writer must quote fields containing commas."""
        scenarios = [{"id": "UAT-C-1", "title": "Test, with comma",
                      "type": "POSITIVE", "persona": "Admin",
                      "pass_criteria": "Pass, done", "estimated_time": "2", "raw": ""}]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == "Test, with comma"
        assert rows[1][4] == "Pass, done"


# ---------------------------------------------------------------------------
# build_test