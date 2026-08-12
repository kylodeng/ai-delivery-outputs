"""
Test module for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, malformed input, empty input,
    missing fields, multiple scenarios, no ID (skipped), boundary values
  - build_test_pack_csv(): correct headers, row values, empty scenario list,
    missing optional fields
  - build_test_pack_md(): correct markdown structure, version/owner/repo
    interpolation, raw content embedding
  - get_results_csv(): happy path (base64 content), missing file (FileNotFoundError),
    API error response shapes

Mocks used:
  - requests.get (patched via unittest.mock.patch) — no real HTTP calls
  - shared module imports (call_claude, get_repo_files, write_output_file,
    send_email, write_audit_entry) — patched at import level where needed
  - base64.b64decode — used directly (no mock needed; pure stdlib)

TODOs:
  - TODO: Integration tests for __main__ block require full env-var setup and
    live shared.py dependencies — stub tests added with pytest.mark.skip
  - TODO: Tests for Mode B (analyse) end-to-end path need call_claude mock
    returning valid SYSTEM_ANALYSE JSON — stub added
  - TODO: build_test_pack_md timestamp is non-deterministic; tests freeze time
    via unittest.mock.patch on datetime.datetime
"""

import base64
import csv
import io
import json
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so tool5_uat imports without error
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda x: x
_shared_stub.call_claude = MagicMock(return_value="")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now safe to import the module under test
import importlib, pathlib

# We import the public callables directly after injecting the stub
_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# Load the module from file so we get its real implementations
import importlib.util as _ilu

spec = _ilu.spec_from_file_location("tool5_uat", _script_path)
tool5 = _ilu.module_from_spec(spec)
# Inject shared stub before exec
tool5.__dict__["shared"] = _shared_stub
sys.modules["tool5_uat"] = tool5
spec.loader.exec_module(tool5)

parse_scenarios   = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md  = tool5.build_test_pack_md
get_results_csv     = tool5.get_results_csv


# ===========================================================================
# Helpers / Fixtures
# ===========================================================================

def _make_scenario_block(
    id_="UAT-STORY1-1",
    title="User can log in",
    type_="POSITIVE",
    persona="Admin",
    pass_criteria="Login succeeds",
    estimated_time="5",
    extra_lines="",
):
    return (
        f"===SCENARIO===\n"
        f"ID: {id_}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PRE-CONDITIONS:\n- System is running\n"
        f"TEST DATA: email=admin@example.com, password=P@ssw0rd!\n"
        f"STEPS:\n1. Navigate to /login\n2. Enter credentials\n3. Click Submit\n"
        f"EXPECTED RESULT: Dashboard shown\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"NOTES: none\n"
        f"{extra_lines}"
    )


SINGLE_SCENARIO_RAW = _make_scenario_block()

TWO_SCENARIO_RAW = (
    _make_scenario_block(id_="UAT-STORY1-1", title="Happy login", type_="POSITIVE")
    + "\n"
    + _make_scenario_block(id_="UAT-STORY1-2", title="Wrong password", type_="NEGATIVE")
)

