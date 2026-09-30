"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, decode errors
- get_pr_diff(): PR diff fetching, truncation boundary
- write_output_file(): File creation (no SHA), file update (with SHA), fallback URL
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure status codes
- email_html(): HTML output correctness for SUCCESS/FAILURE status
- write_audit_entry(): Audit log JSON/Markdown construction and repo write calls

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- datetime.datetime is patched where deterministic timestamps are needed

TODOs:
- TODO: Integration test for real Claude API (requires live ANTHROPIC_API_KEY)
- TODO: Integration test for real SendGrid (requires live SENDGRID_API_KEY)
- TODO: Integration test for real GitHub API (requires live GH_TOKEN)
- TODO: write_audit_entry full path test – needs the rest of the source (truncated)
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module with mandatory env vars pre-set
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


def import_shared(extra_env=None):
    """Import (or re-import) shared with controlled env vars."""
    env = {**REQUIRED_ENV, **(extra_env or {})}
    # Remove cached module so env changes take effect
    sys.modules.pop("shared", None)
    with patch.dict("os.environ", env, clear=False):
        import importlib.util, os, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", pathlib.Path(__file__).parent / "../.github/scripts/shared.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# We import once at module level for most tests; individual tests re-import when needed.
with patch.dict("os.environ", REQUIRED_ENV, clear=False):
    # Prevent the real anthropic package from being required at import time
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = MagicMock()
    sys.modules.setdefault("anthropic", fake_anthropic)

    shared = import_shared()


# ===========================================================================
# clean_json
# ===========================================================================


class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_surrounding_whitespace(self):
        raw = "   \n```json\n{}\n```\n   "
        assert shared.clean_json(raw) == "{}"

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_json_without_newline_after_fence(self):
        # Edge: opening fence has no newline — split keeps whole string as second part
        raw = "```{}"
        result = shared.clean_json(raw)
        # Should not crash; result should at least not contain opening fence marker
        assert isinstance(result, str)

    def test_nested_backticks_not_stripped_when_not_at_start(self):
        raw = 'some text ```json\n{}\n```'
        # Does NOT start with ```, so returned as-is (stripped)
        result = shared.clean_json(raw)
        assert result == 'some text ```json\n{}\n```'

    def test_multiline_json_preserved(self):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    def test_returns_string_type(self):
        assert isinstance(shared.clean_json("{}"), str)


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    def _make_response(self, text="Hello"):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_happy_path_returns_text(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("Answer text")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == "Answer text"

    def test_passes_correct_model_and_tokens(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response()

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == shared.MODEL
        assert call_kwargs["max_tokens"] == 1024

    def test_passes_system_and_user_messages(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response()

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")

        call_kwargs = mock_client.messages.create.call_args.kwargs
        assert call_kwargs["system"] == "my system"
        assert call_kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens_is_4096(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response()

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")

        assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 4096

    def test_api_error_propagates(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                shared.call_claude("s", "u")

    def test_empty_system_prompt_accepted(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_response("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("", "user message")

        assert result == "ok"


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    def _make_blob(self, path, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return {"type": "blob", "path": path, "url": f"https://fake/{path}", "content": encoded}

    def _tree_response(self, items):
        mock = MagicMock()
        mock.json.return_value = {"tree": items}
        return mock

    def _content_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        mock = MagicMock()
        mock.json.return_value = {"content": encoded}
        return mock

    def test_returns_matching_files(self):
        tree_item = {"type": "blob", "path": "foo.py", "url": "http://x/foo"}

        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": [tree_item]}

        content_resp = MagicMock()
        content_resp.json.return_value = {"content": base64.b64encode(b"print('hi')").decode()}

        with patch("requests.get", side_effect=[tree_resp, content_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "foo.py" in result
        assert result["foo.py"] == "print('hi')"

    def test_filters_by_extension(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                {"type": "blob", "path": "a.py", "url": "http://x/a"},
                {"type": "blob", "path": "b.md", "url": "http://x/b"},
                {"type": "blob", "path": "c.py", "url": "http://x/c"},
            ]
        }
        content_a = MagicMock()
        content_a.json.return_value = {"content": base64.b64encode(b"# a").decode()}
        content_c = MagicMock()
        content_c.json.return_value = {"content": base64.b64encode(b"# c").decode()}

        with patch("requests.get", side_effect=[tree_resp, content_a, content_c]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "a.py" in result
        assert "c.py" in result
        assert "b.md" not in result

    def test_max_files_limit(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [{"type": "blob", "path": f"f{i}.py", "url": f"http://x/{i}"} for i in range(10)]
        }

        def make_content(i):
            r = MagicMock()
            r.json.return_value = {"content": base64.b64encode(f"file{i}".encode()).decode()}
            return r

        side_effects = [tree_resp] + [make_content(i) for i in range(3)]

        with patch("requests.get", side_effect=side_effects):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                {"type": "tree", "path": "somedir", "url": "http://x/dir"},
                {"type": "blob", "path": "real.py", "url": "http://x/real"},
            ]
        }
        content_resp = MagicMock()
        content_resp.json.return_value = {"content": base64.b64encode(b"code").decode()}

        with patch("requests.get", side_effect=[tree_resp, content_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "somedir" not in result
        assert "real.py" in result

    def test_handles_decode_error_gracefully(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [{"type": "blob", "path": "bad.py", "url": "http://x/bad"}]
        }
        # Return invalid base64 content
        content_resp = MagicMock()
        content_resp.json.return_value = {"content": "!!!NOT_BASE64!!!"}

        with patch("requests.get", side_effect=[tree_resp, content_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        # File should be silently skipped
        assert "bad.py" not in result

    def test_empty_tree_returns_empty_dict(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {
            "tree": [
                {"type": "blob", "path": "a.py", "url": "http://x/a"},
                {"type": "blob", "path": "b.ts", "url": "http://x/b"},
                {"type": "blob", "path": "c.go", "url": "http://x/c"},
            ]
        }
        responses = []
        for content in [b"py", b"ts"]:
            r = MagicMock()
            r.json.return_value = {"content": base64.b64encode(content).decode()}
            responses.append(r)

        with patch("requests.get", side_effect=[tree_resp] + responses):
            result = shared.get_repo_files("owner", "repo", [".py", ".ts"])

        assert "a.py" in result
        assert "b.ts" in result
        assert "c.go" not in result

    def test_correct_url_constructed(self):
        tree_resp = MagicMock()
        tree_resp.json.return_value = {"tree": []}

        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])

        called_url = mock_get.call_args[0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "HEAD" in called_url
        assert "recursive=1" in called_url


# ===========================================================================
# get_pr_diff
# ===========================================================================


class TestGetPrDiff:
    def test_returns_diff_text(self):
        mock_resp = MagicMock()
        mock_resp.text = "--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new\n"

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 42)

        assert "--- a/file" in result

    def test_truncates_at_30000_chars(self):
        long_diff = "x" * 50000
        mock_resp = MagicMock()
        mock_resp.text = long_diff

        with patch("requests.get", return_value=mock_resp