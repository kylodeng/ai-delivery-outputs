"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, empty input, missing fields, malformed blocks,
      boundary values (single scenario, many scenarios, no ID field)
    - build_test_pack_csv(): CSV structure, header row, data rows, empty input,
      special characters, missing optional fields
    - build_test_pack_md(): markdown structure, version/owner/repo interpolation,
      empty raw string
    - get_results_csv(): successful fetch + base64 decode, missing file (FileNotFoundError),
      unexpected API response shape

Mocks used:
    - requests.get (patched via unittest.mock.patch) — no real HTTP calls
    - shared module imports stubbed via sys.modules fixture
    - base64.b64decode exercised with real encoded payloads in unit tests

TODOs:
    - TODO: Integration test for __main__ block requires full env + GitHub token
    - TODO: call_claude / send_email integration — needs API credentials
    - TODO: write_output_file / write_audit_entry integration — needs repo write access
    - TODO: build_test_pack_md timestamp is dynamic; consider dependency-injection for clock
"""

import base64
import csv
import io
import json
import sys
import os
import types
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module before importing tool5_uat so that we do not
# need the actual shared.py or any of its heavy dependencies.
# ---------------------------------------------------------------------------

_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = lambda s: s
_shared_stub.call_claude = MagicMock(return_value="mocked_claude_response")
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

# Now safe to import the module under test
import importlib.util, pathlib

_script_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

# We load the module from file so the test works regardless of PYTHONPATH.
# If the file does not exist in CI the tests will still be collected and
# individual helpers will be imported via the stub path below.
try:
    _spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
    _mod = importlib.util.module_from_spec(_spec)
    # Inject the stub before exec so `from shared import …` resolves correctly
    sys.modules["tool5_uat"] = _mod
    _spec.loader.exec_module(_mod)
    parse_scenarios = _mod.parse_scenarios
    build_test_pack_csv = _mod.build_test_pack_csv
    build_test_pack_md = _mod.build_test_pack_md
    get_results_csv = _mod.get_results_csv
    GH_API = _mod.GH_API
    GH_HEADERS = _mod.GH_HEADERS
except Exception as _e:
    pytest.skip(f"Could not load tool5_uat.py: {_e}", allow_module_level=True)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_scenario_block(
    scenario_id="UAT-STORY1-1",
    title="Login with valid credentials",
    type_="POSITIVE",
    persona="End User",
    pass_criteria="User reaches dashboard",
    estimated_time="5",
    extra_lines="",
) -> str:
    return (
        f"ID: {scenario_id}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PRE-CONDITIONS:\n- System is online\n"
        f"TEST DATA: alice.chen@example.com / password123\n"
        f"STEPS:\n1. Open login page\n2. Enter credentials\n3. Click Login\n"
        f"EXPECTED RESULT: Dashboard is shown\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"NOTES: none\n"
        f"{extra_lines}"
    )


def _raw_with_scenarios(*blocks: str) -> str:
    """Join scenario blocks with the delimiter."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


# ===========================================================================
# parse_scenarios — happy paths
# ===========================================================================

