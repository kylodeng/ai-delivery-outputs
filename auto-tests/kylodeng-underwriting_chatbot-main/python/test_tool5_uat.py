"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, missing fields, no delimiter),
    boundary values (single scenario, many scenarios), negative cases (malformed blocks)
  - build_test_pack_csv(): happy path, empty list, partial fields, CSV structure/headers
  - build_test_pack_md(): happy path, version/owner/repo interpolation, content checks
  - get_results_csv(): happy path (base64 content), missing file (FileNotFoundError)
  - __main__ block is NOT directly tested here (requires full env + network)

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
  - unittest.mock.patch for `shared` module imports (call_claude, get_repo_files,
    write_output_file, send_email, email_html, write_audit_entry)
  - base64 encoding/decoding exercised inline (no mock needed)

TODOs:
  - TODO: Integration test for __main__ block requires live GitHub token + env vars
  - TODO: Test SYSTEM_GENERATE / SYSTEM_ANALYSE prompt constants for LLM contract compliance
  - TODO: Test build_test_pack_md timestamp format with frozen datetime
  - TODO: Test parse_scenarios against actual Claude output samples when available
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool5_uat.py succeeds
# without requiring real credentials or network access.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="stub")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now safe to import the module under test
import importlib

# Re-insert path so the script can find 'shared'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# We need to import from the actual script location
_script_path = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    ".github", "scripts", "tool5_uat.py",
)

# Load the module dynamically so path tricks inside it work
import importlib.util

spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
tool5 = importlib.util.module_from_spec(spec)
# Inject the stub before exec so the `from shared import …` inside the file resolves
sys.modules["tool5_uat"] = tool5
spec.loader.exec_module(tool5)

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_scenario_block(
    id_="UAT-S1-001",
    title="User can log in",
    type_="POSITIVE",
    persona="End User",
    pass_criteria="Login succeeds",
    estimated_time="5",
    extra_lines="",
) -> str:
    """Return a single raw scenario block (without the leading delimiter)."""
    return (
        f"ID: {id_}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PRE-CONDITIONS:\n- System is up\n"
        f"TEST DATA: user@example.com / P@ssw0rd\n"
        f"STEPS:\n1. Navigate to /login\n2. Enter credentials\n3. Click submit\n"
        f"EXPECTED RESULT: Dashboard is shown\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"NOTES: None\n"
        f"{extra_lines}"
    )


