"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, empty input, missing ID, partial fields, multiple scenarios
  - build_test_pack_csv(): column headers, row data, empty list, special characters
  - build_test_pack_md(): markdown structure, metadata injection, empty raw string
  - get_results_csv(): successful fetch and decode, missing content key, network-level errors

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for `base64.b64decode`
  - shared module symbols (call_claude, get_repo_files, write_output_file, send_email,
    email_html, write_audit_entry) are imported from a patched shared module

TODOs:
  - TODO: Integration test for __main__ block requires live env vars and GitHub credentials
  - TODO: Test SYSTEM_GENERATE / SYSTEM_ANALYSE prompt strings for completeness once
          the Claude response contract is formalised
  - TODO: Test mode-switching logic in __main__ (generate vs analyse) once the full
          script body beyond the truncated source is available
"""

import base64
import csv
import io
import json
import sys
import os
import types
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal `shared` stub so the import at module level
# inside tool5_uat.py does not fail during test collection.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.clean_json = MagicMock(side_effect=lambda x: x)
    stub.call_claude = MagicMock(return_value="")
    stub.get_repo_files = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value=None)
    stub.send_email = MagicMock(return_value=None)
    stub.email_html = MagicMock(return_value="<html/>")
    stub.write_audit_entry = MagicMock(return_value=None)
    stub.OUTPUT_REPO_OWNER = "test-owner"
    stub.OUTPUT_REPO = "test-output-repo"
    stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
    stub.GH_API = "https://api.github.com"
    return stub


# Insert the stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Now safe to import
import importlib
tool5 = importlib.import_module(
    "tool5_uat" if "tool5_uat" in sys.modules
    else ".github.scripts.tool5_uat".replace(".", "/")
)

# Re-import cleanly via path manipulation
_scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Final clean import
from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriting risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is logged in
- Application data is complete
TEST DATA: Age=35, Annual_Income=75000, Risk_Classification=Low
STEPS:
1. Navigate to application review screen
2. Click 'Run Risk Assessment'
3. Review classification result
EXPECTED RESULT: System displays 'Low Risk' classification
PASS CRITERIA: Classification label equals 'Low Risk'
ESTIMATED TIME: 5
NOTES: Uses CatBoostClassifier model
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Positive flow
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: System accepts submission
ESTIMATED TIME: 3
NOTES: none

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Negative — unauthorised access
TYPE: NEGATIVE
PERSONA: Guest
PASS CRITERIA: Access denied message shown
ESTIMATED TIME: 2
NOTES: Check 403 response
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: Missing ID scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Something passes
ESTIMATED TIME: 1
NOTES: no id field
"""

