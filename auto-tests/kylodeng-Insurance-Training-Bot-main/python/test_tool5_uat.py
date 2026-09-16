"""
Tests for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, malformed input, empty input, partial fields
    - build_test_pack_csv(): happy path, empty scenarios, special characters, all fields present
    - build_test_pack_md(): happy path, version/owner/repo injection, timestamp format
    - get_results_csv(): happy path, missing content key (FileNotFoundError), base64 decoding

Mocks used:
    - unittest.mock.patch for `requests.get` (GitHub API calls)
    - unittest.mock.patch for `shared` module imports (call_claude, write_output_file, send_email,
      email_html, write_audit_entry, get_repo_files)
    - base64 encoding/decoding verified inline

TODOs:
    - TODO: Integration test for __main__ block requires full env var setup and live GH token
    - TODO: Test SYSTEM_GENERATE and SYSTEM_ANALYSE prompt constants with actual Claude responses
    - TODO: Test build_test_pack_md timestamp accuracy with time-freezing library (freezegun)
    - TODO: Validate CSV output can be re-parsed correctly for the full round-trip workflow
"""

import base64
import csv
import io
import json
import sys
import os
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module before importing tool5_uat so that the import
# doesn't fail due to missing secrets / network access.
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="mocked claude response")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now safe to import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: User can purchase Generations II policy with valid details
TYPE: POSITIVE
PERSONA: New policyholder aged 35
PRE-CONDITIONS:
- User is logged in
- Product catalogue is loaded
TEST DATA: product_name="Generations II", dob="1989-01-15", sum_assured=500000
STEPS:
1. Navigate to product page
2. Fill in personal details
3. Submit application
EXPECTED RESULT: Policy is issued and confirmation email sent
PASS CRITERIA: Policy number is displayed and email received within 2 minutes
ESTIMATED TIME: 10
NOTES: Depends on email service being available
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Valid cashless claim at designated mainland China hospital
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active
- Hospital is in designated list
TEST DATA: hospital="Beijing Union Medical College Hospital", claim_amount=8000
STEPS:
1. Present insurance card at hospital
2. Hospital submits pre-auth request
3. Approval received
EXPECTED RESULT: Cashless arrangement approved
PASS CRITERIA: Pre-auth approval code returned within 30 minutes
ESTIMATED TIME: 15
NOTES: Requires VIP Medical Navigation network access
===SCENARIO===
ID: UAT-STORY2-2
TITLE: Claim rejected for non-designated hospital
TYPE: NEGATIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active
TEST DATA: hospital="Random Clinic XYZ", claim_amount=500
STEPS:
1. Submit claim for non-designated hospital
EXPECTED RESULT: Claim is rejected with clear error message
PASS CRITERIA: Rejection message displayed; no payment initiated
ESTIMATED TIME: 5
NOTES: Boundary: minimum claim amount tested
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY3-1
TITLE: Maximum sum assured boundary test
TYPE: BOUNDARY
PERSONA: High-net-worth policyholder
PRE-CONDITIONS:
- System supports up to 10,000,000 sum assured
TEST DATA: sum_assured=10000000
STEPS:
1. Enter maximum allowed sum assured
2. Submit application
EXPECTED RESULT: Application accepted
PASS CRITERIA: No validation error; policy issued
ESTIMATED TIME: 8
NOTES: Boundary value analysis
"""


@pytest.fixture
def single_scenario() -> list[dict]:
    return parse_scenarios(SINGLE_SCENARIO_BLOCK)


@pytest.fixture
def two_scenarios() -> list[dict]:
    return parse_scenarios(TWO_SCENARIO_BLOCK)


@pytest.fixture
def boundary_scenario() -> list[dict]:
    return parse_scenarios(BOUNDARY_SCENARIO_BLOCK)


# ---------------------------------------------------------------------------
# parse_scenarios — happy path
# ---------------------------------------------------------------------------


class TestParseScenarios:
    def test_single_scenario_returns_one_item(self, single_scenario):
        assert len(single_scenario) == 1

    def test_single_scenario_id_extracted(self, single_scenario):
        assert single_scenario[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_extracted(self, single_scenario):
        assert single_scenario[0]["title"] == "User can purchase Generations II policy with valid details"

    def test_single_scenario_type_extracted(self, single_scenario):
        assert single_scenario[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_extracted(self, single_scenario):
        assert single_scenario[0]["persona"] == "New policyholder aged 35"

    def test_single_scenario_pass_criteria_extracted(self, single_scenario):
        assert "Policy number is displayed" in single_scenario[0]["pass_criteria"]

    def test_single_scenario_estimated_time_extracted(self, single_scenario):
        assert single_scenario[0]["estimated_time"] == "10"

    def test_single_scenario_raw_preserved(self, single_scenario):
        assert "UAT-STORY1-1" in single_scenario[0]["raw"]
        assert "STEPS:" in single_scenario[0]["raw"]

    def test_two_scenarios_returns_two_items(self, two_scenarios):
        assert len(two_scenarios) == 2

    def test_two_scenarios_ids_correct(self, two_scenarios):
        ids = [s["id"] for s in two_scenarios]
        assert "UAT-STORY2-1" in ids
        assert "UAT-STORY2-2" in ids

    def test_two_scenarios_types(self, two_scenarios):
        types_ = {s["id"]: s["type"] for s in two_scenarios}
        assert types_["UAT-STORY2-1"] == "POSITIVE"
        assert types_["UAT-STORY2-2"] == "NEGATIVE"

    def test_boundary_scenario_type(self, boundary_scenario):
        assert boundary_scenario[0]["type"] == "BOUNDARY"

    # -------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """A block with no ===SCENARIO=== delimiter and no ID should be skipped."""
        raw = "Some random text without any delimiter or ID field"
        result = parse_scenarios(raw)
        assert result == []

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: orphan scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_leading_and_trailing_delimiters(self):
        """Content before first ===SCENARIO=== (empty split head) is ignored."""
        raw = SINGLE_SCENARIO_BLOCK + "===SCENARIO===\nID: UAT-X-1\nTITLE: Extra\n"
        result = parse_scenarios(raw)
        assert any(s["id"] == "UAT-STORY1-1" for s in result)
        assert any(s["id"] == "UAT-X-1" for s in result)

    def test_partial_fields_still_parsed(self):
        """Scenario with only ID should be included; other fields absent."""
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert result[0].get("title") is None

    def test_extra_whitespace_in_id_is_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-99  \nTITLE: Whitespace test\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-99"

    def test_multiple_colons_in_title(self):
        """Lines with multiple colons should not break field extraction."""
        raw = "===SCENARIO===\nID: UAT-COL-1\nTITLE: Check URL: https://example.com\n"
        result = parse_scenarios(raw)
        # TITLE: replacement is prefix-only, so the rest is kept
        assert "https://example.com" in result[0]["title"]

    def test_only_whitespace_block_is_ignored(self):
        raw = "===SCENARIO===\n   \n\t\n===SCENARIO===\nID: UAT-VALID-1\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-VALID-1"

    def test_large_number_of_scenarios(self):
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Bulk {i}\nTYPE: POSITIVE\n"
            for i in range(50)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 50

    @pytest.mark.parametrize("raw_input", [
        "===SCENARIO===\nID: UAT-A-1\nTYPE: POSITIVE\n",
        "===SCENARIO===\nID: UAT-B-1\nTYPE: NEGATIVE\n",
        "===SCENARIO===\nID: UAT-C-1\nTYPE: BOUNDARY\n",
    ])
    def test_valid_scenario_types_accepted(self, raw_input):
        result = parse_scenarios(raw_input)
        assert len(result) == 1

    def test_synthetic_data_in_raw_preserved(self):
        """Ensure synthetic test data (e.g. Generations II product) appears in raw."""
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "Generations II" in result[0]["raw"]


# ---------------------------------------------------------------------------
# build_test_pack_csv
# ---------------------------------------------------------------------------


class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self, single_scenario):
        result = build_test_pack_csv(single_scenario)
        assert isinstance(result, str)

    def test_header_row_present(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[0][0] == "Scenario ID"
        assert rows[0][1] == "Title"

    def test_header_has_correct_column_count(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert len(rows[0]) == 10

    def test_data_row_count_matches_scenarios(self, two_scenarios):
        rows = self._parse_csv(build_test_pack_csv(two_scenarios))
        # 1 header + 2 data rows
        assert len(rows) == 3

    def test_scenario_id_in_first_column(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[1][0] == "UAT-STORY1-1"

    def test_title_in_second_column(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert "Generations II" in rows[1][1]

    def test_type_in_third_column(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[1][2] == "POSITIVE"

    def test_result_column_is_empty(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        # Column index 6 = "Result (PASS/FAIL/BLOCKED)"
        assert rows[1][6] == ""

    def test_tester_column_is_empty(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[1][7] == ""

    def test_defect_ref_column_is_empty(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[1][9] == ""

    def test_empty_scenarios_list_produces_only_header(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1
        assert rows[0][0] == "Scenario ID"

    def test_missing_fields_produce_empty_strings(self):
        minimal = [{"id": "UAT-MIN-1", "raw": "something"}]
        rows = self._parse_csv(build_test_pack_csv(minimal))
        assert rows[1][0] == "UAT-MIN-1"
        assert rows[1][1] == ""  # title missing → empty

    def test_special_characters_in_title(self):
        special = [{
            "id": "UAT-SC-1",
            "title": 'Title with "quotes", commas, and newlines\n',
            "type": "POSITIVE",
            "persona": "",
            "pass_criteria": "",
            "estimated_time": "",
        }]
        csv_str = build_test_pack_csv(special)
        rows = self._parse_csv(csv_str)
        # csv module should handle quoting automatically
        assert "quotes" in rows[1][1]

    def test_boundary_scenario_rows(self, boundary_scenario):
        rows = self._parse_csv(build_test_pack_csv(boundary_scenario))
        assert rows[1][2] == "BOUNDARY"

    @pytest.mark.parametrize("n", [1, 5, 10, 25])
    def