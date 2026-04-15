"""
Test suite for tool5_uat.py
============================
What is tested:
  - parse_scenarios(): happy path, edge cases, malformed input, boundary values
  - build_test_pack_csv(): structure, headers, data rows, empty input
  - build_test_pack_md(): output format, interpolation of version/owner/repo
  - get_results_csv(): successful fetch, missing file (FileNotFoundError), API error shapes

Mocks used:
  - requests.get (via unittest.mock.patch) — never calls real GitHub API
  - shared module functions (call_claude, get_repo_files, write_output_file,
    send_email, write_audit_entry) are patched at import boundaries
  - base64 decoding is exercised directly (no mock needed)

TODOs:
  - TODO: Integration tests for __main__ block require full env-var wiring and
    mocked GitHub Actions context — stubbed below.
  - TODO: Tests for SYSTEM_GENERATE / SYSTEM_ANALYSE prompt correctness require
    an LLM judge or golden-file comparison — stubbed below.
  - TODO: build_test_pack_md timestamp is non-deterministic; freeze time with
    freezegun if exact string matching is needed.
"""

import base64
import csv
import io
import json
import sys
import os
import types
import importlib
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so tool5_uat can be imported without
# the real shared.py being present or having side effects.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.clean_json = MagicMock(side_effect=lambda x: x)
    mod.call_claude = MagicMock(return_value="mocked claude response")
    mod.get_repo_files = MagicMock(return_value={})
    mod.write_output_file = MagicMock(return_value=None)
    mod.send_email = MagicMock(return_value=None)
    mod.email_html = MagicMock(return_value="<html/>")
    mod.write_audit_entry = MagicMock(return_value=None)
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    mod.GH_HEADERS = {"Authorization": "Bearer fake-token"}
    mod.GH_API = "https://api.github.com"
    return mod


# Inject stub before the module under test is imported
shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", shared_stub)

# Also stub requests at top level so the module-level import doesn't fail in
# environments without it installed (it is re-patched per test where needed).
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.get = MagicMock()
    sys.modules["requests"] = requests_stub

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(script_dir))

import tool5_uat as uat


