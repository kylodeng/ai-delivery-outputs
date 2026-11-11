"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios: happy path, edge cases, missing fields, empty input, malformed blocks
  - build_test_pack_csv: correct CSV headers, row content, empty scenarios list
  - build_test_pack_md: markdown structure, version/owner/repo embedding
  - get_results_csv: successful fetch, missing file (FileNotFoundError), bad response
  - Integration smoke tests for mode branching logic (generate / analyse)

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls)
  - unittest.mock.patch for `base64.b64decode`
  - unittest.mock.patch for shared module functions:
      call_claude, get_repo_files, write_output_file, send_email,
      email_html, write_audit_entry
  - io.StringIO is NOT mocked — real CSV writing is exercised

TODOs:
  - TODO: Integration test for __main__ block requires a full env-var harness and
          live shared-module stubs — stubbed below with pytest.mark.skip
  - TODO: Tests for SYSTEM_GENERATE / SYSTEM_ANALYSE prompt constants (content
          assertions) need product-owner sign-off on exact wording
  - TODO: build_test_pack_md timestamp is non-deterministic; freeze time if
          exact string matching is required (e.g. via freezegun)
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
# Minimal stub for the `shared` module so we can import tool5_uat without
# the real shared.py and its heavyweight dependencies.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda s: s)
    shared.call_claude = MagicMock(return_value="stub response")
    shared.get_repo_files = MagicMock(return_value={"file.py": "content"})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer fake-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Insert stub before any import of tool5_uat
sys.modules.setdefault("shared", _make_shared_stub())

# Now import the module under test
import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py"

def _load_tool5():
    spec = importlib.util.spec_from_file_location("tool5_uat", str(_SCRIPT_PATH))
    mod = importlib.util.module_from_spec(spec)
    # Ensure stub shared is visible during exec
    sys.modules["shared"] = sys.modules["shared"]
    spec.loader.exec_module(mod)
    return mod


try:
    tool5 = _load_tool5()
    _IMPORT_OK = True
except Exception as _import_exc:
    _IMPORT_OK = False
    _import_exc_msg = str(_import_exc)

