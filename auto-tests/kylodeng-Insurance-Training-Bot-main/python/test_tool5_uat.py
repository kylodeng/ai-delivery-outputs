"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, missing fields, malformed input, boundary values
    - build_test_pack_csv(): CSV structure, correct headers, data rows, empty input
    - build_test_pack_md(): markdown output structure, version/owner/repo injection
    - get_results_csv(): successful fetch, missing content key, network errors
    - Module-level __main__ block: not directly tested (requires full env wiring)

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
    - unittest.mock.patch for `base64.b64decode` where needed
    - All external dependencies (call_claude, send_email, write_output_file, etc.) are NOT
      called in the tested functions; they live in __main__ only

TODOs:
    - TODO: Integration test for __main__ block requires full env vars + mocked GitHub API
    - TODO: Test call_claude interaction once it is extracted from __main__ into a function
    - TODO: Test send_email / email_html usage once extracted from __main__
    - TODO: Verify SYSTEM_GENERATE and SYSTEM_ANALYSE prompt constants against live Claude responses
"""

import base64
import csv
import io
import json
import sys
import os
import types
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so we can import tool5_uat without
# requiring the real shared.py or its transitive dependencies.
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda x: x
_shared_stub.call_claude = MagicMock(return_value="")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock()
_shared_stub.send_email = MagicMock()
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock()
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now we can safely import the module under test
import importlib
import tool5_uat  # noqa: E402  (imported after path manipulation)

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid login with correct credentials
TYPE: POSITIVE
PERSONA: Registered Customer
PRE-CONDITIONS:
- User account exists
- System is available
TEST DATA: username=john.doe@example.com, password=P@ssw0rd123
STEPS:
1. Navigate to login page
2. Enter username and password
3. Click Submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid login with correct credentials
TYPE: POSITIVE
PERSONA: Registered Customer
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: Registered Customer
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
"""

MINIMAL_SCENARIO = """\
===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario
TYPE: BOUNDARY
PERSONA: Admin
"""


