"""
Test suite for tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, malformed/empty input, boundary values
  - build_test_pack_csv(): correct CSV structure, empty scenarios, special characters
  - build_test_pack_md(): correct markdown structure, version/owner/repo injection
  - get_results_csv(): success path, missing file (FileNotFoundError), malformed response
  - __main__ block integration (mode=generate, mode=analyse) — via subprocess/monkeypatch

Mocks used:
  - requests.get (patched via unittest.mock.patch) — prevents real GitHub API calls
  - shared.call_claude — patched to return synthetic Claude output
  - shared.get_repo_files — patched to return synthetic repo content
  - shared.write_output_file — patched to prevent file I/O
  - shared.send_email — patched to prevent SMTP calls
  - shared.write_audit_entry — patched to prevent file I/O
  - base64.b64decode — used indirectly through requests mock

TODOs:
  - TODO: Integration test for __main__ generate mode requires fully wired shared module + env setup
  - TODO: Integration test for __main__ analyse mode requires real CSV results fixture
  - TODO: Test email_html rendering for UAT-specific template (need template details)
  - TODO: Verify SYSTEM_GENERATE and SYSTEM_ANALYSE prompts produce valid Claude output structure
"""

import base64
import csv
import io
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: insert a minimal stub for `shared` so the import in tool5_uat
# does not fail when the real shared module is absent in the test environment.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    stub = types.ModuleType("shared")
    stub.clean_json = MagicMock(side_effect=lambda s: s)
    stub.call_claude = MagicMock(return_value="")
    stub.get_repo_files = MagicMock(return_value={})
    stub.write_output_file = MagicMock(return_value=None)
    stub.send_email = MagicMock(return_value=None)
    stub.email_html = MagicMock(return_value="<html/>")
    stub.write_audit_entry = MagicMock(return_value=None)
    stub.OUTPUT_REPO_OWNER = "test-output-owner"
    stub.OUTPUT_REPO = "test-output-repo"
    stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
    stub.GH_API = "https://api.github.com"
    return stub


# Only insert the stub when the real module is unavailable
if "shared" not in sys.modules:
    sys.modules["shared"] = _make_shared_stub()

# Now safe to import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Purchase Generations II policy as new customer
TYPE: POSITIVE
PERSONA: New policyholder
PRE-CONDITIONS:
- User is authenticated
- Product catalogue is loaded
TEST DATA: product_name=Generations II, premium=500
STEPS:
1. Navigate to product page
2. Click Buy Now
3. Complete application form
EXPECTED RESULT: Policy is issued and confirmation email sent
PASS CRITERIA: Policy number displayed on confirmation screen
ESTIMATED TIME: 15
NOTES: Requires test Sun Life sandbox environment
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-HEALTH-1
TITLE: Submit claim with valid hospital from mainland China list
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Active health policy
TEST DATA: hospital=Shanghai Ruijin Hospital, claim_amount=2000
STEPS:
1. Log in
2. Navigate to Claims
3. Submit claim
EXPECTED RESULT: Claim accepted
PASS CRITERIA: Claim reference number returned
ESTIMATED TIME: 10
NOTES: None
===SCENARIO===
ID: UAT-HEALTH-2
TITLE: Submit claim with non-designated hospital
TYPE: NEGATIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Active health policy
TEST DATA: hospital=Unknown Clinic, claim_amount=500
STEPS:
1. Log in
2. Navigate to Claims
3. Submit claim with non-listed hospital
EXPECTED RESULT: Claim rejected with clear error message
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 8
NOTES: Boundary — non-designated hospital
===SCENARIO===
ID: UAT-HEALTH-3
TITLE: Submit claim with zero claim amount (boundary)
TYPE: BOUNDARY
PERSONA: Policyholder
PRE-CONDITIONS:
- Active health policy
TEST DATA: hospital=Shanghai Ruijin Hospital, claim_amount=0
STEPS:
1. Log in
2. Navigate to Claims
3. Enter 0 as claim amount
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Form prevents submission
ESTIMATED TIME: 5
NOTES: Lower boundary value
"""

SCENARIO_NO_ID = """\
===SCENARIO===
TITLE: Scenario without an ID
TYPE: POSITIVE
PERSONA: Tester
STEPS:
1. Do something
EXPECTED RESULT: Something happens
PASS CRITERIA: It works
ESTIMATED TIME: 5
NOTES: Should be skipped — no ID
"""

SCENARIO_PARTIAL_FIELDS = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial fields scenario
TYPE: BOUNDARY
"""


