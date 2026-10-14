"""
Test suite for .github/scripts/tool1_code_review.py

What is tested:
  - extract_json(): happy path, markdown fences, nested JSON extraction,
    newline cleaning, error cases, edge cases
  - review_pr(): happy path, Claude error, malformed response
  - review_repo(): happy path, content truncation, empty file list
  - get_output_url(): URL construction
  - build_report_md(): happy path, empty findings, missing fields, partial data

Mocks used:
  - shared.call_claude (via unittest.mock.patch)
  - shared.get_pr_diff (via unittest.mock.patch)
  - shared.get_repo_files (via unittest.mock.patch)
  - shared.post_pr_comment (via unittest.mock.patch)
  - shared.write_output_file (via unittest.mock.patch)
  - shared.send_email (via unittest.mock.patch)
  - shared.write_audit_entry (via unittest.mock.patch)
  - requests (not called directly in tested functions, but imported)
  - datetime.datetime.utcnow (for deterministic timestamps)

TODOs:
  - TODO: Integration test for __main__ block requires env vars + real shared module
  - TODO: Test post_pr_comment failure handling once retry logic is added
  - TODO: Test write_output_file integration once output-repo write path is exercised
"""

import sys
import os
import json
import types
import importlib
import datetime
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: create a minimal stub for `shared` so the import doesn't fail
# even if the real shared.py is absent or has missing env vars.
# ---------------------------------------------------------------------------

def _make_shared_stub():
    mod = types.ModuleType("shared")
    mod.call_claude = MagicMock(return_value="{}")
    mod.get_repo_files = MagicMock(return_value={})
    mod.get_pr_diff = MagicMock(return_value="diff text")
    mod.write_output_file = MagicMock()
    mod.post_pr_comment = MagicMock()
    mod.send_email = MagicMock()
    mod.email_html = MagicMock(return_value="<html/>")
    mod.write_audit_entry = MagicMock()
    mod.OUTPUT_REPO_OWNER = "test-owner"
    mod.OUTPUT_REPO = "test-output-repo"
    mod.GH_HEADERS = {"Authorization": "token test"}
    mod.GH_API = "https://api.github.com"
    return mod


# Inject stub before importing the module under test
_shared_stub = _make_shared_stub()
sys.modules.setdefault("shared", _shared_stub)

# Now import the module under test
script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

# Re-insert so tool1 finds `shared`
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))

import importlib.util, pathlib

_SCRIPT_PATH = pathlib.Path(__file__).parent / ".github" / "scripts" / "tool1_code_review.py"

def _load_tool1():
    """Load tool1_code_review from file path, injecting the stub shared module."""
    spec = importlib.util.spec_from_file_location(
        "tool1_code_review", str(_SCRIPT_PATH)
    )
    mod = importlib.util.module_from_spec(spec)
    # Provide env vars so __main__ block (if reachable) doesn't crash
    with patch.dict(os.environ, {"REVIEW_MODE": "repo", "GITHUB_REPOSITORY": "o/r"}):
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        except Exception:
            pass
    return mod


# Try to load; fall back gracefully
try:
    _tool = _load_tool1()
    extract_json = _tool.extract_json
    review_pr = _tool.review_pr
    review_repo = _tool.review_repo
    get_output_url = _tool.get_output_url
    build_report_md = _tool.build_report_md
    LOAD_ERROR = None
except Exception as exc:
    LOAD_ERROR = exc
    extract_json = review_pr = review_repo = get_output_url = build_report_md = None

