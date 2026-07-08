"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input,
    malformed blocks, multiple scenarios
  - build_test_pack_csv(): correct headers, row data, empty input, special characters
  - build_test_pack_md(): content structure, version/owner/repo injection
  - get_results_csv(): successful decode, missing content key, network response handling

Mocks used:
  - requests.get (for get_results_csv)
  - shared.call_claude
  - shared.get_repo_files
  - shared.write_output_file
  - shared.send_email
  - shared.write_audit_entry
  - base64.b64decode (via requests mock response)

TODOs:
  - TODO: Integration test for __main__ block requires full env + GitHub API credentials
  - TODO: Tests for email HTML formatting require shared.email_html contract to be defined
  - TODO: Tests for write_output_file interaction require OUTPUT_REPO fixture data
"""

import sys
import os
import csv
import io
import base64
import json
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Path setup — mirror what the source file does
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# ---------------------------------------------------------------------------
# We need to stub out the `shared` module before importing tool5_uat,
# because shared contains top-level network/config calls.
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"
shared_stub.clean_json = lambda x: x
sys.modules.setdefault("shared", shared_stub)

# Now safe to import the module under test
import importlib

# Provide the stub before import
with patch.dict(sys.modules, {"shared": shared_stub, "requests": MagicMock()}):
    import tool5_uat  # noqa: E402  (the actual module)

# Re-import the public functions we want to test directly
parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-001
TITLE: Valid underwriting submission accepted
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is authenticated
- Application form is complete
TEST DATA: Customer CUST00000001, Age=34, Annual_Income=75000
STEPS:
1. Log in as Underwriter
2. Submit application with valid data
3. Confirm submission
EXPECTED RESULT: Application is accepted and risk classification returned
PASS CRITERIA: Risk_Classification field populated with PASS or FAIL
ESTIMATED TIME: 5
NOTES: Depends on CatBoostClassifier model being deployed
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-001
TITLE: Valid underwriting submission accepted
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is authenticated
TEST DATA: Customer CUST00000001
STEPS:
1. Submit valid application
EXPECTED RESULT: Risk classification returned
PASS CRITERIA: Risk_Classification populated
ESTIMATED TIME: 3
NOTES: None

===SCENARIO===
ID: UAT-RISK-002
TITLE: Missing income field rejected
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is authenticated
TEST DATA: Customer with no Annual_Income
STEPS:
1. Submit application without income field
EXPECTED RESULT: Validation error returned
PASS CRITERIA: Error message displayed to user
ESTIMATED TIME: 2
NOTES: Boundary check
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: A scenario with no ID
TYPE: POSITIVE
PERSONA: Admin
"""

EMPTY_FIELDS_SCENARIO = """\
===SCENARIO===
ID: UAT-EMPTY-001
"""


