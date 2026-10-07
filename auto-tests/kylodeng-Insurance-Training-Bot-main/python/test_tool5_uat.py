"""
Tests for tool5_uat.py

What is tested:
  - parse_scenarios(): parsing Claude's raw scenario output into structured dicts
  - build_test_pack_csv(): generating a CSV test sheet from parsed scenarios
  - build_test_pack_md(): generating a Markdown test pack document
  - get_results_csv(): fetching a CSV results file from a GitHub repo via API
  - Integration of __main__ block behaviour (stubbed via subprocess/env vars)

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for `shared.call_claude`
  - unittest.mock.patch for `shared.get_repo_files`
  - unittest.mock.patch for `shared.write_output_file`
  - unittest.mock.patch for `shared.send_email`
  - unittest.mock.patch for `shared.write_audit_entry`
  - base64 encoding/decoding for simulated GitHub content responses

TODOs:
  - TODO: Test __main__ block end-to-end (requires full env setup and shared module stubs)
  - TODO: Test Mode B (analyse) path in __main__ — needs call_claude + defect JSON parsing
  - TODO: Test Mode A (generate) path in __main__ — needs call_claude + write_output_file stubs
  - TODO: Test email sending integration (send_email, email_html) in both modes
  - TODO: Test write_audit_entry calls — needs audit infrastructure context
"""

import base64
import csv
import io
import json
import os
import sys
import datetime

import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Path bootstrap — mirror what tool5_uat.py does so imports resolve
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

# Provide a minimal stub for `shared` so we never hit real network/FS
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib

tool5 = importlib.import_module("tool5_uat")  # noqa: E402

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

SINGLE_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Purchase Generations II policy as new customer
TYPE: POSITIVE
PERSONA: New policyholder
PRE-CONDITIONS:
- User is authenticated
- Product catalogue is loaded
TEST DATA: product_name=Generations II, sum_assured=1000000
STEPS:
1. Navigate to product page
2. Select Generations II
3. Complete application form
EXPECTED RESULT: Policy is issued and confirmation email sent
PASS CRITERIA: Policy number is displayed and email received within 5 minutes
ESTIMATED TIME: 15
NOTES: Requires sandbox email service
"""

MULTI_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-HEALTH-1
TITLE: Claim cashless arrangement at designated hospital
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active
- Hospital is on Global Network Hospital List
TEST DATA: hospital=Shanghai Huashan Hospital, policy_no=POL-0001
STEPS:
1. Present policy card at hospital
2. Hospital submits pre-authorisation
3. System approves or denies
EXPECTED RESULT: Pre-authorisation response returned within 30 minutes
PASS CRITERIA: Approval reference number displayed
ESTIMATED TIME: 10
NOTES: Use Cashless Arrangement test environment
===SCENARIO===
ID: UAT-HEALTH-2
TITLE: Submit claim with invalid policy number
TYPE: NEGATIVE
PERSONA: Fraudulent actor
PRE-CONDITIONS:
- System is running
TEST DATA: policy_no=INVALID-9999
STEPS:
1. Submit claim with policy_no=INVALID-9999
2. Observe system response
EXPECTED RESULT: System returns 400 error with message "Policy not found"
PASS CRITERIA: Error message displayed, no claim created
ESTIMATED TIME: 5
NOTES: [TESTER: verify error code]
===SCENARIO===
ID: UAT-HEALTH-3
TITLE: Submit claim with maximum allowed amount
TYPE: BOUNDARY
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active with maximum coverage
TEST DATA: claim_amount=9999999.99
STEPS:
1. Submit claim for maximum amount
EXPECTED RESULT: Claim accepted and queued for review
PASS CRITERIA: Claim reference number returned
ESTIMATED TIME: 7
NOTES: Edge case for financial limits
"""

SCENARIO_WITHOUT_ID_RAW = """\
===SCENARIO===
TITLE: Orphan scenario with no ID
TYPE: POSITIVE
PERSONA: Admin
STEPS:
1. Do something
ESTIMATED TIME: 5
NOTES: None
"""

EMPTY_RAW = ""

ONLY_DELIMITER_RAW = "===SCENARIO==="


