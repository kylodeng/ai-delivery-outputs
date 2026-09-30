"""
Test module for .github/scripts/tool5_uat.py

WHAT IS TESTED:
- parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
- build_test_pack_csv(): correct headers, row content, empty scenarios, special characters
- build_test_pack_md(): correct markdown structure, version/owner/repo interpolation
- get_results_csv(): successful fetch, missing content key, network errors

MOCKS USED:
- requests.get (patched via unittest.mock.patch) — prevents real GitHub API calls
- shared module functions (call_claude, get_repo_files, write_output_file, send_email,
  write_audit_entry) — patched to avoid external side-effects
- base64.b64decode — used indirectly through get_results_csv; tested via mock response

TODOs:
- TODO: Integration test for __main__ block requires full env-var setup and live Claude key
- TODO: Test email HTML rendering (email_html) once template is available
- TODO: Verify SYSTEM_GENERATE / SYSTEM_ANALYSE prompt content against acceptance criteria
- TODO: Test write_output_file path logic when OUTPUT_REPO / OUTPUT_REPO_OWNER are set
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
# Stub out the `shared` module before importing tool5_uat so that
# no real network / filesystem calls happen at import time.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="")
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

# Now safe to import the module under test
import importlib, types as _types

# We need to import from the actual file path
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# If the file doesn't exist at that relative path (e.g. running from repo root),
# try an alternative common layout.
if not _script_path.exists():
    _script_path = pathlib.Path(__file__).parent / "tool5_uat.py"

# Load the module without executing __main__
spec = importlib.util.spec_from_file_location(
    "tool5_uat",
    _script_path,
    submodule_search_locations=[],
)
tool5_uat = importlib.util.module_from_spec(spec)

with patch.object(spec.loader, "exec_module", wraps=spec.loader.exec_module):
    # Prevent __main__ block from running during import
    with patch.dict(os.environ, {"GITHUB_RUN_URL": "https://github.com/run/1"}):
        try:
            spec.loader.exec_module(tool5_uat)
        except SystemExit:
            pass
        except Exception:
            pass

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Helpers / Fixtures
# ===========================================================================

def make_scenario_block(
    id_="UAT-STORY1-1",
    title="User can log in",
    type_="POSITIVE",
    persona="End User",
    pass_criteria="User reaches dashboard",
    estimated_time="5",
    extra_lines=None,
):
    lines = [
        f"ID: {id_}",
        f"TITLE: {title}",
        f"TYPE: {type_}",
        f"PERSONA: {persona}",
        "PRE-CONDITIONS:",
        "- System is running",
        f"TEST DATA: username=testuser, password=Passw0rd!",
        "STEPS:",
        "1. Navigate to login page",
        "2. Enter credentials",
        "3. Click Submit",
        f"EXPECTED RESULT: User is redirected to dashboard",
        f"PASS CRITERIA: {pass_criteria}",
        f"ESTIMATED TIME: {estimated_time}",
        "NOTES: None",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines)


def make_raw_output(*blocks):
    """Join scenario blocks with the ===SCENARIO=== delimiter."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


