"""
Tests for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, missing fields, no ID), boundary values
  - build_test_pack_csv(): CSV structure, header row, data rows, empty scenarios list
  - build_test_pack_md(): Markdown structure, version/owner/repo injection, raw content inclusion
  - get_results_csv(): successful fetch, missing content key (FileNotFoundError), base64 decoding
  - SYSTEM_GENERATE / SYSTEM_ANALYSE: constant presence and content checks

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
  - base64 encoding/decoding verified with controlled payloads

TODOs:
  - TODO: Integration test for full __main__ block requires all env vars + live Claude + GitHub API
  - TODO: Test call_claude / write_output_file / send_email interactions (need shared module stubs)
  - TODO: Test build_test_pack_md timestamp format more precisely (datetime.utcnow patching)
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so we don't need the real file
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.GH_API = "https://api.github.com"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
shared_stub.call_claude = MagicMock(return_value="mocked claude response")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=True)
shared_stub.send_email = MagicMock()
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock()

sys.modules.setdefault("shared", shared_stub)

# Patch sys.path insertion side-effect before import
scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

# Now import the module under test
import importlib
import types

# Because the module uses `sys.path.insert(0, os.path.dirname(__file__))` and
# then `from shared import ...` we need to ensure our stub is in place before
# importing.  We already set sys.modules["shared"] above.
tool5 = importlib.import_module(
    "tool5_uat"
) if "tool5_uat" in sys.modules else None

# Fall back: load the source file directly
if tool5 is None:
    source_path = os.path.join(
        os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
    )
    if not os.path.exists(source_path):
        # Try relative to repo root
        source_path = os.path.join("tool5_uat.py")

    spec = importlib.util.spec_from_file_location(
        "tool5_uat",
        os.path.join(os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"),
    )
    tool5 = importlib.util.module_from_spec(spec)
    sys.modules["tool5_uat"] = tool5
    spec.loader.exec_module(tool5)

parse_scenarios = tool5.parse_scenarios
build_test_pack_csv = tool5.build_test_pack_csv
build_test_pack_md = tool5.build_test_pack_md
get_results_csv = tool5.get_results_csv
SYSTEM_GENERATE = tool5.SYSTEM_GENERATE
SYSTEM_ANALYSE = tool5.SYSTEM_ANALYSE


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """===SCENARIO===
ID: UAT-STORY1-1
TITLE: Successful underwriting risk classification
TYPE: POSITIVE
PERSONA: Underwriter
PRE-CONDITIONS:
- User is logged in
- Application form is complete
TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=LOW
STEPS:
1. Open application
2. Submit form
3. Review classification result
EXPECTED RESULT: System displays LOW risk classification
PASS CRITERIA: Classification displayed matches model output
ESTIMATED TIME: 5
NOTES: Verify CatBoostClassifier model version
"""

TWO_SCENARIO_BLOCK = """===SCENARIO===
ID: UAT-STORY1-1
TITLE: Scenario One
TYPE: POSITIVE
PERSONA: Admin
PASS CRITERIA: Result is shown
ESTIMATED TIME: 3
NOTES: none
===SCENARIO===
ID: UAT-STORY1-2
TITLE: Scenario Two
TYPE: NEGATIVE
PERSONA: Guest
PASS CRITERIA: Error displayed
ESTIMATED TIME: 2
NOTES: edge case
"""


def _make_scenario(
    sid="UAT-FEAT-1",
    title="Test Title",
    stype="POSITIVE",
    persona="Underwriter",
    pass_criteria="Pass if shown",
    estimated_time="5",
):
    return {
        "id": sid,
        "title": title,
        "type": stype,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": f"ID: {sid}\nTITLE: {title}",
    }


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_happy_path(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Successful underwriting risk classification"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Underwriter"
        assert s["pass_criteria"] == "Classification displayed matches model output"
        assert s["estimated_time"] == "5"

    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-STORY1-1"
        assert result[1]["id"] == "UAT-STORY1-2"

    def test_raw_field_present(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-STORY1-1" in result[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This has no scenario delimiter at all.")
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        block = "===SCENARIO===\nTITLE: Orphan\nTYPE: POSITIVE\n"
        result = parse_scenarios(block)
        assert result == []

    def test_scenario_missing_optional_fields_defaults_to_empty(self):
        block = "===SCENARIO===\nID: UAT-X-1\n"
        result = parse_scenarios(block)
        assert len(result) == 1
        s = result[0]
        assert s.get("title") is None or s.get("title") == ""
        assert s.get("type") is None or s.get("type") == ""
        assert s.get("persona") is None or s.get("persona") == ""

    def test_whitespace_only_blocks_ignored(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-OK-1\nTITLE: Good\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-OK-1"

    def test_multiple_colons_in_value_preserved(self):
        block = "===SCENARIO===\nID: UAT-T-1\nTITLE: Check URL: http://example.com\n"
        result = parse_scenarios(block)
        # TITLE: prefix stripped, rest preserved
        assert "http://example.com" in result[0].get("title", "")

    def test_negative_type_parsed(self):
        block = "===SCENARIO===\nID: UAT-NEG-1\nTYPE: NEGATIVE\n"
        result = parse_scenarios(block)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_parsed(self):
        block = "===SCENARIO===\nID: UAT-BND-1\nTYPE: BOUNDARY\n"
        result = parse_scenarios(block)
        assert result[0]["type"] == "BOUNDARY"

    def test_estimated_time_numeric_string(self):
        block = "===SCENARIO===\nID: UAT-T-1\nESTIMATED TIME: 10\n"
        result = parse_scenarios(block)
        assert result[0]["estimated_time"] == "10"

    @pytest.mark.parametrize("count", [5, 10, 20])
    def test_many_scenarios(self, count):
        blocks = "".join(
            f"===SCENARIO===\nID: UAT-P-{i}\nTITLE: Scenario {i}\n"
            for i in range(count)
        )
        result = parse_scenarios(blocks)
        assert len(result) == count

    def test_synthetic_data_in_notes_field_does_not_break_parsing(self):
        """Synthetic data from model_card.json used as TEST DATA value."""
        block = (
            "===SCENARIO===\n"
            "ID: UAT-RISK-1\n"
            "TITLE: Risk classification for Age=34\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Underwriter\n"
            "TEST DATA: Age=34, Annual_Income=75000, Risk_Classification=LOW\n"
            "PASS CRITERIA: LOW risk shown\n"
            "ESTIMATED TIME: 5\n"
        )
        result = parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-RISK-1"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def test_header_row_present(self):
        csv_str = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(csv_str))
        header = next(reader)
        assert header[0] == "Scenario ID"
        assert "Title" in header
        assert "Result (PASS/FAIL/BLOCKED)" in header
        assert "Defect Ref" in header

    def test_empty_scenarios_produces_only_header(self):
        csv_str = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_single_scenario_produces_two_rows(self):
        scenarios = [_make_scenario()]
        csv_str = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 2

    def test_data_row_values_correct(self):
        s = _make_scenario(
            sid="UAT-FEAT-1",
            title="My Test",
            stype="POSITIVE",
            persona="Admin",
            pass_criteria="All good",
            estimated_time="7",
        )
        csv_str = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_str))
        _ = next(reader)  # skip header
        row = next(reader)
        assert row[0] == "UAT-FEAT-1"
        assert row[1] == "My Test"
        assert row[2] == "POSITIVE"
        assert row[3] == "Admin"
        assert row[4] == "All good"
        assert row[5] == "7"

    def test_result_tester_defect_columns_empty_by_default(self):
        s = _make_scenario()
        csv_str = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_str))
        _ = next(reader)
        row = next(reader)
        # columns 6,7,8,9 = Result, Tester, Notes, Defect Ref
        assert row[6] == ""
        assert row[7] == ""
        assert row[8] == ""
        assert row[9] == ""

    def test_multiple_scenarios_all_written(self):
        scenarios = [_make_scenario(sid=f"UAT-X-{i}", title=f"T{i}") for i in range(5)]
        csv_str = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(csv_str))
        rows = list(reader)
        assert len(rows) == 6  # header + 5 data

    def test_missing_keys_produce_empty_strings(self):
        s = {"raw": "some raw text"}  # no id, title, etc.
        csv_str = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_str))
        _ = next(reader)
        row = next(reader)
        assert row[0] == ""
        assert row[1] == ""

    def test_csv_is_valid_utf8_string(self):
        scenarios = [_make_scenario()]
        result = build_test_pack_csv(scenarios)
        assert isinstance(result, str)
        result.encode("utf-8")  # should not raise

    @pytest.mark.parametrize("stype", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_scenario_types_written_verbatim(self, stype):
        s = _make_scenario(stype=stype)
        csv_str = build_test_pack_csv([s])
        assert stype in csv_str

    def test_special_characters_in_title(self):
        s = _make_scenario(title='Title with "quotes" and, commas')
        csv_str = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_str))
        _ = next(reader)
        row = next(reader)
        assert "quotes" in row[1]
        assert "commas" in row[1]


# ===========================================================================
# build_test_pack_md
# ===========================================================================

class TestBuildTestPackMd:

    def test_title_contains_owner_repo_version(self):
        md = build_test_pack_md("raw content", "my-org", "my-repo", "1.2.3")
        assert "my-org/my-repo" in md
        assert "v1.2.3" in md

    def test_raw_content_included(self):
        md = build_test_pack_md("===SCENARIO===\nID: UAT-1", "o", "r", "0.1")
        assert "===SCENARIO===" in md
        assert "UAT-1" in md

    def test_auto_generated_footer_present(self):
        md = build_test_pack_md("", "o", "r", "0.1")
        assert "Auto