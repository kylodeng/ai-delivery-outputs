"""
Tests for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration
- clean_json(): Markdown fence stripping utility
- get_repo_files(): GitHub API file fetching with extension filtering
- get_pr_diff(): GitHub API PR diff fetching
- write_output_file(): GitHub API file create/update
- post_pr_comment(): GitHub API PR comment posting
- send_email(): SendGrid email sending
- email_html(): HTML email body generation
- write_audit_entry(): Audit log writing (partial - see TODOs)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for datetime.datetime to freeze timestamps

TODOs:
- TODO: write_audit_entry full integration test requires OUTPUT_REPO_OWNER/OUTPUT_REPO env and
        a running stub for write_output_file + base64 content decoding of existing audit files.
- TODO: call_claude() test for network timeout / connection error from anthropic SDK.
- TODO: get_repo_files() test for repos with > max_files blobs (pagination edge case).
"""

import base64
import json
import sys
import types
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: inject required environment variables BEFORE importing shared.py
# ---------------------------------------------------------------------------
_ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

# Patch environment before any import of the module under test
with mock.patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
    # Stub out the `anthropic` package so import succeeds without the real SDK
    _anthropic_stub = types.ModuleType("anthropic")

    class _FakeAnthropicClient:
        def __init__(self, **kwargs):
            pass

    _anthropic_stub.Anthropic = _FakeAnthropicClient
    sys.modules.setdefault("anthropic", _anthropic_stub)

    import importlib
    if "shared" in sys.modules:
        shared = importlib.reload(sys.modules["shared"])
    else:
        import importlib.util, pathlib
        _spec = importlib.util.spec_from_file_location(
            "shared", pathlib.Path(__file__).parent.parent / ".github/scripts/shared.py"
        )
        shared = importlib.util.module_from_spec(_spec)
        sys.modules["shared"] = shared
        _spec.loader.exec_module(shared)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_response(status_code=200, json_data=None, text=""):
    """Build a minimal requests.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_triple_backtick_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_triple_backtick_no_language(self):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = "   \n{\"x\": true}\n   "
        assert shared.clean_json(raw) == '{"x": true}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_strips_fence_with_extra_whitespace(self):
        raw = "```json\n  { \"k\": 99 }  \n```"
        result = shared.clean_json(raw)
        assert result.strip() == '{ "k": 99 }'

    def test_nested_backticks_inside_json_preserved(self):
        # Only the outer fences are removed; internal content is untouched
        raw = "```json\n{\"code\": \"x = `y`\"}\n```"
        result = shared.clean_json(raw)
        assert '`y`' in result

    @pytest.mark.parametrize("raw,expected", [
        ('{"a": 1}', '{"a": 1}'),
        ('```\n[]\n```', '[]'),
        ('  ```json\n{"b":2}\n```  ', '{"b":2}'),
    ])
    def test_parametrized_cases(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_claude_response(self, text="Hello from Claude"):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("Test response")

        result = shared.call_claude("system prompt", "user message")

        assert result == "Test response"

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response()

        shared.call_claude("sys", "usr", max_tokens=2048)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL
        assert kwargs["max_tokens"] == 2048

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user_messages(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response()

        shared.call_claude("be helpful", "what is 2+2?")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "be helpful"
        assert kwargs["messages"] == [{"role": "user", "content": "what is 2+2?"}]

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens_is_4096(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response()

        shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_uses_anthropic_api_key_from_env(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response()

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _blob_item(self, path, url="https://api.github.com/blob/abc"):
        return {"type": "blob", "path": path, "url": url}

    def _tree_item(self, path):
        return {"type": "tree", "path": path, "url": ""}

    def _make_blob_content(self, text):
        encoded = base64.b64encode(text.encode()).decode()
        return {"content": encoded + "\n"}  # GitHub adds newline

    @patch("shared.requests.get")
    def test_returns_files_matching_extensions(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._blob_item("src/main.py"),
            self._blob_item("src/utils.py"),
            self._blob_item("README.md"),
        ]})
        blob_py = _make_response(json_data=self._make_blob_content("print('hello')"))

        mock_get.side_effect = [tree_resp, blob_py, blob_py]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_respects_max_files_limit(self, mock_get):
        paths = [f"file{i}.py" for i in range(10)]
        tree_resp = _make_response(json_data={"tree": [self._blob_item(p) for p in paths]})
        blob = _make_response(json_data=self._make_blob_content("code"))

        mock_get.side_effect = [tree_resp] + [blob] * 3  # only 3 blob fetches expected

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_tree_type_items(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._tree_item("src/"),
            self._blob_item("src/file.py"),
        ]})
        blob = _make_response(json_data=self._make_blob_content("data"))
        mock_get.side_effect = [tree_resp, blob]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/" not in result
        assert "src/file.py" in result

    @patch("shared.requests.get")
    def test_empty_repo_returns_empty_dict(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_handles_missing_tree_key(self, mock_get):
        mock_get.return_value = _make_response(json_data={})

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_handles_blob_decode_exception_gracefully(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._blob_item("bad.py"),
            self._blob_item("good.py"),
        ]})
        bad_blob = _make_response(json_data={"content": "!!!not-valid-base64!!!"})
        good_blob = _make_response(json_data=self._make_blob_content("valid content"))

        mock_get.side_effect = [tree_resp, bad_blob, good_blob]

        result = shared.get_repo_files("owner", "repo", [".py"])
        # bad file skipped, good file present
        assert "good.py" in result

    @patch("shared.requests.get")
    def test_multiple_extensions_filter(self, mock_get):
        tree_resp = _make_response(json_data={"tree": [
            self._blob_item("a.py"),
            self._blob_item("b.js"),
            self._blob_item("c.go"),
            self._blob_item("d.txt"),
        ]})
        blob = _make_response(json_data=self._make_blob_content("content"))
        mock_get.side_effect = [tree_resp, blob, blob, blob]

        result = shared.get_repo_files("owner", "repo", [".py", ".js", ".go"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.go" in result
        assert "d.txt" not in result

    @patch("shared.requests.get")
    def test_calls_correct_tree_url(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})

        shared.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo/git/trees/HEAD" in first_call_url
        assert "recursive=1" in first_call_url

    @patch("shared.requests.get")
    def test_decodes_utf8_content_correctly(self, mock_get):
        text = "# Model: Underwriting Risk Classification\nAge: 34.57"
        tree_resp = _make_response(json_data={"tree": [self._blob_item("model_card.py")]})
        blob = _make_response(json_data=self._make_blob_content(text))
        mock_get.side_effect = [tree_resp, blob]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result["model_card.py"] == text


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    @patch("shared.requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_get.return_value = _make_response(text="diff --git a/f.py b/f.py\n+new line")

        result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result

    @patch("shared.requests.get")
    def test_truncates_at_30000_chars(self, mock_get):
        long_diff = "x" * 50000
        mock_get.return_value = _make_response(text=long_diff)

        result = shared.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    @patch("shared.requests.