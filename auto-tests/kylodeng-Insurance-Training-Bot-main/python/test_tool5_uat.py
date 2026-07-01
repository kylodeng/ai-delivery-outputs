"""
Test suite for tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, empty/malformed input, boundary values
    - build_test_pack_csv(): structure, headers, data rows, empty scenarios list
    - build_test_pack_md(): content, formatting, version/owner/repo injection
    - get_results_csv(): happy path (mocked HTTP), missing content key, decode errors
    - Module-level __main__ block is NOT directly tested (requires full env wiring)

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
    - unittest.mock.patch for `shared.*` imports (call_claude, write_output_file,
      send_email, get_repo_files, write_audit_entry, email_html)
    - base64 encoding/decoding verified inline without network calls

TODOs:
    - TODO: Integration test for __main__ block requires full GitHub env vars + secrets
    - TODO: Test call_claude integration path once shared.py test doubles are established
    - TODO: Verify SYSTEM_GENERATE / SYSTEM_ANALYSE prompts against LLM contract tests
    - TODO: Test parse_scenarios with non-ASCII / multi-byte characters (i18n)
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
# Minimal stub for the `shared` module so the import in tool5_uat succeeds
# without requiring the real shared.py or its transitive dependencies.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.clean_json = MagicMock(side_effect=lambda x: x)
    mod.call_claude = MagicMock(return_value="mocked-claude-response")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value=None)
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html/>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    mod.GH_HEADERS = {"Authorization": "Bearer fake-token"}
    mod.GH_API = "https://api.github.com"
    return mod


# Inject stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now safe to import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

# We import selectively to avoid executing __main__
import importlib, unittest.mock as _um

# Patch requests at module level during import so __main__ block is not triggered
with _um.patch.dict(os.environ, {"UAT_MODE": "generate"}):
    import importlib.util, pathlib

    _script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"
    _spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
    tool5_uat = importlib.util.module_from_spec(_spec)
    # Prevent __main__ execution
    tool5_uat.__name__ = "tool5_uat"
    _spec.loader.exec_module(tool5_uat)

parse_scenarios   = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md  = tool5_uat.build_test_pack_md
get_results_csv     = tool5_uat.get_results_csv


# ===========================================================================
# Fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-001
TITLE: Policyholder views Generations II product brochure
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- User is authenticated
- Generations II policy is active
TEST DATA: policy_number=G2-12345, dob=1980-01-01
STEPS:
1. Navigate to My Policies
2. Select Generations II policy
3. Click "View Brochure"
EXPECTED RESULT: Brochure PDF opens in new tab
PASS CRITERIA: PDF loads within 3 seconds and displays product name
ESTIMATED TIME: 5
NOTES: Depends on CDN availability
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-HEALTH-001
TITLE: Search designated hospitals in Mainland China
TYPE: POSITIVE
PERSONA: Policyholder with health plan
PRE-CONDITIONS:
- User logged in
TEST DATA: city=Shanghai, hospital_class=Class3
STEPS:
1. Open hospital search
2. Filter by city "Shanghai"
3. Select Class 3 option
EXPECTED RESULT: List of Class 3 hospitals in Shanghai shown
PASS CRITERIA: At least one result displayed
ESTIMATED TIME: 3
NOTES: None
===SCENARIO===
ID: UAT-HEALTH-002
TITLE: Search with invalid city name
TYPE: NEGATIVE
PERSONA: Policyholder with health plan
PRE-CONDITIONS:
- User logged in
TEST DATA: city=INVALID_CITY_XYZ
STEPS:
1. Open hospital search
2. Enter "INVALID_CITY_XYZ" in city field
3. Submit search
EXPECTED RESULT: Error message displayed
PASS CRITERIA: System shows "No results found" and does not crash
ESTIMATED TIME: 2
NOTES: Boundary case for search
===SCENARIO===
ID: UAT-HEALTH-003
TITLE: Search with empty city input
TYPE: BOUNDARY
PERSONA: Policyholder with health plan
PRE-CONDITIONS:
- User logged in
TEST DATA: city=<empty>
STEPS:
1. Open hospital search
2. Leave city field blank
3. Submit search
EXPECTED RESULT: Validation error shown
PASS CRITERIA: System prompts user to enter a city
ESTIMATED TIME: 2
NOTES: Empty input boundary
"""


@pytest.fixture()
def single_scenario_raw():
    return SINGLE_SCENARIO_BLOCK


@pytest.fixture()
def multi_scenario_raw():
    return MULTI_SCENARIO_RAW


@pytest.fixture()
def parsed_single(single_scenario_raw):
    return parse_scenarios(single_scenario_raw)


