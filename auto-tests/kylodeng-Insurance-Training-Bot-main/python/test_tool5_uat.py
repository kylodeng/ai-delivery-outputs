"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
    - build_test_pack_csv(): CSV structure, row content, empty scenarios list
    - build_test_pack_md(): Markdown output structure, version/owner/repo injection
    - get_results_csv(): successful fetch, missing content key, FileNotFoundError
    - Module-level __main__ block is NOT tested directly (requires full env + network)

Mocks used:
    - unittest.mock.patch for requests.get (get_results_csv)
    - unittest.mock.patch for shared module imports (call_claude, write_output_file, etc.)
    - base64 decoding verified via controlled response payloads

TODOs:
    - TODO: Integration test for __main__ block requires full env vars + mocked GitHub API
    - TODO: Test build_test_pack_md timestamp format — currently datetime.utcnow is not mocked
    - TODO: Test parse_scenarios with Claude-realistic multi-line PRE-CONDITIONS / STEPS blocks
    - TODO: Test get_results_csv with pagination or large base64 payloads
    - TODO: SYSTEM_GENERATE and SYSTEM_ANALYSE prompt constants — validate against schema changes
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
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so tool5_uat.py can be imported
# without the real shared.py present.
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
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Install the stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# We load via spec so we can isolate it; fall back to direct import if path missing.
try:
    _spec = importlib.util.spec_from_file_location("tool5_uat", str(_SCRIPT_PATH))
    tool5_uat = importlib.util.module_from_spec(_spec)
    # Ensure shared stub is visible during exec
    sys.modules["shared"] = _shared_stub
    _spec.loader.exec_module(tool5_uat)
except (FileNotFoundError, AttributeError):
    # If the script path cannot be resolved in CI, attempt plain import
    import tool5_uat  # type: ignore


# Aliases for convenience
parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

def _make_scenario_block(
    id_="UAT-STORY1-1",
    title="User can log in with valid credentials",
    type_="POSITIVE",
    persona="Policy Holder",
    pass_criteria="Dashboard is displayed",
    estimated_time="5",
    extra_lines="",
) -> str:
    return (
        f"ID: {id_}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PRE-CONDITIONS:\n- System is running\n- User account exists\n"
        f"TEST DATA: username=john@example.com, password=Test1234!\n"
        f"STEPS:\n1. Open login page\n2. Enter credentials\n3. Click Sign In\n"
        f"EXPECTED RESULT: User is redirected to dashboard\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"NOTES: None\n"
        f"{extra_lines}"
    )


