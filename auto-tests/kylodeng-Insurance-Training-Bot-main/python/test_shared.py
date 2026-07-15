"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: happy path, API response parsing
- clean_json: markdown fence stripping, edge cases, already-clean JSON
- get_repo_files: filtering by extension, max_files limit, base64 decoding, error handling
- get_pr_diff: URL construction, truncation at 30000 chars, header overriding
- write_output_file: create new file (no sha), update existing file (with sha), fallback URL
- post_pr_comment: correct URL and payload construction
- send_email: success (200/202), failure warning path
- email_html: HTML structure, SUCCESS/FAILURE color coding, content inclusion
- write_audit_entry: (stub — requires full source; see TODO)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for base64.b64decode (selective)

TODOs:
- write_audit_entry: source is truncated; full implementation needed to test completely
- MODEL constant: test that it matches expected value if it becomes configurable
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to load the module with mandatory env vars injected
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


def _load_shared(extra_env: dict = None):
    """Import (or re-import) shared with env vars patched."""
    env = {**REQUIRED_ENV, **(extra_env or {})}
    # Remove cached module so we get a fresh import with the patched env
    sys.modules.pop("shared", None)

    # anthropic may not be installed in the test environment; stub it
    if "anthropic" not in sys.modules:
        anthropic_stub = types.ModuleType("anthropic")
        anthropic_stub.Anthropic = MagicMock()
        sys.modules["anthropic"] = anthropic_stub

    with patch.dict("os.environ", env, clear=False):
        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", ".github/scripts/shared.py"
        )
        module = importlib.util.module_from_spec(spec)
        # Patch os.environ inside the module namespace before exec
        with patch.dict("os.environ", env, clear=False):
            spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shared():
    """Module-level fixture — loads shared once per test session."""
    return _load_shared()


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------


class TestCallClaude:
    def test_happy_path_returns_text(self, shared):
        mock_text = "This is Claude's response"
        mock_content = MagicMock()
        mock_content.text = mock_text
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            # Re-bind the patched Anthropic into the module under test
            shared.anthropic.Anthropic = MagicMock(return_value=mock_client)
            result = shared.call_claude("system prompt", "user prompt")

        assert result == mock_text

    def test_passes_correct_parameters(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        shared.anthropic.Anthropic = MagicMock(return_value=mock_client)

        shared.call_claude("sys", "usr", max_tokens=1024)

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=1024,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    def test_default_max_tokens_is_4096(self, shared):
        mock_content = MagicMock()
        mock_content.text = "response"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        shared.anthropic.Anthropic = MagicMock(return_value=mock_client)

        shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_uses_api_key_from_env(self, shared):
        mock_content = MagicMock()
        mock_content.text = "x"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls = MagicMock(return_value=mock_client)
        shared.anthropic.Anthropic = mock_anthropic_cls

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------


class TestCleanJson:
    def test_no_fences_returns_as_is(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fenced_block(self, shared):
        raw = '```json\n{"key": "value"}\n```'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_plain_code_fence(self, shared):
        raw = '```\n{"key": "value"}\n```'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = '   \n{"key": "value"}\n   '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_fenced_with_extra_whitespace(self, shared):
        raw = '```json\n  {"a": 1}  \n```'
        result = shared.clean_json(raw)
        assert result.strip() == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_fence_markers(self, shared):
        raw = "```\n```"
        # After processing: drop first line → "", rsplit on ``` → ["", ""]
        result = shared.clean_json(raw)
        assert isinstance(result, str)

    def test_nested_content_preserved(self, shared):
        inner = '{"data": [1, 2, 3], "nested": {"a": true}}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    def test_already_valid_json_array(self, shared):
        raw = '[1, 2, 3]'
        assert shared.clean_json(raw) == '[1, 2, 3]'

    def test_multiline_json_in_fence(self, shared):
        raw = '```json\n{\n  "key": "value"\n}\n```'
        result = shared.clean_json(raw)
        assert '"key"' in result
        assert "```" not in result


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------


class TestGetRepoFiles:
    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, text):
        encoded = base64.b64encode(text.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_happy_path_fetches_matching_files(self, shared):
        tree = [
            {"type": "blob", "path": "src/main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "src/utils.py", "url": "https://api.github.com/blob/2"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob1 = self._make_blob_response("print('hello')")
        blob2 = self._make_blob_response("def util(): pass")

        with patch("requests.get", side_effect=[tree_resp, blob1, blob2]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert result["src/main.py"] == "print('hello')"

    def test_filters_by_extension(self, shared):
        tree = [
            {"type": "blob", "path": "README.md", "url": "u1"},
            {"type": "blob", "path": "main.py", "url": "u2"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob_py = self._make_blob_response("# python")

        with patch("requests.get", side_effect=[tree_resp, blob_py]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "README.md" not in result

    def test_max_files_limit(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"u{i}"}
            for i in range(10)
        ]
        tree_resp = self._make_tree_response(tree)
        blob_resps = [self._make_blob_response(f"content{i}") for i in range(3)]

        with patch("requests.get", side_effect=[tree_resp] + blob_resps):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "src", "url": "u1"},
            {"type": "blob", "path": "main.py", "url": "u2"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob = self._make_blob_response("code")

        with patch("requests.get", side_effect=[tree_resp, blob]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_empty_tree_returns_empty_dict(self, shared):
        tree_resp = self._make_tree_response([])

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "main.py", "url": "u1"},
            {"type": "blob", "path": "app.js", "url": "u2"},
            {"type": "blob", "path": "data.csv", "url": "u3"},
        ]
        tree_resp = self._make_tree_response(tree)
        blob1 = self._make_blob_response("python")
        blob2 = self._make_blob_response("javascript")

        with patch("requests.get", side_effect=[tree_resp, blob1, blob2]):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "main.py" in result
        assert "app.js" in result
        assert "data.csv" not in result

    def test_handles_decode_exception_gracefully(self, shared):
        tree = [{"type": "blob", "path": "bad.py", "url": "u1"}]
        tree_resp = self._make_tree_response(tree)

        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "!!!invalid-base64!!!"}

        with patch("requests.get", side_effect=[tree_resp, bad_blob]):
            # Should not raise; bad file is silently skipped
            result = shared.get_repo_files("owner", "repo", [".py"])

        # bad file may or may not be included depending on error, but no exception
        assert isinstance(result, dict)

    def test_constructs_correct_tree_url(self, shared):
        tree_resp = self._make_tree_response([])

        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_uses_gh_headers(self, shared):
        tree_resp = self._make_tree_response([])

        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("owner", "repo", [".py"])

        headers = mock_get.call_args_list[0][1]["headers"]
        assert "Authorization" in headers
        assert "Bearer test-gh-token" in headers["Authorization"]


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------


class TestGetPrDiff:
    def test_happy_path_returns_diff_text(self, shared):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/file.py b/file.py\n+new line"

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result

    def test_truncates_to_30000_chars(self, shared):
        long_diff = "x" * 50000
        mock_resp = MagicMock()
        mock_resp.text = long_diff

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_short_diff_not_truncated(self, shared):
        short_diff = "short diff"
        mock_resp = MagicMock()
        mock_resp.text = short_diff

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)

        assert result