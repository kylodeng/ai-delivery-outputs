"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration (happy path, error handling)
- clean_json(): Markdown fence stripping (various fence formats, edge cases)
- get_repo_files(): GitHub repo file fetching (filtering, max_files, decode errors)
- get_pr_diff(): PR unified diff fetching (happy path, truncation)
- write_output_file(): File create/update in output repo (new file, existing file with SHA)
- post_pr_comment(): PR comment posting (happy path)
- send_email(): SendGrid email dispatch (success codes, failure warning)
- email_html(): HTML email body generation (SUCCESS/FAILURE status, content checks)
- write_audit_entry(): Audit log entry writing (structure, delegation to write_output_file)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for shared.write_output_file (in audit entry tests)

TODOs:
- TODO: Integration test for call_claude with a real Anthropic sandbox key
- TODO: Test write_audit_entry markdown log format in detail once full source is available
  (source was truncated — write_audit_entry body is incomplete)
- TODO: Parameterised tests against real SendGrid sandbox once credentials are available
"""

import base64
import json
import sys
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: inject mandatory environment variables BEFORE importing shared.py
# ---------------------------------------------------------------------------
_ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}

# Patch env before import so module-level reads succeed
with mock.patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
    import importlib
    import shared  # noqa: E402  (must come after env patch)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_env(monkeypatch):
    """Ensure each test starts with a clean, predictable environment."""
    for k, v in _ENV_DEFAULTS.items():
        monkeypatch.setenv(k, v)


def _make_blob_response(content: str) -> dict:
    """Return a fake GitHub blob API response with base64-encoded content."""
    encoded = base64.b64encode(content.encode()).decode() + "\n"
    return {"content": encoded, "encoding": "base64"}


# ---------------------------------------------------------------------------
# Tests: clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_backtick_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_bare_backtick_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = "   \n```json\n{\"a\": 1}\n```\n   "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   \n\t  ") == ""

    def test_no_fence_with_whitespace(self):
        raw = "  [1, 2, 3]  "
        assert shared.clean_json(raw) == "[1, 2, 3]"

    def test_nested_backticks_inside_json_not_stripped(self):
        """Content that starts with ``` but has no closing fence returns partial."""
        raw = "```json\n{\"code\": \"value\"}"
        result = shared.clean_json(raw)
        # After splitting on first newline we get the json; rsplit on ``` finds none → full remainder
        assert '{"code": "value"}' in result

    def test_multiline_json(self):
        raw = "```json\n{\n  \"key\": \"value\",\n  \"num\": 42\n}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"key": "value", "num": 42}

    def test_json_array(self):
        raw = "```json\n[1,2,3]\n```"
        result = shared.clean_json(raw)
        assert json.loads(result) == [1, 2, 3]

    def test_already_clean_complex_json(self):
        payload = json.dumps({"product_name": "Generations II", "doc_type": "product_brochure"})
        assert shared.clean_json(payload) == payload


# ---------------------------------------------------------------------------
# Tests: call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def _make_client_mock(self, text: str):
        """Return a mock anthropic.Anthropic client whose messages.create returns `text`."""
        content_block = MagicMock()
        content_block.text = text

        response = MagicMock()
        response.content = [content_block]

        client = MagicMock()
        client.messages.create.return_value = response
        return client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        expected = '{"answer": 42}'
        mock_anthropic_cls.return_value = self._make_client_mock(expected)

        result = shared.call_claude("system prompt", "user prompt")

        assert result == expected

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("my-system", "my-user")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["system"] == "my-system"
        assert kwargs["messages"] == [{"role": "user", "content": "my-user"}]

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls):
        client_mock = MagicMock()
        client_mock.messages.create.side_effect = Exception("API unavailable")
        mock_anthropic_cls.return_value = client_mock

        with pytest.raises(Exception, match="API unavailable"):
            shared.call_claude("sys", "usr")

    @patch("shared.anthropic.Anthropic")
    def test_empty_response_text(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("")
        mock_anthropic_cls.return_value = client_mock

        result = shared.call_claude("sys", "usr")
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _tree_item(self, path: str, type_: str = "blob", url: str = "http://blob-url"):
        return {"type": type_, "path": path, "url": url}

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        tree = [
            self._tree_item("file.py"),
            self._tree_item("readme.md"),
            self._tree_item("script.js"),
        ]
        blob_content = _make_blob_response("print('hello')")

        def side_effect(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "file.py" in result
        assert "readme.md" not in result
        assert "script.js" not in result

    @patch("shared.requests.get")
    def test_respects_max_files(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        blob_content = _make_blob_response("content")

        def side_effect(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    @patch("shared.requests.get")
    def test_decodes_base64_content(self, mock_get):
        tree = [self._tree_item("hello.py")]
        blob_content = _make_blob_response("print('hello world')")

        def side_effect(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result["hello.py"] == "print('hello world')"

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = [
            {"type": "tree", "path": "somedir", "url": "http://tree-url"},
            self._tree_item("actual.py"),
        ]
        blob_content = _make_blob_response("code")

        def side_effect(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "somedir" not in result
        assert "actual.py" in result

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        def side_effect(url, headers=None):
            resp = MagicMock()
            resp.json.return_value = {"tree": []}
            return resp

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_missing_tree_key_returns_empty_dict(self, mock_get):
        resp = MagicMock()
        resp.json.return_value = {}
        mock_get.return_value = resp

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_handles_decode_error_gracefully(self, mock_get):
        tree = [self._tree_item("bad.py")]

        def side_effect(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = {"content": "!!!NOT_BASE64!!!"}
            return resp

        mock_get.side_effect = side_effect

        # Should not raise; file simply won't be in result
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "bad.py" not in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = [
            self._tree_item("script.py"),
            self._tree_item("config.json"),
            self._tree_item("readme.txt"),
        ]
        blob_content = _make_blob_response("data")

        def side_effect(url, headers=None):
            resp = MagicMock()
            if "trees" in url:
                resp.json.return_value = {"tree": tree}
            else:
                resp.json.return_value = blob_content
            return resp

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py", ".json"])
        assert "script.py" in result
        assert "config.json" in result
        assert "readme.txt" not in result

    @patch("shared.requests.get")
    def test_uses_correct_github_tree_url(self, mock_get):
        resp = MagicMock()
        resp.json.return_value = {"tree": []}
        mock_get.return_value = resp

        shared.get_repo_files("my-owner", "my-repo", [".py"])

        called_url = mock_get.call_args_list[0][0][0]
        assert "my-owner" in called_url
        assert "my-repo" in called_url
        assert "HEAD" in called_url
        assert "