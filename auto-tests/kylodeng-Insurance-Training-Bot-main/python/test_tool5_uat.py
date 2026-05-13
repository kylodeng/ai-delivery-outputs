"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input,
      no-delimiter input, partial blocks
    - build_test_pack_csv(): happy path, empty list, missing fields, special chars
    - build_test_pack_md(): happy path, version/owner/repo substitution
    - get_results_csv(): happy path, missing content key, decode correctness

Mocks used:
    - unittest.mock.patch for requests.get (get_results_csv)
    - unittest.mock.patch for base64.b64decode (get_results_csv)
    - shared module symbols stubbed via sys.modules injection

TODOs:
    - TODO: Integration test for full __main__ block requires real env vars + live GH token
    - TODO: call_claude mock needs real response shapes to test end-to-end mode A/B
    - TODO: write_output_file / send_email side-effect testing needs output-repo fixture
    - TODO: Validate SYSTEM_GENERATE / SYSTEM_ANALYSE prompts against Claude response schema
"""

import base64
import csv
import io
import json
import sys
import types
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module so we can import tool5_uat without real creds
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="stubbed claude response")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now safe to import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / Fixtures
# ===========================================================================

def make_scenario_block(
    id_="UAT-STORY1-1",
    title="User can log in",
    type_="POSITIVE",
    persona="Policyholder",
    pass_criteria="Dashboard is shown",
    estimated_time="5",
    extra_lines="",
) -> str:
    """Return a single raw scenario block (without the delimiter prefix)."""
    return (
        f"ID: {id_}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"STEPS:\n1. Open browser\n2. Enter credentials\n3. Submit\n"
        f"EXPECTED RESULT: Dashboard visible\n"
        f"{extra_lines}"
    )


def make_raw_output(*blocks) -> str:
    """Join blocks with the ===SCENARIO=== delimiter as Claude would output."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_happy_path(self):
        raw = make_raw_output(make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "User can log in"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Policyholder"
        assert s["pass_criteria"] == "Dashboard is shown"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_parsed_correctly(self):
        block1 = make_scenario_block(id_="UAT-F1-1", title="Scenario One")
        block2 = make_scenario_block(id_="UAT-F1-2", title="Scenario Two", type_="NEGATIVE")
        block3 = make_scenario_block(id_="UAT-F1-3", title="Scenario Three", type_="BOUNDARY")
        raw = make_raw_output(block1, block2, block3)
        result = parse_scenarios(raw)
        assert len(result) == 3
        assert result[0]["id"] == "UAT-F1-1"
        assert result[1]["type"] == "NEGATIVE"
        assert result[2]["type"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        """Block without the ===SCENARIO=== delimiter → no valid blocks."""
        raw = make_scenario_block()  # no delimiter prefix
        result = parse_scenarios(raw)
        # The block has no ID-bearing content after splitting on ===SCENARIO===
        # so nothing with an id is appended.
        assert result == []

    def test_block_without_id_is_skipped(self):
        """A block missing the ID line must be discarded."""
        block_no_id = "TITLE: No ID here\nTYPE: POSITIVE\nPERSONA: Admin\n"
        raw = "===SCENARIO===\n" + block_no_id
        result = parse_scenarios(raw)
        assert result == []

    def test_raw_field_contains_full_block_text(self):
        block = make_scenario_block(id_="UAT-RAW-1")
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert "UAT-RAW-1" in result[0]["raw"]

    def test_missing_optional_fields_default_to_absent(self):
        """Only ID is mandatory; other keys may be absent without crashing."""
        minimal = "ID: UAT-MIN-1\n"
        raw = "===SCENARIO===\n" + minimal
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert "title" not in result[0]
        assert "type" not in result[0]

    def test_extra_whitespace_around_values_is_stripped(self):
        block = "ID:   UAT-WS-1  \nTITLE:   Spaces test   \nTYPE:  NEGATIVE  \n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Spaces test"
        assert result[0]["type"] == "NEGATIVE"

    def test_scenario_with_leading_text_before_first_delimiter_is_ignored(self):
        """Text before the first ===SCENARIO=== (preamble) should be ignored."""
        preamble = "Here is the test pack:\n\n"
        block = make_scenario_block(id_="UAT-P-1")
        raw = preamble + "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-P-1"

    def test_only_delimiters_no_content(self):
        raw = "===SCENARIO===\n===SCENARIO===\n===SCENARIO==="
        result = parse_scenarios(raw)
        assert result == []

    def test_persona_with_spaces(self):
        block = make_scenario_block(id_="UAT-PS-1", persona="Senior Policyholder")
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["persona"] == "Senior Policyholder"

    def test_estimated_time_with_unit_string(self):
        block = make_scenario_block(id_="UAT-T-1", estimated_time="10 minutes")
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["estimated_time"] == "10 minutes"

    def test_insurance_synthetic_data_ids(self):
        """Use synthetic data product names as story refs in scenario IDs."""
        block1 = make_scenario_block(id_="UAT-GENIII-1", title="Generations II whole life protection")
        block2 = make_scenario_block(id_="UAT-HEALTH-1", title="Designated hospital claim submission")
        raw = make_raw_output(block1, block2)
        result = parse_scenarios(raw)
        assert any(s["id"] == "UAT-GENIII-1" for s in result)
        assert any(s["id"] == "UAT-HEALTH-1" for s in result)

    def test_large_number_of_scenarios(self):
        blocks = [make_scenario_block(id_=f"UAT-LARGE-{i}") for i in range(50)]
        raw = make_raw_output(*blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    @pytest.mark.parametrize("type_val", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_type_values(self, type_val):
        block = make_scenario_block(id_=f"UAT-TYPE-{type_val}", type_=type_val)
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["type"] == type_val


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_header_row_present(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_empty_scenario_list_produces_only_header(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # only the header

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-1-1",
                "title": "Login test",
                "type": "POSITIVE",
                "persona": "Policyholder",
                "pass_criteria": "Dashboard shown",
                "estimated_time": "5",
            }
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-1-1"
        assert data_row[1] == "Login test"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Policyholder"
        assert data_row[4] == "Dashboard shown"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref must be blank
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "3"}
            for i in range(10)
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 11  # 1 header + 10 data

    def test_missing_fields_default_to_empty_string(self):
        scenarios = [{"id": "UAT-MISS-1"}]  # only id provided
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][0] == "UAT-MISS-1"
        # All other populated columns should be empty
        for col in rows[1][1:6]:
            assert col == ""

    def test_special_characters_in_fields(self):
        scenarios = [
            {
                "id": "UAT-SC-1",
                "title": 'Title with "quotes" and, commas',
                "type": "NEGATIVE",
                "persona": "User",
                "pass_criteria": "Error msg: 'Invalid'",
                "estimated_time": "10",
            }
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Title with "quotes" and, commas'
        assert rows[1][4] == "Error msg: 'Invalid'"

    def test_output_is_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_csv_ends_with_newline(self):
        result = build_test_pack_csv([])
        assert result.endswith("\r\n") or result.endswith("\n")

    @pytest.mark.parametrize("scenario,expected_type", [
        ({"id": "UAT-A", "type": "POSITIVE"}, "POSITIVE"),
        ({"id": "UAT-B", "type": "NEGATIVE"}, "NEGATIVE"),
        ({"id": "UAT-C", "type": "BOUNDARY"}, "BOUNDARY"),
    ])
    def test_type_values_preserved(self, scenario, expected_type):
        result = build_test_pack_csv([scenario])
        rows = self._parse_csv(result)
        assert rows[1][2] == expected_type

    def test_insurance_product_data_in_csv(self):
        """Verify synthetic insurance data passes through correctly."""
        scenarios = [
            {
                "id": "UAT-GENIII-1",
                "title": "Generations II — verify double bonus calculation",
                "type": "BOUNDARY",
                "persona": "Financial Advisor",
                "pass_criteria": "Bonus equals 2x base declared",
                "estimated_time": "15",
            },
            {
                "id": "UAT-HEALTH-1",
                "title": "Designated hospital claim — cashless arrangement",
                "type": "POSITIVE",
                "persona": "Policyholder",
                "pass_criteria": "Claim pre-authorised within 2 hours",
                "estimated_time": "20",
            },
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(