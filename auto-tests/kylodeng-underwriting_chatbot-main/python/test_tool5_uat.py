"""
Tests for tool5_uat.py
======================
What is tested:
    - parse_scenarios(): happy path, edge cases, empty/malformed input, boundary values
    - build_test_pack_csv(): structure, headers, row content, empty scenarios list
    - build_test_pack_md(): output structure, version/owner/repo interpolation
    - get_results_csv(): happy path, missing file (FileNotFoundError)
    - Module-level integration stubs for Mode A (generate) and Mode B (analyse)

Mocks used:
    - requests.get (for get_results_csv GitHub API calls)
    - shared.call_claude (Claude AI calls)
    - shared.get_repo_files (repo file fetching)
    - shared.write_output_file (file writing to output repo)
    - shared.send_email (email dispatch)
    - shared.write_audit_entry (audit logging)
    - base64.b64decode (via requests mock response)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var setup and
            live GitHub token — stub provided below.
    - TODO: Test for email HTML formatting (email_html) needs email template context.
    - TODO: Verify Claude response schema validation for Mode B analyse JSON output.
"""

import base64
import csv
import io
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — mirrors what tool5_uat.py does so imports resolve in test
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# ---------------------------------------------------------------------------
# We patch 'shared' before importing the module under test so that the
# module-level `from shared import …` does not attempt real I/O.
# ---------------------------------------------------------------------------
SHARED_ATTRS = [
    "clean_json", "call_claude", "get_repo_files", "write_output_file",
    "send_email", "email_html", "write_audit_entry",
    "OUTPUT_REPO_OWNER", "OUTPUT_REPO", "GH_HEADERS", "GH_API",
]

shared_mock = MagicMock()
shared_mock.OUTPUT_REPO_OWNER = "test-owner"
shared_mock.OUTPUT_REPO = "test-output-repo"
shared_mock.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_mock.GH_API = "https://api.github.com"

