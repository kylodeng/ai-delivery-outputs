"""
Test module for tool5_uat.py

What is tested:
    - parse_scenarios(): parsing Claude's raw scenario output into structured dicts
    - build_test_pack_csv(): building a CSV sheet from parsed scenarios
    - build_test_pack_md(): building a Markdown test pack document
    - get_results_csv(): fetching a CSV results file from a GitHub repo (mocked)
    - __main__ block entrypoint behaviour (generate and analyse modes) via subprocess/env patching

Mocks used:
    - unittest.mock.patch for requests.get (GitHub API calls)
    - unittest.mock.patch for shared module functions: call_claude, get_repo_files,
      write_output_file, send_email, email_html, write_audit_entry, clean_json
    - base64 encoding/decoding for GitHub content responses
    - io.StringIO for CSV output verification

TODOs:
    - TODO: Integration test for full __main__ generate flow requires real GH_HEADERS/GH_API config
    - TODO: Integration test for full __main__ analyse flow requires real Claude API key
    - TODO: Test email dispatch in generate/analyse modes once SMTP config is available
    - TODO: Verify SYSTEM_GENERATE and SYSTEM_ANALYSE prompt strings produce valid Claude output
"""

import base64
import csv
import io
import json
import os
import sys
import importlib
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so we can import tool5_uat without
# having the real shared.py on the path during tests.
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="mocked claude response")
shared_stub.get_repo_files = MagicMock(return_value="mocked repo files")
shared_stub.write_output_file = MagicMock(return_value={"content": {"html_url": "https://github.com/test"}})
shared_stub.send_email = MagicMock()
shared_stub.email_html = MagicMock(return_value="<html>test</html>")
shared_stub.write_audit_entry = MagicMock()

sys.modules["shared"] = shared_stub

# Now we can import the module under test
import tool5_uat as uat


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid login with correct credentials
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User account exists
TEST DATA: user@example.com / P@ssw0rd123
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Login
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 s
ESTIMATED TIME: 5
NOTES: None
"""

NEGATIVE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- User account exists
TEST DATA: user@example.com / wrongpassword
STEPS:
1. Navigate to login page
2. Enter wrong password
3. Click Login
EXPECTED RESULT: Error message displayed
PASS CRITERIA: Error message shown and user not logged in
ESTIMATED TIME: 3
NOTES: Check lockout after 3 attempts
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-3
TITLE: Login with maximum length password
TYPE: BOUNDARY
PERSONA: Policyholder
PRE-CONDITIONS:
- Account with 128-char password exists
TEST DATA: user@example.com / {"a"*128}
STEPS:
1. Navigate to login page
2. Enter 128-char password
3. Click Login
EXPECTED RESULT: Login succeeds
PASS CRITERIA: User reaches dashboard
ESTIMATED TIME: 4
NOTES: Boundary at 128 chars
"""

MULTI_SCENARIO_RAW = (
    MINIMAL_SCENARIO_BLOCK
    + NEGATIVE_SCENARIO_BLOCK
    + BOUNDARY_SCENARIO_BLOCK
)


@pytest.fixture
def sample_scenarios():
    """Return pre-parsed scenario dicts for reuse."""
    return [
        {
            "id": "UAT-GEN2-1",
            "title": "Purchase Generations II policy",
            "type": "POSITIVE",
            "persona": "Policyholder",
            "pass_criteria": "Policy issued confirmation shown",
            "estimated_time": "10",
            "raw": "raw block 1",
        },
        {
            "id": "UAT-GEN2-2",
            "title": "Attempt purchase without valid ID",
            "type": "NEGATIVE",
            "persona": "Unverified User",
            "pass_criteria": "Error message displayed",
            "estimated_time": "5",
            "raw": "raw block 2",
        },
        {
            "id": "UAT-GEN2-3",
            "title": "Maximum benefit boundary check",
            "type": "BOUNDARY",
            "persona": "Financial Advisor",
            "pass_criteria": "System accepts max benefit value",
            "estimated_time": "7",
            "raw": "raw block 3",
        },
    ]


# ---------------------------------------------------------------------------
# Tests: parse_scenarios()
# ---------------------------------------------------------------------------

