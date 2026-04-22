"""
Test module for tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
    - build_test_pack_csv(): correct headers, row content, empty scenarios list
    - build_test_pack_md(): correct markdown structure, version/owner/repo injection
    - get_results_csv(): successful fetch and decode, missing content key, HTTP errors

Mocks used:
    - requests.get (for get_results_csv)
    - shared.call_claude (not directly tested here but referenced in __main__)
    - base64.b64decode (via content provided in mock response)

TODOs:
    - TODO: Integration tests for __main__ block require full env var setup and GitHub API access
    - TODO: Tests for send_email/write_output_file/write_audit_entry require shared module mocks
    - TODO: call_claude response integration tests require Anthropic API key or deeper mock setup
    - TODO: build_test_pack_md timestamp is dynamic — consider freezing time with freezegun
"""

import base64
import csv
import io
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup – mirror what tool5_uat.py does so imports resolve correctly
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

# We need to stub out the `shared` module before importing tool5_uat so that
# the top-level `from shared import …` does not fail in CI environments that
# lack the real module.
_shared_stub = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib
import types

# Build a minimal module from the source so we can import only the functions
# we want to test without executing the __main__ block.
_tool_path = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
)

# Fall back to reading relative to this test file's directory
if not os.path.exists(_tool_path):
    _tool_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        ".github",
        "scripts",
        "tool5_uat.py",
    )

# Load the module source and exec it in a controlled namespace so the
# if __name__ == "__main__" block is NOT triggered.
_mod_globals: dict = {
    "__name__": "tool5_uat",  # NOT "__main__"
    "__file__": _tool_path,
}

# Provide all external names the module expects at import time
import base64 as _base64
import csv as _csv
import datetime as _datetime
import io as _io
import requests as _requests

_mod_globals.update(
    {
        "sys": sys,
        "os": os,
        "json": json,
        "datetime": _datetime,
        "csv": _csv,
        "io": _io,
        "requests": _requests,
        "base64": _base64,
        # shared symbols
        "clean_json": _shared_stub.clean_json,
        "call_claude": _shared_stub.call_claude,
        "get_repo_files": _shared_stub.get_repo_files,
        "write_output_file": _shared_stub.write_output_file,
        "send_email": _shared_stub.send_email,
        "email_html": _shared_stub.email_html,
        "write_audit_entry": _shared_stub.write_audit_entry,
        "OUTPUT_REPO_OWNER": _shared_stub.OUTPUT_REPO_OWNER,
        "OUTPUT_REPO": _shared_stub.OUTPUT_REPO,
        "GH_HEADERS": _shared_stub.GH_HEADERS,
        "GH_API": _shared_stub.GH_API,
    }
)

if os.path.exists(_tool_path):
    with open(_tool_path, "r") as _fh:
        _source = _fh.read()
    # Truncate at the __main__ guard so we never execute side-effectful code
    _safe_source = _source.split('if __name__ == "__main__"')[0]
    exec(compile(_safe_source, _tool_path, "exec"), _mod_globals)  # noqa: S102

# Extract the public functions into convenient names
parse_scenarios = _mod_globals.get("parse_scenarios")
build_test_pack_csv = _mod_globals.get("build_test_pack_csv")
build_test_pack_md = _mod_globals.get("build_test_pack_md")
get_results_csv = _mod_globals.get("get_results_csv")

# If the file didn't exist yet (CI scaffold), define stubs so tests can still
# be collected and skipped gracefully.
if parse_scenarios is None:
    pytest.skip(
        "tool5_uat.py not found — skipping all tests",
        allow_module_level=True,
    )


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-001
TITLE: Successful underwriting risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is authenticated
- Model is deployed
TEST DATA: Age=35, Annual_Income=75000, Employment_Status=Permanent
STEPS:
1. Navigate to underwriting screen
2. Enter customer data
3. Submit for risk classification
EXPECTED RESULT: System returns Risk_Classification within 2 seconds
PASS CRITERIA: Risk_Classification label displayed and confidence > 0.8
ESTIMATED TIME: 5
NOTES: Uses CatBoostClassifier model
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-001
TITLE: Happy path risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Risk label shown
ESTIMATED TIME: 5
NOTES: none

