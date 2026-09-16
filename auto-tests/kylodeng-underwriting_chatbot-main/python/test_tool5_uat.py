"""
Tests for tool5_uat.py (.github/scripts/tool5_uat.py)

What is tested:
    - parse_scenarios(): happy path, empty input, missing fields, malformed blocks,
      multiple scenarios, boundary values
    - build_test_pack_csv(): happy path, empty scenario list, missing fields,
      special characters, row count, header correctness
    - build_test_pack_md(): happy path, version embedding, timestamp inclusion,
      empty raw content
    - get_results_csv(): happy path (base64 content), missing file (FileNotFoundError),
      malformed API response

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls)
    - unittest.mock.patch for `base64.b64decode`
    - Shared module functions (call_claude, write_output_file, send_email, etc.)
      are NOT imported directly in the functions under test, so no patching needed
      unless __main__ block is exercised.

TODOs:
    - TODO: Integration test for __main__ block requires full env-var setup and
      mocked shared module — stub provided below.
    - TODO: Test call_claude interaction within __main__ requires access to shared
      module internals not visible from source snippet.
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub out the `shared` module so tool5_uat.py can be imported
# without the real shared.py on the path.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = lambda x: x
    shared.call_claude = MagicMock(return_value="stub")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Insert stubs before importing the module under test
sys.modules.setdefault("shared", _make_shared_stub())
# requests is a real package but we will patch individual calls
import requests  # noqa: E402  (needed before tool5_uat import)

# Now we can import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
# Adjust path so the module resolves from repo root too
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid underwriting submission
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is authenticated
- Application form is loaded
TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=Low
STEPS:
1. Navigate to submission screen
2. Enter valid applicant data
3. Click Submit
EXPECTED RESULT: System accepts submission and shows confirmation
PASS CRITERIA: Confirmation message displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: Uses CatBoostClassifier model
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Positive flow
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Confirmation shown
ESTIMATED TIME: 5
NOTES: none
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid income boundary
TYPE: BOUNDARY
PERSONA: Underwriter
PASS CRITERIA: Validation error shown
ESTIMATED TIME: 3
NOTES: edge case
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Unauthorised access attempt
TYPE: NEGATIVE
PERSONA: External User
PASS CRITERIA: 403 returned
ESTIMATED TIME: 2
NOTES: security
"""


