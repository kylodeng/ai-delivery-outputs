"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude(): Claude API interaction and response parsing
  - clean_json(): markdown fence stripping from JSON strings
  - get_repo_files(): GitHub repo file fetching with extension filtering and limits
  - get_pr_diff(): GitHub PR diff fetching and truncation
  - write_output_file(): GitHub file create/update with SHA detection
  - post_pr_comment(): GitHub PR comment posting
  - send_email(): SendGrid email dispatch and error handling
  - email_html(): HTML email body generation
  - write_audit_entry(): Audit log entry construction (partial – source truncated)

Mocks used:
  - unittest.mock.patch for os.environ (env vars required at import time)
  - unittest.mock.MagicMock / patch for anthropic.Anthropic client
  - unittest.mock.patch for requests.get, requests.post, requests.put
  - datetime.datetime patched for deterministic timestamps

TODOs:
  - write_audit_entry(): source is truncated; full logic (JSON + Markdown append) cannot be tested without complete source
  - MODULE_LEVEL env var loading: ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY are read at import time; tests rely on patching os.environ before import
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ─── Helpers to (re)import shared with controlled env ────────────────────────

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


def import_shared(extra_env: dict = None):
    """Import (or re-import) shared with a controlled environment."""
    env = {**REQUIRED_ENV, **(extra_env or {})}
    # Remove cached module so module-level code re-runs
    sys.modules.pop("shared", None)
    with patch.dict("os.environ", env, clear=False):
        # Provide a minimal stub for anthropic so it doesn't need to be installed
        if "anthropic" not in sys.modules:
            anthropic_stub = types.ModuleType("anthropic")
            anthropic_stub.Anthropic = MagicMock()
            sys.modules["anthropic"] = anthropic_stub
        import shared as _shared
    return _shared


@pytest.fixture(scope="module")
def shared():
    """Module-level import of shared with mocked env and anthropic stub."""
    anthropic_stub = types.ModuleType("anthropic")
    anthropic_stub.Anthropic = MagicMock()
    sys.modules.pop("anthropic", None)
    sys.modules["anthropic"] = anthropic_stub
    sys.modules.pop("shared", None)
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        import shared as _shared
    return _shared


# ─── clean_json ──────────────────────────────────────────────────────────────

class TestCleanJson:
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

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = "   \n{\"a\": 1}\n   "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_whitespace_only(self, shared):
        assert shared.clean_json("   ") == ""

    def test_nested_backticks_in_content_preserved(self, shared):
        raw = "```json\n{\"code\": \"x = `y`\"}\n```"
        result = shared.clean_json(raw)
        assert '"code"' in result

    def test_multiline_json_with_fence(self, shared):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_no_closing_fence_does_not_crash(self, shared):
        # If there's no closing ``` the rsplit leaves content intact minus opening
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        assert '{"key": "value"}' in result

    def test_array_json(self, shared):
        raw = "```json\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert json.loads(result) == [1, 2, 3]


# ─── call_claude ─────────────────────────────────────────────────────────────

