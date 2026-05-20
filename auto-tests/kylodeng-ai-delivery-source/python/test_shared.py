"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude: Claude API integration (happy path, error conditions)
  - clean_json: Markdown fence stripping (various fence formats, edge cases)
  - get_repo_files: GitHub repo file fetching (filtering, max_files, decode errors)
  - get_pr_diff: Pull request diff fetching (happy path, truncation)
  - write_output_file: File creation/update in output repo (new file, existing file)
  - post_pr_comment: PR comment posting (happy path, request formation)
  - send_email: SendGrid email sending (success, failure status codes)
  - email_html: HTML email template generation (SUCCESS/FAILURE status, content checks)
  - write_audit_entry: Audit log writing (structure, delegation to write_output_file)

Mocks used:
  - unittest.mock.patch for os.environ (all required env vars)
  - unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
  - unittest.mock.MagicMock for anthropic.Anthropic client
  - base64 encoding/decoding verified inline

TODOs:
  - TODO: write_audit_entry source truncated in provided code — stub tests cover
          what is visible; full JSON/Markdown log format needs complete source.
  - TODO: Integration tests for real GitHub API require live GH_TOKEN + repo access.
  - TODO: Integration tests for real SendGrid require live SENDGRID_API_KEY.
  - TODO: Integration tests for real Claude require live ANTHROPIC_API_KEY.
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------
FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-gh-owner",
}


def _load_shared():
    """Load (or reload) shared module with fake environment variables."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Make sure the script directory is on sys.path
        import importlib.util, os
        spec = importlib.util.spec_from_file_location(
            "shared", ".github/scripts/shared.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["shared"] = mod
        spec.loader.exec_module(mod)
        return mod


@pytest.fixture(scope="module")
def shared():
    """Module-level fixture: import shared with env patched."""
    return _load_shared()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    """Encode text to base64 string as GitHub API returns."""
    return base64.b64encode(text.encode()).decode()


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:

    def test_no_fences_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self, shared):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_surrounding_whitespace(self, shared):
        raw = "  \n```json\n{\"a\": 1}\n```\n  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   \n  ") == ""

    def test_multiline_json_in_fence(self, shared):
        raw = "```json\n{\n  \"key\": \"value\",\n  \"num\": 42\n}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    def test_no_closing_fence(self, shared):
        """If there's an opening fence but no closing, rsplit returns original minus opening."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # rsplit on "```" with no closing fence leaves content intact (stripped)
        assert '{"key": "value"}' in result

    def test_fence_with_extra_language_tag(self, shared):
        raw = "```python\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert '{"key": "value"}' in result

    def test_valid_json_after_clean(self, shared):
        raw = "```json\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert json.loads(result) == [1, 2, 3]


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:

    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_happy_path_returns_text(self, shared):
        mock_response = self._make_response("Hello from Claude")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("You are helpful.", "Say hello")

        assert result == "Hello from Claude"

    def test_passes_correct_model(self, shared):
        mock_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("system prompt", "user prompt")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self, shared):
        mock_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens(self, shared):
        mock_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        mock_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self, shared):
        mock_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
            shared.call_claude("sys", "usr")

        mock_anthropic.assert_called_once_with(api_key="test-anthropic-key")

    def test_api_exception_propagates(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("sys", "usr")

    def test_empty_system_prompt(self, shared):
        mock_response = self._make_response("response text")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("", "user message")

        assert result == "response text"

    def test_long_user_message(self, shared):
        long_message = "x" * 100_000
        mock_response = self._make_response("handled")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys", long_message)

        assert result == "handled"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _mock_tree(self, items):
        """Build a mock response for the tree API call."""
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _mock_blob(self, content_text):
        resp = MagicMock()
        resp.json.return_value = {"content": _b64(content_text) + "\n"}
        return resp

    def test_happy_path_filters_by_extension(self, shared):
        tree_items = [
            {"type": "blob", "path": "src/main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "src/utils.py", "url": "https://api.github.com/blob/3"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._mock_tree(tree_items)
            elif "blob/1" in url:
                return self._mock_blob("print('main')")
            elif "blob/3" in url:
                return self._mock_blob("def util(): pass")
            return MagicMock()

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "README.md" not in result

    def test_max_files_respected(self, shared):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._mock_tree(tree_items)
            return self._mock_blob(f"content")

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._mock_tree(tree_items)
            return self._mock_blob("code")

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "main.py" in result

    def test_empty_tree(self, shared):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"tree": []}
            mock_get.return_value = mock_resp
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self, shared):
        tree_items = [
            {"type": "blob", "path": "app.js", "url": "https://api.github.com/blob/js"},
            {"type": "blob", "path": "style.css", "url": "https://api.github.com/blob/css"},
            {"type": "blob", "path": "readme.txt", "url": "https://api.github.com/blob/txt"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._mock_tree(tree_items)
            return self._mock_blob("content")

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".js", ".css"])

        assert "app.js" in result
        assert "style.css" in result
        assert "readme.txt" not in result

    def test_decode_error_skips_file(self, shared):
        tree_items = [
            {"type": "blob", "path": "bad.py", "url": "https://api.github.com/blob/bad"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._mock_tree(tree_items)
            # Return malformed response missing 'content'
            mock = MagicMock()
            mock.json.return_value = {}  # No 'content' key → KeyError caught
            return mock

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "bad.py" not in result

    def test_no_matching_extensions(self, shared):
        tree_items = [
            {"type": "blob", "path": "main.go", "url": "https://api.github.com/blob/go"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._mock_tree(tree_items)
            return self._mock_blob("go code")

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result ==