BOUNDARY_SCENARIO_RAW = _make_scenario_block(
    id_="UAT-STORY1-3", type_="BOUNDARY", title="Max length username"
)


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_dict(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert len(result) == 1

    def test_single_scenario_id_extracted(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_extracted(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["title"] == "User can log in"

    def test_single_scenario_type_extracted(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_extracted(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["persona"] == "Admin"

    def test_single_scenario_pass_criteria_extracted(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["pass_criteria"] == "Login succeeds"

    def test_single_scenario_estimated_time_extracted(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_RAW)
        assert "raw" in result[0]
        assert "ID: UAT-STORY1-1" in result[0]["raw"]

    # --- Two scenarios ---

    def test_two_scenarios_returns_two_dicts(self):
        result = parse_scenarios(TWO_SCENARIO_RAW)
        assert len(result) == 2

    def test_two_scenarios_ids_distinct(self):
        result = parse_scenarios(TWO_SCENARIO_RAW)
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_two_scenarios_types_correct(self):
        result = parse_scenarios(TWO_SCENARIO_RAW)
        types = {s["id"]: s["type"] for s in result}
        assert types["UAT-STORY1-1"] == "POSITIVE"
        assert types["UAT-STORY1-2"] == "NEGATIVE"

    # --- Boundary scenario ---

    def test_boundary_type_parsed(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_RAW)
        assert result[0]["type"] == "BOUNDARY"

    # --- Edge cases ---

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_only_delimiter_returns_empty_list(self):
        assert parse_scenarios("===SCENARIO===") == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-1-1\nTITLE: something\n"
        # Without the delimiter the whole string is treated as a leading empty block
        result = parse_scenarios(raw)
        assert result == []

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_multiple_scenarios_only_valid_ones_returned(self):
        raw = (
            "===SCENARIO===\n"
            "TITLE: No ID here\n"
            "===SCENARIO===\n"
            "ID: UAT-X-1\nTITLE: Has ID\nTYPE: POSITIVE\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"

    def test_missing_optional_fields_are_absent_from_dict(self):
        raw = "===SCENARIO===\nID: UAT-2-1\nTITLE: Minimal\n"
        result = parse_scenarios(raw)
        assert result[0].get("type") is None
        assert result[0].get("persona") is None
        assert result[0].get("pass_criteria") is None
        assert result[0].get("estimated_time") is None

    def test_whitespace_only_block_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-3-1\nTITLE: X\n"
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_id_with_leading_trailing_spaces_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-4-1   \nTITLE: Spaced\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-4-1"

    def test_title_with_colon_in_value_parsed_correctly(self):
        raw = "===SCENARIO===\nID: UAT-5-1\nTITLE: Login: with colon\n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Login: with colon"

    def test_large_number_of_scenarios(self):
        blocks = "\n".join(
            _make_scenario_block(id_=f"UAT-BIG-{i}", title=f"Scenario {i}")
            for i in range(50)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 50

    def test_synthetic_underwriting_scenario(self):
        """Scenario using underwriting-domain synthetic data."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-UNDERWRITE-1\n"
            "TITLE: Risk classification returns CatBoostClassifier result\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: Age=34, Annual_Income=120000, Risk_Classification=Low\n"
            "PASS CRITERIA: API returns Risk_Classification=Low for given inputs\n"
            "ESTIMATED TIME: 10\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-UNDERWRITE-1"
        assert result[0]["persona"] == "Underwriter"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_empty_scenarios_has_header_only(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # header row only

    def test_header_columns_correct(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        expected = [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]
        assert rows[0] == expected

    def test_single_scenario_produces_two_rows(self):
        scenario = {
            "id": "UAT-1-1", "title": "Login", "type": "POSITIVE",
            "persona": "Admin", "pass_criteria": "Success", "estimated_time": "5"
        }
        rows = self._parse_csv(build_test_pack_csv([scenario]))
        assert len(rows) == 2

    def test_scenario_id_in_first_data_column(self):
        scenario = {"id": "UAT-1-1", "title": "T", "type": "POSITIVE",
                    "persona": "P", "pass_criteria": "PC", "estimated_time": "5"}
        rows = self._parse_csv(build_test_pack_csv([scenario]))
        assert rows[1][0] == "UAT-1-1"

    def test_scenario_title_in_second_column(self):
        scenario = {"id": "UAT-1-1", "title": "My Title", "type": "POSITIVE",
                    "persona": "P", "pass_criteria": "PC", "estimated_time": "5"}
        rows = self._parse_csv(build_test_pack_csv([scenario]))
        assert rows[1][1] == "My Title"

    def test_result_tester_notes_defect_empty(self):
        scenario = {"id": "UAT-1-1", "title": "T", "type": "POSITIVE",
                    "persona": "P", "pass_criteria": "PC", "estimated_time": "5"}
        rows = self._parse_csv(build_test_pack_csv([scenario]))
        # columns 6,7,8,9 are blank for testers to fill in
        assert rows[1][6] == ""
        assert rows[1][7] == ""
        assert rows[1][8] == ""
        assert rows[1][9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-{i}", "title": f"T{i}", "type": "POSITIVE",
             "persona": "P", "pass_criteria": "PC", "estimated_time": "3"}
            for i in range(10)
        ]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 11  # header + 10 data rows

    def test_missing_fields_default_to_empty_string(self):
        scenario = {"id": "UAT-1-1"}  # only id present
        rows = self._parse_csv(build_test_pack_csv([scenario]))
        assert rows[1][1] == ""   # title
        assert rows[1][2] == ""   # type
        assert rows[1][3] == ""   # persona
        assert rows[1][4] == ""   # pass_criteria
        assert rows[1][5] == ""   # estimated_time

    def test_csv_contains_no_trailing_newlines_issue(self):
        """csv module output should be parseable regardless of line endings."""
        result = build_test_pack_csv([])
        