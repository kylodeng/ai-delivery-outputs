"""
Test module for tool1_code_review.py

What is tested:
- extract_json: happy path, markdown fences, embedded newlines, missing JSON, invalid JSON
- review_pr: happy path, comment construction, result returned
- review_repo: happy path, content truncation
- get_output_url: URL construction
- build_report_md: full report structure, empty findings, empty iac/positive observations

Mocks used:
- shared.call_claude (patched via tool1_code_review.call_claude)
- shared.get_pr_diff (patched via tool1_code_review.get_pr_diff)
- shared.get_repo_files (patched via tool1_code_review.get_repo_files)
- shared.post_pr_comment (patched via tool1_code_review.post_pr_comment)
- shared.write_output_file (patched via tool1_code_review.write_output_file)
- shared.send_email (patched via tool1_code_review.send_email)
- shared.write_audit_entry (patched via tool1_code_review.write_audit_entry)
- requests (not directly called in tested functions but imported)

TODOs:
- TODO: Integration tests for __main__ block require real env vars and GitHub tokens
- TODO: test_review_pr_post_comment_failure needs post_pr_comment to raise to verify error propagation
- TODO: test_review_repo_token_budget needs actual token counting to verify 20000-char limit behaviour
"""

import json
import re
import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without the real `shared` module
# ---------------------------------------------------------------------------
shared_stub = MagicMock()
shared_stub.OUTPUT_REPO_OWNER = "test-owner"
shared_stub.OUTPUT_REPO = "test-output-repo"
shared_stub.GH_HEADERS = {"Authorization": "Bearer fake-token"}
shared_stub.GH_API = "https://api.github.com"

sys.modules.setdefault("shared", shared_stub)
sys.modules.setdefault("requests", MagicMock())

# Now import the module under test
import importlib
import types

# Re-insert path so relative import inside the script works
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))

# We import by file path to avoid ambiguity
import importlib.util

_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", ".github", "scripts", "tool1_code_review.py"
)

# If the script is not present in the test environment we skip all module-level work
_SCRIPT_AVAILABLE = os.path.exists(_SCRIPT_PATH)

if _SCRIPT_AVAILABLE:
    spec = importlib.util.spec_from_file_location("tool1_code_review", _SCRIPT_PATH)
    tool1 = importlib.util.module_from_spec(spec)
    # Inject the stub before execution
    tool1.shared = shared_stub  # type: ignore[attr-defined]
    # Patch sys.modules so that `from shared import ...` resolves
    sys.modules["tool1_code_review"] = tool1
    try:
        spec.loader.exec_module(tool1)
    except Exception:
        # The __main__ block may fail; that is fine
        pass
    extract_json = tool1.extract_json
    review_pr = tool1.review_pr
    review_repo = tool1.review_repo
    get_output_url = tool1.get_output_url
    build_report_md = tool1.build_report_md
else:
    pytestmark = pytest.mark.skip(reason="tool1_code_review.py not found at expected path")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

VALID_RESULT = {
    "summary": "Overall the code is well structured.",
    "score": 82,
    "merge_recommendation": "APPROVE",
    "findings": [
        {
            "severity": "HIGH",
            "category": "security",
            "file": "src/example.py",
            "line": 42,
            "issue": "Hardcoded password detected.",
            "recommendation": "Use environment variables instead.",
        }
    ],
    "positive_observations": ["Good test coverage.", "Consistent naming conventions."],
    "iac_findings": ["S3 bucket lacks server-side encryption."],
}

VALID_JSON_STR = json.dumps(VALID_RESULT)


def _wrap_fences(text: str) -> str:
    return f"```json\n{text}\n```"


# ---------------------------------------------------------------------------
# extract_json tests
# ---------------------------------------------------------------------------

