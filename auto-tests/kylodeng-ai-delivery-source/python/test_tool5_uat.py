"""
Tests for tool5_uat.py

What is tested:
- parse_scenarios(): happy path, edge cases, malformed blocks, empty input, partial fields
- build_test_pack_csv(): CSV structure, header row, data rows, empty input, special characters
- build_test_pack_md(): markdown output structure, version/repo embedding, raw content inclusion
- get_results_csv(): successful fetch and decode, missing content key, HTTP errors
- Module-level constants and imports from shared

Mocks used:
- unittest.mock.patch for requests.get (get_results_csv)
- unittest.mock.MagicMock for response objects
- base64 encoding/decoding tested directly

TODOs:
- TODO: Integration test for full __main__ block requires GitHub Actions env vars and live Claude API
- TODO: Test call_claude integration within generate/analyse flow once shared.py contract is stable
- TODO: Test write_output_file and send_email calls in __main__ (need OUTPUT_REPO_OWNER etc.)
- TODO: Test parse_scenarios with actual Claude API output once prompt engineering is finalised
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
# Path setup — mirror what tool5_uat.py does so shared stubs resolve
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), ".github", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# Stub out the `shared` module before importing tool5_uat so we never hit
# real network calls or missing credentials.
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules["shared"] = shared_stub

import importlib
import types

# Now safe to import the module under test
import importlib.util

_tool_path = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
)

# Load via spec so we can patch at import time even if the file has a
# truncated __main__ block (the source ends mid-string literal).
try:
    spec = importlib.util.spec_from_file_location("tool5_uat", _tool_path)
    tool5 = importlib.util.module_from_spec(spec)
    # Inject the stub before exec
    tool5.shared = shared_stub  # type: ignore[attr-defined]
    spec.loader.exec_module(tool5)
except SyntaxError:
    # The provided source has a truncated string literal at the end of the
    # __main__ block.  Extract only the functions we need by loading the
    # file up to the `if __name__ == "__main__":` guard.
    with open(_tool_path, "r") as fh:
        raw_source = fh.read()
    # Truncate at the __main__ guard so the rest of the module is valid
    truncated = raw_source.split('if __name__ == "__main__":')[0]
    tool5 = types.ModuleType("tool5_uat")
    tool5.__dict__["shared"] = shared_stub  # type: ignore[attr-defined]
    # Provide the stubs that tool5 imports from shared at module level
    tool5.__dict__.update(
        {
            "clean_json": shared_stub.clean_json,
            "call_claude": shared_stub.call_claude,
            "get_repo_files": shared_stub.get_repo_files,
            "write_output_file": shared_stub.write_output_file,
            "send_email": shared_stub.send_email,
            "email_html": shared_stub.email_html,
            "write_audit_entry": shared_stub.write_audit_entry,
            "OUTPUT_REPO_OWNER": "test-owner",
            "OUTPUT_REPO": "test-repo",
            "GH_HEADERS": {"Authorization": "Bearer fake-token"},
            "GH_API": "https://api.github.com",
        }
    )
    exec(compile(truncated, _tool_path, "exec"), tool5.__dict__)  # noqa: S102

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO = """===SCENARIO===
ID: UAT-FEAT1-1
TITLE: Successful customer login
TYPE: POSITIVE
PERSONA: Enterprise Admin
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: alice.chen@example.com / SecurePass1!
STEPS:
1. Navigate to /login
2. Enter email and password
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard is displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires seeded DB"""

TWO_SCENARIOS = (
    SINGLE_SCENARIO
    + "\n===SCENARIO===\n"
    + """ID: UAT-FEAT1-2
TITLE: Login with invalid credentials
TYPE: NEGATIVE
PERSONA: Consumer
PRE-CONDITIONS:
- System is online
TEST DATA: invalid-email / wrongpassword
STEPS:
1. Navigate to /login
2. Enter invalid credentials
3. Click Submit
EXPECTED RESULT: Error message displayed
PASS CRITERIA: "Invalid credentials" message shown, user remains on /login
ESTIMATED TIME: 3
NOTES: Check rate-limiting after 5 attempts"""
)

BOUNDARY_SCENARIO = """===SCENARIO===
ID: UAT-FEAT2-1
TITLE: Max-length username boundary check
TYPE: BOUNDARY
PERSONA: SMB User
PRE-CONDITIONS:
- System is online
TEST DATA: username = 'a' * 255
STEPS:
1. Navigate to /register
2. Enter 255-character username
3. Submit form
EXPECTED RESULT: Registration succeeds or graceful validation error
PASS CRITERIA: No 500 error returned
ESTIMATED TIME: 2
NOTES: """


def _make_scenario_block(scenario_id: str = "UAT-X-1", title: str = "Test title") -> str:
    return f"""===SCENARIO===
