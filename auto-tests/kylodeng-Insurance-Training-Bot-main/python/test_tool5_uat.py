"""
Tests for tool5_uat.py

What is tested:
- parse_scenarios: happy path, edge cases (empty input, missing fields, no delimiter, partial blocks)
- build_test_pack_csv: happy path, empty scenarios, special characters, all fields present
- build_test_pack_md: happy path, version/owner/repo interpolation
- get_results_csv: happy path, file-not-found error
- Module-level __main__ block: NOT directly tested here (requires full env wiring)

Mocks used:
- requests.get (for get_results_csv)
- shared.call_claude (imported via tool5_uat)
- shared.get_repo_files
- shared.write_output_file
- shared.send_email
- shared.write_audit_entry
- base64.b64decode (via patching requests response)

TODOs:
- TODO: Integration test for __main__ block — requires full env vars + mocked GitHub API + mocked Claude
- TODO: Test parse_scenarios with Claude's actual output format variations (streaming artifacts, unicode)
- TODO: Test build_test_pack_csv encoding for non-ASCII personas/titles
- TODO: Test get_results_csv with pagination or large file content
- TODO: Test mode B (analyse) end-to-end — requires call_claude mock returning valid SYSTEM_ANALYSE JSON
"""

import base64
import csv
import io
import json
import os
import sys
import types
import unittest.mock as mock
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for the `shared` module so we don't need the real file
# ---------------------------------------------------------------------------

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = lambda s: s
shared_stub.call_claude = MagicMock(return_value="")
shared_stub.get_repo_files = MagicMock(return_value={})
shared_stub.write_output_file = MagicMock(return_value=None)
shared_stub.send_email = MagicMock(return_value=None)
shared_stub.email_html = MagicMock(return_value="<html/>")
shared_stub.write_audit_entry = MagicMock(return_value=None)
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib

# We need to reload if already cached without our stub
if "tool5_uat" in sys.modules:
    del sys.modules["tool5_uat"]

# Patch requests at import time so module-level code doesn't hit network
with patch.dict("sys.modules", {"shared": shared_stub}):
    import tool5_uat  # noqa: E402 – must come after stub injection

from tool5_uat import (
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
    parse_scenarios,
)


