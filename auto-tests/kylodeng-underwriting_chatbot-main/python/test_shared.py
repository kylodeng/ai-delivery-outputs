"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree traversal, base64 decoding, extension filtering, max_files limit
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): file creation (no SHA) and update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure status codes
- email_html(): HTML generation, status colour logic
- write_audit_entry(): audit log construction (stub – requires full source)

Mocks used:
- unittest.mock.patch / MagicMock for:
    - anthropic.Anthropic (Claude client)
    - requests.get / requests.post / requests.put
- os.environ patched via monkeypatch / patch.dict

TODOs:
- write_audit_entry() full body is truncated in source; stub tests added with skip markers
- MODEL constant value assumed as "claude-sonnet-4-6"; test will break if changed
"""

import base64
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

with patch.dict(os.environ, _ENV, clear=False):
    # Make sure the scripts directory is importable
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
    # Try both relative locations depending on where pytest is invoked from
    import importlib.util, pathlib

    _candidate = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py"
    if not _candidate.exists():
        _candidate = pathlib.Path(__file__).parent / ".github" / "scripts" / "shared.py"

    _spec = importlib.util.spec_from_file_location("shared", str(_candidate))
    shared = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(shared)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_response(status_code: int = 200, json_data: dict = None, text: str = ""):
    """Return a mock requests.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


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
        raw = "   {\"key\": \"value\"}   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_fence_with_surrounding_whitespace(self):
        raw = "  ```json\n[1, 2, 3]\n```  "
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_backticks(self):
        raw = "```\n```"
        result = shared.clean_json(raw)
        # Should not raise; inner content may be empty
        assert isinstance(result, str)

    def test_multiline_json_inside_fence(self):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_no_closing_fence(self):
        """If opening fence present but no closing, rsplit should still return something."""
        raw = "```json\n{\"x\": 1}"
        result = shared.clean_json(raw)
        assert isinstance(result, str)

    def test_model_card_json_passthrough(self):
        """Synthetic data: plain JSON from model_card.json should pass through clean."""
        raw = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        assert shared.clean_json(raw) == raw


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_client_mock(self, text: str):
        """Build a mock anthropic.Anthropic client that returns `text`."""
        content_block = MagicMock()
        content_block.text = text

        message = MagicMock()
        message.content = [content_block]

        client = MagicMock()
        client.messages.create.return_value = message
        return client

    @patch("anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("Hello from Claude")
        mock_anthropic_cls.return_value = client_mock

        result = shared.call_claude("system prompt", "user prompt")

        assert result == "Hello from Claude"

    @patch("anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("anthropic.Anthropic")
    def test_messages_structure(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("my system", "my user")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    @patch("anthropic.Anthropic")
    def test_api_key_passed_to_client(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("sys", "usr")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    @patch("anthropic.Anthropic")
    def test_empty_system_and_user(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("")
        mock_anthropic_cls.return_value = client_mock

        result = shared.call_claude("", "")
        assert result == ""

    @patch("anthropic.Anthropic")
    def test_propagates_api_exception(self, mock_anthropic_cls):
        client_mock = MagicMock()
        client_mock.messages.create.side_effect = RuntimeError("API down")
        mock_anthropic_cls.return_value = client_mock

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude("sys", "usr")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return _make_response(json_data={"content": encoded})

    @patch("requests.get")
    def test_happy_path_single_file(self, mock_get):
        tree = [{"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/1"}]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response("# Hello"),
        ]

        result = shared.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == "# Hello"

    @patch("requests.get")
    def test_filters_by_extension(self, mock_get):
        tree = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "style.css", "url": "https://api.github.com/blob/2"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response("print('hello')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "style.css" not in result

    @patch("requests.get")
    def test_skips_tree_items(self, mock_get):
        tree = [
            {"type": "tree", "path": "somedir", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "file.py", "url": "https://api.github.com/blob/2"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response("code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "somedir" not in result
        assert "file.py" in result

    @patch("requests.get")
    def test_max_files_limit(self, mock_get):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        blob_responses = [self._blob_response(f"content{i}") for i in range(3)]
        mock_get.side_effect = [self._tree_response(tree)] + blob_responses

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = [
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "index.js", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "style.css", "url": "https://api.github.com/blob/3"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response("python code"),
            self._blob_response("js code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "app.py" in result
        assert "index.js" in result
        assert "style.css" not in result

    @patch("requests.get")
    def test_empty_tree(self, mock_get):
        mock_get.return_value = self._tree_response([])

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    @patch("requests.get")
    def test_handles_base64_decode_error_gracefully(self, mock_get):
        tree = [{"type": "blob", "path": "file.py", "url": "https://api.github.com/blob/1"}]
        bad_blob = _make_response(json_data={"content": "!!!NOT_VALID_BASE64!!!"})
        mock_get.side_effect = [self._tree_response(tree), bad_blob]

        # Should not raise; file simply skipped
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert isinstance(result, dict)

    @patch("requests.get")
    def test_constructs_correct_tree_url(self, mock_get):
        mock_get.return_value = self._tree_response([])

        shared.get_repo_files("my-owner", "my-repo", [".py"])

        call_url = mock_get.call_args_list[0][0][0]
        assert "my-owner" in call_url
        assert "my-repo" in call_url
        assert "recursive=1" in call_url

    @patch("requests.get")
    def test_arabic_translation_file_utf8(self, mock_get):
        """Synthetic data: Arabic JSON file should decode correctly."""
        content = '{"common": {"actions": {"cancel": "إلغاء"}}}'
        tree = [{"type": "blob", "path": "ar-SA.json", "url": "https://api.github.com/blob/1"}]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response(content),
        ]

        result = shared.get_repo_files("owner", "repo", [".json"])

        assert "ar-SA.json" in result
        assert "إلغاء" in result["ar-SA.json"]


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    @patch("requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_get.return_value = _make_response(text="diff --git a/file.py b/file.py\n+new line")

        result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result

    @patch("requests.get")
    def test_truncates