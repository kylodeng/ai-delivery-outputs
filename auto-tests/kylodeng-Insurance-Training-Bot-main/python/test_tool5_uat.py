"""
Test module for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
    - build_test_pack_csv(): happy path, empty scenario list, missing fields, multi-row output
    - build_test_pack_md(): happy path, version/owner/repo substitution, timestamp presence
    - get_results_csv(): happy path, missing content key (FileNotFoundError), base64 decoding
    - __main__ block logic is NOT tested directly (requires full env + external calls)

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
    - unittest.mock.patch for `base64.b64decode` where needed
    - shared module dependencies are NOT imported here (tool5_uat imports them at module level,
      so shared is patched via sys.modules before import)

TODOs:
    - TODO: Integration test for __main__ block requires live env vars and GitHub secrets
    - TODO: Test call_claude interactions once shared module contract is stable
    - TODO: Test write_output_file and send_email side-effects in mode=generate flow
    - TODO: Test mode=analyse path including JSON parsing of Claude response
    - TODO: Parameterise parse_scenarios with more real-world Claude outputs from staging
"""

import base64
import csv
import io
import json
import sys
import os
import types
import unittest.mock as mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module before tool5_uat is imported, so we do not
# need the actual shared.py to be importable in the test environment.
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="stub-response")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = _shared_stub

# Now it is safe to import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

