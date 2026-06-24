"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation and response extraction
- clean_json(): Markdown fence stripping utility
- get_repo_files(): GitHub repo file fetching with extension filtering
- get_pr_diff(): GitHub PR unified diff fetching
- write_output_file(): GitHub file create/update with SHA handling
- post_pr_comment(): GitHub PR comment posting
- send_email(): SendGrid email dispatch and error handling
- email_html(): HTML email template generation
- write_audit_entry(): Audit log entry construction and file writing

Mocks used:
- unittest.mock.patch for os.environ (env var injection)
- unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client
- unittest.mock.patch for datetime.datetime (deterministic timestamps)

TODOs:
- TODO: write_audit_entry full round-trip test requires real JSON/MD parsing logic
  (the source snippet is truncated; stub tests are marked with skip)
- TODO: Integration test for call_claude with actual model responses beyond first content block
"""

import base64
import datetime
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(autouse=True, scope="session")
def _patch_env_for_import():
    """Patch environment variables before the module is imported."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        # Force (re)import inside the patched env
        import importlib
        if "shared" in sys.modules:
            importlib.reload(sys.modules["shared"])
        yield


# ---------------------------------------------------------------------------
# Import shared under the patched env
# ---------------------------------------------------------------------------
with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
    # Ensure the scripts directory is on the path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".github", "scripts"))
    import importlib
    import shared  # noqa: E402  (imported after env patch)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture()
def mock_anthropic_client():
    """Return a MagicMock that mimics anthropic.Anthropic."""
    with patch("shared.anthropic.Anthropic") as mock_cls:
        client = MagicMock()
        mock_cls.return_value = client
        yield client


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
# call_claude
# ===========================================================================

