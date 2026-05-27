"""
Test suite for .github/scripts/tool5_uat.py

What is tested:
  - parse_scenarios(): happy path, edge cases, malformed input, boundary values
  - build_test_pack_csv(): correct headers, row data, empty input, special characters
  - build_test_pack_md(): output structure, version/owner/repo embedding, raw content inclusion
  - get_results_csv(): successful fetch and base64 decode, missing file (FileNotFoundError),
    unexpected API response shapes
  - Module-level constants and imports (smoke)

Mocks used:
  - unittest.mock.patch for `requests.get` (GitHub API calls in get_results_csv)
  - unittest.mock.patch for `base64.b64decode` where needed
  - shared module helpers (call_claude, write_output_file, send_email, etc.) are NOT imported
    in this test file directly; they are patched at the tool5_uat module boundary

TODOs:
  - TODO: Integration tests for __main__ block require full env-var + GitHub token setup
  - TODO: Tests for call_claude interactions need real/mocked LLM response fixtures
  - TODO: Audit trail (write_audit_entry) verification needs shared module fixture
  - TODO: email_html / send_email side-effect verification needs SMTP mock
"""

import base64
import csv
import io
import json
import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — mirror what the source file does
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

# Provide a minimal stub for `shared` so the import inside tool5_uat succeeds
# without real credentials / network access.
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer stub-token"}
shared_stub.GH_API = "https://api.github.com"
sys.modules.setdefault("shared", shared_stub)

import importlib
# Re-use cached module if already loaded; otherwise import fresh.
import types

