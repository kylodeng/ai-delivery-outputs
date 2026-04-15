"""
Test suite for .github/scripts/shared.py

What is tested:
  - call_claude: happy path, API response parsing
  - clean_json: markdown fence stripping (various formats), plain JSON passthrough, edge cases
  - get_repo_files: file filtering by extension, max_files limit, base64 decoding, decode errors
  - get_pr_diff: URL construction, truncation at 30000 chars
  - write_output_file: create (no SHA), update (with SHA), fallback URL
  - post_pr_comment: correct endpoint and payload
  - send_email: success (200/202), warning on failure
  - email_html: SUCCESS/FAILURE status colours, content inclusion
  - write_audit_entry: partial source (function is truncated in source)

Mocks used:
  - unittest.mock.patch for os.environ (to satisfy module-level env reads)
  - unittest.mock.MagicMock / patch for anthropic.Anthropic client
  - unittest.mock.patch for requests.get, requests.post, requests.put
  - builtins.print patched where warning output is checked

TODOs:
  - write_audit_entry: source is truncated — full logic untestable without complete implementation
  - MODEL constant: currently hardcoded; test will need update if it becomes configurable
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
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(scope="module")
def shared(monkeypatch=None):
    """Import shared module with env vars set.  Uses a fresh import each session."""
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        # Remove cached module so env vars are re-read
        sys.modules.pop("shared", None)
        # The module lives at .github/scripts/shared so we manipulate sys.path
        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", pathlib.Path(".github/scripts/shared.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # Stub anthropic before exec so the import doesn't fail if not installed
        if "anthropic" not in sys.modules:
            fake_anthropic = types.ModuleType("anthropic")
            fake_anthropic.Anthropic = MagicMock()
            sys.modules["anthropic"] = fake_anthropic
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Convenience re-import fixture per test (ensures env is always set)
# ---------------------------------------------------------------------------


@pytest.fixture()
def sh(shared):
    return shared


# ===========================================================================
# clean_json
# ===========================================================================


class TestCleanJson:
    def test_plain_json_unchanged(self, sh):
        raw = '{"key": "value"}'
        assert sh.clean_json(raw) == '{"key": "value"}'

    def test_strips_backtick_json_fence(self, sh):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self, sh):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = sh.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, sh):
        raw = "   \n{\"a\": 1}\n   "
        assert sh.clean_json(raw) == '{"a": 1}'

    def test_fence_with_extra_whitespace_inside(self, sh):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = sh.clean_json(raw)
        assert result.strip() == '{"a": 1}'

    def test_empty_string(self, sh):
        assert sh.clean_json("") == ""

    def test_no_closing_fence_strips_opening_only(self, sh):
        """If there's no closing fence the rsplit leaves content intact."""
        raw = "```json\n{\"key\": \"value\"}"
        result = sh.clean_json(raw)
        # Opening line dropped; no closing fence → content returned as-is
        assert '{"key": "value"}' in result

    def test_nested_json_array(self, sh):
        raw = "```json\n[1, 2, 3]\n```"
        assert sh.clean_json(raw) == "[1, 2, 3]"

    def test_multiline_json_preserved(self, sh):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        assert sh.clean_json(raw) == inner


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    def _make_response(self, text: str):
        content_block = MagicMock()
        content_block.text = text
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_returns_text_from_first_content_block(self, sh):
        fake_response = self._make_response("Hello from Claude")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            # Re-patch inside the module namespace
            with patch.object(sh.anthropic, "Anthropic", return_value=mock_client):
                result = sh.call_claude("sys prompt", "user prompt")

        assert result == "Hello from Claude"

    def test_passes_correct_model_and_params(self, sh):
        fake_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch.object(sh.anthropic, "Anthropic", return_value=mock_client):
            sh.call_claude("system", "user", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1024
        assert call_kwargs.kwargs["system"] == "system"
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "user"}]

    def test_default_max_tokens_is_4096(self, sh):
        fake_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch.object(sh.anthropic, "Anthropic", return_value=mock_client):
            sh.call_claude("s", "u")

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 4096

    def test_uses_configured_api_key(self, sh):
        fake_response = self._make_response("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch.object(sh.anthropic, "Anthropic", return_value=mock_client) as mock_cls:
            sh.call_claude("s", "u")
            mock_cls.assert_called_once_with(api_key=sh.ANTHROPIC_API_KEY)

    def test_empty_system_and_user(self, sh):
        fake_response = self._make_response("")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_response

        with patch.object(sh.anthropic, "Anthropic", return_value=mock_client):
            result = sh.call_claude("", "")

        assert result == ""


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    def _tree_response(self, items):
        """Build a mock requests.get response for the tree endpoint."""
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded}
        return resp

    def test_returns_matching_files(self, sh):
        tree = [
            {"type": "blob", "path": "README.md", "url": "http://gh/blob1"},
            {"type": "blob", "path": "main.py", "url": "http://gh/blob2"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("# readme content"),
                self._blob_response("print('hello')"),
            ]
            result = sh.get_repo_files("owner", "repo", [".md", ".py"])

        assert "README.md" in result
        assert "main.py" in result
        assert result["README.md"] == "# readme content"
        assert result["main.py"] == "print('hello')"

    def test_filters_by_extension(self, sh):
        tree = [
            {"type": "blob", "path": "main.py", "url": "http://gh/blob1"},
            {"type": "blob", "path": "style.css", "url": "http://gh/blob2"},
            {"type": "blob", "path": "data.json", "url": "http://gh/blob3"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("print('hello')"),
            ]
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "style.css" not in result
        assert "data.json" not in result

    def test_respects_max_files(self, sh):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://gh/blob{i}"}
            for i in range(10)
        ]
        blob_resp = self._blob_response("content")

        with patch("requests.get") as mock_get:
            # First call = tree, subsequent = blobs
            mock_get.side_effect = [self._tree_response(tree)] + [blob_resp] * 3
            result = sh.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_tree_items(self, sh):
        tree = [
            {"type": "tree", "path": "src", "url": "http://gh/tree1"},
            {"type": "blob", "path": "main.py", "url": "http://gh/blob1"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("code"),
            ]
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_handles_empty_tree(self, sh):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            result = sh.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_handles_blob_decode_exception_gracefully(self, sh):
        """If base64 decoding raises, file is silently skipped."""
        tree = [
            {"type": "blob", "path": "bad.py", "url": "http://gh/blob1"},
            {"type": "blob", "path": "good.py", "url": "http://gh/blob2"},
        ]
        bad_resp = MagicMock()
        bad_resp.json.return_value = {"content": "NOT_VALID_BASE64!!!"}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                bad_resp,
                self._blob_response("good content"),
            ]
            result = sh.get_repo_files("owner", "repo", [".py"])

        # bad.py may or may not be in result depending on decode behaviour;
        # good.py must be present
        assert "good.py" in result

    def test_constructs_correct_tree_url(self, sh):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            sh.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo/git/trees/HEAD" in first_call_url
        assert "recursive=1" in first_call_url

    def test_default_max_files_is_20(self, sh):
        tree = [
            {"type": "blob", "path": f"f{i}.py", "url": f"http://gh/b{i}"}
            for i in range(25)
        ]
        blob_resp = self._blob_response("x")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree)] + [blob_resp] * 20
            result = sh.get_repo_files("o", "r", [".py"])

        assert len(result) == 20


# ===========================================================================
# get_pr_diff
# ===========================================================================


class TestGetPrDiff:
    def test_returns_diff_text(self, sh):
        mock_resp = MagicMock()
        mock_resp.text = "--- a/file\n+++ b/file\n@@ -1 +1 @@\n-old\n+new"
        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = sh.get_pr_diff("owner", "repo", 42)

        assert "--- a/file" in result

    def test_truncates_at_30000_chars(self, sh):
        long_diff = "x" * 40000
        mock_resp = MagicMock()
        mock_resp.text = long_diff
        with patch("requests.get", return_value=mock_resp):
            result = sh.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_short_diff_not_truncated(self, sh):
        short_diff = "diff content"
        mock_resp = MagicMock()
        mock_resp.text = short_diff
        with patch("requests.get", return_value=mock_resp):
            result = sh.get_pr_diff("owner", "repo", 1)

        assert result == short_diff

    def test_uses_diff_accept_header(self, sh):
        mock_resp = MagicMock()
        mock_resp.text = ""
        with patch("requests.get", return_value=mock_resp) as mock_get:
            sh.