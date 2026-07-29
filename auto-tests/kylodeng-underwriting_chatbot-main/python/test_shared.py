"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, return value extraction
- clean_json(): markdown fence stripping, edge cases, plain JSON passthrough
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding, error handling
- get_pr_diff(): PR diff fetching, truncation at 30000 chars
- write_output_file(): file create (no SHA), file update (with SHA), fallback URL
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure status codes
- email_html(): HTML output structure, status color logic
- write_audit_entry(): audit log entry construction (tested via write_output_file mock)

Mocks used:
- unittest.mock.patch for: requests.get, requests.post, requests.put, anthropic.Anthropic
- os.environ patched for all required environment variables
- base64 decoding behaviour exercised directly

TODOs:
- TODO: Full integration test for write_audit_entry requires the complete source (truncated in provided code)
- TODO: Test concurrent/thread-safety of module-level GH_HEADERS construction
- TODO: Test behaviour when ANTHROPIC_API_KEY / GH_TOKEN / SENDGRID_API_KEY are missing at import time
"""

import base64
import datetime
import importlib
import json
import os
import sys
import types
import unittest.mock as mock
from unittest.mock import MagicMock, Mock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "fallback-owner",
}


@pytest.fixture(autouse=True, scope="session")
def patch_env_and_import():
    """Patch environment variables and import shared module once per session."""
    with mock.patch.dict(os.environ, FAKE_ENV, clear=False):
        # Remove cached module if present so it re-imports with patched env
        sys.modules.pop("shared", None)
        # Add the script directory to path
        script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        # Also try relative path for different working directories
        alt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
        if alt_dir not in sys.path:
            sys.path.insert(0, alt_dir)
        yield


@pytest.fixture(autouse=True)
def ensure_env():
    """Ensure env vars are set for every test."""
    with mock.patch.dict(os.environ, FAKE_ENV, clear=False):
        yield


# ---------------------------------------------------------------------------
# Lazy import helper so individual tests can reload with different env
# ---------------------------------------------------------------------------

def import_shared():
    sys.modules.pop("shared", None)
    import shared as s
    return s


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def setup_method(self):
        self.shared = import_shared()

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert self.shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = self.shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = self.shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   \n  {\"key\": \"value\"}  \n   "
        result = self.shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_fence_with_whitespace(self):
        raw = "  ```json\n[1, 2, 3]\n```  "
        result = self.shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_multiline_json_in_fence(self):
        raw = "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```"
        result = self.shared.clean_json(raw)
        assert '{"a"' in result or '"a": 1' in result

    def test_empty_string(self):
        result = self.shared.clean_json("")
        assert result == ""

    def test_only_whitespace(self):
        result = self.shared.clean_json("   ")
        assert result == ""

    def test_json_with_nested_backticks_not_stripped(self):
        # Content that starts with { not ``` — should be returned as-is
        raw = '{"code": "print(`hello`)"}'
        result = self.shared.clean_json(raw)
        assert result == raw

    def test_fence_without_closing(self):
        # Only opening fence — rsplit on ``` returns original after first split
        raw = "```json\n{\"key\": \"value\"}"
        result = self.shared.clean_json(raw)
        # Should not raise; content after first line returned without trailing ```
        assert '{"key": "value"}' in result

    @pytest.mark.parametrize("raw,expected", [
        ('{"model_name": "Underwriting Risk Classification"}',
         '{"model_name": "Underwriting Risk Classification"}'),
        ("```json\n{\"model_type\": \"CatBoostClassifier\"}\n```",
         '{"model_type": "CatBoostClassifier"}'),
        ("```\n[\"CUST00000001\", \"CUST00006151\"]\n```",
         '["CUST00000001", "CUST00006151"]'),
    ])
    def test_parametrized_inputs(self, raw, expected):
        assert self.shared.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def setup_method(self):
        self.shared = import_shared()

    def _mock_anthropic(self, return_text="Claude response"):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_content = MagicMock()
        mock_content.text = return_text
        mock_message.content = [mock_content]
        mock_client.messages.create.return_value = mock_message
        return mock_client

    def test_happy_path_returns_text(self):
        mock_client = self._mock_anthropic("Hello, world!")
        with patch("anthropic.Anthropic", return_value=mock_client):
            self.shared = import_shared()
            result = self.shared.call_claude("system prompt", "user prompt")
        assert result == "Hello, world!"

    def test_passes_correct_model(self):
        mock_client = self._mock_anthropic()
        with patch("anthropic.Anthropic", return_value=mock_client):
            self.shared = import_shared()
            self.shared.call_claude("sys", "usr")
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == "claude-sonnet-4-6" or \
               call_args_model(call_kwargs) == "claude-sonnet-4-6"

    def test_passes_system_and_user(self):
        mock_client = self._mock_anthropic()
        with patch("anthropic.Anthropic", return_value=mock_client):
            self.shared = import_shared()
            self.shared.call_claude("my system", "my user")
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens(self):
        mock_client = self._mock_anthropic()
        with patch("anthropic.Anthropic", return_value=mock_client):
            self.shared = import_shared()
            self.shared.call_claude("sys", "usr")
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self):
        mock_client = self._mock_anthropic()
        with patch("anthropic.Anthropic", return_value=mock_client):
            self.shared = import_shared()
            self.shared.call_claude("sys", "usr", max_tokens=1024)
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 1024

    def test_api_error_propagates(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        with patch("anthropic.Anthropic", return_value=mock_client):
            self.shared = import_shared()
            with pytest.raises(Exception, match="API error"):
                self.shared.call_claude("sys", "usr")

    def test_uses_api_key_from_env(self):
        mock_client = self._mock_anthropic()
        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            self.shared = import_shared()
            self.shared.call_claude("sys", "usr")
        # Anthropic client is instantiated with the key from env
        mock_cls.assert_called_once_with(api_key="test-anthropic-key")

    def test_empty_response_content(self):
        mock_client = MagicMock()
        mock_content = MagicMock()
        mock_content.text = ""
        mock_client.messages.create.return_value.content = [mock_content]
        with patch("anthropic.Anthropic", return_value=mock_client):
            self.shared = import_shared()
            result = self.shared.call_claude("sys", "usr")
        assert result == ""


def call_args_model(call_kwargs):
    """Helper to extract model from either args or kwargs."""
    if call_kwargs.kwargs:
        return call_kwargs.kwargs.get("model")
    if call_kwargs.args:
        return call_kwargs.args[0]
    return None


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def setup_method(self):
        self.shared = import_shared()

    def _make_tree_response(self, paths):
        return {
            "tree": [
                {"type": "blob", "path": p, "url": f"https://api.github.com/blob/{p}"}
                for p in paths
            ]
        }

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return {"content": encoded + "\n"}  # GitHub adds newline

    def test_happy_path_single_extension(self):
        tree = self._make_tree_response(["src/main.py", "src/utils.py", "README.md"])
        blobs = {
            "src/main.py": self._make_blob_response("print('main')"),
            "src/utils.py": self._make_blob_response("print('utils')"),
        }

        def mock_get(url, headers=None):
            resp = MagicMock()
            if "git/trees" in url:
                resp.json.return_value = tree
            elif "main.py" in url:
                resp.json.return_value = blobs["src/main.py"]
            elif "utils.py" in url:
                resp.json.return_value = blobs["src/utils.py"]
            return resp

        with patch("requests.get", side_effect=mock_get):
            result = self.shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "README.md" not in result
        assert result["src/main.py"] == "print('main')"

    def test_multiple_extensions(self):
        tree = self._make_tree_response(["a.py", "b.json", "c.md", "d.txt"])

        def mock_get(url, headers=None):
            resp = MagicMock()
            if "git/trees" in url:
                resp.json.return_value = tree
            else:
                fname = url.split("/")[-1]
                resp.json.return_value = self._make_blob_response(f"content of {fname}")
            return resp

        with patch("requests.get", side_effect=mock_get):
            result = self.shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "a.py" in result
        assert "b.json" in result
        assert "c.md" not in result
        assert "d.txt" not in result

    def test_max_files_limit(self):
        paths = [f"file{i}.py" for i in range(30)]
        tree = self._make_tree_response(paths)

        def mock_get(url, headers=None):
            resp = MagicMock()
            if "git/trees" in url:
                resp.json.return_value = tree
            else:
                resp.json.return_value = self._make_blob_response("content")
            return resp

        with patch("requests.get", side_effect=mock_get):
            result = self.shared.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    def test_empty_tree(self):
        def mock_get(url, headers=None):
            resp = MagicMock()
            resp.json.return_value = {"tree": []}
            return resp

        with patch("requests.get", side_effect=mock_get):
            result = self.shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions(self):
        tree = self._make_tree_response(["README.md", "LICENSE", "Makefile"])

        def mock_get(url, headers=None):
            resp = MagicMock()
            resp.json.return_value = tree
            return resp

        with patch("requests.get", side_effect=mock_get):
            result = self.shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_skips_non_blob_items(self):
        tree_data = {
            "tree": [
                {"type": "tree", "path": "src", "url": "https://api.github.com/tree/src"},
                {"type": "blob", "path": "src/main.py", "url": "https://api.github.com/blob/main.py"},
            ]
        }

        def mock_get(url, headers=None):
            resp = MagicMock()
            if "git/trees" in url:
                resp.json.return_value = tree_data
            else:
                resp.json.return_value = self._make_blob_response("content")
            return resp

        with patch("requests.get", side_effect=mock_get):
            result = self