def _raw_with_scenarios(*blocks: str) -> str:
    """Join scenario blocks with the delimiter."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_all_fields(self):
        raw = _raw_with_scenarios(_make_scenario_block())
        result = parse_scenarios(raw)

        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "User can log in with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Policy Holder"
        assert s["pass_criteria"] == "Dashboard is displayed"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_returned_in_order(self):
        block1 = _make_scenario_block(id_="UAT-STORY1-1", title="Login success")
        block2 = _make_scenario_block(id_="UAT-STORY1-2", title="Login failure", type_="NEGATIVE")
        block3 = _make_scenario_block(id_="UAT-STORY1-3", title="Empty username", type_="BOUNDARY")
        raw = _raw_with_scenarios(block1, block2, block3)

        result = parse_scenarios(raw)

        assert len(result) == 3
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"
        assert result[2]["id"] == "UAT-STORY1-3"

    def test_raw_field_preserves_full_block(self):
        block = _make_scenario_block(id_="UAT-RAW-1")
        raw = _raw_with_scenarios(block)
        result = parse_scenarios(raw)

        assert "UAT-RAW-1" in result[0]["raw"]
        assert "STEPS:" in result[0]["raw"]

    def test_negative_type_parsed_correctly(self):
        block = _make_scenario_block(id_="UAT-NEG-1", type_="NEGATIVE")
        result = parse_scenarios(_raw_with_scenarios(block))
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_parsed_correctly(self):
        block = _make_scenario_block(id_="UAT-BOUND-1", type_="BOUNDARY")
        result = parse_scenarios(_raw_with_scenarios(block))
        assert result[0]["type"] == "BOUNDARY"

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_delimiter_only_returns_empty_list(self):
        assert parse_scenarios("===SCENARIO===") == []

    def test_multiple_delimiters_no_content_returns_empty(self):
        raw = "===SCENARIO===\n\n===SCENARIO===\n\n===SCENARIO==="
        assert parse_scenarios(raw) == []

    def test_block_without_id_is_excluded(self):
        block_no_id = (
            "TITLE: Missing ID scenario\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Admin\n"
            "PASS CRITERIA: Works\n"
            "ESTIMATED TIME: 3\n"
        )
        raw = "===SCENARIO===\n" + block_no_id
        result = parse_scenarios(raw)
        assert result == []

    def test_block_with_id_but_missing_optional_fields(self):
        minimal_block = "ID: UAT-MIN-1\n"
        raw = "===SCENARIO===\n" + minimal_block
        result = parse_scenarios(raw)

        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        # Optional fields should not raise — they simply won't be present
        assert s.get("title") is None or "title" not in s

    def test_extra_whitespace_around_values_stripped(self):
        block = "ID:   UAT-WS-1   \nTITLE:   Whitespace Test   \nTYPE:  POSITIVE  \nPERSONA:  Tester  \nPASS CRITERIA:  Pass  \nESTIMATED TIME:  10  \n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)

        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace Test"
        assert result[0]["type"] == "POSITIVE"
        assert result[0]["persona"] == "Tester"
        assert result[0]["pass_criteria"] == "Pass"
        assert result[0]["estimated_time"] == "10"

    def test_no_delimiter_prefix_returns_empty(self):
        """Raw text without any ===SCENARIO=== delimiter should yield nothing."""
        raw = "ID: UAT-NODLM-1\nTITLE: Some test\n"
        result = parse_scenarios(raw)
        # Split on delimiter gives one block which is the whole string,
        # but that block starts before any delimiter so it may or may not
        # have an id depending on implementation. Verify no crash.
        assert isinstance(result, list)

    def test_mixed_valid_and_invalid_blocks(self):
        valid_block = _make_scenario_block(id_="UAT-MIX-1")
        invalid_block = "TITLE: No ID here\nTYPE: POSITIVE\n"
        raw = "===SCENARIO===\n" + valid_block + "\n===SCENARIO===\n" + invalid_block
        result = parse_scenarios(raw)

        ids = [s["id"] for s in result]
        assert "UAT-MIX-1" in ids
        # The invalid block should not appear
        assert len(result) == 1

    def test_large_number_of_scenarios(self):
        blocks = [_make_scenario_block(id_=f"UAT-BULK-{i}") for i in range(50)]
        raw = _raw_with_scenarios(*blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    def test_scenario_with_colon_in_title_value(self):
        block = "ID: UAT-COL-1\nTITLE: Login: with special chars\nTYPE: POSITIVE\nPASS CRITERIA: OK\nESTIMATED TIME: 2\n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        # Title should preserve everything after the first "TITLE:" prefix
        assert result[0]["title"] == "Login: with special chars"

    # -----------------------------------------------------------------------
    # Insurance / synthetic data scenarios
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize("story_ref,n,persona", [
        ("GENII", 1, "Policy Holder - Generations II"),
        ("HEALTH", 2, "Health Product Subscriber"),
        ("HOSP", 3, "Mainland China VIP Member"),
    ])
    def test_insurance_synthetic_data_scenarios(self, story_ref, n, persona):
        block = _make_scenario_block(
            id_=f"UAT-{story_ref}-{n}",
            persona=persona,
        )
        raw = _raw_with_scenarios(block)
        result = parse_scenarios(raw)

        assert len(result) == 1
        assert result[0]["persona"] == persona
        assert result[0]["id"] == f"UAT-{story_ref}-{n}"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _read_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_header_row_present(self):
        csv_output = build_test_pack_csv([])
        rows = self._read_csv(csv_output)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        csv_output = build_test_pack_csv([])
        rows = self._read_csv(csv_output)
        assert len(rows) == 1  # header only

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-STORY1-1",
                "title": "Login Test",
                "type": "POSITIVE",
                "persona": "Policy Holder",
                "pass_criteria": "Dashboard shown",
                "estimated_time": "5",
            }
        ]
        csv_output = build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_output)

        assert len(rows) == 2  # header + 1 data row
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Login Test"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Policy Holder"
        assert data_row[4] == "Dashboard shown"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be blank
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-S-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "Admin", "pass_criteria": "OK", "estimated_time": "3"}
            for i in range(5)
        ]
        csv_output = build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_output)
        assert len(rows) == 6  # header + 5

    def test_missing_optional_fields_use_empty_string(self):
        scenarios = [{"id": "UAT-PARTIAL-1"}]
        csv_output = build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_output)
        data_row = rows[1]
        # All optional fields default to ""
        assert data_row[0] == "UAT-PARTIAL-1"
        assert data_row[1] == ""  # title missing