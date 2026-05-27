"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: happy path, API response parsing
- clean_json: markdown fence stripping (various formats), plain JSON passthrough
- get_repo_files: extension filtering, max_files limit, base64 decoding, decode errors
- get_pr_diff: URL construction, header overrides, text truncation
- write_output_file: create new file (no SHA), update existing file (with SHA), fallback URL
- post_pr_comment: correct URL and payload construction
- send_email: successful send (200/202), warning on failure, payload structure
- email_html: SUCCESS/FAILURE status colour, content inclusion
- write_audit_entry: stub (source truncated — full function body unavailable)

Mocks used:
- unittest.mock.patch / MagicMock for requests.get, requests.post, requests.put
- unittest.mock.patch for anthropic.Anthropic client
- os.environ patched via monkeypatch / patch.dict

TODOs:
- write_audit_entry: full function body was truncated; only a stub test is provided
- Integration tests for real GitHub / SendGrid / Anthropic endpoints (skipped)
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the module can be imported without real env vars present
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

with patch.dict(os.environ, ENV_DEFAULTS):
    # Add the scripts directory to sys.path so we can import shared
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
    import shared  # noqa: E402


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def _patch_module_level_constants(monkeypatch):
    """Keep module-level constants consistent with test env vars."""
    monkeypatch.setattr(shared, "GH_TOKEN", "test-gh-token")
    monkeypatch.setattr(shared, "SENDGRID_API_KEY", "test-sg-key")
    monkeypatch.setattr(shared, "OUTPUT_REPO", "ai-delivery-outputs")
    monkeypatch.setattr(shared, "OUTPUT_REPO_OWNER", "test-owner")
    monkeypatch.setattr(shared, "NOTIFY_EMAIL", "notify@example.com")
    monkeypatch.setattr(shared, "SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(shared, "ANTHROPIC_API_KEY", "test-anthropic-key")


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_client_mock(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        message = MagicMock()
        message.content = [content_block]
        client = MagicMock()
        client.messages.create.return_value = message
        return client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        client = self._make_client_mock("Hello, world!")
        mock_anthropic_cls.return_value = client

        result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello, world!"

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls):
        client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client

        shared.call_claude("sys", "usr", max_tokens=512)

        _, kwargs = client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL
        assert kwargs["max_tokens"] == 512

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user_messages(self, mock_anthropic_cls):
        client = self._make_client_mock("response")
        mock_anthropic_cls.return_value = client

        shared.call_claude("my system prompt", "my user prompt")

        _, kwargs = client.messages.create.call_args
        assert kwargs["system"] == "my system prompt"
        assert kwargs["messages"] == [{"role": "user", "content": "my user prompt"}]

    @patch("shared.anthropic.Anthropic")
    def test_uses_configured_api_key(self, mock_anthropic_cls):
        client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens_is_4096(self, mock_anthropic_cls):
        client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client

        shared.call_claude("s", "u")

        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_returns_first_content_block(self, mock_anthropic_cls):
        """Ensures only content[0].text is returned, even if multiple blocks exist."""
        content_block_0 = MagicMock()
        content_block_0.text = "first"
        content_block_1 = MagicMock()
        content_block_1.text = "second"
        message = MagicMock()
        message.content = [content_block_0, content_block_1]
        client = MagicMock()
        client.messages.create.return_value = message
        mock_anthropic_cls.return_value = client

        result = shared.call_claude("s", "u")

        assert result == "first"

    @patch("shared.anthropic.Anthropic")
    def test_propagates_api_exception(self, mock_anthropic_cls):
        client = MagicMock()
        client.messages.create.side_effect = Exception("API error")
        mock_anthropic_cls.return_value = client

        with pytest.raises(Exception, match="API error"):
            shared.call_claude("s", "u")


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    # ── Happy paths ──────────────────────────────────────────────────────────

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = "   {\"key\": \"value\"}   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_code_fence_with_surrounding_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    # ── Edge cases ───────────────────────────────────────────────────────────

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_json_array(self):
        raw = "```json\n[1, 2, 3]\n```"
        assert shared.clean_json(raw) == "[1, 2, 3]"

    def test_multiline_json_preserved(self):
        inner = '{\n  "name": "Alice",\n  "age": 34\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_no_closing_fence_strips_opening_only(self):
        """If there is no closing fence the function should still strip the opening line."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # After split on first \n and rsplit on ```, content should be preserved
        assert '{"key": "value"}' in result

    def test_nested_json_with_fences(self):
        inner = '{"outer": {"inner": "value"}}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    # ── Parametrised boundary values ─────────────────────────────────────────

    @pytest.mark.parametrize("raw,expected", [
        ('{"customer_id": "CUST-001", "email": "alice.chen@example.com"}',
         '{"customer_id": "CUST-001", "email": "alice.chen@example.com"}'),
        ('```json\n{"customer_id": "CUST-002"}\n```',
         '{"customer_id": "CUST-002"}'),
        ('```\n[{"id": "CUST-003", "revenue": 500000}]\n```',
         '[{"id": "CUST-003", "revenue": 500000}]'),
    ])
    def test_parametrised_inputs(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    @patch("shared.requests.get")
    def test_happy_path_returns_matching_files(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "src/main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "src/utils.py", "url": "https://api.github.com/blob/2"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("print('main')"),
            self._make_blob_response("print('utils')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert result["src/main.py"] == "print('main')"

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/2"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("# python"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_respects_max_files_limit(self, mock_get):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        # tree response + up to 3 blob responses
        side_effects = [self._make_tree_response(tree_items)] + [
            self._make_blob_response(f"content{i}") for i in range(10)
        ]
        mock_get.side_effect = side_effects

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree_items = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/2"},
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
    def test_multiple_extensions(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "style.css", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "ignore.txt", "url": "https://api.github.com/blob/3"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("python"),
            self._make_blob_response("css"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".css"])

        assert "main.py" in result
        assert "style.css" in result
        assert "ignore.txt" not in result

    @patch("shared.requests.get")
    def test_handles_decode_error_gracefully(self, mock_get):
        """Files that raise on decode should be silently skipped."""
        tree_items = [
            {"type": "blob", "path": "bad.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "good.py", "url": "https://api.github.com/blob/2"},
        ]
        bad_resp = MagicMock()
        bad_resp.json.return_value = {"content": "not-valid-base64!!!"}
        mock_