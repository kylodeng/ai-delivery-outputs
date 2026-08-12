"""
Test module for .github/scripts/shared.py

What is tested:
    - call_claude(): Claude API interaction, response extraction
    - clean_json(): Markdown fence stripping, edge cases
    - get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, b64 decode
    - get_pr_diff(): PR diff fetching, truncation at 30000 chars
    - write_output_file(): Create new file, update existing file (with SHA), fallback URL
    - post_pr_comment(): PR comment posting
    - send_email(): SendGrid payload construction, success/failure status codes
    - email_html(): HTML output generation, status colour logic
    - write_audit_entry(): (stub – source truncated in provided code)

Mocks used:
    - unittest.mock.patch / MagicMock for:
        - anthropic.Anthropic (Claude client)
        - requests.get
        - requests.post
        - requests.put
        - os.environ (via monkeypatch)

TODOs:
    - write_audit_entry(): Source code is truncated; full coverage requires complete implementation
    - MODEL constant: test may need updating if model name changes
    - GH_HEADERS: token value tested indirectly via env var injection
"""

import base64
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------
_ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-gh-owner",
}

for _k, _v in _ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

# Provide a stub for the `anthropic` package so tests run without the real SDK
if "anthropic" not in sys.modules:
    _anthropic_stub = types.ModuleType("anthropic")
    _anthropic_stub.Anthropic = MagicMock()
    sys.modules["anthropic"] = _anthropic_stub

# Now import the module under test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