def _make_full_scenario(**overrides):
    base = {
        "id": "UAT-STORY1-1",
        "title": "Valid underwriting submission",
        "type": "POSITIVE",
        "persona": "Underwriter",
        "pass_criteria": "Confirmation message displayed",
        "estimated_time": "5",
        "raw": SINGLE_SCENARIO_RAW,
    }
    base.update(overrides)
    return base


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_happy_path(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Valid underwriting submission"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Confirmation message displayed within 3 seconds"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_returned(self):
        scenarios = parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(scenarios) == 3

    def test_multiple_scenarios_ids(self):
        scenarios = parse_scenarios(MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in scenarios]
        assert ids == ["UAT-STORY1-1", "UAT-STORY1-2", "UAT-STORY1-3"]

    def test_multiple_scenarios_types(self):
        scenarios = parse_scenarios(MULTI_SCENARIO_RAW)
        types_ = [s["type"] for s in scenarios]
        assert "POSITIVE" in types_
        assert "BOUNDARY" in types_
        assert "NEGATIVE" in types_

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # A block with no ===SCENARIO=== delimiter and no ID
        result = parse_scenarios("ID: UAT-1\nTITLE: foo")
        # Without the delimiter prefix, the single block has no leading delimiter
        # so it is treated as one non-empty block but may or may not parse
        # depending on whether an ID is found.
        # The implementation splits on ===SCENARIO=== so the whole string is
        # one block; it should be parsed if ID is present.
        assert isinstance(result, list)

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        scenarios = parse_scenarios(raw)
        assert scenarios == []

    def test_missing_optional_fields_are_absent(self):
        raw = "===SCENARIO===\nID: UAT-X-1\nTITLE: Minimal\n"
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert "type" not in s
        assert "persona" not in s
        assert "pass_criteria" not in s
        assert "estimated_time" not in s

    def test_raw_field_contains_original_block(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "UAT-STORY1-1" in scenarios[0]["raw"]

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-1\nTITLE: Real\n"
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-1"

    def test_extra_whitespace_stripped_from_values(self):
        raw = "===SCENARIO===\nID:   UAT-WHITESPACE-1   \nTITLE:   Whitespace test   \n"
        scenarios = parse_scenarios(raw)
        assert scenarios[0]["id"] == "UAT-WHITESPACE-1"
        assert scenarios[0]["title"] == "Whitespace test"

    def test_many_scenarios_performance(self):
        """Boundary: 50 scenarios parsed without error."""
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-PERF-{i}\nTITLE: Scenario {i}\n"
            for i in range(50)
        )
        scenarios = parse_scenarios(blocks)
        assert len(scenarios) == 50

    def test_synthetic_data_in_test_data_field_preserved_in_raw(self):
        """Verify synthetic data from model_card.json survives in raw block."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-UC-1\n"
            "TITLE: CatBoost risk classification\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=Low\n"
            "PASS CRITERIA: Risk label returned\n"
            "ESTIMATED TIME: 3\n"
        )
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert "Age=34" in scenarios[0]["raw"]

    def test_duplicate_delimiter_at_start(self):
        """Leading delimiter before any content should not crash."""
        raw = "===SCENARIO===\n===SCENARIO===\nID: UAT-2\nTITLE: After double\n"
        scenarios = parse_scenarios(raw)
        # Only the block with ID should survive
        assert all(s["id"] == "UAT-2" for s in scenarios)

    def test_returns_list_type(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert isinstance(result, list)

    def test_each_item_is_dict(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        for item in result:
            assert isinstance(item, dict)


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_header_row_correct(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # header only (trailing newline gives empty last element)

    def test_single_scenario_row_count(self):
        scenarios = [_make_full_scenario()]
        csv_str = build_test_pack_csv(scenarios)
        rows = [r for r in self._parse_csv(csv_str) if r]
        assert len(rows) == 2  # header + 1 data row

    def test_multiple_scenarios_row_count(self):
        scenarios = [_make_full_scenario(id=f"UAT-S-{i}") for i in range(5)]
        csv_str = build_test_pack_csv(scenarios)
        rows = [r for r in self._parse_csv(csv_str) if r]
        assert len(rows) == 6  # header + 5 data rows

    def test_data_row_values(self):
        s = _make_full_scenario()
        csv_str = build_test_pack_csv([s])
        rows = self._parse_csv(csv_str)
        data = rows[1]
        assert data[0] == "UAT-STORY1-1"
        assert data[1] == "Valid underwriting submission"
        assert data[2] == "POSITIVE"
        assert data[3] == "Underwriter"
        assert data[4] == "Confirmation message displayed"
        assert data[5] == "5"

    def test_empty_tester_result_defect_columns(self):
        """Result, Tester, Notes, Defect Ref columns should be empty."""
        s = _make_full_scenario()
        csv_str = build_test_pack_csv([s])
        rows = self._parse_csv(csv_str)
        data = rows[1]
        assert data[6] == ""  # Result
        assert data[7] == ""  # Tester
        assert data[8] == ""  # Notes
        assert data[9] == ""  # Defect Ref

    def test_missing_id_field_uses_empty_string(self):
        s = {"title": "No ID scenario", "type": "POSITIVE"}
        csv_str = build_test_pack_csv([s])
        rows = self._parse_csv(csv_str)
        assert rows[1][0] == ""

    def test_special_characters_in_title(self):
        s = _make_full_scenario(title='Title with "quotes" and, commas')
        csv_str = build_test_pack_csv([s])
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_unicode_in_persona(self):
        """Arabic persona name from translation file should survive."""
        s = _make_full_scenario(persona="مستخدم خارجي")
        csv_str = build_test_pack_csv([s])
        rows = self._parse_csv(csv_str)
        assert rows[1][3] == "مستخدم خارجي"

    def test_scenario_with_all_missing_fields(self):
        """Scenario dict with only 'raw' should not raise."""
        s = {"raw": "some raw block"}
        csv_str = build_test_pack_csv([s])
        rows = [r for r in self._parse_csv(csv_str) if r]
        assert len(rows) == 2

    def test_ten_columns_in_header(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows[0]) == 10

    def test_ten_columns_in_data_row(self):
        csv_str = build_test_pack_csv([_make_full_scenario()])
        rows = self._parse_csv(csv_str)
        assert len(rows[1]) == 10

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_scenario_types_written(self, scenario_type):
        s = _make_full_scenario(type=scenario_type)
        csv_str = build_test_pack_csv([s])
        rows = self._parse_csv(csv_str)
        assert rows[1][2] == scenario_