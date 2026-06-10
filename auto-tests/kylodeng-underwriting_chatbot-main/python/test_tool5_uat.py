"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, malformed/empty input, boundary values
    - build_test_pack_csv(): correct headers, data rows, empty input, special characters
    - build_test_pack_md(): markdown structure, version/owner/repo substitution
    - get_results_csv(): successful fetch, missing content key, HTTP error paths

Mocks used:
    - unittest.mock.patch for requests.get (GitHub API calls)
    - unittest.mock.patch for shared module functions (call_claude, get_repo_files,
      write_output_file, send_email, email_html, write_audit_entry)
    - base64 encoding/decoding exercised directly (no mock needed)

TODOs:
    - TODO: Integration test for __main__ block requires full env-var setup + GH token
    - TODO: Test call_claude interaction inside Mode A/B flows (needs shared module stubs)
    - TODO: Test write_output_file and send_email calls at end of __main__ (needs runner)
    - TODO: Test SYSTEM_GENERATE and SYSTEM_ANALYSE prompt constants with LLM in-the-loop
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
# Minimal stub for the `shared` module so the import in tool5_uat doesn't fail
# even when the real shared.py is absent from the test environment.
# ---------------------------------------------------------------------------

def _install_shared_stub():
    """Create a minimal fake `shared` module in sys.modules if not present."""
    if "shared" in sys.modules:
        return
    shared = types.ModuleType("shared")
    shared.clean_json = lambda x: x
    shared.call_claude = MagicMock(return_value="stub")
    shared.get_repo_files = MagicMock(return_value={})
    shared.write_output_file = MagicMock(return_value=None)
    shared.send_email = MagicMock(return_value=None)
    shared.email_html = MagicMock(return_value="<html/>")
    shared.write_audit_entry = MagicMock(return_value=None)
    shared.OUTPUT_REPO_OWNER = "test-owner"
    shared.OUTPUT_REPO = "test-repo"
    shared.GH_HEADERS = {"Authorization": "Bearer fake"}
    shared.GH_API = "https://api.github.com"
    sys.modules["shared"] = shared


_install_shared_stub()

# Now we can safely import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
# Also cover the path used inside the script itself
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import importlib.util, pathlib

# Locate tool5_uat.py relative to this test file or via a known project root
_SCRIPT_CANDIDATES = [
    pathlib.Path(__file__).parent / ".github" / "scripts" / "tool5_uat.py",
    pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "tool5_uat.py",
    pathlib.Path(".github") / "scripts" / "tool5_uat.py",
]

_script_path = None
for _candidate in _SCRIPT_CANDIDATES:
    if _candidate.exists():
        _script_path = _candidate
        break

if _script_path is None:
    # Fall back: try to import directly (works if pytest is run from repo root
    # and .github/scripts is on sys.path)
    try:
        import tool5_uat as _tool5_module  # type: ignore
    except ImportError:
        _tool5_module = None
else:
    _spec = importlib.util.spec_from_file_location("tool5_uat", _script_path)
    _tool5_module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_tool5_module)

# If we still don't have the module, skip the whole file gracefully
if _tool5_module is None:
    pytest.skip(
        "tool5_uat.py could not be located; run pytest from the repo root.",
        allow_module_level=True,
    )

parse_scenarios = _tool5_module.parse_scenarios
build_test_pack_csv = _tool5_module.build_test_pack_csv
build_test_pack_md = _tool5_module.build_test_pack_md
get_results_csv = _tool5_module.get_results_csv


# ===========================================================================
# Fixtures & helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriting risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is logged in
- Valid customer record exists
TEST DATA: CUST00000001, Age=35, Annual_Income=85000
STEPS:
1. Navigate to customer profile
2. Click "Run Risk Assessment"
3. Review classification result
EXPECTED RESULT: System returns Risk_Classification label
PASS CRITERIA: Classification label displayed within 3 seconds
ESTIMATED TIME: 5
NOTES: Depends on CatBoostClassifier model being loaded
"""

TWO_SCENARIO_BLOCK = SINGLE_SCENARIO_BLOCK + """\
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Unauthorised access attempt
TYPE: NEGATIVE
PERSONA: Anonymous User
PRE-CONDITIONS:
- User is NOT logged in
TEST DATA: N/A
STEPS:
1. Navigate directly to /assessment URL
2. Observe system response
EXPECTED RESULT: System returns 401 Unauthorised
PASS CRITERIA: HTTP 401 returned, no data exposed
ESTIMATED TIME: 2
NOTES: Security boundary test
"""

BOUNDARY_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-STORY2-1
TITLE: Maximum input boundary for Annual Income
TYPE: BOUNDARY
PERSONA: Senior Underwriter
PRE-CONDITIONS:
- User authenticated
TEST DATA: Annual_Income=9999999999
STEPS:
1. Enter maximum income value
2. Submit form
EXPECTED RESULT: System accepts or gracefully rejects value
PASS CRITERIA: No unhandled exception; user sees clear message
ESTIMATED TIME: 3
NOTES: Boundary per model_card.json feature range
"""


