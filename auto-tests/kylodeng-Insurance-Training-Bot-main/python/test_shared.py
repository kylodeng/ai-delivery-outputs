"""
Test module for .github/scripts/shared.py

What is tested:
    - call_claude(): Claude API integration, response extraction
    - clean_json(): Markdown fence stripping from JSON strings
    - get_repo_files(): GitHub API tree traversal, extension filtering, base64 decoding
    - get_pr_diff(): GitHub API PR diff fetching, truncation
    - write_output_file(): GitHub file create/update via PUT, SHA handling
    - post_pr_comment(): GitHub PR comment posting
    - send_email(): SendGrid email dispatch, failure warning
    - email_html(): HTML template generation
    - write_audit_entry(): Audit log JSON/Markdown writing (via write_output_file)

Mocks used:
    - unittest.mock.patch / MagicMock for:
        - anthropic.Anthropic (Claude client)
        - requests.get, requests.post, requests.put
        - os.environ (environment variables injected via monkeypatch)
        - datetime.datetime (for deterministic timestamps)
        - builtins.print (to capture warnings)

TODOs:
    - TODO: write_audit_entry full integration — requires inspecting the complete
            source of write_audit_entry (source was truncated); stub tests included.
    - TODO: Test MODEL constant value once the model name is confirmed stable.
    - TODO: Parameterised tests against real insurance PDF annotation synthetic data
            require Claude to be called; skipped to avoid live API calls.
"""

import base64
import json
import sys
import types
import importlib
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen before shared.py is imported
# ---------------------------------------------------------------------------

_ENV_DEFAULTS = {
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
def _patch_env_for_import():
    """Ensure all required env vars are present before shared.py is imported."""
    with patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
        yield


# ---------------------------------------------------------------------------
# Lazy import helper — import shared only after env is patched
# ---------------------------------------------------------------------------

def _import_shared():
    """Import (or re-use cached) shared module with env vars patched."""
    if "shared" in sys.modules:
        return sys.modules["shared"]
    # Add the script directory to path
    import sys as _sys
    import os as _os
    script_dir = _os.path.join(_os.path.dirname(__file__), "..", "..", ".github", "scripts")
    script_dir = _os.path.abspath(script_dir)
    if script_dir not in _sys.path:
        _sys.path.insert(0, script_dir)
    with patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
        import shared  # noqa: PLC0415
    return shared


@pytest.fixture(scope="session")
def shared():
    return _import_shared()


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for the clean_json() utility."""

    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self, shared):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self, shared):
        raw = "  \n  {\"key\": \"value\"}  \n  "
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_whitespace_only_string(self, shared):
        assert shared.clean_json("   ") == ""

    def test_code_fence_with_extra_whitespace(self, shared):
        raw = "  ```json\n{\"a\": 1}\n```  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_no_closing_fence(self, shared):
        """If there is no closing fence, rsplit returns the whole string."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # rsplit on ``` not present → returns original after opening strip
        assert "key" in result

    def test_multiline_json_in_fence(self, shared):
        inner = '{\n  "tool": "review",\n  "status": "SUCCESS"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["tool"] == "review"
        assert parsed["status"] == "SUCCESS"

    def test_array_json_in_fence(self, shared):
        raw = "```json\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert json.loads(result) == [1, 2, 3]

    def test_already_stripped_json(self, shared):
        raw = '{"product_name": "Generations II", "doc_type": "product_brochure"}'
        assert shared.clean_json(raw) == raw

    @pytest.mark.parametrize("fence_lang", ["json", "python", "yaml", ""])
    def test_various_fence_languages_stripped(self, shared, fence_lang):
        raw = f"```{fence_lang}\n{{\"k\": \"v\"}}\n```"
        result = shared.clean_json(raw)
        assert result == '{"k": "v"}'


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for the call_claude() function."""

    def _make_mock_client(self, text_response: str):
        mock_content = MagicMock()
        mock_content.text = text_response
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_happy_path_returns_text(self, shared):
        mock_client = self._make_mock_client("Hello from Claude!")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user message")
        assert result == "Hello from Claude!"

    def test_passes_correct_model(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self, shared):
        mock_client = self._make_mock_client("ok")
        with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
            shared.call_claude("sys", "usr")
        mock_anthropic.assert_called_once_with(api_key="test-anthropic-key")

    def test_json_response_in_fence_can_be_cleaned(self, shared):
        raw = '```json\n{"result": "ok"}\n```'
        mock_client = self._make_mock_client(raw)
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys", "usr")
        cleaned = shared.clean_json(result)
        assert json.loads(cleaned)["result"] == "ok"

    def test_anthropic_exception_propagates(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("sys", "usr")

    def test_empty_system_prompt(self, shared):
        mock_client = self._make_mock_client("response")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("", "user message")
        assert result == "response"

    def test_large_user_message(self, shared):
        large_msg = "x" * 10000
        mock_client = self._make_mock_client("done")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys", large_msg)
        assert result == "done"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for the get_repo_files() function."""

    def _make_tree_response(self, items):
        mock = MagicMock()
        mock.json.return_value = {"tree": items}
        return mock

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        mock = MagicMock()
        mock.json.return_value = {"content": encoded + "\n"}  # GitHub adds newlines
        return mock

    def test_happy_path_fetches_files(self, shared):
        tree_item = {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/abc"}
        tree_resp = self._make_tree_response([tree_item])
        blob_resp = self._make_blob_response("# Hello World")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == "# Hello World"

    def test_filters_by_extension(self, shared):
        items = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "notes.md", "url": "https://api.github.com/blob/2"},
        ]
        tree_resp = self._make_tree_response(items)
        blob_resp = self._make_blob_response("print('hello')")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "notes.md" not in result

    def test_multiple_extensions(self, shared):
        items = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "app.js", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "README.txt", "url": "https://api.github.com/blob/3"},
        ]
        tree_resp = self._make_tree_response(items)
        py_resp = self._make_blob_response("# python")
        js_resp = self._make_blob_response("// js")

        with patch("requests.get", side_effect=[tree_resp, py_resp, js_resp]):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "main.py" in result
        assert "app.js" in result
        assert "README.txt" not in result

    def test_respects_max_files_limit(self, shared):
        items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        tree_resp = self._make_tree_response(items)
        blob_responses = [self._make_blob_response(f"content {i}") for i in range(3)]

        with patch("requests.get", side_effect=[tree_resp] + blob_responses):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        items = [
            {"type": "tree", "path": "src/", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/2"},
        ]
        tree_resp = self._make_tree_response(items)
        blob_resp = self._make_blob_response("code")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert len(result) == 1
        assert "main.py" in result

    def test_empty_tree(self, shared):
        tree_resp = self._make_tree_response([])

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions(self, shared):
        items = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/1"},
        ]
        tree_resp = self._make_tree_response(items)

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_handles_blob_decode_exception_gracefully(self, shared):
        items = [
            {"type": "blob", "path": "bad.py", "url": "https://api.github