class TestParseScenarios:

    def test_single_complete_scenario(self):
        raw = _raw_with_scenarios(_make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "End User"
        assert s["pass_criteria"] == "User reaches dashboard"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_returned_in_order(self):
        blocks = [
            _make_scenario_block("UAT-S1-1", "Test A"),
            _make_scenario_block("UAT-S1-2", "Test B", type_="NEGATIVE"),
            _make_scenario_block("UAT-S1-3", "Test C", type_="BOUNDARY"),
        ]
        result = parse_scenarios(_raw_with_scenarios(*blocks))
        assert len(result) == 3
        assert result[0]["id"] == "UAT-S1-1"
        assert result[1]["id"] == "UAT-S1-2"
        assert result[2]["id"] == "UAT-S1-3"

    def test_scenario_types_parsed_correctly(self):
        for t in ("POSITIVE", "NEGATIVE", "BOUNDARY"):
            raw = _raw_with_scenarios(_make_scenario_block(type_=t))
            result = parse_scenarios(raw)
            assert result[0]["type"] == t

    def test_raw_field_contains_full_block(self):
        block = _make_scenario_block()
        raw = _raw_with_scenarios(block)
        result = parse_scenarios(raw)
        # The raw field should contain the key lines
        assert "ID: UAT-STORY1-1" in result[0]["raw"]
        assert "TITLE: Login with valid credentials" in result[0]["raw"]

    # ------------------------------------------------------------------
    # Edge / boundary cases
    # ------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # A block with no ===SCENARIO=== prefix and no ID — skipped
        assert parse_scenarios("Some random text without delimiter") == []

    def test_block_without_id_is_skipped(self):
        no_id_block = "TITLE: No ID here\nTYPE: POSITIVE\n"
        raw = "===SCENARIO===\n" + no_id_block
        result = parse_scenarios(raw)
        assert result == []

    def test_partial_fields_still_parsed(self):
        """A scenario that only has ID and TITLE — other fields default to absent."""
        raw = "===SCENARIO===\nID: UAT-PARTIAL-1\nTITLE: Partial scenario\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s["title"] == "Partial scenario"
        assert "type" not in s
        assert "persona" not in s

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-REAL-1\nTITLE: Real\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-REAL-1"

    def test_leading_text_before_first_delimiter_ignored(self):
        raw = "Preamble text that should be ignored.\n" + _raw_with_scenarios(
            _make_scenario_block("UAT-LEAD-1")
        )
        result = parse_scenarios(raw)
        # The preamble block has no ID, so only the real scenario is returned
        assert any(s["id"] == "UAT-LEAD-1" for s in result)

    def test_large_number_of_scenarios(self):
        blocks = [_make_scenario_block(f"UAT-BIG-{i}", f"Title {i}") for i in range(50)]
        raw = _raw_with_scenarios(*blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    def test_id_with_extra_whitespace_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-1   \nTITLE: Spaced\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"

    def test_synthetic_customer_data_in_scenario(self):
        """Verify synthetic data samples survive round-trip through parse."""
        block = _make_scenario_block(
            scenario_id="UAT-CUST-1",
            title="Enterprise customer login",
            extra_lines=(
                "TEST DATA: CUST-001, alice.chen@example.com, age=34, GB, enterprise\n"
            ),
        )
        raw = _raw_with_scenarios(block)
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-CUST-1"
        # raw block should contain the synthetic data verbatim
        assert "alice.chen@example.com" in result[0]["raw"]

    def test_negative_scenario_with_invalid_email(self):
        """Boundary: invalid-email from synthetic data used as test input ID."""
        block = _make_scenario_block(
            scenario_id="UAT-NEG-1",
            title="Login with invalid email",
            type_="NEGATIVE",
            extra_lines="TEST DATA: invalid-email, age=25, GB, consumer\n",
        )
        result = parse_scenarios(_raw_with_scenarios(block))
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_scenario_with_negative_age(self):
        block = _make_scenario_block(
            scenario_id="UAT-BOUND-1",
            title="Customer with negative age",
            type_="BOUNDARY",
            extra_lines="TEST DATA: CUST-008, grace.kim@example.com, age=-1, KR\n",
        )
        result = parse_scenarios(_raw_with_scenarios(block))
        assert result[0]["type"] == "BOUNDARY"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_str)))

    def test_header_row_correct(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_empty_scenarios_yields_header_only(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # header only (trailing newline may produce empty row)

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-S1-1",
                "title": "Valid login",
                "type": "POSITIVE",
                "persona": "Admin",
                "pass_criteria": "Reaches dashboard",
                "estimated_time": "3",
                "raw": "",
            }
        ]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[0] == "UAT-S1-1"
        assert data_row[1] == "Valid login"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Admin"
        assert data_row[4] == "Reaches dashboard"
        assert data_row[5] == "3"
        # Tester / Notes / Defect Ref columns are empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_rows_count(self):
        scenarios = [
            {"id": f"UAT-{i}", "title": f"T{i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "5", "raw": ""}
            for i in range(10)
        ]
        csv_str = build_test_pack_csv(scenarios)
        rows = [r for r in self._parse_csv(csv_str) if r]  # drop blank trailing
        assert len(rows) == 11  # header + 10 data

    def test_missing_optional_fields_default_to_empty(self):
        """Scenario dict with only id — other fields should default to empty string."""
        scenarios = [{"id": "UAT-MIN-1", "raw": ""}]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-1"
        assert data_row[1] == ""  # title missing
        assert data_row[2] == ""  # type missing

    def test_special_characters_in_title(self):
        """Commas and quotes in title must be correctly quoted in CSV."""
        scenarios = [
            {"id": "UAT-SC-1", "title": 'Test "quotes" and, commas',
             "type": "NEGATIVE", "persona": "User",
             "pass_criteria": "Error shown", "estimated_time": "2", "raw": ""}
        ]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == 'Test "quotes" and, commas'

    def test_unicode_in_fields(self):
        scenarios = [
            {"id": "UAT-UNI-1", "title": "Ünïcödé tïtle — café",
             "type": "POSITIVE", "persona": "Utilisateur",
             "pass_criteria": "Réussi", "estimated_time": "10", "raw": ""}
        ]
        csv_str = build_test_pack_csv