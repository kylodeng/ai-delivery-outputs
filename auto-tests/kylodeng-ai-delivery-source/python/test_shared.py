"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: happy path, API response parsing
- clean_json: markdown fence stripping (various formats), plain JSON passthrough
- get_repo_files: happy path, extension filtering, max_files limit, base64 decode errors, empty tree
- get_pr_diff: happy path, truncation at 30000 chars
- write_output_file: create new file (no SHA), update existing file (with SHA), fallback URL
- post_pr_comment: happy path, correct URL construction
- send_email: success (200, 202), failure warning path
- email_html: SUCCESS/FAILURE status colour, content inclusion
- write_audit_entry: entry construction, JSON and Markdown log writing (stub — truncated source)

Mocks used:
- unittest.mock.patch for os.environ (all env vars)
- unittest.mock.patch / MagicMock for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put

TODOs:
- write_audit_entry: source is truncated; full logic cannot be tested without complete implementation
- call_claude: extended token/model validation requires real API shape knowledge beyond snippet
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to (re)import the module with controlled env vars
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "kylo.deng@capco.com",
    "SENDER_EMAIL": "kylo.deng@capco.com",
}


def import_shared(extra_env: dict | None = None):
    """Import (or reimport) shared with a known environment."""
    env = {**REQUIRED_ENV, **(extra_env or {})}
    # Remove the cached module so it re-executes module-level code
    sys.modules.pop("shared", None)
    with patch.dict("os.environ", env, clear=False):
        # Ensure anthropic import doesn't fail at module level
        if "anthropic" not in sys.modules:
            mock_anthropic = types.ModuleType("anthropic")
            mock_anthropic.Anthropic = MagicMock()
            sys.modules["anthropic"] = mock_anthropic
        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", ".github/scripts/shared.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_anthropic_module():
    """Provide a fake anthropic module for every test."""
    mock_anthropic = types.ModuleType("anthropic")
    mock_anthropic.Anthropic = MagicMock()
    with patch.dict("os.environ", REQUIRED_ENV):
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            yield mock_anthropic


@pytest.fixture()
def shared(_mock_anthropic_module):
    """Return freshly imported shared module."""
    sys.modules.pop("shared", None)
    with patch.dict("os.environ", REQUIRED_ENV):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "shared", ".github/scripts/shared.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------


