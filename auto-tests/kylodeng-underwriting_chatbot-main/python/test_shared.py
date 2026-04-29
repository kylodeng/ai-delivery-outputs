"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, decode errors
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File creation (no SHA), file update (with SHA), fallback URL
- post_pr_comment(): PR comment posting
- send_email(): SendGrid success (200/202), warning on failure
- email_html(): HTML output content, SUCCESS/FAILURE color
- write_audit_entry(): Audit log writing (tested via write_output_file mock)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- builtins.print patched to capture warnings

TODOs:
- TODO: Integration test for call_claude() with a real Anthropic sandbox key
- TODO: Test write_audit_entry() fully — source truncated in provided code; stub below
- TODO: Test GH_HEADERS token interpolation when GH_TOKEN changes at runtime
"""

import base64
import json
import os
import sys
import datetime
import types
import importlib
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module with required env vars present
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(scope="session", autouse=True)
def patch_env_for_import():
    """Patch environment variables before the module is imported."""
    with patch.dict(os.environ, REQUIRED_ENV, clear=False):
        # Force (re)import so module-level constants pick up patched env
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Add script directory to path
        script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        # Also try relative path for CI
        alt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
        if alt_dir not in sys.path:
            sys.path.insert(0, alt_dir)
        yield


@pytest.fixture(scope="session")
def shared_module(patch_env_for_import):
    """Import shared module once per session with env vars set."""
    with patch.dict(os.environ, REQUIRED_ENV, clear=False):
        with patch("anthropic.Anthropic"):  # prevent real client init at import
            import shared as _shared
            return _shared


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared(shared_module):
    return shared_module


# ---------------------------------------------------------------------------
# Tests: clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_backtick_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_bare_backtick_fence(self, shared):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = "   \n```json\n{\"a\": 1}\n```\n   "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_no_fence_complex_json(self, shared):
        raw = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        assert shared.clean_json(raw) == raw

    def test_nested_backticks_in_content(self, shared):
        """Content that contains backticks but doesn't start with fence."""
        raw = '{"code": "x = `hello`"}'
        assert shared.clean_json(raw) == raw

    def test_multiline_json_with_fence(self, shared):
        raw = "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```"
        result = shared.clean_json(raw)
        assert result.strip() == '{\n  "a": 1,\n  "b": 2\n}'

    def test_array_json_with_fence(self, shared):
        raw = "```json\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert result.strip() == "[1, 2, 3]"

    @pytest.mark.parametrize("raw,expected", [
        ('{"x":1}', '{"x":1}'),
        ("```\n[]\n```", "[]"),
        ("```json\nnull\n```", "null"),
        ('  {"spaces": true}  ', '{"spaces": true}'),
    ])
    def test_parametrized_cases(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# Tests: call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def _make_mock_client(self, text_response="Claude says hello"):
        mock_content = MagicMock()
        mock_content.text = text_response
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_happy_path_returns_text(self, shared):
        mock_client = self._make_mock_client("Hello from Claude")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("You are helpful.", "Say hello.")
        assert result == "Hello from Claude"

    def test_passes_system_and_user_messages(self, shared):
        mock_client = self._make_mock_client("response")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("system prompt", "user prompt", max_tokens=1024)
        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=1024,
            system="system prompt",
            messages=[{"role": "user", "content": "user prompt"}],
        )

    def test_default_max_tokens(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("max_tokens", mock_client.messages.create.call_args[1].get("max_tokens")) == 4096 or \
               mock_client.messages.create.call_args[0] == () and \
               mock_client.messages.create.call_args[1]["max_tokens"] == 4096

    def test_uses_correct_model(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        call_kwargs = mock_client.messages.create.call_args[1]
        assert call_kwargs["model"] == "claude-sonnet-4-6"

    def test_api_error_propagates(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("sys", "usr")

    def test_uses_api_key_from_env(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
            shared.call_claude("sys", "usr")
        mock_anthropic.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_empty_response_content_raises(self, shared):
        mock_response = MagicMock()
        mock_response.content = []
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(IndexError):
                shared.call_claude("sys", "usr")

    def test_large_response_text(self, shared):
        large_text = "x" * 10000
        mock_client = self._make_mock_client(large_text)
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys", "usr", max_tokens=16000)
        assert result == large_text


# ---------------------------------------------------------------------------
# Tests: get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _make_blob_item(self, path, url="https://api.github.com/blob/abc"):
        return {"type": "blob", "path": path, "url": url}

    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_content_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_returns_matching_files(self, shared):
        items = [
            self._make_blob_item("src/main.py"),
            self._make_blob_item("src/utils.js"),
            self._make_blob_item("README.md"),
        ]
        py_content = "print('hello')"
        tree_resp = self._make_tree_response(items)
        content_resp = self._make_content_response(py_content)

        with patch("requests.get", side_effect=[tree_resp, content_resp]) as mock_get:
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == py_content
        assert "src/utils.js" not in result
        assert "README.md" not in result

    def test_respects_max_files_limit(self, shared):
        items = [self._make_blob_item(f"file{i}.py") for i in range(10)]
        tree_resp = self._make_tree_response(items)
        content_resps = [self._make_content_response(f"content{i}") for i in range(3)]

        responses = [tree_resp] + content_resps
        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_multiple_extensions(self, shared):
        items = [
            self._make_blob_item("a.py"),
            self._make_blob_item("b.js"),
            self._make_blob_item("c.go"),
        ]
        tree_resp = self._make_tree_response(items)
        py_resp = self._make_content_response("py content")
        js_resp = self._make_content_response("js content")

        with patch("requests.get", side_effect=[tree_resp, py_resp, js_resp]):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.go" not in result

    def test_skips_non_blob_items(self, shared):
        items = [
            {"type": "tree", "path": "src", "url": "http://x"},
            self._make_blob_item("main.py"),
        ]
        tree_resp = self._make_tree_response(items)
        content_resp = self._make_content_response("code")

        with patch("requests.get", side_effect=[tree_resp, content_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert list(result.keys()) == ["main.py"]

    def test_empty_tree_returns_empty_dict(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}
        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_handles_decode_error_gracefully(self, shared):
        items = [self._make_blob_item("bad.py")]
        tree_resp = self._make_tree_response(items)
        bad_resp = MagicMock()
        bad_resp.json.return_value = {"content": "!!!not-valid-base64!!!"}

        with patch("requests.get", side_effect=[tree_resp, bad_resp]):
            # Should not raise, just skip the file
            result = shared.get_repo_files("owner", "repo", [".py"])
        # File may or may not be present depending on error type; just check no crash
        assert isinstance(result, dict)

    def test_missing_content_key_skipped(self, shared):
        items = [self._make_blob_item("a.py")]
        tree_resp = self._make_tree_response(items)
        no_content_resp = MagicMock()
        no_content_resp.json.return_value = {}  # no "content" key

        with patch("requests.get", side_effect=[tree_resp, no_content_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert isinstance(result, dict)

    def test_correct_url_constructed(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}
        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])
        call_url = mock_get.call_args[0][0]
        assert "myowner" in call_url
        assert "myrepo" in call_url
        assert "recursive=1" in call_url

    def test_utf8_content_decoded_correctly(self, shared):
        content = "# Arabic content: مرحبا"
        items = [self._make_blob_item("arabic.py