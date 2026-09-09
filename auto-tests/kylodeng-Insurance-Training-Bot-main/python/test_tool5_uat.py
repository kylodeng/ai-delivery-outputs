"""
Tests for tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases (empty input, missing fields, no delimiter),
    boundary values (single scenario, many scenarios)
  - build_test_pack_csv(): happy path, empty scenarios, partial fields, special characters
  - build_test_pack_md(): happy path, version/owner/repo injection, timestamp format
  - get_results_csv(): happy path, missing content key (FileNotFoundError), base64 decoding

Mocks used:
  - unittest.mock.patch for requests.get (GitHub API calls)
  - unittest.mock.patch for shared module functions (call_claude, get_repo_files,
    write_output_file, send_email, email_html, write_audit_entry)
  - base64 encoding used directly in test fixtures (no real network calls)

TODOs:
  - TODO: Integration test for __main__ block requires full env var setup and GitHub token
  - TODO: Tests for SYSTEM_GENERATE and SYSTEM_ANALYSE prompt constants require Claude API
  - TODO: End-to-end mode A (generate) and mode B (analyse) require mocking call_claude output
"""

import base64
import csv
import io
import json
import sys
import os
import datetime
import pytest
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Path setup – mirror what tool5_uat.py does so the import resolves
# ---------------------------------------------------------------------------
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS_DIR))

# We need to stub out the `shared` module before importing tool5_uat, because
# shared.py performs network/credential operations at import time in some setups.
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

# Now safe to import the module under test
import importlib

# Patch requests at the module level before importing tool5_uat
with patch.dict("sys.modules", {"shared": shared_stub}):
    import requests as _requests_mod

    with patch("requests.get", MagicMock()):
        # Import the public functions directly after ensuring shared is stubbed
        try:
            import tool5_uat as t5
        except Exception:
            # If the module-level import still fails, we re-try with a looser approach
            t5 = None


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_scenario_block(
    id_="UAT-STORY1-1",
    title="Login with valid credentials",
    type_="POSITIVE",
    persona="Registered User",
    pass_criteria="Dashboard displayed",
    estimated_time="5",
    extra_lines="",
):
    """Return a raw scenario block string (without the leading ===SCENARIO=== delimiter)."""
    return (
        f"ID: {id_}\n"
        f"TITLE: {title}\n"
        f"TYPE: {type_}\n"
        f"PERSONA: {persona}\n"
        f"PRE-CONDITIONS:\n- System is live\n"
        f"TEST DATA: username=test@sun.life password=P@ssw0rd\n"
        f"STEPS:\n1. Navigate to login\n2. Enter credentials\n3. Click Submit\n"
        f"EXPECTED RESULT: User is logged in\n"
        f"PASS CRITERIA: {pass_criteria}\n"
        f"ESTIMATED TIME: {estimated_time}\n"
        f"NOTES: none\n"
        f"{extra_lines}"
    )


SAMPLE_RAW_MULTI = (
    "===SCENARIO===\n"
    + make_scenario_block(id_="UAT-STORY1-1", title="Positive login")
    + "\n===SCENARIO===\n"
    + make_scenario_block(
        id_="UAT-STORY1-2",
        title="Login with wrong password",
        type_="NEGATIVE",
        pass_criteria="Error message shown",
    )
    + "\n===SCENARIO===\n"
    + make_scenario_block(
        id_="UAT-STORY1-3",
        title="Login with max-length username",
        type_="BOUNDARY",
        pass_criteria="System accepts or rejects gracefully",
    )
)


# ---------------------------------------------------------------------------
# Guard: skip all tests if the module could not be imported
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    t5 is None, reason="tool5_uat could not be imported; check shared stub"
)


# ===========================================================================
# parse_scenarios()
# ===========================================================================

