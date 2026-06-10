"""
Tests for tool5_uat.py
======================
What is tested:
  - parse_scenarios(): happy path, edge cases, empty input, missing fields, malformed blocks
  - build_test_pack_csv(): correct headers, rows, missing fields, empty list
  - build_test_pack_md(): output structure, version/owner/repo substitution
  - get_results_csv(): successful fetch, missing file (FileNotFoundError), malformed response
  - Integration smoke: module-level constants and imports from shared are present

Mocks used:
  - unittest.mock.patch for requests.get (get_results_csv)
  - unittest.mock.patch for base64.b64decode
  - shared module dependencies are NOT imported directly; only the public functions
    exposed by tool5_uat are exercised

TODOs:
  - TODO: call_claude / full generate/analyse pipeline requires a live Claude API key
          — integration tests skipped below
  - TODO: write_output_file / send_email / write_audit_entry side-effects need
          OUTPUT_REPO_OWNER, OUTPUT_REPO, GH_HEADERS, GH_API env vars — skipped below
  - TODO: __main__ block (CLI entrypoint) requires full env var matrix + mocked I/O
          — skipped below
"""

import base64
import csv
import io
import json
import os
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so we can import tool5_uat without a
# real GitHub token or network connection.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = lambda s: s
shared_stub.call_claude = MagicMock(return_value="stub")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer stub"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib

tool5_uat = importlib.import_module(
    "tool5_uat" if "tool5_uat" in sys.modules else ".github.scripts.tool5_uat".lstrip(".")
)

# Fallback: direct file-based import
if tool5_uat is None:  # pragma: no cover
    import importlib.util, pathlib

    _spec = importlib.util.spec_from_file_location(
        "tool5_uat",
        pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py",
    )
    tool5_uat = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(tool5_uat)

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Enterprise Admin
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: alice.chen@example.com / P@ssw0rd!
STEPS:
1. Navigate to /login
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Redirect to dashboard
PASS CRITERIA: Dashboard loads within 3 s
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid login
TYPE: POSITIVE
PERSONA: Consumer
PASS CRITERIA: Dashboard visible
ESTIMATED TIME: 3
NOTES: -
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid password
TYPE: NEGATIVE
PERSONA: Consumer
PASS CRITERIA: Error message shown
ESTIMATED TIME: 3
NOTES: -
"""

SCENARIO_MISSING_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Something
ESTIMATED TIME: 2
NOTES: -
"""

SCENARIO_PARTIAL_FIELDS = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Only ID and title present
"""


@pytest.fixture()
def sample_scenarios() -> list[dict]:
    return parse_scenarios(TWO_SCENARIO_BLOCK)


# ===========================================================================
# parse_scenarios
# ===========================================================================


class TestParseScenarios:
    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("some text without the delimiter")
        assert result == []

    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Happy path login"

    def test_single_scenario_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Enterprise Admin"

    def test_single_scenario_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Dashboard loads within 3 s"

    def test_single_scenario_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_field_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_two_scenarios_returns_two_items(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_two_scenarios_ids_correct(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(SCENARIO_MISSING_ID)
        assert result == []

    def test_partial_fields_still_returns_scenario_with_id(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-1"

    def test_partial_fields_missing_keys_absent(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        s = result[0]
        assert "type" not in s
        assert "persona" not in s

    def test_only_delimiter_no_content_returns_empty(self):
        result = parse_scenarios("===SCENARIO===")
        assert result == []

    def test_multiple_delimiters_only_scenario_with_id_included(self):
        raw = "===SCENARIO===\n\n===SCENARIO===\nID: UAT-X-1\nTITLE: T\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"

    def test_whitespace_stripped_from_id(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE: Test\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"

    def test_whitespace_stripped_from_title(self):
        raw = "===SCENARIO===\nID: UAT-WS-2\nTITLE:   Trimmed Title   \n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Trimmed Title"

    def test_type_negative(self):
        raw = "===SCENARIO===\nID: UAT-N-1\nTYPE: NEGATIVE\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_type_boundary(self):
        raw = "===SCENARIO===\nID: UAT-B-1\nTYPE: BOUNDARY\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    @pytest.mark.parametrize("scenario_id", [
        "UAT-CUST001-1",
        "UAT-CUST007-1",  # invalid email customer
        "UAT-CUST008-1",  # age -1 boundary
    ])
    def test_various_id_formats_parsed(self, scenario_id):
        raw = f"===SCENARIO===\nID: {scenario_id}\nTITLE: Test\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == scenario_id

    def test_raw_contains_original_block(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "Navigate to /login" in result[0]["raw"]

    def test_leading_text_before_first_delimiter_ignored(self):
        raw = "Some preamble text\n===SCENARIO===\nID: UAT-Z-1\nTITLE: After\n"
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_large_number_of_scenarios(self):
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
            for i in range(50)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 50

    def test_unicode_in_title(self):
        raw = "===SCENARIO===\nID: UAT-UNI-1\nTITLE: Tëst wïth ünicodé\n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Tëst wïth ünicodé"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    def _parse_csv(self, csv_text: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_text))
        return list(reader)

    def test_returns_string(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        assert isinstance(result, str)

    def test_has_header_row(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        assert rows[0][0] == "Scenario ID"

    def test_header_has_ten_columns(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        assert len(rows[0]) == 10

    def test_header_contains_result_column(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        assert "Result (PASS/FAIL/BLOCKED)" in rows[0]

    def test_row_count_matches_scenarios(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        # 1 header + N scenarios
        assert len(rows) == len(sample_scenarios) + 1

    def test_scenario_id_in_first_data_row(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        assert rows[1][0] == "UAT-STORY1-1"

    def test_scenario_title_in_data_row(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        assert rows[1][1] == "Valid login"

    def test_result_column_is_empty_in_data_rows(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        # Result column index = 6
        for row in rows[1:]:
            assert row[6] == ""

    def test_tester_column_is_empty(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        for row in rows[1:]:
            assert row[7] == ""

    def test_empty_scenarios_list_produces_header_only(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1
        assert rows[0][0] == "Scenario ID"

    def test_missing_optional_fields_produce_empty_strings(self):
        minimal = [{"id": "UAT-MIN-1", "raw": "ID: UAT-MIN-1"}]
        rows = self._parse_csv(build_test_pack_csv(minimal))
        assert rows[1][1] == ""  # title missing → empty
        assert rows[1][2] == ""  # type missing → empty

    def test_pass_criteria_written_to_correct_column(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        # pass_criteria is column index 4
        assert rows[1][4] == "Dashboard visible"

    def test_estimated_time_written_to_correct_column(self, sample_scenarios):
        rows = self._parse_csv(build_test_pack_csv(sample_scenarios))
        assert rows[1][5] == "3"

    def test_csv_is_parseable_as_valid_csv(self, sample_scenarios):
        raw = build_test_pack_csv(sample_scenarios)
        try:
            rows = self._parse_csv(raw)
        except csv.Error:
            pytest.fail("Output is not valid CSV")
        assert len(rows) > 1

    def test_scenario_with_commas_in_title_is_escaped(self):
        scenarios = [{"id": "UAT-C-1", "title": "Login, then logout", "raw": ""}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][1] == "Login, then logout"

    def test_scenario_with_newline_in_notes_handled(self):
        scenarios = [{"id": "UAT-NL-1", "title": "Test", "raw": "multi\nline"}]