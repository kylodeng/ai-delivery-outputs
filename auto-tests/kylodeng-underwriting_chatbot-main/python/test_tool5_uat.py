"""
Test module for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): parsing Claude's delimited scenario output into structured dicts
    - build_test_pack_csv(): generating a CSV test sheet from parsed scenarios
    - build_test_pack_md(): generating a Markdown test pack document
    - get_results_csv(): fetching and decoding a CSV file from a GitHub repo

Mocks used:
    - requests.get (for get_results_csv GitHub API calls)
    - shared.call_claude (not directly called in tested functions, but imported module patched)
    - shared.get_repo_files (patched at module level)
    - shared.write_output_file (patched at module level)
    - shared.send_email (patched at module level)
    - shared.write_audit_entry (patched at module level)
    - base64.b64decode (indirectly tested via get_results_csv)

TODOs:
    - TODO: Integration test for __main__ block (needs full env var setup and live mocks)
    - TODO: Test SYSTEM_GENERATE and SYSTEM_ANALYSE prompt strings with actual Claude API responses
    - TODO: Test build_test_pack_md timestamp freeze (needs freezegun or monkeypatching datetime)
"""

import base64
import csv
import io
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Bootstrap: create a minimal 'shared' stub so the import doesn't fail
# ---------------------------------------------------------------------------

def _make_shared_stub():
    shared = types.ModuleType("shared")
    shared.clean_json = MagicMock(side_effect=lambda x: x)
    shared.call_claude = MagicMock(return_value="{}")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer test-token"}
    shared.GH_API = "https://api.github.com"
    return shared


# Insert the stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib

# Patch requests at sys.modules level before import
_requests_mock = MagicMock()
sys.modules.setdefault("requests", _requests_mock)

# Force (re)import of the module under test
if "tool5_uat" in sys.modules:
    del sys.modules["tool5_uat"]

# Adjust path so the script is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# We import the public functions directly to avoid triggering __main__
with patch.dict("sys.modules", {"shared": _shared_stub, "requests": _requests_mock}):
    # Use importlib to import without executing __main__
    import importlib.util

    _script_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
    )
    # Fallback: look relative to this test file's location
    if not os.path.exists(_script_path):
        _script_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".github",
            "scripts",
            "tool5_uat.py",
        )

    # If we still can't find the file, create a minimal loader from the source
    # provided in the prompt (for CI environments where the file may not exist yet)
    _SOURCE_AVAILABLE = os.path.exists(_script_path)

    if _SOURCE_AVAILABLE:
        spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
        tool5_uat = importlib.util.module_from_spec(spec)
        # Prevent __main__ execution
        tool5_uat.__name__ = "tool5_uat"
        with patch.object(sys, "argv", ["tool5_uat.py"]):
            spec.loader.exec_module(tool5_uat)

        parse_scenarios = tool5_uat.parse_scenarios
        build_test_pack_csv = tool5_uat.build_test_pack_csv
        build_test_pack_md = tool5_uat.build_test_pack_md
        get_results_csv = tool5_uat.get_results_csv
        SYSTEM_GENERATE = tool5_uat.SYSTEM_GENERATE
        SYSTEM_ANALYSE = tool5_uat.SYSTEM_ANALYSE
    else:
        pytest.skip(
            "tool5_uat.py not found — all tests skipped", allow_module_level=True
        )


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture()
def single_scenario_block():
    """A well-formed single scenario string as Claude would produce it."""
    return """===SCENARIO===
ID: UAT-STORY1-1
TITLE: Underwriter views risk classification
TYPE: POSITIVE
PERSONA: Senior Underwriter
PRE-CONDITIONS:
- User is logged in
- Risk model is deployed
TEST DATA: Age=45, Annual_Income=120000, Risk_Classification=HIGH
STEPS:
1. Navigate to /underwriting/assess
2. Enter customer ID CUST00000001
3. Click "Run Assessment"
EXPECTED RESULT: System returns Risk_Classification=HIGH with feature importance breakdown
PASS CRITERIA: Classification badge shows HIGH and confidence >= 0.80
ESTIMATED TIME: 5
NOTES: Depends on CatBoostClassifier model being loaded"""


@pytest.fixture()
def multi_scenario_raw(single_scenario_block):
    """Two scenarios separated by the delimiter."""
    second = """===SCENARIO===
ID: UAT-STORY1-2
TITLE: Underwriter submits empty customer ID
TYPE: NEGATIVE
PERSONA: Junior Underwriter
PRE-CONDITIONS:
- User is logged in
TEST DATA: customer_id=""
STEPS:
1. Navigate to /underwriting/assess
2. Leave customer ID blank
3. Click "Run Assessment"
EXPECTED RESULT: Validation error displayed
PASS CRITERIA: Error message "Customer ID is required" visible
ESTIMATED TIME: 3
NOTES: Boundary case for empty input"""
    return single_scenario_block + "\n\n" + second


@pytest.fixture()
def parsed_scenarios(multi_scenario_raw):
    return parse_scenarios(multi_scenario_raw)


@pytest.fixture()
def minimal_scenario():
    """Scenario with only the mandatory ID field."""
    return """===SCENARIO===
ID: UAT-MIN-1
TITLE: Minimal scenario"""