class TestExtractJson:

    def test_plain_json_string(self):
        result = extract_json(VALID_JSON_STR)
        assert result["score"] == 82
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_leading_whitespace(self):
        result = extract_json("   \n" + VALID_JSON_STR + "\n   ")
        assert result["summary"] == "Overall the code is well structured."

    def test_json_wrapped_in_markdown_fences(self):
        raw = _wrap_fences(VALID_JSON_STR)
        result = extract_json(raw)
        assert result["score"] == 82

    def test_json_wrapped_in_plain_fences_no_language_tag(self):
        raw = f"```\n{VALID_JSON_STR}\n```"
        result = extract_json(raw)
        assert result["findings"][0]["severity"] == "HIGH"

    def test_json_preceded_by_preamble_text(self):
        raw = "Here is the review:\n" + VALID_JSON_STR
        result = extract_json(raw)
        assert result["score"] == 82

    def test_json_followed_by_trailing_text(self):
        raw = VALID_JSON_STR + "\n\nLet me know if you need more details."
        result = extract_json(raw)
        assert result["merge_recommendation"] == "APPROVE"

    def test_json_with_preamble_and_trailing_text(self):
        raw = "Sure thing!\n" + VALID_JSON_STR + "\nDone."
        result = extract_json(raw)
        assert result["score"] == 82

    def test_embedded_literal_newline_in_string_value(self):
        # Simulate a literal \n inside a JSON string value (common LLM mistake)
        broken = '{"summary": "line one\nline two", "score": 50, "merge_recommendation": "APPROVE", "findings": [], "positive_observations": [], "iac_findings": []}'
        # This may or may not parse; the function should either parse it or raise ValueError
        try:
            result = extract_json(broken)
            assert isinstance(result, dict)
        except ValueError:
            pass  # acceptable – the function documents it may raise

    def test_completely_empty_string_raises(self):
        with pytest.raises(ValueError):
            extract_json("")

    def test_no_json_object_raises(self):
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json("There is no JSON here at all.")

    def test_malformed_json_raises(self):
        with pytest.raises(ValueError):
            extract_json('{"key": "value", broken}')

    def test_nested_braces_parsed_correctly(self):
        data = {
            "summary": "ok",
            "score": 90,
            "merge_recommendation": "APPROVE",
            "findings": [],
            "positive_observations": [],
            "iac_findings": [],
        }
        result = extract_json(json.dumps(data))
        assert result["score"] == 90

    def test_score_zero_boundary(self):
        data = dict(VALID_RESULT, score=0)
        result = extract_json(json.dumps(data))
        assert result["score"] == 0

    def test_score_hundred_boundary(self):
        data = dict(VALID_RESULT, score=100)
        result = extract_json(json.dumps(data))
        assert result["score"] == 100

    def test_empty_findings_list(self):
        data = dict(VALID_RESULT, findings=[])
        result = extract_json(json.dumps(data))
        assert result["findings"] == []

    def test_findings_with_null_line(self):
        finding = dict(VALID_RESULT["findings"][0], line=None)
        data = dict(VALID_RESULT, findings=[finding])
        result = extract_json(json.dumps(data))
        assert result["findings"][0]["line"] is None

    def test_multiple_findings(self):
        findings = [
            {"severity": "CRITICAL", "category": "security", "file": "a.py",
             "line": 1, "issue": "Secret exposed.", "recommendation": "Remove it."},
            {"severity": "LOW", "category": "maintainability", "file": "b.py",
             "line": None, "issue": "Missing docstring.", "recommendation": "Add docstring."},
        ]
        data = dict(VALID_RESULT, findings=findings)
        result = extract_json(json.dumps(data))
        assert len(result["findings"]) == 2
        assert result["findings"][0]["severity"] == "CRITICAL"

    def test_json_inside_fences_with_surrounding_text(self):
        raw = "Analysis complete.\n```json\n" + VALID_JSON_STR + "\n```\nThank you."
        result = extract_json(raw)
        assert result["score"] == 82

    def test_returns_dict(self):
        result = extract_json(VALID_JSON_STR)
        assert isinstance(result, dict)

    def test_block_recommendation(self):
        data = dict(VALID_RESULT, merge_recommendation="BLOCK")
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "BLOCK"

    def test_request_changes_recommendation(self):
        data = dict(VALID_RESULT, merge_recommendation="REQUEST_CHANGES")
        result = extract_json(json.dumps(data))
        assert result["merge_recommendation"] == "REQUEST_CHANGES"


