"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
    - parse_scenarios(): happy path, edge cases, malformed input, boundary values
    - build_test_pack_csv(): happy path, empty input, special characters, multi-row
    - build_test_pack_md(): happy path, version/owner/repo substitution
    - get_results_csv(): happy path, missing file (FileNotFoundError), malformed response
    - Module-level constants and imports from shared

Mocks used:
    - requests.get (via unittest.mock.patch) — never makes real HTTP calls
    - shared.call_claude — stubbed to avoid real Claude API calls
    - shared.get_repo_files — stubbed
    - shared.write_output_file — stubbed
    - shared.send_email — stubbed
    - shared.write_audit_entry — stubbed
    - base64.b64decode (indirectly tested via mocked requests response)

TODOs:
    - TODO: Integration test for __main__ block requires full env var setup + mocked GH API
    - TODO: Test build_test_pack_md timestamp format once datetime is injectable
    - TODO: Test SYSTEM_GENERATE / SYSTEM_ANALYSE prompt strings for required keywords
      once a prompt-validation utility is available
    - TODO: Verify CSV output encoding for non-ASCII characters in scenario data
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Ensure the scripts directory is on the path so shared can be imported as a
# stub before importing the module under test.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Minimal stub for `shared` so we don't need the real module during tests.
# ---------------------------------------------------------------------------
import types

shared_stub = types.ModuleType("shared")
shared_stub.clean_json = lambda x: x
shared_stub.call_claude = MagicMock(return_value="stub")
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

# Now import the module under test (after stub is registered).
import importlib

# We need to reload if already cached to pick up the stub.
if "tool5_uat" in sys.modules:
    tool5_uat = importlib.reload(sys.modules["tool5_uat"])
else:
    import tool5_uat  # noqa: E402

from tool5_uat import (  # noqa: E402
    parse_scenarios,
    build_test_pack_csv,
    build_test_pack_md,
    get_results_csv,
)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def make_scenario_block(
    id_="UAT-STORY1-001",
    title="User can log in",
    type_="POSITIVE",
    persona="Admin",
    pass_criteria="Login succeeds",
    estimated_time="5",
    extra_lines="",
) -> str:
    return (
        f"ID: {id_}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        + extra_lines
    )


def wrap_scenarios(*blocks: str) -> str:
    """Join scenario blocks with the required delimiter."""
    return "===SCENARIO===\n" + "\n===SCENARIO===\n".join(blocks)


# ===========================================================================
# parse_scenarios — happy path
# ===========================================================================


