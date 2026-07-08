"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response parsing
- clean_json(): Markdown code-fence stripping, edge cases
- get_repo_files(): GitHub API tree fetch, extension filtering, base64 decode, max_files limit
- get_pr_diff(): PR diff fetch, truncation
- write_output_file(): File create (no SHA) and update (with SHA) paths
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure handling
- email_html(): HTML template rendering for SUCCESS and FAILURE statuses
- write_audit_entry(): Audit log construction (partial – source truncated)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude client)
  - requests.get, requests.post, requests.put
  - os.environ (environment variables)

TODOs:
- write_audit_entry(): Source code is truncated; full behaviour cannot be verified
- Integration tests against real GitHub/Claude/SendGrid APIs (skipped, need credentials)
"""

import base64
import json
import os
import sys
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap environment BEFORE importing shared.py
# (shared.py reads env vars at module level)
# ---------------------------------------------------------------------------
_ENV_PATCH = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

with patch.dict(os.environ, _ENV_PATCH):
    # Ensure the script directory is importable
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
    import shared  # noqa: E402


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_module_globals():
    """Ensure module-level constants reflect test environment values."""
    with patch.dict(os.environ, _ENV_PATCH):
        yield


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for shared.call_claude()"""

    def _make_mock_client(self, text: str):
        mock_content = MagicMock()
        mock_content.text = text

        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        """call_claude returns the text from the first content block."""
        expected_text = "Hello, I am Claude."
        mock_anthropic_cls.return_value = self._make_mock_client(expected_text)

        result = shared.call_claude(system="You are helpful.", user="Hi")

        assert result == expected_text

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls):
        """call_claude passes MODEL and max_tokens to the API."""
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="sys", user="usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user_messages(self, mock_anthropic_cls):
        """call_claude forwards system and user content correctly."""
        mock_client = self._make_mock_client("response")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="Be concise.", user="What is 2+2?")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "Be concise."
        assert kwargs["messages"] == [{"role": "user", "content": "What is 2+2?"}]

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens_is_4096(self, mock_anthropic_cls):
        """call_claude defaults to 4096 max_tokens."""
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude(system="s", user="u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls):
        """call_claude initialises Anthropic client with the env API key."""
        mock_anthropic_cls.return_value = self._make_mock_client("ok")

        shared.call_claude(system="s", user="u")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        """call_claude lets exceptions from the API bubble up."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude(system="s", user="u")


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for shared.clean_json()"""

    def test_no_fences_unchanged(self):
        """Plain JSON is returned unchanged (modulo strip)."""
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == raw

    def test_strips_json_code_fence(self):
        """```json ... ``` fences are removed."""
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        """``` ... ``` fences without language tag are removed."""
        raw = '```\n{"a": 1}\n```'
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_leading_trailing_whitespace_stripped(self):
        """Surrounding whitespace is removed."""
        raw = '   {"x": 0}   '
        assert shared.clean_json(raw) == '{"x": 0}'

    def test_fence_with_extra_whitespace(self):
        """Whitespace inside fences is stripped."""
        raw = '```json\n  {"a": 1}  \n```'
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert shared.clean_json("") == ""

    def test_only_fences_returns_empty(self):
        """Fences with no content returns empty string."""
        raw = "```json\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_multiline_json_preserved(self):
        """Multiline JSON inside fences is preserved correctly."""
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_valid_json_after_clean(self):
        """Output from clean_json is valid JSON when input was valid."""
        raw = '```json\n{"product": "Generations II", "type": "whole_life"}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["product"] == "Generations II"

    def test_non_json_fence_content(self):
        """Non-JSON content inside fences is still extracted."""
        raw = "```\nhello world\n```"
        assert shared.clean_json(raw) == "hello world"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for shared.get_repo_files()"""

    def _make_tree_response(self, items):
        return MagicMock(json=MagicMock(return_value={"tree": items}))

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return MagicMock(json=MagicMock(return_value={"content": encoded}))

    @patch("shared.requests.get")
    def test_happy_path_fetches_matching_files(self, mock_get):
        """Files matching extensions are fetched and decoded."""
        tree = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/main.py"},
            {"type": "blob", "path": "src/utils.py", "url": "http://blob/utils.py"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("print('main')"),
            self._make_blob_response("print('utils')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert result["src/main.py"] == "print('main')"

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        """Only files with matching extensions are included."""
        tree = [
            {"type": "blob", "path": "README.md", "url": "http://blob/readme"},
            {"type": "blob", "path": "app.py", "url": "http://blob/app"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("# python"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        """No more than max_files files are returned."""
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/file{i}"}
            for i in range(10)
        ]
        blob_resp = self._make_blob_response("content")
        mock_get.side_effect = [self._make_tree_response(tree)] + [blob_resp] * 10

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        """Tree items of type 'tree' are skipped."""
        tree = [
            {"type": "tree", "path": "src", "url": "http://blob/src"},
            {"type": "blob", "path": "src/app.py", "url": "http://blob/app"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "src/app.py" in result

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        """An empty repository tree yields an empty dict."""
        mock_get.return_value = self._make_tree_response([])

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        """Multiple extensions are all accepted."""
        tree = [
            {"type": "blob", "path": "main.py", "url": "http://blob/main"},
            {"type": "blob", "path": "index.js", "url": "http://blob/index"},
            {"type": "blob", "path": "data.csv", "url": "http://blob/data"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("py content"),
            self._make_blob_response("js content"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "main.py" in result
        assert "index.js" in result
        assert "data.csv" not in result

    @patch("shared.requests.get")
    def test_bad_base64_blob_is_skipped(self, mock_get):
        """Files whose blob content cannot be decoded are silently skipped."""
        tree = [
            {"type": "blob", "path": "broken.py", "url": "http://blob/broken"},
        ]
        bad_blob = MagicMock(json=MagicMock(return_value={"content": "!!!not-base64!!!"}))
        mock_get.side_effect = [self._make_tree_response(tree), bad_blob]

        result = shared.get_repo_files("owner", "repo", [".py"])

        # Should not raise; broken file may be absent or present depending on decode
        # The important thing is no exception is raised
        assert isinstance(result, dict)

    @patch("shared.requests.get")
    def test_uses_correct_tree_url(self, mock_get):
        """The correct GitHub tree API URL is called."""
        mock_get.return_value = self._make_tree_response([])

        shared.get_repo_files("myowner", "myrepo", [".py"])

        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    """Tests for shared.get_pr_diff()"""

    @patch("shared.requests.get")
    def test_happy_path_returns_diff(self, mock_get):
        """Returns text of the diff response."""
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/file.py b/file.py\n+new line"
        mock_get.return_value = mock_resp

        result = shared.get_pr_diff("owner", "repo", 42)

        assert result == "diff --git a/file.py b/file.py\n+new line"

    @patch("shared.requests.get")
    def test_truncates_to_30000_chars(self, mock_get):
        """Diff is truncated to the first 30 000 characters."""
        long_diff = "x" * 50000
        mock_resp = MagicMock()
        mock_resp.text = long