def _make_scenario(
    id_="UAT-TEST-1",
    title="Test title",
    type_="POSITIVE",
    persona="Tester",
    pass_criteria="Screen shows success",
    estimated_time="10",
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


# ---------------------------------------------------------------------------
# parse_scenarios
# ---------------------------------------------------------------------------

class TestParseScenarios:
    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["id"] == "UAT-GEN2-1"

    def test_single_scenario_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["title"] == "Purchase Generations II policy as new customer"

    def test_single_scenario_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["persona"] == "New policyholder"

    def test_single_scenario_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["pass_criteria"] == "Policy number displayed on confirmation screen"

    def test_single_scenario_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["estimated_time"] == "15"

    def test_single_scenario_raw_preserved(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "UAT-GEN2-1" in result[0]["raw"]

    def test_multi_scenario_returns_correct_count(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3

    def test_multi_scenario_ids_all_present(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in result]
        assert "UAT-HEALTH-1" in ids
        assert "UAT-HEALTH-2" in ids
        assert "UAT-HEALTH-3" in ids

    def test_multi_scenario_types_parsed(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        types = {s["id"]: s["type"] for s in result}
        assert types["UAT-HEALTH-1"] == "POSITIVE"
        assert types["UAT-HEALTH-2"] == "NEGATIVE"
        assert types["UAT-HEALTH-3"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This is just plain text with no delimiters.")
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(SCENARIO_NO_ID)
        assert result == []

    def test_partial_fields_scenario_included_with_id(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-1"

    def test_partial_fields_missing_keys_absent(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        assert "persona" not in result[0]
        assert "pass_criteria" not in result[0]

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: Whitespace test\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_id_with_leading_trailing_spaces_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACES-1   \nTITLE: Space test\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACES-1"

    def test_title_with_colon_in_value(self):
        raw = "===SCENARIO===\nID: UAT-COLON-1\nTITLE: Test: with colon\n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Test: with colon"

    @pytest.mark.parametrize("type_val", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_parsed(self, type_val):
        raw = f"===SCENARIO===\nID: UAT-TYPE-1\nTITLE: Type test\nTYPE: {type_val}\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == type_val

    def test_only_delimiter_no_content_returns_empty(self):
        result = parse_scenarios("===SCENARIO===")
        assert result == []

    def test_multiple_consecutive_delimiters(self):
        raw = "===SCENARIO===\n===SCENARIO===\nID: UAT-CONSEC-1\nTITLE: Consecutive\n"
        result = parse_scenarios(raw)
        # Only the block with an ID should appear
        ids = [s["id"] for s in result]
        assert "UAT-CONSEC-1" in ids

    def test_insurance_synthetic_data_scenario(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-GENII-1\n"
            "TITLE: Verify Generations II lifelong protection benefit\n"
            "TYPE: POSITIVE\n"
            "PERSONA: New policyholder\n"
            "TEST DATA: product_name=Generations II, premium=500\n"
            "PASS CRITERIA: Policy confirmed with lifelong protection\n"
            "ESTIMATED TIME: 20\n"
            "NOTES: Uses Generations II product brochure test data\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-GENII-1"
        assert result[0]["title"] == "Verify Generations II lifelong protection benefit"


# ---------------------------------------------------------------------------
# build_test_pack_csv
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:
    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert header[0] == "Scenario ID"
        assert "Title" in header
        assert "Type" in header
        assert "Persona" in header
        assert "Pass Criteria" in header

    def test_header_has_result_column(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "Result (PASS/FAIL/BLOCKED)" in header

    def test_header_has_defect_ref_column(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "Defect Ref" in header

    def test_empty_scenarios_produces_only_header(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # Only header row (possibly trailing empty row from StringIO)
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == 1

    def test_single_scenario_row_written(self):
        scenarios = [_make_scenario()]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == 2  # header + 1 scenario

    def test_scenario_id_in_csv(self):
        scenarios = [_make_scenario(id_="UAT-CSV-1")]
        result = build_test_pack_csv(scenarios)
        assert "UAT-CSV-1" in result

    def test_scenario_title_in_csv(self):
        scenarios = [_make_scenario(title="My Test Title")]
        result = build_test_pack_csv(scenarios)
        assert "My Test Title" in result

    def test_scenario_type_in_csv(self):
        scenarios = [_make_scenario(type_="NEGATIVE")]
        result = build_test_pack_csv(scenarios)
        assert "NEGATIVE" in result

    def test_scenario_persona_in_csv(self):
        scenarios = [_make_scenario(persona="Admin User")]
        result = build_test_pack_csv(scenarios)
        assert "Admin User" in result

    def test_pass_criteria_in_csv(self):
        scenarios = [_make_scenario(pass_criteria="Success page shown")]
        result = build_test_pack_csv(scenarios)
        assert "Success page shown" in result

    def test_estimated_time_in_csv(self):
        scenarios = [_make_scenario(estimated_time="25")]
        result = build_test_pack_csv(scenarios)
        assert "25" in result

    def test_result_