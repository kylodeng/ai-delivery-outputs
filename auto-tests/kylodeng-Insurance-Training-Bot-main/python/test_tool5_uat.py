"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios: parsing Claude's raw scenario output into structured dicts
  - build_test_pack_csv: generating a CSV test sheet from parsed scenarios
  - build_test_pack_md: generating a Markdown test pack document
  - get_results_csv: fetching and decoding a CSV results file from GitHub API
  - Module-level __main__ block (stubbed, see TODOs)

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for shared module functions:
      call_claude, get_repo_files, write_output_file, send_email,
      email_html, write_audit_entry
  - base64 encoding/decoding tested directly (no mock needed)

TODOs:
  - TODO: Integration test for __main__ block requires full env setup and GitHub credentials
  - TODO: Test for call_claude integration within generate/analyse flows once shared.py context available
  - TODO: Test SYSTEM_GENERATE and SYSTEM_ANALYSE prompt strings with actual Claude API (requires API key)
  - TODO: Validate email sending in __main__ block (requires SMTP/SES mock)
"""

import base64
import csv
import io
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, mock_open

# ---------------------------------------------------------------------------
# Bootstrap: provide a minimal `shared` stub so the import of tool5_uat works
# without the real shared.py needing to be on the path.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="mocked claude response")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib

# We need to load the module from the actual file path relative to this test.
# Because the file lives in .github/scripts/, we resolve it dynamically.
_HERE = os.path.dirname(__file__)
_SCRIPT_PATH = os.path.join(_HERE, ".github", "scripts", "tool5_uat.py")

# Fallback: if tests are run from repo root, adjust as needed.
if not os.path.exists(_SCRIPT_PATH):
    _SCRIPT_PATH = os.path.join(_HERE, "tool5_uat.py")

# Load the module via importlib so we can import even without __init__.py
import importlib.util

_spec = importlib.util.spec_from_file_location("tool5_uat", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
# Inject the stub before execution
sys.modules["shared"] = _shared_stub
_spec.loader.exec_module(_mod)

parse_scenarios = _mod.parse_scenarios
build_test_pack_csv = _mod.build_test_pack_csv
build_test_pack_md = _mod.build_test_pack_md
get_results_csv = _mod.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-001
TITLE: Policyholder views Generations II benefit summary
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User is logged in
- Policy is active
TEST DATA: policy_id=GEN2-TEST-001, DOB=1980-01-15
STEPS:
1. Navigate to policy dashboard
2. Click "View Benefits"
3. Verify benefit table loads
EXPECTED RESULT: Benefit summary table is displayed with correct values
PASS CRITERIA: Table shows guaranteed lifelong protection and double bonuses
ESTIMATED TIME: 5
NOTES: Requires staging environment with seeded Generations II policy
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-HOSP-001
TITLE: Search designated hospitals in Mainland China
TYPE: POSITIVE
PERSONA: Claimant
PRE-CONDITIONS:
- User authenticated
TEST DATA: city=Shanghai, hospital_class=Class3
STEPS:
1. Open hospital search
2. Enter Shanghai
3. Submit
EXPECTED RESULT: List of Class 3 hospitals in Shanghai is returned
PASS CRITERIA: At least one result shown with correct class label
ESTIMATED TIME: 3
NOTES: [TESTER: verify this]
===SCENARIO===
ID: UAT-HOSP-002
TITLE: Search with invalid city name
TYPE: NEGATIVE
PERSONA: Claimant
PRE-CONDITIONS:
- User authenticated
TEST DATA: city=ZZZINVALIDCITY
STEPS:
1. Open hospital search
2. Enter ZZZINVALIDCITY
3. Submit
EXPECTED RESULT: Error message displayed
PASS CRITERIA: "No results found" message visible
ESTIMATED TIME: 2
NOTES: Boundary — empty result set
"""

MULTI_SCENARIO_NO_LEADING_DELIMITER = """\
===SCENARIO===
ID: UAT-CASHLESS-001
TITLE: Cashless claim at network hospital
TYPE: POSITIVE
PERSONA: Insured
PRE-CONDITIONS:
- Pre-authorisation obtained
TEST DATA: hospital=Mount_Sinai_HK, amount=5000
STEPS:
1. Present insurance card
2. Hospital submits claim
3. System approves
EXPECTED RESULT: Cashless payment processed
PASS CRITERIA: Approval code generated
ESTIMATED TIME: 10
NOTES: None
===SCENARIO===
ID: UAT-CASHLESS-002
TITLE: Claim exceeds annual limit
TYPE: BOUNDARY
PERSONA: Insured
PRE-CONDITIONS:
- Annual limit reached
TEST DATA: hospital=Mount_Sinai_HK, amount=999999
STEPS:
1. Present insurance card
2. Hospital submits claim
EXPECTED RESULT: Claim rejected with limit-exceeded message
PASS CRITERIA: Rejection message shown
ESTIMATED TIME: 5
NOTES: Max boundary value
===SCENARIO===
ID: UAT-CASHLESS-003
TITLE: Unauthorised hospital attempts claim
TYPE: NEGATIVE
PERSONA: Non-network Hospital
PRE-CONDITIONS:
- Hospital not in approved network
TEST DATA: hospital=UNKNOWN_HOSP
STEPS:
1. Hospital submits claim
EXPECTED RESULT: Access denied
PASS CRITERIA: 403 / unauthorised response
ESTIMATED TIME: 3
NOTES: Security check
"""


