"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub API tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): File creation (no SHA) and update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success and failure responses
- email_html(): HTML generation, status colour logic, URL embedding
- write_audit_entry(): Audit log writing (JSON + Markdown paths)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (client + messages.create)
  - requests.get / requests.post / requests.put
  - base64.b64decode (selectively)
  - datetime.datetime (for deterministic timestamps)

TODOs:
- TODO: write_audit_entry full integration test requires live OUTPUT_REPO config and git content state
- TODO: call_claude streaming / extended response shapes not covered (needs anthropic SDK internals)
- TODO: send_email retry / back-off behaviour not implemented in source — stub only
"""

import base64
import datetime
import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE importing shared
# ---------------------------------------------------------------------------
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GH_TOKEN", "test-gh-token")
os.environ.setdefault("SENDGRID_API_KEY", "test-sg-key")
os.environ.setdefault("OUTPUT_REPO", "ai-delivery-outputs")
os.environ.setdefault("OUTPUT_REPO_OWNER", "test-owner")
os.environ.setdefault("NOTIFY_EMAIL", "notify@example.com")
os.environ.setdefault("SENDER_EMAIL", "sender@example.com")

# ---------------------------------------------------------------------------
# Stub the `anthropic` package before importing shared so we never hit the
# real SDK during collection.
# ---------------------------------------------------------------------------
anthropic_stub = types.ModuleType("anthropic")
anthropic_stub.Anthropic = MagicMock()
sys.modules.setdefault("anthropic", anthropic_stub)

import importlib
# Force a clean import now that env + stubs are in place
if "shared" in sys.modules:
    del sys.modules["shared"]

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".github", "scripts"))
# Also support running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))

import shared  # noqa: E402  (must come after path manipulation)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

def _make_response(status_code: int = 200, json_data: dict = None, text: str = ""):
    """Build a minimal requests.Response-like mock."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text
    mock_resp.json.return_value = json_data if json_data is not None else {}
    return mock_resp


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

    def test_strips_plain_code_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   {\"key\": \"value\"}   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_multiline_json_in_fence(self):
        raw = "```json\n{\n  \"key\": \"value\",\n  \"num\": 42\n}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["num"] == 42

    def test_no_closing_fence_left_as_is(self):
        """If there's no closing fence the content after opening line is kept."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # rsplit on ``` that doesn't exist returns the whole string — just check it doesn't crash
        assert "{" in result

    def test_already_clean_array(self):
        raw = "[1, 2, 3]"
        assert shared.clean_json(raw) == "[1, 2, 3]"


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_client_mock(self, response_text: str):
        content_block = MagicMock()
        content_block.text = response_text

        message_mock = MagicMock()
        message_mock.content = [content_block]

        client_mock = MagicMock()
        client_mock.messages.create.return_value = message_mock
        return client_mock

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("Hello, world!")
        mock_anthropic_cls.return_value = client_mock

        result = shared.call_claude("sys prompt", "user prompt")
        assert result == "Hello, world!"

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("my system", "my user", max_tokens=512)

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"][0]["content"] == "my user"
        assert kwargs["max_tokens"] == 512

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("s", "u")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_uses_configured_model(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("s", "u")

        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key(self, mock_anthropic_cls):
        client_mock = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client_mock

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        client_mock = MagicMock()
        client_mock.messages.create.side_effect = RuntimeError("API down")
        mock_anthropic_cls.return_value = client_mock

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_large_response_text(self, mock_anthropic_cls):
        big_text = "x" * 100_000
        client_mock = self._make_client_mock(big_text)
        mock_anthropic_cls.return_value = client_mock

        result = shared.call_claude("s", "u")
        assert len(result) == 100_000


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path: str, item_type: str = "blob", url: str = "http://blob-url"):
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, text: str):
        encoded = base64.b64encode(text.encode()).decode()
        return _make_response(json_data={"content": encoded + "\n"})

    @patch("shared.requests.get")
    def test_happy_path_filters_by_extension(self, mock_get):
        tree = [
            self._tree_item("README.md"),
            self._tree_item("main.py"),
            self._tree_item("utils.py"),
            self._tree_item("data.json"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = self._blob_response("file content")

        mock_get.side_effect = [tree_resp, blob_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert set(result.keys()) == {"main.py", "utils.py"}
        assert result["main.py"] == "file content"

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = self._blob_response("content")

        # tree call + up to 3 blob calls
        mock_get.side_effect = [tree_resp] + [blob_resp] * 10

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = [
            {"type": "tree", "path": "src", "url": "u"},
            self._tree_item("main.py"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = self._blob_response("py content")

        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "main.py" in result

    @patch("shared.requests.get")
    def test_empty_tree(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = [
            self._tree_item("a.py"),
            self._tree_item("b.js"),
            self._tree_item("c.txt"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = self._blob_response("data")

        mock_get.side_effect = [tree_resp, blob_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])
        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    @patch("shared.requests.get")
    def test_invalid_base64_skipped(self, mock_get):
        tree = [self._tree_item("bad.py")]
        tree_resp = _make_response(json_data={"tree": tree})
        bad_blob_resp = _make_response(json_data={"content": "!!!not-base64!!!"})

        mock_get.side_effect = [tree_resp, bad_blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        # File with bad content should be silently skipped
        assert "bad.py" not in result

    @patch("shared.requests.get")
    def test_correct_url_construction(self, mock_get):
        tree = [self._tree_item("file.py")]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = self._blob_response("x")

        mock_get.side_effect = [tree_resp, blob_resp]

        shared.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    @patch("shared.requests.get")
    def test_missing_tree_key_returns_empty(self, mock_get):
        mock_get.return_value = _make_response(json_data={})
        result = shared.get_repo_files("o", "r", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_utf8_decoding(self, mock_get):
        arabic = "مرحبا"
        tree = [self._tree_item("ar.json")]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = self._blob_response(arabic)

        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("o", "r", [".json"])
        assert result["ar.json"] == arabic


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    @patch("shared.requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_get.return_value = _make_response(text="diff --git a/f b/f\n+new line")
        result = shared.get_pr_diff("owner", "repo", 42)
        assert "diff" in result

    @patch("shared.requests.get")
    def test_truncates_to_30000_chars(self, mock_get):
        long_diff = "x" * 50_000
        mock_get.return_value = _make_response(text=long_diff)
        result = shared.get_pr_diff("owner", "repo", 1)
        assert len(result) == 30_000

    @patch("shared.requests.get")
    def test_short_diff_not_truncated(self, mock_get):
        short_diff = "short diff"
        mock_get.return_value =