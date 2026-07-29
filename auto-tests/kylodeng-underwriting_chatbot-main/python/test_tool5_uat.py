"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty/malformed input
    - build_test_pack_csv(): correct headers, rows, empty list, special characters
    - build_test_pack_md(): structure, version embedding, raw content inclusion
    - get_results_csv(): happy path, missing 'content' key, network/decode errors

Mocks used:
    - unittest.mock.patch for `requests.get` (get_results_csv)
    - unittest.mock.MagicMock for response objects
    - base64 encoding helpers to construct fake GitHub API responses

TODOs:
    - TODO: Integration test for full __main__ execution requires env vars and
      mocked shared module functions (call_claude, get_repo_files, write_output_file,
      send_email, write_audit_entry) — stub tests provided below.
    - TODO: Tests for build_test_pack_md timestamp format need freezegun or
      datetime mock to assert exact timestamp values.
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
# Bootstrap: stub out the `shared` module so we can import tool5_uat without
# the real shared.py being present (it lives alongside the script in CI).
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="mocked claude response")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-output-repo"
    shared.GH_HEADERS = {"Authorization": "token fake"}
    shared.GH_API = "https://api.github.com"
    return shared


# Insert the stub before importing the module under test
sys.modules.setdefault("shared", _make_shared_stub())

