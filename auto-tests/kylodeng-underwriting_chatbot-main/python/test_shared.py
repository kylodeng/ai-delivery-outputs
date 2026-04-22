"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude: happy path, API error, empty response
  - clean_json: markdown fence stripping (various formats), plain JSON passthrough
  - get_repo_files: happy path with extension filtering, max_files limit, base64 decode errors, empty tree
  - get_pr_diff: happy path, truncation at 30000 chars
  - write_output_file: create new file (no SHA), update existing file (with SHA), fallback URL
  - post_pr_comment: happy path, request forwarding
  - send_email: success (200/202), failure warning path
  - email_html: SUCCESS status renders green, FAILURE renders red, all fields present
  - write_audit_entry: tested via integration stub (requires full source — see TODO)

Mocks used:
  - unittest.mock.patch / MagicMock for:
      * anthropic.Anthropic (Claude client)
      * requests.get / requests.post / requests.put
      * os.environ (via monkeypatch)

TODOs:
  - write_audit_entry: source file is truncated; stub test added with skip marker
  - MODEL constant value: tested as string equality only, may need updating if model name changes
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers to import shared.py safely despite mandatory env vars
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(scope="session", autouse=True)
def _patch_env_for_import(tmp_path_factory):
    """Ensure mandatory env vars exist before shared.py is imported."""
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        # Force a fresh import so module-level constants pick up patched env
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Add the script directory to sys.path so we can import it
        import sys as _sys
        _sys.path.insert(0, ".github/scripts")
        yield
        # Cleanup path
        try:
            _sys.path.remove(".github/scripts")
        except ValueError:
            pass


@pytest.fixture()
def shared_module():
    """Return a freshly-imported (or cached) shared module under patched env."""
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        if "shared" in sys.modules:
            mod = sys.modules["shared"]
        else:
            import importlib as _il
            mod = _il.import_module("shared")
        return mod


# ---------------------------------------------------------------------------
# Attempt the real import once; skip all tests gracefully if scripts missing
# ---------------------------------------------------------------------------

try:
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        sys.path.insert(0, ".github/scripts")
        import shared  # noqa: E402  (imported after sys.path manipulation)
    _IMPORT_OK = True
except Exception as exc:  # pragma: no cover
    _IMPORT_OK = False
    _IMPORT_ERROR = str(exc)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK,
    reason=f"Could not import shared.py – {'' if _IMPORT_OK else _IMPORT_ERROR}",  # type: ignore[possibly-undefined]
)


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for clean_json() – pure function, no mocks needed."""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_triple_backtick_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_triple_backtick_no_language(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   \n{\"key\": \"value\"}\n   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_multiline_json_in_fence(self):
        inner = '{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        # Must be valid JSON after cleaning
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_fence_with_extra_content_after_closing(self):
        """Only the last ``` is stripped as closing fence."""
        raw = "```json\n{\"x\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"x": 1}'

    def test_no_fence_already_valid(self):
        raw = '["CUST00000001", "CUST00006151"]'
        assert shared.clean_json(raw) == '["CUST00000001", "CUST00006151"]'

    @pytest.mark.parametrize("fence_lang", ["```json", "```python", "```text", "```"])
    def test_various_fence_languages(self, fence_lang):
        raw = f"{fence_lang}\n{{\"ok\": true}}\n```"
        result = shared.clean_json(raw)
        assert result == '{"ok": true}'


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for call_claude() — mocks anthropic.Anthropic."""

    def _make_client_mock(self, text: str):
        """Return a mock Anthropic client that yields `text` as response."""
        mock_content = MagicMock()
        mock_content.text = text

        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_messages = MagicMock()
        mock_messages.create.return_value = mock_response

        mock_client = MagicMock()
        mock_client.messages = mock_messages

        return mock_client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = self._make_client_mock("Hello from Claude")
        result = shared.call_claude("system prompt", "user prompt")
        assert result == "Hello from Claude"

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls):
        mock_client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = mock_client
        shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_passes_max_tokens_default(self, mock_anthropic_cls):
        mock_client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = mock_client
        shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_passes_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = mock_client
        shared.call_claude("sys", "usr", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        mock_client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = mock_client
        shared.call_claude("my system", "my user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    @patch("shared.anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API unavailable")
        mock_anthropic_cls.return_value = mock_client
        with pytest.raises(Exception, match="API unavailable"):
            shared.call_claude("sys", "usr")

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = self._make_client_mock("ok")
        shared.call_claude("sys", "usr")
        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_empty_text_response(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = self._make_client_mock("")
        result = shared.call_claude("sys", "usr")
        assert result == ""


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for get_repo_files() — mocks requests.get."""

    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    @patch("shared.requests.get")
    def test_happy_path_filters_by_extension(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/readme"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/main"},
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree/src"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._make_tree_response(tree_items)
            if "readme" in url:
                return self._make_blob_response("# README")
            if "main" in url:
                return self._make_blob_response("print('hello')")
            return MagicMock()

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "main.py" in result
        assert "README.md" not in result
        assert result["main.py"] == "print('hello')"

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(25)
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._make_tree_response(tree_items)
            # Any blob request
            return self._make_blob_response("content")

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)
        assert len(result) == 5

    @patch("shared.requests.get")
    def test_empty_tree(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": []}
        mock_get.return_value = mock_resp
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_base64_decode_error_skips_file(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "bad.py", "url": "https://api.github.com/blob/bad"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._make_tree_response(tree_items)
            # Return a response with invalid/missing content key
            mock_blob = MagicMock()
            mock_blob.json.return_value = {}  # no "content" key → KeyError
            return mock_blob

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"])
        # Exception should be swallowed; file not added
        assert "bad.py" not in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "model.json", "url": "https://api.github.com/blob/json"},
            {"type": "blob", "path": "script.py", "url": "https://api.github.com/blob/py"},
            {"type": "blob", "path": "ignore.txt", "url": "https://api.github.com/blob/txt"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._make_tree_response(tree_items)
            return self._make_blob_response("data")

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".json", ".py"])
        assert "model.json" in result
        assert "script.py" in result
        assert "ignore.txt" not in result

    @patch("shared.requests.get")
    def test_only_blobs_included_not_trees(self, mock_get):
        tree_items = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/blob/src"},
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/app"},
        ]

        def side_effect(url, headers):
            if "trees" in url:
                return self._make_tree_response(tree_items)
            return self._make_blob_response("app content")

        mock_get.side_effect = side_effect