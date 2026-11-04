"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios: happy path, edge cases, missing fields, empty input, malformed blocks
  - build_test_pack_csv: correct CSV headers, row content, empty scenarios list
  - build_test_pack_md: markdown structure, version/owner/repo substitution
  - get_results_csv: successful fetch and decode, missing content key, HTTP errors
  - __main__ block integration paths (generate / analyse modes) — via subprocess/env mocking

Mocks used:
  - unittest.mock.patch for: requests.get, shared.call_claude, shared.get_repo_files,
    shared.write_output_file, shared.send_email, shared.email_html,
    shared.write_audit_entry, base64.b64decode (selectively)
  - io.StringIO is exercised directly (no mock needed)

TODOs:
  - TODO: Integration test for __main__ generate path requires a real or fully-stubbed
    GitHub API and Claude response — stub provided below.
  - TODO: Integration test for __main__ analyse path similarly requires end-to-end env
    setup — stub provided below.
  - TODO: Verify exact Claude prompt content sent in generate/analyse modes once the
    full __main__ block source is available (source was truncated).
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
# needing the real sibling module on PYTHONPATH.
# ---------------------------------------------------------------------------

SHARED_ATTRS = [
    "clean_json", "call_claude", "get_repo_files", "write_output_file",
    "send_email", "email_html", "write_audit_entry",
    "OUTPUT_REPO_OWNER", "OUTPUT_REPO", "GH_HEADERS", "GH_API",
]

@pytest.fixture(autouse=True, scope="session")
def stub_shared_module():
    """Inject a fake `shared` module before tool5_uat is imported."""
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="mocked claude response")
    shared.get_repo_files = MagicMock(return_value={"file.py": "content"})
    shared.write_output_file = MagicMock(return_value={"commit": {"sha": "abc"}})
    shared.send_email = MagicMock()
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock()
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer fake"}
    shared.GH_API = "https://api.github.com"
    sys.modules["shared"] = shared
    yield shared
    # leave the stub in place for the whole session


# ---------------------------------------------------------------------------
# Now import the module under test
# ---------------------------------------------------------------------------

import importlib

@pytest.fixture(scope="session")
def uat_module(stub_shared_module):
    # Ensure requests is importable
    import requests  # noqa: F401
    spec = importlib.util.spec_from_file_location(
        "tool5_uat",
        os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"),
    )
    if spec is None:
        # Fallback: try relative to repo root
        for candidate in [
            os.path.join(os.path.dirname(__file__), "tool5_uat.py"),
            os.path.join(os.path.dirname(__file__), ".github/scripts/tool5_uat.py"),
        ]:
            if os.path.exists(candidate):
                spec = importlib.util.spec_from_file_location("tool5_uat", candidate)
                break
    if spec is None:
        pytest.skip("tool5_uat.py not found on disk — adjust path before running")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful login with valid credentials
TYPE: POSITIVE
PERSONA: Registered Customer
PRE-CONDITIONS:
- User account exists
- User is not logged in
TEST DATA: email=test@example.com, password=Pass1234!
STEPS:
1. Navigate to login screen
2. Enter credentials
3. Click submit
EXPECTED RESULT: User is redirected to dashboard
PASS CRITERIA: Dashboard renders with user's name
ESTIMATED TIME: 3
NOTES: Requires seeded test account
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Happy path
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Success banner shown
ESTIMATED TIME: 5
NOTES: none

