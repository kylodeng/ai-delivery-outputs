"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases (empty input, missing fields, no ID blocks)
    - build_test_pack_csv(): correct headers, row content, empty list, special characters
    - build_test_pack_md(): correct structure, version embedding, timestamp format
    - get_results_csv(): successful fetch, missing content key, HTTP error handling

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls)
    - unittest.mock.patch for `base64.b64decode`
    - sys.path manipulation to isolate shared module imports

TODOs:
    - TODO: Integration test for __main__ block requires live GitHub env vars and Claude API key
    - TODO: call_claude mock not tested end-to-end without a real SYSTEM_GENERATE prompt test
    - TODO: write_output_file, send_email, write_audit_entry require OUTPUT_REPO context
    - TODO: get_repo_files mock needed to test full generate/analyse pipeline
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: stub out the `shared` module so we don't need the real file
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib, types

# We need to import tool5_uat; because it lives in .github/scripts we add
# that directory to the path and import directly.
_script_dir = os.path.join(
    os.path.dirname(__file__), "..", ".github", "scripts"
)
sys.path.insert(0, os.path.abspath(_script_dir))

# Patch `requests` and `base64` at module level before first import
with patch.dict(sys.modules, {"requests": MagicMock(), "shared": shared_stub}):
    import importlib.util, pathlib

    _tool_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"
    if not _tool_path.exists():
        # Try relative to this test file
        _tool_path = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool5_uat.py"

# ---------------------------------------------------------------------------
# Re-import cleanly with the stub in place
# ---------------------------------------------------------------------------
sys.modules["shared"] = shared_stub
import requests as _requests_module  # will be the real requests unless mocked per-test

# Direct import
try:
    from tool5_uat import (
        parse_scenarios,
        build_test_pack_csv,
        build_test_pack_md,
        get_results_csv,
        SYSTEM_GENERATE,
        SYSTEM_ANALYSE,
    )