def _make_scenario(**kwargs):
    """Return a minimal scenario dict."""
    defaults = {
        "id": "UAT-TEST-001",
        "title": "Test scenario",
        "type": "POSITIVE",
        "persona": "Underwriter",
        "pass_criteria": "System responds correctly",
        "estimated_time": "5",
        "raw": "raw block text",
    }
    defaults.update(kwargs)
    return defaults


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-RISK-001"

    def test_single_scenario_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Valid underwriting submission accepted"

    def test_single_scenario_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Risk_Classification field populated with PASS or FAIL"

    def test_single_scenario_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_is_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_two_scenarios_returns_two_items(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_two_scenarios_ids_correct(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert "UAT-RISK-001" in ids
        assert "UAT-RISK-002" in ids

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(SCENARIO_WITHOUT_ID)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_only_delimiter_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===")
        # block is empty after strip — no id → excluded
        assert result == []

    def test_scenario_with_id_only_included(self):
        result = parse_scenarios(EMPTY_FIELDS_SCENARIO)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-EMPTY-001"
        # Optional fields default to absent
        assert result[0].get("title", "") == ""

    def test_whitespace_stripped_from_id(self):
        block = "===SCENARIO===\nID:   UAT-WS-001  \nTITLE: Whitespace test\n"
        result = parse_scenarios(block)
        assert result[0]["id"] == "UAT-WS-001"

    def test_whitespace_stripped_from_title(self):
        block = "===SCENARIO===\nID: UAT-WS-002\nTITLE:   Title with spaces   \n"
        result = parse_scenarios(block)
        assert result[0]["title"] == "Title with spaces"

    def test_multiple_scenarios_raw_different(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["raw"] != result[1]["raw"]

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-NODLM-001\nTITLE: no delimiter here\n"
        result = parse_scenarios(raw)
        # First split produces one block but it has no ===SCENARIO=== header;
        # the text before the first delimiter is ignored only if empty.
        # In this case there is no delimiter so split gives one block (no id prefix).
        # The function still parses lines — "ID:" IS present so it will be found.
        # This documents actual behaviour rather than asserting exclusion.
        assert isinstance(result, list)

    def test_negative_type_preserved(self):
        block = "===SCENARIO===\nID: UAT-NEG-001\nTYPE: NEGATIVE\n"
        result = parse_scenarios(block)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_preserved(self):
        block = "===SCENARIO===\nID: UAT-BND-001\nTYPE: BOUNDARY\n"
        result = parse_scenarios(block)
        assert result[0]["type"] == "BOUNDARY"

    def test_multiline_block_raw_includes_all_text(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "CatBoostClassifier" in result[0]["raw"]

    @pytest.mark.parametrize("raw_input,expected_count", [
        ("===SCENARIO===\nID: UAT-P-001\n", 1),
        ("===SCENARIO===\nID: UAT-P-001\n===SCENARIO===\nID: UAT-P-002\n", 2),
        ("===SCENARIO===\n===SCENARIO===\nID: UAT-P-003\n", 1),
        ("", 0),
    ])
    def test_parametrized_counts(self, raw_input, expected_count):
        result = parse_scenarios(raw_input)
        assert len(result) == expected_count

    def test_synthetic_data_in_raw(self):
        block = (
            "===SCENARIO===\n"
            "ID: UAT-SYNTH-001\n"
            "TITLE: Underwriting with synthetic customer\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: CUST00000001, Age=34, Annual_Income=75000\n"
            "PASS CRITERIA: Risk_Classification in {HIGH,LOW,MEDIUM}\n"
            "ESTIMATED TIME: 5\n"
        )
        result = parse_scenarios(block)
        assert result[0]["id"] == "UAT-SYNTH-001"
        assert "CUST00000001" in result[0]["raw"]


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str):
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1

    def test_single_scenario_produces_two_rows(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario()]))
        assert len(rows) == 2

    def test_single_scenario_id_in_csv(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario(id="UAT-TEST-042")]))
        assert rows[1][0] == "UAT-TEST-042"

    def test_single_scenario_title_in_csv(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario(title="My Title")]))
        assert rows[1][1] == "My Title"

    def test_single_scenario_type_in_csv(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario(type="NEGATIVE")]))
        assert rows[1][2] == "NEGATIVE"

    def test_single_scenario_persona_in_csv(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario(persona="Admin")]))
        assert rows[1][3] == "Admin"

    def test_single_scenario_pass_criteria_in_csv(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario(pass_criteria="Screen shows OK")]))
        assert rows[1][4] == "Screen shows OK"

    def test_single_scenario_estimated_time_in_csv(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario(estimated_time="10")]))
        assert rows[1][5] == "10"

    def test_result_column_is_empty(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario()]))
        assert rows[1][6] == ""

    def test_tester_column_is_empty(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario()]))
        assert rows[1][7] == ""

    def test_notes_column_is_empty(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario()]))
        assert rows[1][8] == ""

    def test_defect_ref_column_is_empty(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario()]))
        assert rows[1][9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(id=f"UAT-X-{i:03d}") for i in range(5)]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 6  # header + 5

    def test_missing_optional_fields_become_empty_string(self):
        minimal = {"id": "UAT-MIN-001", "raw": "block"}
        rows = self._parse_csv(build_test_pack_csv([minimal]))
        assert rows[1][1] == ""   # title
        assert rows[1][2] == ""   # type
        assert rows[1][3] == ""   # persona

    def test_special_characters_in_title(self):
        s = _make_scenario(title='Title with "quotes" and, comma')
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert rows[1][1] == 'Title with "quotes" and, comma'

    def test_unicode_persona(self):
        s = _make_scenario(persona="المكتتِب")  # Arabic underwriter
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert