"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: happy path, API response parsing
- clean_json: markdown fence stripping, edge cases, plain JSON passthrough
- get_repo_files: happy path, extension filtering, max_files limit, base64 decode errors
- get_pr_diff: happy path, truncation at 30000 chars
- write_output_file: create new file (no SHA), update existing file (with SHA), fallback URL
- post_pr_comment: happy path, correct endpoint construction
- send_email: success (200/202), failure warning path
- email_html: SUCCESS/FAILURE status colour, content inclusion
- write_audit_entry: tested as far as the truncated source allows

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude client)
  - requests.get / requests.post / requests.put
  - os.environ (via monkeypatch)

TODOs:
- write_audit_entry: source is truncated — full body/behaviour cannot be verified
- call_claude: test streaming / error handling once error paths are confirmed
- get_repo_files: test pagination if GitHub ever pages the tree response
"""

import base64
import json
import os
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
    "NOTIFY_EMAIL": "kylo.deng@capco.com",
    "SENDER_EMAIL": "kylo.deng@capco.com",
}

for k, v in ENV_DEFAULTS.items():
    os.environ.setdefault(k, v)

# Now safe to import
import importlib
import sys

# Force a clean import with our env vars in place
if "shared" in sys.modules:
    del sys.modules["shared"]

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))

import shared  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None, text: str = ""):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self):
        payload = '{"key": "value"}'
        assert shared.clean_json(payload) == payload

    def test_strips_backtick_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_leading_trailing_whitespace_stripped(self):
        raw = '  \n  {"key": "value"}  \n  '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_fence_with_extra_whitespace(self):
        raw = "```json\n   {\"a\": 1}   \n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_backticks(self):
        # Degenerate: just fences, no content
        raw = "```\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_no_closing_fence(self):
        # If there's no closing fence rsplit returns the whole string
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # Should not crash; content after opening line is kept
        assert '{"key": "value"}' in result

    def test_multiline_json_in_fence(self):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    @pytest.mark.parametrize("value", [
        '{"customer_id": "CUST-001", "email": "alice.chen@example.com"}',
        '{"customer_id": "CUST-007", "email": "invalid-email"}',
        '{"age": -1, "country_code": "KR"}',
    ])
    def test_synthetic_customer_payloads_passthrough(self, value):
        assert shared.clean_json(value) == value


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_claude_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    @patch("anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("Hello!")

        result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello!"
        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="sys prompt",
            messages=[{"role": "user", "content": "user prompt"}],
        )

    @patch("anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("ok")

        shared.call_claude("sys", "user", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("anthropic.Anthropic")
    def test_returns_first_content_block(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        block1 = MagicMock()
        block1.text = "first"
        block2 = MagicMock()
        block2.text = "second"
        resp = MagicMock()
        resp.content = [block1, block2]
        mock_client.messages.create.return_value = resp

        result = shared.call_claude("s", "u")
        assert result == "first"

    @patch("anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        with pytest.raises(Exception, match="API error"):
            shared.call_claude("s", "u")

    @patch("anthropic.Anthropic")
    def test_uses_configured_api_key(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = self._make_claude_response("x")

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path: str, item_type: str = "blob", url: str = "https://fake/blob"):
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, content: str):
        return {"content": _b64(content) + "\n"}

    @patch("requests.get")
    def test_happy_path_single_extension(self, mock_get):
        tree = {
            "tree": [
                self._tree_item("src/main.py"),
                self._tree_item("src/utils.py"),
                self._tree_item("README.md"),
            ]
        }
        mock_get.side_effect = [
            _make_response(json_data=tree),               # tree call
            _make_response(json_data=self._blob_response("print('main')")),
            _make_response(json_data=self._blob_response("# utils")),
        ]
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert "README.md" not in result
        assert result["src/main.py"] == "print('main')"

    @patch("requests.get")
    def test_extension_filter_multiple(self, mock_get):
        tree = {
            "tree": [
                self._tree_item("app.js"),
                self._tree_item("style.css"),
                self._tree_item("data.json"),
                self._tree_item("image.png"),
            ]
        }
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data=self._blob_response("console.log()")),
            _make_response(json_data=self._blob_response("{}")),
        ]
        result = shared.get_repo_files("owner", "repo", [".js", ".json"])
        assert "app.js" in result
        assert "data.json" in result
        assert "style.css" not in result
        assert "image.png" not in result

    @patch("requests.get")
    def test_max_files_limit(self, mock_get):
        paths = [f"file{i}.py" for i in range(10)]
        tree = {"tree": [self._tree_item(p) for p in paths]}

        blob = _make_response(json_data=self._blob_response("content"))
        mock_get.side_effect = [_make_response(json_data=tree)] + [blob] * 10

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    @patch("requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = {
            "tree": [
                {"type": "tree", "path": "src", "url": "https://fake/tree"},
                self._tree_item("src/main.py"),
            ]
        }
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data=self._blob_response("code")),
        ]
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "src/main.py" in result

    @patch("requests.get")
    def test_base64_decode_error_skipped(self, mock_get):
        tree = {"tree": [self._tree_item("bad.py")]}
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data={"content": "!!!not-valid-base64!!!"}),
        ]
        # Should not raise; bad file is silently skipped
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "bad.py" not in result

    @patch("requests.get")
    def test_empty_tree(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_correct_tree_url_constructed(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        shared.get_repo_files("myowner", "myrepo", [".py"])
        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo" in called_url
        assert "recursive=1" in called_url

    @patch("requests.get")
    def test_utf8_content_decoded(self, mock_get):
        content = "# héllo wörld"
        tree = {"tree": [self._tree_item("unicode.py")]}
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data={"content": _b64(content)}),
        ]
        result = shared.get_repo_files("o", "r", [".py"])
        assert result["unicode.py"] == content


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    @patch("requests.get")
    def test_happy_path_returns_text(self, mock_get):
        mock_get.return_value = _make_response(text="diff --git a/foo.py b/foo.py\n+added line")
        result = shared.get_pr_diff("owner", "repo", 42)
        assert "diff --git" in result
        assert "+added line" in result

    @patch("requests.get")
    def test_correct_url_and_headers(self, mock_get):
        mock_get.return_value = _make_response(text="")
        shared.get_pr_diff("owner", "repo", 7)
        called_url = mock_get.call_args[0][0]
        called_headers = mock_get.call_args[1]["headers"]
        assert "owner/repo/pulls/7" in called_url
        assert called_headers["Accept"] == "application/vnd.github.diff"

    @patch("requests.get")
    def test_truncated_to_30000_chars(self, mock_get):
        long_text = "x" * 50000
        mock_get.return_value = _make_response(text=long_text)
        result = shared.get_pr_diff("owner", "repo", 1)
        assert len(result) == 30000

    @patch("requests.get")
    def test_short_diff_not_padded(self, mock_get):
        mock_get.return_value = _make_response(text="short")
        result = shared.get_pr_diff("owner", "repo", 1)
        assert result == "short"

    @patch("requests.get")
    def test_empty_diff(self, mock_get):
        mock_get.return_value = _make_response(text="")
        result = shared.get_pr_diff("owner", "repo", 99)
        assert result == ""


# ===========================================================================