except ModuleNotFoundError:
    # Fallback: load via spec from explicit path
    import importlib.util

    _candidates = [
        pathlib.Path(__file__).parent / "tool5_uat.py",
        pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py",
    ]
    _spec = None
    for _c in _candidates:
        if _c.exists():
            _spec = importlib.util.spec_from_file_location("tool5_uat", str(_c))
            break
    if _spec is None:
        pytest.skip("tool5_uat.py not found; adjust path", allow_module_level=True)  # type: ignore[call-arg]
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    parse_scenarios = _mod.parse_scenarios
    build_test_pack_csv = _mod.build_test_pack_csv
    build_test_pack_md = _mod.build_test_pack_md
    get_results_csv = _mod.get_results_csv
    SYSTEM_GENERATE = _mod.SYSTEM_GENERATE
    SYSTEM_ANALYSE = _mod.SYSTEM_ANALYSE


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriting submission
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is logged in
- Application form is open
TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=Low
STEPS:
1. Navigate to the application form
2. Fill in all required fields
3. Click Submit
EXPECTED RESULT: Application is submitted and risk score displayed
PASS CRITERIA: Risk classification shown as Low within 5 seconds
ESTIMATED TIME: 10
NOTES: Depends on CatBoostClassifier model being deployed
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path submission
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Logged in
TEST DATA: Age=34
STEPS:
1. Submit form
EXPECTED RESULT: Success
PASS CRITERIA: Green confirmation banner shown
ESTIMATED TIME: 5
NOTES: None

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Missing mandatory field
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Form open
TEST DATA: Age=
STEPS:
1. Leave Age blank
2. Submit form
EXPECTED RESULT: Validation error displayed
PASS CRITERIA: Red error message shown
ESTIMATED TIME: 3
NOTES: Boundary check
"""

SCENARIO_NO_ID = """\
===SCENARIO===
TITLE: Orphan block
TYPE: POSITIVE
PERSONA: Guest
"""

SCENARIO_PARTIAL_FIELDS = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Only ID and title
"""


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_string_without_delimiter_returns_empty_list(self):
        result = parse_scenarios("Some random text without the delimiter")
        assert result == []

    def test_single_scenario_parsed_correctly(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful underwriting submission"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Risk classification shown as Low within 5 seconds"
        assert s["estimated_time"] == "10"

    def test_single_scenario_raw_field_populated(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_two_scenarios_parsed_correctly(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_two_scenarios_types(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_block_without_id_is_skipped(self):
        result = parse_scenarios(SCENARIO_NO_ID)
        assert result == []

    def test_partial_fields_scenario_still_parsed(self):
        result = parse_scenarios(SCENARIO_PARTIAL_FIELDS)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-1"
        assert result[0]["title"] == "Only ID and title"
        # Missing fields should not be present
        assert "type" not in result[0]
        assert "persona" not in result[0]

    def test_mixed_valid_and_invalid_blocks(self):
        mixed = TWO_SCENARIO_BLOCK + SCENARIO_NO_ID
        result = parse_scenarios(mixed)
        assert len(result) == 2

    def test_whitespace_only_input(self):
        result = parse_scenarios("   \n\t  ")
        assert result == []

    def test_delimiter_only(self):
        result = parse_scenarios("===SCENARIO===")
        assert result == []

    def test_multiple_delimiters_no_content(self):
        result = parse_scenarios("===SCENARIO===\n===SCENARIO===\n===SCENARIO===")
        assert result == []

    def test_estimated_time_as_string(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert isinstance(result[0]["estimated_time"], str)
        assert result[0]["estimated_time"] == "10"

    def test_leading_delimiter_is_handled(self):
        """Output may start with ===SCENARIO=== producing an empty first block."""
        block = "===SCENARIO===\n" + SINGLE_SCENARIO_BLOCK.lstrip("===SCENARIO===\n")
        result = parse_scenarios(block)
        # Should still find at least one valid scenario
        assert any(s.get("id") for s in result)

    def test_scenario_with_synthetic_data_fields(self):
        synthetic_block = """\
===SCENARIO===
ID: UAT-RISK-1
TITLE: Risk classification for known customer
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Customer CUST00000001 exists
TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=Low, CustomerID=CUST00000001
STEPS:
1. Load customer CUST00000001
2. Run risk classification
EXPECTED RESULT: Returns Risk_Classification=Low
PASS CRITERIA: Risk_Classification field equals Low
ESTIMATED TIME: 5
NOTES: Uses model_card.json feature Age with importance 34.57
"""
        result = parse_scenarios(synthetic_block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-RISK-1"
        assert result[0]["persona"] == "Underwriter"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_empty_list_produces_header_only(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # header row only

    def test_header_columns_correct(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        header = rows[0]
        assert header[0] == "Scenario ID"
        assert header[1] == "Title"
        assert header[2] == "Type"
        assert header[3] == "Persona"
        assert header[4] == "Pass Criteria"
        assert header[5] == "Est. Time (min)"
        assert header[6] == "Result (PASS/FAIL/BLOCKED)"
        assert header[7] == "Tester"
        assert header[8] == "Notes"
        assert header[9] == "Defect Ref"

    def test_single_scenario_row_count(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 2  # header + 1 data row

    def test_single_scenario_data_in_row(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Successful underwriting submission"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Underwriter"

    def test_two_scenarios_row_count(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 3  # header + 2 data rows

    def test_result_tester_defect_columns_empty_by_default(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        # Columns 6,7,8,9 should be blank (to be filled by testers)
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_missing_optional_fields_produce_empty_strings(self):
        scenarios = [{"id": "UAT-MIN-1"}]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-1"
        assert data_row[1] == ""
        assert data_row[2] == ""

    def test_special_characters_in_title(self):
        scenarios = [{"id": "UAT-SC-1", "title": 'Title with "quotes" and, commas'}]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_unicode_in_persona(self):
        # Simulating potential Arabic locale test persona
        scenarios = [{"id": "UAT-AR-1", "persona": "\u0645\u0633\u062a\u062e\u062f\u0645"}]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][3] == "\u0645\u0633\u062a\u062e\u062f\u0645"

    def test_many_scenarios(self):
        scenarios = [
            {"id": f"UAT-BULK-{i}", "title": f"Scenario {i}", "type": "POSITIVE"}
            for i in range(50)
        ]
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(rows) == 51  # header + 50


# ===========================================================================
# build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd:
    """Tests for build_test_pack_md()."""

    def test_returns_string(self):
        result = build_test_pack_md("raw content", "owner", "repo", "1.0.0")
        assert isinstance(result, str)

    def test_contains_owner_repo_version(self):
        result = build_test_pack_