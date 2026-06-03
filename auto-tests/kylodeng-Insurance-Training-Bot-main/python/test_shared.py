"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration
- clean_json(): Markdown fence stripping utility
- get_repo_files(): GitHub API file fetching with extension filtering
- get_pr_diff(): GitHub API PR diff fetching
- write_output_file(): GitHub API file creation/update
- post_pr_comment(): GitHub API PR comment posting
- send_email(): SendGrid email delivery
- email_html(): HTML email template generation
- write_audit_entry(): Audit log writing (partial - depends on write_output_file)

Mocks used:
- unittest.mock.patch for os.environ (all API keys/tokens)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- datetime.datetime.utcnow patched for deterministic timestamps

TODOs:
- TODO: write_audit_entry full integration test requires knowing exact JSON/Markdown
        format of audit log files and the truncated source code to be complete
- TODO: test for concurrent/thread-safety of module-level GH_HEADERS construction
- TODO: test rate-limit handling (GitHub/SendGrid) — no retry logic visible in source
"""

import base64
import datetime
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE importing shared
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

with patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
    # Stub the `anthropic` module so the import of shared.py succeeds
    # even without the real package installed in the test environment.
    _anthropic_stub = types.ModuleType("anthropic")
    _anthropic_stub.Anthropic = MagicMock()
    sys.modules.setdefault("anthropic", _anthropic_stub)

    import importlib
    import shared  # noqa: E402  (imported after env patch)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None, text: str = "") -> MagicMock:
    """Build a mock requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data if json_data is not None else {}
    mock_resp.text = text
    return mock_resp


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for shared.clean_json()."""

    def test_no_fences_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = '  \n  {"key": "value"}  \n  '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self):
        raw = '  ```json\n{"a": 1}\n```  '
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_fence_with_multiline_json(self):
        inner = '{\n  "product_name": "Generations II",\n  "doc_type": "product_brochure"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_nested_backticks_content_preserved(self):
        """Content containing backticks but not starting with ``` should be unchanged."""
        raw = 'use `code` here'
        assert shared.clean_json(raw) == 'use `code` here'

    def test_fence_without_closing_backticks(self):
        """Opening fence present but no closing — still strips the first line."""
        raw = "```json\n{}"
        result = shared.clean_json(raw)
        # rsplit on ``` not present → original tail returned stripped
        assert "{}" in result

    @pytest.mark.parametrize("raw,expected", [
        ('```json\n[1,2,3]\n```', '[1,2,3]'),
        ('```\nnull\n```', 'null'),
        ('```json\n"hello"\n```', '"hello"'),
    ])
    def test_parametrized_fence_variants(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for shared.call_claude()."""

    def _make_claude_client(self, text_response: str) -> MagicMock:
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_content = MagicMock()
        mock_content.text = text_response
        mock_message.content = [mock_content]
        mock_client.messages.create.return_value = mock_message
        return mock_client

    def test_happy_path_returns_text(self):
        expected_text = '{"result": "ok"}'
        mock_client = self._make_claude_client(expected_text)

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == expected_text

    def test_passes_correct_model(self):
        mock_client = self._make_claude_client("text")

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self):
        mock_client = self._make_claude_client("text")

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "my system"
        assert call_kwargs["messages"] == [{"role": "user", "content": "my user"}]
        assert call_kwargs["max_tokens"] == 1024

    def test_default_max_tokens_is_4096(self):
        mock_client = self._make_claude_client("text")

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["max_tokens"] == 4096

    def test_uses_api_key_from_env(self):
        mock_client = self._make_claude_client("text")

        with patch("shared.anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("sys", "usr")

        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_api_exception_propagates(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API failure")

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API failure"):
                shared.call_claude("sys", "usr")

    def test_returns_first_content_block(self):
        """Ensures only content[0].text is returned."""
        mock_client = MagicMock()
        c1, c2 = MagicMock(), MagicMock()
        c1.text = "first"
        c2.text = "second"
        mock_client.messages.create.return_value.content = [c1, c2]

        with patch("shared.anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys", "usr")

        assert result == "first"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for shared.get_repo_files()."""

    def _tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return _make_response(json_data={"content": encoded + "\n"})

    def test_happy_path_returns_matching_files(self):
        tree = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/readme"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/main"},
        ]
        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("# readme content"),
                self._blob_response("print('hello')"),
            ]
            result = shared.get_repo_files("owner", "repo", [".md", ".py"])

        assert "README.md" in result
        assert result["README.md"] == "# readme content"
        assert "main.py" in result
        assert result["main.py"] == "print('hello')"

    def test_filters_by_extension(self):
        tree = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/main"},
            {"type": "blob", "path": "image.png", "url": "https://api.github.com/blob/img"},
            {"type": "blob", "path": "data.json", "url": "https://api.github.com/blob/data"},
        ]
        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("python code"),
                self._blob_response('{"key": "val"}'),
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "main.py" in result
        assert "data.json" in result
        assert "image.png" not in result

    def test_excludes_non_blob_types(self):
        tree = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree/src"},
            {"type": "blob", "path": "src/main.py", "url": "https://api.github.com/blob/main"},
        ]
        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "src/main.py" in result

    def test_respects_max_files_limit(self):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        blob_responses = [self._blob_response(f"content {i}") for i in range(10)]

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree)] + blob_responses
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self):
        with patch("shared.requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions_returns_empty(self):
        tree = [
            {"type": "blob", "path": "image.png", "url": "https://api.github.com/blob/img"},
        ]
        with patch("shared.requests.get") as mock_get:
            mock_get.return_value = self._tree_response(tree)
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_decode_error_skips_file(self):
        """If base64 decode fails, the file is silently skipped."""
        tree = [
            {"type": "blob", "path": "bad.py", "url": "https://api.github.com/blob/bad"},
        ]
        with patch("shared.requests.get") as mock_get:
            bad_blob = _make_response(json_data={"content": "!!!not-valid-base64!!!"})
            mock_get.side_effect = [self._tree_response(tree), bad_blob]
            result = shared.get_repo_files("owner", "repo", [".py"])

        # File skipped, no exception raised
        assert "bad.py" not in result

    def test_constructs_correct_tree_url(self):
        with patch("shared.requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            shared.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo/git/trees/HEAD?recursive=1" in first_call_url

    def test_default_max_files_is_20(self):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"u{i}"}
            for i in range(25)
        ]
        blob_responses = [self._blob_response("c") for _ in range(25)]

        with patch("shared.requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree)] + blob_responses
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert len(result) == 20

    def test_uses_gh_headers(self):
        with patch("shared.requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            shared.get_repo_files("owner", "repo", [".py"])

        _, kwargs = mock_get.call_args_list[0]
        assert kwargs["headers"] == shared.GH_HEADERS


# ===========================================================================
# get_pr_diff
# ===========================================================================

class Test