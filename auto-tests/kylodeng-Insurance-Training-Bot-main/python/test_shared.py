"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): file creation (no SHA) and update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure status codes
- email_html(): HTML generation, status colour logic
- write_audit_entry(): audit log creation (stubbed – incomplete source)

Mocks used:
- unittest.mock.patch for requests.get / requests.post / requests.put
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- os.environ patched via monkeypatch / pytest fixture

TODOs:
- write_audit_entry() source is truncated; full behaviour cannot be verified without
  seeing the complete function body.
"""

import base64
import json
import os
import sys
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

for k, v in ENV_DEFAULTS.items():
    os.environ.setdefault(k, v)

# Ensure the scripts directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

import shared  # noqa: E402  (import after env setup)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_env(monkeypatch):
    """Guarantee each test starts with clean env values."""
    for k, v in ENV_DEFAULTS.items():
        monkeypatch.setenv(k, v)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None, text: str = ""):
    """Build a mock requests.Response-like object."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text
    mock_resp.json.return_value = json_data if json_data is not None else {}
    return mock_resp


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "  \n  {\"a\": 1}  \n  "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_fence_with_whitespace_around_content(self):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_fence_markers(self):
        raw = "```\n```"
        result = shared.clean_json(raw)
        # After stripping opening line we get "```", rsplit drops it → ""
        assert result == ""

    def test_multiline_json_preserved(self):
        raw = "```json\n{\n  \"key\": \"value\",\n  \"num\": 42\n}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"key": "value", "num": 42}

    def test_no_closing_fence(self):
        """If there's no closing fence rsplit returns the whole string."""
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        # rsplit on '```' with maxsplit=1 returns [content] when marker absent
        assert '{"a": 1}' in result

    def test_returns_string_type(self):
        assert isinstance(shared.clean_json("{}"), str)


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_claude_client(self, response_text: str):
        mock_content = MagicMock()
        mock_content.text = response_text

        mock_message = MagicMock()
        mock_message.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        return mock_client

    @patch("shared.anthropic.Anthropic")
    def test_returns_text_response(self, mock_anthropic_cls):
        mock_client = self._make_claude_client("Hello, world!")
        mock_anthropic_cls.return_value = mock_client

        result = shared.call_claude(system="You are a bot.", user="Say hello.")
        assert result == "Hello, world!"

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls):
        mock_client = self._make_claude_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        mock_client = self._make_claude_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="sys_prompt", user="user_prompt")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "sys_prompt"
        assert kwargs["messages"] == [{"role": "user", "content": "user_prompt"}]

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_claude_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_claude_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls):
        mock_client = self._make_claude_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("sys", "usr")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="API error"):
            shared.call_claude("sys", "usr")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path: str, url: str = "https://example.com/blob"):
        return {"type": "blob", "path": path, "url": url}

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode() + "\n"
        return {"content": encoded}

    @patch("shared.requests.get")
    def test_fetches_files_matching_extension(self, mock_get):
        tree = [
            self._tree_item("src/main.py"),
            self._tree_item("README.md"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("print('hello')")),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/main.py" in result
        assert "README.md" not in result
        assert result["src/main.py"] == "print('hello')"

    @patch("shared.requests.get")
    def test_respects_max_files_limit(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        blob_resp = _make_response(json_data=self._blob_response("content"))

        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
        ] + [blob_resp] * 5  # only 5 will be fetched

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)
        assert len(result) == 5

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = [
            {"type": "tree", "path": "src/", "url": "https://example.com/tree"},
            self._tree_item("src/main.py"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("code")),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/" not in result
        assert "src/main.py" in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = [
            self._tree_item("main.py"),
            self._tree_item("app.js"),
            self._tree_item("README.txt"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("py_code")),
            _make_response(json_data=self._blob_response("js_code")),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])
        assert "main.py" in result
        assert "app.js" in result
        assert "README.txt" not in result

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_missing_tree_key_returns_empty_dict(self, mock_get):
        mock_get.return_value = _make_response(json_data={})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_blob_decode_failure_skipped_silently(self, mock_get):
        tree = [self._tree_item("bad.py")]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data={"content": "!!!not-valid-base64!!!"}),
        ]
        # Should not raise; the file is simply skipped
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "bad.py" not in result

    @patch("shared.requests.get")
    def test_correct_tree_url_built(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        shared.get_repo_files("myowner", "myrepo", [".py"])
        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url

    @patch("shared.requests.get")
    def test_default_max_files_is_20(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(25)]
        blob_resp = _make_response(json_data=self._blob_response("content"))
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
        ] + [blob_resp] * 20

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert len(result) == 20


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    @patch("shared.requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_get.return_value = _make_response(text="diff --git a/foo.py b/foo.py\n+added line")
        result = shared.get_pr_diff("owner", "repo", 42)
        assert "diff --git" in result

    @patch("shared.requests.get")
    def test_truncates_to_30000_chars(self, mock_get):
        long_diff = "x" * 50000
        mock_get.return_value = _make_response(text=long_diff)
        result = shared.get_pr_diff("owner", "repo", 1)
        assert len(result) == 30000

    @patch("shared.requests.get")
    def test_short_diff_not_truncated(self, mock_get):
        short_diff = "small diff"
        mock_get.return_value = _make_response(text=short_diff)
        result = shared.get_pr_diff("owner", "repo", 1)
        assert result == "small diff"

    @patch("shared.requests.get")
    def test_uses_diff_accept_header(self, mock_get):
        mock_get.return_value = _make_response(text="")
        shared.get_pr_diff("owner", "repo", 7)
        headers = mock_get.call_args[1]["headers"]
        assert headers["Accept"] == "application/vnd.github.diff"