"""
Tests for tool5_uat.py
======================
What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input, malformed blocks
  - build_test_pack_csv(): correct headers, row content, empty scenarios, special characters
  - build_test_pack_md(): correct markdown structure, version/owner/repo embedding
  - get_results_csv(): successful fetch + base64 decode, missing content key, HTTP errors

Mocks used:
  - unittest.mock.patch for requests.get (GitHub API calls)
  - unittest.mock.patch for shared module functions (call_claude, write_output_file, send_email, etc.)
  - base64 encoding helpers for synthetic API responses

TODOs:
  - TODO: Integration test for full __main__ block requires env vars + live GitHub token
  - TODO: Test call_claude interaction inside mode=generate flow (needs shared module contract)
  - TODO: Test write_audit_entry call in generate/analyse flows
  - TODO: Test email formatting in send_email call (needs SMTP/SES mock details)
  - TODO: Test analyse mode end-to-end (needs SYSTEM_ANALYSE prompt + clean_json behaviour)
"""

import base64
import csv
import io
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub out the `shared` module before importing tool5_uat, so we never need
# real credentials or network access.
# ---------------------------------------------------------------------------
shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="mocked claude response")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value="sha123")
shared_stub.send_email = MagicMock()
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules["shared"] = shared_stub

# Now import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
import importlib

# Dynamically load tool5_uat from its actual path so the path manipulation works
_script_path = os.path.join(
    os.path.dirname(__file__), "..", ".github", "scripts", "tool5_uat.py"
)
_spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
tool5_uat = importlib.util.module_from_spec(_spec)

# Patch requests at module level before exec
with patch.dict("sys.modules", {"requests": MagicMock(), "base64": base64}):
    import requests as _requests_mock

    _spec.loader.exec_module(tool5_uat)

parse_scenarios = tool5_uat.parse_scenarios
build_test_pack_csv = tool5_uat.build_test_pack_csv
build_test_pack_md = tool5_uat.build_test_pack_md
get_results_csv = tool5_uat.get_results_csv


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Standard underwriter
PRE-CONDITIONS:
- System is online
- User account exists
TEST DATA: username=john.doe@example.com, password=P@ssw0rd1!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Login
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: Dashboard page loads within 3 seconds
ESTIMATED TIME: 5
NOTES: Verify SSO edge case
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Valid risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Model service running
TEST DATA: Age=35, Annual_Income=75000, Risk_Classification=Low
STEPS:
1. Submit application
EXPECTED RESULT: Low risk returned
PASS CRITERIA: API returns 200 with Risk_Classification=Low
ESTIMATED TIME: 3
NOTES: Uses CatBoostClassifier model

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid age input
TYPE: NEGATIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- Model service running
TEST DATA: Age=-1
STEPS:
1. Submit application with Age=-1
EXPECTED RESULT: Validation error returned
PASS CRITERIA: API returns 422
ESTIMATED TIME: 2
NOTES: Boundary check
"""


def _make_github_content_response(content: str) -> dict:
    """Return a dict mimicking GitHub contents API response."""
    encoded = base64.b64encode(content.encode()).decode()
    return {"content": encoded, "encoding": "base64"}


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================


class TestParseScenarios:
    def test_single_scenario_parsed(self):
        results = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(results) == 1
        s = results[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Standard underwriter"
        assert s["pass_criteria"] == "Dashboard page loads within 3 seconds"
        assert s["estimated_time"] == "5"

    def test_single_scenario_raw_field_present(self):
        results = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in results[0]
        assert "Successful login" in results[0]["raw"]

    def test_two_scenarios_parsed(self):
        results = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(results) == 2
        ids = [s["id"] for s in results]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_scenario_types_captured(self):
        results = parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = {s["id"]: s["type"] for s in results}
        assert types_["UAT-STORY1-1"] == "POSITIVE"
        assert types_["UAT-STORY1-2"] == "NEGATIVE"

    def test_synthetic_test_data_in_raw(self):
        """Synthetic data (Age, Annual_Income) should appear in the raw block."""
        results = parse_scenarios(TWO_SCENARIO_BLOCK)
        raw_combined = " ".join(s["raw"] for s in results)
        assert "Age=35" in raw_combined
        assert "Annual_Income=75000" in raw_combined

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        assert parse_scenarios("Some random text without the delimiter") == []

    def test_delimiter_only_returns_empty_list(self):
        """A block that has the delimiter but no ID should be skipped."""
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        results = parse_scenarios(raw)
        assert results == []

    def test_missing_optional_fields_defaults_absent(self):
        """Scenario with only ID should parse with just id + raw."""
        raw = "===SCENARIO===\nID: UAT-X-1\n"
        results = parse_scenarios(raw)
        assert len(results) == 1
        s = results[0]
        assert s["id"] == "UAT-X-1"
        assert "title" not in s
        assert "type" not in s

    def test_multiple_delimiters_with_empty_blocks(self):
        """Empty blocks between delimiters must not produce entries."""
        raw = "===SCENARIO===\n\n===SCENARIO===\nID: UAT-Y-1\nTITLE: T\n"
        results = parse_scenarios(raw)
        assert len(results) == 1
        assert results[0]["id"] == "UAT-Y-1"

    def test_whitespace_stripped_from_fields(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Spaced Title   \n"
        results = parse_scenarios(raw)
        assert results[0]["id"] == "UAT-WS-1"
        assert results[0]["title"] == "Spaced Title"

    def test_pass_criteria_with_colon_in_value(self):
        """PASS CRITERIA value may itself contain colons."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-C-1\n"
            "PASS CRITERIA: API returns 200: body contains {status: ok}\n"
        )
        results = parse_scenarios(raw)
        assert "200" in results[0]["pass_criteria"]

    def test_large_number_of_scenarios(self):
        """Performance / correctness check with 50 scenarios."""
        blocks = []
        for i in range(1, 51):
            blocks.append(
                f"===SCENARIO===\nID: UAT-PERF-{i}\nTITLE: Scenario {i}\n"
                f"TYPE: POSITIVE\nPERSONA: Tester\n"
                f"PASS CRITERIA: Pass {i}\nESTIMATED TIME: {i}\n"
            )
        raw = "\n".join(blocks)
        results = parse_scenarios(raw)
        assert len(results) == 50
        assert results[-1]["id"] == "UAT-PERF-50"

    def test_unicode_content_handled(self):
        """Arabic / non-ASCII characters in scenario fields (from ar-SA.json)."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-AR-1\n"
            "TITLE: إلغاء العملية\n"
            "PERSONA: مستخدم عربي\n"
            "PASS CRITERIA: تأكيد\n"
        )
        results = parse_scenarios(raw)
        assert results[0]["title"] == "إلغاء العملية"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_header_row_correct(self):
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
        assert len(rows) == 1

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-S1-1",
                "title": "Valid login",
                "type": "POSITIVE",
                "persona": "Underwriter",
                "pass_criteria": "Dashboard loads",
                "estimated_time": "5",
            }
        ]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-S1-1"
        assert data_row[1] == "Valid login"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Underwriter"
        assert data_row[4] == "Dashboard loads"
        assert data_row[5] == "5"
        # Tester-filled columns should be blank
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-X-{i}", "title": f"Title {i}"} for i in range(5)
        ]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_optional_keys_default_empty(self):
        scenarios = [{"id": "UAT-MIN-1"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-1"
        # All other populated fields default to ""
        assert all(cell == "" for cell in data_row[1:])

    def test_special_characters_in_fields(self):
        """Commas and quotes must be properly escaped in CSV."""
        scenarios = [
            {
                "id": "UAT-SC-1",
                "title": 'Title with "quotes" and, comma',
                "type": "BOUNDARY",
                "persona": "Tester, Senior",
                "pass_criteria": "Returns {\"status\": \"ok\"}",
                "estimated_time": "10",
            }
        ]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == 'Title with "quotes" and, comma'
        assert rows[1][3] == "Tester, Senior"

    def test_synthetic_risk_classification_scenario(self):
        """Scenario based on synthetic model card data."""
        scenarios = [
            {
                "id": "UAT-RISK-1",
                "title": "Risk Classification: Age boundary at 34",
                "type": "BOUNDARY",
                "persona": "Underwriter",
                "pass_criteria": "Model returns Risk_Classification within 2s",
                "estimated_time": "3",
            }
        ]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-RISK-1"
        assert "Age boundary" in rows[1][1]

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_newline_terminated(self):
        """CSV output should end with a newline."""
        result = build_test_pack_csv([{"id": "UAT-NL-1"}])
        assert result.endswith("\r\n") or result.endswith("\n")


# ===========================================================================
# build_test_pack_md
# ===========================================================================


class TestBuildTestPackMd:
    def test_returns_string(self):
        result = build_test_pack_md("raw content", "owner", "repo", "1.0.0")
        assert isinstance(result, str)

    def test_contains_owner_repo_version(self):
        result = build_test_pack_md("", "acme-corp",