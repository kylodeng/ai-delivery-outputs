"""
Test suite for tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
    - build_test_pack_csv(): correct CSV headers, row content, empty scenarios, special characters
    - build_test_pack_md(): correct markdown structure, version/owner/repo substitution, raw content inclusion
    - get_results_csv(): successful fetch and base64 decode, missing file error, malformed API response
    - Module-level constants/imports are accessible

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
    - unittest.mock.patch for `base64.b64decode` (selective, where needed)
    - No real network calls, no real GitHub API calls, no real AWS/S3/Lambda calls

TODOs:
    - TODO: Integration test for __main__ block requires full env-var setup + mocked shared module
    - TODO: call_claude mock needed to test end-to-end generate/analyse flows
    - TODO: write_output_file, send_email, write_audit_entry stubs needed for full pipeline test
    - TODO: Test parse_scenarios with actual Claude output samples once prompt is finalised
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Ensure the scripts directory is on sys.path so imports resolve without the
# real `shared` module being present.  We stub it before importing the module
# under test.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")

# Build a minimal fake `shared` module so tool5_uat can be imported without
# the real GitHub token / environment being present.
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda x: x
_shared_stub.call_claude = MagicMock(return_value="mocked")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token test"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now safe to import the module under test
import importlib, types as _types

# We need to import from the actual file path; use importlib so we don't need
# it on sys.path as a package.
import importlib.util

_TOOL_PATH = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
)

# If the file doesn't exist at that path (CI running from repo root), try CWD.
if not os.path.exists(_TOOL_PATH):
    _TOOL_PATH = os.path.join("tool5_uat.py")

# Provide a fallback: import directly if the file is alongside this test file.
_candidates = [
    _TOOL_PATH,
    os.path.join(os.path.dirname(__file__), "tool5_uat.py"),
    "tool5_uat.py",
]

_spec = None
for _candidate in _candidates:
    if os.path.exists(_candidate):
        _spec = importlib.util.spec_from_file_location("tool5_uat", _candidate)
        break

if _spec is not None:
    tool5_uat = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(tool5_uat)
else:
    # Fallback: attempt normal import (works when pytest runs from scripts dir)
    import tool5_uat  # type: ignore

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful policy purchase
TYPE: POSITIVE
PERSONA: New Customer
PRE-CONDITIONS:
- User is logged in
- Product catalogue is available
TEST DATA: product_name=Generations II, dob=1985-03-15
STEPS:
1. Navigate to product page
2. Click Buy Now
3. Complete application form
EXPECTED RESULT: Policy is issued and confirmation email sent
PASS CRITERIA: Policy number displayed on confirmation screen
ESTIMATED TIME: 10
NOTES: Requires sandbox payment gateway
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Positive flow
TYPE: POSITIVE
PERSONA: Admin
PRE-CONDITIONS:
- System is running
TEST DATA: user=admin@example.com
STEPS:
1. Login
EXPECTED RESULT: Dashboard shown
PASS CRITERIA: Dashboard visible
ESTIMATED TIME: 5
NOTES: none
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Negative flow - invalid login
TYPE: NEGATIVE
PERSONA: Attacker
PRE-CONDITIONS:
- System is running
TEST DATA: user=bad@example.com, password=wrong
STEPS:
1. Enter wrong credentials
2. Submit
EXPECTED RESULT: Error message shown
PASS CRITERIA: 401 response displayed
ESTIMATED TIME: 3
NOTES: none
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-99
TITLE: Max character input in policy name field
TYPE: BOUNDARY
PERSONA: Power User
PRE-CONDITIONS:
- Form is open
TEST DATA: policy_name={"A" * 255}
STEPS:
1. Enter 255 characters in policy name
2. Submit
EXPECTED RESULT: Form accepts or gracefully rejects
PASS CRITERIA: No 500 error
ESTIMATED TIME: 2
NOTES: Check DB column size
"""

INSURANCE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-HEALTH-1
TITLE: Claim submission for designated hospital in mainland China
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Policy is active
- Hospital is on the List of Designated Hospitals in Mainland China
TEST DATA: hospital=Class 3 hospital in Shanghai, claim_amount=50000 HKD
STEPS:
1. Login to self-service portal
2. Navigate to Claims
3. Select hospital from dropdown
4. Submit claim
EXPECTED RESULT: Claim submitted and reference number issued
PASS CRITERIA: Reference number displayed
ESTIMATED TIME: 15
NOTES: Uses health_products linked product
"""


# ---------------------------------------------------------------------------
# Tests: parse_scenarios
# ---------------------------------------------------------------------------

class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_happy_path(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful policy purchase"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "New Customer"
        assert s["pass_criteria"] == "Policy number displayed on confirmation screen"
        assert s["estimated_time"] == "10"
        assert "raw" in s
        assert "UAT-STORY1-1" in s["raw"]

    def test_two_scenarios_parsed_correctly(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["id"] == "UAT-STORY1-2"
        assert result[1]["type"] == "NEGATIVE"

    def test_boundary_scenario_type(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["type"] == "BOUNDARY"

    def test_insurance_scenario_parsed(self):
        result = parse_scenarios(INSURANCE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-HEALTH-1"
        assert s["persona"] == "Policyholder"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This is just some random text without any scenario delimiter.")
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===")
        # Block after split is empty string — no id found, so nothing appended
        assert result == []

    def test_scenario_without_id_is_skipped(self):
        block = """\