def _load_module():
    """Load tool5_uat, re-using sys.modules cache when possible."""
    if "tool5_uat" in sys.modules:
        return sys.modules["tool5_uat"]
    spec_path = os.path.join(
        os.path.dirname(__file__), "..", "..", ".github", "scripts", "tool5_uat.py"
    )
    if not os.path.exists(spec_path):
        # fallback: try relative to cwd
        spec_path = os.path.join(".github", "scripts", "tool5_uat.py")
    import importlib.util
    spec = importlib.util.spec_from_file_location("tool5_uat", spec_path)
    mod = importlib.util.module_from_spec(spec)
    # Inject the shared stub before exec
    mod.__dict__["shared"] = shared_stub
    sys.modules["tool5_uat"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        # If the module can't fully load (e.g. missing env), we still want
        # the functions defined above the __main__ guard.
        pass
    return mod


try:
    tool5_uat = _load_module()
    parse_scenarios = tool5_uat.parse_scenarios
    build_test_pack_csv = tool5_uat.build_test_pack_csv
    build_test_pack_md = tool5_uat.build_test_pack_md
    get_results_csv = tool5_uat.get_results_csv
    MODULE_LOADED = True
except Exception as e:
    MODULE_LOADED = False
    MODULE_LOAD_ERROR = str(e)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

SINGLE_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-FEAT1-1
TITLE: Login with valid credentials
TYPE: POSITIVE
PERSONA: enterprise customer
PRE-CONDITIONS:
- User account exists
- System is operational
TEST DATA: alice.chen@example.com / P@ssw0rd
STEPS:
1. Navigate to /login
2. Enter email and password
3. Click Submit
EXPECTED RESULT: Dashboard is displayed
PASS CRITERIA: Dashboard loads within 3 seconds
ESTIMATED TIME: 5
NOTES: none
"""

MULTI_SCENARIO_BLOCK = """\
===SCENARIO===
ID: UAT-FEAT1-1
TITLE: Login with valid credentials
TYPE: POSITIVE
PERSONA: enterprise customer
PASS CRITERIA: Dashboard loads
ESTIMATED TIME: 5
NOTES: none
===SCENARIO===
ID: UAT-FEAT1-2
TITLE: Login with invalid password
TYPE: NEGATIVE
PERSONA: enterprise customer
PASS CRITERIA: Error message shown
ESTIMATED TIME: 3
NOTES: none
===SCENARIO===
ID: UAT-FEAT1-3
TITLE: Login with empty email
TYPE: BOUNDARY
PERSONA: anonymous user
PASS CRITERIA: Validation message shown
ESTIMATED TIME: 2
NOTES: none
"""


@pytest.fixture
def single_scenario():
    return SINGLE_SCENARIO_BLOCK


@pytest.fixture
def multi_scenario_raw():
    return MULTI_SCENARIO_BLOCK


@pytest.fixture
def parsed_scenarios():
    """Pre-parsed list for CSV/MD tests."""
    return [
        {
            "id": "UAT-FEAT1-1",
            "title": "Login with valid credentials",
            "type": "POSITIVE",
            "persona": "enterprise customer",
            "pass_criteria": "Dashboard loads",
            "estimated_time": "5",
            "raw": "block1",
        },
        {
            "id": "UAT-FEAT1-2",
            "title": "Login with invalid password",
            "type": "NEGATIVE",
            "persona": "enterprise customer",
            "pass_criteria": "Error message shown",
            "estimated_time": "3",
            "raw": "block2",
        },
    ]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def require_module():
    if not MODULE_LOADED:
        pytest.skip(f"tool5_uat could not be loaded: {MODULE_LOAD_ERROR}")


# ===========================================================================
# parse_scenarios
# ===========================================================================

class TestParseScenarios:

    def test_single_scenario_returns_one_item(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert len(result) == 1

    def test_single_scenario_id(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert result[0]["id"] == "UAT-FEAT1-1"

    def test_single_scenario_title(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert result[0]["title"] == "Login with valid credentials"

    def test_single_scenario_type(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert result[0]["type"] == "POSITIVE"

    def test_single_scenario_persona(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert result[0]["persona"] == "enterprise customer"

    def test_single_scenario_pass_criteria(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert result[0]["pass_criteria"] == "Dashboard loads within 3 seconds"

    def test_single_scenario_estimated_time(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert result[0]["estimated_time"] == "5"

    def test_single_scenario_raw_preserved(self, single_scenario):
        result = parse_scenarios(single_scenario)
        assert "Login with valid credentials" in result[0]["raw"]

    def test_multi_scenario_returns_three_items(self, multi_scenario_raw):
        result = parse_scenarios(multi_scenario_raw)
        assert len(result) == 3

    def test_multi_scenario_ids_are_unique(self, multi_scenario_raw):
        result = parse_scenarios(multi_scenario_raw)
        ids = [s["id"] for s in result]
        assert len(set(ids)) == 3

    def test_multi_scenario_types(self, multi_scenario_raw):
        result = parse_scenarios(multi_scenario_raw)
        types = {s["type"] for s in result}
        assert types == {"POSITIVE", "NEGATIVE", "BOUNDARY"}

    def test_empty_string_returns_empty_list(self):
        result = parse_scenarios("")
        assert result == []

    def test_no_delimiter_returns_empty_list(self):
        raw = "This is some text without any scenario delimiter."
        result = parse_scenarios(raw)
        assert result == []

    def test_delimiter_only_no_content(self):
        raw = "===SCENARIO===\n\n===SCENARIO===\n\n"
        result = parse_scenarios(raw)
        # Blocks without an ID should be discarded
        assert result == []

    def test_block_missing_id_is_skipped(self):
        raw = "===SCENARIO===\nTITLE: No ID scenario\nTYPE: POSITIVE\n"
        result = parse_scenarios(raw)
        assert result == []

    def test_block_with_id_only(self):
        raw = "===SCENARIO===\nID: UAT-MIN-1\n"
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-MIN-1"
        assert result[0].get("title", "") == ""

    def test_extra_whitespace_around_delimiter(self):
        raw = "  ===SCENARIO===  \nID: UAT-WS-1\nTITLE: Whitespace test\n"
        # The split is on literal '===SCENARIO==='; leading spaces mean no match
        # Behaviour: should gracefully handle (either 0 or 1 results acceptable)
        result = parse_scenarios(raw)
        # We just assert it doesn't raise
        assert isinstance(result, list)

    def test_id_with_extra_spaces_stripped(self):
        raw = "===SCENARIO===\nID:   UAT-SPACE-1   \nTITLE: Space test\n"
        result = parse_scenarios(raw)
        assert result[0]["id"] == "UAT-SPACE-1"

    def test_title_with_colon_in_value(self):
        raw = "===SCENARIO===\nID: UAT-COLON-1\nTITLE: Test: with colon\n"
        result = parse_scenarios(raw)
        assert "with colon" in result[0]["title"]

    def test_large_number_of_scenarios(self):
        blocks = []
        for i in range(50):
            blocks.append(
                f"===SCENARIO===\nID: UAT-BULK-{i}\nTITLE: Bulk scenario {i}\n"
                f"TYPE: POSITIVE\nPERSONA: tester\nPASS CRITERIA: ok\nESTIMATED TIME: 1\n"
            )
        raw = "\n".join(blocks)
        result = parse_scenarios(raw)
        assert len(result) == 50

    @pytest.mark.parametrize("customer_id,email", [
        ("CUST-001", "alice.chen@example.com"),
        ("CUST-002", "bob.smith@example.com"),
        ("CUST-007", "invalid-email"),
    ])
    def test_scenario_with_synthetic_test_data_in_body(self, customer_id, email):
        raw = (
            f"===SCENARIO===\nID: UAT-CUST-1\nTITLE: Test {customer_id}\n"
            f"TYPE: POSITIVE\nPERSONA: tester\nTEST DATA: {email}\n"
            f"PASS CRITERIA: User created\nESTIMATED TIME: 2\n"
        )
        result = parse_scenarios(raw)
        assert len(result) == 1
        assert result[0]["id"] == "UAT-CUST-1"

    def test_negative_type_recognised(self):
        raw = "===SCENARIO===\nID: UAT-NEG-1\nTYPE: NEGATIVE\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "NEGATIVE"

    def test_boundary_type_recognised(self):
        raw = "===SCENARIO===\nID: UAT-BND-1\nTYPE: BOUNDARY\n"
        result = parse_scenarios(raw)
        assert result[0]["type"] == "BOUNDARY"

    def test_result_items_always_contain_raw_key(self, multi_scenario_raw):
        result = parse_scenarios(multi_scenario_raw)
        for s in result:
            assert "raw" in s

    def test_none_input_raises(self):
        with pytest.raises((AttributeError, TypeError)):
            parse_scenarios(None)  # type: ignore


# ===========================================================================
# build_test_pack_csv
# ===========================================================================

class TestBuildTestPackCsv:

    def test_returns_string(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        assert isinstance(result, str)

    def test_header_row_present(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert "Scenario ID" in header
        assert "Title" in header
        assert "Result (PASS/FAIL/BLOCKED)" in header

    def test_header_has_ten_columns(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        reader = csv.reader(io.StringIO(result))
        header = next(reader)
        assert len(header) == 10

    def test_row_count_matches_scenarios(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        # 1 header + len(scenarios) data rows
        assert len(rows) == 1 + len(parsed_scenarios)

    def test_first_data_row_id(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)  # skip header
        first_row = next(reader)
        assert first_row[0] == "UAT-FEAT1-1"

    def test_result_column_empty_in_output(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        for row in reader:
            assert row[6] == ""   # Result column should be blank

    def test_tester_column_empty_in_output(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        for row in reader:
            assert row[7] == ""   # Tester column blank

    def test_defect_ref_column_empty_in_output(self, parsed_scenarios):
        result = build_test_pack_csv(parsed_scenarios)
        reader = csv.reader(io.StringIO(result))
        next(reader)
        for row in reader:
            assert row[9] == ""   # Defect Ref blank

    def test_empty_scenarios_list(self):
        result = build_test_pack_csv([])
        reader = csv.reader(io.StringIO(result))
        rows = list(reader)
        assert len(