class TestParseScenarios:
    """Tests for the parse_scenarios() function."""

    def test_parse_single_scenario_happy_path(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Valid login with correct credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Policyholder"
        assert s["pass_criteria"] == "Dashboard loads within 3 s"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_parse_multiple_scenarios(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_parse_types_captured_correctly(self):
        result = uat.parse_scenarios(MULTI_SCENARIO_RAW)
        types = {s["id"]: s["type"] for s in result}
        assert types["UAT-STORY1-1"] == "POSITIVE"
        assert types["UAT-STORY1-2"] == "NEGATIVE"
        assert types["UAT-STORY1-3"] == "BOUNDARY"

    def test_parse_empty_string_returns_empty_list(self):
        result = uat.parse_scenarios("")
        assert result == []

    def test_parse_no_delimiter_returns_empty_list(self):
        raw = "This is just some free text without any scenario blocks."
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_parse_block_without_id_is_skipped(self):
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
"""
        result = uat.parse_scenarios(raw)
        assert result == []

    def test_parse_partial_fields_still_creates_entry(self):
        raw = """\
===SCENARIO===
ID: UAT-PARTIAL-1
TITLE: Partial scenario
"""
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-PARTIAL-1"
        assert result[0]["title"] == "Partial scenario"
        # Missing fields should be absent
        assert "type" not in result[0]
        assert "persona" not in result[0]

    def test_parse_raw_field_contains_original_block(self):
        result = uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert "Dashboard loads within 3 s" in result[0]["raw"]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_parse_whitespace_only_blocks_are_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-X-1\nTITLE: Real\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"

    def test_parse_scenario_with_leading_text_before_first_delimiter(self):
        raw = "Some preamble text\n" + MINIMAL_SCENARIO_BLOCK
        result = uat.parse_scenarios(raw)
        # Preamble block has no ID → skipped
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_parse_scenario_ids_with_insurance_synthetic_data(self):
        """Use synthetic insurance product reference in scenario IDs."""
        raw = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Generations II policy purchase happy path
TYPE: POSITIVE
PERSONA: Policyholder
PASS CRITERIA: Policy confirmation issued with Generations II plan details
ESTIMATED TIME: 10
NOTES: Uses Generations II product brochure data
"""
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-GEN2-1"
        assert "Generations II" in result[0]["title"]

    def test_parse_estimated_time_whitespace_stripped(self):
        raw = """\
===SCENARIO===
ID: UAT-TIME-1
TITLE: Time whitespace test
ESTIMATED TIME:   15  
"""
        result = uat.parse_scenarios(raw)
        assert result[0]["estimated_time"] == "15"

    def test_parse_large_number_of_scenarios(self):
        """Boundary: parse 50 scenario blocks."""
        blocks = ""
        for i in range(1, 51):
            blocks += f"""\
===SCENARIO===
ID: UAT-BULK-{i}
TITLE: Bulk scenario {i}
TYPE: POSITIVE
PERSONA: Tester
PASS CRITERIA: Step {i} passes
ESTIMATED TIME: 2
NOTES: Bulk test
"""
        result = uat.parse_scenarios(blocks)
        assert len(result) == 50
        assert result[0]["id"] == "UAT-BULK-1"
        assert result[49]["id"] == "UAT-BULK-50"

    def test_parse_pass_criteria_multiword(self):
        raw = """\
===SCENARIO===
ID: UAT-CRIT-1
TITLE: Criteria test
PASS CRITERIA: User sees success banner AND email is received within 60 seconds
"""
        result = uat.parse_scenarios(raw)
        assert "User sees success banner AND email is received within 60 seconds" in result[0]["pass_criteria"]


# ---------------------------------------------------------------------------
# Tests: build_test_pack_csv()
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:
    """Tests for the build_test_pack_csv() function."""

    def test_csv_has_correct_header(self, sample_scenarios):
        csv_str = uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(csv_str))
        header = next(reader)
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

    def test_csv_row_count_matches_scenarios(self, sample_scenarios):
        csv_str = uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        # 1 header + 3 data rows
        assert len(rows) == 4

    def test_csv_data_row_values(self, sample_scenarios):
        csv_str = uat.build_test_pack_csv(sample_scenarios)
        reader = csv.reader(io.StringIO(csv_str))
        next(reader)  # skip header
        first_row = next(reader)
        assert first_row[0] == "UAT-GEN2-1"
        assert first_row[1] == "Purchase Generations II policy"
        assert first_row[2] == "POSITIVE"
        assert first_row[3] == "Policyholder"
        assert first_row[4] == "Policy issued confirmation shown"
        assert first_row[5] == "10"
        # Editable columns should be blank
        assert first_row[6] == ""
        assert first_row[7] == ""
        assert first_row[8] == ""
        assert first_row[9] == ""

    def test_csv_empty_scenarios_list_returns_header_only(self):
        csv_str = uat.build_test_pack_csv([])
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0][0] == "Scenario ID"

    def test_csv_scenario_with_missing_fields(self):
        """Edge case: scenario dict missing optional fields."""
        scenarios = [{"id": "UAT-MIN-1", "raw": "raw"}]
        csv_str = uat.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(csv_str))
        next(reader)
        row = next(reader)
        assert row[0] == "UAT-MIN-1"
        # All other fields should be empty strings
        assert row[1] == ""
        assert row[2] == ""
        assert row[3] == ""
        assert row[4] == ""
        assert row[5] == ""

    def test_csv_special_characters_in_title(self):
        """Edge case: title with commas and quotes."""
        scenarios = [{
            "id": "UAT-SPEC-1",
            "title": 'Title with "quotes" and, commas',
            "type": "POSITIVE",
            "persona": "Admin",
            "pass_criteria": "OK",
            "estimated_time": "5",
            "raw": "",
        }]
        csv_str = uat.build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(csv_str))
        next(reader)
        row = next(reader)
        assert row[1] == 'Title with "quotes" and, commas'

    def test_csv_returns_string(self, sample_scenarios):
        result = uat.build_test_pack_csv(sample_scenarios)
        assert isinstance(result, str)

    def test_csv_single_scenario_with_insurance_data(self):
        """Uses synthetic insurance data as test data."""
        scenarios = [{
            "id": "UAT-HLTH-1",
            