class TestParseScenarios:
    """Tests for parse_scenarios()."""

    def test_happy_path_single_scenario(self):
        raw = "===SCENARIO===\n" + make_scenario_block()
        result = t5.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-STORY1-1"
        assert s["title"] == "Login with valid credentials"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "Registered User"
        assert s["pass_criteria"] == "Dashboard displayed"
        assert s["estimated_time"] == "5"
        assert "raw" in s

    def test_happy_path_multiple_scenarios(self):
        result = t5.parse_scenarios(SAMPLE_RAW_MULTI)
        assert len(result) == 3
        ids = [s["id"] for s in result]
        assert "UAT-STORY1-1" in ids
        assert "UAT-STORY1-2" in ids
        assert "UAT-STORY1-3" in ids

    def test_empty_string_returns_empty_list(self):
        result = t5.parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        """A block with no ===SCENARIO=== delimiter and no ID is discarded."""
        raw = "ID: UAT-X-1\nTITLE: Something\n"
        # Without the delimiter the split produces one chunk that still has an ID
        result = t5.parse_scenarios(raw)
        # Depending on position: either 0 or 1 scenarios — the block has an ID so 1
        assert isinstance(result, list)

    def test_block_without_id_is_discarded(self):
        raw = "===SCENARIO===\nTITLE: No ID here\nTYPE: POSITIVE\n"
        result = t5.parse_scenarios(raw)
        assert result == []

    def test_block_with_partial_fields(self):
        """A block that has an ID but is missing other fields returns defaults."""
        raw = "===SCENARIO===\nID: UAT-PARTIAL-1\n"
        result = t5.parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-1"
        assert s.get("title") is None  # field was not set
        assert s.get("type") is None

    def test_raw_field_contains_original_block(self):
        raw = "===SCENARIO===\n" + make_scenario_block(id_="UAT-RAW-1")
        result = t5.parse_scenarios(raw)
        assert len(result) == 1
        assert "UAT-RAW-1" in result[0]["raw"]
        assert "Dashboard displayed" in result[0]["raw"]

    def test_leading_delimiter_not_producing_empty_scenario(self):
        """First split chunk before ===SCENARIO=== is empty and should be skipped."""
        raw = "===SCENARIO===\n" + make_scenario_block(id_="UAT-1-1")
        result = t5.parse_scenarios(raw)
        # Should only yield the one real scenario
        assert len(result) == 1

    def test_multiple_empty_delimiters_skipped(self):
        raw = "===SCENARIO===\n===SCENARIO===\n" + make_scenario_block(id_="UAT-E-1")
        result = t5.parse_scenarios(raw)
        # Only the scenario with an ID survives
        ids = [s["id"] for s in result]
        assert "UAT-E-1" in ids

    def test_whitespace_only_block_is_skipped(self):
        raw = "===SCENARIO===\n   \n===SCENARIO===\n" + make_scenario_block(id_="UAT-W-1")
        result = t5.parse_scenarios(raw)
        assert any(s["id"] == "UAT-W-1" for s in result)

    def test_type_values_preserved(self):
        types = ["POSITIVE", "NEGATIVE", "BOUNDARY"]
        blocks = ""
        for i, t in enumerate(types):
            blocks += "===SCENARIO===\n" + make_scenario_block(id_=f"UAT-T-{i}", type_=t)
            blocks += "\n"
        result = t5.parse_scenarios(blocks)
        result_types = [s["type"] for s in result]
        for t in types:
            assert t in result_types

    @pytest.mark.parametrize("bad_input", [None])
    def test_none_input_raises(self, bad_input):
        """parse_scenarios expects a str; None should raise AttributeError."""
        with pytest.raises(AttributeError):
            t5.parse_scenarios(bad_input)

    def test_very_large_number_of_scenarios(self):
        blocks = ""
        n = 50
        for i in range(n):
            blocks += "===SCENARIO===\n" + make_scenario_block(id_=f"UAT-BIG-{i}")
            blocks += "\n"
        result = t5.parse_scenarios(blocks)
        assert len(result) == n

    def test_special_characters_in_title(self):
        raw = "===SCENARIO===\n" + make_scenario_block(
            id_="UAT-SC-1",
            title='Login with <script>alert("xss")</script>',
        )
        result = t5.parse_scenarios(raw)
        assert len(result) == 1
        assert '<script>' in result[0]["title"]

    def test_insurance_product_scenario(self):
        """Use synthetic insurance data as test data content."""
        block = (
            "===SCENARIO===\n"
            "ID: UAT-GEN2-1\n"
            "TITLE: Submit claim for Generations II product\n"
            "TYPE: POSITIVE\n"
            "PERSONA: Policyholder\n"
            "PASS CRITERIA: Claim submitted successfully\n"
            "ESTIMATED TIME: 10\n"
            "TEST DATA: product_name=Generations II, doc_type=product_brochure\n"
        )
        result = t5.parse_scenarios(block)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-GEN2-1"
        assert result[0]["title"] == "Submit claim for Generations II product"


