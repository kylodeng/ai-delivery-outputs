"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude: happy path, API response parsing, custom max_tokens
  - clean_json: markdown fence stripping, plain JSON passthrough, edge cases
  - get_repo_files: file fetching with extension filtering, max_files limit, base64 decode, decode errors
  - get_pr_diff: happy path, truncation at 30000 chars
  - write_output_file: create new file (no SHA), update existing file (with SHA), missing html_url fallback
  - post_pr_comment: correct URL and payload construction
  - send_email: success (200/202), warning on failure, correct payload shape
  - email_html: SUCCESS/FAILURE status colour, all fields present, timestamp format
  - write_audit_entry: (stub — source code truncated, cannot fully test)

Mocks used:
  - unittest.mock.patch for os.environ (environment variables)
  - unittest.mock.MagicMock / patch for anthropic.Anthropic client
  - unittest.mock.patch for requests.get, requests.post, requests.put
  - datetime.datetime patched for deterministic timestamps

TODOs:
  - TODO: write_audit_entry body is truncated in source; full tests need complete implementation
  - TODO: Integration test for full audit log round-trip requires real or mock GitHub repo state
"""

import base64
import datetime
import os
import sys
import types
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
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(autouse=True, scope="session")
def _patch_env_session():
    """Patch environment variables once for the whole session before import."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        yield


# Lazy import after env is set
@pytest.fixture(scope="session")
def shared(tmp_path_factory):
    """Import shared module once with env vars in place."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        # Remove cached module if any
        sys.modules.pop("shared", None)
        # Add script dir to path
        script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        # Also try relative path for CI
        alt_dir = os.path.join(os.path.dirname(__file__))
        if alt_dir not in sys.path:
            sys.path.insert(0, alt_dir)
        import importlib
        import importlib.util, pathlib

        # Locate shared.py robustly
        candidates = [
            pathlib.Path(".github/scripts/shared.py"),
            pathlib.Path("scripts/shared.py"),
            pathlib.Path("shared.py"),
        ]
        spec = None
        for candidate in candidates:
            if candidate.exists():
                spec = importlib.util.spec_from_file_location("shared", str(candidate))
                break

        if spec is None:
            pytest.skip("Cannot locate shared.py — adjust path in fixture")

        mod = importlib.util.module_from_spec(spec)
        sys.modules["shared"] = mod
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anthropic_response(text: str):
    """Build a minimal fake anthropic Messages response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def test_happy_path_returns_text(self, shared):
        fake_response = _make_anthropic_response("Hello from Claude")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == "Hello from Claude"

    def test_uses_correct_model(self, shared):
        fake_response = _make_anthropic_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        create_kwargs = mock_client.messages.create.call_args
        assert create_kwargs.kwargs["model"] == shared.MODEL

    def test_default_max_tokens(self, shared):
        fake_response = _make_anthropic_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        fake_response = _make_anthropic_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 1024

    def test_passes_system_and_user(self, shared):
        fake_response = _make_anthropic_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user message")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user message"}]

    def test_uses_api_key_from_env(self, shared):
        fake_response = _make_anthropic_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_constructor:
            shared.call_claude("s", "u")

        mock_constructor.assert_called_once_with(api_key="test-anthropic-key")

    def test_returns_first_content_block(self, shared):
        """Only the first content block text should be returned."""
        block1 = MagicMock()
        block1.text = "first"
        block2 = MagicMock()
        block2.text = "second"
        response = MagicMock()
        response.content = [block1, block2]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u")

        assert result == "first"

    def test_propagates_api_exception(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                shared.call_claude("s", "u")


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, shared):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self, shared):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_surrounding_whitespace(self, shared):
        raw = '   {"key": "value"}   '
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_fence_and_whitespace(self, shared):
        raw = '  ```json\n  {"a": 1}  \n```  '
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_multiline_json_preserved(self, shared):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f'```json\n{inner}\n```'
        result = shared.clean_json(raw)
        assert result == inner

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_no_closing_fence_returns_content(self, shared):
        """If the closing fence is absent the function should still strip the opening."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # Should at minimum not crash; content should be present
        assert "key" in result

    @pytest.mark.parametrize("raw,expected", [
        ('{"model_name": "Underwriting Risk Classification"}',
         '{"model_name": "Underwriting Risk Classification"}'),
        ('```json\n{"status": "ok"}\n```', '{"status": "ok"}'),
        ('```\n[1,2,3]\n```', '[1,2,3]'),
    ])
    def test_parametrised_samples(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _blob_response(self, text):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": _b64(text)}
        return mock_resp

    def test_happy_path_fetches_matching_files(self, shared):
        tree_items = [
            {"type": "blob", "path": "src/main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "src/util.py", "url": "https://api.github.com/blob/2"},
        ]
        blob_content = "print('hello')"

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree_items),
                self._blob_response(blob_content),
                self._blob_response(blob_content),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/util.py" in result
        assert result["src/main.py"] == blob_content

    def test_filters_by_extension(self, shared):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "style.css", "url": "https://api.github.com/blob/3"},
        ]

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree_items),
                self._blob_response("python code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "README.md" not in result
        assert "style.css" not in result

    def test_multiple_extensions(self, shared):
        tree_items = [
            {"type": "blob", "path": "main.py", "url": "u1"},
            {"type": "blob", "path": "config.json", "url": "u2"},
            {"type": "blob", "path": "notes.txt", "url": "u3"},
        ]

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree_items),
                self._blob_response("py"),
                self._blob_response("json"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "main.py" in result
        assert "config.json" in result
        assert "notes.txt" not in result

    def test_max_files_limit(self, shared):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"u{i}"}
            for i in range(10)
        ]

        side_effects = [self._tree_response(tree_items)] + [
            self._blob_response(f"content {i}") for i in range(3)
        ]

        with patch("requests.get") as mock_get:
            mock_get.side_effect = side_effects
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_trees_non_blobs(self, shared):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "u1"},
            {"type": "blob", "path": "app.py", "url": "u2"},
        ]

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree_items),
                self._blob_response("code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "app.py" in result

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_decode_error_skips_file(self, shared):
        tree_items = [
            {"type": "blob", "path": "binary.py", "url": "u1"},
        ]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {}  # missing 'content' key → raises KeyError

        with patch("requests.get") as mock_get:
            mock_get.side_effect =