"""
Test module for tool5_uat.py

What is tested:
    - parse_scenarios(): parsing Claude's scenario output into structured dicts
    - build_test_pack_csv(): building a CSV test sheet from scenario dicts
    - build_test_pack_md(): building a Markdown test pack document
    - get_results_csv(): fetching a CSV results file from the output repo (GitHub API)
    - Integration smoke tests for __main__ entry-point environment variable handling

Mocks used:
    - unittest.mock.patch for requests.get (GitHub API calls)
    - unittest.mock.patch for shared module functions:
        call_claude, get_repo_files, write_output_file,
        send_email, email_html, write_audit_entry, clean_json
    - base64 encoding/decoding for fake file content

TODOs:
    - TODO: Full __main__ integration tests require a real or fully stubbed GitHub
      environment plus Claude API keys — stubs provided below.
    - TODO: Test build_test_pack_md timestamp determinism (utcnow is not mocked here).
    - TODO: Tests for write_output_file and send_email side-effects in __main__ flow
      require deeper environment setup.
"""

import base64
import csv
import io
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without the real 'shared' module
# ---------------------------------------------------------------------------

# Build a minimal fake 'shared' module so we can import tool5_uat without
# needing the real shared.py or its transitive dependencies.
_fake_shared = types.ModuleType("shared")
_fake_shared.clean_json = MagicMock(side_effect=lambda x: x)
_fake_shared.call_claude = MagicMock(return_value="")
_fake_shared.get_repo_files = MagicMock(return_value={})
_fake_shared.write_output_file = MagicMock(return_value=None)
_fake_shared.send_email = MagicMock(return_value=None)
_fake_shared.email_html = MagicMock(return_value="<html/>")
_fake_shared.write_audit_entry = MagicMock(return_value=None)
_fake_shared.OUTPUT_REPO_OWNER = "test-owner"
_fake_shared.OUTPUT_REPO = "test-repo"
_fake_shared.GH_HEADERS = {"Authorization": "token fake"}
_fake_shared.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _fake_shared)

# Now import the module under test
import importlib
import tool5_uat  # noqa: E402  (inserted into path by sys.path.insert in the module itself)

