"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios: happy path, empty input, missing fields, multiple scenarios,
                     blocks without IDs, malformed blocks
  - build_test_pack_csv: correct headers, row content, empty list, special characters
  - build_test_pack_md: correct structure, version/owner/repo substitution,
                        raw content inclusion
  - get_results_csv: success path, missing content key, non-existent path
  - __main__ block: not directly tested (integration concern); stubs provided

Mocks used:
  - unittest.mock.patch for requests.get (get_results_csv)
  - unittest.mock.patch for shared module imports (call_claude, get_repo_files,
    write_output_file, send_email, email_html, write_audit_entry)
  - base64 decoding is exercised with real encoded data in unit tests

TODOs:
  - TODO: Integration test for __main__ block requires GitHub Actions env vars and
          network access — stubs provided with pytest.mark.skip
  - TODO: Tests for SYSTEM_GENERATE and SYSTEM_ANALYSE prompt strings exercised
          through Claude — requires live API key
  - TODO: build_test_pack_md timestamp is non-deterministic; freeze time if precise
          matching is needed (e.g. with freezegun)
"""

import base64
import csv
import io
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Minimal shared-module stub so the import in tool5_uat.py does not fail
# when the real shared.py is absent from the test environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="stub response")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Inject the stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Now import the module under test
scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
sys.path.insert(0, scripts_dir)

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Fixtures & Helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Underwriter submits a valid insurance application
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is accessible
- Test customer CUST00000001 exists
TEST DATA: Age=35, Annual_Income=85000, Employment_Status=permanent
STEPS:
1. Log in as Underwriter
2. Navigate to New Application
3. Submit form with valid data
EXPECTED RESULT: Application is accepted and risk classification returned
PASS CRITERIA: Risk_Classification field is populated and status = ACCEPTED
ESTIMATED TIME: 10
NOTES: Depends on model_card.json being loaded
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid application submission
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is online
TEST DATA: Age=35
STEPS:
1. Submit form
EXPECTED RESULT: Success
PASS CRITERIA: Status=200
ESTIMATED TIME: 5
NOTES: None
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid income value
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is online
TEST DATA: Annual_Income=-1
STEPS:
1. Submit form with negative income
EXPECTED RESULT: Validation error
PASS CRITERIA: Error message shown
ESTIMATED TIME: 3
NOTES: Boundary
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Empty application form
TYPE: BOUNDARY
PERSONA: Underwriter
PRE-CONDITIONS:
- System is online
TEST DATA: All fields empty
STEPS:
1. Click Submit without filling form
EXPECTED RESULT: All required field errors shown
PASS CRITERIA: At least 1 validation error per required field
ESTIMATED TIME: 2
NOTES: Edge case
"""


def _make_scenario(
    sid="UAT-S1-1",
    title="Test Title",
    stype="POSITIVE",
    persona="Tester",
    pass_criteria="Status OK",
    estimated_time="5",
    raw="raw block",
):
    return {
        "id": sid,
        "title": title,
        "type": stype,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": raw,
    }


# ===========================================================================
# parse_scenarios
# ===========================================================================


class TestParseScenarios:

    def test_single_scenario_all_fields_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Underwriter submits a valid insurance application"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Risk_Classification field is populated and status = ACCEPTED"
        assert s["estimated_time"] == "10"

    def test_single_scenario_raw_included(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_multiple_scenarios_parsed(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3

    def test_multiple_scenarios_ids(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_multiple_scenarios_types(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        types_found = {s["type"] for s in result}
        assert "POSITIVE" in types_found
        assert "NEGATIVE" in types_found
        assert "BOUNDARY" in types_found

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_scenario_delimiter_returns_empty_list(self):
        result = parse_scenarios("Some random text without any delimiter")
        assert result == []

    def test_block_without_id_is_excluded(self):
        raw = """\
===SCENARIO===
TITLE: No ID here
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Pass
ESTIMATED TIME: 5
NOTES: None
"""
        result = parse_scenarios(raw)
        assert result == []

    def test_partial_fields_missing_title(self):
        raw = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TYPE: NEGATIVE
PERSONA: Admin
PASS CRITERIA: Error shown
ESTIMATED TIME: 2
NOTES: Partial scenario
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert "title" not in s or s.get("title", None) is None or s.get("title") == ""

    def test_leading_whitespace_in_delimiter_handled(self):
        raw = "\n\n===SCENARIO===\nID: UAT-WS-1\nTITLE: Whitespace Test\nTYPE: POSITIVE\nPERSONA: User\nPASS CRITERIA: OK\nESTIMATED TIME: 1\nNOTES:\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_delimiter_at_start_empty_first_block_ignored(self):
        raw = "===SCENARIO===\nID: UAT-FIRST-1\nTITLE: First\nTYPE: POSITIVE\nPERSONA: User\nPASS CRITERIA: OK\nESTIMATED TIME: 1\nNOTES:\n"
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_scenario_with_extra_lines_does_not_crash(self):
        raw = """\
===SCENARIO===
ID: UAT-EXTRA-1
TITLE: Extra Lines Scenario
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Condition 1
- Condition 2
TEST DATA: Age=42, Annual_Income=100000
STEPS:
1. Step one
2. Step two
EXPECTED RESULT: Success
PASS CRITERIA: Response code 200
ESTIMATED TIME: 7
NOTES: Uses CUST00000001 from customer_similarity_dict.json
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-EXTRA-1"

    def test_unicode_values_parsed_correctly(self):
        """Covers Arabic locale data from frontend translations."""
        raw = "===SCENARIO===\nID: UAT-AR-1\nTITLE: Arabic locale test \u0625\u0644\u063a\u0627\u0621\nTYPE: POSITIVE\nPERSONA: \u0645\u0633\u062a\u062e\u062f\u0645\nPASS CRITERIA: \u062a\u0623\u0643\u064a\u062f\nESTIMATED TIME: 5\nNOTES:\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert "\u0625\u0644\u063a\u0627\u0621" in result[0]["title"]

    def test_id_with_whitespace_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-1   \nTITLE: Spaced\nTYPE: POSITIVE\nPERSONA: User\nPASS CRITERIA: OK\nESTIMATED TIME: 1\nNOTES:\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"

    def test_many_scenarios_performance(self):
        """100 scenarios should parse without error."""
        blocks = []
        for i in range(100):
            blocks.append(
                f"===SCENARIO===\nID: UAT-PERF-{i}\nTITLE: Perf {i}\nTYPE: POSITIVE\n"
                f"PERSONA: Tester\nPASS CRITERIA: OK\nESTIMATED TIME: 1\nNOTES:\n"
            )
        result = parse_scenarios("\n".join(blocks))
        assert len(result) == 100


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:

    def test_header_row_correct(self):
        csv_output = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(csv_output))
        header = next(reader)
        assert header == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_empty_scenarios_only_header(self):
        csv_output = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(csv_output))
        rows = list(reader)
        assert len(rows) == 1  # only header

    def test_single_scenario_row_content(self):
        s = _make_scenario()
        csv_output = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_output))
        next(reader)  # skip header
        row = next(reader)
        assert row[0] == "UAT-S1-1"
        assert row[1] == "Test Title"
        assert row[2] == "POSITIVE"
        assert row[3] == "Tester"
        assert row[4] == "Status OK"
        assert row[5] == "5"

    def test_result_tester_notes_defect_columns_empty(self):
        s = _make_scenario()
        csv_output = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_output))
        next(reader)
        row = next(reader)
        assert row[6] == ""  # Result
        assert row[7] == ""  # Tester
        assert row[8] == ""  # Notes
        assert row[9] == ""  # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(sid=f"UAT-S1-{i}") for i in range(5)]
        csv_output = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(csv_output))
        rows = list(reader)
        assert len(rows) == 6  # 1 header + 5 data

    def test_scenario_missing_optional_fields_uses_empty_string(self):
        s = {"id": "UAT-MIN-1", "raw": "minimal"}
        csv_output = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_output))
        next(reader)
        row = next(reader)
        assert row[0] == "UAT-MIN-1"
        assert row[1] == ""  # title missing
        assert row[2] == ""  # type missing

    def test_special_characters_in_fields(self):
        """Commas and quotes inside fields must be escaped properly."""
        s = _make_scenario(
            title='Application with "quotes" and, comma',
            pass_criteria="Result > 0 and status = 'OK'",
        )
        csv_output = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_output))
        next(reader)
        row = next(reader)
        assert 'quotes' in row[1]
        assert 'comma' in row[1]

    def test_unicode_in_csv(self):
        s = _make_scenario(title="\u0625\u0644\u063a\u0627\u0621", persona="\u0645\u0633\u062a\u062e\u062f\u0645")
        csv_output = build_test_pack_csv([s])