# If the script cannot be found/loaded, skip all tests with a clear message
pytestmark = pytest.mark.skipif(
    not _IMPORT_OK,
    reason=f"Could not import tool5_uat.py — check path or dependencies: "
           + (str(_import_exc_msg) if not _IMPORT_OK else ""),
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Standard User
PRE-CONDITIONS:
- User account exists
- System is online
TEST DATA: username=test@example.com, password=P@ssw0rd!
STEPS:
1. Navigate to login page
2. Enter credentials
3. Click Submit
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: Dashboard renders within 3 seconds
ESTIMATED TIME: 5
NOTES: Requires seeded user data
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path login
TYPE: POSITIVE
PERSONA: Standard User
PASS CRITERIA: Dashboard renders within 3 seconds
ESTIMATED TIME: 5
NOTES: none
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Login with wrong password
TYPE: NEGATIVE
PERSONA: Standard User
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 3
NOTES: none
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: No ID scenario
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: something
ESTIMATED TIME: 2
NOTES: none
"""


@pytest.fixture()
def single_scenario():
    return tool5.parse_scenarios(SINGLE_SCENARIO_BLOCK)


@pytest.fixture()
def two_scenarios():
    return tool5.parse_scenarios(TWO_SCENARIO_BLOCK)


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_returns_list(self, single_scenario):
        assert isinstance(single_scenario, list)

    def test_single_scenario_parsed(self, single_scenario):
        assert len(single_scenario) == 1

    def test_id_extracted(self, single_scenario):
        assert single_scenario[0]["id"] == "UAT-STORY1-1"

    def test_title_extracted(self, single_scenario):
        assert single_scenario[0]["title"] == "Happy path login"

    def test_type_extracted(self, single_scenario):
        assert single_scenario[0]["type"] == "POSITIVE"

    def test_persona_extracted(self, single_scenario):
        assert single_scenario[0]["persona"] == "Standard User"

    def test_pass_criteria_extracted(self, single_scenario):
        assert "Dashboard renders" in single_scenario[0]["pass_criteria"]

    def test_estimated_time_extracted(self, single_scenario):
        assert single_scenario[0]["estimated_time"] == "5"

    def test_raw_field_present(self, single_scenario):
        assert "raw" in single_scenario[0]
        assert len(single_scenario[0]["raw"]) > 0

    def test_two_scenarios_parsed(self, two_scenarios):
        assert len(two_scenarios) == 2

    def test_two_scenarios_ids(self, two_scenarios):
        ids = [s["id"] for s in two_scenarios]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_scenario_without_id_excluded(self):
        result = tool5.parse_scenarios(SCENARIO_WITHOUT_ID)
        assert result == []

    def test_empty_string_returns_empty_list(self):
        result = tool5.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = tool5.parse_scenarios("This is just some random text without any scenarios")
        assert result == []

    def test_whitespace_only_string(self):
        result = tool5.parse_scenarios("   \n\n   ")
        assert result == []

    def test_multiple_delimiters_only(self):
        result = tool5.parse_scenarios("===SCENARIO===\n===SCENARIO===\n===SCENARIO===")
        assert result == []

    def test_partial_scenario_missing_optional_fields(self):
        """Scenario with only ID must still be included."""
        raw = "===SCENARIO===\nID: UAT-X-1\n"
        result = tool5.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-1"
        # Optional fields default to absent (not in dict)
        assert "title" not in result[0] or result[0].get("title", "") == ""

    def test_id_with_leading_trailing_whitespace(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \n"
        result = tool5.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"

    def test_title_with_special_characters(self):
        raw = "===SCENARIO===\nID: UAT-X-2\nTITLE: Login with 'quotes' & <html>\n"
        result = tool5.parse_scenarios(raw)
        assert result[0]["title"] == "Login with 'quotes' & <html>"

    def test_raw_field_contains_original_content(self):
        result = tool5.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "STEPS:" in result[0]["raw"]

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_parsed(self, scenario_type):
        raw = f"===SCENARIO===\nID: UAT-T-1\nTYPE: {scenario_type}\n"
        result = tool5.parse_scenarios(raw)
        assert result[0]["type"] == scenario_type

    def test_many_scenarios(self):
        """Boundary: 50 scenarios in a single blob."""
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\nID: UAT-BIG-{i}\nTITLE: Scenario {i}\n"
                f"TYPE: POSITIVE\nPERSONA: Tester\n"
                f"PASS CRITERIA: ok\nESTIMATED TIME: 1\nNOTES: none\n"
            )
        result = tool5.parse_scenarios("".join(blocks))
        assert len(result) == 50

    def test_unicode_content_handled(self):
        """Arabic/non-ASCII in scenario fields — from frontend translations sample."""
        raw = "===SCENARIO===\nID: UAT-AR-1\nTITLE: إلغاء\nPERSONA: مستخدم\n"
        result = tool5.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-AR-1"
        assert result[0]["title"] == "إلغاء"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str):
        return list(csv.reader(io.StringIO(csv_str)))

    def test_returns_string(self, two_scenarios):
        result = tool5.build_test_pack_csv(two_scenarios)
        assert isinstance(result, str)

    def test_header_row_correct(self, two_scenarios):
        rows = self._parse_csv(tool5.build_test_pack_csv(two_scenarios))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_row_count_matches_scenarios(self, two_scenarios):
        rows = self._parse_csv(tool5.build_test_pack_csv(two_scenarios))
        # 1 header + 2 data rows
        assert len(rows) == 3

    def test_empty_scenarios_produces_header_only(self):
        result = tool5.build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1  # only header

    def test_scenario_id_in_first_column(self, single_scenario):
        rows = self._parse_csv(tool5.build_test_pack_csv(single_scenario))
        assert rows[1][0] == "UAT-STORY1-1"

    def test_result_column_empty_by_default(self, single_scenario):
        rows = self._parse_csv(tool5.build_test_pack_csv(single_scenario))
        assert rows[1][6] == ""

    def test_tester_column_empty_by_default(self, single_scenario):
        rows = self._parse_csv(tool5.build_test_pack_csv(single_scenario))
        assert rows[1][7] == ""

    def test_defect_ref_column_empty_by_default(self, single_scenario):
        rows = self._parse_csv(tool5.build_test_pack_csv(single_scenario))
        assert rows[1][9] == ""

    def test_scenario_with_missing_fields_produces_empty_cells(self):
        scenarios = [{"id": "UAT-MIN-1", "raw": ""}]
        rows = self._parse_csv(tool5.build_test_pack_csv(scenarios))
        # title, type, persona, pass_criteria, estimated_time should be empty
        assert rows[1][1] == ""
        assert rows[1][2] == ""

    def test_csv_contains_comma_safe_values(self):
        """Values containing commas must be properly quoted."""
        scenarios = [{
            "id": "UAT-CSV-1",
            "title": "Test, with comma",
            "type": "POSITIVE",
            "persona": "Admin, Super",
            "pass_criteria": "Result: ok",
            "estimated_time": "10",
            "raw": "",
        }]
        result = tool5.build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert rows[1][1] == "Test, with comma"
        assert rows[1][3] == "Admin, Super"

    def test_all_ten_columns_present_per_row(self, single_scenario):
        rows = self._parse_csv(tool5.build_test_pack_csv(single_scenario))
        assert len(rows[1]) == 10

    @pytest.mark.parametrize("n", [1, 5, 20])
    def test_row_count_parametrised(self, n):
        scenarios = [{"id": f"UAT-P-{i}", "title": f"T{i}", "type": "POSITIVE",
                      "persona": "User", "pass_criteria": "ok",
                      "estimated_time": "2", "raw": ""} for i in range(n)]
        rows = self._parse_csv(tool5.build_test_pack_csv(scenarios))
        assert len(rows) == n + 1  # +1 for header


# ===========================================================================
# build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd:

    def _make_md(self, raw="## Scenario content", owner="acme", repo="myapp", version="1.2.3"):
        return tool5.build_test_pack_md(raw, owner, repo, version)

    def test_returns_string(self):
        assert isinstance(self._make_md(), str)

    def test_contains_owner_and_repo(self):
        md = self._make_md(owner="acme", repo="myapp")
        assert "acme/myapp" in md

    def test_contains_version(self):
        md = self._make_md(version="2.0.0")
        assert "2.0.0" in md

    def test_contains_raw_content(self):
        md = self._make_md(raw="## Unique scenario raw content XYZ")
        assert "## Unique scenario raw content XYZ" in