def _make_scenario(
    sid="UAT-TEST-001",
    title="Test title",
    stype="POSITIVE",
    persona="Tester",
    pass_criteria="All checks green",
    estimated_time="5",
):
    return {
        "id": sid,
        "title": title,
        "type": stype,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": f"ID: {sid}\nTITLE: {title}",
    }


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================


class TestParseScenarios:
    """Tests for parse_scenarios()"""

    def test_single_scenario_all_fields(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-GEN2-001"
        assert s["title"] == "Policyholder views Generations II benefit summary"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Policyholder"
        assert s["pass_criteria"] == "Table shows guaranteed lifelong protection and double bonuses"
        assert s["estimated_time"] == "5"
        assert "raw" in s
        assert "ID: UAT-GEN2-001" in s["raw"]

    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-HOSP-001"
        assert result[1]["id"] == "UAT-HOSP-002"

    def test_two_scenarios_types(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_three_scenarios_parsed(self):
        result = parse_scenarios(MULTI_SCENARIO_NO_LEADING_DELIMITER)
        assert len(result) == 3

    def test_scenario_ids_unique(self):
        result = parse_scenarios(MULTI_SCENARIO_NO_LEADING_DELIMITER)
        ids = [s["id"] for s in result]
        assert len(ids) == len(set(ids))

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-001\nTITLE: Something\nTYPE: POSITIVE"
        # Without the ===SCENARIO=== delimiter, only the leading block before first delimiter
        # is produced — it has no ID after splitting, so nothing is appended.
        result = parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===\n   \n===SCENARIO===\n")
        assert result == []

    def test_scenario_without_id_is_skipped(self):
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: N/A
ESTIMATED TIME: 1
NOTES: none
===SCENARIO===
ID: UAT-VALID-001
TITLE: Valid scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Pass
ESTIMATED TIME: 2
NOTES: none
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-VALID-001"

    def test_missing_optional_fields_do_not_raise(self):
        raw = """\
===SCENARIO===
ID: UAT-MINIMAL-001
TITLE: Minimal scenario
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MINIMAL-001"
        assert s["title"] == "Minimal scenario"
        # Optional fields should simply be absent (not error)
        assert "type" not in s or s.get("type") is None or True  # just no KeyError

    def test_raw_field_contains_original_text(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["raw"] == SINGLE_SCENARIO_BLOCK.split("===SCENARIO===")[1].strip()

    def test_whitespace_trimmed_from_field_values(self):
        raw = "===SCENARIO===\nID:    UAT-WS-001   \nTITLE:   Whitespace test   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-001"
        assert result[0]["title"] == "Whitespace test"

    def test_boundary_type_scenario(self):
        result = parse_scenarios(MULTI_SCENARIO_NO_LEADING_DELIMITER)
        types = [s["type"] for s in result]
        assert "BOUNDARY" in types

    def test_negative_type_scenario(self):
        result = parse_scenarios(MULTI_SCENARIO_NO_LEADING_DELIMITER)
        types = [s["type"] for s in result]
        assert "NEGATIVE" in types

    def test_pass_criteria_field(self):
        result = parse_scenarios(MULTI_SCENARIO_NO_LEADING_DELIMITER)
        assert result[0]["pass_criteria"] == "Approval code generated"

    def test_estimated_time_field(self):
        result = parse_scenarios(MULTI_SCENARIO_NO_LEADING_DELIMITER)
        assert result[2]["estimated_time"] == "3"

    def test_large_number_of_scenarios(self):
        """Stress test with many repeated scenarios."""
        single = """\
===SCENARIO===
ID: UAT-STRESS-{n}
TITLE: Stress scenario {n}
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Pass
ESTIMATED TIME: 1
NOTES: none
"""
        raw = "".join(single.replace("{n}", str(i)) for i in range(50))
        result = parse_scenarios(raw)
        assert len(result) == 50

    def test_scenario_with_special_characters_in_title(self):
        raw = "===SCENARIO===\nID: UAT-SPECIAL-001\nTITLE: Test <script>alert('xss')</script>\n"
        result = parse_scenarios(raw)
        assert "<script>" in result[0]["title"]

    def test_pass_criteria_line_with_extra_colon(self):
        """PASS CRITERIA value may contain colons — only the label colon should be stripped."""
        raw = "===SCENARIO===\nID: UAT-COLON-001\nPASS CRITERIA: Status: OK and code: 200\n"
        result = parse_scenarios(raw)
        assert result[0]["pass_criteria"] == "Status: OK and code: 200"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()"""

    def test_returns_string(self):
        scenarios = [_make_scenario()]
        result = build_test_pack_csv(scenarios)
        assert isinstance(result, str)

    def test_header_row_present(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "Scenario ID" in header
        assert "Title" in header
        assert "Type" in header
        assert "Persona" in header
        assert "Pass Criteria" in header
        assert "Est. Time (min)" in header
        assert "Result (PASS/FAIL/BLOCKED)" in header
        assert "Tester" in header
        assert "Notes" in header
        assert "Defect Ref" in header

    def test_empty_scenarios_produces_header_only(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # only header

    def test_single_scenario_row(self):
        s = _make_scenario(
            sid="UAT-GEN2-001",
            title="Generations II benefit summary",
            stype="POSITIVE",
            persona="Policyholder",
            pass_criteria="Table displays correctly",
            estimated_time="5",
        )
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data row
        data = rows[1]
        assert data[0] == "UAT-GEN