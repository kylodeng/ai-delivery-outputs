"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, no ID, multiple scenarios,
    missing fields, extra delimiters, partial blocks)
  - build_test_pack_csv(): happy path, empty list, missing fields, special characters
  - build_test_pack_md(): happy path, version/owner/repo interpolation
  - get_results_csv(): happy path, file-not-found error, base64 decoding

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for `base64.b64decode` where needed
  - shared module functions (call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry) stubbed via sys.modules injection

TODOs:
  - TODO: Integration tests for __main__ block require full env-var setup and
    live shared.py dependencies — stubbed below
  - TODO: Tests for call_claude interaction inside __main__ require a running
    mock of the Claude API client
  - TODO: build_test_pack_md timestamp assertion is time-dependent — currently
    uses regex match
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: inject a fake `shared` module so tool5_uat.py can be imported
# without the real shared.py (which has external dependencies).
# ---------------------------------------------------------------------------

_fake_shared = types.ModuleType("shared")
_fake_shared.clean_json = MagicMock(side_effect=lambda x: x)
_fake_shared.call_claude = MagicMock(return_value="mocked claude response")
_fake_shared.get_repo_files = MagicMock(return_value={})
_fake_shared.write_output_file = MagicMock(return_value=None)
_fake_shared.send_email = MagicMock(return_value=None)
_fake_shared.email_html = MagicMock(return_value="<html/>")
_fake_shared.write_audit_entry = MagicMock(return_value=None)
_fake_shared.OUTPUT_REPO_OWNER = "test-owner"
_fake_shared.OUTPUT_REPO = "test-output-repo"
_fake_shared.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_fake_shared.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _fake_shared)

# Now import the module under test
import importlib
import tool5_uat  # noqa: E402  (path inserted by sys.path.insert in the module itself)

# Re-export the public functions for convenience
parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

SINGLE_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Underwriting risk classification positive flow
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is logged in
- Model card is loaded
TEST DATA: Age=35, Annual_Income=75000, Risk_Classification=Low
STEPS:
1. Navigate to assessment screen
2. Enter customer data
3. Submit for classification
EXPECTED RESULT: System returns Risk_Classification=Low
PASS CRITERIA: Result matches expected classification
ESTIMATED TIME: 5
NOTES: Uses CatBoostClassifier model"""

TWO_SCENARIOS_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: First scenario
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Passes first check
ESTIMATED TIME: 3
NOTES: none
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Second scenario
TYPE: NEGATIVE
PERSONA: Customer
PASS CRITERIA: Error shown
ESTIMATED TIME: 2
NOTES: invalid input"""

SCENARIO_MISSING_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: BOUNDARY
PERSONA: Admin
PASS CRITERIA: Should be skipped
ESTIMATED TIME: 1
NOTES: missing id field"""


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("Some random text without the scenario delimiter.")
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===")
        # The single block after splitting will be empty string → skipped
        assert result == []

    def test_single_scenario_happy_path(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Underwriting risk classification positive flow"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Result matches expected classification"
        assert s["estimated_time"] == "5"

    def test_single_scenario_raw_field_preserved(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_two_scenarios_parsed_correctly(self):
        result = parse_scenarios(TWO_SCENARIOS_RAW)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_scenario_missing_id_is_excluded(self):
        result = parse_scenarios(SCENARIO_MISSING_ID)
        assert result == []

    def test_mixed_valid_and_invalid_scenarios(self):
        raw = SCENARIO_MISSING_ID + "\n" + TWO_SCENARIOS_RAW
        result = parse_scenarios(raw)
        assert len(result) == 2

    def test_extra_leading_delimiter_ignored(self):
        raw = "===SCENARIO===\n===SCENARIO===\n" + """\