ID: {scenario_id}
TITLE: {title}
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: System behaves correctly
ESTIMATED TIME: 5
NOTES: none"""


# ===========================================================================
# parse_scenarios
# ===========================================================================


class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert result[0]["id"] == "UAT-FEAT1-1"

    def test_single_scenario_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert result[0]["title"] == "Successful customer login"

    def test_single_scenario_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert result[0]["persona"] == "Enterprise Admin"

    def test_single_scenario_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert "Dashboard" in result[0]["pass_criteria"]

    def test_single_scenario_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_preserved(self):
        result = parse_scenarios(SINGLE_SCENARIO)
        assert "Navigate to /login" in result[0]["raw"]

    def test_two_scenarios_returns_two_items(self):
        result = parse_scenarios(TWO_SCENARIOS)
        assert len(result) == 2

    def test_two_scenarios_ids_distinct(self):
        result = parse_scenarios(TWO_SCENARIOS)
        ids = [s["id"] for s in result]
        assert ids == ["UAT-FEAT1-1", "UAT-FEAT1-2"]

    def test_two_scenarios_types_correct(self):
        result = parse_scenarios(TWO_SCENARIOS)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_boundary_scenario_type(self):
        result = parse_scenarios(BOUNDARY_SCENARIO)
        assert result[0]["type"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This is just some random text with no delimiter.")
        assert result == []

    def test_delimiter_only_no_content_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===\n   \n===SCENARIO===\n  ")
        assert result == []

    def test_block_without_id_excluded(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_partial_fields_still_captured(self):
        """Scenario with only ID and TITLE — missing optional fields."""
        raw = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal scenario\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert result[0]["title"] == "Minimal scenario"
        # Optional fields should be absent (no key set)
        assert "type" not in result[0]

    def test_multiple_colons_in_value(self):
        """Values containing colons should not be truncated."""
        raw = "===SCENARIO===\nID: UAT-COL-1\nTITLE: Check URL: https://example.com\n"
        result = parse_scenarios(raw)
        # The current parser does startswith so only the first colon matters for key
        # TITLE: strips "TITLE:" prefix → value contains the rest
        assert "https://example.com" in result[0]["title"]

    def test_whitespace_trimmed_from_values(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace test   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"

    def test_leading_delimiter_ignored(self):
        """Content before the first delimiter is discarded."""
        raw = "Preamble text\n===SCENARIO===\nID: UAT-P-1\nTITLE: After preamble\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-P-1"

    @pytest.mark.parametrize(
        "scenario_id,expected",
        [
            ("UAT-FEAT1-1", "UAT-FEAT1-1"),
            ("UAT-AUTH-99", "UAT-AUTH-99"),
            ("UAT-CUST-001", "UAT-CUST-001"),  # customer ID format from synthetic data
        ],
    )
    def test_various_id_formats(self, scenario_id, expected):
        raw = _make_scenario_block(scenario_id=scenario_id)
        result = parse_scenarios(raw)
        assert result[0]["id"] == expected

    def test_large_number_of_scenarios(self):
        """Performance / correctness with many scenarios."""
        blocks = "\n".join(
            _make_scenario_block(scenario_id=f"UAT-LOAD-{i}") for i in range(50)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 50

    def test_unicode_content_handled(self):
        raw = "===SCENARIO===\nID: UAT-UNI-1\nTITLE: 日本語テスト\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "日本語テスト"

    def test_windows_line_endings(self):
        raw = "===SCENARIO===\r\nID: UAT-WIN-1\r\nTITLE: Windows CRLF\r\nTYPE: POSITIVE\r\n"
        # The parser splits on "\n"; on Windows CRLF the \r may remain in values.
        # Test that we at least get a result (robustness check).
        result = parse_scenarios(raw)
        assert len(result) == 1


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_returns_string(self):
        assert isinstance(build_test_pack_csv([]), str)

    def test_header_row_present(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0][0] == "Scenario ID"

    def test_header_has_ten_columns(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows[0]) == 10

    def test_header_contains_result_column(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert "Result (PASS/FAIL/BLOCKED)" in rows[0]

    def test_empty_scenarios_only_header(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # just header

    def test_single_scenario_produces_two_rows(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 2  # header + 1

    def test_scenario_id_in_first_data_column(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-FEAT1-1"

    def test_scenario_title_in_second_column(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][1] == "Successful customer login"

    def test_result_column_is_empty_for_tester(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        # Column index 6 = Result
        assert rows[1][6] == ""

    def test_tester_column_is_empty(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO)
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][7] == ""

    def test_defect_ref_column_is_empty(