class TestCleanJson:
    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_backtick_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_backtick_fence(self, shared):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = "   \n{\"key\": \"value\"}\n   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_fence_with_extra_whitespace_inside(self, shared):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_whitespace_only(self, shared):
        assert shared.clean_json("   ") == ""

    def test_no_closing_fence_strips_opening_only(self, shared):
        """If there's no closing fence, rsplit returns the whole string."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # opening line stripped, no closing fence present
        assert '{"key": "value"}' in result

    def test_nested_json_preserved(self, shared):
        inner = '{"list": [1, 2, 3], "nested": {"a": true}}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    def test_array_json(self, shared):
        raw = "```json\n[1, 2, 3]\n```"
        assert shared.clean_json(raw) == "[1, 2, 3]"


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------


class TestCallClaude:
    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_happy_path_returns_text(self, shared, _mock_anthropic_module):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("Hello!")
        _mock_anthropic_module.Anthropic.return_value = mock_client

        result = shared.call_claude("sys prompt", "user msg")

        assert result == "Hello!"

    def test_passes_system_and_user_to_api(self, shared, _mock_anthropic_module):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        _mock_anthropic_module.Anthropic.return_value = mock_client

        shared.call_claude("my system", "my user", max_tokens=512)

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=512,
            system="my system",
            messages=[{"role": "user", "content": "my user"}],
        )

    def test_default_max_tokens(self, shared, _mock_anthropic_module):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        _mock_anthropic_module.Anthropic.return_value = mock_client

        shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_uses_anthropic_api_key_from_env(self, shared, _mock_anthropic_module):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")
        _mock_anthropic_module.Anthropic.return_value = mock_client

        shared.call_claude("s", "u")

        _mock_anthropic_module.Anthropic.assert_called_once_with(
            api_key="test-anthropic-key"
        )

    def test_returns_first_content_block(self, shared, _mock_anthropic_module):
        content_a = MagicMock()
        content_a.text = "first"
        content_b = MagicMock()
        content_b.text = "second"
        response = MagicMock()
        response.content = [content_a, content_b]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = response
        _mock_anthropic_module.Anthropic.return_value = mock_client

        assert shared.call_claude("s", "u") == "first"

    def test_api_exception_propagates(self, shared, _mock_anthropic_module):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API failure")
        _mock_anthropic_module.Anthropic.return_value = mock_client

        with pytest.raises(RuntimeError, match="API failure"):
            shared.call_claude("s", "u")


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------


class TestGetRepoFiles:
    def _make_blob_item(self, path: str, url: str = "http://blob-url"):
        return {"type": "blob", "path": path, "url": url}

    def _make_content_response(self, text: str):
        encoded = base64.b64encode(text.encode()).decode()
        return {"content": encoded}

    @patch("requests.get")
    def test_happy_path_fetches_matching_files(self, mock_get, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                self._make_blob_item("src/main.py"),
                self._make_blob_item("src/utils.py"),
                self._make_blob_item("README.md"),
            ]
        }
        blob_resp = MagicMock()
        blob_resp.json.return_value = self._make_content_response("print('hello')")

        mock_get.side_effect = [tree_resp, blob_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "README.md" not in result

    @patch("requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get, shared):
        resp = MagicMock()
        resp.json.return_value = {"tree": []}
        mock_get.return_value = resp

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_max_files_limit_respected(self, mock_get, shared):
        blobs = [self._make_blob_item(f"file{i}.py") for i in range(10)]
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": blobs}

        blob_resp = MagicMock()
        blob_resp.json.return_value = self._make_content_response("code")
        mock_get.side_effect = [tree_resp] + [blob_resp] * 5

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)
        assert len(result) == 5

    @patch("requests.get")
    def test_multiple_extensions_filtered(self, mock_get, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                self._make_blob_item("a.py"),
                self._make_blob_item("b.js"),
                self._make_blob_item("c.md"),
                self._make_blob_item("d.txt"),
            ]
        }
        blob_resp = MagicMock()
        blob_resp.json.return_value = self._make_content_response("content")
        mock_get.side_effect = [tree_resp, blob_resp, blob_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py", ".js", ".md"])
        assert set(result.keys()) == {"a.py", "b.js", "c.md"}

    @patch("requests.get")
    def test_base64_decode_error_skips_file(self, mock_get, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [self._make_blob_item("bad.py")]
        }
        blob_resp = MagicMock()
        # Missing 'content' key — will raise KeyError → caught by bare except
        blob_resp.json.return_value = {}
        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_non_blob_items_ignored(self, mock_get, shared):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                {"type": "tree", "path": "src/", "url": "http://x"},
                self._make_blob_item("src/main.py"),
            ]
        }
        blob_resp = MagicMock()
        blob_resp.json.return_value = self._make_content_response("code")
        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert list(result.keys()) == ["src/main.py"]

    @patch("requests.get")
    def test_correct_tree_url_constructed(self, mock_get, shared):
        resp = MagicMock()
        resp.json.return_value = {"tree": []}
        mock_get.return_value = resp

        shared.get_repo_files("my-owner", "my-repo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "my-owner/my-repo/git/trees/HEAD?recursive=1" in first_call_url

    @patch("requests.get")
    def test_utf8_replace_on_decode_error(self, mock_get, shared):
        """Files with non-UTF-8 bytes are decoded with errors='replace'."""
        binary_content = base64.b64encode(b"\xff\xfe hello").decode()
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [self._make_blob_item("binary.py")]
        }
        blob_resp = MagicMock()
        blob_resp.json.return_value = {"content": binary_content}
        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "binary.py" in result  # decoded with replacement chars, not skipped


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------


class TestGetPrDiff:
    @patch("requests.get")
    def test_happy_path_returns_diff_text(self, mock_get, shared):
        mock_resp = M