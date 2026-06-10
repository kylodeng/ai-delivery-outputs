"""
Tests for tool5_uat.py

What is tested:
    - parse_scenarios: happy path, edge cases (empty input, no ID, partial fields, multiple scenarios)
    - build_test_pack_csv: correct CSV structure, all columns, empty scenario list
    - build_test_pack_md: correct markdown structure, version/owner/repo embedding
    - get_results_csv: successful fetch and decode, missing content key (FileNotFoundError)
    - Module-level __main__ block is NOT directly tested (requires subprocess/env wiring)

Mocks used:
    - requests.get (for get_results_csv)
    - shared.call_claude
    - shared.get_repo_files
    - shared.write_output_file
    - shared.send_email
    - shared.email_html
    - shared.write_audit_entry
    - base64.b64decode (indirectly via mocked response)

TODOs:
    - TODO: Integration test for __main__ block requires full env wiring + subprocess execution
    - TODO: Test call_claude integration inside generate/analyse flows once those helpers are extracted
    - TODO: Test write_output_file is called with correct content in end-to-end flow
    - TODO: Verify synthetic insurance data (Generations II, hospital lists) propagates correctly
      into Claude prompt construction once prompt-building is extracted to a testable function
"""

import base64
import csv
import io
import json
import sys
import os
import types
import unittest.mock as mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool5_uat doesn't fail
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "token fake"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib, types as _types

# Patch requests before importing so the module-level import resolves cleanly
with patch.dict(sys.modules, {"requests": MagicMock()}):
    # Ensure a clean import even if already cached
    if "tool5_uat" in sys.modules:
        del sys.modules["tool5_uat"]

    import requests as _requests_mock  # noqa: F401 – will be patched per-test

    spec_path = os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py")
    # Fallback: try relative to CWD
    if not os.path.exists(spec_path):
        spec_path = os.path.join("tool5_uat.py")

    # Use importlib to load from an explicit path
    import importlib.util

    _spec = importlib.util.spec_from_file_location(
        "tool5_uat",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts", "tool5_uat.py")
        if os.path.exists(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts", "tool5_uat.py")
        )
        else os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool5_uat.py"),
    )

    # If file not found in either location, try CWD
    _candidate_paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts", "tool5_uat.py"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool5_uat.py"),
        os.path.join(os.getcwd(), ".github", "scripts", "tool5_uat.py"),
        os.path.join(os.getcwd(), "tool5_uat.py"),
    ]

    _found_path = next((p for p in _candidate_paths if os.path.exists(p)), None)

    if _found_path:
        _spec = importlib.util.spec_from_file_location("tool5_uat", _found_path)
        _loader_module = importlib.util.module_from_spec(_spec)
        sys.modules["tool5_uat"] = _loader_module
        # Execute under patched requests
        with patch("requests.get") as _:
            _spec.loader.exec_module(_loader_module)
        tool5_uat = _loader_module
    else:
        # If source file is not available, create a stub for CI environments
        tool5_uat = None  # tests will be skipped


# ---------------------------------------------------------------------------
# Skip marker when module cannot be loaded
# ---------------------------------------------------------------------------