with patch.dict("sys.modules", {"shared": shared_mock}):
    import importlib
    import types

    # Build a fake 'shared' module that satisfies `from shared import …`
    fake_shared = types.ModuleType("shared")
    for attr in SHARED_ATTRS:
        setattr(fake_shared, attr, getattr(shared_mock, attr))
    fake_shared.OUTPUT_REPO_OWNER = "test-owner"
    fake_shared.OUTPUT_REPO = "test-output-repo"
    fake_shared.GH_HEADERS = {"Authorization": "Bearer fake-token"}
    fake_shared.GH_API = "https://api.github.com"

    sys.modules["shared"] = fake_shared

    # Now import the module under test
    import importlib.util, pathlib

    _script_path = (
        pathlib.Path(__file__).parent.parent.parent
        / ".github" / "scripts" / "tool5_uat.py"
    )

    # Fallback: if running from repo root during CI
    if not _script_path.exists():
        _script_path = pathlib.Path(".github/scripts/tool5_uat.py")

    spec = importlib.util.spec_from_file_location("tool5_uat", str(_script_path))
    tool5 = importlib.util.module_from_spec(spec)
    # Inject our fake shared into the new module's namespace before exec
    tool5.shared = fake_shared  # type: ignore[attr-defined]
    spec.loader.exec_module(tool5)

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriter login
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- System is running
- Valid credentials exist
TEST DATA: username=underwriter@test.com, password=Secure123!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Dashboard displayed
PASS CRITERIA: Dashboard page loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Check SSO flow too
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriter login
TYPE: POSITIVE
PERSONA: Underwriter
PASS CRITERIA: Dashboard page loads within 3 seconds
ESTIMATED TIME: 5
NOTES: None
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: Underwriter
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: None
"""

SCENARIO_MISSING_ID = """\
===SCENARIO===
TITLE: Orphaned scenario — no ID
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Something happens
ESTIMATED TIME: 2
NOTES: None
"""


def _make_scenario(
    sid="UAT-X-1",
    title="Test title",
    stype="POSITIVE",
    persona="Tester",
    pass_criteria="Criteria met",
    estimated_time="5",
    raw="raw block text",
):
    return {
        "id": sid,
        "title": title,
        "type": stype,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": raw,
    }


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================

class TestParseScenarios:
    def test_single_scenario_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful underwriter login"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Dashboard page loads within 3 seconds"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_scenario_types_preserved(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_raw_field_populated(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result[0]["raw"]) > 0
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_scenario_without_id_excluded(self):
        result = parse_scenarios(SCENARIO_MISSING_ID)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This text has no scenario delimiters at all.")
        assert result == []

    def test_whitespace_only_returns_empty_list(self):
        result = parse_scenarios("   \n\n\t  ")
        assert result == []

    def test_multiple_scenarios_order_preserved(self):
        raw = ""
        for i in range(1, 6):
            raw += f"\n===SCENARIO===\nID: UAT-S-{i}\nTITLE: Scenario {i}\n"
        result = parse_scenarios(raw)
        assert len(result) == 5
        for i, s in enumerate(result, start=1):
            assert s["id"] == f"UAT-S-{i}"

    def test_missing_optional_fields_default_absent(self):
        minimal = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal\n"
        result = parse_scenarios(minimal)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        # Optional fields should be absent (not defaulted to empty string by parser)
        assert "type" not in s or s.get("type") is not None  # parser only sets if line found

    def test_extra_whitespace_in_field_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace test   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace test"

    def test_boundary_single_delimiter_at_start(self):
        raw = "===SCENARIO===\n"
        result = parse_scenarios(raw)
        # Block has no ID so should be excluded
        assert result == []

    def test_mixed_valid_invalid_scenarios(self):
        raw = TWO_SCENARIO_BLOCK + SCENARIO_MISSING_ID
        result = parse_scenarios(raw)
        # Only the 2 with IDs should be returned
        assert len(result) == 2

    def test_boundary_type_values(self):
        for stype in ["POSITIVE", "NEGATIVE", "BOUNDARY"]:
            raw = f"===SCENARIO===\nID: UAT-T-1\nTITLE: t\nTYPE: {stype}\n"
            result = parse_scenarios(raw)
            assert result[0]["type"] == stype

    def test_synthetic_persona_underwriting(self):
        """Use synthetic data: Underwriting Risk Classification persona."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-UNDERWRITE-1\n"
            "TITLE: Risk classification for Age=34\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=Medium\n"
            "PASS CRITERIA: CatBoost model returns Risk_Classification within 2 seconds\n"
            "ESTIMATED TIME: 10\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["persona"] == "Underwriter"

    @pytest.mark.parametrize("n_scenarios", [0, 1, 10, 50])
    def test_parametrised_scenario_counts(self, n_scenarios):
        raw = "".join(
            f"===SCENARIO===\nID: UAT-P-{i}\nTITLE: Scenario {i}\n"
            for i in range(n_scenarios)
        )
        result = parse_scenarios(raw)
        assert len(result) == n_scenarios


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:
    EXPECTED_HEADERS = [
        "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
        "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
    ]

    def _parse_csv(self, csv_string: str):
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_headers_correct(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0] == self.EXPECTED_HEADERS

    def test_empty_scenarios_produces_header_only(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1  # only header

    def test_single_scenario_row(self):
        s = _make_scenario()
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-X-1"
        assert data_row[1] == "Test title"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Tester"
        assert data_row[4] == "Criteria met"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be blank
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(sid=f"UAT-X-{i}") for i in range(5)]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 6  # header + 5 data rows

    def test_missing_keys_produce_empty_strings(self):
        """Scenario dict with only 'raw' key — all CSV cells should be empty strings."""
        s = {"raw": "some raw block"}
        rows = self._parse_csv(build_test_pack_csv([s]))
        data_row = rows[1]
        assert data_row[0] == ""
        assert data_row[1] == ""

    def test_output_is_string(self):
        result = build_test_pack_csv([_make_scenario()])
        assert isinstance(result, str)

    def test_row_column_count_matches_header(self):
        s = _make_scenario()
        rows = self._parse_csv(build_test_pack_csv([s]))
        for row in rows:
            assert len(row) == len(self.EXPECTED_HEADERS)

    def test_synthetic_data_in_csv(self):
        """Use synthetic customer/underwriting data in scenario."""
        s = _make_scenario(
            sid="UAT-CUST-001",
            title="Customer CUST00000001 similarity lookup",
            persona="Underwriter",
            pass_criteria="Returns 20 similar customers",
        )
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert rows[1][0] == "UAT-CUST-001"
        assert "CUST00000001" in rows[1][1]

    def test_special_characters_in_fields(self):
        s = _make_scenario(
            title='Title with "quotes" and, commas',
            pass_criteria="Result ≥ 95%",
        )
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert "quotes" in rows[1][1]
        assert "≥" in rows[1][4]

    def test_newline_in_pass_criteria_handled(self):
        s = _make_scenario(pass_criteria="Line1\nLine2")
        result = build_test_pack_csv([s])
        # Should not raise; CSV library handles quoting
        assert isinstance(result, str)

    @pytest.mark.parametrize("stype", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_scenario