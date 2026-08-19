"""
Test module for tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, malformed blocks
  - build_test_pack_csv(): header row, data rows, empty list, special characters
  - build_test_pack_md(): structure/content, version substitution, timestamp presence
  - get_results_csv(): successful fetch + base64 decode, missing file error, malformed response

Mocks used:
  - requests.get (for get_results_csv GitHub API calls)
  - shared module functions (call_claude, get_repo_files, write_output_file,
    send_email, email_html, write_audit_entry) are imported but not directly
    exercised here — stubs are provided where needed
  - base64 (real module used; encoding/decoding in-process)

TODOs:
  - TODO: Integration tests for __main__ block require environment variables and
    live GitHub credentials — stub tests provided with skip markers
  - TODO: test_build_test_pack_md_timestamp_accuracy requires time-mocking to
    assert exact UTC string
  - TODO: Tests for call_claude interaction inside __main__ require a running
    Claude/Anthropic client mock at the shared module level
"""

import base64
import csv
import io
import json
import sys
import os
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without the full shared module
# being present by stubbing out 'shared' before importing the module under test
# ---------------------------------------------------------------------------

# Build a minimal fake 'shared' module so the import at the top of tool5_uat
# doesn't fail in a test environment that lacks the real shared.py dependencies.
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda s: s
_shared_stub.call_claude = MagicMock(return_value="mocked claude response")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html></html>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib

# Re-insert the scripts directory so relative imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# We must patch 'requests' before the module is imported if it hasn't been yet
with patch.dict(sys.modules, {"requests": MagicMock()}):
    if "tool5_uat" in sys.modules:
        tool5_uat = sys.modules["tool5_uat"]
    else:
        import importlib.util, pathlib

        _script_path = pathlib.Path(__file__).parent / "tool5_uat.py"
        if _script_path.exists():
            spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
            tool5_uat = importlib.util.module_from_spec(spec)
            sys.modules["tool5_uat"] = tool5_uat
            spec.loader.exec_module(tool5_uat)
        else:
            # Fallback: assume the file is importable normally
            import tool5_uat  # type: ignore

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User account exists
TEST DATA: username=john.doe@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click login
EXPECTED RESULT: User is authenticated and redirected to dashboard
PASS CRITERIA: Dashboard visible within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires active LDAP connection
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login fails with invalid password
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User account exists
TEST DATA: username=john.doe@example.com, password=WrongPass
STEPS:
1. Navigate to login page
2. Enter invalid credentials
3. Click login
EXPECTED RESULT: Error message displayed
PASS CRITERIA: "Invalid credentials" message shown; no dashboard access
ESTIMATED TIME: 3
NOTES: None
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Login with maximum-length password
TYPE: BOUNDARY
PERSONA: Underwriter
PRE-CONDITIONS:
- User account exists with 128-char password
TEST DATA: username=john.doe@example.com, password={"A"*128}
STEPS:
1. Navigate to login page
2. Enter 128-char password
3. Click login
EXPECTED RESULT: Login succeeds
PASS CRITERIA: Dashboard visible
ESTIMATED TIME: 5
NOTES: Edge case for password length validation
"""

MULTI_SCENARIO_RAW = "\n".join(
    [MINIMAL_SCENARIO_BLOCK, NEGATIVE_SCENARIO_BLOCK, BOUNDARY_SCENARIO_BLOCK]
)


def _make_scenario(
    id_="UAT-S1-1",
    title="Test Title",
    type_="POSITIVE",
    persona="Underwriter",
    pass_criteria="System behaves correctly",
    estimated_time="5",
):
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": "raw block content",
    }


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================


class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_valid_scenario(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Dashboard visible within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_raw_field_preserved(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_multiple_scenarios_parsed(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_scenario_types_preserved(self):
        result = parse_scenarios(MULTI_SCENARIO_RAW)
        types = {s["id"]: s["type"] for s in result}
        assert types["UAT-STORY1-1"] == "POSITIVE"
        assert types["UAT-STORY1-2"] == "NEGATIVE"
        assert types["UAT-STORY1-3"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_scenario_delimiter_returns_empty_list(self):
        result = parse_scenarios("This is just a plain block of text with no delimiter.")
        assert result == []

    def test_delimiter_only_no_id_excluded(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        # Block has no ID so it should not be appended
        assert result == []

    def test_partial_fields_scenario(self):
        """Scenario with only ID and TITLE — other fields should be absent or empty."""
        raw = "===SCENARIO===\nID: UAT-X-1\nTITLE: Partial scenario\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-X-1"
        assert s["title"] == "Partial scenario"
        assert "type" not in s
        assert "persona" not in s
        assert "pass_criteria" not in s

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-Y-1\nTITLE: Real scenario\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-Y-1"

    def test_leading_delimiter_prefix_text_ignored(self):
        """Any text before the first ===SCENARIO=== should be ignored."""
        raw = "Some preamble text\n" + MINIMAL_SCENARIO_BLOCK
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_id_with_extra_spaces_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-Z-99   \nTITLE: Spaced ID\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-Z-99"

    def test_title_with_extra_spaces_stripped(self):
        raw = "===SCENARIO===\nID: UAT-Z-1\nTITLE:   Spaced title   \n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Spaced title"

    @pytest.mark.parametrize(
        "raw_block,expected_id",
        [
            (
                "===SCENARIO===\nID: UAT-RISK-1\nTITLE: Risk classification\nTYPE: POSITIVE\n",
                "UAT-RISK-1",
            ),
            (
                "===SCENARIO===\nID: UAT-INCOME-2\nTITLE: Income assessment\nTYPE: NEGATIVE\n",
                "UAT-INCOME-2",
            ),
            (
                "===SCENARIO===\nID: UAT-CUST-3\nTITLE: Customer similarity\nTYPE: BOUNDARY\n",
                "UAT-CUST-3",
            ),
        ],
    )
    def test_parametrized_scenario_ids(self, raw_block, expected_id):
        result = parse_scenarios(raw_block)
        assert len(result) == 1
        assert result[0]["id"] == expected_id

    def test_many_scenarios_count(self):
        blocks = "\n".join(
            [
                f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
                for i in range(20)
            ]
        )
        result = parse_scenarios(blocks)
        assert len(result) == 20

    def test_pass_criteria_colon_in_value(self):
        """PASS CRITERIA value contains colons — should not truncate."""
        raw = (
            "===SCENARIO===\nID: UAT-PC-1\nTITLE: Colon test\n"
            "PASS CRITERIA: HTTP 200: response body contains 'ok'\n"
        )
        result = parse_scenarios(raw)
        assert "HTTP 200: response body contains 'ok'" == result[0]["pass_criteria"]

    def test_estimated_time_numeric_string(self):
        raw = "===SCENARIO===\nID: UAT-T-1\nTITLE: Time test\nESTIMATED TIME: 15\n"
        result = parse_scenarios(raw)
        assert result[0]["estimated_time"] == "15"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_header_row_correct(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID",
            "Title",
            "Type",
            "Persona",
            "Pass Criteria",
            "Est. Time (min)",
            "Result (PASS/FAIL/BLOCKED)",
            "Tester",
            "Notes",
            "Defect Ref",
        ]

    def test_empty_scenarios_only_header(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # header only (trailing newline produces empty last row in some parsers)

    def test_single_scenario_row(self):
        scenario = _make_scenario()
        result = build_test_pack_csv([scenario])
        rows = self._parse_csv(result)
        # rows[0] = header, rows[1] = data
        assert len([r for r in rows if any(r)]) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-S1-1"
        assert data_row[1] == "Test Title"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Underwriter"
        assert data_row[4] == "System behaves correctly"
        assert data_row[5] == "5"

    def test_result_tester_notes_defect_empty(self):
        scenario = _make_scenario()
        result = build_test_pack_csv([scenario])
        rows = self._parse_csv(result)
        data_row = rows[1]
        assert data_row[6] == ""  # Result
        assert data_row[7] == ""  # Tester
        assert data_row[8] == ""  # Notes
        assert data_row[9] == ""  # Defect Ref

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(id_=f"UAT-S1-{i}") for i in range(5)]
        result = build_test_pack_csv(scenarios)
        rows = [r for r in self._parse_csv(result) if any(r)]
        assert len(rows) == 6  # 1 header + 5 data

    def test_scenario_ids_in_order(self):
        ids = ["UAT-S1-1", "UAT-S1-2", "UAT-S1-3"]
        scenarios = [_make_