"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response parsing
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree traversal, extension filtering, base64 decoding, max_files limit
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File creation (no SHA) and update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid integration, success/failure status codes
- email_html(): HTML template generation, SUCCESS/FAILURE coloring
- write_audit_entry(): Audit log JSON/Markdown writes (stub — requires full source)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude API client)
  - requests.get, requests.post, requests.put (all HTTP calls)
  - os.environ (environment variables injected via monkeypatch)

TODOs:
- TODO: write_audit_entry() source is truncated — full implementation needed for complete tests
- TODO: Integration tests for real GitHub API calls (require live GH_TOKEN)
- TODO: Integration tests for real SendGrid calls (require live SENDGRID_API_KEY)
- TODO: Integration tests for real Claude API (require live ANTHROPIC_API_KEY)
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE importing shared.py
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

for _k, _v in _ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

# Now safe to import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
import shared  # noqa: E402


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_module_globals():
    """Ensure module-level GH_HEADERS is consistent for every test."""
    yield


@pytest.fixture()
def mock_anthropic_client():
    """Return a fully-mocked anthropic.Anthropic client."""
    with patch("shared.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        msg = MagicMock()
        msg.content = [MagicMock(text="Hello from Claude")]
        instance.messages.create.return_value = msg
        yield MockClient, instance


@pytest.fixture()
def mock_requests_get():
    with patch("shared.requests.get") as mock_get:
        yield mock_get


@pytest.fixture()
def mock_requests_post():
    with patch("shared.requests.post") as mock_post:
        yield mock_post


@pytest.fixture()
def mock_requests_put():
    with patch("shared.requests.put") as mock_put:
        yield mock_put


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = '   {"key": "value"}   '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_extra_whitespace(self):
        raw = '```json\n\n{"a": 1}\n\n```'
        result = shared.clean_json(raw)
        # After stripping fences the inner content should be parseable
        assert json.loads(result) == {"a": 1}

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_fence_only_opening(self):
        """Only an opening fence — no closing backticks."""
        raw = "```json\n{}"
        result = shared.clean_json(raw)
        # rsplit on missing ``` keeps string intact after stripping header
        assert "{}" in result

    def test_nested_json_string(self):
        inner = '{"product_name": "Generations II", "doc_type": "product_brochure"}'
        raw = f"```json\n{inner}\n```"
        assert json.loads(shared.clean_json(raw)) == {
            "product_name": "Generations II",
            "doc_type": "product_brochure",
        }

    def test_multiline_json(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        assert json.loads(shared.clean_json(raw)) == {"a": 1, "b": 2}

    def test_already_stripped(self):
        raw = '{"status": "ok"}'
        assert shared.clean_json(raw) == raw


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def test_happy_path_returns_text(self, mock_anthropic_client):
        MockClient, instance = mock_anthropic_client
        result = shared.call_claude("system prompt", "user prompt")
        assert result == "Hello from Claude"

    def test_passes_correct_model(self, mock_anthropic_client):
        _, instance = mock_anthropic_client
        shared.call_claude("sys", "usr")
        _, kwargs = instance.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self, mock_anthropic_client):
        _, instance = mock_anthropic_client
        shared.call_claude("my system", "my user")
        _, kwargs = instance.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens(self, mock_anthropic_client):
        _, instance = mock_anthropic_client
        shared.call_claude("sys", "usr")
        _, kwargs = instance.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, mock_anthropic_client):
        _, instance = mock_anthropic_client
        shared.call_claude("sys", "usr", max_tokens=1024)
        _, kwargs = instance.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self, mock_anthropic_client):
        MockClient, _ = mock_anthropic_client
        shared.call_claude("sys", "usr")
        MockClient.assert_called_once_with(api_key="test-anthropic-key")

    def test_returns_first_content_block(self, mock_anthropic_client):
        _, instance = mock_anthropic_client
        first = MagicMock(text="first block")
        second = MagicMock(text="second block")
        instance.messages.create.return_value.content = [first, second]
        result = shared.call_claude("sys", "usr")
        assert result == "first block"

    def test_anthropic_exception_propagates(self, mock_anthropic_client):
        _, instance = mock_anthropic_client
        instance.messages.create.side_effect = Exception("API error")
        with pytest.raises(Exception, match="API error"):
            shared.call_claude("sys", "usr")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _make_blob(self, path: str, content: str) -> dict:
        encoded = base64.b64encode(content.encode()).decode()
        return {
            "type": "blob",
            "path": path,
            "url": f"https://api.github.com/repos/test/test/git/blobs/abc",
            "content_encoded": encoded,
        }

    def _build_mock_get(self, tree_items: list, contents: dict):
        """
        tree_items: list of dicts with type/path/url
        contents: mapping url → {"content": base64_string}
        """
        def side_effect(url, headers=None):
            mock_resp = MagicMock()
            if "git/trees" in url:
                mock_resp.json.return_value = {"tree": tree_items}
            else:
                # blob fetch
                blob_url = url
                mock_resp.json.return_value = contents.get(blob_url, {})
            return mock_resp

        return side_effect

    def test_happy_path_single_file(self):
        path = "src/main.py"
        content = "print('hello')"
        encoded = base64.b64encode(content.encode()).decode()
        blob_url = "https://blob/1"
        tree = [{"type": "blob", "path": path, "url": blob_url}]

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get(
                tree, {blob_url: {"content": encoded}}
            )
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert path in result
        assert result[path] == content

    def test_filters_by_extension(self):
        blob_url_py = "https://blob/py"
        blob_url_md = "https://blob/md"
        tree = [
            {"type": "blob", "path": "main.py", "url": blob_url_py},
            {"type": "blob", "path": "README.md", "url": blob_url_md},
            {"type": "blob", "path": "data.json", "url": "https://blob/json"},
        ]
        encoded = base64.b64encode(b"content").decode()

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get(
                tree,
                {
                    blob_url_py: {"content": encoded},
                    blob_url_md: {"content": encoded},
                },
            )
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "README.md" not in result
        assert "data.json" not in result

    def test_multiple_extensions(self):
        blob_py = "https://blob/py"
        blob_md = "https://blob/md"
        tree = [
            {"type": "blob", "path": "main.py", "url": blob_py},
            {"type": "blob", "path": "README.md", "url": blob_md},
        ]
        encoded = base64.b64encode(b"x").decode()

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get(
                tree, {blob_py: {"content": encoded}, blob_md: {"content": encoded}}
            )
            result = shared.get_repo_files("owner", "repo", [".py", ".md"])

        assert "main.py" in result
        assert "README.md" in result

    def test_max_files_limit(self):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://blob/{i}"}
            for i in range(10)
        ]
        encoded = base64.b64encode(b"code").decode()
        contents = {f"https://blob/{i}": {"content": encoded} for i in range(10)}

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get(tree, contents)
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self):
        blob_url = "https://blob/1"
        tree = [
            {"type": "tree", "path": "src", "url": "https://tree/1"},
            {"type": "blob", "path": "main.py", "url": blob_url},
        ]
        encoded = base64.b64encode(b"code").decode()

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get(
                tree, {blob_url: {"content": encoded}}
            )
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_handles_decode_exception_gracefully(self):
        blob_url = "https://blob/bad"
        tree = [{"type": "blob", "path": "bad.py", "url": blob_url}]

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get(
                tree, {blob_url: {}}  # missing "content" key → KeyError
            )
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Should silently skip the file
        assert "bad.py" not in result

    def test_empty_tree(self):
        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get([], {})
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_no_matching_extensions(self):
        blob_url = "https://blob/1"
        tree = [{"type": "blob", "path": "main.go", "url": blob_url}]
        encoded = base64.b64encode(b"package main").decode()

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = self._build_mock_get(
                tree, {blob_url: {"content": encoded}}
            )
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_correct_url_constructed(self):
        with patch("shared.requests.get") as mock_get:
            mock_get.return_value.json.return_value = {"tree": []}
            shared.get_repo_files("myowner", "myrepo", [".py"])
            first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "git/trees/HEAD" in first_call_url

    def test_utf8_replace_on_binary(self):
        """Files with non-UTF8 bytes should still be included via errors='replace'."""
        blob_url = "https://blob/binary"
        raw_bytes = bytes([0xFF, 0xFE, 0x41])  # invalid UTF-8 prefix + 'A'
        encoded = base64.b64encode