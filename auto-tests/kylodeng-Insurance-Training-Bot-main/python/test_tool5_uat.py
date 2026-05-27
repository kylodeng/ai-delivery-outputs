"""
Tests for tool5_uat.py
======================

What is tested:
- parse_scenarios(): happy path, edge cases, missing fields, malformed input, boundary values
- build_test_pack_csv(): output structure, header row, data rows, empty input, special characters
- build_test_pack_md(): output formatting, metadata embedding, empty raw content
- get_results_csv(): successful fetch, missing content key, HTTP/decode errors
- Module-level helpers indirectly exercised through the above

Mocks used:
- requests.get (patched via unittest.mock.patch) — prevents real GitHub API calls
- shared.call_claude — not directly tested here (called in __main__ block)
- base64.b64decode — exercised through get_results_csv tests
- datetime.datetime — patched for deterministic timestamp assertions

TODOs:
- TODO: Integration tests for __main__ block require full env-var setup and mocked GitHub + Claude APIs
- TODO: build_test_pack_md timezone-sensitive timestamp test needs CI timezone control
- TODO: Tests for write_output_file, send_email, write_audit_entry require shared module stubs
- TODO: parse_scenarios PRE-CONDITIONS and STEPS multi-line block parsing (not currently extracted into dict)
"""

import base64
import csv
import io
import json
import sys
import os
import datetime
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure the scripts directory is importable without a real `shared` module
# ---------------------------------------------------------------------------

# We must stub out `shared` before importing tool5_uat, because it is imported
# at module level and would fail without the real repo secrets / network.
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

# Now we can safely import the module under test
import importlib, types

# Build a minimal fake module so we can import just the pure functions
# without executing the __main__ block.
# We import the source as text and exec only the function definitions.
_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), ".github", "scripts", "tool5_uat.py"
)

# Attempt a direct import; if the file path doesn't exist in the test runner
# environment, fall back to importing via sys.path manipulation.
try:
    import importlib.util

    spec = importlib.util.spec_from_file_location("tool5_uat", _SCRIPT_PATH)
    _mod = importlib.util.module_from_spec(spec)
    # Prevent __main__ execution during import
    with patch.dict(os.environ, {}, clear=False):
        spec.loader.exec_module(_mod)  # type: ignore[union-attr]
except (FileNotFoundError, AttributeError, TypeError):
    # If the file is already on sys.path (e.g., when tests run from repo root)
    try:
        import tool5_uat as _mod  # type: ignore[import]
    except ImportError:
        _mod = None  # type: ignore[assignment]

# If we still have no module, skip everything gracefully
_MODULE_AVAILABLE = _mod is not None

if _MODULE_AVAILABLE:
    parse_scenarios = _mod.parse_scenarios
    build_test_pack_csv = _mod.build_test_pack_csv
    build_test_pack_md = _mod.build_test_pack_md
    get_results_csv = _mod.get_results_csv
else:
    # Provide stubs so the file is at least parseable
    def parse_scenarios(raw):  # type: ignore[misc]
        raise NotImplementedError

    def build_test_pack_csv(scenarios):  # type: ignore[misc]
        raise NotImplementedError

    def build_test_pack_md(raw, owner, repo, version):  # type: ignore[misc]
        raise NotImplementedError

    def get_results_csv(owner, repo, results_path):  # type: ignore[misc]
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-GEN2-001
TITLE: Purchase Generations II whole life policy as new customer
TYPE: POSITIVE
PERSONA: New retail customer
PRE-CONDITIONS:
- Customer is logged in
- Product is available in their region
TEST DATA: product_name=Generations II, customer_age=35, premium=5000
STEPS:
1. Navigate to product page
2. Click "Get a Quote"
3. Fill in personal details and submit
EXPECTED RESULT: Quote is generated and confirmation email sent
PASS CRITERIA: Quote reference number displayed within 5 seconds
ESTIMATED TIME: 10
NOTES: Uses Sun Life Generations II brochure data
"""

TWO_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-HEALTH-001
TITLE: Verify cashless arrangement at designated mainland China hospital
TYPE: POSITIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active
- Hospital is in Global Network Hospital List
TEST DATA: hospital=Shanghai General Hospital, policy_id=POL-12345
STEPS:
1. Locate hospital in the cashless network list
2. Present insurance card at admissions
3. Confirm pre-authorisation request submitted
EXPECTED RESULT: Pre-authorisation approved within 2 hours
PASS CRITERIA: Approval reference displayed in mobile app
ESTIMATED TIME: 15
NOTES: Refer to Network_Hospitals_with_Cashless_Arrangement document
===SCENARIO===
ID: UAT-HEALTH-002
TITLE: Attempt claim at non-designated hospital — negative case
TYPE: NEGATIVE
PERSONA: Existing policyholder
PRE-CONDITIONS:
- Policy is active
- Hospital is NOT in the designated list
TEST DATA: hospital=Unknown Private Clinic, policy_id=POL-12345
STEPS:
1. Submit claim for non-designated hospital
2. Confirm rejection message received
EXPECTED RESULT: System rejects claim with clear error message
PASS CRITERIA: Error code HOSP-404 displayed
ESTIMATED TIME: 5
NOTES: Boundary: empty hospital name should also be tested
"""

