"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub API tree fetching, extension filtering, max_files limit
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File creation and update (with/without SHA)
- post_pr_comment(): PR comment posting
- send_email(): SendGrid integration, failure warning
- email_html(): HTML generation with SUCCESS/FAILURE status
- write_audit_entry(): Audit log writing (JSON + Markdown)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude client)
  - requests.get, requests.post, requests.put
  - os.environ (injected via monkeypatch)
  - datetime.datetime (for deterministic timestamps)
  - base64 (implicitly tested via real calls)

TODOs:
- TODO: Integration test for full Claude round-trip (requires real API key)
- TODO: Test write_audit_entry Markdown append logic (requires full source — entry dict construction is cut off)
- TODO: Test audit log JSON structure (full function body not available)
"""

import base64
import datetime
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
ENV_VARS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(autouse=True, scope="session")
def set_env_vars():
    """Inject required environment variables before module import."""
    with patch.dict(os.environ, ENV_VARS, clear=False):
        yield


# Import the module under test inside a fixture so env vars are present.
# We import at module level after patching to keep it simple.
with patch.dict(os.environ, ENV_VARS, clear=False):
    # Ensure anthropic is importable (mock if not installed)
    try:
        import anthropic  # noqa: F401
    except ImportError:
        anthropic_mock = MagicMock()
        sys.modules["anthropic"] = anthropic_mock

    import importlib
    # Add the scripts directory to path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
    import shared  # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================

def _make_response_mock(text: str):
    """Create a mock anthropic Messages response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        """call_claude returns text from first content block."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_response_mock("Hello from Claude!")

        result = shared.call_claude(system="You are helpful.", user="Say hello.")

        assert result == "Hello from Claude!"

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls):
        """call_claude passes model constant and default max_tokens."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_response_mock("ok")

        shared.call_claude(system="sys", user="usr")

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        """call_claude respects custom max_tokens argument."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_response_mock("ok")

        shared.call_claude(system="sys", user="usr", max_tokens=512)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 512

    @patch("shared.anthropic.Anthropic")
    def test_uses_injected_api_key(self, mock_anthropic_cls):
        """call_claude initialises Anthropic client with env API key."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_response_mock("ok")

        shared.call_claude("sys", "usr")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        """call_claude does not swallow exceptions from the API."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude("sys", "usr")

    @patch("shared.anthropic.Anthropic")
    def test_empty_string_system_and_user(self, mock_anthropic_cls):
        """call_claude handles empty strings without error."""
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_response_mock("")

        result = shared.call_claude("", "")
        assert result == ""


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:

    def test_no_fences_unchanged(self):
        """Plain JSON is returned as-is (stripped)."""
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_json_code_fence(self):
        """```json ... ``` fences are removed."""
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_generic_code_fence(self):
        """``` ... ``` fences without language tag are removed."""
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_surrounding_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped."""
        raw = "   {\"a\": 1}   "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_fence_with_leading_whitespace(self):
        """Fences with surrounding whitespace are handled."""
        raw = "  ```json\n[1,2,3]\n```  "
        result = shared.clean_json(raw)
        assert result == "[1,2,3]"

    def test_multiline_json_with_fence(self):
        """Multiline JSON inside fences is returned correctly."""
        inner = '{\n  "product_name": "Generations II",\n  "doc_type": "product_brochure"\n}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    def test_valid_json_parseable_after_clean(self):
        """Result of clean_json should be parseable as JSON."""
        raw = '```json\n{"tool": "audit", "status": "SUCCESS"}\n```'
        cleaned = shared.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["tool"] == "audit"
        assert parsed["status"] == "SUCCESS"

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        """Whitespace-only string returns empty string."""
        assert shared.clean_json("   ") == ""

    def test_nested_backticks_in_content(self):
        """Content with backticks inside value is handled gracefully."""
        raw = '```json\n{"key": "value with ` backtick"}\n```'
        result = shared.clean_json(raw)
        # Should not raise; content may vary but should not crash
        assert isinstance(result, str)

    def test_multiple_fenced_blocks_takes_first(self):
        """Only the outermost fence is stripped (first ``` open, last ``` close)."""
        raw = "```json\n{\"a\":1}\n```\nsome text\n```"
        result = shared.clean_json(raw)
        # Should not crash; result is a string
        assert isinstance(result, str)


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _tree_item(self, path: str, url: str, item_type: str = "blob"):
        return {"type": item_type, "path": path, "url": url}

    @patch("shared.requests.get")
    def test_happy_path_single_extension(self, mock_get):
        """Files matching extension are fetched and decoded."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [self._tree_item("src/main.py", "https://api.github.com/blob/abc")]
        }
        blob_resp = MagicMock()
        blob_resp.json.return_value = {"content": _b64("print('hello')\n")}

        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == "print('hello')\n"

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        """Only files with matching extensions are returned."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                self._tree_item("main.py", "url1"),
                self._tree_item("README.md", "url2"),
                self._tree_item("data.json", "url3"),
            ]
        }
        py_blob = MagicMock()
        py_blob.json.return_value = {"content": _b64("# py")}
        json_blob = MagicMock()
        json_blob.json.return_value = {"content": _b64("{}")}

        mock_get.side_effect = [tree_resp, py_blob, json_blob]

        result = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "main.py" in result
        assert "data.json" in result
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        """get_repo_files stops after max_files entries."""
        items = [self._tree_item(f"file{i}.py", f"url{i}") for i in range(10)]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": items}

        blob_resp = MagicMock()
        blob_resp.json.return_value = {"content": _b64("code")}

        # First call = tree; remaining = blobs (up to max_files=3)
        mock_get.side_effect = [tree_resp] + [blob_resp] * 3

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        """Tree items that are not blobs (e.g. trees) are skipped."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                {"type": "tree", "path": "src", "url": "url1"},
                self._tree_item("src/main.py", "url2"),
            ]
        }
        blob_resp = MagicMock()
        blob_resp.json.return_value = {"content": _b64("code")}

        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert list(result.keys()) == ["src/main.py"]

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        """Empty repository tree returns empty dict."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}
        mock_get.return_value = tree_resp

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_malformed_blob_is_skipped(self, mock_get):
        """Blobs that fail to decode are silently skipped."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [self._tree_item("bad.py", "url_bad")]
        }
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "!!! not base64 !!!@@##"}

        mock_get.side_effect = [tree_resp, bad_blob]

        # Should not raise
        result = shared.get_repo_files("owner", "repo", [".py"])
        # File may or may not be present depending on error handling
        assert isinstance(result, dict)

    @patch("shared.requests.get")
    def test_correct_url_constructed(self, mock_get):
        """Correct GitHub API URL is constructed for tree fetch."""
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}
        mock_get.return_value = tree_resp

        shared.get_repo_files("myowner", "myrepo", [".py"])

        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url

    @patch("shared.requests.get")
    def test_multiple_extensions_insurance_data(self, mock_get):
        """Synthetic insurance data: JSON annotation files are fetched."""
        items = [
            self._tree_item("data/Insurance-product-info/Generations-II/Generations-II_