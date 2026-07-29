"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): file create (no SHA) and update (with SHA) paths
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success and failure paths
- email_html(): HTML generation, status colour logic
- write_audit_entry(): audit log construction (partial — source truncated)

Mocks used:
- unittest.mock.patch / MagicMock for: anthropic.Anthropic, requests.get, requests.post, requests.put
- os.environ patched via monkeypatch / patch.dict so module-level constants are stable

TODOs:
- write_audit_entry(): source code is truncated; full behaviour (JSON + Markdown append) cannot be
  fully verified without seeing the rest of the function. Stub tests are marked with pytest.mark.skip.
- call_claude() streaming / multi-content-block responses: needs real API contract knowledge.
- email_html() datetime: frozen-time library would make the timestamp assertion exact.
"""

import base64
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without real env vars or the
# real `anthropic` package being present.
# ---------------------------------------------------------------------------

# Stub the anthropic package before importing shared.py
anthropic_stub = types.ModuleType("anthropic")


class _FakeAnthropicClient:
    def __init__(self, api_key=None):
        self.messages = self

    def create(self, **kwargs):
        raise NotImplementedError("stub – patch this in tests")


anthropic_stub.Anthropic = _FakeAnthropicClient
sys.modules.setdefault("anthropic", anthropic_stub)

# Provide required env vars before the module is imported
_REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}

with patch.dict(os.environ, _REQUIRED_ENV, clear=False):
    import importlib, importlib.util, pathlib

    _spec = importlib.util.spec_from_file_location(
        "shared",
        pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
    )
    shared = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(shared)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stable_env(monkeypatch):
    """Ensure module-level constants remain predictable across tests."""
    for k, v in _REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)


def _make_response(status_code=200, json_data=None, text=""):
    """Build a minimal requests.Response-like mock."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text
    mock_resp.json.return_value = json_data if json_data is not None else {}
    return mock_resp


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------


class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_triple_backtick_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_triple_backtick_fence(self):
        raw = "```\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_strips_leading_and_trailing_whitespace(self):
        raw = "   \n  {\"a\": 1}  \n  "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_fence_markers(self):
        raw = "```json\n```"
        result = shared.clean_json(raw)
        # After stripping the opening line we get "```" then rsplit removes it → empty
        assert result == ""

    def test_no_closing_fence(self):
        """If there is no closing fence, rsplit leaves content intact."""
        raw = "```json\n{\"x\": 1}"
        result = shared.clean_json(raw)
        assert result == '{"x": 1}'

    def test_multiline_json_preserved(self):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    def test_nested_backtick_content_not_over_stripped(self):
        """Only the outermost fence pair should be removed."""
        raw = "```json\n{\"code\": \"x = `hello`\"}\n```"
        result = shared.clean_json(raw)
        assert "hello" in result


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------


class TestCallClaude:
    def _make_claude_response(self, text="Hello from Claude"):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    @patch("anthropic.Anthropic")
    def test_returns_first_content_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("Test response")

        result = shared.call_claude("sys", "user msg")

        assert result == "Test response"

    @patch("anthropic.Anthropic")
    def test_passes_correct_model_and_params(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response()

        shared.call_claude("system prompt", "user prompt", max_tokens=1024)

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=1024,
            system="system prompt",
            messages=[{"role": "user", "content": "user prompt"}],
        )

    @patch("anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response()

        shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response()

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("anthropic.Anthropic")
    def test_propagates_api_exception(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            shared.call_claude("s", "u")


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------


class TestGetRepoFiles:
    def _tree_item(self, path, item_type="blob", url="https://api.github.com/blobs/abc"):
        return {"path": path, "type": item_type, "url": url}

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return {"content": encoded}

    @patch("requests.get")
    def test_fetches_files_matching_extension(self, mock_get):
        tree = [
            self._tree_item("src/main.py"),
            self._tree_item("README.md"),
            self._tree_item("src/utils.py"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("print('main')")),
            _make_response(json_data=self._blob_response("print('utils')")),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "README.md" not in result
        assert result["src/main.py"] == "print('main')"

    @patch("requests.get")
    def test_respects_max_files_limit(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        blob_resp = _make_response(json_data=self._blob_response("content"))
        mock_get.return_value = MagicMock(
            json=MagicMock(side_effect=[{"tree": tree}] + [self._blob_response("content")] * 10)
        )

        # Rebuild side_effect properly
        mock_get.side_effect = [_make_response(json_data={"tree": tree})] + [
            _make_response(json_data=self._blob_response("content")) for _ in range(5)
        ]

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    @patch("requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = [
            self._tree_item("src", item_type="tree"),
            self._tree_item("src/main.py"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("code")),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert list(result.keys()) == ["src/main.py"]

    @patch("requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_multiple_extensions_filter(self, mock_get):
        tree = [
            self._tree_item("a.py"),
            self._tree_item("b.js"),
            self._tree_item("c.txt"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("py content")),
            _make_response(json_data=self._blob_response("js content")),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])
        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    @patch("requests.get")
    def test_blob_decode_failure_skips_file(self, mock_get):
        tree = [self._tree_item("bad.py")]
        # Return blob without 'content' key to trigger the except branch
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data={}),  # missing 'content' key
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_constructs_correct_tree_url(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        shared.get_repo_files("my-owner", "my-repo", [".py"])
        called_url = mock_get.call_args[0][0]
        assert "my-owner/my-repo/git/trees/HEAD?recursive=1" in called_url

    @patch("requests.get")
    def test_uses_gh_headers(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        shared.get_repo_files("owner", "repo", [".py"])
        called_headers = mock_get.call_args[1]["headers"]
        assert "Authorization" in called_headers
        assert called_headers["Authorization"] == f"Bearer test-gh-token"


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------


class TestGetPrDiff:
    @patch("requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_get.return_value = _make_response(text="--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new")
        result = shared.get_pr_diff("owner", "repo", 42)
        assert "--- a/file" in result

    @patch("requests.get")
    def test_truncates_to_30000_chars(self, mock_get):
        long_diff = "x" * 50000
        mock_get.return_value = _make_response(text=long_diff)
        result = shared.get_pr_diff("owner", "repo", 1)
        assert len(result) == 30000

    @patch("requests.get")
    def test_constructs_correct_url(self, mock_get):
        mock_get.return_value = _make_response(text="diff")
        shared.get_pr_diff("acme", "my-repo", 99)
        called_url = mock_get.call_args[0][0]
        assert called_url == f"{shared.GH_API}/repos/acme/my-repo/pulls/99"

    @patch("requests.get")
    def test_uses_diff_accept_header(self, mock_get):
        mock_get.return_value = _make_response(text="")
        shared.get_pr_diff("owner", "repo", 1)
        headers = mock_get.call_args[1]["headers"]
        assert headers.get("Accept") == "application/vnd.github.diff"

    @patch("requests.get")
    def test_empty_diff(self, mock_get):
        mock_get.return_value =