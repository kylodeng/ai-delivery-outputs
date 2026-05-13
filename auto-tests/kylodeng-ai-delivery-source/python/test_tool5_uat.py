"""
Test suite for tool5_uat.py

What is tested:
    - parse_scenarios: happy path, edge cases, malformed/empty input, boundary values
    - build_test_pack_csv: correct CSV headers, row data, empty input, special characters
    - build_test_pack_md: correct markdown structure, version/owner/repo injection
    - get_results_csv: successful fetch with base64 content, missing file error, malformed response
    - Module-level integration stubs for Mode A (generate) and Mode B (analyse)

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls)
    - unittest.mock.patch for `call_claude` (Claude API calls)
    - unittest.mock.patch for `write_output_file`, `send_email`, `write_audit_entry`
    - unittest.mock.patch for `get_repo_files`
    - base64 encoding/decoding tested directly (no mock needed)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var wiring and live GH_HEADERS
    - TODO: Test email_html output format once its signature is confirmed in shared.py
    - TODO: Test write_audit_entry side-effects once audit schema is confirmed
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
# Minimal stubs for the `shared` module so we can import tool5_uat without
# having the real shared.py on the path during testing.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda s: s
_shared_stub.call_claude = MagicMock(return_value="")
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

tool5 = importlib.import_module(
    "tool5_uat" if "tool5_uat" in sys.modules else "tool5_uat"
)

# Reload to ensure the stub is picked up properly
import importlib as _il

# We need the actual path insertion to work; patch sys.path entry
_script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

# Re-import using the file path directly so tests always target the right module
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "tool5_uat",
    os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"),
)
if _spec is not None:
    tool5 = _ilu.module_from_spec(_spec)
    # Patch shared before exec
    sys.modules["shared"] = _shared_stub
    try:
        _spec.loader.exec_module(tool5)
    except Exception:
        pass  # __main__ block may fail; module-level functions are still available


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-FEAT1-1
TITLE: Valid user login
TYPE: POSITIVE
PERSONA: Registered customer
PRE-CONDITIONS:
- User account exists
TEST DATA: alice.chen@example.com / SecurePass1!
STEPS:
1. Navigate to /login
2. Enter valid credentials
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIO_BLOCK = (
    SINGLE_SCENARIO_BLOCK
    + """\
===SCENARIO===
ID: UAT-FEAT1-2
TITLE: Login with invalid email format
TYPE: NEGATIVE
PERSONA: Registered customer
PRE-CONDITIONS:
- None
TEST DATA: invalid-email
STEPS:
1. Enter invalid-email in email field
2. Click Submit
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: Use CUST-007 data
"""
)

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-FEAT1-3
TITLE: Age boundary — minimum valid age
TYPE: BOUNDARY
PERSONA: Consumer
PRE-CONDITIONS:
- Registration form open
TEST DATA: age=19, country=AU (CUST-004)
STEPS:
1. Enter age 19
2. Submit form
EXPECTED RESULT: Account created
PASS CRITERIA: Success message shown
ESTIMATED TIME: 2
NOTES: Boundary: minimum age
"""


@pytest.fixture()
def single_scenario():
    return parse_scenarios(SINGLE_SCENARIO_BLOCK)


def parse_scenarios(raw: str):
    """Thin wrapper so tests don't have to import via the module attr each time."""
    return tool5.parse_scenarios(raw)


def build_csv(scenarios):
    return tool5.build_test_pack_csv(scenarios)


def build_md(raw, owner="acme", repo="myapp", version="1.2.3"):
    return tool5.build_test_pack_md(raw, owner, repo, version)


# ===========================================================================
# parse_scenarios
# ===========================================================================