# ===========================================================================
# Helpers / Fixtures
# ===========================================================================

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-FEAT-1
TITLE: Basic login with valid credentials
TYPE: POSITIVE
PERSONA: End User
PRE-CONDITIONS:
- User account exists
TEST DATA: alice.chen@example.com / Password1!
STEPS:
1. Navigate to /login
2. Enter valid email and password
3. Click Sign In
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-FEAT-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Enterprise User
PASS CRITERIA: User logged in
ESTIMATED TIME: 3
NOTES: -
===SCENARIO===
ID: UAT-FEAT-2
TITLE: Invalid email rejected
TYPE: NEGATIVE
PERSONA: Anonymous
PASS CRITERIA: Error displayed
ESTIMATED TIME: 2
NOTES: Uses invalid-email test data
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-FEAT-3
TITLE: Max revenue boundary
TYPE: BOUNDARY
PERSONA: Finance Admin
PASS CRITERIA: Value saved
ESTIMATED TIME: 4
NOTES: annual_revenue=500000
"""


@pytest.fixture()
def single_parsed():
    return uat.parse_scenarios(MINIMAL_SCENARIO_BLOCK)


@pytest.fixture()
def two_parsed():
    return uat.parse_scenarios(TWO_SCENARIO_BLOCK)


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:
    """Tests for parse_scenarios()."""

    # --- Happy path ---

    def test_returns_list(self, single_parsed):
        assert isinstance(single_parsed, list)

    def test_single_scenario_count(self, single_parsed):
        assert len(single_parsed) == 1

    def test_id_parsed(self, single_parsed):
        assert single_parsed[0]["id"] == "UAT-FEAT-1"

    def test_title_parsed(self, single_parsed):
        assert single_parsed[0]["title"] == "Basic login with valid credentials"

    def test_type_parsed(self, single_parsed):
        assert single_parsed[0]["type"] == "POSITIVE"

    def test_persona_parsed(self, single_parsed):
        assert single_parsed[0]["persona"] == "End User"

    def test_pass_criteria_parsed(self, single_parsed):
        assert "Dashboard loads" in single_parsed[0]["pass_criteria"]

    def test_estimated_time_parsed(self, single_parsed):
        assert single_parsed[0]["estimated_time"] == "5"

    def test_raw_field_present(self, single_parsed):
        assert "raw" in single_parsed[0]
        assert "UAT-FEAT-1" in single_parsed[0]["raw"]

    def test_two_scenarios_parsed(self, two_parsed):
        assert len(two_parsed) == 2

    def test_two_scenarios_ids(self, two_parsed):
        ids = [s["id"] for s in two_parsed]
        assert "UAT-FEAT-1" in ids
        assert "UAT-FEAT-2" in ids

    def test_negative_type_captured(self, two_parsed):
        negative = next(s for s in two_parsed if s["id"] == "UAT-FEAT-2")
        assert negative["type"] == "NEGATIVE"

    def test_boundary_type_captured(self):
        result = uat.parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    # --- Edge cases ---

    def test_empty_string_returns_empty_list(self):
        assert uat.parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # No ===SCENARIO=== → no parseable blocks with IDs
        result = uat.parse_scenarios("This is just free text without any delimiter.")
        assert result == []

    def test_delimiter_only_no_id_skipped(self):
        # Block with delimiter but no ID line should be skipped
        raw = "===SCENARIO===\nTITLE: Something\nTYPE: POSITIVE\n"
        assert uat.parse_scenarios(raw) == []

    def test_whitespace_only_block_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-X-1\nTITLE: T\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"

    def test_missing_optional_fields_do_not_raise(self):
        # Only ID present
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = uat.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        # Optional keys should be absent (not defaulted to wrong value)
        assert "title" not in s or s.get("title") is not None  # no crash

    def test_extra_whitespace_in_id_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE: Whitespace Test\n"
        result = uat.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"

    def test_extra_whitespace_in_title_stripped(self):
        raw = "===SCENARIO===\nID: UAT-WS-2\nTITLE:   Padded Title   \n"
        result = uat.parse_scenarios(raw)
        assert result[0]["title"] == "Padded Title"

    def test_multiple_colons_in_value(self):
        """Title containing colons should not be truncated."""
        raw = "===SCENARIO===\nID: UAT-C-1\nTITLE: Login: Step 1: Enter URL\n"
        result = uat.parse_scenarios(raw)
        assert result[0]["title"] == "Login: Step 1: Enter URL"

    def test_large_number_of_scenarios(self):
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
            for i in range(50)
        )
        result = uat.parse_scenarios(blocks)
        assert len(result) == 50

    # --- Negative / bad input ---

    def test_none_raises_attribute_error(self):
        with pytest.raises(AttributeError):
            uat.parse_scenarios(None)  # type: ignore

    def test_integer_input_raises(self):
        with pytest.raises(AttributeError):
            uat.parse_scenarios(42)  # type: ignore

    # --- Synthetic data referenced in scenarios ---

    @pytest.mark.parametrize("email,scenario_id", [
        ("alice.chen@example.com", "UAT-CUST-1"),
        ("invalid-email", "UAT-CUST-2"),
        ("grace.kim@example.com", "UAT-CUST-3"),
    ])
    def test_synthetic_email_in_test_data_field(self, email, scenario_id):
        raw = (
            f"===SCENARIO===\n"
            f"ID: {scenario_id}\n"
            f"TITLE: Customer scenario\n"
            f"TYPE: POSITIVE\n"
            f"TEST DATA: {email}\n"
        )
        result = uat.parse_scenarios(raw)
        assert result[0]["raw"].count(email) == 1


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    EXPECTED_HEADERS = [
        "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
        "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
    ]

    def _read_csv(self, csv_string: str):
        return list(csv.reader(io.StringIO(csv_string)))

    # --- Happy path ---

    def test_returns_string(self, two_parsed):
        result = uat.build_test_pack_csv(two_parsed)
        assert isinstance(result, str)

    def test_header_row_correct(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        assert rows[0] == self.EXPECTED_HEADERS

    def test_row_count_matches_scenarios(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        # header + 2 data rows
        assert len(rows) == 3

    def test_scenario_id_in_csv(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        ids = [r[0] for r in rows[1:]]
        assert "UAT-FEAT-1" in ids
        assert "UAT-FEAT-2" in ids

    def test_title_in_csv(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        titles = [r[1] for r in rows[1:]]
        assert "Happy path login" in titles

    def test_result_column_empty_initially(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        for row in rows[1:]:
            assert row[6] == ""  # Result column blank

    def test_tester_column_empty_initially(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        for row in rows[1:]:
            assert row[7] == ""  # Tester column blank

    def test_defect_ref_column_empty_initially(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        for row in rows[1:]:
            assert row[9] == ""  # Defect Ref column blank

    def test_each_row_has_ten_columns(self, two_parsed):
        rows = self._read_csv(uat.build_test_pack_csv(two_parsed))
        for row in rows:
            assert len(row) == 10

    # --- Edge cases ---

    def test_empty_scenarios_list(self):
        result = uat.build_test_pack_csv([])
        rows = self._read_csv(result)
        assert len(rows) == 1  # header only
        assert rows[0] == self.EXPECTED_HEADERS

    def test_scenario_with_missing_fields(self):
        """Scenarios lacking optional keys should not raise."""
        scenarios = [{"id": "UAT-MISS-1"}]  # only id present
        result = uat.build_test_pack_csv(scenarios)
        rows = self._read_csv(result)
        assert rows[1][0] == "UAT-MISS-1"
        assert rows[1][1] == ""  # title missing → empty string

    def test_scenario_with_commas_in_title(self):
        """CSV writer must quote fields containing commas."""
        scenarios = [{
            "id": "UAT-COMMA-1",
            "title": "Login, then navigate, then logout",
            "type": "POSITIVE",
            "persona": "User",
            "pass_criteria": "OK",
            "estimated_time": "5",
        }]
        result = uat.build_test_pack_csv(scenarios)
        rows = self._read_csv(result)
        assert rows[1][1] == "Login, then navigate, then logout"

    def test_scenario_with_newline_in_pass_criteria(self):
        """CSV writer must handle embedded newlines gracefully."""
        scenarios = [{
            "id": "UAT-NL-1",
            "title": "Newline test",
            "pass_criteria": "Line1\nLine2",
        }]
        # Should not raise
        result = uat.build_test_pack_csv(scenarios)
        assert "UAT-NL-1" in result

    def test_large_scenario_list_performance(self):
        scenarios = [
            {"id":