# Re-export helpers for brevity
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
TITLE: Successful risk classification for standard customer
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is running
- Customer CUST00000001 exists in the database
TEST DATA: Age=35, Annual_Income=75000, Employment_Status=permanent
STEPS:
1. Log in as Underwriter
2. Navigate to risk classification screen
3. Enter customer ID CUST00000001 and submit
EXPECTED RESULT: Risk classification displayed
PASS CRITERIA: Risk_Classification value shown on screen equals model output
ESTIMATED TIME: 5
NOTES: Uses CatBoostClassifier model
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-001
TITLE: Successful risk classification for standard customer
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is running
TEST DATA: Age=35, Annual_Income=75000
STEPS:
1. Log in
2. Submit form
EXPECTED RESULT: Success message
PASS CRITERIA: Status = PASS
ESTIMATED TIME: 3
NOTES: None
===SCENARIO===
ID: UAT-RISK-002
TITLE: Invalid customer ID rejected
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is running
TEST DATA: CustomerID=INVALID
STEPS:
1. Log in
2. Enter INVALID as customer ID
3. Submit
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Error code 404 shown
ESTIMATED TIME: 2
NOTES: Boundary check
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-INCOME-BOUNDARY-001
TITLE: Maximum income boundary
TYPE: BOUNDARY
PERSONA: Senior Underwriter
PRE-CONDITIONS:
- System is running
TEST DATA: Annual_Income=9999999999
STEPS:
1. Enter max income value
2. Submit
EXPECTED RESULT: System accepts or gracefully rejects
PASS CRITERIA: No unhandled exception
ESTIMATED TIME: 2
NOTES: Boundary value analysis
"""


def _make_github_content_response(content_str: str) -> dict:
    """Return a dict mimicking GitHub Contents API response."""
    encoded = base64.b64encode(content_str.encode()).decode()
    return {"content": encoded, "encoding": "base64"}


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("Some random text without any delimiter")
        assert result == []

    def test_single_scenario_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-RISK-001"
        assert s["title"] == "Successful risk classification for standard customer"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Risk_Classification value shown on screen equals model output"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-RISK-001"
        assert result[1]["id"] == "UAT-RISK-002"

    def test_boundary_scenario_type_preserved(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["type"] == "BOUNDARY"

    def test_negative_scenario_type_preserved(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = {s["type"] for s in result}
        assert "NEGATIVE" in types_

    def test_raw_field_contains_original_block(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-RISK-001" in result[0]["raw"]
        assert "CatBoostClassifier" in result[0]["raw"]

    def test_block_without_id_is_skipped(self):
        block = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
"""
        result = parse_scenarios(block)
        assert result == []

    def test_multiple_scenarios_only_with_id_included(self):
        block = """\
===SCENARIO===
TITLE: Missing ID
TYPE: POSITIVE
PERSONA: Admin
===SCENARIO===
ID: UAT-VALID-001
TITLE: Valid scenario
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Screen shows success
ESTIMATED TIME: 1
"""
        result = parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-VALID-001"

    def test_leading_text_before_first_delimiter_ignored(self):
        raw = "Some preamble text\n" + TWO_SCENARIO_BLOCK
        result = parse_scenarios(raw)
        assert len(result) == 2

    def test_extra_whitespace_in_id_stripped(self):
        block = """\
===SCENARIO===
ID:   UAT-SPACE-001   
TITLE: Spaced ID
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: OK
ESTIMATED TIME: 1
"""
        result = parse_scenarios(block)
        assert result[0]["id"] == "UAT-SPACE-001"

    def test_missing_optional_fields_default_to_empty(self):
        block = """\
===SCENARIO===
ID: UAT-MIN-001
TITLE: Minimal scenario
"""
        result = parse_scenarios(block)
        assert len(result) == 1
        s = result[0]
        assert s.get("type", "") == ""
        assert s.get("persona", "") == ""
        assert s.get("pass_criteria", "") == ""
        assert s.get("estimated_time", "") == ""

    @pytest.mark.parametrize("scenario_count", [1, 3, 5, 10])
    def test_various_scenario_counts(self, scenario_count):
        blocks = ""
        for i in range(1, scenario_count + 1):
            blocks += f"""\
===SCENARIO===
ID: UAT-PARAM-{i:03d}
TITLE: Scenario {i}
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Criteria {i}
ESTIMATED TIME: {i}
"""
        result = parse_scenarios(blocks)
        assert len(result) == scenario_count

    def test_parse_with_synthetic_customer_data_in_test_data_field(self):
        """Verify synthetic data (customer IDs, income values) survive round-trip."""
        block = """\
===SCENARIO===
ID: UAT-CUST-001
TITLE: Customer similarity lookup
TYPE: POSITIVE
PERSONA: Underwriter
TEST DATA: CustomerID=CUST00000001, similar=[CUST00006151, CUST00000272]
STEPS:
1. Enter CUST00000001
2. Submit
EXPECTED RESULT: Similarity list returned
PASS CRITERIA: Response contains CUST00006151
ESTIMATED TIME: 3
NOTES: Uses customer_similarity_dict.json fixture
"""
        result = parse_scenarios(block)
        assert len(result) == 1
        assert "CUST00000001" in result[0]["raw"]


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_empty_scenarios_returns_header_only(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # header only
        assert rows[0][0] == "Scenario ID"

    def test_header_columns_correct(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        expected_headers = [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]
        assert rows[0] == expected_headers

    def test_single_scenario_produces_two_rows(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2  # header + 1 data row

    def test_scenario_fields_mapped_correctly(self):
        scenarios = [
            {
                "id": "UAT-RISK-001",
                "title": "Risk classification test",
                "type": "POSITIVE",
                "persona": "Underwriter",
                "pass_criteria": "Status = PASS",
                "estimated_time": "5",
            }
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-RISK-001"
        assert data_row[1] == "Risk classification test"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Underwriter"
        assert data_row[4] == "Status = PASS"
        assert data_row[5] == "5"

    def test_result_tester_notes_defect_columns_empty(self):
        scenarios = [{"id": "UAT-X-001", "title": "T", "type": "P", "persona": "A",
                      "pass_criteria": "C", "estimated_time": "1"}]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_produce_correct_row_count(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 3  # header + 2

    def test_missing_keys_default_to_empty_string(self):
        scenarios = [{"id": "UAT-MIN-001"}]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-001"
        assert data_row[1] == ""  # title missing → empty

    def test_output_is_valid_csv_string(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_special_characters_in_fields_handled(self):
        scenarios = [
            {
                "id": 'UAT-SPECIAL-001',
                "title": 'Title with "quotes" and, commas',
                "type": "POSITIVE",
                "persona": "Tester",
                "pass_criteria": "Result=OK",
                "estimated_time": "2",
            }
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    @pytest.mark.parametrize("scenario_count", [0, 1, 5, 50])
    def test_row_count_matches_scenario_count(self, scenario_count):
        scenarios = [
            {"id": f"UAT-{i:03d}", "title": f"T{i}", "type": "POSITIVE",
             "persona": "Tester", "pass_criteria": "OK", "estimated_time": "1"}
            for i in range(scenario_count)
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == scenario_count + 1  # +