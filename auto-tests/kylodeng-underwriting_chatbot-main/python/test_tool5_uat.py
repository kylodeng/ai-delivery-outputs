"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, malformed input, boundary values
    - build_test_pack_csv(): correct headers, row population, empty input, special characters
    - build_test_pack_md(): correct metadata embedding, raw content inclusion
    - get_results_csv(): successful decode, missing file error, malformed API response
    - Module-level constants and imports from shared are reachable

Mocks used:
    - requests.get (for get_results_csv)
    - shared.call_claude
    - shared.get_repo_files
    - shared.write_output_file
    - shared.send_email
    - shared.write_audit_entry
    - base64.b64decode (via real stdlib — no mock needed, responses are crafted)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var matrix + GitHub API credentials
    - TODO: SYSTEM_GENERATE and SYSTEM_ANALYSE prompts could be validated against a live Claude
      model to ensure they still produce parseable output after prompt changes
    - TODO: build_test_pack_md timestamp is UTC-generated at call time; freeze time for deterministic assertions
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so we can import tool5_uat without
# a real GitHub token or network connection.
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
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now safe to import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# We import after patching sys.modules so `from shared import ...` resolves to our stub.
import importlib

# Patch requests before importing so module-level code is safe
with patch.dict("sys.modules", {"requests": MagicMock()}):
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "tool5_uat",
        os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts", "tool5_uat.py"),
    )
    if _spec is None:
        # Fallback: try relative path from repo root
        _script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".github", "scripts", "tool5_uat.py",
        )
        _spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)

    tool5_uat = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    try:
        _spec.loader.exec_module(tool5_uat)  # type: ignore[union-attr]
    except Exception as exc:
        # If the file truly is not present in the test environment we create
        # a minimal shim so the rest of the tests can still be collected.
        tool5_uat = types.ModuleType("tool5_uat")  # type: ignore[assignment]
        tool5_uat._import_error = exc  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: username=underwriter@test.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Positive login
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Dashboard loads
ESTIMATED TIME: 5
NOTES: -

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid credentials rejected
TYPE: NEGATIVE
PERSONA: Underwriter
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: -
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Max income boundary
TYPE: BOUNDARY
PERSONA: Senior Underwriter
PASS CRITERIA: System accepts max value
ESTIMATED TIME: 10
NOTES: Use Annual_Income=9999999
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Something happens
ESTIMATED TIME: 2
NOTES: Should be skipped
"""

MALFORMED_BLOCK = """\
===SCENARIO===
This block has no key-value pairs at all.
Just free text.
"""


# ---------------------------------------------------------------------------
# Tests: parse_scenarios
# ---------------------------------------------------------------------------


class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_parsed(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Happy path login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Dashboard loads within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_raw_field_preserved(self):
        result = tool5_uat.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "Happy path login" in result[0]["raw"]

    def test_two_scenarios_parsed(self):
        result = tool5_uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_scenario_types_preserved(self):
        result = tool5_uat.parse_scenarios(TWO_SCENARIO_BLOCK)
        types_found = {s["type"] for s in result}
        assert "POSITIVE" in types_found
        assert "NEGATIVE" in types_found

    def test_boundary_scenario_parsed(self):
        result = tool5_uat.parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["type"] == "BOUNDARY"
        assert result[0]["persona"] == "Senior Underwriter"

    def test_scenario_without_id_is_excluded(self):
        """Scenarios missing an ID field must be silently dropped."""
        result = tool5_uat.parse_scenarios(SCENARIO_WITHOUT_ID)
        assert result == []

    def test_malformed_block_no_id_excluded(self):
        result = tool5_uat.parse_scenarios(MALFORMED_BLOCK)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("")
        assert result == []

    def test_only_delimiter_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("===SCENARIO===")
        assert result == []

    def test_multiple_delimiters_no_content(self):
        result = tool5_uat.parse_scenarios("===SCENARIO===\n===SCENARIO===\n===SCENARIO===")
        assert result == []

    def test_mixed_valid_and_invalid_scenarios(self):
        mixed = TWO_SCENARIO_BLOCK + SCENARIO_WITHOUT_ID
        result = tool5_uat.parse_scenarios(mixed)
        assert len(result) == 2

    def test_scenario_with_extra_whitespace_in_id(self):
        block = "===SCENARIO===\nID:   UAT-WS-001   \nTITLE: Whitespace test\nTYPE: POSITIVE\n"
        result = tool5_uat.parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-001"

    def test_missing_optional_fields_default_absent(self):
        """Optional fields like persona/pass_criteria absent — should not raise."""
        block = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal\n"
        result = tool5_uat.parse_scenarios(block)
        assert len(result) == 1
        assert "persona" not in result[0]
        assert "pass_criteria" not in result[0]

    def test_synthetic_data_referenced_in_notes(self):
        """Scenario raw content includes synthetic customer data references."""
        block = (
            "===SCENARIO===\n"
            "ID: UAT-CUST-1\n"
            "TITLE: Customer similarity lookup\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "PASS CRITERIA: Similarity list returned for CUST00000001\n"
            "ESTIMATED TIME: 5\n"
            "NOTES: Use CUST00000001 from customer_similarity_dict.json\n"
        )
        result = tool5_uat.parse_scenarios(block)
        assert len(result) == 1
        assert "CUST00000001" in result[0]["raw"]

    def test_large_number_of_scenarios(self):
        """Boundary: 50 scenarios parsed correctly."""
        blocks = ""
        for i in range(50):
            blocks += (
                f"===SCENARIO===\n"
                f"ID: UAT-BULK-{i}\n"
                f"TITLE: Bulk scenario {i}\n"
                f"TYPE: POSITIVE\n"
            )
        result = tool5_uat.parse_scenarios(blocks)
        assert len(result) == 50
        assert result[49]["id"] == "UAT-BULK-49"

    def test_no_scenario_delimiter_returns_empty(self):
        """Input with no delimiter at all → no scenarios."""
        result = tool5_uat.parse_scenarios("ID: UAT-NO-DELIM-1\nTITLE: Orphan\n")
        assert result == []


# ---------------------------------------------------------------------------
# Tests: build_test_pack_csv
# ---------------------------------------------------------------------------


class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_header_row_correct(self):
        result = tool5_uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_empty_scenarios_only_header(self):
        result = tool5_uat.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # only header

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-STORY1-1",
                "title": "Happy path login",
                "type": "POSITIVE",
                "persona": "Underwriter",
                "pass_criteria": "Dashboard loads",
                "estimated_time": "5",
            }
        ]
        result = tool5_uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Happy path login"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Underwriter"
        assert data_row[4] == "Dashboard loads"
        assert data_row[5] == "5"
        # Tester-filled columns must be blank
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_row_count(self):
        scenarios = [
            {"id": f"UAT-X-{i}", "title": f"Scenario {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "3"}
            for i in range(10)
        ]
        result = tool5_uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 11  # header + 10 data rows

    def test_missing_dict_keys_default_empty(self):
        """Scenarios with missing keys should not raise; cells should be empty."""
        scenarios = [{"id": "UAT-EMPTY-1"}]
        result = tool5_uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2
        assert rows[1][1] == ""  # title absent → empty

    def test_special_characters_in_title(self):
        """Commas and quotes in title must be handled by csv writer."""
        scenarios = [
            {"id": "UAT-SPEC-1",
             "title": 'Input with "quotes" and, commas',
             "type": "NEGATIVE",
             "persona": "Admin",
             "pass_criteria": "Error shown",
             "estimated_time": "2"}
        ]
        result = tool5_uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Input with "quotes" and, commas'

    def test_unicode_persona(self):
        """Persona with Arabic characters (from translation files) handled correctly."""
        scenarios = [
            {"id": "UAT-AR-1",
             "title": "Arabic UI test",
             "type": "POSITIVE",
             "persona": "مستخدم",
             "pass_criteria": "تأكيد",
             "estimated_time": "4"}
        ]
        result = tool5_uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][3] == "مستخدم"

    def test_returns_string(self):
        result = tool5_uat.build_test_pack_csv([])
        assert isinstance(result,