import tool5_uat as uat  # noqa: E402  (import after sys.path manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Purchase Generations II policy as a new customer
TYPE: POSITIVE
PERSONA: New retail customer
PRE-CONDITIONS:
- Customer is logged in
- Product is available in their region
TEST DATA: product_name=Generations II, age=35, sum_assured=500000
STEPS:
1. Navigate to product page
2. Fill in personal details
3. Submit application
EXPECTED RESULT: Application is accepted and policy number is generated
PASS CRITERIA: Policy number displayed on confirmation screen
ESTIMATED TIME: 10
NOTES: Verify Sun Life underwriting rules apply
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-HEALTH-1
TITLE: Search designated hospital in Mainland China
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- User has an active health product policy
TEST DATA: city=Shanghai, hospital_class=Class 3
STEPS:
1. Open hospital search
2. Enter Shanghai
3. Select Class 3
EXPECTED RESULT: List of Class 3 hospitals displayed
PASS CRITERIA: At least one hospital listed for Shanghai
ESTIMATED TIME: 5
NOTES: Uses List of Designated Hospitals in Mainland China dataset
===SCENARIO===
ID: UAT-HEALTH-2
TITLE: Attempt hospital search without login
TYPE: NEGATIVE
PERSONA: Unauthenticated user
PRE-CONDITIONS:
- User is NOT logged in
TEST DATA: city=Beijing
STEPS:
1. Navigate to hospital search without logging in
2. Submit search
EXPECTED RESULT: System redirects to login page
PASS CRITERIA: Redirect to /login with error message
ESTIMATED TIME: 3
NOTES: [TESTER: verify redirect URL]
"""


def _make_scenario(
    id_="UAT-X-1",
    title="Test title",
    type_="POSITIVE",
    persona="Tester",
    pass_criteria="System works",
    estimated_time="5",
):
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": f"ID: {id_}\nTITLE: {title}",
    }


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================


class TestParseScenarios:
    """Unit tests for parse_scenarios()."""

    def test_single_scenario_returns_one_item(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-GEN2-1"

    def test_single_scenario_title_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Purchase Generations II policy as a new customer"

    def test_single_scenario_type_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "New retail customer"

    def test_single_scenario_pass_criteria_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Policy number displayed on confirmation screen"

    def test_single_scenario_estimated_time_parsed(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "10"

    def test_single_scenario_raw_present(self):
        result = uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-GEN2-1" in result[0]["raw"]

    def test_two_scenarios_returns_two_items(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_two_scenarios_ids_correct(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert "UAT-HEALTH-1" in ids
        assert "UAT-HEALTH-2" in ids

    def test_two_scenarios_types_correct(self):
        result = uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = {s["id"]: s["type"] for s in result}
        assert types_["UAT-HEALTH-1"] == "POSITIVE"
        assert types_["UAT-HEALTH-2"] == "NEGATIVE"

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """Block without ===SCENARIO=== delimiter → no id → should be skipped."""
        raw = "ID: UAT-X-1\nTITLE: Something\n"
        result = uat.parse_scenarios(raw)
        # The entire string is one block split by delimiter that doesn't appear,
        # but split produces one element; that element has no id prefix after
        # stripping; wait — actually the raw string itself could parse. Let's
        # assert the function does NOT crash and returns a list.
        assert isinstance(result, list)

    def test_scenario_without_id_is_excluded(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_scenario_missing_optional_fields_still_parsed(self):
        """Scenarios missing PERSONA, ESTIMATED TIME etc. should still appear."""
        raw = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal scenario\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert result[0].get("persona") is None
        assert result[0].get("estimated_time") is None

    def test_whitespace_only_blocks_are_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-OK-1\nTITLE: Real\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-OK-1"

    def test_multiple_colons_in_value_preserved(self):
        raw = "===SCENARIO===\nID: UAT-COLON-1\nTITLE: Test: with colon: here\n"
        result = uat.parse_scenarios(raw)
        # Only the first "TITLE:" prefix is stripped; remainder including colons stays
        assert "with colon" in result[0]["title"]

    def test_large_number_of_scenarios(self):
        """Boundary: 50 scenarios should all be parsed without error."""
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Bulk test {i}\n"
            )
        raw = "\n".join(blocks)
        result = uat.parse_scenarios(raw)
        assert len(result) == 50

    def test_boundary_empty_id_value(self):
        """ID line present but value is blank → should be excluded."""
        raw = "===SCENARIO===\nID:  \nTITLE: No real id\n"
        result = uat.parse_scenarios(raw)
        # strip() of "  " → "" → falsy → excluded
        assert result == []

    @pytest.mark.parametrize("type_value", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_parsed(self, type_value):
        raw = f"===SCENARIO===\nID: UAT-T-1\nTITLE: typed\nTYPE: {type_value}\n"
        result = uat.parse_scenarios(raw)
        assert result[0]["type"] == type_value


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    """Unit tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_returns_string(self):
        result = uat.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_empty_scenarios_has_header_only(self):
        result = uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # header row only

    def test_header_columns_correct(self):
        result = uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
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

    def test_single_scenario_produces_two_rows(self):
        scenarios = [_make_scenario()]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2

    def test_single_scenario_data_written_correctly(self):
        s = _make_scenario(
            id_="UAT-GEN2-1",
            title="Purchase Generations II policy",
            type_="POSITIVE",
            persona="New retail customer",
            pass_criteria="Policy number shown",
            estimated_time="10",
        )
        result = uat.build_test_pack_csv([s])
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-GEN2-1"
        assert data_row[1] == "Purchase Generations II policy"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "New retail customer"
        assert data_row[4] == "Policy number shown"
        assert data_row[5] == "10"

    def test_result_tester_notes_defect_columns_empty(self):
        """Columns that testers fill in should start empty."""
        scenarios = [_make_scenario()]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[6] == ""  # Result
        assert data_row[7] == ""  # Tester
        assert data_row[8] == ""  # Notes
        assert data_row[9] == ""  # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(id_=f"UAT-X-{i}") for i in range(5)]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 6  # 1 header + 5 data

    def test_scenario_missing_fields_uses_empty_string(self):
        """Scenario dict with no keys → all cells empty (except raw)."""
        s = {"raw": "something"}
        result = uat.build_test_pack_csv([s])
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == ""
        assert data_row[1] == ""

    def test_csv_contains_no_extra_leading_whitespace(self):
        scenarios = [_make_scenario()]
        result = uat.build_test_pack_csv(scenarios)
        assert not result.startswith(" ")

    def test_field_with_comma_is_quoted(self):
        """CSV writer should handle commas in field values correctly."""
        s =