# ===========================================================================
# Fixtures / helpers
# ===========================================================================

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-001
TITLE: Purchase Generations II policy with valid data
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- User is logged in
- Product is active
TEST DATA: policy_number=GEN2-12345, dob=1980-01-01
STEPS:
1. Navigate to product page
2. Fill in personal details
3. Submit application
EXPECTED RESULT: Application is accepted and confirmation email sent
PASS CRITERIA: Confirmation screen displayed with policy number
ESTIMATED TIME: 10
NOTES: None
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-001
TITLE: Purchase Generations II policy — happy path
TYPE: POSITIVE
PERSONA: New customer
PRE-CONDITIONS:
- System is online
TEST DATA: name=John Doe, age=35
STEPS:
1. Open portal
2. Select product
3. Submit
EXPECTED RESULT: Policy issued
PASS CRITERIA: Policy number returned
ESTIMATED TIME: 5
NOTES: -
===SCENARIO===
ID: UAT-GEN2-002
TITLE: Submit with missing mandatory fields
TYPE: NEGATIVE
PERSONA: New customer
PRE-CONDITIONS:
- System is online
TEST DATA: name=, age=
STEPS:
1. Open portal
2. Leave fields blank
3. Submit
EXPECTED RESULT: Validation error shown
PASS CRITERIA: Error message visible
ESTIMATED TIME: 3
NOTES: boundary check
"""

SCENARIO_WITHOUT_ID = """\
===SCENARIO===
TITLE: Orphan scenario without ID
TYPE: POSITIVE
PERSONA: Admin
STEPS:
1. Do something
EXPECTED RESULT: Something happens
PASS CRITERIA: It works
ESTIMATED TIME: 2
NOTES: None
"""


def _make_scenario(**overrides):
    base = {
        "id": "UAT-TEST-001",
        "title": "Sample scenario",
        "type": "POSITIVE",
        "persona": "Admin user",
        "pass_criteria": "Screen shows success",
        "estimated_time": "5",
        "raw": "raw block text",
    }
    base.update(overrides)
    return base


# ===========================================================================
# parse_scenarios
# ===========================================================================


class TestParseScenarios:
    def test_single_valid_scenario(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-GEN2-001"
        assert s["title"] == "Purchase Generations II policy with valid data"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Existing policyholder"
        assert s["pass_criteria"] == "Confirmation screen displayed with policy number"
        assert s["estimated_time"] == "10"
        assert "raw" in s
        assert "UAT-GEN2-001" in s["raw"]

    def test_two_scenarios(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        ids = [s["id"] for s in result]
        assert "UAT-GEN2-001" in ids
        assert "UAT-GEN2-002" in ids

    def test_second_scenario_negative_type(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        neg = next(s for s in result if s["id"] == "UAT-GEN2-002")
        assert neg["type"] == "NEGATIVE"

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """If there is no ===SCENARIO=== delimiter, no scenarios are parsed."""
        raw = "ID: UAT-001\nTITLE: Something\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_scenario_without_id_is_excluded(self):
        result = parse_scenarios(SCENARIO_WITHOUT_ID)
        assert result == []

    def test_scenario_missing_optional_fields(self):
        """Scenarios with ID but missing optional keys should still be included."""
        raw = "===SCENARIO===\nID: UAT-MIN-001\nTITLE: Minimal\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-001"
        assert s.get("type") is None  # field absent — should not raise
        assert s.get("persona") is None

    def test_whitespace_only_blocks_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\nID: UAT-WS-001\nTITLE: Valid\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-WS-001"

    def test_raw_field_contains_full_block(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        raw = result[0]["raw"]
        assert "STEPS:" in raw
        assert "EXPECTED RESULT:" in raw

    def test_multiple_colons_in_value_parsed_correctly(self):
        """Values containing colons should not be truncated at the first colon."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-COL-001\n"
            "TITLE: Test with colon: value here\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: tester\n"
            "PASS CRITERIA: result: success\n"
            "ESTIMATED TIME: 7\n"
        )
        result = parse_scenarios(raw)
        # The implementation uses line.replace("TITLE:", "").strip(), so the
        # rest of the value (including further colons) is preserved.
        assert result[0]["title"] == "Test with colon: value here"
        assert result[0]["pass_criteria"] == "result: success"

    def test_boundary_type_scenario(self):
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-BOUND-001\n"
            "TITLE: Max character input\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: Power user\n"
            "PASS CRITERIA: System accepts max length\n"
            "ESTIMATED TIME: 4\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_large_number_of_scenarios(self):
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\n"
                f"ID: UAT-LARGE-{i:03d}\n"
                f"TITLE: Scenario {i}\n"
                f"TYPE: POSITIVE\n"
                f"PERSONA: user\n"
                f"PASS CRITERIA: pass\n"
                f"ESTIMATED TIME: 1\n"
            )
        result = parse_scenarios("\n".join(blocks))
        assert len(result) == 50

    def test_insurance_synthetic_data_in_scenario(self):
        """Verify synthetic data values (e.g. Generations II) appear in raw block."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-INS-001\n"
            "TITLE: Purchase Generations II plan\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Existing policyholder\n"
            "TEST DATA: product_name=Generations II, doc_type=product_brochure\n"
            "PASS CRITERIA: Policy issued\n"
            "ESTIMATED TIME: 8\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-INS-001"
        assert "Generations II" in result[0]["raw"]


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    def test_header_row_present(self):
        csv_text = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(csv_text))
        header = next(reader)
        assert header[0] == "Scenario ID"
        assert "Result (PASS/FAIL/BLOCKED)" in header
        assert "Defect Ref" in header

    def test_empty_scenarios_produces_only_header(self):
        csv_text = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 1  # header only

    def test_single_scenario_row(self):
        s = _make_scenario()
        csv_text = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 2  # header + 1 data row
        data_row = rows[1]
        assert data_row[0] == "UAT-TEST-001"
        assert data_row[1] == "Sample scenario"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Admin user"
        assert data_row[4] == "Screen shows success"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [_make_scenario(id=f"UAT-T-{i:03d}") for i in range(5)]
        csv_text = build_test_pack_csv(scenarios)
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_fields_become_empty_strings(self):
        s = {"raw": "raw", "id": "UAT-MISS-001"}  # no other fields
        csv_text = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        data_row = rows[1]
        assert data_row[0] == "UAT-MISS-001"
        assert data_row[1] == ""  # title missing
        assert data_row[2] == ""  # type missing

    def test_special_characters_in_title(self):
        """Commas and quotes in scenario data must be handled by csv module."""
        s = _make_scenario(title='Title with "quotes" and, commas')
        csv_text = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert rows[1][1] == 'Title with "quotes" and, commas'

    def test_output_is_string(self):
        result = build_test_pack_csv([_make_scenario()])
        assert isinstance(result, str)

    def test_ten_columns_per_row(self):
        s = _make_scenario()
        csv_text = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        for row in rows:
            assert len(row) == 10

    def test_negative_scenario_type_written(self):
        s = _make_scenario(type="NEGATIVE", id="UAT-NEG-001")
        csv_text = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert rows[1][2] == "NEGATIVE"

    def test_boundary_scenario_type_written(self):
        s = _make_scenario(type="BOUNDARY", id="UAT-BND-001")
        csv_text = build_test_pack_csv([s])
        reader = csv.reader(io.StringIO(csv_text))
        rows = list(reader)
        assert rows[1][2] == "BOUNDARY"

    def test_insurance_scenario_in_csv(self):
        """Use synthetic data: Generations II product scenario."""
        s = _make_scenario(
            id="UAT-GEN2-001",
            title="Purchase Generations II plan",
            persona="Existing policyholder",
            pass_criteria="Policy issued with confirmation",
        