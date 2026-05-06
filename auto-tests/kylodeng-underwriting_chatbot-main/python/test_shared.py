"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Happy path, API error propagation, response text extraction
- clean_json(): Markdown fence stripping (```json, plain ```, no fences), edge cases
- get_repo_files(): Happy path with extension filtering, max_files limit, base64 decode,
                    decode errors (skipped gracefully), empty tree
- get_pr_diff(): Happy path, truncation at 30000 chars
- write_output_file(): Create new file (no SHA), update existing file (with SHA), fallback URL
- post_pr_comment(): Happy path, verifies correct URL/payload
- send_email(): 202 success, 200 success, non-200 warning (no exception raised)
- email_html(): SUCCESS status (green), FAILURE status (red), content presence
- write_audit_entry(): Covered indirectly via write_output_file mock (function body truncated
                       in source — see TODO)

Mocks used:
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for anthropic.Anthropic (client factory)
- os.environ patched via monkeypatch / patch.dict

TODOs:
- write_audit_entry() body is truncated in the provided source; tests are stubs pending full source
- MODEL constant ("claude-sonnet-4-6") — verify correct model string if it changes
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen BEFORE shared.py is imported, because the
# module reads env vars at import time.
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

with patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
    # Add the scripts directory to path so we can import shared
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
    # Also try direct path resolution relative to repo root
    _script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)
    import shared  # noqa: E402  (imported after env patch)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def _patch_module_env(monkeypatch):
    """Keep module-level constants aligned with test env for every test."""
    monkeypatch.setattr(shared, "ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setattr(shared, "GH_TOKEN", "test-gh-token")
    monkeypatch.setattr(shared, "SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(shared, "OUTPUT_REPO", "ai-delivery-outputs")
    monkeypatch.setattr(shared, "OUTPUT_REPO_OWNER", "test-owner")
    monkeypatch.setattr(shared, "NOTIFY_EMAIL", "notify@example.com")
    monkeypatch.setattr(shared, "SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(
        shared,
        "GH_HEADERS",
        {
            "Authorization": "Bearer test-gh-token",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _make_blob_url_response(content_str: str) -> dict:
    """Build a fake GitHub blob API response with base64-encoded content."""
    encoded = base64.b64encode(content_str.encode()).decode()
    return {"content": encoded + "\n"}  # GitHub adds a trailing newline


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:

    def _mock_client(self, text: str) -> MagicMock:
        """Return a mock anthropic.Anthropic() whose .messages.create() returns text."""
        content_block = MagicMock()
        content_block.text = text

        message = MagicMock()
        message.content = [content_block]

        client = MagicMock()
        client.messages.create.return_value = message
        return client

    def test_happy_path_returns_text(self):
        client = self._mock_client("Hello, world!")
        with patch("anthropic.Anthropic", return_value=client):
            result = shared.call_claude("sys prompt", "user prompt")
        assert result == "Hello, world!"

    def test_passes_correct_model(self):
        client = self._mock_client("ok")
        with patch("anthropic.Anthropic", return_value=client):
            shared.call_claude("sys", "usr")
        _, kwargs = client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self):
        client = self._mock_client("ok")
        with patch("anthropic.Anthropic", return_value=client):
            shared.call_claude("my system", "my user")
        _, kwargs = client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens(self):
        client = self._mock_client("ok")
        with patch("anthropic.Anthropic", return_value=client):
            shared.call_claude("s", "u")
        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self):
        client = self._mock_client("ok")
        with patch("anthropic.Anthropic", return_value=client):
            shared.call_claude("s", "u", max_tokens=1024)
        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_api_error_propagates(self):
        client = MagicMock()
        client.messages.create.side_effect = Exception("API failure")
        with patch("anthropic.Anthropic", return_value=client):
            with pytest.raises(Exception, match="API failure"):
                shared.call_claude("s", "u")

    def test_uses_api_key_from_module(self):
        client = self._mock_client("ok")
        with patch("anthropic.Anthropic", return_value=client) as mock_cls:
            shared.call_claude("s", "u")
        mock_cls.assert_called_once_with(api_key="test-anthropic-key")

    def test_multiline_response(self):
        text = "Line 1\nLine 2\nLine 3"
        client = self._mock_client(text)
        with patch("anthropic.Anthropic", return_value=client):
            result = shared.call_claude("s", "u")
        assert result == text


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:

    # ── Happy paths ──────────────────────────────────────────────────────────

    def test_no_fences_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_result_is_valid_json(self):
        raw = '```json\n{"model_name": "Underwriting Risk Classification"}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_strips_surrounding_whitespace(self):
        raw = '  \n  {"key": 1}  \n  '
        result = shared.clean_json(raw)
        assert result == '{"key": 1}'

    def test_fence_with_leading_and_trailing_whitespace(self):
        raw = '  ```json\n{"a": 1}\n```  '
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    # ── Edge cases ───────────────────────────────────────────────────────────

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_whitespace_only(self):
        assert shared.clean_json("   ") == ""

    def test_only_opening_fence(self):
        """No closing fence — should still strip the opening line."""
        raw = "```json\n{}"
        result = shared.clean_json(raw)
        assert result == "{}"

    def test_nested_json_preserved(self):
        inner = json.dumps({"global_feature_importance": {"Age": 34.576, "Education_Level": 2.098}})
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert json.loads(result)["global_feature_importance"]["Age"] == pytest.approx(34.576)

    def test_array_json(self):
        raw = '```json\n[1, 2, 3]\n```'
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_multiline_json_body_preserved(self):
        body = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{body}\n```"
        result = shared.clean_json(raw)
        assert json.loads(result) == {"a": 1, "b": 2}


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _tree_item(self, path: str, item_type: str = "blob", url: str = "https://api.github.com/blob/abc") -> dict:
        return {"type": item_type, "path": path, "url": url}

    @patch("shared.requests.get")
    def test_happy_path_returns_filtered_files(self, mock_get):
        tree = [
            self._tree_item("src/main.py"),
            self._tree_item("src/utils.py"),
            self._tree_item("README.md"),
        ]
        # First call: tree endpoint; subsequent calls: blob content
        mock_get.side_effect = [
            MagicMock(json=lambda: {"tree": tree}),
            MagicMock(json=lambda: _make_blob_url_response("print('hello')")),
            MagicMock(json=lambda: _make_blob_url_response("def util(): pass")),
        ]
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert set(result.keys()) == {"src/main.py", "src/utils.py"}
        assert "print('hello')" in result["src/main.py"]

    @patch("shared.requests.get")
    def test_extension_filtering(self, mock_get):
        tree = [
            self._tree_item("app.py"),
            self._tree_item("style.css"),
            self._tree_item("index.js"),
        ]
        mock_get.side_effect = [
            MagicMock(json=lambda: {"tree": tree}),
            MagicMock(json=lambda: _make_blob_url_response("# python")),
        ]
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "app.py" in result
        assert "style.css" not in result
        assert "index.js" not in result

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        blob_response = MagicMock(json=lambda: _make_blob_url_response("content"))
        mock_get.side_effect = [MagicMock(json=lambda: {"tree": tree})] + [blob_response] * 10
        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    @patch("shared.requests.get")
    def test_default_max_files_is_20(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(25)]
        blob_response = MagicMock(json=lambda: _make_blob_url_response("x"))
        mock_get.side_effect = [MagicMock(json=lambda: {"tree": tree})] + [blob_response] * 25
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert len(result) == 20

    @patch("shared.requests.get")
    def test_non_blob_items_skipped(self, mock_get):
        tree = [
            {"type": "tree", "path": "src/", "url": "https://api.github.com/tree/abc"},
            self._tree_item("src/app.py"),
        ]
        mock_get.side_effect = [
            MagicMock(json=lambda: {"tree": tree}),
            MagicMock(json=lambda: _make_blob_url_response("code")),
        ]
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/" not in result
        assert "src/app.py" in result

    @patch("shared.requests.get")
    def test_empty_tree(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"tree": []})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_missing_tree_key(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_decode_error_skipped_gracefully(self, mock_get):
        tree = [
            self._tree_item("good.py"),
            self._tree_item("bad.py"),
        ]
        good_blob = MagicMock(json=lambda: _make_blob_url_response("good content"))

        def bad_blob_json():
            return {"content": "!!!not-valid-base64!!!"}

        bad_blob = MagicMock(json=bad_blob_json)

        mock_get.side_effect = [
            MagicMock(json=lambda: {"tree": tree}),
            good_blob,
            bad_blob,
        ]
        # Should not raise; bad file simply absent
        result = shared.get_repo_files("owner", "repo", [".py"])