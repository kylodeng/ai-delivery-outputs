"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
    - build_test_pack_csv(): structure, headers, row content, empty scenarios, special chars
    - build_test_pack_md(): content inclusion, version/owner/repo substitution, formatting
    - get_results_csv(): happy path (base64 decode), missing file (FileNotFoundError), API error
    - Mode A (generate) and Mode B (analyse) integration paths (via __main__ block stubs)

Mocks used:
    - unittest.mock.patch for requests.get (get_results_csv)
    - unittest.mock.patch for shared module functions: call_claude, get_repo_files,
      write_output_file, send_email, email_html, write_audit_entry
    - base64 encoding used to build realistic fake API responses

TODOs:
    - TODO: Integration test for full __main__ generate path requires real env vars + Claude mock wiring
    - TODO: Integration test for full __main__ analyse path requires real env vars + Claude mock wiring
    - TODO: Test write_output_file / send_email interactions inside __main__ once entry point
            is refactored into callable functions
    - TODO: Verify clean_json behaviour on edge-case Claude analyse output once shared module is available
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
# Minimal stub for the `shared` module so the import in tool5_uat doesn't
# crash when the real shared.py is not available in the test environment.
# ---------------------------------------------------------------------------

def _build_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="stub response")
    shared.get_repo_files = MagicMock(return_value={"file.py": "content"})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Insert the stub before importing the module under test
if "shared" not in sys.modules:
    sys.modules["shared"] = _build_shared_stub()

# Now import the module under test
import importlib
import tool5_uat  # noqa: E402  (lives in .github/scripts, added to sys.path)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_scenario_block(
    id_="UAT-STORY1-1",
    title="Login with valid credentials",
    type_="POSITIVE",
    persona="End User",
    pass_criteria="User reaches dashboard",
    estimated_time="5",
    extra_lines="",
):
    """Return a single raw scenario text block (without the delimiter)."""
    return (
        f"ID: {id_}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PRE-CONDITIONS:\n- System is online\n"
        f"TEST DATA: username=test@example.com, password=P@ssw0rd!\n"
        f"STEPS:\n1. Navigate to login page\n2. Enter credentials\n3. Click Login\n"
        f"EXPECTED RESULT: Dashboard is displayed\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"NOTES: Check session cookie\n"
        f"{extra_lines}"
    )


