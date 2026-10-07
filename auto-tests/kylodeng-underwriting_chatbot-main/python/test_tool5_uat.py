"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, missing fields,
    no delimiter, multiple scenarios, malformed blocks)
  - build_test_pack_csv(): correct CSV headers, row content, empty list
  - build_test_pack_md(): correct markdown structure, version/owner/repo embedding
  - get_results_csv(): happy path with mocked GitHub API, missing file (FileNotFoundError),
    malformed response

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for shared module functions (call_claude, get_repo_files,
    write_output_file, send_email, write_audit_entry)
  - base64 encoding helpers used inline

TODOs:
  - TODO: Integration test for __main__ block requires full env var setup + secrets
  - TODO: Tests for SYSTEM_GENERATE / SYSTEM_ANALYSE prompt strings (content validation)
    require Claude API access — stub provided
  - TODO: build_test_pack_md timestamp is non-deterministic; consider injecting clock
"""

import base64
import csv
import io
import json
import sys
import os
import types
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal fake `shared` module so tool5_uat imports cleanly
# without needing the real shared.py or any secrets.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="stub response")
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


# Insert fake shared module before importing tool5_uat
sys.modules.setdefault("shared", _make_shared_stub())

# Now import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# We load the module programmatically so the path is flexible.
# If the file doesn't exist in CI, tests will be collected but skipped.
_tool5 = None
_import_error = None

try:
    spec = importlib.util.spec_from_file_location("tool5_uat", str(_script_path))
    _tool5 = importlib.util.module_from_spec(spec)
    sys.modules["tool5_uat"] = _tool5
    spec.loader.exec_module(_tool5)
except FileNotFoundError as exc:
    _import_error = exc
except Exception as exc:
    _import_error = exc


def _require_tool5():
    """Skip the test if the module could not be loaded."""
    if _tool5 is None:
        pytest.skip(f"tool5_uat.py could not be imported: {_import_error}")


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_TEXT = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriting risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is logged in
- Application form is complete
TEST DATA: Age=35, Annual_Income=75000, Risk_Classification=Low
STEPS:
1. Navigate to application dashboard
2. Select customer CUST00000001
3. Click 'Assess Risk'
EXPECTED RESULT: System returns Risk_Classification = Low
PASS CRITERIA: Risk_Classification field displays 'Low'
ESTIMATED TIME: 5
NOTES: Ensure model card version matches backend/model_card.json
"""

TWO_SCENARIO_TEXT = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Admin
PRE-CONDITIONS:
- System is up
TEST DATA: username=admin@example.com, password=Synth@1234
STEPS:
1. Open login page
2. Enter credentials
3. Click Login
EXPECTED RESULT: Dashboard loads
PASS CRITERIA: Dashboard title visible
ESTIMATED TIME: 3
NOTES: None

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with wrong password
TYPE: NEGATIVE
PERSONA: Admin
PRE-CONDITIONS:
- System is up
TEST DATA: username=admin@example.com, password=WrongPass
STEPS:
1. Open login page
2. Enter wrong password
3. Click Login
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Error banner visible
ESTIMATED TIME: 2
NOTES: JIRA-999
"""

BOUNDARY_SCENARIO_TEXT = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Max annual income boundary
TYPE: BOUNDARY
PERSONA: Underwriter
PRE-CONDITIONS:
- User logged in
TEST DATA: Annual_Income=9999999999
STEPS:
1. Enter max income value
EXPECTED RESULT: System accepts or rejects gracefully
PASS CRITERIA: No 500 error returned
ESTIMATED TIME: 10
NOTES: [TESTER: verify this]
"""

