"""
Test module for tool5_uat.py

What is tested:
    - parse_scenarios: parsing Claude raw output into structured dicts
    - build_test_pack_csv: CSV generation from scenario list
    - build_test_pack_md: Markdown test pack generation
    - get_results_csv: fetching and decoding CSV results from GitHub API
    - Integration of mode-dispatch logic (generate / analyse) via __main__ block stubs

Mocks used:
    - requests.get (for get_results_csv GitHub API call)
    - shared.call_claude
    - shared.get_repo_files
    - shared.write_output_file
    - shared.send_email
    - shared.write_audit_entry
    - base64.b64decode (indirectly via mocked response content)

TODOs:
    - TODO: Full end-to-end test of __main__ block requires env var injection + subprocess
            or importlib reload; stubs provided below.
    - TODO: Test call_claude prompt contents once prompt templates are finalised.
    - TODO: Test write_output_file actually writes correct filenames/paths (needs output repo fixture).
    - TODO: Test send_email payload structure for UAT report emails.
"""

import base64
import csv
import io
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so we can import tool5_uat without
# a real shared.py on the path in the test environment.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda s: s
_shared_stub.call_claude = MagicMock(return_value="")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib
import tool5_uat  # noqa: E402  (inserted after path manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login
TYPE: POSITIVE
PERSONA: Enterprise Admin
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: alice.chen@example.com / Password123
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Sign In
EXPECTED RESULT: Dashboard loads
PASS CRITERIA: Dashboard page visible within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIOS_RAW = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login
TYPE: POSITIVE
PERSONA: Enterprise Admin
PRE-CONDITIONS:
- User account exists
TEST DATA: alice.chen@example.com
STEPS:
1. Open app
EXPECTED RESULT: Dashboard
PASS CRITERIA: Dashboard visible
ESTIMATED TIME: 5
NOTES: -

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid email
TYPE: NEGATIVE
PERSONA: Consumer
PRE-CONDITIONS:
- System online
TEST DATA: invalid-email
STEPS:
1. Enter invalid-email
2. Submit
EXPECTED RESULT: Error shown
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: uses invalid-email from synthetic data
"""

NO_ID_BLOCK_RAW = """\
===SCENARIO===
TITLE: No ID here
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Something passes
ESTIMATED TIME: 2
NOTES: -
"""

EMPTY_RAW = ""

ONLY_DELIMITERS_RAW = "===SCENARIO===\n===SCENARIO==="


@pytest.fixture()
def single_scenario():
    return tool5_uat.parse_scenarios(SINGLE_SCENARIO_RAW)


@pytest.fixture()
def two_scenarios():
    return tool5_uat.parse_scenarios(TWO_SCENARIOS_RAW)


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self, single_scenario):
        assert len(single_scenario) == 1

    def test_single_scenario_id(self, single_scenario):
        assert single_scenario[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title(self, single_scenario):
        assert single_scenario[0]["title"] == "Successful login"

    def test_single_scenario_type(self, single_scenario):
        assert single_scenario[0]["type"] == "POSITIVE"

    def test_single_scenario_persona(self, single_scenario):
        assert single_scenario[0]["persona"] == "Enterprise Admin"

    def test_single_scenario_pass_criteria(self, single_scenario):
        assert "Dashboard" in single_scenario[0]["pass_criteria"]

    def test_single_scenario_estimated_time(self, single_scenario):
        assert single_scenario[0]["estimated_time"] == "5"

    def test_single_scenario_raw_contains_block(self, single_scenario):
        assert "Successful login" in single_scenario[0]["raw"]

    def test_two_scenarios_returns_two_items(self, two_scenarios):
        assert len(two_scenarios) == 2

    def test_two_scenarios_ids(self, two_scenarios):
        ids = [s["id"] for s in two_scenarios]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_negative_type_parsed(self, two_scenarios):
        neg = next(s for s in two_scenarios if s["type"] == "NEGATIVE")
        assert neg["id"] == "UAT-STORY1-2"

    def test_block_without_id_is_excluded(self):
        result = tool5_uat.parse_scenarios(NO_ID_BLOCK_RAW)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = tool5_uat.parse_scenarios(EMPTY_RAW)
        assert result == []

    def test_only_delimiters_returns_empty_list(self):
        result = tool5_uat.parse_scenarios(ONLY_DELIMITERS_RAW)
        assert result == []

    def test_whitespace_stripped_from_id(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE: Whitespace test\nTYPE: POSITIVE\nPASS CRITERIA: ok\nESTIMATED TIME: 1\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"

    def test_scenario_missing_optional_fields_still_included(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0].get("title") is None
        assert result[0]["id"] == "UAT-MIN-1"

    def test_raw_field_present_on_all_items(self, two_scenarios):
        for s in two_scenarios:
            assert "raw" in s

    def test_multiple_scenarios_order_preserved(self, two_scenarios):
        assert two_scenarios[0]["id"] == "UAT-STORY1-1"
        assert two_scenarios[1]["id"] == "UAT-STORY1-2"

    @pytest.mark.parametrize("raw,expected_count", [
        (SINGLE_SCENARIO_RAW, 1),
        (TWO_SCENARIOS_RAW, 2),
        (NO_ID_BLOCK_RAW, 0),
        (EMPTY_RAW, 0),
    ])
    def test_count_parametrised(self, raw, expected_count):
        assert len(tool5_uat.parse_scenarios(raw)) == expected_count

    def test_boundary_large_number_of_scenarios(self):
        """Boundary: 50 concatenated scenarios parsed correctly."""
        block = "\n===SCENARIO===\nID: UAT-BULK-{n}\nTITLE: Bulk {n}\nTYPE: POSITIVE\nPERSONA: Admin\nPASS CRITERIA: ok\nESTIMATED TIME: 1\n"
        raw = "".join(block.format(n=i) for i in range(50))
        result = tool5_uat.parse_scenarios(raw)
        assert len(result) == 50

    def test_scenario_with_unicode_content(self):
        raw = "===SCENARIO===\nID: UAT-UNICODE-1\nTITLE: Ünïcödé tïtle\nTYPE: POSITIVE\nPASS CRITERIA: ok\nESTIMATED TIME: 2\n"
        result = tool5_uat.parse_scenarios(raw)
        assert result[0]["title"] == "Ünïcödé tïtle"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_returns_string(self, two_scenarios):
        result = tool5_uat.build_test_pack_csv(two_scenarios)
        assert isinstance(result, str)

    def test_header_row_present(self, two_scenarios):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(two_scenarios))
        assert rows[0][0] == "Scenario ID"

    def test_header_has_ten_columns(self, two_scenarios):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(two_scenarios))
        assert len(rows[0]) == 10

    def test_data_rows_count_matches_scenarios(self, two_scenarios):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(two_scenarios))
        # header + 2 data rows
        assert len(rows) == 3

    def test_scenario_id_in_csv(self, two_scenarios):
        csv_str = tool5_uat.build_test_pack_csv(two_scenarios)
        assert "UAT-STORY1-1" in csv_str
        assert "UAT-STORY1-2" in csv_str

    def test_result_column_empty_for_tester(self, two_scenarios):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(two_scenarios))
        # Result column index = 6
        for row in rows[1:]:
            assert row[6] == ""

    def test_empty_scenarios_list_produces_header_only(self):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv([]))
        assert len(rows) == 1
        assert rows[0][0] == "Scenario ID"

    def test_csv_valid_parseable_format(self, single_scenario):
        csv_str = tool5_uat.build_test_pack_csv(single_scenario)
        rows = self._parse_csv(csv_str)
        assert rows[1][0] == "UAT-STORY1-1"

    def test_missing_keys_produce_empty_strings(self):
        minimal = [{"id": "UAT-X-1"}]
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(minimal))
        data_row = rows[1]
        # title, type, persona, pass_criteria, estimated_time all empty
        assert data_row[1] == ""
        assert data_row[2] == ""

    def test_type_column_populated(self, two_scenarios):
        rows = self._parse_csv(tool5_uat.build_test_pack_csv(two_scenarios))
        types_in_csv = [r[2] for r in rows[1:]]
        assert "POSITIVE" in types_in_csv
        assert "NEGATIVE" in types_in_csv

    @pytest.mark.parametrize("scenario_data,expected_id", [
        ([{"id": "UAT-CUST-001", "title": "Customer login", "type": "POSITIVE",
           "persona": "Enterprise Admin", "pass_criteria": "ok", "estimated_time": "5"}],
         "UAT-CUST-001"),
        ([{"id": "UAT-CUST-007", "title": "Invalid email login", "type": "NEGATIVE",
           "persona": "Consumer", "pass_criteria": "error shown", "estimated_time": "3"}],
         "UAT-CUST-007"),
    ])
    def test_synthetic_data_scenarios(self, scenario_data, expected_id):
        csv_str = tool5_uat.build_test_pack_csv(scenario_data)
        assert expected_id in csv_str


# ===========================================================================
# build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd:

    def test_returns_string(self):
        result = tool5_uat.build_test_pack_md("some raw", "owner", "repo", "1.0.0")
        assert isinstance(result, str)

    def test_contains_owner_and_repo(self):
        md = tool5_uat.build_test_pack_md("content", "acme-corp", "my-repo", "2.0.0")
        assert "acme-corp/my-repo" in md

    def test_contains_version(self):
        md = tool5_uat.build_test_pack_md("content", "owner", "repo", "3.1.4")
        assert "3.1.4" in md

    def test_contains_raw_content(self):
        md = tool5_uat.build_test_pack_md("RAW_SCENARIO_CONTENT", "o", "r", "0.0.1")
        assert "RAW_SCENARIO_CONTENT" in md

    def test_contains_utc_timestamp(self):
        md = tool5_uat.build_test_pack_md("x", "o", "r", "1.0.0")
        assert "UTC" in md

    def test_contains_instructions(self):
        md = tool5_uat.build_test_pack_md("x", "o", "r", "1.0.0")
        assert "PASS" in md or "FAIL" in md or "Instructions" in md

    def test_contains_auto_generated_footer(self):
        md = tool5_uat.build_test_pack_md("x", "o", "r", "1.0.0")
        assert "Auto-generated" in md or "AI Delivery Bot" in md

    def test_empty_raw_still_renders(self):
        md = tool5_uat.build_test_pack_md("", "o", "r", "0.0.1")
        assert "o/r" in md

    def test_special_chars_in_repo_name(self):
        md = tool5_uat.build_test_pack_md("x", "my-org", "service_v2.