"""
Tests for tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
  - build_test_pack_csv(): happy path, empty list, partial fields, special characters
  - build_test_pack_md(): output structure, version/owner/repo injection
  - get_results_csv(): happy path (mocked GitHub API), missing file, decode error

Mocks used:
  - requests.get (for get_results_csv GitHub API call)
  - shared.call_claude (not directly tested here but stubbed at import)
  - shared.get_repo_files
  - shared.write_output_file
  - shared.send_email
  - shared.write_audit_entry
  - base64.b64decode (indirectly via requests mock)

TODOs:
  - TODO: Integration tests for __main__ block require full env var setup and live GitHub token
  - TODO: Test call_claude interaction in generate/analyse modes once main() is refactored out of __main__
  - TODO: Test write_output_file and send_email calls in the full workflow
  - TODO: SYSTEM_GENERATE / SYSTEM_ANALYSE prompt content validation (requires Claude mock)
"""

import base64
import csv
import io
import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so we can import tool5_uat without
# a real shared.py on the path.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda s: s)
shared_stub.call_claude = MagicMock(return_value="")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now we can safely import the module under test
import importlib
import tool5_uat as t5  # noqa: E402  (import after sys.path manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Standard User
PRE-CONDITIONS:
- User account exists
- System is running
TEST DATA: username=test@example.com, password=S3cr3t!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Login
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires test account setup
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login fails with wrong password
TYPE: NEGATIVE
PERSONA: Standard User
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com, password=WRONG
STEPS:
1. Navigate to login page
2. Enter wrong credentials
3. Click Login
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Error shown, no redirect
ESTIMATED TIME: 3
NOTES: Check error message wording
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Login with max-length username
TYPE: BOUNDARY
PERSONA: Edge-case User
PRE-CONDITIONS:
- System is running
TEST DATA: username={"doc": {"product_name": "Generations II"}}, password=x
STEPS:
1. Enter 255-char username
2. Submit
EXPECTED RESULT: Graceful error or success
PASS CRITERIA: No 500 error
ESTIMATED TIME: 2
NOTES: [TESTER: verify this]
"""

TWO_SCENARIO_RAW = SINGLE_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_two_scenarios_returns_two_items(self):
        result = t5.parse_scenarios(TWO_SCENARIO_RAW)
        assert len(result) == 2

    def test_three_scenarios_all_parsed(self):
        raw = SINGLE_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK + BOUNDARY_SCENARIO_BLOCK
        result = t5.parse_scenarios(raw)
        assert len(result) == 3

    def test_id_extracted_correctly(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_title_extracted_correctly(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Successful login with valid credentials"

    def test_type_extracted_correctly(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_persona_extracted_correctly(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Standard User"

    def test_pass_criteria_extracted(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "Dashboard loads" in result[0]["pass_criteria"]

    def test_estimated_time_extracted(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_raw_field_present(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_raw_field_contains_steps(self):
        result = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "Navigate to login page" in result[0]["raw"]

    def test_negative_type_parsed(self):
        result = t5.parse_scenarios(NEGATIVE_SCENARIO_BLOCK)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_parsed(self):
        result = t5.parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_synthetic_data_in_raw(self):
        """Verify synthetic JSON data in TEST DATA field ends up in raw block."""
        result = t5.parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert "Generations II" in result[0]["raw"]

    # --- edge cases ---

    def test_empty_string_returns_empty_list(self):
        assert t5.parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # Block without ===SCENARIO=== delimiter and no ID → not appended
        raw = "ID: UAT-X-1\nTITLE: Something\n"
        # split gives one block with no delimiter prefix but it has no leading ===SCENARIO===
        # The first element after split is the part before first ===SCENARIO=== so empty
        result = t5.parse_scenarios(raw)
        assert result == []

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = t5.parse_scenarios(raw)
        assert result == []

    def test_block_with_only_id_included(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = t5.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"

    def test_missing_optional_fields_default_absent(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = t5.parse_scenarios(raw)
        assert result[0].get("title") is None
        assert result[0].get("type") is None
        assert result[0].get("persona") is None

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\n"
        result = t5.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_multiple_colons_in_value_handled(self):
        """A line like 'TITLE: foo: bar' should not break parsing."""
        raw = "===SCENARIO===\nID: UAT-COL-1\nTITLE: foo: bar\n"
        result = t5.parse_scenarios(raw)
        assert result[0]["title"] == "foo: bar"

    def test_extra_whitespace_in_id_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WH-1   \n"
        result = t5.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WH-1"

    def test_pass_criteria_line_with_spaces(self):
        raw = "===SCENARIO===\nID: UAT-PC-1\nPASS CRITERIA:   All items load  \n"
        result = t5.parse_scenarios(raw)
        assert result[0]["pass_criteria"] == "All items load"

    def test_large_number_of_scenarios(self):
        blocks = ""
        for i in range(50):
            blocks += f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
        result = t5.parse_scenarios(blocks)
        assert len(result) == 50

    def test_ids_are_unique_across_parsed_scenarios(self):
        raw = SINGLE_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK + BOUNDARY_SCENARIO_BLOCK
        result = t5.parse_scenarios(raw)
        ids = [s["id"] for s in result]
        assert len(ids) == len(set(ids))


# ===========================================================================
# build_test_pack_csv — happy path + edge cases
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_returns_string(self):
        result = t5.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        rows = self._parse_csv(t5.build_test_pack_csv([]))
        assert rows[0][0] == "Scenario ID"
        assert rows[0][1] == "Title"

    def test_header_has_ten_columns(self):
        rows = self._parse_csv(t5.build_test_pack_csv([]))
        assert len(rows[0]) == 10

    def test_empty_scenario_list_only_header(self):
        rows = self._parse_csv(t5.build_test_pack_csv([]))
        assert len(rows) == 1

    def test_single_scenario_produces_two_rows(self):
        scenarios = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(t5.build_test_pack_csv(scenarios))
        assert len(rows) == 2  # header + 1 data row

    def test_scenario_id_in_first_data_column(self):
        scenarios = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(t5.build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-STORY1-1"

    def test_scenario_title_in_second_column(self):
        scenarios = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(t5.build_test_pack_csv(scenarios))
        assert rows[1][1] == "Successful login with valid credentials"

    def test_result_column_is_empty_string(self):
        scenarios = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(t5.build_test_pack_csv(scenarios))
        # Column index 6 = Result (PASS/FAIL/BLOCKED)
        assert rows[1][6] == ""

    def test_tester_column_is_empty_string(self):
        scenarios = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(t5.build_test_pack_csv(scenarios))
        assert rows[1][7] == ""

    def test_defect_ref_column_is_empty_string(self):
        scenarios = t5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._parse_csv(t5.build_test_pack_csv(scenarios))
        assert rows[1][9] == ""

    def test_multiple_scenarios_all_appear(self):
        raw = SINGLE_SCENARIO_BLOCK + NEGATIVE_SCENARIO_BLOCK + BOUNDARY_SCENARIO_BLOCK
        scenarios = t5.parse_scenarios(raw)
        rows = self._parse_csv(t5.build_test_pack_csv(scenarios))
        assert len(rows) == 4  # header + 3

    def test_partial_scenario_missing_type(self):
        scenario = {"id": "UAT-PART-1", "title": "Partial"}
        rows = self._parse_csv(t5.build_test_pack_csv([scenario]))
        # type column (index 2) should be empty string from .get("type","")
        assert rows[1][2] == ""

    def test_special_characters_in_title(self):
        scenario = {"id": "UAT-SPL-1", "title": 'Title with "quotes", commas'}
        result = t5.build_test_pack_csv([scenario])
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Title with "quotes", commas'

    def test_synthetic_data_as_test_data_field(self):
        """Scenario data referencing Generations II product info goes through cleanly."""
        scenario = {
            "id": "UAT-GEN-1",
            "title": "Generations II policy purchase",
            "type": "POSITIVE",
            "persona": "Policyholder",
            "pass_criteria": "Policy issued",
            "estimated_time": "10",
        }
        rows = self._parse_csv(t5.build_test_pack_csv([scenario]))
        assert rows[1][0] == "UAT-GEN-1"
        assert rows[1][4] == "Policy issued"

    def test_unicode_in_persona(self):
        scenario = {"id": "UAT-UNI-1", "persona": "用户 (Chinese User)"}
        rows = self._parse_csv(t5.build_test_pack_csv([