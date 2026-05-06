"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude(): Claude API invocation, response extraction
  - clean_json(): Markdown fence stripping, edge cases
  - get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decode errors
  - get_pr_diff(): PR diff fetching, truncation behaviour
  - write_output_file(): File create (no SHA) and update (with SHA) paths
  - post_pr_comment(): PR comment posting
  - send_email(): SendGrid success and failure paths
  - email_html(): HTML generation for SUCCESS and non-SUCCESS statuses
  - write_audit_entry(): Audit log construction (partial source — see TODO)

Mocks used:
  - unittest.mock.patch for os.environ (environment variables)
  - unittest.mock.MagicMock / patch for anthropic.Anthropic client
  - unittest.mock.patch for requests.get, requests.post, requests.put
  - base64 encoding/decoding tested directly (no mock needed)

TODOs:
  - TODO: write_audit_entry() source is truncated — full implementation needed to test audit log format and file writing
  - TODO: Integration test for full workflow requires secrets (ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY)
"""

import base64
import json
import os
import sys
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

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

# Patch environment before module import so module-level lookups succeed
with patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
    # Ensure the script directory is on sys.path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
    # Also try direct path resolution relative to repo root
    _script_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)
    import shared  # noqa: E402


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_env(monkeypatch):
    """Ensure each test runs with clean, predictable environment variables."""
    for key, value in _ENV_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    yield


@pytest.fixture()
def mock_anthropic_client():
    """Return a fully-configured mock Anthropic client."""
    with patch("shared.anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # Default happy-path response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        yield mock_cls, mock_client, mock_response


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
# call_claude()
# ===========================================================================

class TestCallClaude:

    def test_happy_path_returns_text(self, mock_anthropic_client):
        mock_cls, mock_client, mock_response = mock_anthropic_client
        mock_response.content[0].text = "Claude response text"

        result = shared.call_claude(system="You are helpful.", user="Say hi.")

        assert result == "Claude response text"

    def test_passes_system_and_user_messages(self, mock_anthropic_client):
        mock_cls, mock_client, _ = mock_anthropic_client

        shared.call_claude(system="System prompt", user="User message")

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="System prompt",
            messages=[{"role": "user", "content": "User message"}],
        )

    def test_custom_max_tokens(self, mock_anthropic_client):
        mock_cls, mock_client, _ = mock_anthropic_client

        shared.call_claude(system="sys", user="usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("max_tokens") == 1024 or mock_client.messages.create.call_args[1].get("max_tokens") == 1024
        create_kwargs = mock_client.messages.create.call_args
        # Support both positional and keyword call styles
        if create_kwargs.kwargs:
            assert create_kwargs.kwargs["max_tokens"] == 1024
        else:
            assert create_kwargs[1]["max_tokens"] == 1024

    def test_uses_configured_api_key(self, mock_anthropic_client):
        mock_cls, _, _ = mock_anthropic_client

        shared.call_claude(system="sys", user="usr")

        mock_cls.assert_called_once_with(api_key="test-anthropic-key")

    def test_returns_first_content_block(self, mock_anthropic_client):
        mock_cls, mock_client, mock_response = mock_anthropic_client
        block1 = MagicMock(text="first block")
        block2 = MagicMock(text="second block")
        mock_response.content = [block1, block2]

        result = shared.call_claude(system="sys", user="usr")

        assert result == "first block"

    def test_propagates_api_exception(self, mock_anthropic_client):
        mock_cls, mock_client, _ = mock_anthropic_client
        mock_client.messages.create.side_effect = Exception("API error")

        with pytest.raises(Exception, match="API error"):
            shared.call_claude(system="sys", user="usr")

    def test_empty_system_prompt(self, mock_anthropic_client):
        """Edge case: empty system prompt should still work."""
        mock_cls, mock_client, mock_response = mock_anthropic_client
        mock_response.content[0].text = "ok"

        result = shared.call_claude(system="", user="hello")

        assert result == "ok"
        create_kwargs = mock_client.messages.create.call_args
        passed_system = create_kwargs[1].get("system") if create_kwargs[1] else create_kwargs[0][2]
        assert passed_system == ""

    def test_large_user_input(self, mock_anthropic_client):
        """Boundary: very large user input string."""
        mock_cls, mock_client, mock_response = mock_anthropic_client
        mock_response.content[0].text = "processed"
        large_input = "x" * 100_000

        result = shared.call_claude(system="sys", user=large_input)

        assert result == "processed"


# ===========================================================================
# clean_json()
# ===========================================================================

class TestCleanJson:

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = '   {"key": "value"}   '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_whitespace_inside_fences(self):
        raw = '```json\n  {"key": "value"}  \n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   \n\t  ") == ""

    def test_json_array(self):
        raw = '```json\n[1, 2, 3]\n```'
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_nested_backticks_in_content(self):
        """Edge: JSON value itself contains backtick-like characters (not fences)."""
        raw = '{"code": "x = `hello`"}'
        result = shared.clean_json(raw)
        assert result == '{"code": "x = `hello`"}'

    def test_multiline_json_in_fence(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        assert json.loads(result) == {"a": 1, "b": 2}

    def test_result_is_valid_json_after_cleaning(self):
        raw = '```json\n{"customer": "CUST-001", "email": "alice.chen@example.com"}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["customer"] == "CUST-001"

    def test_no_newline_after_fence_open(self):
        """Edge: fence without trailing newline — split behaviour."""
        raw = '```{"key":"val"}```'
        # According to implementation: splits on first \n — if none exists, entire string after ``` is kept
        result = shared.clean_json(raw)
        # Should not raise; result is implementation-defined for malformed input
        assert isinstance(result, str)


# ===========================================================================
# get_repo_files()
# ===========================================================================

class TestGetRepoFiles:

    def _make_blob_item(self, path, url="https://api.github.com/repos/o/r/git/blobs/abc"):
        return {"type": "blob", "path": path, "url": url}

    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_happy_path_single_extension(self, mock_requests_get):
        tree_items = [
            self._make_blob_item("src/main.py"),
            self._make_blob_item("README.md"),
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("print('hello')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == "print('hello')"
        assert "README.md" not in result

    def test_multiple_extensions(self, mock_requests_get):
        tree_items = [
            self._make_blob_item("main.py"),
            self._make_blob_item("style.css"),
            self._make_blob_item("index.js"),
            self._make_blob_item("data.json"),
        ]
        blob_py = self._make_blob_response("# python")
        blob_css = self._make_blob_response("body {}")
        blob_js = self._make_blob_response("console.log()")
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            blob_py,
            blob_css,
            blob_js,
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".css", ".js"])

        assert "main.py" in result
        assert "style.css" in result
        assert "index.js" in result
        assert "data.json" not in result

    def test_max_files_limit(self, mock_requests_get):
        """Should stop fetching after max_files blobs."""
        tree_items = [self._make_blob_item(f"file{i}.py") for i in range(10)]
        blob_resp = self._make_blob_response("content")

        # First call = tree, subsequent = blob fetches (only 3 should happen)
        mock_requests_get.side_effect = [self._make_tree_response(tree_items)] + [blob_resp] * 3

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree(self, mock_requests_get):
        mock_requests_get.return_value = self._make_tree_response([])

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_non_blob_items_skipped(self, mock_requests_get):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "https://example.com"},
            self._make_blob_item("src/main.py"),
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("# code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert len(result) == 1
        assert "src/main.py" in result

    def test_base64_decode_error_silently_skipped(self, mock_requests_get):
        """Files that cannot be decoded should be silently skipped."""
        tree_items = [self._make_blob_item("binary.py")]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": None}  # will cause decode error

        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            bad_blob,
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        # Should not raise; file is simply absent
        assert "binary.py" not in result

    def test_no_matching_extensions(self, mock_requests_get):
        tree_items = [
            self._make_blob_item("README.md"),
            self._make_blob_item("Makefile"),
        ]
        mock_requests_get.return_value = self._make_tree_response(tree_items)

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert result == {}

    def test_correct_url_construction(self, mock_requests_get):
        mock