# ---------------------------------------------------------------------------
# review_pr tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _SCRIPT_AVAILABLE, reason="Script not found")
class TestReviewPr:

    def _patch_all(self, diff_return="diff content", claude_return=None):
        if claude_return is None:
            claude_return = VALID_JSON_STR
        patches = {
            "get_pr_diff": patch.object(tool1, "get_pr_diff", return_value=diff_return),
            "call_claude": patch.object(tool1, "call_claude", return_value=claude_return),
            "post_pr_comment": patch.object(tool1, "post_pr_comment", return_value=None),
        }
        return patches

    def test_happy_path_returns_result(self):
        with patch.object(tool1, "get_pr_diff", return_value="diff text"), \
             patch.object(tool1, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(tool1, "post_pr_comment", return_value=None) as mock_comment:
            result = review_pr("acme", "backend", 7, "https://ci.example.com/run/1")
            assert result["score"] == 82
            assert result["merge_recommendation"] == "APPROVE"
            mock_comment.assert_called_once()

    def test_pr_comment_contains_score(self):
        with patch.object(tool1, "get_pr_diff", return_value="diff text"), \
             patch.object(tool1, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(tool1, "post_pr_comment", return_value=None) as mock_comment:
            review_pr("acme", "backend", 7, "https://ci.example.com/run/1")
            posted_comment = mock_comment.call_args[0][3]
            assert "82" in posted_comment

    def test_pr_comment_contains_recommendation(self):
        with patch.object(tool1, "get_pr_diff", return_value="diff text"), \
             patch.object(tool1, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(tool1, "post_pr_comment", return_value=None) as mock_comment:
            review_pr("acme", "backend", 7, "https://ci.example.com/run/1")
            posted_comment = mock_comment.call_args[0][3]
            assert "APPROVE" in posted_comment

    def test_pr_comment_contains_summary(self):
        with patch.object(tool1, "get_pr_diff", return_value="diff text"), \
             patch.object(tool1, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(tool1, "post_pr_comment", return_value=None) as mock_comment:
            review_pr("acme", "backend", 7, "https://ci.example.com/run/1")
            posted_comment = mock_comment.call_args[0][3]
            assert "Overall the code is well structured." in posted_comment

    def test_pr_comment_no_findings_shows_placeholder(self):
        data = dict(VALID_RESULT, findings=[])
        with patch.object(tool1, "get_pr_diff", return_value="diff"), \
             patch.object(tool1, "call_claude", return_value=json.dumps(data)), \
             patch.object(tool1, "post_pr_comment", return_value=None) as mock_comment:
            review_pr("acme", "backend", 1, "")
            posted_comment = mock_comment.call_args[0][3]
            assert "_No findings_" in posted_comment

    def test_pr_comment_no_positive_obs_shows_placeholder(self):
        data = dict(VALID_RESULT, positive_observations=[])
        with patch.object(tool1, "get_pr_diff", return_value="diff"), \
             patch.object(tool1, "call_claude", return_value=json.dumps(data)), \
             patch.object(tool1, "post_pr_comment", return_value=None) as mock_comment:
            review_pr("acme", "backend", 1, "")
            posted_comment = mock_comment.call_args[0][3]
            assert "_None_" in posted_comment

    def test_pr_comment_finding_includes_file_and_line(self):
        with patch.object(tool1, "get_pr_diff", return_value="diff"), \
             patch.object(tool1, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(tool1, "post_pr_comment", return_value=None) as mock_comment:
            review_pr("acme", "backend", 1, "")
            posted_comment = mock_comment.call_args[0][3]
            assert "src/example.py" in posted_comment
            assert "42" in posted_comment

    def test_get_pr_diff_called_with_correct_args(self):
        with patch.object(tool1, "get_pr_diff", return_value="diff") as mock_diff, \
             patch.object(tool1, "call_claude", return_value=VALID_JSON_STR), \
             patch.object(tool1, "post_pr_comment", return_value=None):
            review_pr("myorg", "myrepo", 99, "url")
            mock_diff.assert_called_once_with("myorg", "myrepo", 99)

    def test_call_claude_called_with_diff_in_prompt(self):
        with patch.object(tool1, "get_pr_diff", return_value="UNIQUE_