import shared  # noqa: E402  (import after env setup)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    """Return base-64 encoded version of *text* (as the GitHub API would)."""
    return base64.b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_fence(self):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   \n{\"x\": 2}\n   "
        assert shared.clean_json(raw) == '{"x": 2}'

    def test_fence_with_extra_whitespace_inside(self):
        raw = "```json\n  {\"k\": \"v\"}  \n```"
        result = shared.clean_json(raw)
        assert result == '{"k": "v"}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_fence_with_no_closing_backticks(self):
        raw = "```json\n{\"k\": \"v\"}"
        # rsplit on ``` finds nothing extra; should still strip opening line
        result = shared.clean_json(raw)
        assert '{"k": "v"}' in result

    def test_valid_json_array(self):
        raw = "```json\n[1,2,3]\n```"
        assert shared.clean_json(raw) == "[1,2,3]"

    def test_multiline_json(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def _make_mock_client(self, text: str):
        mock_content = MagicMock()
        mock_content.text = text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_returns_text_content(self):
        mock_client = self._make_mock_client("Hello from Claude")
        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys prompt", "user prompt")
        assert result == "Hello from Claude"

    def test_passes_correct_model(self):
        mock_client = self._make_mock_client("ok")
        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self):
        mock_client = self._make_mock_client("ok")
        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my-system", "my-user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my-system"
        assert kwargs["messages"] == [{"role": "user", "content": "my-user"}]

    def test_default_max_tokens(self):
        mock_client = self._make_mock_client("ok")
        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self):
        mock_client = self._make_mock_client("ok")
        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u", max_tokens=1000)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1000

    def test_uses_api_key_from_env(self):
        mock_client = self._make_mock_client("ok")
        with patch("shared.anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("s", "u")
        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_propagates_api_exception(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                shared.call_claude("s", "u")


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _tree_response(self, items):
        return MagicMock(json=MagicMock(return_value={"tree": items}))

    def _blob_response(self, content: str):
        return MagicMock(json=MagicMock(return_value={"content": _b64(content)}))

    def test_returns_matching_files(self):
        tree = [
            {"type": "blob", "path": "README.md", "url": "http://gh/blob/readme"},
            {"type": "blob", "path": "main.py",   "url": "http://gh/blob/main"},
        ]
        responses = [
            self._tree_response(tree),
            self._blob_response("# README content"),
            self._blob_response("print('hello')"),
        ]
        with patch("shared.requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".md", ".py"])
        assert "README.md" in result
        assert "main.py" in result
        assert result["README.md"] == "# README content"

    def test_filters_by_extension(self):
        tree = [
            {"type": "blob", "path": "README.md",  "url": "http://gh/blob/readme"},
            {"type": "blob", "path": "script.sh",  "url": "http://gh/blob/sh"},
        ]
        responses = [
            self._tree_response(tree),
            self._blob_response("# only md"),
        ]
        with patch("shared.requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".md"])
        assert "README.md" in result
        assert "script.sh" not in result

    def test_skips_non_blob_items(self):
        tree = [
            {"type": "tree", "path": "src",        "url": "http://gh/tree/src"},
            {"type": "blob", "path": "main.py",    "url": "http://gh/blob/main"},
        ]
        responses = [
            self._tree_response(tree),
            self._blob_response("code"),
        ]
        with patch("shared.requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "main.py" in result

    def test_respects_max_files(self):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://gh/blob/{i}"}
            for i in range(5)
        ]
        blob_responses = [self._blob_response(f"content{i}") for i in range(5)]
        responses = [self._tree_response(tree)] + blob_responses
        with patch("shared.requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    def test_empty_tree(self):
        with patch("shared.requests.get", return_value=self._tree_response([])):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_handles_decode_error_gracefully(self):
        tree = [
            {"type": "blob", "path": "binary.py", "url": "http://gh/blob/binary"},
        ]
        bad_blob = MagicMock(json=MagicMock(return_value={"content": "!!!not-base64!!!"}))
        responses = [self._tree_response(tree), bad_blob]
        with patch("shared.requests.get", side_effect=responses):
            # Should not raise; bad file is silently skipped
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "binary.py" not in result

    def test_missing_tree_key_returns_empty(self):
        mock_resp = MagicMock(json=MagicMock(return_value={}))
        with patch("shared.requests.get", return_value=mock_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_correct_url_constructed(self):
        mock_tree = MagicMock(json=MagicMock(return_value={"tree": []}))
        with patch("shared.requests.get", return_value=mock_tree) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])
        call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in call_url
        assert "myrepo" in call_url
        assert "recursive=1" in call_url

    def test_multiple_extensions(self):
        tree = [
            {"type": "blob", "path": "a.json", "url": "http://gh/blob/a"},
            {"type": "blob", "path": "b.yaml", "url": "http://gh/blob/b"},
            {"type": "blob", "path": "c.txt",  "url": "http://gh/blob/c"},
        ]
        responses = [
            self._tree_response(tree),
            self._blob_response('{"k":"v"}'),
            self._blob_response("key: val"),
        ]
        with patch("shared.requests.get", side_effect=responses):
            result = shared.get_repo_files("o", "r", [".json", ".yaml"])
        assert "a.json" in result
        assert "b.yaml" in result
        assert "c.txt" not in result


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------

class TestGetPrDiff:
    def test_returns_diff_text(self):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/file b/file\n+added line"
        with patch("shared.requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 42)
        assert "diff --git" in result

    def test_truncates_to_30000_chars(self):
        long_diff = "x" * 50000
        mock_resp = MagicMock()
        mock_resp.text = long_diff
        with patch("shared.requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)
        assert len(result) == 30000

    def test_short_diff_not_truncated(self):
        short_diff = "small diff"
        mock_resp = MagicMock()
        mock_resp.text = short_diff
        with patch("shared.requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)
        assert result == short_diff

    def test_uses_diff_accept_header(self):
        mock_resp = MagicMock()
        mock_resp.text = ""
        with patch("shared.requests.get", return_value=mock_resp) as mock_get:
            shared.get_pr_diff("owner", "repo", 7)
        headers_used = mock_get.call_args[1]["headers"]
        assert headers_used["Accept"] == "application/vnd.github.diff"

    def test_correct_pr_url(self):
        mock_resp = MagicMock()
        mock_resp.text = ""
        with patch("shared.requests.get", return_value=mock_resp) as mock_get:
            shared.get_pr_diff("my