def _make_scenario(
    id_="UAT-GEN2-1",
    title="Test scenario",
    type_="POSITIVE",
    persona="Tester",
    pass_criteria="System responds correctly",
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


def _github_content_response(text: str) -> dict:
    """Simulate a GitHub API /contents response with base64-encoded content."""
    encoded = base64.b64encode(text.encode()).decode()
    return {"content": encoded, "encoding": "base64"}


# ===========================================================================
# parse_scenarios
# ===========================================================================


class TestParseScenarios:
    def test_single_scenario_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-GEN2-1"
        assert s["title"] == "Purchase Generations II policy as new customer"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "New policyholder"
        assert "Policy number is displayed" in s["pass_criteria"]
        assert s["estimated_time"] == "15"
        assert "UAT-GEN2-1" in s["raw"]

    def test_multiple_scenarios_parsed(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3

    def test_scenario_ids_correct(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-HEALTH-1", "UAT-HEALTH-2", "UAT-HEALTH-3"]

    def test_scenario_types(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        types = [s["type"] for s in result]
        assert "POSITIVE" in types
        assert "NEGATIVE" in types
        assert "BOUNDARY" in types

    def test_scenario_without_id_excluded(self):
        result = parse_scenarios(SCENARIO_WITHOUT_ID_RAW)
        assert result == []

    def test_empty_string_returns_empty(self):
        result = parse_scenarios(EMPTY_RAW)
        assert result == []

    def test_only_delimiter_returns_empty(self):
        result = parse_scenarios(ONLY_DELIMITER_RAW)
        assert result == []

    def test_raw_field_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_missing_optional_fields_default_to_missing(self):
        minimal = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario
"""
        result = parse_scenarios(minimal)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        assert s["title"] == "Minimal scenario"
        assert "type" not in s
        assert "persona" not in s
        assert "pass_criteria" not in s
        assert "estimated_time" not in s

    def test_whitespace_stripped_from_values(self):
        raw = """\
===SCENARIO===
ID:   UAT-WS-1   
TITLE:   Whitespace test   
TYPE:   NEGATIVE   
PERSONA:   Admin User   
PASS CRITERIA:   No extra spaces   
ESTIMATED TIME:   20   
"""
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"
        assert result[0]["type"] == "NEGATIVE"
        assert result[0]["persona"] == "Admin User"
        assert result[0]["pass_criteria"] == "No extra spaces"
        assert result[0]["estimated_time"] == "20"

    def test_delimiter_at_start_only(self):
        raw = "===SCENARIO===\nID: UAT-X-1\nTITLE: X\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"

    def test_multiple_delimiters_no_content_between(self):
        raw = "===SCENARIO===\n\n===SCENARIO===\nID: UAT-Y-1\nTITLE: Y\n"
        result = parse_scenarios(raw)
        # First block is empty/no id, second has id
        assert len(result) == 1
        assert result[0]["id"] == "UAT-Y-1"

    @pytest.mark.parametrize("type_val", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_parsed(self, type_val):
        raw = f"===SCENARIO===\nID: UAT-T-1\nTITLE: T\nTYPE: {type_val}\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == type_val

    def test_insurance_synthetic_data_scenario(self):
        """Use synthetic insurance product data as test data in scenario block."""
        raw = """\
===SCENARIO===
ID: UAT-INS-1
TITLE: Verify Generations II policy brochure content
TYPE: POSITIVE
PERSONA: Compliance officer
TEST DATA: product_name=Generations II, doc_type=product_brochure, linked_product=Generations II
PASS CRITERIA: Brochure fields match expected product metadata
ESTIMATED TIME: 8
NOTES: Use Generations-II_PB_EN.pdf.annot.json fixture
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-INS-1"
        assert result[0]["pass_criteria"] == "Brochure fields match expected product metadata"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_header_row_correct(self):
        scenarios = [_make_scenario()]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
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

    def test_single_scenario_row(self):
        scenarios = [_make_scenario()]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 2  # header + 1 data row
        data = rows[1]
        assert data[0] == "UAT-GEN2-1"
        assert data[1] == "Test scenario"
        assert data[2] == "POSITIVE"
        assert data[3] == "Tester"
        assert data[4] == "System responds correctly"
        assert data[5] == "10"
        # Result, Tester, Notes, Defect Ref should be blank
        assert data[6] == ""
        assert data[7] == ""
        assert data[8] == ""
        assert data[9] == ""

    def test_multiple_scenario_rows(self):
        scenarios = [
            _make_scenario(id_="UAT-1", title="First"),
            _make_scenario(id_="UAT-2", title="Second"),
            _make_scenario(id_="UAT-3", title="Third"),
        ]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 4  # header + 3 data rows

    def test_empty_scenarios_list(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # header only

    def test_missing_fields_produce_empty_string(self):
        scenario = {"id": "UAT-EMPTY-1", "raw": "ID: UAT-EMPTY-1"}
        csv_str = build_test_pack_csv([scenario])
        rows = self._parse_csv(csv_str)
        data = rows[1]
        assert data[0] == "UAT-EMPTY-1"
        assert data[1] == ""  # title missing
        assert data[2] == ""  # type missing

    def test_output_is_string(self):
        csv_str = build_test_pack_csv([_make_scenario()])
        assert isinstance(csv_str, str)

    def test_csv_parseable(self):
        scenarios = [_make_scenario(id_="UAT-PARSE-1")]
        csv_str = build_test_pack_csv(scenarios)
        # Should not raise
        rows = list(csv.reader(io.StringIO(csv_str)))
        assert len(rows) >= 1

    def test_field_with_comma_is_quoted(self):
        scenario = _make_scenario(
            id_="UAT-COMMA-1",
            title="Buy product A, then B",
            pass_criteria="Both A, B purchased",
        )
        csv_str = build_test_pack_csv([scenario])
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == "Buy product A, then B"
        assert rows[1][4] == "Both A, B purchased"

    @pytest.mark.parametrize(
        "type_,expected",
        [
            ("POSITIVE", "POSITIVE"),
            ("NEGATIVE", "NEGATIVE"),
            ("BOUNDARY", "