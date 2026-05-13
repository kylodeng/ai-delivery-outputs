"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation and text extraction
- clean_json(): Markdown fence stripping from JSON strings
- get_repo_files(): GitHub API tree traversal + base64 decode with extension filtering
- get_pr_diff(): PR unified diff fetch with correct Accept header and truncation
- write_output_file(): Create/update file in output repo (with/without existing SHA)
- post_pr_comment(): PR comment posting
- send_email(): SendGrid email dispatch, success and failure paths
- email_html(): HTML email generation (status colours, content presence)
- write_audit_entry(): Audit log append (JSON + Markdown) via write_output_file

Mocks used:
- unittest.mock.patch / MagicMock for:
    - anthropic.Anthropic (Claude client)
    - requests.get / requests.post / requests.put
    - shared.write_output_file (inside write_audit_entry tests)
- os.environ patched at import time via monkeypatch / patch.dict

TODOs:
- TODO: Full integration test for write_audit_entry verifying exact JSON/MD content
  written to GitHub — needs a real or fully-faked repo fixture.
- TODO: Test MODEL constant matches a valid Anthropic model slug (needs Anthropic
  model-list endpoint or maintained allowlist).
- TODO: Verify email HTML is well-formed XML/HTML (needs lxml or html.parser fixture).
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal environment so the module-level os.environ[] lookups don't raise
# ---------------------------------------------------------------------------
FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(scope="session", autouse=True)
def _patch_env_for_import():
    """Patch environment before shared.py is imported so module-level lookups succeed."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        # Remove cached module if already loaded without the env vars
        sys.modules.pop("shared", None)
        # Make sure 'anthropic' stub exists so import doesn't fail in CI
        if "anthropic" not in sys.modules:
            stub = types.ModuleType("anthropic")
            stub.Anthropic = MagicMock()
            sys.modules["anthropic"] = stub
        yield


@pytest.fixture()
def shared_module():
    """Re-import shared with the fake environment active."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        sys.modules.pop("shared", None)
        import shared as _shared
        return _shared


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

    def test_strips_plain_backtick_fence(self, shared_module):
        raw = "```\n{\"a\": 1}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self, shared_module):
        raw = "  \n  ```json\n{}\n```  \n  "
        result = shared_module.clean_json(raw)
        assert result == "{}"

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_nested_json_preserved(self, shared_module):
        inner = '{"a": {"b": [1, 2, 3]}}'
        fenced = f"```json\n{inner}\n```"
        assert shared_module.clean_json(fenced) == inner

    def test_valid_json_after_cleaning(self, shared_module):
        raw = "```json\n{\"product_name\": \"Generations II\"}\n```"
        cleaned = shared_module.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["product_name"] == "Generations II"

    def test_multiline_json_preserved(self, shared_module):
        raw = '```json\n{\n  "key": "value",\n  "num": 42\n}\n```'
        cleaned = shared_module.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["num"] == 42

    def test_no_closing_fence(self, shared_module):
        """If there is no closing fence, rsplit returns the whole remaining string."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_module.clean_json(raw)
        # Should still strip the opening line; no closing fence means full content kept
        assert "key" in result


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

    def test_happy_path(self, shared_module):
        mock_response = self._make_response("Hello from Claude")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared_module.call_claude("system prompt", "user message")

        assert result == "Hello from Claude"
        mock_client.messages.create.assert_called_once_with(
            model=shared_module.MODEL,
            max_tokens=4096,
            system="system prompt",
            messages=[{"role": "user", "content": "user message"}],
        )

    def test_custom_max_tokens(self, shared_module):
        mock_response = self._make_response("short")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module.call_claude("sys", "usr", max_tokens=512)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 512

    def test_returns_first_content_block(self, shared_module):
        """Only the first content block's text should be returned."""
        second_block = MagicMock()
        second_block.text = "ignored"
        first_block = MagicMock()
        first_block.text = "correct"
        response = MagicMock()
        response.content = [first_block, second_block]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared_module.call_claude("s", "u")

        assert result == "correct"

    def test_api_exception_propagates(self, shared_module):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API error"):
                shared_module.call_claude("s", "u")

    def test_uses_configured_api_key(self, shared_module):
        mock_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared_module.call_claude("s", "u")
            mock_cls.assert_called_once_with(api_key=shared_module.ANTHROPIC_API_KEY)


# ===========================================================================
# get_repo_files
# ===========================================================================
class TestGetRepoFiles:
    def _make_tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded}
        return resp

    def test_happy_path_single_file(self, shared_module):
        tree_items = [{"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/abc"}]
        tree_resp = self._make_tree_response(tree_items)
        blob_resp = self._make_blob_response("# Hello")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared_module.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == "# Hello"

    def test_extension_filter(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "file.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "file.js", "url": "https://api.github.com/blob/2"},
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob_resp = self._make_blob_response("print('hello')")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "file.py" in result
        assert "file.js" not in result

    def test_max_files_limit(self, shared_module):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(5)
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob_resps = [self._make_blob_response(f"content{i}") for i in range(2)]

        with patch("requests.get", side_effect=[tree_resp] + blob_resps):
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=2)

        assert len(result) == 2

    def test_skips_non_blob_items(self, shared_module):
        tree_items = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/2"},
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob_resp = self._make_blob_response("code")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_empty_tree(self, shared_module):
        tree_resp = self._make_tree_response([])
        with patch("requests.get", return_value=tree_resp):
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_blob_decode_error_skipped(self, shared_module):
        """If base64 decode fails, the file should be silently skipped."""
        tree_items = [{"type": "blob", "path": "bad.py", "url": "https://x"}]
        tree_resp = self._make_tree_response(tree_items)
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": None}  # will raise during decode

        with patch("requests.get", side_effect=[tree_resp, bad_blob]):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self, shared_module):
        tree_items = [
            {"type": "blob", "path": "a.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "b.json", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "c.txt", "url": "https://api.github.com/blob/3"},
        ]
        tree_resp = self._make_tree_response(tree_items)
        blob_py = self._make_blob_response("py content")
        blob_json = self._make_blob_response('{"key": "value"}')

        with patch("requests.get", side_effect=[tree_resp, blob_py, blob_json]):
            result = shared_module.get_repo_files("owner", "repo", [".py", ".json"])

        assert "a.py" in result
        assert "b.json" in result
        assert "c.txt" not in result


# ===========================================================================
# get_pr_diff
# ===========================================================================
class TestGetPrDiff:
    def test_happy_path(self, shared_module):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/file.py b/file.py\n+new line"

        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = shared_module.get_pr_diff("owner", "repo", 42)

        assert result == "diff --git a/file.py b/file.py\n+new line"
        called_url = mock_get.call_args[0][0]
        assert "/repos/owner/repo/pulls/42" in called_url

    def test_uses_diff_accept_header(self, shared_module):
        mock_resp = MagicMock()
        mock_resp.text = "diff content"

        with patch("requests.get", return_value=mock_resp) as mock_get:
            shared_module.get_pr_diff("owner", "repo", 1)

        headers = mock_get.call_args[1]["headers"]
        assert headers["Accept"] == "application/vnd.github.diff"

    def test_truncates_at_30000_chars(self, shared_module):
        long_diff = "x" * 40000
        mock_resp = MagicMock()
        mock_resp.text = long_diff

        with patch("requests.get", return_value=mock_resp):
            result = shared_module.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_short_diff_not_truncated(self, shared_module):
        short_diff = "short diff content"
        mock_resp = MagicMock()
        mock_resp.text = short_diff

        with patch("requests.get", return_value=mock_resp):
            result =