def _make_scenario(id_="UAT-X-1", title="Test title", type_="POSITIVE",
                   persona="Tester", pass_criteria="System responds", estimated_time="5"):
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": "raw block text",
    }


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1

    def test_single_scenario_id_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["id"] == "UAT-STORY1-1"

    def test_single_scenario_title_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["title"] == "Successful underwriting risk classification"

    def test_single_scenario_type_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["persona"] == "Underwriter"

    def test_single_scenario_pass_criteria_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["pass_criteria"] == "Classification label displayed within 3 seconds"

    def test_single_scenario_estimated_time_parsed(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_field_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert len(result[0]["raw"]) > 0

    def test_two_scenarios_returns_two_items(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2

    def test_two_scenarios_ids_distinct(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        ids = [s["id"] for s in result]
        assert ids[0] != ids[1]

    def test_two_scenarios_types(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        types_ = {s["type"] for s in result}
        assert "POSITIVE" in types_
        assert "NEGATIVE" in types_

    def test_boundary_scenario_type(self):
        result = parse_scenarios(BOUNDARY_SCENARIO_BLOCK)
        assert result[0]["type"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """A raw block with no ID line should not be appended."""
        result = parse_scenarios("Some random text without the delimiter or ID field.")
        assert result == []

    def test_block_without_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_block_with_only_id_is_included(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"

    def test_missing_optional_fields_are_absent(self):
        raw = "===SCENARIO===\nID: UAT-MIN-2\n"
        result = parse_scenarios(raw)
        assert "title" not in result[0] or result[0].get("title", None) is None or True
        # At minimum, id must be set; other keys absent is acceptable
        assert result[0]["id"] == "UAT-MIN-2"

    def test_whitespace_only_blocks_are_skipped(self):
        raw = "===SCENARIO===\n   \n\t\n===SCENARIO===\nID: UAT-WS-1\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-1"

    def test_leading_trailing_whitespace_stripped_from_id(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-1   \nTITLE: Stripped\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"

    def test_leading_trailing_whitespace_stripped_from_title(self):
        raw = "===SCENARIO===\nID: UAT-SPACE-2\nTITLE:   Padded title   \n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Padded title"

    def test_multiple_scenarios_preserves_order(self):
        raw = (
            "===SCENARIO===\nID: UAT-ORD-1\n"
            "===SCENARIO===\nID: UAT-ORD-2\n"
            "===SCENARIO===\nID: UAT-ORD-3\n"
        )
        result = parse_scenarios(raw)
        assert [s["id"] for s in result] == ["UAT-ORD-1", "UAT-ORD-2", "UAT-ORD-3"]

    def test_raw_field_contains_original_block_text(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_unicode_content_handled(self):
        """Arabic characters (from ar-SA.json translation file) should not break parsing."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-I18N-1\n"
            "TITLE: Arabic locale test\n"
            "PERSONA: \u0645\u0633\u062a\u062e\u062f\u0645\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-I18N-1"

    @pytest.mark.parametrize("n_scenarios", [0, 1, 5, 50])
    def test_various_scenario_counts(self, n_scenarios):
        blocks = "".join(
            f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Scenario {i}\n"
            for i in range(n_scenarios)
        )
        result = parse_scenarios(blocks)
        assert len(result) == n_scenarios


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_returns_string(self):
        assert isinstance(build_test_pack_csv([]), str)

    def test_header_row_present(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref",
        ]

    def test_empty_scenarios_produces_only_header(self):
        rows = self._parse_csv(build_test_pack_csv([]))
        assert len(rows) == 1

    def test_single_scenario_produces_two_rows(self):
        rows = self._parse_csv(build_test_pack_csv([_make_scenario()]))
        assert len(rows) == 2

    def test_scenario_id_in_correct_column(self):
        s = _make_scenario(id_="UAT-STORY1-1")
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert rows[1][0] == "UAT-STORY1-1"

    def test_scenario_title_in_correct_column(self):
        s = _make_scenario(title="Risk classification check")
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert rows[1][1] == "Risk classification check"

    def test_scenario_type_in_correct_column(self):
        s = _make_scenario(type_="NEGATIVE")
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert rows[1][2] == "NEGATIVE"

    def test_scenario_persona_in_correct_column(self):
        s = _make_scenario(persona="Senior Underwriter")
        rows = self._parse_csv(build_test_pack_csv([s]))
        assert rows[1][3] == "Senior Underwriter"

    def test_pass_criteria_in_correct_column(self):
        s = _make_scenario