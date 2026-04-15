"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: happy path, API response parsing
- clean_json: markdown fence stripping (various formats), plain JSON passthrough, edge cases
- get_repo_files: normal fetch, extension filtering, max_files cap, base64 decode errors, empty tree
- get_pr_diff: happy path, truncation at 30000 chars
- write_output_file: create new file (no sha), update existing file (with sha), fallback URL
- post_pr_comment: happy path, correct URL construction
- send_email: success (200/202), warning on failure
- email_html: SUCCESS/FAILURE status colour, content inclusion
- write_audit_entry: stub (requires further repo-write context)

Mocks used:
- unittest.mock.patch for: requests.get, requests.post, requests.put, anthropic.Anthropic
- os.environ patched via monkeypatch/pytest fixtures

TODOs:
- write_audit_entry: full integration requires inspecting the audit JSON/Markdown
  written to the output repo; stubbed below pending that context.
"""

import base64
import json
import os
import sys
import types
import importlib
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Fixture: inject required environment variables BEFORE importing shared.py
# ---------------------------------------------------------------------------
REQUIRED_ENV = {
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
def _set_env():
    """Set required env vars for the entire test session."""
    with patch.dict(os.environ, REQUIRED_ENV, clear=False):
        yield


@pytest.fixture(scope="session")
def shared():
    """Import shared module once per session after env vars are set."""
    # Remove any cached version so it re-imports with patched env
    if "shared" in sys.modules:
        del sys.modules["shared"]
    # Ensure the script directory is on sys.path
    script_dir = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
    abs_dir = os.path.abspath(script_dir)
    added = False
    if abs_dir not in sys.path:
        sys.path.insert(0, abs_dir)
        added = True
    module = importlib.import_module("shared")
    yield module
    if added:
        sys.path.remove(abs_dir)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text=""):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == raw

    def test_strips_json_code_fence(self, shared):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self, shared):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = '  {"key": "value"}  '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self, shared):
        raw = '  ```json\n{"a": 1}\n```  '
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_multiline_json_in_fence(self, shared):
        raw = '```json\n{\n  "product_name": "Generations II",\n  "doc_type": "product_brochure"\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["product_name"] == "Generations II"
        assert parsed["doc_type"] == "product_brochure"

    def test_no_closing_fence_leaves_content(self, shared):
        """If there's an opening fence but no closing, rsplit still works."""
        raw = '```json\n{"key": "value"}'
        result = shared.clean_json(raw)
        # Should strip the opening line and leave the rest
        assert '{"key": "value"}' in result

    def test_json_array_in_fence(self, shared):
        raw = '```json\n[1, 2, 3]\n```'
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_does_not_strip_non_fence_backticks(self, shared):
        """Inline backticks that are not fences should not be stripped."""
        raw = '{"key": "some `value`"}'
        assert shared.clean_json(raw) == raw


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def test_happy_path_returns_text(self, shared):
        mock_text = "Here is the analysis."
        mock_content = MagicMock()
        mock_content.text = mock_text

        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == mock_text

    def test_passes_correct_model_and_tokens(self, shared):
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1024
        assert call_kwargs.kwargs["model"] == shared.MODEL

    def test_passes_system_and_user_messages(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens_is_4096(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 4096

    def test_uses_api_key_from_env(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("sys", "usr")

        mock_cls.assert_called_once_with(api_key="test-anthropic-key")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _make_tree_item(self, path, item_type="blob", url="http://example.com/blob"):
        return {"path": path, "type": item_type, "url": url}

    def test_happy_path_single_file(self, shared):
        file_content = "print('hello')"
        tree_resp = _make_response(json_data={"tree": [
            self._make_tree_item("src/main.py", url="http://gh/blob/1")
        ]})
        blob_resp = _make_response(json_data={"content": _b64(file_content) + "\n"})

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == file_content

    def test_filters_by_extension(self, shared):
        tree_resp = _make_response(json_data={"tree": [
            self._make_tree_item("src/main.py"),
            self._make_tree_item("README.md"),
            self._make_tree_item("app.js"),
        ]})
        blob_py = _make_response(json_data={"content": _b64("python code")})

        with patch("requests.get", side_effect=[tree_resp, blob_py]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "README.md" not in result
        assert "app.js" not in result

    def test_respects_max_files_limit(self, shared):
        items = [self._make_tree_item(f"file{i}.py") for i in range(10)]
        tree_resp = _make_response(json_data={"tree": items})
        blob_resps = [_make_response(json_data={"content": _b64(f"content{i}")}) for i in range(3)]

        with patch("requests.get", side_effect=[tree_resp] + blob_resps):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self, shared):
        tree_resp = _make_response(json_data={"tree": []})

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_skips_non_blob_items(self, shared):
        tree_resp = _make_response(json_data={"tree": [
            self._make_tree_item("src", item_type="tree"),
            self._make_tree_item("src/main.py"),
        ]})
        blob_resp = _make_response(json_data={"content": _b64("code")})

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "src/main.py" in result

    def test_handles_base64_decode_error_gracefully(self, shared):
        tree_resp = _make_response(json_data={"tree": [
            self._make_tree_item("bad.py"),
        ]})
        # Missing 'content' key triggers exception
        blob_resp = _make_response(json_data={})

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Should silently skip the file
        assert result == {}

    def test_multiple_extensions(self, shared):
        tree_resp = _make_response(json_data={"tree": [
            self._make_tree_item("main.py"),
            self._make_tree_item("app.js"),
            self._make_tree_item("style.css"),
        ]})
        blob_py = _make_response(json_data={"content": _b64("python")})
        blob_js = _make_response(json_data={"content": _b64("javascript")})

        with patch("requests.get", side_effect=[tree_resp, blob_py, blob_js]):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "main.py" in result
        assert "app.js" in result
        assert "style.css" not in result

    def test_correct_url_construction(self, shared):
        tree_resp = _make_response(json_data={"tree": []})

        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_missing_tree_key_defaults_to_empty(self, shared):
        tree_resp = _make_response(json_data={})  # no 'tree' key

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    def test_happy_path_returns_diff_text(self, shared):
        diff_text = "diff --git a/file.py b/file.py\n+added line\n-removed line"
        mock_resp = _make_response(text=diff_text)

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 42)

        assert result == diff_text

    def test_truncates_at_30000_chars(self, shared):
        long_diff = "x" * 40000
        mock_resp = _make_response(text=long_diff)

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_exact_30000_chars_not_truncated(self, shared):
        exact