@pytest.fixture()
def sample_csv_content():
    rows = [
        ["Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
         "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"],
        ["UAT-STORY1-1", "Underwriter views risk classification", "POSITIVE",
         "Senior Underwriter", "Classification badge shows HIGH", "5", "PASS", "tester1", "", ""],
        ["UAT-STORY1-2", "Empty customer ID", "NEGATIVE",
         "Junior Underwriter", "Error message visible", "3", "FAIL", "tester2", "Bug found", "DEF-001"],
    ]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerows(rows)
    return out.getvalue()


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================


class TestParseScenarios:
    """Tests for the parse_scenarios() function."""

    def test_happy_path_single_scenario(self, single_scenario_block):
        result = parse_scenarios(single_scenario_block)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Underwriter views risk classification"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Senior Underwriter"
        assert s["pass_criteria"] == "Classification badge shows HIGH and confidence >= 0.80"
        assert s["estimated_time"] == "5"
        assert "raw" in s
        assert "UAT-STORY1-1" in s["raw"]

    def test_happy_path_multiple_scenarios(self, multi_scenario_raw):
        result = parse_scenarios(multi_scenario_raw)
        assert len(result) == 2
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "Some text without the delimiter\nID: UAT-X-1\nTITLE: Whatever"
        # No ===SCENARIO=== delimiter → no valid blocks
        result = parse_scenarios(raw)
        assert result == []

    def test_block_without_id_is_skipped(self):
        raw = """===SCENARIO===
TITLE: No ID here
TYPE: POSITIVE
PERSONA: Tester"""
        result = parse_scenarios(raw)
        assert result == []

    def test_minimal_scenario_with_only_id(self, minimal_scenario):
        result = parse_scenarios(minimal_scenario)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        # Fields not present should be absent or empty
        assert result[0].get("type", "") == ""

    def test_raw_field_preserved(self, single_scenario_block):
        result = parse_scenarios(single_scenario_block)
        assert result[0]["raw"] == single_scenario_block.split("===SCENARIO===")[1].strip()

    def test_extra_whitespace_around_delimiter(self):
        raw = "\n\n===SCENARIO===\n\nID: UAT-WS-1\nTITLE: Whitespace test\n\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_multiple_colons_in_value(self):
        """Values containing colons should not be truncated."""
        raw = """===SCENARIO===
ID: UAT-COL-1
TITLE: Check URL: https://example.com/path"""
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["title"] == "Check URL: https://example.com/path"

    def test_negative_type_parsed(self):
        raw = """===SCENARIO===
ID: UAT-NEG-1
TITLE: Invalid access attempt
TYPE: NEGATIVE
PERSONA: Anonymous User
PASS CRITERIA: 403 Forbidden returned
ESTIMATED TIME: 2"""
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_parsed(self):
        raw = """===SCENARIO===
ID: UAT-BND-1
TITLE: Max income boundary
TYPE: BOUNDARY
PERSONA: Underwriter
PASS CRITERIA: System accepts 9999999
ESTIMATED TIME: 4"""
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_delimiter_only_string(self):
        raw = "===SCENARIO==="
        result = parse_scenarios(raw)
        # Block is empty after split → skipped
        assert result == []

    def test_three_scenarios_count(self):
        blocks = "\n".join([
            f"===SCENARIO===\nID: UAT-X-{i}\nTITLE: Test {i}\nTYPE: POSITIVE\n"
            for i in range(1, 4)
        ])
        result = parse_scenarios(blocks)
        assert len(result) == 3

    @pytest.mark.parametrize("field,prefix,value", [
        ("id", "ID:", "UAT-P-1"),
        ("title", "TITLE:", "Param Title"),
        ("type", "TYPE:", "POSITIVE"),
        ("persona", "PERSONA:", "Risk Analyst"),
        ("pass_criteria", "PASS CRITERIA:", "System returns 200"),
        ("estimated_time", "ESTIMATED TIME:", "10"),
    ])
    def test_parametrised_field_parsing(self, field, prefix, value):
        raw = f"===SCENARIO===\nID: UAT-P-1\n{prefix} {value}\n"
        result = parse_scenarios(raw)
        assert len(result) >= 1
        assert result[0].get(field) == value


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    """Tests for the build_test_pack_csv() function."""

    def _parse_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_header_row_correct(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        rows = self._parse_csv(result)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_row_count_matches_scenarios(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        rows = self._parse_csv(result)
        # 1 header + N scenario rows
        assert len(rows) == len(parsed_scenarios) + 1

    def test_scenario_data_in_rows(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        rows = self._parse_csv(result)
        ids = [row[0] for row in rows[1:]]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids

    def test_result_tester_notes_defect_ref_empty(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        rows = self._parse_csv(result)
        for row in rows[1:]:
            assert row[6] == ""   # Result
            assert row[7] == ""   # Tester
            assert row[8] == ""   # Notes
            assert row[9] == ""   # Defect Ref

    def test_empty_scenarios_list_returns_header_only(self):
        result = build_test_pack_csv([])
        rows = self._parse_csv(result)
        assert len(rows) == 1
        assert rows[0][0] == "Scenario ID"

    def test_output_is_string(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        assert isinstance(result, str)

    def test_missing_fields_handled_gracefully(self):
        """Scenarios missing optional fields should produce empty cells, not raise."""
        scenarios = [{"id": "UAT-INC-1"}]  # missing title, type, etc.
        result = build_test_pack_csv(scenarios)
        rows = self._parse_csv(result)
        assert len(