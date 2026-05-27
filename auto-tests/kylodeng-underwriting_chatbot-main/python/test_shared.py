"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude(): Happy path, API error propagation, custom max_tokens
  - clean_json(): Strips markdown fences, handles plain JSON, edge cases
  - get_repo_files(): Happy path, extension filtering, max_files limit, decode errors, empty tree
  - get_pr_diff(): Happy path, truncation at 30000 chars
  - write_output_file(): Create new file (no SHA), update existing file (with SHA), fallback URL
  - post_pr_comment(): Happy path, request construction
  - send_email(): Success (202), warning on failure, custom recipient
  - email_html(): SUCCESS/FAILURE status colours, required fields present
  - write_audit_entry(): Tested via integration of write_output_file mock (stub — source truncated)

Mocks used:
  - unittest.mock.patch / MagicMock for:
      * anthropic.Anthropic (Claude client)
      * requests.get / requests.post / requests.put
      * base64.b64decode (selected tests)
      * datetime.datetime (selected tests)

TODOs:
  - write_audit_entry() source is truncated — full behaviour untestable without complete source
  - GH_HEADERS token value depends on GH_TOKEN env var set at import time
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GH_TOKEN", "test-gh-token")
os.environ.setdefault("SENDGRID_API_KEY", "test-sendgrid-key")
os.environ.setdefault("OUTPUT_REPO", "ai-delivery-outputs")
os.environ.setdefault("OUTPUT_REPO_OWNER", "test-owner")
os.environ.setdefault("NOTIFY_EMAIL", "notify@example.com")
os.environ.setdefault("SENDER_EMAIL", "sender@example.com")

# Now safe to import
import importlib.util, pathlib

_SHARED_PATH = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py"