===SCENARIO===
ID: UAT-STORY1-2
TITLE: Invalid input rejected
TYPE: NEGATIVE
PERSONA: Guest
PASS CRITERIA: Error message displayed
ESTIMATED TIME: 2
NOTES: boundary check
"""

INSURANCE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-1
TITLE: Verify Generations II plan brochure download
TYPE: POSITIVE
PERSONA: Prospective Policyholder
PRE-CONDITIONS:
- Product brochure published to portal
TEST DATA: product_name=Generations II, doc_type=product_brochure
STEPS:
1. Navigate to product page
2. Click Download Brochure
3. Verify PDF opens
EXPECTED RESULT: PDF opens successfully
PASS CRITERIA: File size > 0 and correct product name in header
ESTIMATED TIME: 5
NOTES: Linked product: Generations II

===SCENARIO===
ID: UAT-GEN2-2
TITLE: Attempt brochure download as unauthenticated user
TYPE: NEGATIVE
PERSONA: Anonymous User
PASS CRITERIA: 401 Unauthorised response
ESTIMATED TIME: 2
NOTES: none
"""


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_all_fields(self, uat_module):
        result = uat_module.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Registered Customer"
        assert s["pass_criteria"] == "Dashboard renders with user's name"
        assert s["estimated_time"] == "3"
        assert "raw" in s

    def test_two_scenarios_parsed(self, uat_module):
        result = uat_module.parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_insurance_synthetic_data_scenarios(self, uat_module):
        result = uat_module.parse_scenarios(INSURANCE_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-GEN2-1"
        assert result[0]["type"] == "POSITIVE"
        assert result[1]["type"] == "NEGATIVE"

    def test_empty_string_returns_empty_list(self, uat_module):
        result = uat_module.parse_scenarios("")
        assert result == []

    def test_no_scenario_delimiter_returns_empty(self, uat_module):
        result = uat_module.parse_scenarios("Some random text without delimiter")
        assert result == []

    def test_scenario_without_id_is_excluded(self, uat_module):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = uat_module.parse_scenarios(raw)
        assert result == []

    def test_raw_field_contains_original_block(self, uat_module):
        result = uat_module.parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-STORY1-1" in result[0]["raw"]
        assert "Successful login" in result[0]["raw"]

    def test_missing_optional_fields_do_not_raise(self, uat_module):
        """Scenario with only ID should still parse without KeyError."""
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = uat_module.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        assert s.get("title") is None
        assert s.get("type") is None

    def test_multiple_delimiters_at_start(self, uat_module):
        """Leading ===SCENARIO=== produces empty first block that is skipped."""
        raw = "===SCENARIO===\nID: UAT-X-1\nTITLE: X\n===SCENARIO===\nID: UAT-X-2\nTITLE: Y\n"
        result = uat_module.parse_scenarios(raw)
        assert len(result) == 2

    def test_whitespace_only_blocks_skipped(self, uat_module):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-1\n"
        result = uat_module.parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_id_with_extra_spaces_stripped(self, uat_module):
        raw = "===SCENARIO===\nID:   UAT-SP-1   \n"
        result = uat_module.parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SP-1"

    def test_title_with_extra_spaces_stripped(self, uat_module):
        raw = "===SCENARIO===\nID: UAT-T-1\nTITLE:   My Title   \n"
        result = uat_module.parse_scenarios(raw)
        assert result[0]["title"] == "My Title"

    def test_large_number_of_scenarios(self, uat_module):
        """Boundary: parse 50 scenarios without error."""
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BIG-{i}\nTITLE: Scenario {i}\n"
            for i in range(50)
        )
        result = uat_module.parse_scenarios(blocks)
        assert len(result) == 50

    @pytest.mark.parametrize("type_val", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_all_valid_types_parsed(self, uat_module, type_val):
        raw = f"===SCENARIO===\nID: UAT-TYPE-1\nTYPE: {type_val}\n"
        result = uat_module.parse_scenarios(raw)
        assert result[0]["type"] == type_val


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_str: str):
        return list(csv.reader(io.StringIO(csv_str)))

    def test_header_row(self, uat_module):
        csv_str = uat_module.build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self, uat_module):
        csv_str = uat_module.build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # only header

    def test_single_scenario_row(self, uat_module):
        scenarios = [{
            "id": "UAT-STORY1-1",
            "title": "Login test",
            "type": "POSITIVE",
            "persona": "Admin",
            "pass_criteria": "Dashboard shown",
            "estimated_time": "5",
        }]
        csv_str = uat_module.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-STORY1-1"
        assert data_row[1] == "Login test"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Admin"
        assert data_row[4] == "Dashboard shown"
        assert data_row[5] == "5"

    def test_result_tester_notes_defect_columns_empty(self, uat_module):
        scenarios = [{"id": "UAT-1", "title": "T", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "C", "estimated_time": "1"}]
        csv_str = uat_module.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data = rows[1]
        # columns 6-9 (Result, Tester, Notes, Defect Ref) should be empty
        assert data[6] == ""
        assert data[7] == ""
        assert data[8] == ""
        assert data[9] == ""

    def test_multiple_scenarios_produce_correct_row_count(self, uat_module):
        scenarios = [
            {"id": f"UAT-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "3"}
            for i in range(10)
        ]
        csv_str = uat_module.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 11  # 1 header + 10 data

    def test_missing_fields_default_to_empty_string(self, uat_module):
        scenarios = [{"id": "UAT-PARTIAL-1"}]  # only id
        csv_str = uat_module.build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data = rows[1]
        assert data[0] == "UAT-PARTIAL-1"
        assert data[1] == ""
        assert data[2] == ""

    def test_csv_contains_insurance_synthetic_data(self, uat_module):
        scenarios = [{
            "id": "UAT-GEN2-1",
            "title": "Verify Generations II plan brochure download",
            "type": "POSITIVE",
            "persona": "Prospective Policyholder",
            "pass_criteria": "File size > 0 and correct product name in header",
            "estimated_time": "5",
        }]
        csv_str = uat_module.build_test_pack_csv(scenarios)
        assert "UAT-GEN2-1" in csv_str
        assert "Generations II" in