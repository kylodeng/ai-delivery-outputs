"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude(): Claude API integration
  - clean_json(): markdown code fence stripping
  - get_repo_files(): GitHub repo file fetching with extension filtering
  - get_pr_diff(): GitHub PR diff fetching
  - write_output_file(): GitHub file creation/update
  - post_pr_comment(): GitHub PR comment posting
  - send_email(): SendGrid email sending
  - email_html(): HTML email body generation
  - write_audit_entry(): Audit log writing (partial - see TODOs)

Mocks used:
  - unittest.mock.patch for os.environ (to satisfy module-level env var reads)
  - unittest.mock.MagicMock / patch for anthropic.Anthropic client
  - unittest.mock.patch for requests.get, requests.post, requests.put
  - base64 decoding verified inline

TODOs:
  - write_audit_entry(): source code is truncated; full behaviour not testable
  - MODEL constant ("claude-sonnet-4-6") not verified at runtime
  - GH_HEADERS construction depends on GH_TOKEN env var at import time
"""

import base64
import datetime
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Patch environment variables BEFORE importing shared, because the module
# reads them at import time (module-level assignments).
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
def _patch_env_for_import():
    """Patch env vars before the module is first imported."""
    with patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        # Ensure a fresh import with the patched env
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Also remove from any cached path entries
        for key in list(sys.modules.keys()):
            if "shared" in key:
                del sys.modules[key]
        yield


# We import shared *after* patching env so the module-level os.environ reads succeed.
with patch.dict("os.environ", ENV_DEFAULTS, clear=False):
    import importlib
    import sys as _sys
    # Make the script importable from its unusual path
    import os as _os
    _scripts_dir = _os.path.join(_os.path.dirname(__file__), ".github", "scripts")
    if _scripts_dir not in _sys.path:
        _sys.path.insert(0, _scripts_dir)
    # Stub out anthropic if not installed
    if "anthropic" not in _sys.modules:
        _anthropic_stub = types.ModuleType("anthropic")
        _anthropic_stub.Anthropic = MagicMock()
        _sys.modules["anthropic"] = _anthropic_stub
    import shared  # noqa: E402


# ===========================================================================
# Helpers
# ===========================================================================

def _make_response(status_code=200, json_data=None, text=""):
    """Build a mock requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data if json_data is not None else {}
    mock_resp.text = text
    return mock_resp


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_no_fences_unchanged(self):
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
        raw = "   ```json\n{\"a\":1}\n```   "
        result = shared.clean_json(raw)
        assert result == '{"a":1}'

    def test_multiline_json_inside_fence(self):
        raw = '```json\n{\n  "key": "value",\n  "num": 42\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["num"] == 42

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_empty_fence(self):
        raw = "```\n\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_fence_without_closing(self):
        """If there is no closing fence, rsplit returns the original content."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # split on \n once drops the opening line; rsplit on ``` with no closing
        # returns the whole remainder unchanged
        assert '{"key": "value"}' in result

    def test_non_json_plain_text_unchanged(self):
        raw = "just some plain text"
        assert shared.clean_json(raw) == "just some plain text"

    @pytest.mark.parametrize("fence_type", ["```json", "```python", "```"])
    def test_various_fence_types(self, fence_type):
        raw = f"{fence_type}\ncontent\n```"
        result = shared.clean_json(raw)
        assert result == "content"


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_claude_client(self):
        client = MagicMock()
        message = MagicMock()
        message.content = [MagicMock(text="Claude response text")]
        client.messages.create.return_value = message
        return client

    def test_happy_path_returns_text(self):
        client = self._make_claude_client()
        with patch("shared.anthropic.Anthropic", return_value=client):
            result = shared.call_claude("system prompt", "user prompt")
        assert result == "Claude response text"

    def test_passes_correct_model(self):
        client = self._make_claude_client()
        with patch("shared.anthropic.Anthropic", return_value=client):
            shared.call_claude("sys", "usr")
        _, kwargs = client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self):
        client = self._make_claude_client()
        with patch("shared.anthropic.Anthropic", return_value=client):
            shared.call_claude("my system", "my user")
        _, kwargs = client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens(self):
        client = self._make_claude_client()
        with patch("shared.anthropic.Anthropic", return_value=client):
            shared.call_claude("s", "u")
        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self):
        client = self._make_claude_client()
        with patch("shared.anthropic.Anthropic", return_value=client):
            shared.call_claude("s", "u", max_tokens=1024)
        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self):
        client = self._make_claude_client()
        with patch("shared.anthropic.Anthropic", return_value=client) as mock_cls:
            shared.call_claude("s", "u")
        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_propagates_exception(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API error")
        with patch("shared.anthropic.Anthropic", return_value=client):
            with pytest.raises(RuntimeError, match="API error"):
                shared.call_claude("s", "u")

    def test_empty_prompts_still_calls_api(self):
        client = self._make_claude_client()
        with patch("shared.anthropic.Anthropic", return_value=client):
            result = shared.call_claude("", "")
        assert result == "Claude response text"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path, item_type="blob", url="http://example.com/blob"):
        return {"type": item_type, "path": path, "url": url}

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return {"content": encoded}

    def test_happy_path_fetches_matching_files(self):
        tree = [
            self._tree_item("src/main.py"),
            self._tree_item("README.md"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._make_blob_response("print('hello')"))

        with patch("shared.requests.get", side_effect=[tree_resp, blob_resp]) as mock_get:
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == "print('hello')"
        assert "README.md" not in result

    def test_filters_by_multiple_extensions(self):
        tree = [
            self._tree_item("a.py", url="http://url/a"),
            self._tree_item("b.js", url="http://url/b"),
            self._tree_item("c.txt", url="http://url/c"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_py = _make_response(json_data=self._make_blob_response("py content"))
        blob_js = _make_response(json_data=self._make_blob_response("js content"))

        with patch("shared.requests.get", side_effect=[tree_resp, blob_py, blob_js]):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    def test_respects_max_files_limit(self):
        tree = [self._tree_item(f"file{i}.py", url=f"http://url/{i}") for i in range(10)]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._make_blob_response("content"))

        with patch("shared.requests.get", side_effect=[tree_resp] + [blob_resp] * 3):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self):
        tree = [
            self._tree_item("src", item_type="tree"),
            self._tree_item("main.py"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._make_blob_response("code"))

        with patch("shared.requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert len(result) == 1
        assert "main.py" in result

    def test_empty_tree_returns_empty_dict(self):
        tree_resp = _make_response(json_data={"tree": []})
        with patch("shared.requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_missing_tree_key_returns_empty(self):
        tree_resp = _make_response(json_data={})
        with patch("shared.requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_blob_decode_exception_skips_file(self):
        tree = [self._tree_item("bad.py")]
        tree_resp = _make_response(json_data={"tree": tree})
        # Return a blob with no 'content' key so base64.b64decode raises KeyError
        blob_resp = _make_response(json_data={})

        with patch("shared.requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_correct_url_constructed(self):
        tree_resp = _make_response(json_data={"tree": []})
        with patch("shared.requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])
        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_default_max_files_is_20(self):
        tree = [self._tree_item(f"f{i}.py", url=f"http://u/{i}") for i in range(25)]
        tree_resp = _make_response(json_data={"tree": tree})
        blob = _make_response(json_data=self._make_blob_response("x"))

        with patch("shared.requests.get", side_effect=[tree_resp] + [blob] * 20):
            result = shared.get_repo_files("o", "r", [".py"])

        assert len(result) == 20

    def test_utf8_content_decoded(self):
        content = "# Arabic: \u0625\u0644\u063a\u0627\u0621"
        tree = [self._tree_item("ar.py")]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._make_blob_response(content))

        with patch("shared.requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("o", "r", [".py"])

        assert result["ar.py"] == content


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    def test_happy_path_returns_diff_text(self):
        diff_text = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
        mock_resp = _make_response(