"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration (happy path, response parsing)
- clean_json(): Markdown fence stripping (various fence formats, edge cases)
- get_repo_files(): GitHub API file fetching (filtering, max_files limit, decode errors)
- get_pr_diff(): PR diff fetching (content truncation)
- write_output_file(): File creation/update in output repo (new file, existing file with SHA)
- post_pr_comment(): PR comment posting
- send_email(): SendGrid email sending (success, failure status codes)
- email_html(): HTML email body generation (SUCCESS/FAILURE status, content checks)
- write_audit_entry(): Audit log writing (calls write_output_file for JSON and MD logs)

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client
- unittest.mock.patch for datetime.datetime to control timestamps

TODOs:
- TODO: Integration test for call_claude() with real Anthropic API (requires live key)
- TODO: Test write_audit_entry() full JSON/MD content format once full source is available
  (source code was truncated before the function body was complete)
- TODO: Test get_repo_files() behaviour when GitHub API returns non-200 responses
- TODO: Test send_email() retry/backoff behaviour if implemented in future
"""

import base64
import datetime
import json
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: inject required environment variables BEFORE importing shared.py
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

# Patch environment before the module is imported so the module-level
# os.environ[] accesses succeed.
with mock.patch.dict("os.environ", ENV_DEFAULTS, clear=False):
    # Stub out the `anthropic` package so we don't need the real library
    anthropic_stub = types.ModuleType("anthropic")
    anthropic_stub.Anthropic = MagicMock()
    sys.modules.setdefault("anthropic", anthropic_stub)

    import importlib
    import shared  # noqa: E402  (relative import from test location)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_content_response(text: str):
    """Build a minimal fake Anthropic message response."""
    content_item = MagicMock()
    content_item.text = text
    response = MagicMock()
    response.content = [content_item]
    return response


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    """Tests for call_claude()."""

    def test_happy_path_returns_text(self):
        fake_response = _make_content_response("Hello from Claude")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("You are helpful.", "Say hello")

        assert result == "Hello from Claude"
        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=4096,
            system="You are helpful.",
            messages=[{"role": "user", "content": "Say hello"}],
        )

    def test_custom_max_tokens_forwarded(self):
        fake_response = _make_content_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_api_key_passed_to_client(self):
        fake_response = _make_content_response("ok")
        mock_anthropic_cls = MagicMock(return_value=MagicMock())
        mock_anthropic_cls.return_value.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic", mock_anthropic_cls):
            shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_returns_first_content_block(self):
        """Ensures we index [0].text correctly even when multiple blocks exist."""
        first = MagicMock()
        first.text = "first block"
        second = MagicMock()
        second.text = "second block"
        response = MagicMock()
        response.content = [first, second]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u")

        assert result == "first block"

    def test_propagates_api_exception(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API error"):
                shared.call_claude("s", "u")


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    """Tests for clean_json()."""

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
        raw = "   \n```json\n{\"a\": 1}\n```\n   "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_multiline_json_preserved(self):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_no_fence_no_change(self):
        raw = "[1, 2, 3]"
        assert shared.clean_json(raw) == "[1, 2, 3]"

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_fence_without_closing(self):
        """Should not crash; strips opening fence line, returns remainder."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # The opening fence line is dropped; no closing fence → result is the body
        assert '{"key": "value"}' in result

    @pytest.mark.parametrize("raw,expected", [
        ('{"product_name": "Generations II", "doc_type": "product_brochure"}',
         '{"product_name": "Generations II", "doc_type": "product_brochure"}'),
        ('```json\n{"product_name": "Generations II"}\n```',
         '{"product_name": "Generations II"}'),
        ('```\n[{"linked_product": "health_products"}]\n```',
         '[{"linked_product": "health_products"}]'),
    ])
    def test_parametrised_insurance_samples(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    """Tests for get_repo_files()."""

    def _tree_item(self, path: str, url: str = "http://blob-url"):
        return {"type": "blob", "path": path, "url": url}

    def _blob_response(self, text: str):
        encoded = base64.b64encode(text.encode()).decode()
        return {"content": encoded + "\n"}  # GitHub adds newline

    @patch("shared.requests.get")
    def test_happy_path_filters_by_extension(self, mock_get):
        tree = [
            self._tree_item("file.py"),
            self._tree_item("file.md"),
            self._tree_item("file.txt"),
        ]
        blob_resp = self._blob_response("print('hello')")

        mock_get.side_effect = [
            MagicMock(json=lambda: {"tree": tree}),   # tree call
            MagicMock(json=lambda: blob_resp),          # file.py blob
            MagicMock(json=lambda: blob_resp),          # file.md blob
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".md"])

        assert "file.py" in result
        assert "file.md" in result
        assert "file.txt" not in result

    @patch("shared.requests.get")
    def test_max_files_limit_respected(self, mock_get):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        blob_resp = self._blob_response("content")

        responses = [MagicMock(json=lambda: {"tree": tree})]
        responses += [MagicMock(json=lambda: blob_resp) for _ in range(5)]
        mock_get.side_effect = responses

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"tree": []})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_no_matching_extensions_returns_empty(self, mock_get):
        tree = [self._tree_item("file.go"), self._tree_item("file.rs")]
        mock_get.return_value = MagicMock(json=lambda: {"tree": tree})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_non_blob_items_skipped(self, mock_get):
        tree = [
            {"type": "tree", "path": "src/", "url": "http://tree-url"},
            self._tree_item("src/file.py"),
        ]
        blob_resp = self._blob_response("data")
        mock_get.side_effect = [
            MagicMock(json=lambda: {"tree": tree}),
            MagicMock(json=lambda: blob_resp),
        ]
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/file.py" in result
        assert "src/" not in result

    @patch("shared.requests.get")
    def test_decode_error_skips_file_gracefully(self, mock_get):
        tree = [self._tree_item("bad.py"), self._tree_item("good.py")]

        bad_blob = {"content": "!!!not-valid-base64!!!"}
        good_blob = self._blob_response("good content")

        mock_get.side_effect = [
            MagicMock(json=lambda: {"tree": tree}),
            MagicMock(json=lambda: bad_blob),
            MagicMock(json=lambda: good_blob),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        # bad.py should be absent; good.py present
        assert "good.py" in result

    @patch("shared.requests.get")
    def test_correct_url_constructed(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {"tree": []})
        shared.get_repo_files("myowner", "myrepo", [".py"])
        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url

    @patch("shared.requests.get")
    def test_missing_tree_key_returns_empty(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: {})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}