class TestCallClaude:
    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_happy_path_returns_text(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("Hello world")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module = import_shared()
            result = shared_module.call_claude("system prompt", "user prompt")
        assert result == "Hello world"

    def test_passes_correct_model_and_tokens(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module = import_shared()
            shared_module.call_claude("sys", "usr", max_tokens=1024)
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs["model"] == shared_module.MODEL
            assert kwargs["max_tokens"] == 1024

    def test_passes_system_and_user_messages(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module = import_shared()
            shared_module.call_claude("my system", "my user")
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs["system"] == "my system"
            assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens_is_4096(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module = import_shared()
            shared_module.call_claude("s", "u")
            _, kwargs = mock_client.messages.create.call_args
            assert kwargs["max_tokens"] == 4096

    def test_uses_anthropic_api_key_from_env(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared_module = import_shared()
            shared_module.call_claude("s", "u")
            mock_cls.assert_called_once_with(api_key="test-anthropic-key")

    def test_returns_first_content_block_text(self, shared):
        block1 = MagicMock()
        block1.text = "first"
        block2 = MagicMock()
        block2.text = "second"
        response = MagicMock()
        response.content = [block1, block2]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module = import_shared()
            result = shared_module.call_claude("s", "u")
        assert result == "first"

    def test_api_exception_propagates(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module = import_shared()
            with pytest.raises(RuntimeError, match="API down"):
                shared_module.call_claude("s", "u")


# ─── get_repo_files ───────────────────────────────────────────────────────────

class TestGetRepoFiles:
    def _make_tree_response(self, paths: list[str]):
        """Build a mock requests.get response for the tree endpoint."""
        tree = [{"type": "blob", "path": p, "url": f"https://api.github.com/blob/{p}"} for p in paths]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": tree}
        return tree_resp

    def _make_file_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded}
        return resp

    def test_returns_matching_files(self, shared):
        tree_resp = self._make_tree_response(["file.py", "readme.md", "script.js"])
        file_resp = self._make_file_response("print('hello')")

        def side_effect(url, headers):
            if "trees" in url:
                return tree_resp
            return file_resp

        with patch("requests.get", side_effect=side_effect):
            shared_module = import_shared()
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "file.py" in result
        assert "readme.md" not in result
        assert "script.js" not in result

    def test_respects_max_files_limit(self, shared):
        paths = [f"file{i}.py" for i in range(10)]
        tree_resp = self._make_tree_response(paths)
        file_resp = self._make_file_response("content")

        def side_effect(url, headers):
            if "trees" in url:
                return tree_resp
            return file_resp

        with patch("requests.get", side_effect=side_effect):
            shared_module = import_shared()
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    def test_multiple_extensions(self, shared):
        tree_resp = self._make_tree_response(["a.py", "b.js", "c.txt"])
        file_resp = self._make_file_response("data")

        def side_effect(url, headers):
            if "trees" in url:
                return tree_resp
            return file_resp

        with patch("requests.get", side_effect=side_effect):
            shared_module = import_shared()
            result = shared_module.get_repo_files("owner", "repo", [".py", ".js"])
        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    def test_empty_tree_returns_empty_dict(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp):
            shared_module = import_shared()
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "dir.py", "url": "https://x"},
            {"type": "blob", "path": "file.py", "url": "https://y"},
        ]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": tree}
        file_resp = self._make_file_response("code")

        def side_effect(url, headers):
            if "trees" in url:
                return tree_resp
            return file_resp

        with patch("requests.get", side_effect=side_effect):
            shared_module = import_shared()
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "file.py" in result
        assert "dir.py" not in result

    def test_silently_skips_files_with_bad_content(self, shared):
        """Files that can't be base64-decoded are skipped without raising."""
        tree_resp = self._make_tree_response(["bad.py", "good.py"])

        bad_resp = MagicMock()
        bad_resp.json.return_value = {"content": "!!!not-base64!!!"}
        good_resp = self._make_file_response("good content")

        call_count = {"n": 0}

        def side_effect(url, headers):
            if "trees" in url:
                return tree_resp
            call_count["n"] += 1
            if call_count["n"] == 1:
                return bad_resp
            return good_resp

        with patch("requests.get", side_effect=side_effect):
            shared_module = import_shared()
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        # good.py should still appear
        assert "good.py" in result

    def test_missing_tree_key_handled(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {}  # no "tree" key

        with patch("requests.get", return_value=tree_resp):
            shared_module = import_shared()
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_correct_url_constructed(self, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared_module = import_shared()
            shared_module.get_repo_files("myowner", "myrepo", [".py"])
            args, _ = mock_get.call_args
            assert "myowner/myrepo" in args[0]
            assert "recursive=1" in args[0]

    def test_file_content_decoded_correctly(self, shared):
        tree_resp = self._make_tree_response(["hello.py"])
        file_resp = self._make_file_response("print('hello world')")

        def side_effect(url, headers