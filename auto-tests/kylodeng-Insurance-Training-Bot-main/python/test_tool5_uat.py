"""
Tests for tool5_uat.py
======================
What is tested:
  - parse_scenarios(): happy path, edge cases, missing fields, empty input, no delimiter
  - build_test_pack_csv(): CSV structure, row content, empty scenarios, special characters
  - build_test_pack_md(): markdown structure, version/owner/repo injection
  - get_results_csv(): successful fetch, missing content key, FileNotFoundError

Mocks used:
  - requests.get (for get_results_csv GitHub API calls)
  - shared.call_claude (not directly tested here but patched where imported)
  - base64.b64decode (via requests mock response)

TODOs:
  - TODO: Integration tests for __main__ block require full env setup + GitHub API credentials
  - TODO: Tests for write_output_file, send_email, write_audit_entry require shared module stubs
  - TODO: Tests for get_repo_files require GitHub API mocking at the shared module level
  - TODO: End-to-end mode A (generate) test requires call_claude stub returning realistic scenario text
  - TODO: End-to-end mode B (analyse) test requires call_claude stub returning realistic JSON
"""

import base64
import csv
import io
import json
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Path bootstrap – mirrors what the source file does so imports resolve
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

# We must stub the `shared` module before importing tool5_uat, otherwise the
# real shared module (which may not exist in the test environment) will fail.
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer test-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

# Now import the module under test
import importlib
import types

# Build a minimal module from source without executing __main__
def _load_tool5():
    """Import tool5_uat with __name__ set to a non-main value so the
    ``if __name__ == "__main__"`` block is never executed."""
    spec_path = os.path.join(
        os.path.dirname(__file__), "..", ".github", "scripts", "tool5_uat.py"
    )
    # Fallback: try sibling directory (running from repo root)
    if not os.path.exists(spec_path):
        spec_path = os.path.join(
            os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
        )
    if not os.path.exists(spec_path):
        pytest.skip("tool5_uat.py not found – adjust path if needed")

    loader = importlib.machinery.SourceFileLoader("tool5_uat", spec_path)
    spec = importlib.util.spec_from_loader("tool5_uat", loader)
    mod = types.ModuleType("tool5_uat")
    mod.__spec__ = spec
    # Inject the stub so the module's `from shared import …` resolves
    sys.modules["tool5_uat"] = mod
    loader.exec_module(mod)
    return mod


try:
    tool5 = _load_tool5()
    parse_scenarios = tool5.parse_scenarios
    build_test_pack_csv = tool5.build_test_pack_csv
    build_test_pack_md = tool5.build_test_pack_md
    get_results_csv = tool5.get_results_csv