ID: UAT-X-1
TITLE: Valid
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: OK
ESTIMATED TIME: 1
NOTES: -"""
        result = parse_scenarios(raw)
        # Only the block with an ID should be included
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"

    def test_whitespace_stripped_from_values(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1  \nTITLE:   Whitespace Test   \nTYPE:  BOUNDARY  \nPERSONA:  Tester  \nPASS CRITERIA:  Trimmed  \nESTIMATED TIME:  10  \nNOTES: -"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-WS-1"
        assert s["title"] == "Whitespace Test"
        assert s["type"] == "BOUNDARY"
        assert s["persona"] == "Tester"
        assert s["pass_criteria"] == "Trimmed"
        assert s["estimated_time"] == "10"

    def test_missing_optional_fields_do_not_raise(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\nNOTES: minimal"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        assert "title" not in s
        assert "type" not in s

    def test_many_scenarios_returns_correct_count(self):
        blocks = []
        for i in range(20):
            blocks.append(
                f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Bulk {i}\nTYPE: POSITIVE\n"
                f"PERSONA: User\nPASS CRITERIA: OK\nESTIMATED TIME: 2\nNOTES: -"
            )
        raw = "\n".join(blocks)
        result = parse_scenarios(raw)
        assert len(result) == 20
        assert result[0]["id"] == "UAT-BULK-0"
        assert result[-1]["id"] == "UAT-BULK-19"

    def test_scenario_with_colon_in_value(self):
        raw = "===SCENARIO===\nID: UAT-COLON-1\nTITLE: Check URL: https://example.com\nTYPE: POSITIVE\nPERSONA: Admin\nPASS CRITERIA: URL accepted\nESTIMATED TIME: 3\nNOTES: -"
        result = parse_scenarios(raw)
        assert len(result) == 1
        # Title should capture only up to first colon split by the replace logic
        # ID line uses line.replace("TITLE:","") so full remainder is captured
        assert "https://example.com" in result[0]["title"]

    def test_boundary_type_parsed(self):
        raw = "===SCENARIO===\nID: UAT-B-1\nTITLE: Max values\nTYPE: BOUNDARY\nPERSONA: Underwriter\nPASS CRITERIA: Accepted\nESTIMATED TIME: 5\nNOTES: -"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_negative_type_parsed(self):
        raw = "===SCENARIO===\nID: UAT-N-1\nTITLE: Unauthorised access\nTYPE: NEGATIVE\nPERSONA: Guest\nPASS CRITERIA: Access denied\nESTIMATED TIME: 2\nNOTES: -"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_raw_field_contains_full_block(self):
        raw = "===SCENARIO===\nID: UAT-RAW-1\nTITLE: Raw check\nTYPE: POSITIVE\nPERSONA: User\nPASS CRITERIA: OK\nESTIMATED TIME: 1\nNOTES: check raw"
        result = parse_scenarios(raw)
        assert "check raw" in result[0]["raw"]

    def test_unicode_content_handled(self):
        # Simulate Arabic/unicode in persona field (from ar-SA.json translations)
        raw = "===SCENARIO===\nID: UAT-UNI-1\nTITLE: Arabic UI test\nTYPE: POSITIVE\nPERSONA: \u0645\u0633\u062a\u062e\u062f\u0645\nPASS CRITERIA: \u062a\u0623\u0643\u064a\u062f\nESTIMATED TIME: 3\nNOTES: -"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["persona"] == "\u0645\u0633\u062a\u062e\u062f\u0645"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_header_row_present(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_list_produces_header_only(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # header only (trailing newline may add empty row)

    def test_single_scenario_produces_two_rows(self):
        scenarios = [{
            "id": "UAT-STORY1-1",
            "title": "Positive flow",
            "type": "POSITIVE",
            "persona": "Underwriter",
            "pass_criteria": "Classification matches",
            "estimated_time": "5",
        }]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_rows = [r for r in rows if r]  # skip blank trailing row
        assert len(data_rows) == 2
        assert data_rows[1][0] == "UAT-STORY1-1"
        assert data_rows[1][1] == "Positive flow"
        assert data_rows[1][2] == "POSITIVE"
        assert data_rows[1][3] == "Underwriter"
        assert data_rows[1][4] == "Classification matches"
        assert data_rows[1][5] == "5"

    def test_result_tester_notes_defect_ref_are_empty(self):
        scenarios = [{"id": "UAT-1", "title": "T", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "C", "estimated_time": "1"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        data = [r for r in rows if r][1]
        assert data[6] == ""   # Result
        assert data[7] == ""   # Tester
        assert data[8] == ""   # Notes
        assert data[9] == ""   # Defect Ref

    def test_multiple_scenarios(self):
        scenarios = [
            {"id": f"UAT-{i}", "title": f"Scenario {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": str(i)}
            for i in range(1, 6)
        ]
        rows = [r for r in self._parse_csv(build_test_pack_csv(scenarios)) if r]
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_fields_produce_empty_strings(self):
        scenarios = [{"id": "UAT-MISS-1"}]  # only id present
        rows = [r for r in self._parse_csv(build_test_pack_csv(scenarios)) if r]
        data = rows[1]
        assert data[0] == "UAT-MISS-1"
        assert data[1] == ""  # title missing
        assert data[2] == ""  # type missing

    def test_special_characters_in_csv(self):
        scenarios = [{
            "id": "UAT-SPEC-1",
            "title": 'Title with "quotes" and, commas',
            "type": "POSITIVE",
            "persona": "Tester",
            "pass_criteria": "OK",
            "estimated_time": "3",
        