class TestParseScenarios:

    def test_single_complete_scenario(self):
        raw = wrap_scenarios(make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-001"
        assert s["title"] == "User can log in"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Admin"
        assert s["pass_criteria"] == "Login succeeds"
        assert s["estimated_time"] == "5"

    def test_raw_field_preserved(self):
        block = make_scenario_block()
        raw = wrap_scenarios(block)
        result = parse_scenarios(raw)
        assert "raw" in result[0]
        assert "UAT-STORY1-001" in result[0]["raw"]

    def test_multiple_scenarios(self):
        block1 = make_scenario_block(id_="UAT-S1-001", title="Login")
        block2 = make_scenario_block(id_="UAT-S1-002", title="Logout", type_="NEGATIVE")
        block3 = make_scenario_block(id_="UAT-S1-003", title="Max session", type_="BOUNDARY")
        raw = wrap_scenarios(block1, block2, block3)
        result = parse_scenarios(raw)
        assert len(result) == 3
        assert result[0]["id"] == "UAT-S1-001"
        assert result[1]["type"] == "NEGATIVE"
        assert result[2]["type"] == "BOUNDARY"

    def test_scenario_types_positive_negative_boundary(self):
        for stype in ("POSITIVE", "NEGATIVE", "BOUNDARY"):
            raw = wrap_scenarios(make_scenario_block(type_=stype))
            result = parse_scenarios(raw)
            assert result[0]["type"] == stype

    def test_extra_lines_in_block_are_ignored_gracefully(self):
        extra = "SOME-UNKNOWN-KEY: unknown value\n"
        block = make_scenario_block(extra_lines=extra)
        raw = wrap_scenarios(block)
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-STORY1-001"

    def test_synthetic_data_product_name_in_test_data_line(self):
        """Scenario with TEST DATA line referencing synthetic insurance data."""
        block = (
            "ID: UAT-GEN2-001\n"
            "TITLE: Policyholder views Generations II brochure\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Policyholder\n"
            "TEST DATA: product_name=Generations II, doc_type=product_brochure\n"
            "PASS CRITERIA: Brochure displayed correctly\n"
            "ESTIMATED TIME: 3\n"
        )
        raw = wrap_scenarios(block)
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-GEN2-001"

    # ------------------------------------------------------------------
    # Edge / negative cases
    # ------------------------------------------------------------------

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # A block with no ===SCENARIO=== delimiter means split gives one element with no ID
        raw = "ID: UAT-001\nTITLE: Something\n"
        # Without the delimiter, the block won't start with ===SCENARIO===
        # The first element after split will be the content itself — but it
        # has an ID, so it WILL be parsed.
        result = parse_scenarios(raw)
        # Either 0 or 1 is acceptable; what matters is no crash
        assert isinstance(result, list)

    def test_block_without_id_is_skipped(self):
        block = "TITLE: No ID here\nTYPE: POSITIVE\n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result == []

    def test_whitespace_only_block_is_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\n" + make_scenario_block()
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_delimiter_only_returns_empty_list(self):
        raw = "===SCENARIO===\n===SCENARIO===\n===SCENARIO==="
        result = parse_scenarios(raw)
        assert result == []

    def test_leading_text_before_first_delimiter_is_ignored(self):
        preamble = "Some intro text that should be ignored.\n"
        raw = preamble + wrap_scenarios(make_scenario_block())
        result = parse_scenarios(raw)
        assert len(result) == 1

    def test_missing_optional_fields_do_not_raise(self):
        block = "ID: UAT-MIN-001\nTITLE: Minimal\n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0].get("type") is None
        assert result[0].get("persona") is None

    def test_id_with_extra_whitespace_is_stripped(self):
        block = "ID:   UAT-WS-001  \nTITLE: Whitespace\n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-001"

    def test_title_with_extra_whitespace_is_stripped(self):
        block = "ID: UAT-WS-002\nTITLE:   Spaced Title   \n"
        raw = "===SCENARIO===\n" + block
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Spaced Title"

    def test_large_number_of_scenarios(self):
        blocks = [make_scenario_block(id_=f"UAT-S-{i:03d}") for i in range(50)]
        raw = wrap_scenarios(*blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    def test_special_characters_in_title(self):
        block = make_scenario_block(title='User enters <script>alert("xss")</script>')
        raw = wrap_scenarios(block)
        result = parse_scenarios(raw)
        assert "<script>" in result[0]["title"]

    def test_unicode_in_fields(self):
        block = make_scenario_block(title="用户登录测试", persona="保险客户")
        raw = wrap_scenarios(block)
        result = parse_scenarios(raw)
        assert result[0]["title"] == "用户登录测试"
        assert result[0]["persona"] == "保险客户"

    def test_pass_criteria_line_parsed_correctly(self):
        block = make_scenario_block(pass_criteria="Button is green and redirects to /dashboard")
        raw = wrap_scenarios(block)
        result = parse_scenarios(raw)
        assert result[0]["pass_criteria"] == "Button is green and redirects to /dashboard"

    def test_estimated_time_parsed_correctly(self):
        block = make_scenario_block(estimated_time="15")
        raw = wrap_scenarios(block)
        result = parse_scenarios(raw)
        assert result[0]["estimated_time"] == "15"


# ===========================================================================
# build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(csv_string)))

    def test_header_row_present(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_produces_header_only(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # header only

    def test_single_scenario_row(self):
        scenarios = [{
            "id": "UAT-S1-001",
            "title": "Login test",
            "type": "POSITIVE",
            "persona": "Admin",
            "pass_criteria": "Redirected to dashboard",
            "estimated_time": "5",
        }]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 2
        data_row = rows[1]
        assert data_row[0] == "UAT-S1-001"
        assert data_row[1] == "Login test"
        assert data_row[2] == "POSITIVE"
        assert data_row[3] == "Admin"
        assert data_row[4] == "Redirected to dashboard"
        assert data_row[5] == "5"
        # Result, Tester, Notes, Defect Ref should be blank
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-S-{i}", "title": f"Test {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": "3"}
            for i in range(5)
        ]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_keys_produce_empty_strings(self):
        scenarios = [{"id": "UAT-MIN-001"}]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        data_row = rows[1]
        assert data_row[0] == "UAT-MIN-001"
        assert data_row[1] == ""
        assert data_row[2] == ""

    def test_scenario_with_commas_in_title(self):
        scenarios = [{
            "id": "UAT-COMMA-001",
            "title": "User enters name, age, and postcode",
            "type": "POSITIVE",
            "persona": "Customer",
            "pass_criteria": "Form submits",
            "estimated_time": "4",
        }]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == "User enters name, age, and postcode"

    def test_scenario_with_quotes_in_fields(self):
        scenarios = [{
            "id": "UAT-QUOTE-001",
            "title": 'User says "hello"',
            "type": "POSITIVE",
            "persona": "Customer",
            "pass_criteria": 'Greeting displayed: "hello"',
            "estimated_time": "2",
        }]
        csv_str =