except Exception as exc:  # pragma: no cover
    tool5 = None
    _LOAD_ERROR = exc


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-001
TITLE: Purchase Generations II policy
TYPE: POSITIVE
PERSONA: New policyholder
PRE-CONDITIONS:
- User is logged in
- Product catalogue is available
TEST DATA: policy_id=GEN2-TEST-001, sum_assured=500000
STEPS:
1. Navigate to product page
2. Select Generations II
3. Complete application form
EXPECTED RESULT: Policy is issued successfully
PASS CRITERIA: Policy number is displayed and confirmation email is sent
ESTIMATED TIME: 15
NOTES: Requires UAT environment with test product catalogue
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-HEALTH-001
TITLE: Claim against designated hospital
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active
TEST DATA: hospital=Shanghai General, claim_amount=10000
STEPS:
1. Submit claim form
2. Attach hospital receipt
EXPECTED RESULT: Claim is accepted
PASS CRITERIA: Claim status = APPROVED
ESTIMATED TIME: 10
NOTES: None
===SCENARIO===
ID: UAT-HEALTH-002
TITLE: Claim against non-designated hospital - negative
TYPE: NEGATIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active
TEST DATA: hospital=Unknown Clinic, claim_amount=5000
STEPS:
1. Submit claim form
2. Attach hospital receipt
EXPECTED RESULT: Claim is rejected with reason code
PASS CRITERIA: Claim status = REJECTED
ESTIMATED TIME: 10
NOTES: Boundary for network validation
"""


# ---------------------------------------------------------------------------
# parse_scenarios
# ---------------------------------------------------------------------------

class TestParseScenarios:

    def test_single_scenario_happy_path(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-GEN2-001"
        assert s["title"] == "Purchase Generations II policy"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "New policyholder"
        assert s["pass_criteria"] == "Policy number is displayed and confirmation email is sent"
        assert s["estimated_time"] == "15"
        assert "raw" in s

    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        assert result[0]["id"] == "UAT-HEALTH-001"
        assert result[1]["id"] == "UAT-HEALTH-002"

    def test_raw_field_contains_original_block(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "UAT-GEN2-001" in result[0]["raw"]
        assert "Purchase Generations II policy" in result[0]["raw"]

    def test_empty_string_returns_empty_list(self):
        assert parse_scenarios("") == []

    def test_no_delimiter_returns_empty_list(self):
        # A block with no ===SCENARIO=== and no ID: → skipped
        assert parse_scenarios("some random text without delimiter") == []

    def test_delimiter_present_but_no_id_skipped(self):
        raw = "===SCENARIO===\nTITLE: Orphan scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_partial_fields_still_parsed(self):
        """Scenarios missing optional fields should not raise."""
        raw = "===SCENARIO===\nID: UAT-PARTIAL-001\nTITLE: Partial\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-PARTIAL-001"
        assert s.get("type") is None
        assert s.get("persona") is None
        assert s.get("pass_criteria") is None
        assert s.get("estimated_time") is None

    def test_multiple_delimiters_leading_empty_block_ignored(self):
        raw = "===SCENARIO===\n===SCENARIO===\nID: UAT-X-001\nTITLE: Valid\n"
        result = parse_scenarios(raw)
        # First block has no ID so is skipped; second has ID
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-001"

    def test_id_with_extra_whitespace_is_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-001   \nTITLE: Spaced\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-001"

    def test_title_with_extra_whitespace_is_stripped(self):
        raw = "===SCENARIO===\nID: UAT-T-001\nTITLE:   Trimmed Title   \n"
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Trimmed Title"

    @pytest.mark.parametrize("scenario_type", ["POSITIVE", "NEGATIVE", "BOUNDARY"])
    def test_type_values(self, scenario_type):
        raw = f"===SCENARIO===\nID: UAT-TYPE-001\nTYPE: {scenario_type}\n"
        result = parse_scenarios(raw)
        assert result[0].get("type") == scenario_type

    def test_large_number_of_scenarios(self):
        """Performance / robustness: 50 scenarios."""
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\n"
                f"ID: UAT-BULK-{i:03d}\n"
                f"TITLE: Bulk scenario {i}\n"
                f"TYPE: POSITIVE\n"
            )
        raw = "".join(blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50
        assert result[49]["id"] == "UAT-BULK-049"

    def test_insurance_synthetic_data_in_test_data_field(self):
        """Synthetic data from Generations II should survive in the raw field."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-GEN2-010\n"
            "TITLE: Verify Generations II double bonus\n"
            "TYPE: POSITIVE\n"
            "PERSONA: New policyholder\n"
            "TEST DATA: product_name=Generations II, sum_assured=500000, bonus_type=double\n"
            "PASS CRITERIA: Bonus is doubled on anniversary\n"
            "ESTIMATED TIME: 20\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert "Generations II" in result[0]["raw"]

    def test_hospital_claim_negative_scenario(self):
        """Synthetic data: non-designated hospital rejection."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-HOSP-NEG-001\n"
            "TITLE: Claim denied for non-network hospital\n"
            "TYPE: NEGATIVE\n"
            "PERSONA: Existing policyholder\n"
            "TEST DATA: hospital=Unknown Clinic, claim_amount=5000\n"
            "PASS CRITERIA: Claim status = REJECTED\n"
            "ESTIMATED TIME: 10\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"
        assert "REJECTED" in result[0]["pass_criteria"]


# ---------------------------------------------------------------------------
# build_test_pack_csv
# ---------------------------------------------------------------------------

class TestBuildTestPackCsv:

    def _parse_csv(self, csv_string: str) -> list[list[str]]:
        reader = csv.reader(io.StringIO(csv_string))
        return list(reader)

    def test_header_row_present(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert rows[0] == [
            "Scenario ID", "Title", "Type", "Persona", "Pass Criteria",
            "Est. Time (min)", "Result (PASS/FAIL/BLOCKED)", "Tester", "Notes", "Defect Ref"
        ]

    def test_empty_scenarios_only_header(self):
        csv_str = build_test_pack_csv([])
        rows = self._parse_csv(csv_str)
        assert len(rows) == 1  # header only

    def test_single_scenario_produces_two_rows(self):
        scenarios = [{"id": "UAT-001", "title": "Test A", "type": "POSITIVE",
                      "persona": "Admin", "pass_criteria": "Logged in",
                      "estimated_time": "5"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 2
        assert rows[1][0] == "UAT-001"
        assert rows[1][1] == "Test A"

    def test_result_tester_notes_defect_columns_empty(self):
        scenarios = [{"id": "UAT-002", "title": "B", "type": "NEGATIVE",
                      "persona": "User", "pass_criteria": "Error shown",
                      "estimated_time": "3"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        data_row = rows[1]
        # columns 6,7,8,9 should be empty
        assert data_row[6] == ""
        assert data_row[7] == ""
        assert data_row[8] == ""
        assert data_row[9] == ""

    def test_missing_fields_default_to_empty_string(self):
        scenarios = [{"id": "UAT-003"}]  # only id present
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert rows[1][0] == "UAT-003"
        assert rows[1][1] == ""  # title missing

    def test_multiple_scenarios_correct_row_count(self):
        scenarios = [
            {"id": f"UAT-{i:03d}", "title": f"Scenario {i}",
             "type": "POSITIVE", "persona": "User",
             "pass_criteria": "OK", "estimated_time": "5"}
            for i in range(10)
        ]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows) == 11  # 1 header + 10 data

    def test_special_characters_in_title_csv_safe(self):
        """Commas, quotes, newlines in title should be properly escaped by csv module."""
        scenarios = [{"id": "UAT-SPEC-001",
                      "title": 'Claim with "special" chars, and comma',
                      "type": "POSITIVE", "persona": "User",
                      "pass_criteria": "OK", "estimated_time": "5"}]
        csv_str = build_test_pack_csv(scenarios)
        rows = self._parse_csv(csv_str)
        assert rows[1][1] == 'Claim with "special" chars, and comma'

    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    def test_column_count_ten(self):
        scenarios = [{"id": "UAT-COL-001", "title": "T", "type": "POSITIVE",
                      "persona": "P", "pass_criteria": "C", "estimated_time": "1"}]
        rows = self._parse_csv(build_test_pack_csv(scenarios))
        assert len(rows[0]) == 10
        assert len(rows[1]) == 10

    @pytest.mark.parametrize("scenario_input,expected_type", [
        ({"id": "UAT-P-001", "type": "POSITIVE"}, "POSITIVE"),
        ({"id": "UAT-N