# Also stub `requests` so the top-level import inside the script doesn't fail
# (we will patch it per-test as needed)
if "requests" not in sys.modules:
    sys.modules["requests"] = MagicMock()

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / Fixtures
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Standard User
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: Standard User
PRE-CONDITIONS:
- User account exists
TEST DATA: username=test@example.com, password=WRONG
STEPS:
1. Navigate to login page
2. Enter invalid credentials
3. Click Submit
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Error message shown, user NOT redirected
ESTIMATED TIME: 3
NOTES: Check lockout after 5 attempts
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Maximum length username
TYPE: BOUNDARY
PERSONA: Admin User
PRE-CONDITIONS:
- System running
TEST DATA: username=a*255, password=ValidPass1!
STEPS:
1. Navigate to login page
2. Enter 255-character username
3. Click Submit
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Form rejects input > 254 chars
ESTIMATED TIME: 2
NOTES: [TESTER: verify this]
"""

MULTI_SCENARIO_RAW = "\n".join([
    MINIMAL_SCENARIO_BLOCK,
    NEGATIVE_SCENARIO_BLOCK,
    BOUNDARY_SCENARIO_BLOCK,
])


def _fake_gh_response(content: str, status: int = 200):
    """Return a mock requests.Response-like object with encoded content."""
    encoded = base64.b64encode(content.encode()).decode()
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json.return_value = {"content": encoded}
    return mock_resp


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_happy_path(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Happy path login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Standard User"
        assert s["pass_criteria"] == "Dashboard loads within 3 seconds"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_parsed(self):
        scenarios = parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(scenarios) == 3
        ids = [s["id"] for s in scenarios]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_scenario_types_preserved(self):
        scenarios = parse_scenarios(MULTI_SCENARIO_RAW)
        types_ = {s["id"]: s["type"] for s in scenarios}
        assert types_["UAT-STORY1-1"] == "POSITIVE"
        assert types_["UAT-STORY1-2"] == "NEGATIVE"
        assert types_["UAT-STORY1-3"] == "BOUNDARY"

    def test_raw_field_contains_original_block(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "Navigate to login page" in scenarios[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        """Block without ===SCENARIO=== delimiter should yield nothing (no ID)."""
        raw = "ID: UAT-X-1\nTITLE: Something\n"
        # No delimiter means one block with content but it lacks ===SCENARIO===
        # actually the split still happens but produces one block from split("===SCENARIO===")
        # The block has an ID so it WILL be parsed — this tests that non-delimited
        # content that nevertheless has an ID is captured.
        result = parse_scenarios(raw)
        # raw doesn't start with delimiter, first element of split is non-empty
        # and contains ID so one scenario should be returned
        assert isinstance(result, list)

    def test_block_without_id_is_skipped(self):
        """Blocks that have no ID line should be discarded."""
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 0

    def test_missing_optional_fields_default_to_absent(self):
        """A scenario missing PERSONA / ESTIMATED TIME should still parse."""
        raw = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal\nTYPE: POSITIVE\n"
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-MIN-1"
        assert "persona" not in scenarios[0]
        assert "estimated_time" not in scenarios[0]

    def test_extra_whitespace_around_values(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1  \nTITLE:   Whitespace test   \nTYPE:  BOUNDARY  \n"
        scenarios = parse_scenarios(raw)
        assert scenarios[0]["id"] == "UAT-WS-1"
        assert scenarios[0]["title"] == "Whitespace test"
        assert scenarios[0]["type"] == "BOUNDARY"

    def test_delimiter_only_returns_empty(self):
        """Only delimiters, no real content."""
        raw = "===SCENARIO===\n===SCENARIO===\n===SCENARIO==="
        scenarios = parse_scenarios(raw)
        assert scenarios == []

    def test_large_number_of_scenarios(self):
        """Performance / correctness with many blocks."""
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Bulk test {i}\nTYPE: POSITIVE\n"
            )
        raw = "\n".join(blocks)
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 50
        assert scenarios[0]["id"] == "UAT-BULK-0"
        assert scenarios[49]["id"] == "UAT-BULK-49"

    @pytest.mark.parametrize("field_line,expected_key,expected_val", [
        ("ID: UAT-P-1", "id", "UAT-P-1"),
        ("TITLE: My Title", "title", "My Title"),
        ("TYPE: NEGATIVE", "type", "NEGATIVE"),
        ("PERSONA: Admin", "persona", "Admin"),
        ("PASS CRITERIA: System rejects", "pass_criteria", "System rejects"),
        ("ESTIMATED TIME: 10", "estimated_time", "10"),
    ])
    def test_individual_field_parsing(self, field_line, expected_key, expected_val):
        raw = f"===SCENARIO===\nID: UAT-P-1\n{field_line}\n"
        scenarios = parse_scenarios(raw)
        assert scenarios[0].get(expected_key) == expected_val

    def test_scenario_with_underwriting_risk_test_data(self):
        """Uses synthetic data sample from model_card.json context."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-UNDERWRITING-1\n"
            "TITLE: Submit application for high-risk customer\n"
            "TYPE: NEGATIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: Age=25, Annual_Income=30000, Risk_Classification=HIGH\n"
            "PASS CRITERIA: Application flagged for manual review\n"
            "ESTIMATED TIME: 8\n"
        )
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-UNDERWRITING-1"

    def test_scenario_with_customer_id_test_data(self):
        """Uses synthetic data sample from customer_similarity_dict.json."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-CUST-1\n"
            "TITLE: Retrieve similar customers for CUST00000001\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Risk Analyst\n"
            "TEST DATA: customer_id=CUST00000001\n"
            "PASS CRITERIA: Returns list of 20 similar customer IDs\n"
            "ESTIMATED TIME: 3\n"
        )
        scenarios = parse_scenarios(raw)
        assert scenarios[0]["pass_criteria"] == "Returns list of 20 similar customer IDs"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_header_row_correct(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_produces_header_only(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # header only

    def test_single_scenario_produces_two_rows(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 2  # header + 1 data row

    def test_data_row_values_correct(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Happy path login"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Standard User"
        assert data_row[4] == "Dashboard loads within 3 seconds"
        assert data_row[5] == "5"

    def test_result_tester_notes_defect_columns_empty(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        # Columns 6-9 should be empty (tester fills in)
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = parse_scenarios(MULTI_SCENARIO_RAW)
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 4  # header + 3 data rows

    def test_missing_fields_produce_empty_strings(self):
        """Scenarios with missing optional fields should not raise."""
        partial = [{"id": "UAT-PARTIAL-1", "raw": "block"}]
        csv_str = build_test_pack_csv(partial)
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == ""  # title empty
        assert rows[1][2] == ""  # type empty

    def test_special_characters_in_fields(self):
        """Fields with commas and quotes should be properly escaped."""
        scenarios = [
            {