===SCENARIO===
TITLE: Missing ID scenario
TYPE: POSITIVE
PERSONA: User
"""
        result = parse_scenarios(block)
        assert result == []

    def test_scenario_missing_optional_fields_still_parsed(self):
        """A scenario with only ID should still be returned with partial data."""
        block = """\
===SCENARIO===
ID: UAT-MIN-1
"""
        result = parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert result[0].get("title") is None
        assert result[0].get("type") is None
        assert result[0].get("persona") is None

    def test_raw_field_preserves_original_block(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "Successful policy purchase" in result[0]["raw"]
        assert "Requires sandbox payment gateway" in result[0]["raw"]

    def test_multiple_scenarios_raw_fields_are_independent(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert "Positive flow" in result[0]["raw"]
        assert "Attacker" not in result[0]["raw"]
        assert "Attacker" in result[1]["raw"]

    def test_whitespace_only_blocks_ignored(self):
        block = "===SCENARIO===\n   \n\t\n===SCENARIO===\nID: UAT-WS-1\n"
        result = parse_scenarios(block)
        # First block is whitespace only → skipped; second has ID
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_extra_colons_in_value_preserved(self):
        """Values that contain colons should be captured correctly (first colon splits)."""
        block = """\
===SCENARIO===
ID: UAT-COL-1
TITLE: Test with URL: https://example.com
TYPE: POSITIVE
PERSONA: API User
PASS CRITERIA: 200 OK
ESTIMATED TIME: 1
"""
        result = parse_scenarios(block)
        # The title line has "TITLE:" prefix — replace only first occurrence
        assert result[0]["id"] == "UAT-COL-1"
        # Title will be everything after "TITLE:" — colon handling is via replace
        assert "Test with URL: https://example.com" in result[0]["title"]

    def test_large_number_of_scenarios_performance(self):
        """Parsing 50 scenarios should complete without error."""
        blocks = "\n".join([
            f"===SCENARIO===\nID: UAT-PERF-{i}\nTITLE: Scenario {i}\nTYPE: POSITIVE\nPERSONA: User\nPASS CRITERIA: ok\nESTIMATED TIME: 1\n"
            for i in range(50)
        ])
        result = parse_scenarios(blocks)
        assert len(result) == 50
        assert result[49]["id"] == "UAT-PERF-49"

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_parsed(self, scenario_type):
        block = f"""\
===SCENARIO===
ID: UAT-T-1
TITLE: Type test
TYPE: {scenario_type}
PERSONA: User
PASS CRITERIA: ok
ESTIMATED TIME: 5
"""
        result = parse_scenarios(block)
        assert result[0]["type"] == scenario_type


# ---------------------------------------------------------------------------
# Tests: build_test_pack_csv
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_header_row_correct(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # header only

    def test_single_scenario_row(self):
        scenarios = [{
            "id": "UAT-STORY1-1",
            "title": "Successful policy purchase",
            "type": "POSITIVE",
            "persona": "New Customer",
            "pass_criteria": "Policy number displayed",
            "estimated_time": "10",
        }]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2
        assert rows[1][0] == "UAT-STORY1-1"
        assert rows[1][1] == "Successful policy purchase"
        assert rows[1][2] == "POSITIVE"
        assert rows[1][3] == "New Customer"
        assert rows[1][4] == "Policy number displayed"
        assert rows[1][5] == "10"
        # Result, Tester, Notes, Defect Ref should be empty
        assert rows[1][6] == ""
        assert rows[1][7] == ""
        assert rows[1][8] == ""
        assert rows[1][9] == ""

    def test_multiple_scenarios_produce_correct_row_count(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 3  # header + 2 data rows

    def test_missing_fields_default_to_empty_string(self):
        scenarios = [{"id": "UAT-MIN-1"}]  # Only id present
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][0] == "UAT-MIN-1"
        assert rows[1][1] == ""
        assert rows[1][2] == ""

    def test_special_characters_in_title_handled(self):
        scenarios = [{
            "id": "UAT-SC-1",
            "title": 'Title with "quotes" and, comma',
            "type": "POSITIVE",
            "persona": "