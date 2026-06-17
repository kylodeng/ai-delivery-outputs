"""
Test module for tool5_uat.py

WHAT IS TESTED:
- parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
- build_test_pack_csv(): correct CSV headers, row content, empty scenarios list
- build_test_pack_md(): correct markdown structure, version/owner/repo injection
- get_results_csv(): successful decode, missing content key, FileNotFoundError
- Module-level constants and imports (SYSTEM_GENERATE, SYSTEM_ANALYSE presence)

MOCKS USED:
- requests.get (via unittest.mock.patch) — for get_results_csv GitHub API call
- base64.b64decode (indirectly via mock response content)
- shared module functions (call_claude, get_repo_files, write_output_file, send_email,
  email_html, write_audit_entry) — patched at import boundary
- os.environ — patched with monkeypatch for __main__ guard tests

TODOs:
- TODO: Integration test for __main__ block (generate mode) requires full env setup + Claude API
- TODO: Integration test for __main__ block (analyse mode) requires results CSV + Claude API
- TODO: Test write_output_file and send_email calls within __main__ execution path
- TODO: Test audit entry is written correctly (needs shared.write_audit_entry mock verification)
- TODO: Verify behaviour when SOURCE_REPO_OWNER / SOURCE_REPO_NAME env vars are missing
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Ensure the shared module can be imported without real credentials by
# providing a minimal stub before importing the module under test.
# ---------------------------------------------------------------------------

# Build a minimal fake 'shared' module so that importing tool5_uat doesn't
# trigger real network / credential lookups.
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda x: x
_shared_stub.call_claude = MagicMock(return_value="")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token fake"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now it is safe to import the module under test.
import importlib

# We patch requests at module level before importing so any module-level
# usage is also covered.
with patch.dict(sys.modules, {"requests": MagicMock()}):
    import tool5_uat  # noqa: E402  (import after sys.path manipulation)

from tool5_uat import (
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
    SYSTEM_GENERATE,
    SYSTEM_ANALYSE,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: User can log in with valid credentials
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User is registered
TEST DATA: email=test@example.com, password=ValidPass1!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: Dashboard page loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIOS_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid login
TYPE: POSITIVE
PERSONA: Policyholder
PASS CRITERIA: Dashboard loads
ESTIMATED TIME: 5
NOTES: -
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid login
TYPE: NEGATIVE
PERSONA: Anonymous
PASS CRITERIA: Error message shown
ESTIMATED TIME: 3
NOTES: -
"""