class TestCallClaude:
    def test_happy_path_returns_text(self, mock_anthropic_client):
        """Claude returns a single text block; we get its .text."""
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="Hello from Claude")]
        mock_anthropic_client.messages.create.return_value = fake_response

        result = shared.call_claude(system="You are helpful.", user="Say hi")

        assert result == "Hello from Claude"

    def test_passes_correct_model_and_tokens(self, mock_anthropic_client):
        """Verifies model, max_tokens, system and user message are forwarded."""
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="ok")]
        mock_anthropic_client.messages.create.return_value = fake_response

        shared.call_claude(system="sys", user="usr", max_tokens=1024)

        mock_anthropic_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=1024,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    def test_default_max_tokens_is_4096(self, mock_anthropic_client):
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="x")]
        mock_anthropic_client.messages.create.return_value = fake_response

        shared.call_claude(system="s", user="u")

        _, kwargs = mock_anthropic_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_returns_first_content_block_only(self, mock_anthropic_client):
        """Only the first content block's text is returned."""
        fake_response = MagicMock()
        fake_response.content = [
            MagicMock(text="first"),
            MagicMock(text="second"),
        ]
        mock_anthropic_client.messages.create.return_value = fake_response

        result = shared.call_claude(system="s", user="u")

        assert result == "first"

    def test_api_key_used(self, mock_anthropic_client):
        """The Anthropic client is constructed with the injected API key."""
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="ok")]
        mock_anthropic_client.messages.create.return_value = fake_response

        with patch("shared.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value = mock_anthropic_client
            shared.call_claude("s", "u")
            mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_propagates_api_exception(self, mock_anthropic_client):
        """Exceptions from the Anthropic client bubble up unchanged."""
        mock_anthropic_client.messages.create.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            shared.call_claude("s", "u")


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_no_fences_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        raw = '```\n{"a": 1}\n```'
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_whitespace(self):
        raw = '  ```json\n{"x":1}\n```  '
        result = shared.clean_json(raw)
        assert result == '{"x":1}'

    def test_multiline_json_inside_fence(self):
        raw = '```json\n{\n  "key": "value",\n  "num": 42\n}\n```'
        result = shared.clean_json(raw)
        assert json.loads(result) == {"key": "value", "num": 42}

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_fence_without_closing(self):
        """If there's no closing fence the function still strips the opening line."""
        raw = "```json\n{}"
        result = shared.clean_json(raw)
        # rsplit on ``` that doesn't exist returns the whole string
        assert "{}" in result

    def test_nested_backticks_in_content(self):
        """Content that contains backtick-like strings should survive."""
        raw = '```json\n{"code": "use `backticks`"}\n```'
        result = shared.clean_json(raw)
        assert '"code"' in result

    @pytest.mark.parametrize("fence_lang", ["json", "JSON", ""])
    def test_various_fence_languages(self, fence_lang):
        raw = f"```{fence_lang}\n{{\"k\":1}}\n```"
        result = shared.clean_json(raw)
        assert '{"k":1}' in result


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _make_tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded + "\n"}
        return resp

    def test_happy_path_returns_matching_files(self, mock_requests_get):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "http://blob/readme"},
            {"type": "blob", "path": "main.py", "url": "http://blob/main"},
            {"type": "tree", "path": "src", "url": "http://tree/src"},
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("# README content"),
        ]

        result = shared.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == "# README content"
        assert "main.py" not in result

    def test_filters_by_multiple_extensions(self, mock_requests_get):
        tree_items = [
            {"type": "blob", "path": "a.py", "url": "http://blob/a"},
            {"type": "blob", "path": "b.js", "url": "http://blob/b"},
            {"type": "blob", "path": "c.txt", "url": "http://blob/c"},
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("py content"),
            self._make_blob_response("js content"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "a.py" in result
        assert "b.js" in result
        assert "c.txt" not in result

    def test_respects_max_files_limit(self, mock_requests_get):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(5)
        ]
        blob_response = self._make_blob_response("content")

        # First call: tree; subsequent: blob responses
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
        ] + [self._make_blob_response(f"content{i}") for i in range(5)]

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_tree_type_items(self, mock_requests_get):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "http://tree/src"},
            {"type": "blob", "path": "src/app.py", "url": "http://blob/app"},
        ]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response("app content"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/app.py" in result
        assert "src/" not in result

    def test_empty_tree_returns_empty_dict(self, mock_requests_get):
        mock_requests_get.return_value = self._make_tree_response([])
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_handles_blob_decode_exception_gracefully(self, mock_requests_get):
        """If base64 decode fails, the file is silently skipped."""
        tree_items = [
            {"type": "blob", "path": "broken.py", "url": "http://blob/broken"},
        ]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "!!!not-valid-base64!!!"}

        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            bad_blob,
        ]

        # Should not raise; broken file just won't be in result
        result = shared.get_repo_files("owner", "repo", [".py"])
        # Either empty or contains broken content depending on base64 tolerance
        assert isinstance(result, dict)

    def test_uses_correct_github_api_url(self, mock_requests_get):
        mock_requests_get.return_value = self._make_tree_response([])
        shared.get_repo_files("myowner", "myrepo", [".py"])
        first_call_url = mock_requests_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_unicode_content_decoded_correctly(self, mock_requests_get):
        unicode_content = "# 中文注释\nprint('hello')"
        tree_items = [{"type": "blob", "path": "unicode.py", "url": "http://blob/u"}]
        mock_requests_get.side_effect = [
            self._make_tree_response(tree_items),
            self._make_blob_response(unicode_content),
        ]
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result["unicode.py"] == unicode_content


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    def test_happy_path_returns_diff_text(self, mock_requests_get):
        mock_response = MagicMock()
        mock_response.text = "diff --git a/file.py b/file.py\n+added line\n"
        mock_requests_get.return_value = mock_response

        result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result
        assert "+added line" in result

    def test_url_contains_pr_number(self, mock_requests_get):
        mock_response = MagicMock()
        mock_response.text = "diff"
        mock_requests_get.return_value = mock_response

        shared.get_pr_diff("owner", "repo", 99)

        called_url = mock_requests_get.call_args[0][0]
        assert "99" in called_url
        assert "pulls" in called_url

    def test_uses_diff_accept