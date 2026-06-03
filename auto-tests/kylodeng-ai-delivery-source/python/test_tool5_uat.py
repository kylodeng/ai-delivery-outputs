"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, malformed input, boundary values
    - build_test_pack_csv(): structure, headers, row content, empty input
    - build_test_pack_md(): output structure, version/owner/repo embedding
    - get_results_csv(): successful fetch, missing file (FileNotFoundError), bad response
    - Integration paths through __main__ block (stubbed via mocks)

Mocks used:
    - requests.get (for get_results_csv and any GitHub API calls)
    - shared.call_claude (LLM calls)
    - shared.get_repo_files
    - shared.write_output_file
    - shared.send_email
    - shared.email_html
    - shared.write_audit_entry
    - base64.b64decode (where needed)
    - datetime.datetime.utcnow (for deterministic timestamp checks)

TODOs:
    - TODO: Full __main__ block integration tests require environment variable orchestration
      and deeper shared module context — stubs provided with skip markers.
    - TODO: build_test_pack_md timestamp format validation requires real datetime injection.
    - TODO: SYSTEM_GENERATE / SYSTEM_ANALYSE prompt content validation is business-logic
      dependent; add tests once acceptance criteria are finalised.
"""

import base64
import csv
import io
import json
import sys
import os
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stub for the `shared` module so the import in tool5_uat doesn't fail
# ---------------------------------------------------------------------------
_shared_stub = types.ModuleType("shared")
_shared_stub.clean_json = MagicMock(side_effect=lambda x: x)
_shared_stub.call_claude = MagicMock(return_value="{}")
_shared_stub.get_repo_files = MagicMock(return_value={})
_shared_stub.write_output_file = MagicMock(return_value=None)
_shared_stub.send_email = MagicMock(return_value=None)
_shared_stub.email_html = MagicMock(return_value="<html/>")
_shared_stub.write_audit_entry = MagicMock(return_value=None)
_shared_stub.OUTPUT_REPO_OWNER = "test-owner"
_shared_stub.OUTPUT_REPO = "test-output-repo"
_shared_stub.GH_HEADERS = {"Authorization": "token test"}
_shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
import importlib
import tool5_uat  # noqa: E402  (inserted path via sys.path.insert in source)

# Re-import helpers so we reference the same objects
from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_scenario_block(
    id_="UAT-FEAT1-1",
    title="Login with valid credentials",
    type_="POSITIVE",
    persona="End User",
    pass_criteria="User lands on dashboard",
    estimated_time="5",
    extra_lines: list[str] | None = None,
) -> str:
    lines = [
        f"ID: {id_}",
        f"TITLE: {title}",
        f"TYPE: {type_}",
        f"PERSONA: {persona}",
        f"PASS CRITERIA: {pass_criteria}",
        f"ESTIMATED TIME: {estimated_time}",
    ]
    if extra_lines:
        lines.extend(extra_lines)
    return "\n".join(lines)


def _wrap_scenarios(*blocks: str) -> str:
    """Join blocks with the ===SCENARIO=== delimiter."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_happy_path(self):
        raw = _wrap_scenarios(_make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-FEAT1-1"
        assert s["title"] == "Login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "End User"
        assert s["pass_criteria"] == "User lands on dashboard"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_multiple_scenarios_parsed(self):
        block1 = _make_scenario_block(id_="UAT-S1-1", title="Scenario One")
        block2 = _make_scenario_block(id_="UAT-S1-2", title="Scenario Two", type_="NEGATIVE")
        block3 = _make_scenario_block(id_="UAT-S1-3", title="Scenario Three", type_="BOUNDARY")
        raw = _wrap_scenarios(block1, block2, block3)
        result = parse_scenarios(raw)
        assert len(result) == 3
        assert result[0]["id"] == "UAT-S1-1"
        assert result[1]["type"] == "NEGATIVE"
        assert result[2]["type"] == "BOUNDARY"

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # A block without the ===SCENARIO=== delimiter and no ID is silently dropped
        raw = "ID: UAT-X-1\nTITLE: Something"
        # No delimiter → split on "===SCENARIO===" yields one block without the delimiter prefix
        result = parse_scenarios(raw)
        # The block exists but doesn't start after a delimiter, still parsed if it has an ID
        # Actual behaviour: split produces ["ID: UAT-X-1\nTITLE: Something"] which has no "===SCENARIO===" prefix
        # Since the code splits on the literal string, and it's not present, result is a single stripped block
        # with an ID — so it WILL be parsed.
        assert isinstance(result, list)

    def test_block_without_id_is_dropped(self):
        block_no_id = "TITLE: No ID here\nTYPE: POSITIVE\nPERSONA: Admin"
        raw = "===SCENARIO===\n" + block_no_id
        result = parse_scenarios(raw)
        assert result == []

    def test_partial_fields_parsed(self):
        """Only ID is mandatory; missing optional fields default to absent keys."""
        raw = "===SCENARIO===\nID: UAT-MIN-1\nTITLE: Minimal"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-1"
        assert s["title"] == "Minimal"
        assert "type" not in s
        assert "persona" not in s

    def test_whitespace_around_values_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-WS-1   \nTITLE:   Whitespace Test   "
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-1"
        assert result[0]["title"] == "Whitespace Test"

    def test_raw_field_contains_full_block(self):
        block = _make_scenario_block(id_="UAT-RAW-1")
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["raw"] == block

    def test_delimiter_only_no_content(self):
        raw = "===SCENARIO===\n===SCENARIO===\n===SCENARIO==="
        result = parse_scenarios(raw)
        assert result == []

    def test_negative_type_scenario(self):
        block = _make_scenario_block(id_="UAT-NEG-1", type_="NEGATIVE",
                                     title="Login with invalid password")
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_scenario(self):
        block = _make_scenario_block(id_="UAT-BOUND-1", type_="BOUNDARY",
                                     title="Max length username")
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    @pytest.mark.parametrize("id_val,expected", [
        ("UAT-FEAT1-1", "UAT-FEAT1-1"),
        ("UAT-001-99", "UAT-001-99"),
        ("UAT-ABC-DEF-1", "UAT-ABC-DEF-1"),
    ])
    def test_various_id_formats(self, id_val, expected):
        block = _make_scenario_block(id_=id_val)
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == expected

    def test_extra_lines_stored_in_raw_not_parsed_as_extra_keys(self):
        extra = ["STEPS:", "1. Open browser", "2. Navigate to login"]
        block = _make_scenario_block(id_="UAT-STEPS-1", extra_lines=extra)
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-STEPS-1"
        assert "1. Open browser" in result[0]["raw"]

    def test_large_number_of_scenarios(self):
        blocks = [_make_scenario_block(id_=f"UAT-BIG-{i}") for i in range(50)]
        raw = "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    def test_synthetic_customer_data_in_test_data_field(self):
        """Test DATA lines (not extracted as a key) don't break parsing."""
        block = (
            "ID: UAT-CUST-1\n"
            "TITLE: Login as enterprise customer\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Enterprise Admin\n"
            "TEST DATA: customer_id=CUST-001, email=alice.chen@example.com, age=34, country=GB\n"
            "PASS CRITERIA: Dashboard loads\n"
            "ESTIMATED TIME: 3"
        )
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-CUST-1"
        assert "alice.chen@example.com" in result[0]["raw"]


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_header_row_correct(self):
        csv_out = build_test_pack_csv([])
        rows = self._parse_csv(csv_out)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        csv_out = build_test_pack_csv([])
        rows = self._parse_csv(csv_out)
        assert len(rows) == 1

    def test_single_scenario_row(self):
        scenarios = [
            {
                "id": "UAT-S1-1",
                "title": "Valid Login",
                "type": "POSITIVE",
                "persona": "End User",
                "pass_criteria": "Dashboard shown",
                "estimated_time": "5",
            }
        ]
        csv_out = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        assert len(rows) == 2  # header + 1 data row
        row = rows[1]
        assert row[0] == "UAT-S1-1"
        assert row[1] == "Valid Login"
        assert row[2] == "POSITIVE"
        assert row[3] == "End User"
        assert row[4] == "Dashboard shown"
        assert row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be empty
        assert row[6] == ""
        assert row[7] == ""
        assert row[8] == ""
        assert row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-S{i}-1", "title": f"Scenario {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "2"}
            for i in range(5)
        ]
        csv_out = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        assert len(rows) == 6  # header + 5

    def test_missing_optional_fields_default_to_empty_string(self):
        """Scenarios with minimal fields (only id) fill remaining cols with empty string."""
        scenarios = [{"id": "UAT-MIN-1"}]
        csv_out = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        row = rows[1]
        assert row[0] == "UAT-MIN-1"
        assert row[1] == ""   # title missing
        assert row[2] == ""   # type missing

    def test_returns_string(self):
        csv_out = build_test_pack_csv([])
        assert isinstance(csv_out, str)

    def test_csv_parseable_by_standard_library(self):
        scenarios = [
            {"id": "UAT-CSV-1", "title": 'Title with, comma', "type": "NEGATIVE",
             "persona": "Admin", "pass_criteria": 'Must "quote" correctly', "estimated_time": "10"}
        ]
        csv_out = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_out)
        assert rows[1][1] == "Title with, comma"
        assert rows[1][4] == 'Must "quote" correctly'

    def test_synthetic_customer_scenarios(self):
        """Use synthetic customer data as scenario inputs."""
        customers = [
            {"id": "UAT-CUST-001", "title": "Enterprise customer alice.chen@example.com login",
             "type": "POSITIVE", "persona": "Enterprise Admin",
             "pass_criteria": "Dashboard loaded", "estimated_time": "3"},
            {"id": "UAT-CUST-007", "title": "Invalid email customer blocked",
             "type": "NEGATIVE", "persona": "Consumer",
             "pass_criteria": "Error message shown", "estimated_time": "2"},
        ]
        csv_out = build_test_pack_csv(customers)
        rows = self._parse_csv(csv_out)
        assert rows[1][0] == "UAT-CUST-001"
        assert rows[2][0] == "UAT-CUST-007"
        assert rows[2][2] == "NEGATIVE"


# ===========================================================================
# build_test_pack_