def _join_scenarios(*blocks: str) -> str:
    """Join scenario blocks with the delimiter used by the real Claude output."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarioHappyPath:

    def test_single_scenario_returns_one_item(self):
        raw = _join_scenarios(_make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        raw = _join_scenarios(_make_scenario_block(id_="UAT-S1-001"))
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-S1-001"

    def test_single_scenario_title_parsed(self):
        raw = _join_scenarios(_make_scenario_block(title="Risk Classification Check"))
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Risk Classification Check"

    def test_single_scenario_type_parsed(self):
        raw = _join_scenarios(_make_scenario_block(type_="NEGATIVE"))
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_single_scenario_persona_parsed(self):
        raw = _join_scenarios(_make_scenario_block(persona="Underwriter"))
        result = parse_scenarios(raw)
        assert result[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria_parsed(self):
        raw = _join_scenarios(_make_scenario_block(pass_criteria="Risk score displayed"))
        result = parse_scenarios(raw)
        assert result[0]["pass_criteria"] == "Risk score displayed"

    def test_single_scenario_estimated_time_parsed(self):
        raw = _join_scenarios(_make_scenario_block(estimated_time="10"))
        result = parse_scenarios(raw)
        assert result[0]["estimated_time"] == "10"

    def test_raw_field_populated(self):
        block = _make_scenario_block()
        raw = _join_scenarios(block)
        result = parse_scenarios(raw)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_multiple_scenarios_count(self):
        raw = _join_scenarios(
            _make_scenario_block(id_="UAT-S1-001"),
            _make_scenario_block(id_="UAT-S1-002"),
            _make_scenario_block(id_="UAT-S1-003"),
        )
        result = parse_scenarios(raw)
        assert len(result) == 3

    def test_multiple_scenarios_ids_unique(self):
        raw = _join_scenarios(
            _make_scenario_block(id_="UAT-S1-001"),
            _make_scenario_block(id_="UAT-S1-002"),
        )
        result = parse_scenarios(raw)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-S1-001", "UAT-S1-002"]

    def test_scenario_with_synthetic_test_data(self):
        """Verify parsing still succeeds when TEST DATA contains JSON-like model card data."""
        extra = 'TEST DATA: {"model_name": "Underwriting Risk Classification", "Age": 34.57}'
        raw = _join_scenarios(_make_scenario_block(extra_lines=extra))
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_boundary_type_parsed(self):
        raw = _join_scenarios(_make_scenario_block(type_="BOUNDARY"))
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"


# ===========================================================================
# parse_scenarios — edge / negative cases
# ===========================================================================

class TestParseScenarioEdgeCases:

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # No ===SCENARIO=== delimiter → no blocks with IDs
        raw = _make_scenario_block()
        result = parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_no_content_returns_empty_list(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_block_without_id_is_skipped(self):
        block_no_id = (
            "TITLE: Missing ID scenario\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Admin\n"
            "PASS CRITERIA: Should pass\n"
            "ESTIMATED TIME: 3\n"
        )
        raw = "===SCENARIO===\n" + block_no_id
        result = parse_scenarios(raw)
        assert result == []

    def test_block_with_id_but_missing_other_fields(self):
        raw = "===SCENARIO===\nID: UAT-S2-001\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-S2-001"
        # Optional fields should not be present (no KeyError)
        assert result[0].get("title") is None
        assert result[0].get("type") is None

    def test_whitespace_around_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-S3-001   \nTITLE:   Some Title   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-S3-001"
        assert result[0]["title"] == "Some Title"

    def test_mixed_valid_invalid_blocks(self):
        valid_block = _make_scenario_block(id_="UAT-S1-001")
        invalid_block = "TITLE: No ID here\nTYPE: POSITIVE\n"
        raw = "===SCENARIO===\n" + valid_block + "\n===SCENARIO===\n" + invalid_block
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-S1-001"

    def test_large_number_of_scenarios(self):
        blocks = [_make_scenario_block(id_=f"UAT-S1-{i:03d}") for i in range(50)]
        raw = _join_scenarios(*blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    def test_duplicate_id_blocks_both_included(self):
        """Parser does not deduplicate — both blocks appear."""
        raw = _join_scenarios(
            _make_scenario_block(id_="UAT-DUPE-001"),
            _make_scenario_block(id_="UAT-DUPE-001"),
        )
        result = parse_scenarios(raw)
        assert len(result) == 2

    def test_pass_criteria_line_with_colon_in_value(self):
        raw = "===SCENARIO===\nID: UAT-X-001\nPASS CRITERIA: Status code: 200\n"
        result = parse_scenarios(raw)
        # Only the text immediately after "PASS CRITERIA:" is captured
        assert "200" in result[0]["pass_criteria"]


# ===========================================================================
# build_test_pack_csv — happy path
# ===========================================================================

class TestBuildTestPackCsvHappyPath:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0][0] == "Scenario ID"
        assert rows[0][1] == "Title"

    def test_header_has_ten_columns(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows[0]) == 10

    def test_empty_scenarios_only_header(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # header only (trailing newline gives empty last row excluded by reader)

    def test_single_scenario_creates_data_row(self):
        scenarios = [{"id": "UAT-S1-001", "title": "Login", "type": "POSITIVE",
                      "persona": "Underwriter", "pass_criteria": "Logged in",
                      "estimated_time": "5"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 2  # header + 1 data row

    def test_data_row_id_correct(self):
        scenarios = [{"id": "UAT-S1-001", "title": "T", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "PC", "estimated_time": "5"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-S1-001"

    def test_data_row_title_correct(self):
        scenarios = [{"id": "UAT-S1-001", "title": "Risk Check", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "PC", "estimated_time": "3"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][1] == "Risk Check"

    def test_result_columns_empty_for_testers(self):
        scenarios = [{"id": "UAT-S1-001", "title": "T", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "PC", "estimated_time": "5"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        # Columns 6-9 are Result, Tester, Notes, Defect Ref — should be blank
        assert rows[1][6] == ""
        assert rows[1][7] == ""
        assert rows[1][8] == ""
        assert rows[1][9] == ""

    def test_multiple_scenarios_row_count(self):
        scenarios = [
            {"id": f"UAT-S1-{i:03d}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "2"}
            for i in range(5)
        ]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 6  # header + 5

    def test_scenario_with_comma_in_title_escaped_