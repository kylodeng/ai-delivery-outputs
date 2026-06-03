"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios: happy path, edge cases (empty input, missing fields, no ID, multiple scenarios)
  - build_test_pack_csv: correct headers, row content, empty list, special characters
  - build_test_pack_md: markdown structure, version/owner/repo embedding, raw content inclusion
  - get_results_csv: happy path (base64 content), missing content key (FileNotFoundError), API error shape

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for `shared` module imports (call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry) — imported at module level via sys.path insertion
  - base64 encoding/decoding exercised with real values (no mock needed)

TODOs:
  - TODO: Integration test for __main__ block requires full environment variable setup and
    live shared module — stub tests provided below
  - TODO: Test Mode A (generate) end-to-end requires mocking call_claude return value and
    verifying write_output_file + send_email calls
  - TODO: Test Mode B (analyse) end-to-end requires mocking call_claude + clean_json return
    and verifying defect report output
  - TODO: Verify SYSTEM_GENERATE and SYSTEM_ANALYSE prompt strings contain required keywords
    once prompt engineering is finalised
"""

import base64
import csv
import io
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module before importing tool5_uat so we never hit
# real network calls or missing credentials.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="mocked claude response")
    shared.get_repo_files = MagicMock(return_value={"file.py": "print('hello')"})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html></html>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "token test"}
    shared.GH_API = "https://api.github.com"
    return shared


# Inject stub before any import of tool5_uat
_shared_stub = _make_shared_stub()
sys.modules["shared"] = _shared_stub

# Now safe to import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
# Also try relative path for environments where repo root is cwd
_script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Standard User
PRE-CONDITIONS:
- System is running
- User account exists
TEST DATA: username=test@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard is displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Admin
PRE-CONDITIONS:
- Account exists
TEST DATA: email=admin@corp.com
STEPS:
1. Open app
EXPECTED RESULT: Dashboard shown
PASS CRITERIA: HTTP 200 returned
ESTIMATED TIME: 3
NOTES: -

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: Standard User
PRE-CONDITIONS:
- Account exists
TEST DATA: password=wrong
STEPS:
1. Enter wrong password
EXPECTED RESULT: Error message
PASS CRITERIA: Error banner visible
ESTIMATED TIME: 2
NOTES: Edge case
"""

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario
TYPE: BOUNDARY
"""


def _make_scenario(
    id_="UAT-X-1",
    title="Test Title",
    type_="POSITIVE",
    persona="End User",
    pass_criteria="System responds correctly",
    estimated_time="5",
):
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": "raw block content",
    }


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_string_with_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("Some random text with no scenario delimiters")
        assert result == []

    def test_single_scenario_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Standard User"
        assert s["pass_criteria"] == "Dashboard is displayed within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_single_scenario_includes_raw(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_two_scenarios_ids(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_two_scenarios_types(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = {s["id"]: s["type"] for s in result}
        assert types_["UAT-STORY1-1"] == "POSITIVE"
        assert types_["UAT-STORY1-2"] == "NEGATIVE"

    def test_scenario_without_id_is_excluded(self):
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: User
"""
        result = parse_scenarios(raw)
        assert result == []

    def test_minimal_scenario_has_id_and_raw(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert result[0]["title"] == "Minimal scenario"
        assert result[0]["type"] == "BOUNDARY"

    def test_minimal_scenario_missing_keys_not_present(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        s = result[0]
        # Optional fields not in block should be absent
        assert "persona" not in s
        assert "pass_criteria" not in s
        assert "estimated_time" not in s

    def test_leading_delimiter_with_empty_first_block(self):
        raw = "===SCENARIO===\n\n===SCENARIO===\nID: UAT-2-1\nTITLE: Second\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-2-1"

    def test_whitespace_stripped_from_values(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1  \nTITLE:   Whitespace Test   \nTYPE:  BOUNDARY  \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace Test"
        assert result[0]["type"] == "BOUNDARY"

    def test_many_scenarios(self):
        blocks = []
        for i in range(10):
            blocks.append(
                f"===SCENARIO===\nID: UAT-LOAD-{i}\nTITLE: Load test {i}\nTYPE: POSITIVE\n"
            )
        raw = "\n".join(blocks)
        result = parse_scenarios(raw)
        assert len(result) == 10

    @pytest.mark.parametrize("type_val", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_type_values_parsed(self, type_val):
        raw = f"===SCENARIO===\nID: UAT-T-1\nTITLE: Type test\nTYPE: {type_val}\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == type_val

    def test_pass_criteria_with_colon_in_value(self):
        raw = "===SCENARIO===\nID: UAT-C-1\nTITLE: Colon test\nTYPE: POSITIVE\nPASS CRITERIA: Response time < 3s: verified\n"
        result = parse_scenarios(raw)
        # Only the first colon is used for the label split — value includes rest
        assert result[0]["pass_criteria"] == "Response time < 3s: verified"

    def test_synthetic_customer_id_in_test_data_field(self):
        """Use synthetic data: customer IDs from customer_similarity_dict.json"""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-CUST-1\n"
            "TITLE: Customer similarity lookup\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: CUST00000001 → similar: CUST00006151, CUST00000272\n"
            "PASS CRITERIA: Similarity list returned with 10 entries\n"
            "ESTIMATED TIME: 3\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-CUST-1"

    def test_underwriting_scenario_with_risk_classification(self):
        """Synthetic data: model_card.json — Risk Classification model."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-MODEL-1\n"
            "TITLE: Risk classification for Age=34 applicant\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: Underwriter\n"
            "PASS CRITERIA: Risk_Classification returned as valid enum value\n"
            "ESTIMATED TIME: 5\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-MODEL-1"
        assert result[0]["type"] == "BOUNDARY"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_empty_scenarios_produces_header_only(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_header_columns(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
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
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data row

    def test_data_row_values_correct(self):
        s = _make_scenario(
            id_="UAT-X-1",
            title="My Test",
            type_="NEGATIVE",
            persona="Admin",
            pass_criteria="Error shown",
            estimated_time="10",
        )
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        row = next(reader)
        assert row[0] == "UAT-X-1"
        assert row[1] == "My Test"
        assert row[2] == "NEGATIVE"
        assert row[3] == "Admin"
        assert row[4] == "Error shown"
        assert row[5] == "10"

    def test_result_tester_notes_defect_columns_empty(self):
        s = _make_scenario()
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[6] == ""   # Result
        assert row[7] == ""   # Tester
        assert row[8] == ""   # Notes
        assert row[9] == ""   # Defect Ref

    def test_multiple_scenarios_row_count(self):
        scenarios = [_make_scenario(id_=f"UAT-X-{i}") for i in range(5)]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 6  # header + 5

    def test_missing_keys_produce_empty_string(self):
        """Scenario dict with no keys should not raise, values default to empty."""
        result = build_test_pack_csv([{"raw": "something"}])
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[0] == ""
        assert row[1] == ""

    def test_special_characters_in_title(self):
        s = _make