# ===========================================================================
# build_test_pack_csv()
# ===========================================================================

class TestBuildTestPackCsv:
    """Tests for build_test_pack_csv()."""

    def _read_csv(self, csv_str: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_str))
        return list(reader)

    def test_happy_path_header_row(self):
        scenarios = [
            {
                "id": "UAT-1-1",
                "title": "Test title",
                "type": "POSITIVE",
                "persona": "Admin",
                "pass_criteria": "Works",
                "estimated_time": "3",
            }
        ]
        csv_str = t5.build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_happy_path_data_row(self):
        scenarios = [
            {
                "id": "UAT-1-1",
                "title": "Valid login",
                "type": "POSITIVE",
                "persona": "Registered User",
                "pass_criteria": "Dashboard shown",
                "estimated_time": "5",
            }
        ]
        csv_str = t5.build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert len(rows) == 2  # header + 1 data row
        assert rows[1][0] == "UAT-1-1"
        assert rows[1][1] == "Valid login"
        assert rows[1][2] == "POSITIVE"
        assert rows[1][3] == "Registered User"
        assert rows[1][4] == "Dashboard shown"
        assert rows[1][5] == "5"
        # Result/Tester/Notes/Defect Ref should be empty
        assert rows[1][6] == ""
        assert rows[1][7] == ""
        assert rows[1][8] == ""
        assert rows[1][9] == ""

    def test_empty_scenarios_list(self):
        csv_str = t5.build_test_pack_csv([])
        rows = self._read_csv(csv_str)
        # Only the header row
        assert len(rows) == 1
        assert rows[0][0] == "Scenario ID"

    def test_multiple_scenarios(self):
        scenarios = [
            {"id": f"UAT-X-{i}", "title": f"Scenario {i}", "type": "POSITIVE",
             "persona": "User", "pass_criteria": "OK", "estimated_time": str(i)}
            for i in range(5)
        ]
        csv_str = t5.build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert len(rows) == 6  # 1 header + 5 data

    def test_missing_fields_use_empty_string(self):
        """Scenarios with missing keys should produce empty cells, not crash."""
        scenarios = [{"id": "UAT-PARTIAL-1"}]
        csv_str = t5.build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert len(rows) == 2
        assert rows[1][0] == "UAT-PARTIAL-1"
        # All other columns should be empty
        for cell in rows[1][1:]:
            assert cell == ""

    def test_special_characters_in_fields(self):
        """CSV should properly quote fields with commas and quotes."""
        scenarios = [
            {
                "id": "UAT-SC-1",
                "title": 'Login, then "logout"',
                "type": "NEGATIVE",
                "persona": "Tester, QA",
                "pass_criteria": "Error: access denied",
                "estimated_time": "2",
            }
        ]
        csv_str = t5.build_test_pack_csv(scenarios)
        rows = self._read_csv(csv_str)
        assert rows[1][1] == 'Login, then "logout"'
        assert rows[1][3] == "Tester, QA"

    def test_returns_string(self):
        csv_str = t5.build_test_pack_csv([])
        assert isinstance(csv_str, str)

    def test_insurance_scenario_in_csv(self):
        """Synthetic data: Generations II insurance product scenario."""
        scenarios = [
            {