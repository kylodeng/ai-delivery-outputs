"""
Tests for tool5_uat.py
======================
What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty/malformed input
  - build_test_pack_csv(): correct headers, row generation, empty scenarios, special characters
  - build_test_pack_md(): output format, metadata injection, version/owner/repo embedding
  - get_results_csv(): successful fetch + base64 decode, missing content key, network errors
  - build_test_pack_csv with synthetic data derived from model_card / customer data shapes

Mocks used:
  - requests.get (patched via unittest.mock.patch) — never makes real HTTP calls
  - shared module functions: call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry (patched at import level)
  - base64 decoding exercised with controlled encoded payloads
  - datetime.datetime.utcnow patched for deterministic markdown output

TODOs:
  - TODO: Integration tests for __main__ block require full env var setup + GitHub API access
  - TODO: call_claude interaction tests need a real/stubbed Claude API contract
  - TODO: end-to-end Mode A (generate) and Mode B (analyse) flow tests need orchestration context
"""

import base64
import csv
import datetime
import io
import json
import sys
import os
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module before tool5_uat imports it so we never hit
# real network/filesystem helpers.
# ---------------------------------------------------------------------------
shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="{}")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now import the module under test
import importlib
import tool5_uat as uat


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_scenario_block(
    scenario_id="UAT-STORY1-1",
    title="User can log in",
    type_="POSITIVE",
    persona="Standard User",
    pass_criteria="Dashboard is displayed",
    estimated_time="5",
    extra_lines="",
) -> str:
    return (
        f"ID: {scenario_id}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PRE-CONDITIONS:\n- System is running\n"
        f"TEST DATA: username=test@example.com password=Test1234!\n"
        f"STEPS:\n1. Navigate to login page\n2. Enter credentials\n3. Click login\n"
        f"EXPECTED RESULT: User is logged in\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"NOTES: None\n"
        f"{extra_lines}"
    )


@pytest.fixture
def single_scenario_raw():
    return "===SCENARIO===\n" + _make_scenario_block()


@pytest.fixture
def multi_scenario_raw():
    blocks = [
        _make_scenario_block("UAT-STORY1-1", "Login happy path", "POSITIVE", "Admin", "Dashboard shown", "3"),
        _make_scenario_block("UAT-STORY1-2", "Login with wrong password", "NEGATIVE", "Standard User", "Error shown", "2"),
        _make_scenario_block("UAT-STORY1-3", "Login with max-length password", "BOUNDARY", "Standard User", "Login succeeds or fails gracefully", "4"),
    ]
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


@pytest.fixture
def sample_scenarios():
    return [
        {
            "id": "UAT-STORY1-1",
            "title": "Login happy path",
            "type": "POSITIVE",
            "persona": "Admin",
            "pass_criteria": "Dashboard shown",
            "estimated_time": "3",
            "raw": _make_scenario_block("UAT-STORY1-1"),
        },
        {
            "id": "UAT-STORY1-2",
            "title": "Login with wrong password",
            "type": "NEGATIVE",
            "persona": "Standard User",
            "pass_criteria": "Error shown",
            "estimated_time": "2",
            "raw": _make_scenario_block("UAT-STORY1-2"),
        },
    ]


# ---------------------------------------------------------------------------
# parse_scenarios() tests
# ---------------------------------------------------------------------------