PARTIAL_SCENARIO = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Only ID and title present
"""


@pytest.fixture()
def single_parsed():
    return parse_scenarios(SINGLE_SCENARIO_BLOCK)


@pytest.fixture()
def multi_parsed():
    return parse_scenarios(MULTI_SCENARIO_RAW)


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self, single_parsed):
        assert len(single_parsed) == 1

    def test_single_scenario_id(self, single_parsed):
        assert single_parsed[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title(self, single_parsed):
        assert single_parsed[0]["title"] == "Successful underwriting risk classification"

    def test_single_scenario_type(self, single_parsed):
        assert single_parsed[0]["type"] == "POSITIVE"

    def test_single_scenario_persona(self, single_parsed):
        assert single_parsed[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria(self, single_parsed):
        assert single_parsed[0]["pass_criteria"] == "Classification label equals 'Low Risk'"

    def test_single_scenario_estimated_time(self, single_parsed):
        assert single_parsed[0]["estimated_time"] == "5"

    def test_single_scenario_raw_present(self, single_parsed):
        assert "raw" in single_parsed[0]
        assert "UAT-STORY1-1" in single_parsed[0]["raw"]

    def test_multiple_scenarios_count(self, multi_parsed):
        assert len(multi_parsed) == 2

    def test_multiple_scenarios_ids(self, multi_parsed):
        ids = [s["id"] for s in multi_parsed]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_multiple_scenarios_types(self, multi_parsed):
        types_ = [s["type"] for s in multi_parsed]
        assert "POSITIVE" in types_
        assert "NEGATIVE" in types_

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This is just free text with no delimiters.")
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(SCENARIO_WITHOUT_ID)
        assert result == []

    def test_partial_scenario_with_id_included(self):
        result = parse_scenarios(PARTIAL_SCENARIO)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-1"
        assert result[0]["title"] == "Only ID and title present"
        # Optional keys should be absent or empty
        assert result[0].get("type", "") == ""

    def test_whitespace_only_blocks_are_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: WS Test\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_many_scenarios(self):
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
            for i in range(1, 21)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 20

    def test_raw_contains_full_block(self, single_parsed):
        raw = single_parsed[0]["raw"]
        assert "STEPS:" in raw
        assert "EXPECTED RESULT:" in raw

    def test_special_characters_in_title(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-SPEC-1\n"
            "TITLE: Test <script>alert('xss')</script> & 'quotes' & \"double\"\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Test <script>alert('xss')</script> & 'quotes' & \"double\""

    def test_arabic_title_preserved(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-AR-1\n"
            "TITLE: إلغاء العملية\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["title"] == "إلغاء العملية"

    def test_leading_delimiter_produces_empty_first_block(self):
        raw = "===SCENARIO===\nID: UAT-LEAD-1\nTITLE: Lead\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-LEAD-1"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def test_returns_string(self, single_parsed):
        result = build_test_pack_csv(single_parsed)
        assert isinstance(result, str)

    def test_header_row_columns(self, single_parsed):
        result = build_test_pack_csv(single_parsed)
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)
        assert headers[0] == "Scenario ID"
        assert headers[1] == "Title"
        assert headers[2] == "Type"
        assert headers[3] == "Persona"
        assert headers[4] == "Pass Criteria"
        assert headers[5] == "Est. Time (min)"
        assert headers[6] == "Result (PASS/FAIL/BLOCKED)"
        assert headers[7] == "Tester"
        assert headers[8] == "Notes"
        assert headers[9] == "Defect Ref"

    def test_header_has_ten_columns(self, single_parsed):
        result = build_test_pack_csv(single_parsed)
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)
        assert len(headers) == 10

    def test_data_row_count_matches_input(self, multi_parsed):
        result = build_test_pack_csv(multi_parsed)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # 1 header + 2 data rows
        assert len(rows) == 3

    def test_data_row_scenario_id(self, single_parsed):
        result = build_test_pack_csv(single_parsed)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        row = next(reader)
        assert row[0] == "UAT-STORY1-1"

    def test_data_row_title(self, single_parsed):
        result = build_test_pack_csv(single_parsed)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[1] == "Successful underwriting risk classification"

    def test_result_tester_notes_defect_columns_empty(self, single_parsed):
        result = build_test_pack_csv(single_parsed)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[6] == ""
        assert row[7] == ""
        assert row[8] == ""
        assert row[9] == ""

    def test_empty_scenario_list_only_header(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # only header

    def test_scenario_with_missing_fields_uses_empty_string(self):
        sparse = [{"id": "UAT-SPARSE-1", "raw": "ID: UAT-SPARSE-1"}]
        result = build_test_pack_csv(sparse)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[0] == "UAT-SPARSE-1"
        assert row[1] == ""  # title missing
        assert row[2] == ""  # type missing

    def test_csv_valid_parseable(self, multi_parsed):
        result = build_test_pack_csv(multi_parsed)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert all(len(r) == 10 for r in rows)

    def test_special_chars_in_title_csv_encoded(self):
        scenarios = [{
            "id": "UAT-CSV-1",
            "title": 'Risk "High", Age>60',
            "type": "BOUNDARY",
            "persona": "Underwriter",
            "pass_criteria": "pass",
            "estimated_time": "2",
            "raw": "",
        }]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[1] == 'Risk "High", Age>60'

    def test_large_input_performance(self):
        """Boundary: 500 scenarios should still produce valid CSV."""
        scenarios = [
            {"id": f"UAT-PERF-{i}", "title": f"Scenario {i}", "type": "POSITIVE",
             "persona": "Admin", "pass_criteria": "pass", "estimated_time": "1", "raw": ""}
            for i in range(500)
        ]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 501  # header + 500 data rows


# ===========================================================================
# Tests: build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd:

    def test_returns_string(self):
        result = build_test_pack_md("raw content", "my-org", "my-repo", "1.2.3")
        assert isinstance(result, str)

    def test_title_contains_owner_and_repo(self):
        result = build_test_pack_md("raw", "acme", "platform", "2.0.0")
        assert "acme/platform" in result

    def test_title_contains_version(self):
        result = build_test_pack_md("raw", "acme", "platform", "2.0.0")
        assert "2.0.0" in result

    def test_raw_content_embedded(self):
        raw = "===SCENARIO===\nID: UAT-MD-1\nTITLE: Markdown test\n"
        result = build_test_pack_md(raw, "org", "repo", "0.1.0")
        assert raw in result

    def test