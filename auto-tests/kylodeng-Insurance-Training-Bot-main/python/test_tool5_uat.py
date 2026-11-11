"""
Tests for .github/scripts/tool5_uat.py

What is tested:
- parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
- build_test_pack_csv(): CSV structure, row content, empty scenarios, special characters
- build_test_pack_md(): markdown structure, version/owner/repo substitution
- get_results_csv(): happy path (mocked GitHub API), file-not-found error
- Module-level constants and imports are exercised indirectly

Mocks used:
- unittest.mock.patch for requests.get (GitHub API calls in get_results_csv)
- unittest.mock.patch for shared module functions (call_claude, get_repo_files,
  write_output_file, send_email, email_html, write_audit_entry)
- base64 encoding/decoding handled inline in test helpers

TODOs:
- TODO: Integration test for __main__ block requires full env setup + all shared deps
- TODO: Tests for SYSTEM_GENERATE / SYSTEM_ANALYSE prompt constants (content validation)
  need agreement on required prompt sections with product team
- TODO: End-to-end Mode A (generate) test requires a live or recorded Claude response
- TODO: End-to-end Mode B (analyse) test requires a completed CSV fixture + Claude response
- TODO: Verify exact CSV column order against tester tooling/downstream consumers
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — mirrors what tool5_uat.py does so shared imports resolve
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# Stub out the `shared` module before importing tool5_uat so we never touch
# real network/filesystem/secrets code.
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules["shared"] = shared_stub

# Now safe to import the module under test
import importlib
import types

# We load the source directly so we are not relying on __init__ side-effects
_script_path = os.path.join(
    os.path.dirname(__file__), "..", "..", ".github", "scripts", "tool5_uat.py"
)

# If the script doesn't exist at that relative path (CI layout differs),
# try a sibling path used in the repo
if not os.path.exists(_script_path):
    _script_path = os.path.join(
        os.path.dirname(__file__), "tool5_uat.py"
    )

# Load as a module regardless
_spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
tool5_uat = importlib.util.module_from_spec(_spec)
# Inject the stub before exec so internal `from shared import …` works
tool5_uat.__dict__["shared"] = shared_stub
sys.modules["tool5_uat"] = tool5_uat
_spec.loader.exec_module(tool5_uat)

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

def make_scenario_block(
    id_="UAT-S1-1",
    title="User can log in",
    type_="POSITIVE",
    persona="Admin",
    pass_criteria="Login succeeds and dashboard is shown",
    estimated_time="5",
    extra_lines=None,
) -> str:
    """Return a single raw scenario block (without the delimiter)."""
    lines = [
        f"ID: {id_}",
        f"TITLE: {title}",
        f"TYPE: {type_}",
        f"PERSONA: {persona}",
        "PRE-CONDITIONS:",
        "- System is online",
        "TEST DATA: username=tester@example.com password=P@ssw0rd!",
        "STEPS:",
        "1. Navigate to /login",
        "2. Enter credentials",
        "3. Click Submit",
        f"EXPECTED RESULT: Dashboard shown",
        f"PASS CRITERIA: {pass_criteria}",
        f"ESTIMATED TIME: {estimated_time}",
        "NOTES: none",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines)


def make_raw_output(*blocks: str) -> str:
    """Join scenario blocks with the ===SCENARIO=== delimiter."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


