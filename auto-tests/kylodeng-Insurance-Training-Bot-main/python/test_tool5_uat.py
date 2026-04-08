"""
Test module for tool5_uat.py

What is tested:
    - parse_scenarios(): parsing Claude's raw scenario output into structured dicts
    - build_test_pack_csv(): CSV generation from scenario list
    - build_test_pack_md(): Markdown test pack document generation
    - get_results_csv(): Fetching and decoding CSV from GitHub API

Mocks used:
    - requests.get (for get_results_csv GitHub API calls)
    - shared.call_claude (not directly tested here but imported via module)
    - base64.b64decode (indirectly via requests mock)
    - os.environ (patched for __main__ block tests)

TODOs:
    - TODO: Integration test for __main__ block requires full env setup (GH tokens, Claude API)
    - TODO: Test call_claude integration in generate/analyse modes requires Claude API key
    - TODO: Test write_output_file requires OUTPUT_REPO / GH_HEADERS secrets
    - TODO: Test send_email requires SMTP or SES configuration
    - TODO: Test get_repo_files requires GitHub API access and valid repo
    - TODO: Test write_audit_entry requires output repo write access
"""

import base64
import csv
import io
import json
import sys
import os

import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without a real 'shared' module
# ---------------------------------------------------------------------------

# We create a minimal fake 'shared' module so the import in tool5_uat doesn't
# blow up when the real secrets / env vars are absent.
import types

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda s: s
_shared_stub.call_claude = MagicMock(return_value="stubbed")
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

# We need to import via path because the file lives under .github/scripts/
script_path = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
)

# Fallback: if running from repo root the path above works; if already in
# .github/scripts just import directly.
try:
    import tool5_uat as _module
except ModuleNotFoundError:
    import importlib.util

    spec = importlib.util.spec_from_file_location("tool5_uat", script_path)
    _module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_module)

parse_scenarios = _module.parse_scenarios
build_test_pack_csv = _module.build_test_pack_csv
build_test_pack_md = _module.build_test_pack_md
get_results_csv = _module.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-001
TITLE: Verify lifelong protection activation for Generations II product
TYPE: POSITIVE
PERSONA: New policyholder (individual, 35-year-old)
PRE-CONDITIONS:
- Policy is active
- Premium payment is up to date
TEST DATA: product_name=Generations II, dob=1989-01-15, sum_assured=500000
STEPS:
1. Log in to policyholder portal
2. Navigate to policy summary page
3. Verify protection status shows "Active"
EXPECTED RESULT: Protection status displays as Active with correct sum assured
PASS CRITERIA: Status = Active AND sum_assured = 500000
ESTIMATED TIME: 5
NOTES: Relates to Generations II product brochure feature set
"""

TWO_SCENARIOS_BLOCK = """\
===SCENARIO===
ID: UAT-HEALTH-001
TITLE: Cashless hospital admission – happy path
TYPE: POSITIVE
PERSONA: Insured member
PRE-CONDITIONS:
- Member has active health policy
TEST DATA: hospital=Shanghai General, policy_no=HK-123456
STEPS:
1. Present insurance card at reception
2. Staff verifies policy online
3. Admission is approved
EXPECTED RESULT: Admission approved without upfront payment
PASS CRITERIA: Admission approved = YES
ESTIMATED TIME: 10
NOTES: Uses Global Network Hospital List for Cashless Arrangement

===SCENARIO===
ID: UAT-HEALTH-002
TITLE: Cashless admission with expired policy – negative
TYPE: NEGATIVE
PERSONA: Insured member (expired policy)
PRE-CONDITIONS:
- Policy renewal date has passed
TEST DATA: hospital=Shanghai General, policy_no=HK-EXPIRED
STEPS:
1. Present insurance card at reception
2. Staff verifies policy online
EXPECTED RESULT: System rejects cashless request
PASS CRITERIA: Error message displayed = "Policy expired"
ESTIMATED TIME: 5
NOTES: [TESTER: verify exact error message]
"""

EMPTY_BLOCK = ""
NO_SCENARIO_DELIMITER = "This is just plain text without any scenario delimiters."


@pytest.fixture()
def single_scenario():
    return parse_scenarios(SINGLE_SCENARIO_BLOCK)


@pytest.fixture()
def two_scenarios():
    return parse_scenarios(TWO_SCENARIOS_BLOCK)


# ===========================================================================
# parse_scenarios() tests
# ===========================================================================


class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_returns_list(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert isinstance(result, list)

    def test_single_scenario_count(self, single_scenario):
        assert len(single_scenario) == 1

    def test_single_scenario_id(self, single_scenario):
        assert single_scenario[0]["id"] == "UAT-GEN2-001"

    def test_single_scenario_title(self, single_scenario):
        assert single_scenario[0]["title"] == (
            "Verify lifelong protection activation for Generations II product"
        )

    def test_single_scenario_type(self, single_scenario):
        assert single_scenario[0]["type"] == "POSITIVE"

    def test_single_scenario_persona(self, single_scenario):
        assert single_scenario[0]["persona"] == "New policyholder (individual, 35-year-old)"

    def test_single_scenario_pass_criteria(self, single_scenario):
        assert single_scenario[0]["pass_criteria"] == (
            "Status = Active AND sum_assured = 500000"
        )

    def test_single_scenario_estimated_time(self, single_scenario):
        assert single_scenario[0]["estimated_time"] == "5"

    def test_single_scenario_raw_present(self, single_scenario):
        assert "raw" in single_scenario[0]
        assert len(single_scenario[0]["raw"]) > 0

    def test_two_scenarios_count(self, two_scenarios):
        assert len(two_scenarios) == 2

    def test_two_scenarios_ids(self, two_scenarios):
        ids = [s["id"] for s in two_scenarios]
        assert "UAT-HEALTH-001" in ids
        assert "UAT-HEALTH-002" in ids

    def test_two_scenarios_types(self, two_scenarios):
        types = {s["id"]: s["type"] for s in two_scenarios}
        assert types["UAT-HEALTH-001"] == "POSITIVE"
        assert types["UAT-HEALTH-002"] == "NEGATIVE"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios(EMPTY_BLOCK)
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        # A block with no ===SCENARIO=== delimiter and no ID → nothing returned
        result = parse_scenarios(NO_SCENARIO_DELIMITER)
        assert result == []

    def test_block_without_id_is_excluded(self):
        """A block that has delimiter but no ID line must be excluded."""
        raw = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
"""
        result = parse_scenarios(raw)
        assert result == []

    def test_multiple_scenarios_raw_field_contains_content(self, two_scenarios):
        for s in two_scenarios:
            assert len(s["raw"]) > 10

    def test_scenario_without_optional_fields_still_parsed(self):
        """Minimal scenario with only ID should still be returned."""
        raw = """\
===SCENARIO===
ID: UAT-MIN-001
"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-001"
        # Optional fields should not be present (no default injection)
        assert result[0].get("title", "") == ""

    def test_leading_text_before_first_delimiter_ignored(self):
        """Any text before the first ===SCENARIO=== is discarded."""
        raw = "Preamble text that should be ignored\n" + SINGLE_SCENARIO_BLOCK
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-GEN2-001"

    def test_boundary_scenario_type_parsed(self):
        raw = """\
