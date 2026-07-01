"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetch, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetch, truncation
- write_output_file(): create/update file in output repo, SHA handling
- post_pr_comment(): PR comment posting
- send_email(): SendGrid API call, failure warning
- email_html(): HTML output structure, SUCCESS/FAILURE status colour
- write_audit_entry(): audit log composition (partial – see TODOs)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude client)
  - requests.get / requests.post / requests.put
  - os.environ (via monkeypatch)
  - datetime.datetime (for deterministic timestamps)

TODOs:
- TODO: write_audit_entry full integration test requires the actual file read/write
        cycle with write_output_file; stub left below.
- TODO: call_claude streaming variant (if added later)
- TODO: get_repo_files with pagination beyond max_files across multiple pages
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

for k, v in ENV_DEFAULTS.items():
    os.environ.setdefault(k, v)

# Now we can safely import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
import shared  # noqa: E402  (import after env setup)


# ===========================================================================
# Helpers / fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def reset_module_headers(monkeypatch):
    """Ensure GH_HEADERS always reflects the test token."""
    monkeypatch.setitem(shared.GH_HEADERS, "Authorization", "Bearer test-gh-token")


def _make_response(status_code=200, json_data=None, text=""):
    """Build a mock requests.Response."""
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

    def test_strips_json_fenced_block(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fenced_block(self):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_surrounding_whitespace(self):
        raw = "   \n```json\n{}\n```\n   "
        result = shared.clean_json(raw)
        assert result == "{}"

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_no_closing_fence(self):
        """If there is no closing fence the content after the opening line is returned."""
        raw = "```json\n{\"x\": 1}"
        result = shared.clean_json(raw)
        # rsplit on ``` that doesn't exist returns the whole string unchanged
        assert '{"x": 1}' in result

    def test_multiline_json_preserved(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        assert result == '{\n  "a": 1,\n  "b": 2\n}'

    def test_nested_backticks_inside_json(self):
        """Backticks inside JSON string values should not break parsing."""
        raw = '{"code": "no fences here"}'
        assert shared.clean_json(raw) == '{"code": "no fences here"}'


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        result = shared.call_claude("system prompt", "user message")

        assert result == "Hello from Claude"
        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="system prompt",
            messages=[{"role": "user", "content": "user message"}],
        )

    @patch("shared.anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="short")]
        mock_client.messages.create.return_value = mock_response

        shared.call_claude("sys", "usr", max_tokens=512)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 512

    @patch("shared.anthropic.Anthropic")
    def test_uses_configured_api_key(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_empty_system_and_user(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="")]
        mock_client.messages.create.return_value = mock_response

        result = shared.call_claude("", "")
        assert result == ""


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path, item_type="blob", url="http://blob-url"):
        return {"type": item_type, "path": path, "url": url}

    @patch("shared.requests.get")
    def test_happy_path_filters_by_extension(self, mock_get):
        tree = {
            "tree": [
                self._tree_item("src/main.py"),
                self._tree_item("src/utils.js"),
                self._tree_item("README.md"),
            ]
        }
        blob_content = base64.b64encode(b"print('hello')").decode()

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data=tree)
            return _make_response(json_data={"content": blob_content})

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert result["src/main.py"] == "print('hello')"
        assert "src/utils.js" not in result
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        tree = {"tree": [self._tree_item(f"file{i}.py") for i in range(30)]}
        blob_content = base64.b64encode(b"x").decode()

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data=tree)
            return _make_response(json_data={"content": blob_content})

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)
        assert len(result) == 5

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = {
            "tree": [
                self._tree_item("src/", item_type="tree"),
                self._tree_item("main.py", item_type="blob"),
            ]
        }
        blob_content = base64.b64encode(b"code").decode()

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data=tree)
            return _make_response(json_data={"content": blob_content})

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("o", "r", [".py"])
        assert len(result) == 1
        assert "main.py" in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = {
            "tree": [
                self._tree_item("app.py"),
                self._tree_item("index.ts"),
                self._tree_item("notes.txt"),
            ]
        }
        blob_content = base64.b64encode(b"content").decode()

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data=tree)
            return _make_response(json_data={"content": blob_content})

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("o", "r", [".py", ".ts"])
        assert "app.py" in result
        assert "index.ts" in result
        assert "notes.txt" not in result

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        result = shared.get_repo_files("o", "r", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_missing_tree_key_returns_empty_dict(self, mock_get):
        mock_get.return_value = _make_response(json_data={})
        result = shared.get_repo_files("o", "r", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_bad_blob_content_silently_skipped(self, mock_get):
        tree = {
            "tree": [
                self._tree_item("broken.py"),
                self._tree_item("good.py"),
            ]
        }
        good_content = base64.b64encode(b"good code").decode()

        call_count = {"n": 0}

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data=tree)
            call_count["n"] += 1
            if call_count["n"] == 1:
                # missing 'content' key triggers exception in b64decode
                return _make_response(json_data={})
            return _make_response(json_data={"content": good_content})

        mock_get.side_effect = side_effect

        result = shared.get_repo_files("o", "r", [".py"])
        # broken.py silently skipped, good.py included
        assert "good.py" in result
        assert result["good.py"] == "good code"

    @patch("shared.requests.get")
    def test_correct_url_constructed(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        shared.get_repo_files("myowner", "myrepo", [".py"])
        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo/git/trees/HEAD" in called_url
        assert "recursive=1" in called_url


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    @patch("shared.requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_get.return_value = _make_response(text="--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new")
        result = shared.get_pr_diff("owner", "repo", 42)
        assert "--- a/file" in result

    @patch("shared.requests.get")
    def test_correct_url_and_headers(self, mock_get):
        mock_get.return_value = _make_response(text="diff")
        shared.get_pr_diff("owner", "repo", 7)
        call_args = mock_get.call_args
        url = call_args[0][0]
        headers = call_args[1]["headers"]
        assert url == f"{shared.GH_API}/repos/owner/repo/pulls/7"
        assert headers["Accept"] == "application/vnd.github.diff"

    @patch("shared.requests.get")
    def test_truncates_at_30000_chars(self, mock_get):
        long_diff = "x" * 50000
        mock_get.return_value = _make_response(text=long_diff)
        result = shared.get_pr_diff("o", "r", 1)
        assert len(result) == 30000

    @patch("shared.requests.get")
    def test_short_diff_not_truncated(self, mock_get):
        short_diff = "short diff content"
        mock_get.return_value = _make_response(text=short_diff)
        result = shared.get_pr_diff("o", "r", 1)
        assert result == short_diff

    @patch("shared.requests.get")
    def test_empty_diff