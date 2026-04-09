"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation at 30000 chars
- write_output_file(): create/update file in output repo (with/without SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid API call, success/failure handling
- email_html(): HTML template generation, status colour logic
- write_audit_entry(): audit log entry construction (stub — source truncated)

Mocks used:
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- os.environ patched via monkeypatch / patch.dict

TODOs:
- write_audit_entry: source code is truncated; full behaviour cannot be tested without
  knowing how the audit entry is persisted/formatted — stubs provided.
- email_html timestamp: utcnow() is called inside the function; exact timestamp matching
  requires freezing time (e.g. freezegun) — approximation used instead.
"""

import base64
import json
import os
import importlib
import sys
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-gh-owner",
}


@pytest.fixture(autouse=True, scope="session")
def patch_env_and_import():
    """Ensure all required env vars exist before shared.py is imported."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        # Force fresh import so module-level os.environ[] calls succeed
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Add the script directory to path
        script_dir = os.path.join(os.path.dirname(__file__), "..", ".github", "scripts")
        script_dir = os.path.normpath(script_dir)
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        yield


@pytest.fixture()
def shared_module():
    """Return a freshly-importable reference to shared (already loaded in session)."""
    import shared  # noqa: PLC0415
    return shared


# ===========================================================================
# Helpers
# ===========================================================================

def _make_anthropic_response(text: str):
    """Build a minimal fake anthropic Messages response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:

    def test_no_fences_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self, shared_module):
        raw = "```\n{\"hello\": 1}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"hello": 1}'

    def test_strips_leading_trailing_whitespace(self, shared_module):
        raw = "   \n{\"a\": 1}\n   "
        result = shared_module.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_fence_with_whitespace(self, shared_module):
        raw = "  ```json\n{\"x\": 2}\n```  "
        result = shared_module.clean_json(raw)
        assert result == '{"x": 2}'

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_fence_markers(self, shared_module):
        raw = "```\n```"
        result = shared_module.clean_json(raw)
        # Should not raise; result is an empty or minimal string
        assert isinstance(result, str)

    def test_nested_content_preserved(self, shared_module):
        inner = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        raw = f"```json\n{inner}\n```"
        result = shared_module.clean_json(raw)
        assert json.loads(result)["model_name"] == "Underwriting Risk Classification"

    def test_multiline_json_preserved(self, shared_module):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        result = shared_module.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_no_closing_fence(self, shared_module):
        """Should not raise even if closing fence is missing."""
        raw = "```json\n{\"k\": \"v\"}"
        result = shared_module.clean_json(raw)
        assert isinstance(result, str)


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:

    @patch("anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_anthropic_response("Hello from Claude")

        with patch.object(shared_module, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            result = shared_module.call_claude("system prompt", "user message")

        assert result == "Hello from Claude"

    @patch("anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        with patch.object(shared_module, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_module.call_claude("sys", "usr")
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs.get("model") == shared_module.MODEL

    @patch("anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        with patch.object(shared_module, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_module.call_claude("sys", "usr")
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs.get("max_tokens") == 4096

    @patch("anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        with patch.object(shared_module, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_module.call_claude("sys", "usr", max_tokens=512)
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs.get("max_tokens") == 512

    @patch("anthropic.Anthropic")
    def test_messages_structure(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        with patch.object(shared_module, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            shared_module.call_claude("sys text", "user text")
            _, kwargs = mock_client.messages.create.call_args
            messages = kwargs.get("messages")
            assert len(messages) == 1
            assert messages[0] == {"role": "user", "content": "user text"}

    @patch("anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API failure")

        with patch.object(shared_module, "anthropic") as mock_anthropic_mod:
            mock_anthropic_mod.Anthropic.return_value = mock_client
            with pytest.raises(RuntimeError, match="API failure"):
                shared_module.call_claude("sys", "usr")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _make_tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, content_str: str):
        encoded = base64.b64encode(content_str.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded + "\n"}
        return resp

    def test_happy_path_single_extension(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "file.py", "url": "https://api.github.com/blob/abc"},
        ]
        blob_content = "print('hello')"
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response(blob_content),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {"file.py": blob_content}

    def test_filters_by_extension(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "main.py", "url": "url1"},
            {"type": "blob", "path": "README.md", "url": "url2"},
            {"type": "blob", "path": "utils.py", "url": "url3"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("py content 1"),
                self._make_blob_response("py content 2"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "main.py" in result
        assert "utils.py" in result
        assert "README.md" not in result

    def test_multiple_extensions(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "a.py", "url": "u1"},
            {"type": "blob", "path": "b.json", "url": "u2"},
            {"type": "blob", "path": "c.txt", "url": "u3"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("py"),
                self._make_blob_response("json"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py", ".json"])
        assert "a.py" in result
        assert "b.json" in result
        assert "c.txt" not in result

    def test_skips_non_blob_items(self, shared_module):
        tree_items = [
            {"type": "tree", "path": "src", "url": "u1"},
            {"type": "blob", "path": "main.py", "url": "u2"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("content"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "main.py" in result

    def test_respects_max_files(self, shared_module):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"url{i}"}
            for i in range(10)
        ]
        blob_resp = self._make_blob_response("content")

        with patch("requests.get") as mock_get:
            # First call = tree; subsequent = blobs
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
            ] + [self._make_blob_response(f"c{i}") for i in range(3)]
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self, shared_module):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._make_tree_response([])
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_handles_decode_error_gracefully(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "good.py", "url": "u1"},
            {"type": "blob", "path": "bad.py", "url": "u2"},
        ]
        good_resp = self._make_blob_response("good content")
        bad_resp = MagicMock()
        bad_resp.json.return_value = {}  # missing 'content' key → raises KeyError

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                good_resp,
                bad_resp,
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        # good file should still be present; bad one silently skipped
        assert "good.py" in result
        assert "bad.py" not in result

    def test_constructs_correct_tree_url(self, shared_module):
        with patch("requests.get") as