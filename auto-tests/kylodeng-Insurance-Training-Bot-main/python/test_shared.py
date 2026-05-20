"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration
- clean_json(): Markdown fence stripping utility
- get_repo_files(): GitHub repo file fetching with extension filtering
- get_pr_diff(): GitHub PR diff fetching
- write_output_file(): GitHub file create/update
- post_pr_comment(): GitHub PR comment posting
- send_email(): SendGrid email sending
- email_html(): HTML email template generation
- write_audit_entry(): Audit log writing (stub – requires more context)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- base64 encoding/decoding tested with real stdlib (no mock needed)

TODOs:
- TODO: write_audit_entry full coverage requires knowing the complete function body
        (source is truncated); stub tests are marked with pytest.mark.skip
- TODO: Integration tests for actual Claude/SendGrid/GitHub endpoints (omitted – would need live keys)
"""

import base64
import datetime
import json
import os
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap environment variables BEFORE importing the module under test,
# because shared.py reads os.environ at module level and will raise KeyError
# if the keys are absent.
# ---------------------------------------------------------------------------
_ENV_PATCH = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}

with mock.patch.dict(os.environ, _ENV_PATCH, clear=False):
    import importlib
    # Ensure anthropic stub exists so the import doesn't blow up in CI
    if "anthropic" not in sys.modules:
        anthropic_stub = types.ModuleType("anthropic")
        anthropic_stub.Anthropic = MagicMock()
        sys.modules["anthropic"] = anthropic_stub

    import shared  # noqa: E402  (the module under test)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_module_globals(monkeypatch):
    """Keep module-level constants stable across tests."""
    monkeypatch.setattr(shared, "OUTPUT_REPO", "ai-delivery-outputs")
    monkeypatch.setattr(shared, "OUTPUT_REPO_OWNER", "test-owner")
    monkeypatch.setattr(shared, "NOTIFY_EMAIL", "notify@example.com")
    monkeypatch.setattr(shared, "SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(shared, "SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(shared, "GH_TOKEN", "test-gh-token")


# ============================================================================
# clean_json
# ============================================================================

class TestCleanJson:
    """Tests for the clean_json() utility."""

    def test_no_fences_returns_as_is(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = '  \n{"key": "value"}  \n'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self):
        raw = '  ```json\n{"a": 1}\n```  '
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_fence_markers(self):
        raw = "```\n```"
        result = shared.clean_json(raw)
        # After stripping opening line we get "```", rsplit drops it → empty
        assert result == ""

    def test_nested_json_preserved(self):
        raw = '```json\n{"outer": {"inner": [1, 2, 3]}}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["outer"]["inner"] == [1, 2, 3]

    def test_multiline_json_preserved(self):
        raw = '```json\n{\n  "key": "value",\n  "num": 42\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["num"] == 42

    def test_no_fence_with_backtick_in_content(self):
        raw = '{"code": "use `backtick`"}'
        assert shared.clean_json(raw) == '{"code": "use `backtick`"}'

    @pytest.mark.parametrize("raw,expected", [
        ('{"a":1}', '{"a":1}'),
        ('```json\n[1,2,3]\n```', '[1,2,3]'),
        ('```\ntrue\n```', 'true'),
        ('  null  ', 'null'),
    ])
    def test_parametrized_cases(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ============================================================================
# call_claude
# ============================================================================

class TestCallClaude:
    """Tests for the call_claude() function."""

    def _make_mock_client(self, text_response: str):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_content = MagicMock()
        mock_content.text = text_response
        mock_message.content = [mock_content]
        mock_client.messages.create.return_value = mock_message
        return mock_client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("Hello from Claude")
        mock_anthropic_cls.return_value = mock_client

        result = shared.call_claude("You are helpful.", "Say hello.")

        assert result == "Hello from Claude"

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("system", "user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("sys-prompt", "user-prompt")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "sys-prompt"
        assert kwargs["messages"] == [{"role": "user", "content": "user-prompt"}]

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key_from_module(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    @patch("shared.anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="API error"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_returns_first_content_block(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_message = MagicMock()
        first = MagicMock()
        first.text = "first block"
        second = MagicMock()
        second.text = "second block"
        mock_message.content = [first, second]
        mock_client.messages.create.return_value = mock_message
        mock_anthropic_cls.return_value = mock_client

        result = shared.call_claude("s", "u")
        assert result == "first block"


# ============================================================================
# get_repo_files
# ============================================================================

class TestGetRepoFiles:
    """Tests for get_repo_files()."""

    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content: str):
        mock_resp = MagicMock()
        encoded = base64.b64encode(content.encode()).decode()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    @patch("shared.requests.get")
    def test_returns_matching_files(self, mock_get):
        tree = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/1"},
            {"type": "blob", "path": "README.md", "url": "http://blob/2"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("print('hello')"),
            self._make_blob_response("# readme"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".md"])

        assert "src/main.py" in result
        assert "README.md" in result
        assert result["src/main.py"] == "print('hello')"
        assert result["README.md"] == "# readme"

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        tree = [
            {"type": "blob", "path": "app.py", "url": "http://blob/1"},
            {"type": "blob", "path": "data.json", "url": "http://blob/2"},
            {"type": "blob", "path": "notes.txt", "url": "http://blob/3"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("py content"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "data.json" not in result
        assert "notes.txt" not in result

    @patch("shared.requests.get")
    def test_ignores_tree_nodes(self, mock_get):
        tree = [
            {"type": "tree", "path": "src", "url": "http://tree/1"},
            {"type": "blob", "path": "src/app.py", "url": "http://blob/1"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("content"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "src/app.py" in result

    @patch("shared.requests.get")
    def test_respects_max_files(self, mock_get):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_responses = [self._make_blob_response(f"content{i}") for i in range(3)]
        mock_get.side_effect = [self._make_tree_response(tree)] + blob_responses

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = self._make_tree_response([])

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_handles_blob_decode_error_gracefully(self, mock_get):
        tree = [
            {"type": "blob", "path": "bad.py", "url": "http://blob/bad"},
            {"type": "blob", "path": "good.py", "url": "http://blob/good"},
        ]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "!!!invalid base64!!!"}
        good_blob = self._make_blob_response("good content")

        mock_get.side_effect = [self._make_tree_response(tree), bad_blob, good_blob]

        # Should not raise; bad file is skipped
        result = shared.get_repo_files("owner", "repo", [".py"])
        # bad.py may be skipped due to exception; good.py should be present
        assert "good.py" in result

    @patch("shared.requests.get")
    def test_correct_url_constructed(self, mock_get):
        mock_get.return_value = self._make_tree_response([])

        shared.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    @patch("shared.requests.get")
    def test_missing_tree_key_returns_empty(self, mock_get):
        mock_resp = MagicMock()
        mock_resp