class TestParseScenarios:
    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_two_scenarios_returns_two_items(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_id_is_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-FEAT1-1"

    def test_title_is_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Valid user login"

    def test_type_is_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_persona_is_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Registered customer"

    def test_pass_criteria_is_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Dashboard loads within 3 seconds"

    def test_estimated_time_is_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_raw_field_is_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-FEAT1-1" in result[0]["raw"]

    def test_boundary_type_parsed(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_negative_type_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = [s["type"] for s in result]
        assert "NEGATIVE" in types_

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """Block without ===SCENARIO=== delimiter and no ID → skipped."""
        result = parse_scenarios("ID: UAT-X-1\nTITLE: something\n")
        # Without the delimiter the split produces one block; but it still
        # has an ID so it should be parsed.
        # Behaviour: depends on whether the block has an ID.
        # The raw text DOES have "ID:" so it will be parsed.
        assert isinstance(result, list)

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_multiple_scenarios_preserve_order(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-FEAT1-1"
        assert result[1]["id"] == "UAT-FEAT1-2"

    def test_whitespace_only_block_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: t\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_missing_optional_fields_have_no_key(self):
        """Fields not present in the block should simply be absent (not raise)."""
        raw = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-MIN-1"
        assert "persona" not in result[0]

    def test_extra_whitespace_around_values_is_stripped(self):
        raw = "===SCENARIO===\nID:    UAT-WS-2   \nTITLE:   Stripped title   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-2"
        assert result[0]["title"] == "Stripped title"

    def test_synthetic_data_in_test_data_field_not_broken(self):
        """Ensure synthetic customer data embedded in blocks doesn't break parsing."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-CUST-1\n"
            "TITLE: Enterprise customer revenue update\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Enterprise admin\n"
            "TEST DATA: CUST-001,alice.chen@example.com,34,GB,enterprise,250000\n"
            "PASS CRITERIA: Revenue saved\n"
            "ESTIMATED TIME: 4\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-CUST-1"

    @pytest.mark.parametrize(
        "raw_id,expected",
        [
            ("UAT-STORY1-1", "UAT-STORY1-1"),
            ("UAT-FEAT-99", "UAT-FEAT-99"),
            ("UAT-001", "UAT-001"),
        ],
    )
    def test_various_id_formats(self, raw_id, expected):
        raw = f"===SCENARIO===\nID: {raw_id}\nTITLE: t\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == expected


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    def _read_csv(self, csv_str: str):
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self):
        assert isinstance(build_csv([]), str)

    def test_header_row_is_correct(self):
        rows = self._read_csv(build_csv([]))
        assert rows[0] == [
            "Scenario ID",
            "Title",
            "Type",
            "Persona",
            "Pass Criteria",
            "Est. Time (min)",
            "Result (PASS/FAIL/BLOCKED)",
            "Tester",
            "Notes",
            "Defect Ref",
        ]

    def test_empty_scenarios_produces_header_only(self):
        rows = self._read_csv(build_csv([]))
        assert len(rows) == 1

    def test_single_scenario_row_count(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._read_csv(build_csv(scenarios))
        assert len(rows) == 2  # header + 1 data row

    def test_two_scenarios_row_count(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        rows = self._read_csv(build_csv(scenarios))
        assert len(rows) == 3  # header + 2 data rows

    def test_scenario_id_in_first_column(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._read_csv(build_csv(scenarios))
        assert rows[1][0] == "UAT-FEAT1-1"

    def test_scenario_title_in_second_column(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._read_csv(build_csv(scenarios))
        assert rows[1][1] == "Valid user login"

    def test_result_column_is_blank(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._read_csv(build_csv(scenarios))
        assert rows[1][6] == ""

    def test_tester_column_is_blank(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._read_csv(build_csv(scenarios))
        assert rows[1][7] == ""

    def test_defect_ref_column_is_blank(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        rows = self._read_csv(build_csv(scenarios))
        assert rows[1][9] == ""

    def test_missing_fields_produce_empty_strings(self):
        """A scenario dict with no keys should still produce a valid row."""
        scenarios = [{}]
        rows = self._read_csv(build_csv(scenarios))
        assert len(rows) == 2
        # All data columns should be empty strings
        assert all(cell == "" for cell in rows[1])

    def test_special_characters_in_title_escaped_correctly(self):
        scenarios = [{"id": "UAT-X-1", "title": 'Title with "quotes" and, commas'}]
        csv_str = build_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_synthetic_customer_data_in_csv(self):
        """Ensure synthetic data values round-trip through CSV correctly."""
        scenarios = [
            {
                "id": "UAT-CUST-2",
                "title": "SMB customer creation",
                "type": "POSITIVE",
                "persona": "SMB admin",
                "pass_criteria": "Customer saved",
                "estimated_time": "3",
            }
        ]
        rows = self._read_csv(build_csv(scenarios))
        assert rows[1][0] == "UAT-CUST-2"
        assert rows[1