def _make_scenario(
    id_="UAT-S1-1",
    title="Test title",
    type_="POSITIVE",
    persona="Customer",
    pass_criteria="System responds correctly",
    estimated_time="5",
) -> dict:
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": "raw block text",
    }


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_single_scenario_parsed_correctly(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Valid login with correct credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Registered Customer"
        assert s["pass_criteria"] == "Dashboard loads within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_raw_field_always_present(self):
        scenarios = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in scenarios[0]
        assert len(scenarios[0]["raw"]) > 0

    def test_two_scenarios_parsed(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(scenarios) == 2
        assert scenarios[0]["id"] == "UAT-STORY1-1"
        assert scenarios[1]["id"] == "UAT-STORY1-2"

    def test_scenario_types_captured(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = {s["type"] for s in scenarios}
        assert "POSITIVE" in types_
        assert "NEGATIVE" in types_

    def test_minimal_scenario_missing_optional_fields(self):
        scenarios = parse_scenarios(MINIMAL_SCENARIO)
        assert len(scenarios) == 1
        s = scenarios[0]
        assert s["id"] == "UAT-MIN-1"
        assert s["title"] == "Minimal scenario"
        # Optional fields absent
        assert s.get("pass_criteria", "") == ""
        assert s.get("estimated_time", "") == ""

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-X-1\nTITLE: Some title\nTYPE: POSITIVE"
        # No ===SCENARIO=== delimiter → first block is empty prefix
        result = parse_scenarios(raw)
        assert result == []

    def test_block_without_id_is_excluded(self):
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Customer
"""
        result = parse_scenarios(raw)
        assert result == []

    def test_extra_whitespace_around_values(self):
        raw = """\
===SCENARIO===
ID:   UAT-SPACE-1   
TITLE:   Whitespace test   
TYPE:   BOUNDARY   
PERSONA:   Admin   
PASS CRITERIA:   Passes   
ESTIMATED TIME:   10   
"""
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-SPACE-1"
        assert scenarios[0]["title"] == "Whitespace test"
        assert scenarios[0]["type"] == "BOUNDARY"
        assert scenarios[0]["estimated_time"] == "10"

    def test_multiple_scenarios_all_have_raw(self):
        scenarios = parse_scenarios(TWO_SCENARIO_BLOCK)
        for s in scenarios:
            assert "raw" in s

    def test_insurance_product_scenario(self):
        """Synthetic-data derived: simulate UAT scenario for Generations II."""
        raw = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Submit claim for Generations II terminal illness benefit
TYPE: POSITIVE
PERSONA: Policyholder
PRE-CONDITIONS:
- Policy is active
- Diagnosis from designated hospital in mainland China
TEST DATA: product_name=Generations II, doc_type=product_brochure, claim_type=terminal_illness
STEPS:
1. Log in as policyholder
2. Navigate to Claims section
3. Select Accelerated Benefit - Terminal Illness
4. Upload diagnosis documents from designated hospital
5. Submit claim
EXPECTED RESULT: Claim submitted successfully with reference number
PASS CRITERIA: Reference number displayed and confirmation email received
ESTIMATED TIME: 15
NOTES: Hospital must be on the designated list for mainland China
"""
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        assert scenarios[0]["id"] == "UAT-GEN2-1"
        assert scenarios[0]["persona"] == "Policyholder"

    def test_boundary_values_many_scenarios(self):
        """Boundary: parse a large number of scenario blocks."""
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BIG-{i}\nTITLE: Scenario {i}\nTYPE: POSITIVE\n"
            for i in range(50)
        )
        scenarios = parse_scenarios(blocks)
        assert len(scenarios) == 50

    def test_scenario_with_colon_in_value(self):
        """Values that contain colons should not break parsing."""
        raw = """\
===SCENARIO===
ID: UAT-COLON-1
TITLE: URL with https://example.com
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Redirect to https://dashboard.example.com works
ESTIMATED TIME: 2
"""
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 1
        # The title line starts with "TITLE:" so split on first colon is the key behaviour
        # The implementation uses line.replace("TITLE:", "").strip() which is correct
        assert "TITLE:" not in scenarios[0]["title"]

    def test_duplicate_ids_both_parsed(self):
        """Duplicate IDs are not deduplicated — both are returned."""
        raw = """\
===SCENARIO===
ID: UAT-DUP-1
TITLE: First
TYPE: POSITIVE
===SCENARIO===
ID: UAT-DUP-1
TITLE: Second
TYPE: NEGATIVE
"""
        scenarios = parse_scenarios(raw)
        assert len(scenarios) == 2


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_header_row_present(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)
        assert "Scenario ID" in headers
        assert "Title" in headers
        assert "Type" in headers
        assert "Persona" in headers
        assert "Pass Criteria" in headers
        assert "Est. Time (min)" in headers
        assert "Result (PASS/FAIL/BLOCKED)" in headers
        assert "Tester" in headers
        assert "Notes" in headers
        assert "Defect Ref" in headers

    def test_header_has_ten_columns(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        headers = next(reader)
        assert len(headers) == 10

    def test_empty_scenarios_only_header(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_single_scenario_produces_one_data_row(self):
        s = _make_scenario()
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data

    def test_data_row_values_match_scenario(self):
        s = _make_scenario(
            id_="UAT-T-1",
            title="My Test",
            type_="NEGATIVE",
            persona="Admin",
            pass_criteria="Error shown",
            estimated_time="3",
        )
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        row = next(reader)
        assert row[0] == "UAT-T-1"
        assert row[1] == "My Test"
        assert row[2] == "NEGATIVE"
        assert row[3] == "Admin"
        assert row[4] == "Error shown"
        assert row[5] == "3"

    def test_result_tester_notes_defect_blank(self):
        s = _make_scenario()
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        # Columns 6-9 (Result, Tester, Notes, Defect Ref) should be empty
        assert row[6] == ""
        assert row[7] == ""
        assert row[8] == ""
        assert row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(id_=f"UAT-M-{i}") for i in range(5)]
        result = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_optional_fields_produce_empty_strings(self):
        s = {"id": "UAT-PARTIAL-1", "raw": "raw"}  # missing most fields
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[0] == "UAT-PARTIAL-1"
        assert row[1] == ""  # title missing
        assert row[2] == ""  # type missing

    def test_insurance_synthetic_scenario_in_csv(self):
        """Synthetic data: Generations II claim scenario."""
        s = _make_scenario(
            id_="UAT-GEN2-2",
            title="Cashless claim at designated mainland China hospital",
            type_="POSITIVE",
            persona="Policyholder",
            pass_criteria="Cashless arrangement confirmed within 30 minutes",
            estimated_time="20",
        )
        result = build_test_pack_csv([s])
        assert "UAT-GEN2-2" in result
        assert "Cashless claim at designated mainland China hospital" in result

    def test_csv_is_valid_utf8(self):
        s = _make_scenario(title="Scénario avec accents")
        result = build_test_pack_csv([s])
        # Should not raise
        result.encode("utf-8")

    def test_special_characters_in_title_csv_safe(self):
        """Titles with commas and quotes must be CSV-escaped."""
        s = _make_scenario(title='He said "hello, world"')
        result = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(result))
        next(reader)
        row = next(reader)
        assert row[1] == 'He said "hello, world"'

    