BOUNDARY_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Max length input
TYPE: BOUNDARY
PERSONA: Admin
PASS CRITERIA: System accepts exactly 255 characters
ESTIMATED TIME: 2
NOTES: Edge case
"""


@pytest.fixture()
def single_scenario_list():
    return parse_scenarios(MINIMAL_SCENARIO_BLOCK)


@pytest.fixture()
def two_scenario_list():
    return parse_scenarios(TWO_SCENARIOS_RAW)


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:

    def test_returns_list(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert isinstance(result, list)

    def test_single_scenario_parsed(self, single_scenario_list):
        assert len(single_scenario_list) == 1

    def test_id_extracted(self, single_scenario_list):
        assert single_scenario_list[0]["id"] == "UAT-STORY1-1"

    def test_title_extracted(self, single_scenario_list):
        assert single_scenario_list[0]["title"] == "User can log in with valid credentials"

    def test_type_extracted(self, single_scenario_list):
        assert single_scenario_list[0]["type"] == "POSITIVE"

    def test_persona_extracted(self, single_scenario_list):
        assert single_scenario_list[0]["persona"] == "Policyholder"

    def test_pass_criteria_extracted(self, single_scenario_list):
        assert "Dashboard" in single_scenario_list[0]["pass_criteria"]

    def test_estimated_time_extracted(self, single_scenario_list):
        assert single_scenario_list[0]["estimated_time"] == "5"

    def test_raw_stored(self, single_scenario_list):
        assert "UAT-STORY1-1" in single_scenario_list[0]["raw"]

    def test_two_scenarios_parsed(self, two_scenario_list):
        assert len(two_scenario_list) == 2

    def test_two_scenarios_ids(self, two_scenario_list):
        ids = [s["id"] for s in two_scenario_list]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_negative_type_preserved(self, two_scenario_list):
        neg = next(s for s in two_scenario_list if s["id"] == "UAT-STORY1-2")
        assert neg["type"] == "NEGATIVE"

    def test_boundary_type_preserved(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_RAW)
        assert result[0]["type"] == "BOUNDARY"

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # Without the delimiter the block has no ID → filtered out
        result = parse_scenarios("ID: UAT-X-1\nTITLE: something\n")
        assert result == []

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_multiple_blocks_some_without_id(self):
        raw = (
            "===SCENARIO===\nID: UAT-A-1\nTITLE: A\nTYPE: POSITIVE\n"
            "PASS CRITERIA: pass\nESTIMATED TIME: 1\n"
            "===SCENARIO===\nTITLE: No ID\nTYPE: NEGATIVE\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-A-1"

    def test_whitespace_only_blocks_ignored(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-B-1\nTITLE: B\n"
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_missing_optional_fields_default_absent(self):
        raw = "===SCENARIO===\nID: UAT-C-1\n"
        result = parse_scenarios(raw)
        assert result[0].get("title") is None
        assert result[0].get("type") is None
        assert result[0].get("persona") is None

    def test_extra_whitespace_around_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-D-1   \nTITLE:   Spaced Title   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-D-1"
        assert result[0]["title"] == "Spaced Title"

    def test_large_number_of_scenarios(self):
        blocks = ""
        for i in range(50):
            blocks += (
                f"===SCENARIO===\n"
                f"ID: UAT-PERF-{i}\n"
                f"TITLE: Scenario {i}\n"
                f"TYPE: POSITIVE\n"
                f"PASS CRITERIA: ok\n"
                f"ESTIMATED TIME: 1\n"
            )
        result = parse_scenarios(blocks)
        assert len(result) == 50

    def test_synthetic_data_in_raw_preserved(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-INS-1\n"
            "TITLE: Quote for Generations II\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Policyholder\n"
            "TEST DATA: product=Generations II, sum_assured=500000\n"
            "PASS CRITERIA: Premium displayed\n"
            "ESTIMATED TIME: 5\n"
        )
        result = parse_scenarios(raw)
        assert "Generations II" in result[0]["raw"]

    def test_scenario_with_unicode_title(self):
        raw = "===SCENARIO===\nID: UAT-UNI-1\nTITLE: 保险产品测试\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "保险产品测试"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _read_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self, two_scenario_list):
        assert isinstance(build_test_pack_csv(two_scenario_list), str)

    def test_header_row_present(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        assert rows[0][0] == "Scenario ID"
        assert rows[0][1] == "Title"

    def test_header_has_ten_columns(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        assert len(rows[0]) == 10

    def test_row_count_matches_scenarios(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        # 1 header + 2 data rows
        assert len(rows) == 3

    def test_scenario_id_in_row(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        ids = [r[0] for r in rows[1:]]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_result_column_empty(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        for row in rows[1:]:
            assert row[6] == ""  # Result column

    def test_tester_column_empty(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        for row in rows[1:]:
            assert row[7] == ""  # Tester column

    def test_defect_ref_column_empty(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        for row in rows[1:]:
            assert row[9] == ""  # Defect Ref column

    def test_empty_scenarios_list(self):
        rows = self._read_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # header only

    def test_scenario_with_missing_fields(self):
        scenarios = [{"id": "UAT-X-1"}]
        rows = self._read_csv(build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-X-1"
        assert rows[1][1] == ""  # title missing → empty string

    def test_csv_parseable_round_trip(self, single_scenario_list):
        csv_str = build_test_pack_csv(single_scenario_list)
        rows = self._read_csv(csv_str)
        assert rows[1][0] == single_scenario_list[0]["id"]

    def test_title_with_commas_handled(self):
        """CSV writer must quote fields containing commas."""
        scenarios = [{"id": "UAT-Y-1", "title": "Verify A, B, and C", "type": "POSITIVE"}]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert rows[1][1] == "Verify A, B, and C"

    def test_title_with_quotes_handled(self):
        scenarios = [{"id": "UAT-Z-1", "title": 'He said "hello"', "type": "POSITIVE"}]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert rows[1][1] == 'He said "hello"'

    def test_correct_column_names_order(self, two_scenario_list):
        rows = self._read_csv(build_test_pack_csv(two_scenario_list))
        expected_headers = [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]
        assert rows[0] == expected_headers

    def test_estimated_time_populated(self):
        scenarios = [{"id": "UAT-T-1", "estimated_time": "10"}]
        rows = self._read_csv(build_test_pack_csv(scenarios))
        assert rows[1][5] == "10"

    def test_insurance_synthetic_data_scenario(self):
        scenarios = [
            {
                "id": "UAT-INS-1",
                "title": "Apply for Generations II policy",
                "type": "POSITIVE",
                "persona":