MINIMAL_SCENARIO_FIELDS = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal fields scenario
TYPE: POSITIVE
"""


def _make_scenario_dicts():
    """Return parsed scenario list from TWO_SCENARIO_TEXT."""
    _require_tool5()
    return _tool5.parse_scenarios(TWO_SCENARIO_TEXT)


# ---------------------------------------------------------------------------
# parse_scenarios — happy paths
# ---------------------------------------------------------------------------

class TestParseScenarios:

    def setup_method(self):
        _require_tool5()

    def test_single_scenario_id_extracted(self):
        result = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_extracted(self):
        result = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        assert result[0]["title"] == "Successful underwriting risk classification"

    def test_single_scenario_type_extracted(self):
        result = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_extracted(self):
        result = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        assert result[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria_extracted(self):
        result = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        assert "Low" in result[0]["pass_criteria"]

    def test_single_scenario_estimated_time_extracted(self):
        result = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_present(self):
        result = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        assert "ID: UAT-STORY1-1" in result[0]["raw"]

    def test_two_scenarios_count(self):
        result = _tool5.parse_scenarios(TWO_SCENARIO_TEXT)
        assert len(result) == 2

    def test_two_scenarios_ids(self):
        result = _tool5.parse_scenarios(TWO_SCENARIO_TEXT)
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_second_scenario_type_negative(self):
        result = _tool5.parse_scenarios(TWO_SCENARIO_TEXT)
        neg = next(s for s in result if s["id"] == "UAT-STORY1-2")
        assert neg["type"] == "NEGATIVE"

    def test_boundary_scenario_type(self):
        result = _tool5.parse_scenarios(BOUNDARY_SCENARIO_TEXT)
        assert result[0]["type"] == "BOUNDARY"

    def test_estimated_time_boundary_scenario(self):
        result = _tool5.parse_scenarios(BOUNDARY_SCENARIO_TEXT)
        assert result[0]["estimated_time"] == "10"

    # ------------------------------------------------------------------
    # Edge / negative cases
    # ------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        result = _tool5.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = _tool5.parse_scenarios("Some random text without delimiters")
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = _tool5.parse_scenarios("===SCENARIO===")
        assert result == []

    def test_block_without_id_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = _tool5.parse_scenarios(raw)
        assert result == []

    def test_minimal_scenario_has_raw(self):
        result = _tool5.parse_scenarios(MINIMAL_SCENARIO_FIELDS)
        assert len(result) == 1
        assert result[0]["raw"] != ""

    def test_missing_fields_default_to_absent_keys(self):
        result = _tool5.parse_scenarios(MINIMAL_SCENARIO_FIELDS)
        s = result[0]
        # persona, pass_criteria, estimated_time not present
        assert "persona" not in s or s.get("persona") == ""
        assert s.get("id") == "UAT-MIN-1"

    def test_multiple_delimiters_lead_correct_count(self):
        raw = TWO_SCENARIO_TEXT + BOUNDARY_SCENARIO_TEXT
        result = _tool5.parse_scenarios(raw)
        assert len(result) == 3

    def test_whitespace_only_block_skipped(self):
        raw = "===SCENARIO===\n   \n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: Real\nTYPE: POSITIVE\n"
        result = _tool5.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_extra_spaces_in_field_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-1   \nTITLE:   Spaced Title   \nTYPE: POSITIVE\n"
        result = _tool5.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"
        assert result[0]["title"] == "Spaced Title"

    def test_unicode_content_handled(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-AR-1\n"
            "TITLE: اختبار\n"
            "TYPE: POSITIVE\n"
            "PERSONA: مستخدم\n"
        )
        result = _tool5.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["title"] == "اختبار"


# ---------------------------------------------------------------------------
# build_test_pack_csv
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:

    def setup_method(self):
        _require_tool5()

    def _parse_csv(self, csv_str: str):
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self):
        result = _tool5.build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        rows = self._parse_csv(_tool5.build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_produces_header_only(self):
        rows = self._parse_csv(_tool5.build_test_pack_csv([]))
        assert len(rows) == 1  # header only (trailing newline may add blank)

    def test_single_scenario_produces_two_rows(self):
        scenarios = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == 2  # header + 1 data row

    def test_scenario_id_in_csv(self):
        scenarios = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        ids = [r[0] for r in rows[1:] if r]
        assert "UAT-STORY1-1" in ids

    def test_title_in_csv(self):
        scenarios = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        titles = [r[1] for r in rows[1:] if r]
        assert "Successful underwriting risk classification" in titles

    def test_result_column_initially_empty(self):
        scenarios = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        data_row = rows[1]
        assert data_row[6] == ""

    def test_tester_column_initially_empty(self):
        scenarios = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        data_row = rows[1]
        assert data_row[7] == ""

    def test_defect_ref_column_initially_empty(self):
        scenarios = _tool5.parse_scenarios(SINGLE_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        data_row = rows[1]
        assert data_row[9] == ""

    def test_two_scenarios_produce_three_rows(self):
        scenarios = _tool5.parse_scenarios(TWO_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        data_rows = [r for r in rows if any(r)]
        assert len(data_rows) == 3

    def test_type_column_populated(self):
        scenarios = _tool5.parse_scenarios(TWO_SCENARIO_TEXT)
        rows = self._parse_csv(_tool5.build_test_pack_csv(scenarios))
        types_col = [r[2] for r in rows[1:] if r]
        assert "POSITIVE" in types_col
        assert "NEGATIVE" in types_col

    def test_missing_id_produces_empty_string_in_csv(self):
        scenario = {"title": "No ID", "type": "POSITIVE"}
        rows = self._parse_csv(_tool5.build_test_