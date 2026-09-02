"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation and response extraction
- clean_json(): Markdown fence stripping from JSON strings
- get_repo_files(): GitHub repo file fetching with extension filtering and max_files cap
- get_pr_diff(): GitHub PR diff retrieval and truncation
- write_output_file(): GitHub file create/update (with and without existing SHA)
- post_pr_comment(): GitHub PR comment posting
- send_email(): SendGrid email dispatch, success and failure paths
- email_html(): HTML email template rendering
- write_audit_entry(): Audit log writing (JSON + Markdown) in the output repo

Mocks used:
- unittest.mock.patch / MagicMock for:
    - anthropic.Anthropic (Claude client)
    - requests.get, requests.post, requests.put
    - base64 (where needed indirectly)
- All environment variables injected via monkeypatch / os.environ patching

TODOs:
- TODO: Integration test for write_audit_entry reading back persisted logs requires a real/emulated repo
- TODO: Verify exact SHA collision behaviour when GitHub returns unexpected payload shapes
- TODO: Test MODEL constant is sent correctly to Claude (currently checked via call args)
"""

import base64
import importlib
import json
import os
import sys
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported, because
# the module reads os.environ at import time.
# ---------------------------------------------------------------------------
REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}

for k, v in REQUIRED_ENV.items():
    os.environ.setdefault(k, v)

# Add the scripts directory to sys.path so `import shared` resolves correctly.
_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts")
_SCRIPTS_DIR = os.path.normpath(_SCRIPTS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import shared  # noqa: E402  (imported after env setup)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anthropic_response(text: str):
    """Build a minimal fake Anthropic Message object."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ---------------------------------------------------------------------------
# Tests: clean_json
# ---------------------------------------------------------------------------


class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   {\"a\": 1}   "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_fence_with_extra_whitespace_inside(self):
        raw = "```json\n\n  {\"x\": true}\n\n```"
        result = shared.clean_json(raw)
        assert result == '{"x": true}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_backtick_fence_no_content(self):
        raw = "```json\n```"
        # After stripping opening line we get "", after rsplit on ``` we get ""
        result = shared.clean_json(raw)
        assert result == ""