def _make_raw_claude_output(*blocks):
    """Join scenario blocks with the ===SCENARIO=== delimiter."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


def _fake_github_file_response(content_str: str) -> dict:
    """Return a dict that mimics the GitHub Contents API response."""
    encoded = base64.b64encode(content_str.encode()).decode()
    return {"content": encoded, "encoding": "base64"}


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_valid_scenario(self):
        raw = _make_raw_claude_output(_make_scenario_block())
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "End User"
        assert s["pass_criteria"] == "User reaches dashboard"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_valid_scenarios(self):
        blocks = [
            _make_scenario_block(id_=f"UAT-F1-{i}", title=f"Scenario {i}")
            for i in range(1, 5)
        ]
        raw = _make_raw_claude_output(*blocks)
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 4
        ids = [s["id"] for s in result]
        assert ids == ["UAT-F1-1", "UAT-F1-2", "UAT-F1-3", "UAT-F1-4"]

    def test_empty_string_returns_empty_list(self):
        result = tool5_uat.parse_scenarios("")
        assert result == []

    def test_no_delimiter_no_id_returns_empty(self):
        result = tool5_uat.parse_scenarios("Some random text without delimiters")
        assert result == []

    def test_block_missing_id_is_skipped(self):
        block_no_id = (
            "TITLE: Something\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Admin\n"
            "PASS CRITERIA: It works\n"
            "ESTIMATED TIME: 3\n"
        )
        raw = "===SCENARIO===\n" + block_no_id
        result = tool5_uat.parse_scenarios(raw)
        assert result == []

    def test_block_missing_optional_fields_still_parsed(self):
        block = "ID: UAT-MIN-1\nTITLE: Minimal\n"
        raw = "===SCENARIO===\n" + block
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        assert s["title"] == "Minimal"
        assert s.get("type") is None
        assert s.get("persona") is None
        assert s.get("pass_criteria") is None
        assert s.get("estimated_time") is None

    def test_raw_field_contains_full_block_text(self):
        block = _make_scenario_block(id_="UAT-RAW-1")
        raw = "===SCENARIO===\n" + block
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        # The raw field should include the full block content
        assert "UAT-RAW-1" in result[0]["raw"]
        assert "STEPS:" in result[0]["raw"]

    def test_leading_garbage_before_first_delimiter_is_ignored(self):
        garbage = "Some preamble text from Claude\n\n"
        block = _make_scenario_block(id_="UAT-G-1")
        raw = garbage + "===SCENARIO===\n" + block
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-G-1"

    def test_extra_whitespace_around_values_is_stripped(self):
        block = "ID:   UAT-WS-1   \nTITLE:   Whitespace Test   \n"
        raw = "===SCENARIO===\n" + block
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace Test"

    def test_negative_scenario_type(self):
        block = _make_scenario_block(id_="UAT-NEG-1", type_="NEGATIVE")
        raw = _make_raw_claude_output(block)
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_scenario_type(self):
        block = _make_scenario_block(id_="UAT-BND-1", type_="BOUNDARY")
        raw = _make_raw_claude_output(block)
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_only_delimiter_lines_returns_empty(self):
        raw = "===SCENARIO===\n===SCENARIO===\n===SCENARIO===\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result == []

    def test_insurance_synthetic_data_in_test_data_field(self):
        """Synthetic data: scenario referencing insurance product names."""
        block = (
            "ID: UAT-INS-1\n"
            "TITLE: Verify Generations II policy lookup\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Insurance Agent\n"
            "PASS CRITERIA: Policy details displayed\n"
            "ESTIMATED TIME: 10\n"
            "TEST DATA: product_name=Generations II, doc_type=product_brochure\n"
        )
        raw = "===SCENARIO===\n" + block
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-INS-1"

    def test_large_number_of_scenarios_performance(self):
        """Boundary: 100 scenarios parsed without error."""
        blocks = [_make_scenario_block(id_=f"UAT-PERF-{i}") for i in range(100)]
        raw = _make_raw_claude_output(*blocks)
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 100

    def test_unicode_content_in_scenario(self):
        block = (
            "ID: UAT-UNI-1\n"
            "TITLE: 測試場景 — Тест сценарий\n"
            "TYPE: POSITIVE\n"
            "PERSONA: 用户\n"
            "PASS CRITERIA: 成功\n"
            "ESTIMATED TIME: 5\n"
        )
        raw = "===SCENARIO===\n" + block
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-UNI-1"
        assert "測試場景" in result[0]["title"]


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str):
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_header_row_is_correct(self):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_produces_header_only(self):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([]))
        assert len(rows) == 1  # header only

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-F1-1",
                "title": "Login test",
                "type": "POSITIVE",
                "persona": "End User",
                "pass_criteria": "Dashboard shown",
                "estimated_time": "5",
            }
        ]
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-F1-1"
        assert data_row[1] == "Login test"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "End User"
        assert data_row[4] == "Dashboard shown"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenario_rows(self):
        scenarios = [
            {"id": f"UAT-F1-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": str(i)}
            for i in range(1, 6)
        ]
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_fields_default_to_empty_string(self):
        scenarios = [{"id": "UAT-X-1"}]  # all other fields missing
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        data_row = rows[1]
        assert data_row[0] == "UAT-X-1"
        assert data_row[1] == ""
        assert data_row[2] == ""

    def test_special_characters_in_fields(self):
        scenarios = [
            {
                "id": "UAT-SC-1",
                "title": 'Title with "quotes" and, commas',
                "type": "NEGATIVE",
                "persona": "Admin, Super",
                "pass_criteria": "No crash",
                "estimated_time": "10",
            }
        ]
        csv_str = tool5_uat.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        # csv module should handle quoting
        assert rows[1][1] == 'Title with "quotes" and, commas'
        assert rows[1][3] == "Admin, Super"

    def test_insurance_product_scenario_in_csv(self):
        """Synthetic data: insurance product info as scenario content."""
        scenarios = [
            {
                "id": "UAT-INS-2",
                "title": "Verify designated hospital list lookup (Mainland China)",
                "type": "POSITIVE",
                "persona": "Policyholder",
                "pass_criteria": "Class 3 hospital list returned for Guangzhou",
                "estimated_time": "8",
            }
        ]
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(scenarios))
        assert rows[1