@pytest.fixture()
def parsed_multi(multi_scenario_raw):
    return parse_scenarios(multi_scenario_raw)


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:

    def test_returns_list(self, parsed_single):
        assert isinstance(parsed_single, list)

    def test_single_scenario_count(self, parsed_single):
        assert len(parsed_single) == 1

    def test_single_id_parsed(self, parsed_single):
        assert parsed_single[0]["id"] == "UAT-GEN2-001"

    def test_single_title_parsed(self, parsed_single):
        assert parsed_single[0]["title"] == "Policyholder views Generations II product brochure"

    def test_single_type_parsed(self, parsed_single):
        assert parsed_single[0]["type"] == "POSITIVE"

    def test_single_persona_parsed(self, parsed_single):
        assert parsed_single[0]["persona"] == "Existing policyholder"

    def test_single_pass_criteria_parsed(self, parsed_single):
        assert "PDF loads" in parsed_single[0]["pass_criteria"]

    def test_single_estimated_time_parsed(self, parsed_single):
        assert parsed_single[0]["estimated_time"] == "5"

    def test_raw_field_present(self, parsed_single):
        assert "raw" in parsed_single[0]
        assert len(parsed_single[0]["raw"]) > 0

    def test_raw_contains_original_content(self, parsed_single):
        assert "Navigate to My Policies" in parsed_single[0]["raw"]

    def test_multi_scenario_count(self, parsed_multi):
        assert len(parsed_multi) == 3

    def test_multi_ids(self, parsed_multi):
        ids = [s["id"] for s in parsed_multi]
        assert ids == ["UAT-HEALTH-001", "UAT-HEALTH-002", "UAT-HEALTH-003"]

    def test_multi_types(self, parsed_multi):
        types_ = [s["type"] for s in parsed_multi]
        assert "POSITIVE" in types_
        assert "NEGATIVE" in types_
        assert "BOUNDARY" in types_

    def test_multi_personas_all_present(self, parsed_multi):
        for s in parsed_multi:
            assert s.get("persona") == "Policyholder with health plan"

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        # A block without the ===SCENARIO=== delimiter has no ID so should be skipped
        result = parse_scenarios("ID: UAT-NODEL-001\nTITLE: Test without delimiter\n")
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===\n\n===SCENARIO===\n\n")
        assert result == []

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_block_with_id_only_is_included(self):
        raw = "===SCENARIO===\nID: UAT-MIN-001\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-001"

    def test_missing_optional_fields_default_absent(self):
        raw = "===SCENARIO===\nID: UAT-PARTIAL-001\nTITLE: Partial scenario\n"
        result = parse_scenarios(raw)
        assert result[0].get("type") is None
        assert result[0].get("persona") is None
        assert result[0].get("pass_criteria") is None
        assert result[0].get("estimated_time") is None

    def test_extra_whitespace_in_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-001   \nTITLE:   Spaced Title   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-001"
        assert result[0]["title"] == "Spaced Title"

    def test_many_scenarios_performance(self):
        """Boundary: 100 scenarios should parse without error."""
        block = ""
        for i in range(100):
            block += f"===SCENARIO===\nID: UAT-PERF-{i:03d}\nTITLE: Perf test {i}\n\n"
        result = parse_scenarios(block)
        assert len(result) == 100

    def test_scenario_type_negative_preserved(self):
        raw = "===SCENARIO===\nID: UAT-NEG-001\nTYPE: NEGATIVE\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_scenario_type_boundary_preserved(self):
        raw = "===SCENARIO===\nID: UAT-BOUND-001\nTYPE: BOUNDARY\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_lines_not_matching_any_key_are_ignored(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-IGNORE-001\n"
            "UNKNOWN_FIELD: some value\n"
            "ANOTHER: value\n"
        )
        result = parse_scenarios(raw)
        assert "UNKNOWN_FIELD" not in result[0]
        assert result[0]["id"] == "UAT-IGNORE-001"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str):
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_returns_string(self, parsed_multi):
        result = build_test_pack_csv(parsed_multi)
        assert isinstance(result, str)

    def test_header_row_correct(self, parsed_multi):
        rows = self._parse_csv(build_test_pack_csv(parsed_multi))
        header = rows[0]
        assert header[0] == "Scenario ID"
        assert header[1] == "Title"
        assert header[2] == "Type"
        assert header[3] == "Persona"
        assert header[4] == "Pass Criteria"
        assert header[5] == "Est. Time (min)"
        assert header[6] == "Result (PASS/FAIL/BLOCKED)"
        assert header[7] == "Tester"
        assert header[8] == "Notes"
        assert header[9] == "Defect Ref"

    def test_row_count_matches_scenarios_plus_header(self, parsed_multi):
        rows = self._parse_csv(build_test_pack_csv(parsed_multi))
        # header + 3 scenarios
        assert len(rows) == 4

    def test_data_rows_have_correct_ids(self, parsed_multi):
        rows = self._parse_csv(build_test_pack_csv(parsed_multi))
        ids = [row[0] for row in rows[1:]]
        assert ids == ["UAT-HEALTH-001", "UAT-HEALTH-002", "UAT-HEALTH-003"]

    def test_result_column_empty_by_default(self, parsed_multi):
        rows = self._parse_csv(build_test_pack_csv(parsed_multi))
        for row in rows[1:]:
            assert row[6] == ""

    def test_tester_column_empty_by_default(self, parsed_multi):
        rows = self._parse_csv(build_test_pack_csv(parsed_multi))
        for row in rows[1:]:
            assert row[7] == ""

    def test_defect_ref_column_empty_by_default(self, parsed_multi):
        rows = self._parse_csv(build_test_pack_csv(parsed_multi))
        for row in rows[1:]:
            assert row[9] == ""

    def test_empty_scenarios_list_returns_header_only(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # header row only
        assert rows[0][0] == "Scenario ID"

    def test_single_scenario_csv(self, parsed_single):
        rows = self._parse_csv(build_test_pack_csv(parsed_single))
        assert len(rows) == 2
        assert rows[1][0] == "UAT-GEN2-001"

    def test_missing_fields_produce_empty_strings(self):
        scenarios = [{"id": "UAT-MISSING-001", "raw": ""}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][1] == ""   # title
        assert rows[1][2] == ""   # type
        assert rows[1][3] == ""   # persona
        assert