MINIMAL_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-MIN-001
TITLE: Minimal scenario
TYPE: BOUNDARY
PERSONA: Admin
"""


def _make_scenario(
    id_="UAT-GEN2-001",
    title="Test title",
    type_="POSITIVE",
    persona="Customer",
    pass_criteria="Widget appears",
    estimated_time="5",
):
    return {
        "id": id_,
        "title": title,
        "type": type_,
        "persona": persona,
        "pass_criteria": pass_criteria,
        "estimated_time": estimated_time,
        "raw": "raw block text",
    }


# ---------------------------------------------------------------------------
# Guard decorator — skip all tests if module could not be loaded
# ---------------------------------------------------------------------------

skip_if_no_module = pytest.mark.skipif(
    not _MODULE_AVAILABLE,
    reason="tool5_uat module could not be imported — check script path and shared stub",
)


# ===========================================================================
# Tests: parse_scenarios
# ===========================================================================


class TestParseScenarios:
    @skip_if_no_module
    def test_single_scenario_all_fields(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-GEN2-001"
        assert s["title"] == "Purchase Generations II whole life policy as new customer"
        assert s["type"] == "POSITIVE"
        assert s["persona"] == "New retail customer"
        assert s["pass_criteria"] == "Quote reference number displayed within 5 seconds"
        assert s["estimated_time"] == "10"

    @skip_if_no_module
    def test_single_scenario_raw_preserved(self):
        result = parse_scenarios(SINGLE_SCENARIO_BLOCK)
        assert "raw" in result[0]
        assert "UAT-GEN2-001" in result[0]["raw"]

    @skip_if_no_module
    def test_two_scenarios_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        assert len(result) == 2
        ids = [s["id"] for s in result]
        assert "UAT-HEALTH-001" in ids
        assert "UAT-HEALTH-002" in ids

    @skip_if_no_module
    def test_scenario_types_parsed(self):
        result = parse_scenarios(TWO_SCENARIO_BLOCK)
        types = {s["id"]: s["type"] for s in result}
        assert types["UAT-HEALTH-001"] == "POSITIVE"
        assert types["UAT-HEALTH-002"] == "NEGATIVE"

    @skip_if_no_module
    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    @skip_if_no_module
    def test_no_delimiter_returns_empty_list(self):
        result = parse_scenarios("This is just some text with no delimiter.")
        assert result == []

    @skip_if_no_module
    def test_delimiter_only_returns_empty_list(self):
        result = parse_scenarios("===SCENARIO===")
        # Single empty block after split — no ID, so should be excluded
        assert result == []

    @skip_if_no_module
    def test_minimal_scenario_missing_optional_fields(self):
        result = parse_scenarios(MINIMAL_SCENARIO_BLOCK)
        assert len(result) == 1
        s = result[0]
        assert s["id"] == "UAT-MIN-001"
        assert s["title"] == "Minimal scenario"
        assert s["type"] == "BOUNDARY"
        assert s["persona"] == "Admin"
        # Optional fields absent → not in dict or empty
        assert s.get("pass_criteria", "") == ""
        assert s.get("estimated_time", "") == ""

    @skip_if_no_module
    def test_block_without_id_is_excluded(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    @skip_if_no_module
    def test_multiple_delimiters_leading_empty_block(self):
        raw = "===SCENARIO===\n===SCENARIO===\nID: UAT-X-001\nTITLE: Valid\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-X-001"

    @skip_if_no_module
    def test_whitespace_trimmed_from_field_values(self):
        raw = "===SCENARIO===\nID:   UAT-WS-001   \nTITLE:   Whitespace test   \n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-WS-001"
        assert result[0]["title"] == "Whitespace test"

    @skip_if_no_module
    def test_insurance_synthetic_data_scenario(self):
        """Use synthetic data from Generations II product brochure."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-GEN2-BOUNDARY-001\n"
            "TITLE: Apply maximum coverage Generations II policy\n"
            "TYPE: BOUNDARY\n"
            "PERSONA: High-net-worth customer\n"
            "TEST DATA: product_name=Generations II, coverage=99999999, premium=999999\n"
            "PASS CRITERIA: System accepts maximum coverage amount\n"
            "ESTIMATED TIME: 8\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["type"] == "BOUNDARY"

    @skip_if_no_module
    @pytest.mark.parametrize(
        "field_line,expected_key,expected_value",
        [
            ("ID: UAT-001", "id", "UAT-001"),
            ("TITLE: My Title", "title", "My Title"),
            ("TYPE: NEGATIVE", "type", "NEGATIVE"),
            ("PERSONA: Admin User", "persona", "Admin User"),
            ("PASS CRITERIA: Item saved", "pass_criteria", "Item saved"),
            ("ESTIMATED TIME: 20", "estimated_time", "20"),
        ],
    )
    def test_individual_field_parsing(self, field_line, expected_key, expected_value):
        raw = f"===SCENARIO===\nID: UAT-001\n{field_line}\n"
        result = parse_scenarios(raw)
        assert len(result) >= 1
        assert result[0].get(expected_key) == expected_value

    @skip_if_no_module
    def test_large_number_of_scenarios(self):
        """Boundary: 50 scenarios parsed without error."""
        blocks = "\n".join(
            f"===SCENARIO===\nID: UAT-BULK-{i:03d}\nTITLE: Scenario {i}\n"
            for i in range(50)
        )
        result = parse_scenarios(blocks)
        assert len(result) == 50

    @skip_if_no_module
    def test_colon_in_title_value(self):
        """Edge: colon character in a field value should not break parsing."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-COLON-001\n"
            "TITLE: Verify redirect to https://example.com\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["title"] == "Verify redirect to https://example.com"

    @skip_if_no_module
    def test_unicode_in_scenario(self):
        """Edge: Chinese/accented characters in field values."""
        raw = (
            "===SCENARIO===\n"
            "ID: UAT-CN-001\n"
            "TITLE: 验证大湾区指定医院直付安排\n"
            "PERSONA: 内地保单持有人\n"
        )
        result = parse_scenarios(raw)
        assert result[0]["title"] == "验证大湾区指定医院直付安排"
        assert result[0]["persona"] == "内地保单持有人"


# ===========================================================================
# Tests: build_test_pack_csv
# ===========================================================================


class TestBuildTestPackCsv:
    @skip_if_no_module
    def test_returns_string(self):
        result = build_test_pack_csv([])
        assert isinstance(result, str)

    @skip_if_no_module
    def test_header_row_present(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert header[0] == "Scenario ID"
        assert "Title" in header
        assert "Result (PASS/FAIL/BLOCKED)" in header
        assert "Defect Ref" in header

    @skip_if_no_module
    def test_header_has_ten_columns(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert len(header) == 10

    @skip_if_no_module
    def test_empty_scenarios_only_header(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(rows) == 1  # header only

    @skip_if_no_module
    def test_single_scenario_row(self):
        s = _make_scenario()
        result = build_test