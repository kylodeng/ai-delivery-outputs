"""
Test suite for .github/scripts/shared.py

What is tested:
    - call_claude(): Claude API wrapper, happy path and error handling
    - clean_json(): markdown fence stripping, edge cases, boundary values
    - get_repo_files(): GitHub tree traversal, extension filtering, max_files limit, decode errors
    - get_pr_diff(): PR diff fetching, truncation to 30000 chars
    - write_output_file(): file create (no SHA) and update (with SHA) paths
    - post_pr_comment(): PR comment posting
    - send_email(): SendGrid integration, success and failure status codes
    - email_html(): HTML generation for SUCCESS and non-SUCCESS statuses
    - write_audit_entry(): audit log dispatch (partial source, truncated in source)

Mocks used:
    - unittest.mock.patch / MagicMock for:
        - anthropic.Anthropic (Claude client)
        - requests.get
        - requests.post
        - requests.put
    - os.environ patched via monkeypatch / patch.dict to supply required env vars

TODOs:
    - write_audit_entry: source is truncated — full body untestable without complete source
    - Integration tests against real GitHub / SendGrid APIs are skipped
"""

import base64
import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Ensure required env vars exist before the module is imported
# ---------------------------------------------------------------------------
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
    # Add the scripts directory to path so the module resolves
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    # Try multiple import paths to handle different working directories
    try:
        import shared
    except ModuleNotFoundError:
        script_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".github",
            "scripts",
        )
        sys.path.insert(0, script_dir)
        import shared  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    """Return a mock requests.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    return resp


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for clean_json() — strips markdown fences from Claude responses."""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = "   \n{\"key\": \"value\"}\n   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_extra_whitespace(self):
        raw = "```json\n   {\"a\": 1}   \n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_fence_markers(self):
        raw = "```\n```"
        result = shared.clean_json(raw)
        # Inner content between fences is empty
        assert result == ""

    def test_multiline_json_in_fence(self):
        inner = '{\n  "customers": [\n    {"id": "CUST-001"}\n  ]\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert json.loads(result) == {"customers": [{"id": "CUST-001"}]}

    def test_no_closing_fence_leaves_content(self):
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # rsplit on missing ``` returns original string after opening strip
        assert '{"key": "value"}' in result

    def test_valid_json_roundtrip(self):
        data = {"tool": "review", "status": "SUCCESS", "customers": ["CUST-001", "CUST-002"]}
        raw = f"```json\n{json.dumps(data)}\n```"
        assert json.loads(shared.clean_json(raw)) == data

    @pytest.mark.parametrize("raw,expected", [
        ('{"a":1}', '{"a":1}'),
        ('  {"a":1}  ', '{"a":1}'),
        ("```\n[]\n```", "[]"),
        ("```json\nnull\n```", "null"),
    ])
    def test_parametrised_cases(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for call_claude() — wraps anthropic.Anthropic.messages.create."""

    def _make_claude_response(self, text: str) -> MagicMock:
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("Hello, world!")

        result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello, world!"
        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="sys prompt",
            messages=[{"role": "user", "content": "user prompt"}],
        )

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("ok")

        shared.call_claude("sys", "user", max_tokens=512)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 512

    @patch("shared.anthropic.Anthropic")
    def test_uses_configured_model(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("ok")

        shared.call_claude("sys", "user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == "claude-sonnet-4-6"

    @patch("shared.anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API unavailable")

        with pytest.raises(Exception, match="API unavailable"):
            shared.call_claude("sys", "user")

    @patch("shared.anthropic.Anthropic")
    def test_empty_response_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("")

        result = shared.call_claude("sys", "user")
        assert result == ""

    @patch("shared.anthropic.Anthropic")
    def test_client_instantiated_with_api_key(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("x")

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for get_repo_files() — fetches and decodes files from GitHub."""

    def _tree_item(self, path: str, item_type: str = "blob", url: str = "https://api.github.com/blob/abc") -> dict:
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, content: str) -> dict:
        encoded = base64.b64encode(content.encode()).decode() + "\n"
        return {"content": encoded}

    @patch("shared.requests.get")
    def test_happy_path_filters_by_extension(self, mock_get):
        tree = [
            self._tree_item("src/main.py"),
            self._tree_item("docs/README.md"),
            self._tree_item("src/utils.py"),
        ]
        blob_py = self._blob_response("print('hello')")

        def side_effect(url, **kwargs):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=blob_py)

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "docs/README.md" not in result
        assert result["src/main.py"] == "print('hello')"

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        blob = self._blob_response("content")

        def side_effect(url, **kwargs):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=blob)

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_non_blob_items_skipped(self, mock_get):
        tree = [
            {"type": "tree", "path": "src", "url": "https://x"},
            self._tree_item("src/main.py"),
        ]
        blob = self._blob_response("code")

        def side_effect(url, **kwargs):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=blob)

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "src/main.py" in result

    @patch("shared.requests.get")
    def test_empty_tree(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    @patch("shared.requests.get")
    def test_missing_tree_key_returns_empty(self, mock_get):
        mock_get.return_value = _make_response(json_data={})

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    @patch("shared.requests.get")
    def test_decode_error_is_silenced(self, mock_get):
        tree = [self._tree_item("bad.py")]

        def side_effect(url, **kwargs):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            # Return invalid base64 to trigger exception
            bad_resp = MagicMock()
            bad_resp.json.return_value = {"content": "!!!not-valid-base64!!!"}
            return bad_resp

        mock_get.side_effect = side_effect

        # Should not raise
        result = shared.get_repo_files("owner", "repo", [".py"])
        # File silently skipped
        assert "bad.py" not in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = [
            self._tree_item("app.py"),
            self._tree_item("style.css"),
            self._tree_item("index.js"),
        ]
        blob = self._blob_response("data")

        def side_effect(url, **kwargs):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=blob)

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py", ".css"])

        assert "app.py" in result
        assert "style.css" in result
        assert "index.js" not in result

    @patch("shared.requests.get")
    def test_uses_correct_tree_url(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})

        shared.get_repo_files("my-owner", "my-repo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "my-owner" in first_call_url
        assert "my-repo" in first_call_url
        assert "git/trees/HEAD" in first_call_url
        assert "recursive=1" in first_call_url

    @patch("shared.requests.get")
    def test_utf8_replace_on_decode(self, mock_get):
        """Files with non-UTF8 bytes should be decoded with replace strategy."""
        raw_bytes = b"hello \xff world"
        encoded = base64.b64encode(raw_bytes).decode() + "\n"
        tree = [self._tree_item("binary.py")]

        def side_effect(url, **kwargs):
            if "git/trees" in url:
                return _make_response(json_data={"