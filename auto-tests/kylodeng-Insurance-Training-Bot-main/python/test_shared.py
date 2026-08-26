"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation and text response extraction
- clean_json(): Markdown fence stripping from JSON strings
- get_repo_files(): GitHub API tree fetching and file content decoding
- get_pr_diff(): Pull request diff fetching
- write_output_file(): File creation/update in output repo (create and update paths)
- post_pr_comment(): PR comment posting
- send_email(): SendGrid email sending (success and failure paths)
- email_html(): HTML email body generation
- write_audit_entry(): Audit log entry construction and repo writing

Mocks used:
- unittest.mock.patch for `requests.get`, `requests.post`, `requests.put`
- unittest.mock.patch for `anthropic.Anthropic` client
- unittest.mock.patch for `base64.b64decode` (selectively)
- Environment variables patched via monkeypatch / os.environ

TODOs:
- TODO: write_audit_entry full integration test requires knowing the exact JSON/MD
  audit log format from the truncated source (source code is cut off mid-function)
- TODO: Test retry/back-off behaviour if added in future
- TODO: Test Unicode handling in get_repo_files beyond the errors="replace" path
"""

import base64
import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, Mock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(autouse=True, scope="session")
def _set_env():
    """Inject required env vars before the module is imported."""
    original = {}
    for k, v in ENV_DEFAULTS.items():
        original[k] = os.environ.get(k)
        os.environ[k] = v
    yield
    for k, v in original.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# Import the module under test (after env is set)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def shared_module(_set_env):
    """Import shared once per session with env vars already in place."""
    # Remove cached version if present so env vars are picked up cleanly
    sys.modules.pop("shared", None)

    script_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
    script_dir = os.path.normpath(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import shared  # noqa: PLC0415
    return shared


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text=""):
    """Build a minimal mock requests.Response."""
    resp = Mock()
    resp.status_code = status_code
    resp.text = text
    resp.json = Mock(return_value=json_data if json_data is not None else {})
    return resp


# ============================================================================
# clean_json
# ============================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_fence(self, shared_module):
        raw = "```\n{\"a\": 1}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"a": 1}'

    def test_leading_trailing_whitespace(self, shared_module):
        raw = "   ```json\n{\"x\": 2}\n```   "
        result = shared_module.clean_json(raw)
        assert result == '{"x": 2}'

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_no_closing_fence(self, shared_module):
        """If there's no closing fence rsplit still returns the whole string."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_module.clean_json(raw)
        # Opening fence is stripped; no closing ``` present so rsplit returns same
        assert '{"key": "value"}' in result

    def test_multiline_json_preserved(self, shared_module):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        assert shared_module.clean_json(raw) == inner

    def test_already_clean_no_fence_prefix(self, shared_module):
        raw = "plain text without fence"
        assert shared_module.clean_json(raw) == "plain text without fence"

    @pytest.mark.parametrize("fence", ["```json", "```python", "```"])
    def test_various_fence_languages(self, shared_module, fence):
        raw = f"{fence}\n{{\"ok\": true}}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"ok": true}'


# ============================================================================
# call_claude
# ============================================================================

class TestCallClaude:
    def test_happy_path_returns_text(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared_module.call_claude("sys prompt", "user prompt")

        assert result == "Hello from Claude"

    def test_passes_correct_model_and_tokens(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="response")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module.call_claude("sys", "user", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1024
        assert call_kwargs.kwargs["model"] == shared_module.MODEL

    def test_passes_system_and_user(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module.call_claude("my-system", "my-user")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["system"] == "my-system"
        assert kwargs["messages"] == [{"role": "user", "content": "my-user"}]

    def test_default_max_tokens(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module.call_claude("s", "u")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 4096

    def test_api_key_used(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared_module.call_claude("s", "u")
            mock_cls.assert_called_once_with(api_key="test-anthropic-key")

    def test_anthropic_exception_propagates(self, shared_module):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API error"):
                shared_module.call_claude("s", "u")


# ============================================================================
# get_repo_files
# ============================================================================

class TestGetRepoFiles:
    def _make_tree(self, paths):
        """Build a fake GitHub tree response."""
        tree = []
        for path in paths:
            tree.append({"type": "blob", "path": path, "url": f"https://api.github.com/blob/{path}"})
        return {"tree": tree}

    def _make_blob(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return {"content": encoded + "\n"}  # GitHub adds newline

    def test_happy_path_filters_by_extension(self, shared_module):
        tree_response = _make_response(json_data=self._make_tree(["a.py", "b.js", "c.py", "d.txt"]))
        blob_py = _make_response(json_data=self._make_blob("print('hello')"))

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                tree_response,
                blob_py,
                blob_py,
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "a.py" in result
        assert "c.py" in result
        assert "b.js" not in result
        assert "d.txt" not in result

    def test_max_files_limit(self, shared_module):
        paths = [f"file{i}.py" for i in range(10)]
        tree_response = _make_response(json_data=self._make_tree(paths))
        blob = _make_response(json_data=self._make_blob("content"))

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [tree_response] + [blob] * 3
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree(self, shared_module):
        tree_response = _make_response(json_data={"tree": []})

        with patch("requests.get", return_value=tree_response):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self, shared_module):
        tree = self._make_tree(["a.py", "b.md", "c.js", "d.txt"])
        tree_response = _make_response(json_data=tree)
        blob = _make_response(json_data=self._make_blob("data"))

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [tree_response, blob, blob]
            result = shared_module.get_repo_files("owner", "repo", [".py", ".md"])

        assert "a.py" in result
        assert "b.md" in result
        assert "c.js" not in result

    def test_skips_non_blob_items(self, shared_module):
        tree_data = {
            "tree": [
                {"type": "tree", "path": "src", "url": "https://example.com/src"},
                {"type": "blob", "path": "main.py", "url": "https://example.com/main.py"},
            ]
        }
        tree_response = _make_response(json_data=tree_data)
        blob = _make_response(json_data=self._make_blob("code"))

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [tree_response, blob]
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "src" not in result

    def test_bad_blob_content_skipped(self, shared_module):
        """If decoding raises, the file is silently skipped."""
        tree = self._make_tree(["ok.py", "bad.py"])
        tree_response = _make_response(json_data=tree)
        good_blob = _make_response(json_data=self._make_blob("good content"))
        bad_blob = _make_response(json_data={"content": None})  # will cause AttributeError

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [tree_response, good_blob, bad_blob]
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "ok.py" in result
        assert "bad.py" not in result

    def test_correct_url_constructed(self, shared_module):
        tree_response = _make_response(json_data={"tree": []})

        with patch("requests.get") as mock_get:
            mock_get.return_value = tree_response
            shared_module.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_tree_missing_key_defaults_empty(self, shared_module):
        """If response has no 'tree' key, defaults to empty list."""
        tree_response = _make_response(json_data={})

        with patch("requests.get", return_value=tree_response):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert result == {}


# ============================================================================
# get_pr_diff
# ============================================================================

class TestGetPrDiff:
    def test_happy_path(self, shared_module):
        diff_text = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new\n"
        resp = _make_response(text=diff_text)

        with patch("requests.get", return_value=resp) as mock_get:
            result = shared_module.get_pr_diff("owner", "repo", 42)

        assert result == diff_text
        url = mock_get.call_args[0][0]
        assert "owner" in url
        assert "repo" in url
        assert "42" in url

    def test_diff_truncated_at_30000(self, shared_module):
        long_diff = "x" * 40000
        resp = _make_response(text=long_diff)

        with patch("requests.