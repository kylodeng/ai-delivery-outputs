"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Happy path, API error handling, custom max_tokens
- clean_json(): Stripping markdown fences, plain JSON passthrough, edge cases
- get_repo_files(): Happy path, extension filtering, max_files cap, decode errors, empty tree
- get_pr_diff(): Happy path, truncation at 30000 chars
- write_output_file(): Create new file (no SHA), update existing file (with SHA), missing html_url fallback
- post_pr_comment(): Happy path, correct URL construction
- send_email(): Success (202), failure (4xx) warning path, custom recipient
- email_html(): SUCCESS/FAILURE status colour, HTML structure, all fields rendered
- write_audit_entry(): Covered via stub (function body is truncated in source)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for base64.b64decode (where needed)

TODOs:
- TODO: write_audit_entry() source is truncated — full logic untested; stub provided
- TODO: MODEL constant ("claude-sonnet-4-6") — verify correct model name when available
- TODO: GH_HEADERS version header value — confirm "2022-11-28" is still current
"""

import base64
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Bootstrap: inject required env vars BEFORE shared.py is imported
# ---------------------------------------------------------------------------
_ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

for _k, _v in _ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

# Now safe to import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
import shared  # noqa: E402  (module path added above)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_module_constants(monkeypatch):
    """Ensure module-level constants derived from env stay predictable."""
    monkeypatch.setattr(shared, "OUTPUT_REPO", "ai-delivery-outputs")
    monkeypatch.setattr(shared, "OUTPUT_REPO_OWNER", "test-owner")
    monkeypatch.setattr(shared, "NOTIFY_EMAIL", "notify@example.com")
    monkeypatch.setattr(shared, "SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(shared, "SENDGRID_API_KEY", "test-sendgrid-key")


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for shared.call_claude()"""

    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("Hello, world!")

        result = shared.call_claude("You are helpful.", "Say hello.")

        assert result == "Hello, world!"

    @patch("shared.anthropic.Anthropic")
    def test_uses_correct_model(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("system", "user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("system", "user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("system", "user", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user_messages(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("Be concise.", "What is 2+2?")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "Be concise."
        assert kwargs["messages"] == [{"role": "user", "content": "What is 2+2?"}]

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API unavailable")

        with pytest.raises(Exception, match="API unavailable"):
            shared.call_claude("system", "user")

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key_from_module(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("ok")

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    @patch("shared.anthropic.Anthropic")
    def test_empty_system_prompt(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response("response")

        result = shared.call_claude("", "Tell me something.")
        assert result == "response"

    @patch("shared.anthropic.Anthropic")
    def test_large_response_text(self, mock_anthropic_cls):
        large_text = "x" * 100_000
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_response(large_text)

        result = shared.call_claude("s", "u")
        assert result == large_text


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for shared.clean_json()"""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_surrounding_whitespace(self):
        raw = '  \n{"key": "value"}\n  '
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_whitespace_inside_fences(self):
        raw = '```json\n  {"key": "value"}  \n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_multiline_json_in_fence(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        # Should be valid JSON after stripping
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_no_closing_fence(self):
        # Edge case: opening fence but no closing; should strip the first line
        raw = '```json\n{"key": "value"}'
        result = shared.clean_json(raw)
        assert '{"key": "value"}' in result

    def test_json_array(self):
        raw = '```json\n[1, 2, 3]\n```'
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_does_not_alter_non_fence_content(self):
        raw = "just a plain string"
        assert shared.clean_json(raw) == "just a plain string"

    def test_real_model_card_snippet(self):
        """Uses synthetic data: model_card-like JSON wrapped in fences."""
        raw = '```json\n{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_nested_backticks_in_content_preserved(self):
        """Backticks inside the JSON value should be preserved."""
        raw = '```json\n{"code": "use `x`"}\n```'
        result = shared.clean_json(raw)
        assert "`x`" in result


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for shared.get_repo_files()"""

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
    def test_happy_path_fetches_matching_files(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/2"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("print('hello')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert result["app.py"] == "print('hello')"
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_filters_by_multiple_extensions(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "main.py", "url": "u1"},
            {"type": "blob", "path": "index.js", "url": "u2"},
            {"type": "blob", "path": "style.css", "url": "u3"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("python code"),
            self._make_blob_response("js code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "main.py" in result
        assert "index.js" in result
        assert "style.css" not in result

    @patch("shared.requests.get")
    def test_respects_max_files_cap(self, mock_get):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"u{i}"}
            for i in range(10)
        ]
        blob_response = self._make_blob_response("content")

        # tree call + up to 3 blob calls
        mock_get.side_effect = (
            [self._make_tree_response(tree_items)]
            + [self._make_blob_response(f"content{i}") for i in range(10)]
        )

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_tree_nodes(self, mock_get):
        tree_items = [
            {"type": "tree", "path": "src", "url": "u1"},
            {"type": "blob", "path": "main.py", "url": "u2"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "main.py" in result

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = self._make_tree_response([])
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_handles_decode_exception_gracefully(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "bad.py", "url": "u1"},
            {"type": "blob", "path": "good.py", "url": "u2"},
        ]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {}  # missing 'content' key → KeyError
        good_blob = self._make_blob_response("good code")

        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            bad_blob,
            good_blob,
        ]

        result = shared.get_repo_files("owner", "repo", [".py