class TestParseScenarios:

    def test_single_scenario_parsed(self, single_scenario_raw):
        result = uat.parse_scenarios(single_scenario_raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "User can log in"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Standard User"
        assert s["pass_criteria"] == "Dashboard is displayed"
        assert s["estimated_time"] == "5"

    def test_raw_field_present(self, single_scenario_raw):
        result = uat.parse_scenarios(single_scenario_raw)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_multiple_scenarios_parsed(self, multi_scenario_raw):
        result = uat.parse_scenarios(multi_scenario_raw)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        # Raw Claude output without any ===SCENARIO=== delimiter
        result = uat.parse_scenarios("Some random text without delimiter")
        assert result == []

    def test_delimiter_only_no_content_returns_empty(self):
        result = uat.parse_scenarios("===SCENARIO===\n   \n===SCENARIO===\n   ")
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        block = (
            "===SCENARIO===\n"
            "TITLE: Some title\n"
            "TYPE: POSITIVE\n"
            "PERSONA: User\n"
        )
        result = uat.parse_scenarios(block)
        assert result == []

    def test_scenario_missing_optional_fields_still_parsed(self):
        block = "===SCENARIO===\nID: UAT-X-1\nTITLE: Minimal\n"
        result = uat.parse_scenarios(block)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-X-1"
        assert s["title"] == "Minimal"
        # Optional fields should be absent (not defaulted to wrong value)
        assert "type" not in s or s.get("type") == ""

    def test_whitespace_stripped_from_values(self):
        block = "===SCENARIO===\nID:   UAT-TRIM-1   \nTITLE:   Trimmed Title   \n"
        result = uat.parse_scenarios(block)
        assert result[0]["id"] == "UAT-TRIM-1"
        assert result[0]["title"] == "Trimmed Title"

    def test_pass_criteria_with_colon_in_value(self):
        block = (
            "===SCENARIO===\n"
            "ID: UAT-COL-1\n"
            "PASS CRITERIA: HTTP 200: response body contains token\n"
        )
        result = uat.parse_scenarios(block)
        assert result[0]["pass_criteria"] == "HTTP 200: response body contains token"

    def test_type_values_positive_negative_boundary(self):
        types_to_test = ["POSITIVE", "NEGATIVE", "BOUNDARY"]
        for t in types_to_test:
            block = f"===SCENARIO===\nID: UAT-TYPE-1\nTYPE: {t}\n"
            result = uat.parse_scenarios(block)
            assert result[0]["type"] == t

    def test_large_number_of_scenarios(self):
        blocks = []
        for i in range(50):
            blocks.append(_make_scenario_block(scenario_id=f"UAT-LOAD-{i}"))
        raw = "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)
        result = uat.parse_scenarios(raw)
        assert len(result) == 50

    def test_unicode_content_in_scenario(self):
        # Simulate Arabic/Unicode test data (from frontend translations fixture)
        block = (
            "===SCENARIO===\n"
            "ID: UAT-ARABIC-1\n"
            "TITLE: تأكيد الإجراء\n"
            "PERSONA: مستخدم عربي\n"
        )
        result = uat.parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["title"] == "تأكيد الإجراء"

    def test_scenario_with_synthetic_customer_id(self):
        block = (
            "===SCENARIO===\n"
            "ID: UAT-CUST-1\n"
            "TITLE: Customer similarity lookup for CUST00000001\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "PASS CRITERIA: Returns 20 similar customers\n"
            "ESTIMATED TIME: 3\n"
        )
        result = uat.parse_scenarios(block)
        assert result[0]["id"] == "UAT-CUST-1"
        assert "CUST00000001" in result[0]["title"]

    @pytest.mark.parametrize("estimated_time", ["0", "1", "60", "120", "999"])
    def test_estimated_time_boundary_values(self, estimated_time):
        block = f"===SCENARIO===\nID: UAT-T-1\nESTIMATED TIME: {estimated_time}\n"
        result = uat.parse_scenarios(block)
        assert result[0]["estimated_time"] == estimated_time


# ---------------------------------------------------------------------------
# build_test_pack_csv() tests
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:

    def test_returns_string(self, sample_scenarios):
        result = uat.build_test_pack_csv(sample_scenarios)
        assert isinstance(result, str)

    def test_header_row_correct(self, sample_scenarios):
        result = uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert header == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_row_count_matches_scenarios(self, sample_scenarios):
        result = uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # 1 header + len(sample_scenarios) data rows
        assert len(rows) == 1 + len(sample_scenarios)

    def test_data_row_values_correct(self, sample_scenarios):
        result = uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        first_row = next(reader)
        s = sample_scenarios[0]
        assert first_row[0] == s["id"]
        assert first_row[1] == s["title"]
        assert first_row[2] == s["type"]
        assert first_row[3] == s["persona"]
        assert first_row[4] == s["pass_criteria"]
        assert first_row[5] == s["estimated_time"]

    def test_result_tester_notes_defect_columns_empty(self, sample_scenarios):
        result = uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        for row in reader:
            assert row[6] == ""   # Result
            assert row[7] == ""   # Tester
            assert row[8] == ""   # Notes
            assert row[9] == ""   # Defect Ref

    def test_empty_scenarios_list_produces_header_only(self):
        result = uat.build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_missing_fields_in_scenario_produce_empty_strings(self):
        scenarios = [{"id": "UAT-SPARSE-1"}]  # most fields missing
        result = uat.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[0] == "UAT-SPARSE-1"
        assert row[1] == ""
        assert row[2] == ""

    def test_special_characters_in_csv(self):
        scenarios = [
            {
                "id": "UAT-SPECIAL-1",
                "title": 'Title with "quotes" and, commas',
                "type": "POSITIVE",
                "persona": "User & Admin",
                "pass_criteria": "Result: <success>",
                "estimated_time": "5",
            }
        ]
        result = uat.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[1] == 'Title with "quotes" and, commas'
        assert row[3] == "User & Admin"
        assert row[4] == "Result: <success>"

    def test_unicode_in_csv(self):
        scenarios = [
            {
                "id": "UAT-ARABIC-1",
                "title": "تأكيد الإجراء",
                "type": "POSITIVE",
                "persona": "مستخدم",
                "pass_criteria": "النجاح",
                "estimated_time": "2",
            }
        ]
        result = uat.build_test_pack_csv(scenarios)
        assert "تأكيد الإجراء" in result

    def test_csv_is_valid_format(self, sample_scenarios):
        result = uat.build_test_pack_csv(sample_scenarios)
        # Should parse without errors
        try:
            rows = list(csv.reader(io.StringIO(result)))
            assert len(rows)