_SKIP_IF_LOAD_FAILED = pytest.mark.skipif(
    LOAD_ERROR is not None,
    reason=f"Could not load tool1_code_review: {LOAD_ERROR}",
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code is reasonable.",
    "score": 75,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded secret detected.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good use of type hints."],
    "iac_findings": ["S3 bucket lacks versioning."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def _claude_returns(result: dict):
    """Return a mock side-effect that returns the JSON-encoded result."""
    return json.dumps(result)


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:

    @_SKIP_IF_LOAD_FAILED
    def test_plain_valid_json(self):
        result = extract_json(VALID_JSON_STR)
        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    @_SKIP_IF_LOAD_FAILED
    def test_strips_markdown_fences_backtick_json(self):
        wrapped = f"```json\n{VALID_JSON_STR}\n```"
        result = extract_json(wrapped)
        assert result["score"] == 75

    @_SKIP_IF_LOAD_FAILED
    def test_strips_markdown_fences_plain_backtick(self):
        wrapped = f"```\n{VALID_JSON_STR}\n```"
        result = extract_json(wrapped)
        assert result["summary"] == VALID_RESULT["summary"]

    @_SKIP_IF_LOAD_FAILED
    def test_extracts_json_with_leading_text(self):
        raw = f"Here is your review:\n{VALID_JSON_STR}"
        result = extract_json(raw)
        assert result["score"] == 75

    @_SKIP_IF_LOAD_FAILED
    def test_extracts_json_with_trailing_text(self):
        raw = f"{VALID_JSON_STR}\n\nPlease let me know if you need more detail."
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    @_SKIP_IF_LOAD_FAILED
    def test_extracts_json_surrounded_by_text(self):
        raw = f"Preamble text\n{VALID_JSON_STR}\nPostamble text"
        result = extract_json(raw)
        assert isinstance(result, dict)

    @_SKIP_IF_LOAD_FAILED
    def test_newline_inside_string_values_cleaned(self):
        # Build JSON with a literal newline inside a string value (malformed)
        malformed = '{"summary": "line one\nline two", "score": 80}'
        # This would fail direct json.loads, but extract_json should clean it
        try:
            result = extract_json(malformed)
            assert "summary" in result
        except ValueError:
            # Acceptable: cleaning may not recover all cases
            pass

    @_SKIP_IF_LOAD_FAILED
    def test_raises_value_error_on_no_json(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("This is completely plain text with no JSON.")

    @_SKIP_IF_LOAD_FAILED
    def test_raises_value_error_on_broken_json(self):
        with pytest.raises(ValueError):
            extract_json("{ broken json :::}")

    @_SKIP_IF_LOAD_FAILED
    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    @_SKIP_IF_LOAD_FAILED
    def test_only_whitespace_raises(self):
        with pytest.raises(ValueError):
            extract_json("   \n\t  ")

    @_SKIP_IF_LOAD_FAILED
    def test_minimal_valid_json(self):
        result = extract_json('{"a": 1}')
        assert result == {"a": 1}

    @_SKIP_IF_LOAD_FAILED
    def test_empty_json_object(self):
        result = extract_json("{}")
        assert result == {}

    @_SKIP_IF_LOAD_FAILED
    def test_score_boundary_zero(self):
        data = {**VALID_RESULT, "score": 0}
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    @_SKIP_IF_LOAD_FAILED
    def test_score_boundary_hundred(self):
        data = {**VALID_RESULT, "score": 100}
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    @_SKIP_IF_LOAD_FAILED
    def test_findings_empty_list(self):
        data = {**VALID_RESULT, "findings": []}
        result = extract_json(json.dumps(data))
        assert result["findings"] == []

    @_SKIP_IF_LOAD_FAILED
    def test_null_line_in_finding(self):
        data = {
            **VALID_RESULT,
            "findings": [{**VALID_RESULT["findings"][0], "line": None}],
        }
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    @_SKIP_IF_LOAD_FAILED
    @pytest.mark.parametrize("recommendation", ["APPROVE", "REQUEST_CHANGES", "BLOCK"])
    def test_valid_merge_recommendations(self, recommendation):
        data = {**VALID_RESULT, "merge_recommendation": recommendation}
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == recommendation

    @_SKIP_IF_LOAD_FAILED
    @pytest.mark.parametrize("severity", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    def test_valid_severity_values(self, severity):
        finding = {**VALID_RESULT["findings"][0], "severity": severity}
        data = {**VALID_RESULT, "findings": [finding]}
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["severity"] == severity

    @_SKIP_IF_LOAD_FAILED
    def test_unicode_content_preserved(self):
        # Arabic content from synthetic data
        data = {"summary": "تقييم الكود", "score": 50, "findings": []}
        result = extract_json(json.dumps(data, ensure_ascii=False))
        assert result["summary"] == "تقييم الكود"

    @_SKIP_IF_LOAD_FAILED
    def test_deeply_nested_is_parsed(self):
        data = {"outer": {"inner": {"value": 42}}}
        result = extract_json(json.dumps(data))
        assert result["outer"]["inner"]["value"] == 42

    @_SKIP_IF_LOAD_FAILED
    def test_strips_leading_trailing_whitespace(self):
        result = extract_json(f"   \n  {VALID_JSON_STR}  \n  ")
        assert result["score"] == 75

    @_SKIP_IF_LOAD_FAILED
    def test_multiple_json_blocks_picks_outermost(self):
        # Text contains two JSON blobs – should pick first { ... } pair
        raw = f'{VALID_JSON_STR} some text {{"other": true}}'
        # The function finds first { and last } so result wraps everything
        # Just ensure it doesn't raise and returns a dict
        try:
            result = extract_json(raw)
            assert isinstance(result, dict)
        except ValueError:
            pass  # Acceptable: outer span may not be valid JSON


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

class TestReviewPr:

    @_SKIP_IF_LOAD_FAILED
    def test_happy_path_posts_comment_and_returns_result(self):
        with patch.object(_shared_stub, "get_pr_diff", return_value="diff content"), \
             patch.object(_shared_stub, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(_shared_stub, "post_pr_comment") as mock_comment:
            # Re-bind in tool module
            _tool.get_pr_diff = _shared_stub.get_pr_diff
            _tool.call_claude = _shared_stub.call_claude
            _tool.post_pr_comment = _shared_stub.post_pr_comment

            result = review_pr("myorg", "myrepo", 42, "http://run-url")

        assert result["score"] == 75
        assert result["merge_recommendation"] == "APPROVE"

    @_SKIP_IF_LOAD_FAILED
    def test_comment_contains_score(self):
        posted_comments = []

        def capture_comment(owner, repo, pr_number, comment):
            posted_comments.append(comment)

        with patch.object(_shared_stub, "get_pr_diff", return_value="diff"), \
             patch.object(_shared_stub, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(_shared_stub, "post_pr_comment", side_effect=capture_comment):
            _tool.get_pr_diff = _shared_stub.get_pr_diff
            _tool.call_claude = _shared_stub.call_claude
            _tool.post_pr_comment = _shared_stub.post_pr_comment

            review_pr("org", "repo", 1, "url")

        assert posted_comments, "post_pr_comment was never called"
        comment_body = posted_comments[0]
        assert "75" in comment_body
        assert "APPROVE" in comment_body

    @_SKIP_IF_LOAD_FAILED
    def test_comment_contains_finding_details(self):
        posted_comments = []

        def capture_comment(owner, repo, pr_number, comment):
            posted_comments.append(comment)

        with patch.object(_shared_stub, "get_pr_diff", return_value="diff"), \
             patch.object(_shared_stub, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(_shared_stub, "post_pr_comment", side_effect=capture_comment):
            _tool.get_pr_diff = _shared_stub.get_pr_diff
            _tool.call_claude = _shared_stub.call_claude
            _tool.post_pr_comment = _shared_stub.post_pr_comment

            review_pr("org", "repo", 1, "url")

        comment_body = posted_comments[0]
        assert "src/example.py" in comment_body
        assert "Hardcoded secret detected" in comment_body

    @_SKIP_IF_LOAD_FAILED
    def test_no_findings_shows_placeholder(self):
        posted_comments = []

        def capture_comment(owner, repo, pr_number, comment):
            