===SCENARIO===
ID: UAT-RISK-002
TITLE: Missing income field
TYPE: NEGATIVE
PERSONA: Underwriter
PASS CRITERIA: Validation error displayed
ESTIMATED TIME: 3
NOTES: boundary edge
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-RISK-003
TITLE: Maximum income boundary
TYPE: BOUNDARY
PERSONA: Senior Underwriter
PASS CRITERIA: System accepts max value without overflow
ESTIMATED TIME: 4
NOTES: test with Annual_Income=99999999
"""


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================


class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-RISK-001"

    def test_single_scenario_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Successful underwriting risk classification"

    def test_single_scenario_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "confidence > 0.8" in result[0]["pass_criteria"]

    def test_single_scenario_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_field_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-RISK-001" in result[0]["raw"]

    def test_two_scenarios_returns_two_items(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_two_scenarios_ids_correct(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert "UAT-RISK-001" in ids
        assert "UAT-RISK-002" in ids

    def test_boundary_scenario_type(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_negative_scenario_type(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        types = [s["type"] for s in result]
        assert "NEGATIVE" in types

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        # A block without ===SCENARIO=== delimiter that also has no ID
        result = parse_scenarios("Some random text without delimiters or IDs")
        assert result == []

    def test_block_without_id_excluded(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_multiple_consecutive_delimiters(self):
        raw = "===SCENARIO===\n===SCENARIO===\nID: UAT-X-001\nTITLE: Valid\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        # Only the block with an ID should be included
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-001"

    def test_missing_optional_fields_do_not_raise(self):
        raw = "===SCENARIO===\nID: UAT-MIN-001\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        # Optional fields should simply be absent (not raise KeyError)
        assert s.get("title") is None or isinstance(s.get("title"), str)

    def test_whitespace_around_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-001   \nTITLE:   Whitespace test   \nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-001"
        assert result[0]["title"] == "Whitespace test"

    def test_large_number_of_scenarios(self):
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\nID: UAT-LOAD-{i:03d}\nTITLE: Load scenario {i}\nTYPE: POSITIVE\n"
            )
        raw = "\n".join(blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    def test_raw_field_contains_full_block_content(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "CatBoostClassifier" in result[0]["raw"]

    @pytest.mark.parametrize(
        "scenario_type",
        ["POSITIVE", "NEGATIVE", "BOUNDARY"],
    )
    def test_all_valid_types_parsed(self, scenario_type):
        raw = (
            f"===SCENARIO===\nID: UAT-TYPE-001\nTITLE: Type test\nTYPE: {scenario_type}\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["type"] == scenario_type

    def test_leading_text_before_first_delimiter_ignored(self):
        raw = "Preamble text that should be ignored\n" + TWO_SCENARIO_BLOCK
        result = parse_scenarios(raw)
        assert len(result) == 2

    def test_unicode_content_handled(self):
        # Arabic characters from synthetic data (ar-SA.json)
        raw = "===SCENARIO===\nID: UAT-AR-001\nTITLE: إلغاء action test\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-AR-001"
        assert "إلغاء" in result[0]["title"]


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        rows = self._parse_csv(build_test_pack_csv([]))
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
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # header only (trailing newline may produce empty row)

    def test_single_scenario_produces_two_rows(self):
        scenarios = [
            {
                "id": "UAT-RISK-001",
                "title": "Happy path",
                "type": "POSITIVE",
                "persona": "Underwriter",
                "pass_criteria": "Label shown",
                "estimated_time": "5",
            }
        ]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        data_rows = [r for r in rows if r]  # drop empty rows from trailing newline
        assert len(data_rows) == 2  # header + 1 scenario

    def test_scenario_id_in_correct_column(self):
        scenarios = [{"id": "UAT-RISK-001", "title": "T", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "PC", "estimated_time": "3"}]
        rows = [r for r in self._parse_csv(build_test_pack_csv(scenarios)) if r]
        assert rows[1][0] == "UAT-RISK-001"

    def test_title_in_correct_column(self):
        scenarios = [{"id": "UAT-001", "title": "My Title", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "PC", "estimated_time": "3"}]
        rows = [r for r in self._parse_csv(build_test_pack_csv(scenarios)) if r]
        assert rows[1][1] == "My Title"

    def test_result_column_empty_for_tester(self):
        scenarios = [{"id": "UAT-001", "title": "T", "type": "