SAMPLE_SCENARIO_DICT = {
    "id": "UAT-S1-1",
    "title": "User can log in",
    "type": "POSITIVE",
    "persona": "Admin",
    "pass_criteria": "Login succeeds and dashboard is shown",
    "estimated_time": "5",
    "raw": make_scenario_block(),
}


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_all_fields(self):
        raw = make_raw_output(make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-S1-1"
        assert s["title"] == "User can log in"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Admin"
        assert s["pass_criteria"] == "Login succeeds and dashboard is shown"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_returned(self):
        block1 = make_scenario_block(id_="UAT-S1-1", title="Positive login")
        block2 = make_scenario_block(id_="UAT-S1-2", title="Invalid password", type_="NEGATIVE")
        block3 = make_scenario_block(id_="UAT-S1-3", title="Empty username", type_="BOUNDARY")
        raw = make_raw_output(block1, block2, block3)
        result = parse_scenarios(raw)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-S1-1" in ids
        assert "UAT-S1-2" in ids
        assert "UAT-S1-3" in ids

    def test_scenario_types_preserved(self):
        raw = make_raw_output(
            make_scenario_block(id_="UAT-X-1", type_="POSITIVE"),
            make_scenario_block(id_="UAT-X-2", type_="NEGATIVE"),
            make_scenario_block(id_="UAT-X-3", type_="BOUNDARY"),
        )
        result = parse_scenarios(raw)
        types = {s["id"]: s["type"] for s in result}
        assert types["UAT-X-1"] == "POSITIVE"
        assert types["UAT-X-2"] == "NEGATIVE"
        assert types["UAT-X-3"] == "BOUNDARY"

    def test_raw_field_contains_original_block(self):
        block = make_scenario_block()
        raw = make_raw_output(block)
        result = parse_scenarios(raw)
        assert block.strip() in result[0]["raw"]

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        """Block without ===SCENARIO=== delimiter is not parsed."""
        raw = make_scenario_block()  # no delimiter
        result = parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===")
        assert result == []

    def test_block_without_id_is_skipped(self):
        block = (
            "TITLE: Orphan scenario\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Admin\n"
            "PASS CRITERIA: Something works\n"
            "ESTIMATED TIME: 3\n"
        )
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result == []

    def test_partial_fields_still_parsed(self):
        """A block with an ID but missing optional fields is still returned."""
        block = "ID: UAT-PARTIAL-1\nTITLE: Partial scenario\n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Partial scenario"
        # Optional fields absent → key missing or empty
        assert s.get("type", "") == ""
        assert s.get("persona", "") == ""

    def test_whitespace_stripped_from_field_values(self):
        block = "ID:   UAT-SPACE-1   \nTITLE:   Spacey title   \n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"
        assert result[0]["title"] == "Spacey title"

    def test_leading_and_trailing_delimiter_whitespace(self):
        """Extra whitespace around blocks should not cause crashes."""
        raw = "\n\n===SCENARIO===\n\n" + make_scenario_block() + "\n\n"
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_many_scenarios_performance(self):
        """100 scenarios should parse without error."""
        blocks = [make_scenario_block(id_=f"UAT-PERF-{i}") for i in range(100)]
        raw = make_raw_output(*blocks)
        result = parse_scenarios(raw)
        assert len(result) == 100

    def test_scenario_with_insurance_synthetic_data(self):
        """Uses synthetic insurance data as test-data values."""
        block = make_scenario_block(
            id_="UAT-INS-1",
            title="Submit claim for Generations II plan",
            persona="Policyholder",
            pass_criteria="Claim submitted and reference number displayed",
        )
        raw = make_raw_output(block)
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-INS-1"
        assert result[0]["persona"] == "Policyholder"

    def test_duplicate_field_lines_last_wins(self):
        """If a field appears twice, the last assignment wins (dict overwrite)."""
        block = (
            "ID: UAT-DUP-1\n"
            "TITLE: First title\n"
            "TITLE: Second title\n"
            "PASS CRITERIA: ok\n"
            "ESTIMATED TIME: 2\n"
        )
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        # Second TITLE line overwrites the first
        assert result[0]["title"] == "Second title"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _read_csv(self, csv_string: str):
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_header_row_correct(self):
        rows = self._read_csv(build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_empty_scenarios_only_header(self):
        rows = self._read_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # only header

    def test_single_scenario_one_data_row(self):
        rows = self._read_csv(build_test_pack_csv([SAMPLE_SCENARIO_DICT]))
        assert len(rows) == 2
        data = rows[1]
        assert data[0] == "UAT-S1-1"
        assert data[1] == "User can log in"
        assert data[2] == "POSITIVE"
        assert data[3] == "Admin"
        assert data[4] == "Login succeeds and dashboard is shown"
        assert data[5] == "5"

    def test_result_tester_notes_defect_columns_empty(self):
        rows = self._read_csv(build_test_pack_csv([SAMPLE_SCENARIO_DICT]))
        data = rows[1]
        # columns 6-9 are tester-filled; must be blank in generated sheet
        assert data[6] == ""
        assert data[7] == ""
        assert data[8] == ""
        assert data[9] == ""

    def test_multiple_scenarios_multiple_rows(self):
        scenarios = [
            {**SAMPLE_SCENARIO_DICT, "id": f"UAT-S1-{i}", "title": f"Scenario {i}"}
            for i in range(5)
        ]
        rows = self._read_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 6  # header + 5 data rows

    def test_missing_optional_fields_produce_empty_cells(self):
        minimal = {"id": "UAT-MIN-1", "raw": "ID: UAT-MIN-1"}
        rows = self._read_csv(build_test_pack_csv([minimal]))
        data = rows[1]
        assert data[0] == "UAT-MIN-1"
        # title, type, persona, pass_criteria, estimated_time all empty
        assert data[1] == ""
        assert data[2] == ""

    def test_special_characters_handled(self):
        """Commas, quotes, newlines in fields must be properly escaped."""
        s = {
            **SAMPLE_SCENARIO_DICT,
            "title": 'Field with "quotes", commas',
            "pass_criteria": "Line1\nLine2",
        }
        csv_string = build_test_pack_csv([s])
        rows = self._read_csv(csv_string)
        assert rows[1][1] == 'Field with "quotes", commas'

    def test_insurance_product_scenario_in_csv(self):
        """Synthetic insurance data: Generations II plan scenario."""
        s = {
            "id": "UAT-GEN2-1",
            "title": "Verify Generations II lifelong protection benefit",
            "type": "POSITIVE",
            "persona": "Policyholder",
            "pass_criteria": "Benefit summary displays guaranteed lifelong protection",
            "estimated_time": "10",
            "raw": "",
        }
        rows = self._read_csv(build_test_pack_csv([s]))
        assert rows[1][0] == "UAT-GEN2-1"
        assert rows[1][4] == "Benefit summary displays guaranteed lifelong protection"

    def test_returns_string(self):
        result = build_test_pack_csv([SAMPLE_SCENARIO_DICT])
        assert isinstance(result, str)

    def test_csv_ends_with_newline(self):
        """csv.writer always terminates the last row; string must not be