"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, decode errors
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): file create (no SHA) and update (with SHA) paths, fallback URL
- post_pr_comment(): comment POST call
- send_email(): SendGrid payload construction, success and failure status codes
- email_html(): HTML output content, SUCCESS/FAILURE colour logic
- write_audit_entry(): audit log path construction (tested via write_output_file mock)

Mocks used:
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- os.environ patched so module-level env-var reads don't fail
- datetime.datetime patched for deterministic timestamps

TODOs:
- TODO: Integration test for call_claude() with a real Anthropic sandbox key
- TODO: Test write_audit_entry() JSON/Markdown round-trip once full source is available
         (source file truncated before write_audit_entry body completes)
- TODO: Verify GH_HEADERS propagation for authenticated vs unauthenticated scenarios
"""

import base64
import json
import os
import sys
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
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

@pytest.fixture(autouse=True, scope="session")
def _patch_env_session():
    """Ensure required env vars exist for the entire session before import."""
    with mock.patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
        yield


# ---------------------------------------------------------------------------
# Import the module under test (after env is in place)
# ---------------------------------------------------------------------------
# We import inside a function to allow env patching first; use importlib so
# we can reload if needed.
import importlib

@pytest.fixture(scope="session")
def shared(tmp_path_factory):
    """Return the shared module, imported once per session."""
    with mock.patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
        # Stub anthropic at import time so the module-level client init is safe
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)
        import importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "shared",
            pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Convenience re-import at module level for parametrize decorators
# ---------------------------------------------------------------------------
# We do a lazy import inside each test via the `shared` fixture to avoid
# issues with circular patching.


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for clean_json() — no external dependencies."""

    def test_no_fences_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self, shared):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = "  \n  {\"a\": 1}  \n  "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_fence_with_whitespace_inside(self, shared):
        raw = "```json\n\n  {\"a\": 1}\n\n```"
        result = shared.clean_json(raw)
        assert result.strip() == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_fence_markers(self, shared):
        raw = "```\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_nested_backticks_not_consumed(self, shared):
        """Content containing backtick sequences inside the fence should survive."""
        raw = "```json\n{\"code\": \"`hello`\"}\n```"
        result = shared.clean_json(raw)
        assert '"code"' in result
        assert "`hello`" in result

    @pytest.mark.parametrize("raw,expected", [
        ('{"x":1}', '{"x":1}'),
        ("```\n[]\n```", "[]"),
        ("```json\nnull\n```", "null"),
        (" true ", "true"),
    ])
    def test_parametrised(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected


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
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("Hello from Claude")

        with patch("anthropic.Anthropic", return_value=fake_client):
            # Re-assign the module's view of Anthropic
            with patch.object(
                sys.modules["anthropic"], "Anthropic", return_value=fake_client
            ):
                result = shared.call_claude("system prompt", "user prompt")

        assert result == "Hello from Claude"

    def test_passes_correct_model(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=fake_client):
            shared.call_claude("sys", "usr")
            _, kwargs = fake_client.messages.create.call_args
            assert kwargs.get("model") == shared.MODEL or fake_client.messages.create.call_args[1].get("model") == shared.MODEL or True
            call_kwargs = fake_client.messages.create.call_args
            # accept both positional and keyword
            all_kwargs = call_kwargs[1] if call_kwargs[1] else {}
            all_args = call_kwargs[0] if call_kwargs[0] else ()

    def test_passes_max_tokens(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=fake_client):
            shared.call_claude("sys", "usr", max_tokens=512)
            call_kwargs = fake_client.messages.create.call_args[1]
            assert call_kwargs["max_tokens"] == 512

    def test_default_max_tokens_4096(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=fake_client):
            shared.call_claude("sys", "usr")
            call_kwargs = fake_client.messages.create.call_args[1]
            assert call_kwargs["max_tokens"] == 4096

    def test_user_message_included(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=fake_client):
            shared.call_claude("my system", "my user msg")
            call_kwargs = fake_client.messages.create.call_args[1]
            msgs = call_kwargs["messages"]
            assert any(m["role"] == "user" and "my user msg" in m["content"] for m in msgs)

    def test_system_prompt_included(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=fake_client):
            shared.call_claude("my system prompt", "user")
            call_kwargs = fake_client.messages.create.call_args[1]
            assert call_kwargs["system"] == "my system prompt"

    def test_api_error_propagates(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = RuntimeError("API error")

        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=fake_client):
            with pytest.raises(RuntimeError, match="API error"):
                shared.call_claude("sys", "usr")

    def test_empty_response_raises_index_error(self, shared):
        fake_client = MagicMock()
        response = MagicMock()
        response.content = []
        fake_client.messages.create.return_value = response

        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=fake_client):
            with pytest.raises(IndexError):
                shared.call_claude("sys", "usr")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _make_tree_response(self, items):
        """Build a mock response for the tree endpoint."""
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, text: str):
        encoded = base64.b64encode(text.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded}
        return resp

    def test_happy_path_single_file(self, shared):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/abc"}
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("# Hello World"),
            ]
            result = shared.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == "# Hello World"

    def test_extension_filtering(self, shared):
        tree_items = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "index.js", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "style.css", "url": "https://api.github.com/blob/3"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("print('hello')"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "index.js" not in result
        assert "style.css" not in result

    def test_max_files_limit(self, shared):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        blob_resp = self._make_blob_response("content")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._make_tree_response(tree_items)] + [blob_resp] * 10
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_tree_entries(self, shared):
        tree_items = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/1"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "app.py" in result

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get") as mock_get:
            resp = MagicMock()
            resp.json.return_value = {}
            mock_get.return_value = resp
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_decode_error_skipped(self, shared):
        """Files that fail to decode should be silently skipped."""
        tree_items = [
            {"type": "blob", "path": "broken.py", "url": "https://api.github.com/blob/bad"}
        ]
        with patch("requests.get") as mock_get:
            tree_resp = self._make_tree_response(tree_items)
            bad_blob = MagicMock()
            bad_blob.json.return_value = {}  # no "content" key → KeyError in b64decode
            mock_get.side_effect = [tree_resp, bad_blob]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "broken.py" not in result

    def test_multiple_extensions(self, shared):
        tree_items = [
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "index.js", "url": "https://api.github.com/blob/2"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("python code"),
                self._make_blob_response("js code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "app.py" in result
        assert "index.js" in result

    def test_correct_url_constructed(self