# ---------------------------------------------------------------------------
# get_pr_diff
# ---------------------------------------------------------------------------

class TestGetPrDiff:
    """Tests for get_pr_diff()."""

    @patch("shared.requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_get.return_value = MagicMock(text="diff --git a/file.py b/file.py\n+new line")
        result = shared.get_pr_diff("owner", "repo", 42)
        assert "diff --git" in result
        assert "+new line" in result

    @patch("shared.requests.get")
    def test_truncates_to_30000_chars(self, mock_get):
        long_diff = "x" * 50000
        mock_get.return_value = MagicMock(text=long_diff)
        result = shared.get_pr_diff("owner", "repo", 1)
        assert len(result) == 30000

    @patch("shared.requests.get")
    def test_short_diff_not_truncated(self, mock_get):
        short_diff = "small diff"
        mock_get.return_value = MagicMock(text=short_diff)
        result = shared.get_pr_diff("owner", "repo", 1)
        assert result == "small diff"

    @patch("shared.requests.get")
    def test_correct_url_and_headers(self, mock_get):
        mock_get.return_value = MagicMock(text="")
        shared.get_pr_diff("myowner", "myrepo", 99)
        call_kwargs = mock_get.call_args
        url = call_kwargs[0][0]
        headers = call_kwargs[1]["headers"]
        assert "myowner/myrepo/pulls/99" in url
        assert headers["Accept"] == "application/vnd.github.diff"

    @patch("shared.requests.get")
    def test_empty_diff(self, mock_get):
        mock_get.return_value = MagicMock(text="")
        result = shared.get_pr_diff("owner", "repo