SAMPLE_SCENARIO_DICT = {
    "id": "UAT-STORY1-1",
    "title": "User can log in",
    "type": "POSITIVE",
    "persona": "End User",
    "pass_criteria": "User reaches dashboard",
    "estimated_time": "5",
    "raw": make_scenario_block(),
}


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_happy_path_single_scenario(self):
        raw = make_raw_output(make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "User can log in"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "End User"
        assert s["pass_criteria"] == "User reaches dashboard"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_happy_path_multiple_scenarios(self):
        block1 = make_scenario_block(id_="UAT-S1-1", title="Scenario One")
        block2 = make_scenario_block(id_="UAT-S1-2", title="Scenario Two", type_="NEGATIVE")
        block3 = make_scenario_block(id_="UAT-S1-3", title="Scenario Three", type_="BOUNDARY")
        raw = make_raw_output(block1, block2, block3)
        result = parse_scenarios(raw)
        assert len(result) == 3
        assert result[0]["id"] == "UAT-S1-1"
        assert result[1]["id"] == "UAT-S1-2"
        assert result[2]["id"] == "UAT-S1-3"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "This is some text without any scenario delimiter."
        result = parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_no_content(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\n   "
        result = parse_scenarios(raw)
        # Blocks are whitespace-only → stripped → empty → should be skipped
        assert result == []

    def test_scenario_missing_id_is_excluded(self):
        block = (
            "TITLE: No ID scenario\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Admin\n"
            "PASS CRITERIA: Something\n"
            "ESTIMATED TIME: 3"
        )
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result == []

    def test_scenario_missing_optional_fields_still_included(self):
        """A scenario with only ID should be included with missing keys absent."""
        block = "ID: UAT-MIN-1\n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert "title" not in result[0]
        assert "type" not in result[0]

    def test_raw_field_contains_original_block_text(self):
        block = make_scenario_block()
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert block.strip() in result[0]["raw"] or result[0]["raw"].strip() == block.strip()

    def test_whitespace_around_values_is_stripped(self):
        block = "ID:   UAT-WS-1   \nTITLE:   Whitespace test   \nTYPE:   NEGATIVE   \n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"
        assert result[0]["type"] == "NEGATIVE"

    def test_negative_scenario_type(self):
        block = make_scenario_block(type_="NEGATIVE", id_="UAT-NEG-1", title="Invalid login")
        raw = make_raw_output(block)
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_scenario_type(self):
        block = make_scenario_block(type_="BOUNDARY", id_="UAT-BND-1", title="Max input length")
        raw = make_raw_output(block)
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_special_characters_in_fields(self):
        block = (
            "ID: UAT-SPEC-1\n"
            "TITLE: User <Admin> can access /api/v1/risk?param=value&other=1\n"
            'PERSONA: "Finance Manager" (Underwriter)\n'
            "PASS CRITERIA: Response code 200 & JSON contains 'risk_score'\n"
            "ESTIMATED TIME: 10\n"
        )
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPEC-1"
        assert "/api/v1/risk" in result[0]["title"]

    def test_leading_text_before_first_delimiter_is_ignored(self):
        preamble = "Some introductory text that should be ignored.\n"
        block = make_scenario_block(id_="UAT-P-1")
        raw = preamble + "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-P-1"

    @pytest.mark.parametrize("scenario_count", [1, 5, 10, 25])
    def test_large_number_of_scenarios(self, scenario_count):
        blocks = [
            make_scenario_block(id_=f"UAT-BULK-{i}", title=f"Scenario {i}")
            for i in range(scenario_count)
        ]
        raw = make_raw_output(*blocks)
        result = parse_scenarios(raw)
        assert len(result) == scenario_count

    def test_synthetic_underwriting_scenario(self):
        """Use synthetic data from model card context."""
        block = (
            "ID: UAT-RISK-1\n"
            "TITLE: Underwriting Risk Classification for high-income customer\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: CustomerID=CUST00000001, Age=45, Annual_Income=120000, "
            "Risk_Classification=LOW\n"
            "PASS CRITERIA: System returns Risk_Classification=LOW within 2s\n"
            "ESTIMATED TIME: 8\n"
        )
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-RISK-1"
        assert result[0]["pass_criteria"] == "System returns Risk_Classification=LOW within 2s"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    EXPECTED_HEADERS = [
        "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
        "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
    ]

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_happy_path_returns_string(self):
        result = build_test_pack_csv([SAMPLE_SCENARIO_DICT])
        assert isinstance(result, str)

    def test_correct_headers(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == self.EXPECTED_HEADERS

    def test_empty_scenarios_only_header_row(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1
        assert rows[0] == self.EXPECTED_HEADERS

    def test_single_scenario_row_content(self):
        result = build_test_pack_csv([SAMPLE_SCENARIO_DICT])
        rows = self._parse_csv(result)
        assert len(rows) == 2  # header + 1 data row
        data = rows[1]
        assert data[0] == "UAT-STORY1-1"
        assert data[1] == "User can log in"
        assert data[2] == "POSITIVE"
        assert data[3] == "End User"
        assert data[4] == "User reaches dashboard"
        assert data[5] == "5"
        # Result, Tester, Notes, Defect Ref should be blank
        assert data[6] == ""
        assert data[7] == ""
        assert data[8] == ""
        assert data[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {**SAMPLE_SCENARIO_DICT, "id": f"UAT-S-{i}", "title": f"Scenario {i}"}
            for i in range(5)
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 6  # header + 5 data rows

    def test_missing_optional_keys_produce_empty_cells(self):
        minimal = {"id": "UAT-MIN-1", "raw": "ID: UAT-MIN-1"}
        result = build_test_pack_csv([minimal])
        rows = self._parse_csv(result)
        assert rows[1][0] == "UAT-MIN-1"
        # All other cells should be empty strings
        for cell in rows[1][1:]:
            assert cell == ""

    def test_special_characters_encoded_correctly(self):
        scenario = {
            **SAMPLE_SCENARIO_DICT,
            "id": "UAT-CSV-1",
            "title": 'Title with "