def _load_shared():
    spec = importlib.util.spec_from_file_location("shared", _SHARED_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

try:
    shared = _load_shared()
except FileNotFoundError:
    # Allow test collection to succeed even when running outside the repo root
    shared = None  # tests will be skipped below


pytestmark = pytest.mark.skipif(shared is None, reason="shared.py not found at expected path")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _make_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:

    def _mock_client(self, text="Hello from Claude"):
        message = MagicMock()
        message.content = [MagicMock(text=text)]
        client = MagicMock()
        client.messages.create.return_value = message
        return client

    @patch("anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        client = self._mock_client("Generated text")
        mock_anthropic_cls.return_value = client

        result = shared.call_claude("system prompt", "user prompt")

        assert result == "Generated text"
        client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="system prompt",
            messages=[{"role": "user", "content": "user prompt"}],
        )

    @patch("anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        client = self._mock_client("short")
        mock_anthropic_cls.return_value = client

        shared.call_claude("sys", "user", max_tokens=512)

        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 512

    @patch("anthropic.Anthropic")
    def test_uses_correct_model(self, mock_anthropic_cls):
        client = self._mock_client()
        mock_anthropic_cls.return_value = client

        shared.call_claude("s", "u")

        _, kwargs = client.messages.create.call_args
        assert kwargs["model"] == "claude-sonnet-4-6"

    @patch("anthropic.Anthropic")
    def test_api_key_passed_to_client(self, mock_anthropic_cls):
        client = self._mock_client()
        mock_anthropic_cls.return_value = client

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("anthropic.Anthropic")
    def test_propagates_exception(self, mock_anthropic_cls):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API down")
        mock_anthropic_cls.return_value = client

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude("s", "u")

    @patch("anthropic.Anthropic")
    def test_empty_system_and_user(self, mock_anthropic_cls):
        client = self._mock_client("")
        mock_anthropic_cls.return_value = client

        result = shared.call_claude("", "")
        assert result == ""

    @patch("anthropic.Anthropic")
    def test_large_response_returned_in_full(self, mock_anthropic_cls):
        large_text = "x" * 100_000
        client = self._mock_client(large_text)
        mock_anthropic_cls.return_value = client

        result = shared.call_claude("s", "u")
        assert len(result) == 100_000


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "  \n  {\"x\": 2}  \n  "
        result = shared.clean_json(raw)
        assert result == '{"x": 2}'

    def test_fence_with_whitespace_around_content(self):
        raw = "```json\n  {\"k\": \"v\"}  \n```"
        result = shared.clean_json(raw)
        assert result == '{"k": "v"}'

    def test_nested_backtick_content_preserved(self):
        raw = "```json\n{\"code\": \"x = `y`\"}\n```"
        result = shared.clean_json(raw)
        # The closing ``` split; inner backticks are part of value
        assert "x = `y`" in result

    def test_model_card_json_input(self):
        """Synthetic data: model card JSON should pass through clean."""
        mc = json.dumps({
            "model_name": "Underwriting Risk Classification",
            "model_type": "CatBoostClassifier",
            "target_variable": "Risk_Classification",
        })
        assert shared.clean_json(mc) == mc

    def test_arabic_unicode_json(self):
        """Synthetic data: Arabic translation JSON."""
        raw = json.dumps({"cancel": "\u0625\u0644\u063a\u0627\u0621"})
        assert shared.clean_json(raw) == raw

    def test_empty_string(self):
        result = shared.clean_json("")
        assert result == ""

    def test_only_fence_markers(self):
        raw = "```\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_multiline_json_with_fence(self):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner.strip()


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _tree_item(self, path, item_type="blob", url="https://api.github.com/blob/abc"):
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, content: str):
        return {"content": _b64(content) + "\n"}

    @patch("requests.get")
    def test_happy_path_returns_matching_files(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._tree_item("src/foo.py"),
            self._tree_item("src/bar.js"),
        ]})
        blob_py = _make_response(json_data=self._blob_response("print('hello')"))
        mock_get.side_effect = [tree_resp, blob_py]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/foo.py" in result
        assert result["src/foo.py"] == "print('hello')"
        assert "src/bar.js" not in result

    @patch("requests.get")
    def test_extension_filter_multiple(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._tree_item("a.py"),
            self._tree_item("b.js"),
            self._tree_item("c.md"),
        ]})
        blob_py = _make_response(json_data=self._blob_response("py content"))
        blob_js = _make_response(json_data=self._blob_response("js content"))
        mock_get.side_effect = [tree_resp, blob_py, blob_js]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.md" not in result

    @patch("requests.get")
    def test_max_files_limit(self, mock_get):
        items = [self._tree_item(f"file{i}.py") for i in range(10)]
        tree_resp = _make_response(json_data={"tree": items})
        blob_resp = _make_response(json_data=self._blob_response("content"))
        # tree + 3 blobs
        mock_get.side_effect = [tree_resp] + [blob_resp] * 3

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("requests.get")
    def test_non_blob_items_skipped(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            {"type": "tree", "path": "src", "url": "https://x"},
            self._tree_item("src/f.py"),
        ]})
        blob_resp = _make_response(json_data=self._blob_response("data"))
        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert len(result) == 1
        assert "src/f.py" in result

    @patch("requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        tree_resp = _make_response(json_data={"tree": []})
        mock_get.return_value = tree_resp

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_missing_tree_key_returns_empty(self, mock_get):
        tree_resp = _make_response(json_data={})
        mock_get.return_value = tree_resp

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_decode_error_skips_file(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._tree_item("bad.py"),
            self._tree_item("good.py"),
        ]})
        bad_blob = _make_response(json_data={})   # missing "content" key → exception
        good_blob = _make_response(json_data=self._blob_response("ok"))
        mock_get.side_effect = [tree_resp, bad_blob, good_blob]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "good.py" in result
        assert "bad.py" not in result

    @patch("requests.get")
    def test_correct_url_constructed(self, mock_get):
        tree_resp = _make_response(json_data={"tree": []})
        mock_get.return_value = tree_resp

        shared.get_repo_files("myowner", "myrepo", [".py"])

        url_called = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo" in url_called
        assert "recursive=1" in url_called

    @patch("requests.get")
    def test_no_matching_extension(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._tree_item("readme.md"),
            self._tree_item("image.png"),
        ]})
        mock_get.return_value = tree_resp

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:

    @patch("requests.get")
    def test_happy_path_returns_text(self, mock_get):
        mock_get.return_value = _make_response(text="diff --git a/f.py b/f.py\n+new line")

        result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result
        assert "+new line" in result

    @patch("requests.get")
    def test_truncates_at_30000_chars(self, mock_get):
        long_diff = "x" * 50_000
        mock_get.return_value = _make_response(text=long_diff)

        result = shared.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30_000

    @patch("requests.get")
    def test_shorter_than_limit_not_truncated(self, mock_get):