    def test_nested_json_preserved(self):
        inner = '{"a": {"b": [1, 2, 3]}}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    @pytest.mark.parametrize("raw,expected", [
        ('{"product_name": "Generations II"}', '{"product_name": "Generations II"}'),
        ('```json\n{"doc_type": "supplementary"}\n```', '{"doc_type": "supplementary"}'),
        ('```\n{"linked_product": "health_products"}\n```', '{"linked_product": "health_products"}'),
    ])
    def test_parametrized_synthetic_samples(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# Tests: call_claude
# ---------------------------------------------------------------------------


class TestCallClaude:
    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_anthropic_response("Hello!")

        result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello!"
        mock_anthropic_cls.assert_called_once_with(api_key=REQUIRED_ENV["ANTHROPIC_API_KEY"])
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
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        shared.call_claude("s", "u", max_tokens=512)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 512

    @patch("shared.anthropic.Anthropic")
    def test_returns_first_content_block(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        block1, block2 = MagicMock(), MagicMock()
        block1.text = "first"
        block2.text = "second"
        resp = MagicMock()
        resp.content = [block1, block2]
        mock_client.messages.create.return_value = resp

        assert shared.call_claude("s", "u") == "first"

    @patch("shared.anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_empty_system_prompt_allowed(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.return_value = _make_anthropic_response("")

        result = shared.call_claude("", "anything")
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: get_repo_files
# ---------------------------------------------------------------------------


class TestGetRepoFiles:
    def _tree_response(self, items):
        """Build a fake GitHub tree response."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _blob_response(self, text: str):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": _b64(text) + "\n"}
        return mock_resp

    @patch("shared.requests.get")
    def test_happy_path_single_extension(self, mock_get):
        tree = [
            {"type": "blob", "path": "foo.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "bar.md", "url": "https://api.github.com/blob/2"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response("print('hello')"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert list(result.keys()) == ["foo.py"]
        assert result["foo.py"] == "print('hello')"

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = [
            {"type": "blob", "path": "a.py", "url": "u1"},
            {"type": "blob", "path": "b.js", "url": "u2"},
            {"type": "blob", "path": "c.md", "url": "u3"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response("python"),
            self._blob_response("javascript"),
        ]

        result = shared.get_repo_files("o", "r", [".py", ".js"])
        assert set(result.keys()) == {"a.py", "b.js"}

    @patch("shared.requests.get")
    def test_respects_max_files(self, mock_get):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"u{i}"}
            for i in range(10)
        ]
        # First call: tree; subsequent calls: blob content
        mock_get.side_effect = [self._tree_response(tree)] + [
            self._blob_response(f"content{i}") for i in range(3)
        ]

        result = shared.get_repo_files("o", "r", [".py"], max_files=3)
        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_tree_nodes(self, mock_get):
        tree = [
            {"type": "tree", "path": "somedir", "url": "u1"},
            {"type": "blob", "path": "real.py", "url": "u2"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree),
            self._blob_response("code"),
        ]

        result = shared.get_repo_files("o", "r", [".py"])
        assert "somedir" not in result
        assert "real.py" in result

    @patch("shared.requests.get")
    def test_empty_tree(self, mock_get):
        mock_get.return_value = self._tree_response([])
        result = shared.get_repo_files("o", "r", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_no_matching_extensions(self, mock_get):
        tree = [{"type": "blob", "path": "file.go", "url": "u1"}]
        mock_get.side_effect = [self._tree_response(tree)]
        result = shared.get_repo_files("o", "r", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_bad_blob_silently_skipped(self, mock_get):
        tree = [
            {"type": "blob", "path": "broken.py", "url": "u1"},
            {"type": "blob", "path": "good.py", "url": "u2"},
        ]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {}   # no "content" key → base64 decode raises

        mock_get.side_effect = [
            self._tree_response(tree),
            bad_blob,
            self._blob_response("good content"),
        ]

        result = shared.get_repo_files("o", "r", [".py"])
        # broken.py raises KeyError inside except block → skipped
        assert "broken.py" not in result
        assert result.get("good.py") == "good content"

    @patch("shared.requests.get")
    def test_constructs_correct_tree_url(self, mock_get):
        mock_get.return_value = self._tree_response([])
        shared.get_repo_files("myowner", "myrepo", [".txt"])
        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo/git/trees/HEAD?recursive=1" in called_url


# ---------------------------------------------------------------------------
# Tests: get_pr_diff
# ---------------------------------------------------------------------------


class TestGetPrDiff:
    @patch("shared.requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/foo b/foo\n+new line"
        mock_get.return_value = mock_resp

        result = shared.get_pr_diff("owner", "repo", 42)
        assert result == "diff --git a/foo b/foo\n+new line"

    @patch("shared.requests.get")
    def test_truncates_to_30000_chars(self, mock_get):
        long_diff = "x" * 50000
        mock_resp = MagicMock()
        mock_resp.text = long_diff
        mock_get.return_value = mock_resp

        result = shared.get_pr_diff("o", "r", 1)
        assert len(result) == 30000

    @patch("shared.requests.get")
    def test_uses_diff_accept_header(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_get.return_value = mock_resp

        shared.get_pr_diff("o", "r", 7)
        headers = mock_get.call_args[1]["headers"]
        assert headers["Accept"] == "application/vnd.github.diff"

    @patch("shared.requests.get")
    def test_correct_url_constructed(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_get.return_value = mock_resp

        shared.get_pr_diff("capco", "myrepo", 99)
        url = mock_get.call_args[0][0]
        assert url == "https://api.github.com/repos/capco/myrepo/pulls/99"

    @patch("shared.requests.get")
    def test_empty_diff(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = ""
        mock_get.return_value = mock_resp

        assert shared.get_pr_diff("o", "r", 0) == ""


# ---------------------------------------------------------------------------
# Tests: write_output_file
# ---------------------------------------------------------------------------


class TestWriteOutputFile:
    @patch("shared.requests.put")
    @patch("shared.requests.