pytestmark_needs_module = pytest.mark.skipif(
    tool5_uat is None,
    reason="tool5_uat.py not found in expected locations",
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_RAW = """===SCENARIO===
ID: UAT-GEN2-1
TITLE: Purchase Generations II policy as new customer
TYPE: POSITIVE
PERSONA: New policyholder
PRE-CONDITIONS:
- User is authenticated
- Product is active
TEST DATA: product_name=Generations II, premium=5000
STEPS:
1. Navigate to product page
2. Fill in application form
3. Submit application
EXPECTED RESULT: Application is accepted and policy number is issued
PASS CRITERIA: Policy number displayed on confirmation screen
ESTIMATED TIME: 10
NOTES: Uses synthetic data from Generations II brochure"""

TWO_SCENARIO_RAW = """===SCENARIO===
ID: UAT-GEN2-1
TITLE: Purchase Generations II policy
TYPE: POSITIVE
PERSONA: New policyholder
PRE-CONDITIONS:
- Authenticated
TEST DATA: premium=5000
STEPS:
1. Open product page
EXPECTED RESULT: Policy issued
PASS CRITERIA: Policy number shown
ESTIMATED TIME: 5
NOTES: none
===SCENARIO===
ID: UAT-GEN2-2
TITLE: Submit empty application form
TYPE: NEGATIVE
PERSONA: New policyholder
PRE-CONDITIONS:
- Authenticated
TEST DATA: empty form
STEPS:
1. Click submit without filling form
EXPECTED RESULT: Validation error displayed
PASS CRITERIA: Error message visible
ESTIMATED TIME: 3
NOTES: Boundary/negative"""

PARTIAL_SCENARIO_RAW = """===SCENARIO===
TITLE: No ID scenario
TYPE: BOUNDARY
PERSONA: Admin
PRE-CONDITIONS:
- none
TEST DATA: none
STEPS:
1. Do something
EXPECTED RESULT: Something happens
PASS CRITERIA: It worked
ESTIMATED TIME: 2
NOTES: none"""


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def _fn(self):
        return tool5_uat.parse_scenarios

    # Happy path – single scenario
    @pytestmark_needs_module
    def test_single_scenario_parsed_correctly(self):
        results = self._fn()(SINGLE_SCENARIO_RAW)
        assert len(results) == 1
        s = results[0]
        assert s["id"] == "UAT-GEN2-1"
        assert s["title"] == "Purchase Generations II policy as new customer"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "New policyholder"
        assert s["pass_criteria"] == "Policy number displayed on confirmation screen"
        assert s["estimated_time"] == "10"

    # Happy path – raw field preserved
    @pytestmark_needs_module
    def test_single_scenario_raw_preserved(self):
        results = self._fn()(SINGLE_SCENARIO_RAW)
        assert "raw" in results[0]
        assert "UAT-GEN2-1" in results[0]["raw"]

    # Happy path – two scenarios
    @pytestmark_needs_module
    def test_two_scenarios_parsed(self):
        results = self._fn()(TWO_SCENARIO_RAW)
        assert len(results) == 2
        assert results[0]["id"] == "UAT-GEN2-1"
        assert results[1]["id"] == "UAT-GEN2-2"

    # Scenario types preserved
    @pytestmark_needs_module
    def test_scenario_types_preserved(self):
        results = self._fn()(TWO_SCENARIO_RAW)
        assert results[0]["type"] == "POSITIVE"
        assert results[1]["type"] == "NEGATIVE"

    # Edge case – empty string
    @pytestmark_needs_module
    def test_empty_string_returns_empty_list(self):
        results = self._fn()("")
        assert results == []

    # Edge case – only delimiter, no content
    @pytestmark_needs_module
    def test_only_delimiter_returns_empty_list(self):
        results = self._fn()("===SCENARIO===")
        assert results == []

    # Edge case – scenario without ID is excluded
    @pytestmark_needs_module
    def test_scenario_without_id_excluded(self):
        results = self._fn()(PARTIAL_SCENARIO_RAW)
        assert results == []

    # Edge case – whitespace-only blocks ignored
    @pytestmark_needs_module
    def test_whitespace_only_blocks_ignored(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-1\nTITLE: Test\n"
        results = self._fn()(raw)
        assert len(results) == 1
        assert results[0]["id"] == "UAT-1"

    # Edge case – missing optional fields default to absent keys
    @pytestmark_needs_module
    def test_missing_optional_fields_not_in_dict(self):
        raw = "===SCENARIO===\nID: UAT-HOSP-1\nTITLE: Hospital claim\n"
        results = self._fn()(raw)
        assert len(results) == 1
        assert results[0].get("estimated_time") is None
        assert results[0].get("persona") is None

    # Boundary – very large number of scenarios
    @pytestmark_needs_module
    def test_many_scenarios_parsed(self):
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Bulk test {i}\n"
                f"TYPE: POSITIVE\nPERSONA: Tester\n"
                f"PASS CRITERIA: Passes\nESTIMATED TIME: 1\n"
            )
        raw = "\n".join(blocks)
        results = self._fn()(raw)
        assert len(results) == 50
        assert results[49]["id"] == "UAT-BULK-49"

    # Negative – leading/trailing whitespace in field values stripped
    @pytestmark_needs_module
    def test_field_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-STRIP-1   \nTITLE:   Stripped Title   \n"
        results = self._fn()(raw)
        assert results[0]["id"] == "UAT-STRIP-1"
        assert results[0]["title"] == "Stripped Title"

    # Negative – no delimiter at all → empty list
    @pytestmark_needs_module
    def test_no_delimiter_returns_empty_list(self):
        raw = "ID: UAT-NODEL-1\nTITLE: No delimiter scenario\n"
        results = self._fn()(raw)
        assert results == []

    # Synthetic data: Generations II product scenario
    @pytestmark_needs_module
    def test_generations_ii_scenario(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-GEN2-BOUNDARY-1\n"
            "TITLE: Submit application with maximum sum assured for Generations II\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: Wealth management advisor\n"
            "PASS CRITERIA: Application accepted or rejection message shown\n"
            "ESTIMATED TIME: 15\n"
        )
        results = self._fn()(raw)
        assert len(results) == 1
        assert results[0]["type"] == "BOUNDARY"
        assert "Generations II" in results[0]["title"]

    # Synthetic data: hospital list scenario
    @pytestmark_needs_module
    def test_hospital_list_scenario(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-HOSP-NEG-1\n"
            "TITLE: Claim at non-designated hospital in mainland China\n"
            "TYPE: NEGATIVE\n"
            "PERSONA: Policyholder\n"
            "PASS CRITERIA: Claim rejected with appropriate error\n"
            "ESTIMATED TIME: 8\n"
        )
        results = self._fn()(raw)
        assert results[0]["type"] == "NEGATIVE"
        assert "hospital" in results[0]["title"].lower()


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _fn(self):
        return tool5_uat.build_test_pack_csv

    def _parse_csv(self, csv_str: str):
        return list(csv.reader(io.StringIO(csv_str)))

    # Happy path – correct headers
    @pytestmark_needs_module
    def test_csv_has_correct_headers(self):
        rows = self._parse_csv(self._fn()([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    # Happy path – single scenario produces one data row
    @pytestmark_needs_module
    def test_single_scenario_produces_one_row(self):
        scenarios = [
            {"id": "UAT-1", "title": "Login", "type": "POSITIVE",
             "persona": "Admin", "pass_criteria": "Dashboard shown",
             "estimated_time": "5"}
        ]
        rows = self._parse_csv(self._fn()(scenarios))
        assert len(rows) == 2  # header + 1 data row
        assert rows[1][0] == "UAT-1"
        assert rows[1][1] == "Login"
        assert rows[1][2] == "POSITIVE"
        assert rows[1][3] == "Admin"
        assert rows[1][4] == "Dashboard shown"
        assert rows[1][5] == "5"

    # Happy path – trailing columns are empty strings
    @pytestmark_needs_module
    def test_result_tester_notes_defect_empty(self):
        scenarios = [
            {"id": "UAT-2", "title": "Logout", "type": "NEGATIVE",
             "persona": "User", "pass_criteria": "Signed out",
             "estimated_time": "2"}
        ]
        rows = self._parse_csv(self._fn()(scenarios))
        # Result, Tester, Notes, Defect Ref columns should be empty
        assert rows[