===SCENARIO===
ID: UAT-BOUND-001
TITLE: Max sum assured boundary test
TYPE: BOUNDARY
PERSONA: Underwriter
PASS CRITERIA: System accepts maximum value
ESTIMATED TIME: 3
"""
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_scenario_with_whitespace_only_block_ignored(self):
        raw = "===SCENARIO===\n   \n\t\n===SCENARIO===\nID: UAT-WS-001\n"
        result = parse_scenarios(raw)
        # Only the second block (with ID) should survive
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-001"

    @pytest.mark.parametrize("scenario_id,expected_type", [
        ("UAT-HEALTH-001", "POSITIVE"),
        ("UAT-HEALTH-002", "NEGATIVE"),
    ])
    def test_parametrised_scenario_types(self, scenario_id, expected_type, two_scenarios):
        scenario = next(s for s in two_scenarios if s["id"] == scenario_id)
        assert scenario["type"] == expected_type


# ===========================================================================
# build_test_pack_csv() tests
# ===========================================================================


class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_returns_string(self, single_scenario):
        result = build_test_pack_csv(single_scenario)
        assert isinstance(result, str)

    def test_header_row_present(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_row_count_matches_scenarios_plus_header(self, two_scenarios):
        rows = self._parse_csv(build_test_pack_csv(two_scenarios))
        # header + 2 scenario rows
        assert len(rows) == 3

    def test_scenario_id_in_first_data_column(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[1][0] == "UAT-GEN2-001"

    def test_result_column_empty_for_new_csv(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        # Column index 6 = "Result (PASS/FAIL/BLOCKED)"
        assert rows[1][6] == ""

    def test_tester_column_empty_for_new_csv(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[1][7] == ""

    def test_defect_ref_column_empty_for_new_csv(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert rows[1][9] == ""

    def test_empty_scenario_list_produces_only_header(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1
        assert rows[0][0] == "Scenario ID"

    def test_all_ten_columns_present(self, single_scenario):
        rows = self._parse_csv(build_test_pack_csv(single_scenario))
        assert len(rows[0]) == 10

    def test_scenario_without_optional_keys_writes_empty_strings(self):
        minimal = [{"id": "UAT-MIN-999", "raw": "raw text"}]
        rows = self._parse_csv(build_test_pack_csv(minimal))
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-999"
        # title, type, persona, pass_criteria, estimated_time all empty
        assert data_row[1] == ""
        assert data_row[2] == ""
        assert data_row[3] == ""
        assert data_row[4] == ""
        assert data_row[5] == ""

    def test_two_scenarios_data_rows(self, two_scenarios):
        rows = self._parse_csv(build_test_pack_csv(two_scenarios))
        ids = [row[0] for row in rows[1:]]
        assert "UAT-HEALTH-001" in ids
        assert "UAT-HEALTH-002" in ids

    def test_csv_is_valid_parseable_csv(self, two_scenarios):
        csv_str = build_test_pack_csv(two_scenarios)
        try:
            rows = list(csv.reader(io.StringIO(csv_str)))
            assert len(rows) > 0
        except csv.Error:
            pytest.fail("build_test_pack_csv returned invalid CSV")

    def test_fields_with_commas_are_quoted(self):
        scenario_with_comma = [{
            "id": "UAT-COMMA-001",
            "title": "Title with, comma",
            "type": "POSITIVE",
            "persona": "User, Admin",
            "pass_criteria": "value = 1, 2",
            "estimated_time": "5",
        }]
        rows = self._parse_csv(build_test_pack_csv(scenario_with_comma))
        assert rows[1][1] == "Title with, comma"
        assert rows[1][3] == "User, Admin"


# ===========================================================================
# build_test_pack_md() tests
# ===========================================================================


class TestBuildTestPackMd:
    """Tests for build_test_pack_md()."""

    def test_returns_string(self):
        result = build_test_pack_md("raw scenario content", "acme", "myrepo