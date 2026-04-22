"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude: Claude API invocation, response extraction
- clean_json: markdown fence stripping, edge cases
- get_repo_files: GitHub tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff: PR diff fetching, truncation behaviour
- write_output_file: file creation (no SHA), file update (with SHA), URL fallback
- post_pr_comment: PR comment posting
- send_email: SendGrid payload construction, success/failure status codes
- email_html: HTML output content, status colour logic
- write_audit_entry: (stub — source truncated, cannot fully test)

Mocks used:
- unittest.mock.patch for os.environ (env vars)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- No real network calls, no real API keys

TODOs:
- write_audit_entry: source code is truncated; stub tests provided
- Integration tests requiring real GitHub/SendGrid/Anthropic credentials
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to bootstrap the module with fake env vars
# (The module executes os.environ[] at import time, so we must patch before
#  importing.)
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "fake-anthropic-key",
    "GH_TOKEN": "fake-gh-token",
    "SENDGRID_API_KEY": "fake-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


def import_shared():
    """Import (or re-import) shared with fake environment variables."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        # Remove cached module so env vars are re-evaluated
        sys.modules.pop("shared", None)
        # Make sure the scripts directory is on the path
        import importlib.util, pathlib
        spec_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py"
        if not spec_path.exists():
            # Fallback: assume tests run from repo root
            spec_path = pathlib.Path(".github/scripts/shared.py")
        spec = importlib.util.spec_from_file_location("shared", spec_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["shared"] = mod
        spec.loader.exec_module(mod)
        return mod


@pytest.fixture(scope="module")
def shared():
    """Module-scoped fixture: import shared once with fake env."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        sys.modules.pop("shared", None)
        import importlib.util, pathlib
        spec_path = pathlib.Path(".github/scripts/shared.py")
        spec = importlib.util.spec_from_file_location("shared", spec_path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["shared"] = mod
        spec.loader.exec_module(mod)
        return mod


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:

    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_triple_backtick_fence(self, shared):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_surrounding_whitespace(self, shared):
        raw = "   \n{\"x\": 99}\n   "
        result = shared.clean_json(raw)
        assert result == '{"x": 99}'

    def test_strips_fence_and_whitespace(self, shared):
        raw = "  ```json\n  {\"k\": \"v\"}\n  ```  "
        result = shared.clean_json(raw)
        assert result == '{"k": "v"}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_json_array(self, shared):
        raw = "```json\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_no_closing_fence(self, shared):
        """If there is an opening fence but no closing, rsplit still works gracefully."""
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        # rsplit on ``` that doesn't exist returns the original string unchanged
        assert '{"a": 1}' in result

    def test_nested_backticks_in_content(self, shared):
        """Content containing single backticks should be preserved."""
        raw = '{"code": "`hello`"}'
        assert shared.clean_json(raw) == '{"code": "`hello`"}'

    def test_multiline_json(self, shared):
        raw = "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:

    def _make_mock_response(self, text: str):
        mock_content = MagicMock()
        mock_content.text = text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        return mock_response

    def test_happy_path_returns_text(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("Hello!")
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys prompt", "user prompt")
        assert result == "Hello!"

    def test_passes_correct_model(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_mock_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("sys", "usr")
        mock_cls.assert_called_once_with(api_key="fake-anthropic-key")

    def test_returns_first_content_block(self, shared):
        """Only the first content block's text should be returned."""
        content_0 = MagicMock()
        content_0.text = "first"
        content_1 = MagicMock()
        content_1.text = "second"
        mock_response = MagicMock()
        mock_response.content = [content_0, content_1]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u")
        assert result == "first"

    def test_api_exception_propagates(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                shared.call_claude("s", "u")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content_str: str):
        encoded = base64.b64encode(content_str.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_happy_path_fetches_matching_files(self, shared):
        tree = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/main"},
            {"type": "blob", "path": "src/util.js", "url": "http://blob/util"},
        ]
        blob_py = self._make_blob_response("print('hello')")

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._make_tree_response(tree)
            return blob_py

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/util.js" not in result
        assert result["src/main.py"] == "print('hello')"

    def test_respects_max_files_limit(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob = self._make_blob_response("content")

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._make_tree_response(tree)
            return blob

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "src/", "url": "http://tree/src"},
            {"type": "blob", "path": "src/app.py", "url": "http://blob/app"},
        ]
        blob = self._make_blob_response("# app")

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._make_tree_response(tree)
            return blob

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "src/app.py" in result

    def test_multiple_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "a.py", "url": "http://blob/a"},
            {"type": "blob", "path": "b.js", "url": "http://blob/b"},
            {"type": "blob", "path": "c.md", "url": "http://blob/c"},
        ]
        blob = self._make_blob_response("data")

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._make_tree_response(tree)
            return blob

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.md" not in result

    def test_empty_tree_returns_empty_dict(self, shared):
        def fake_get(url, headers=None):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"tree": []}
            return mock_resp

        with patch("requests.get", side_effect=fake_get):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_handles_blob_decode_exception_gracefully(self, shared):
        tree = [
            {"type": "blob", "path": "broken.py", "url": "http://blob/broken"},
            {"type": "blob", "path": "good.py", "url": "http://blob/good"},
        ]

        good_blob = self._make_blob_response("good content")
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "!!!NOT_BASE64!!!"}

        call_count = {"n": 0}

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._make_tree_response(tree)
            call_count["n"] += 1
            if call_count["n"] == 1:
                return bad_blob
            return good_blob

        with patch("requests.get", side_effect=fake_get):
            # Should not raise
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Only the good file should appear (bad one skipped silently)
        assert "good.py" in result

    def test_missing_content_key_silently_skipped(self, shared):
        tree = [{"type": "blob", "path": "no_content.py", "url": "http://blob/x"}]

        def fake_get(url, headers=None):
            if "trees" in url:
                return self._make_tree_response(tree)
            mock_resp = MagicMock()
            mock