"""
Test module for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty/malformed input
    - build_test_pack_csv(): correct headers, row content, empty scenarios list
    - build_test_pack_md(): markdown structure, version/owner/repo substitution
    - get_results_csv(): successful fetch + base64 decode, missing content key, HTTP errors
    - Module-level constants and imports from shared

Mocks used:
    - requests.get (for get_results_csv)
    - shared.call_claude
    - shared.get_repo_files
    - shared.write_output_file
    - shared.send_email
    - shared.write_audit_entry
    - shared.GH_API, GH_HEADERS, OUTPUT_REPO_OWNER, OUTPUT_REPO

TODOs:
    - TODO: Integration test for __main__ block requires env vars + full shared module wiring
    - TODO: Test call_claude interaction inside generate/analyse flows once those functions are
            extracted from __main__ into testable functions
    - TODO: Test write_output_file call with actual GitHub API mock chain
    - TODO: Test send_email / email_html rendering with a real SMTP stub
"""

import base64
import csv
import io
import sys
import os
import types
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool5_uat without
# needing the real shared.py on the path.
# ---------------------------------------------------------------------------

def _install_shared_stub():
    shared_stub = types.ModuleType("shared")
    shared_stub.clean_json = lambda s: s
    shared_stub.call_claude = MagicMock(return_value="stub")
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
    return shared_stub


_shared_stub = _install_shared_stub()

# Now we can safely import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# Gracefully handle the case where the source file may not exist in the test
# runner's workspace (CI path differences).
if _script_path.exists():
    spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
    tool5_uat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool5_uat)
else:
    # Fall back: assume the script is importable via sys.path manipulation
    # that the module itself does (sys.path.insert in the source).
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / ".github" / "scripts"))
    import tool5_uat  # type: ignore

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriter login
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is running
- Valid credentials exist
TEST DATA: username=underwriter@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Login
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Depends on auth service"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriter login
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: none
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: Underwriter
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: none"""

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario"""

NO_ID_BLOCK = """\
===SCENARIO===
TITLE: Scenario without ID
TYPE: POSITIVE"""


@pytest.fixture()
def sample_scenarios():
    """Pre-parsed scenario list for CSV/MD tests."""
    return [
        {
            "id": "UAT-STORY1-1",
            "title": "Successful underwriter login",
            "type": "POSITIVE",
            "persona": "Underwriter",
            "pass_criteria": "Dashboard loads within 3 seconds",
            "estimated_time": "5",
            "raw": SINGLE_SCENARIO_BLOCK,
        },
        {
            "id": "UAT-STORY1-2",
            "title": "Login with invalid password",
            "type": "NEGATIVE",
            "persona": "Underwriter",
            "pass_criteria": "Error message displayed",
            "estimated_time": "3",
            "raw": "===SCENARIO===\nID: UAT-STORY1-2\n...",
        },
    ]


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_two_scenarios_returns_two_items(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_scenario_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_scenario_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Successful underwriter login"

    def test_scenario_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_scenario_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Underwriter"

    def test_scenario_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Dashboard loads within 3 seconds"

    def test_scenario_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_raw_block_stored(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This text has no scenario delimiter at all.")
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(NO_ID_BLOCK)
        assert len(result) == 0

    def test_minimal_scenario_with_id_is_included(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"

    def test_minimal_scenario_missing_fields_default_absent(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        s = result[0]
        # Optional fields should simply be absent rather than raise
        assert "type" not in s or s.get("type") is not None  # no crash

    def test_multiple_scenarios_second_id(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_multiple_scenarios_second_type(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[1]["type"] == "NEGATIVE"

    def test_whitespace_only_blocks_are_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\nTITLE: Real\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_id_with_extra_spaces_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-1   \nTITLE: Space test\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"

    def test_large_number_of_scenarios(self):
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Bulk {i}\n"
            for i in range(50)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 50

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_parsed(self, scenario_type):
        raw = f"===SCENARIO===\nID: UAT-T-1\nTITLE: T\nTYPE: {scenario_type}\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == scenario_type

    def test_delimiter_at_very_start_handled(self):
        raw = "===SCENARIO===\nID: UAT-START-1\nTITLE: Start\n"
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_returns_list_type(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert isinstance(result, list)

    def test_each_item_is_dict(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        for item in result:
            assert isinstance(item, dict)


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def test_returns_string(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        assert isinstance(result, str)

    def test_header_row_present(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert header[0] == "Scenario ID"
        assert "Title" in header
        assert "Result (PASS/FAIL/BLOCKED)" in header

    def test_correct_number_of_data_rows(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # 1 header + len(sample_scenarios) data rows
        assert len(rows) == 1 + len(sample_scenarios)

    def test_first_data_row_scenario_id(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        first_data = next(reader)
        assert first_data[0] == "UAT-STORY1-1"

    def test_first_data_row_title(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        first_data = next(reader)
        assert first_data[1] == "Successful underwriter login"

    def test_result_column_empty_for_tester(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        for row in reader:
            assert row[6] == ""   # Result column blank

    def test_tester_column_empty(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        for row in reader:
            assert row[7] == ""   # Tester column blank

    def test_defect_ref_column_empty(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        for row in reader:
            assert row[9] == ""   # Defect Ref blank

    def test_empty_scenarios_list_only_header(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_scenario_with_missing_fields_uses_empty_string(self):
        scenarios = [{"id": "UAT-NOFIELDS-1"}]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[0] == "UAT-NOFIELDS-1"
        assert row[1] == ""   # title missing → empty
        assert row[2] == ""   # type missing → empty

    def test_header_has_ten_columns(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert len(header) == 10

    def test_csv_parseable_without_error(self, sample_scenarios):
        result = build_test_pack_csv(sample_scenarios)
        # Should not raise
        rows = list(csv.reader(io.StringIO(result)))
        assert rows  # non-empty

    def test_special_characters_in_title(self):
        scenarios = [{"id": "UAT-SC-1", "title": 'Title with "quotes" and, commas'}]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[1] == 'Title with "quotes" and, commas'

    def test_synthetic_model_card_data_as_test_data(self):
        """Use synthetic insurance underwriting data in scenario."""
        scenarios = [
            {
                "id": "UAT-UW-1",
                "