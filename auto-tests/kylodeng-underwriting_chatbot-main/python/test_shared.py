"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetch, extension filtering, base64 decoding, max_files limit
- get_pr_diff(): PR diff fetch, truncation
- write_output_file(): file creation (no SHA) and update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid integration, success/failure status codes
- email_html(): HTML generation, status colour logic
- write_audit_entry(): (stub — source truncated, see TODO)

Mocks used:
- unittest.mock.patch for os.environ (all env vars)
- unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client

TODOs:
- TODO: write_audit_entry() — source code is truncated; full behaviour unknown
- TODO: Integration test for call_claude() with real API key (skipped, requires live credentials)
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers to bootstrap the module with controlled env vars
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "fallback-owner",
}


def _load_shared(extra_env: dict | None = None):
    """Import (or re-import) shared.py with a controlled environment."""
    env = {**FAKE_ENV, **(extra_env or {})}
    # Remove cached module so re-import picks up new env
    sys.modules.pop("shared", None)

    # Stub anthropic at import time so the module-level client instantiation
    # in call_claude doesn't actually hit the network.
    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = MagicMock()
    sys.modules["anthropic"] = fake_anthropic

    with patch.dict("os.environ", env, clear=False):
        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", ".github/scripts/shared.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def shared():
    return _load_shared()


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self, shared):
        raw = "```\n{\"hello\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"hello": 1}'

    def test_strips_leading_and_trailing_whitespace(self, shared):
        raw = '   {"a": 1}   '
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_fences_no_content(self, shared):
        raw = "```json\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_multiline_json_in_fence(self, shared):
        inner = '{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_no_closing_fence_leaves_content(self, shared):
        """If there is no closing fence the rsplit returns the whole string."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        assert '"key"' in result

    def test_already_stripped_json_array(self, shared):
        raw = '[1, 2, 3]'
        assert shared.clean_json(raw) == '[1, 2, 3]'

    @pytest.mark.parametrize("raw,expected", [
        ('```json\n{"a":1}\n```', '{"a":1}'),
        ('```\n{"b":2}\n```', '{"b":2}'),
        ('{"c":3}', '{"c":3}'),
        ('  {"d":4}  ', '{"d":4}'),
    ])
    def test_parametrized_variants(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_response(self, text):
        msg = MagicMock()
        msg.content = [MagicMock(text=text)]
        return msg

    def test_returns_text_from_response(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("Hello world")

        with patch("anthropic.Anthropic", return_value=fake_client):
            shared_mod = _load_shared()
            result = shared_mod.call_claude("sys prompt", "user prompt")

        assert result == "Hello world"

    def test_passes_correct_model_and_params(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch("anthropic.Anthropic", return_value=fake_client):
            shared_mod = _load_shared()
            shared_mod.call_claude("system", "user", max_tokens=1024)
            _, kwargs = fake_client.messages.create.call_args
            assert kwargs["model"] == shared_mod.MODEL
            assert kwargs["max_tokens"] == 1024
            assert kwargs["system"] == "system"
            assert kwargs["messages"] == [{"role": "user", "content": "user"}]

    def test_default_max_tokens_is_4096(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.return_value = self._make_response("ok")

        with patch("anthropic.Anthropic", return_value=fake_client):
            shared_mod = _load_shared()
            shared_mod.call_claude("s", "u")
            _, kwargs = fake_client.messages.create.call_args
            assert kwargs["max_tokens"] == 4096

    def test_api_exception_propagates(self, shared):
        fake_client = MagicMock()
        fake_client.messages.create.side_effect = RuntimeError("API failure")

        with patch("anthropic.Anthropic", return_value=fake_client):
            shared_mod = _load_shared()
            with pytest.raises(RuntimeError, match="API failure"):
                shared_mod.call_claude("s", "u")

    @pytest.mark.skip(reason="TODO: requires live ANTHROPIC_API_KEY for integration test")
    def test_live_call_claude(self):
        pass


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _blob_response(self, content: str):
        """Return a requests Response mock carrying base64-encoded content."""
        resp = MagicMock()
        resp.json.return_value = {
            "content": base64.b64encode(content.encode()).decode()
        }
        return resp

    def _tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def test_fetches_matching_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "file.py", "url": "http://example.com/file.py"},
            {"type": "blob", "path": "file.js", "url": "http://example.com/file.js"},
            {"type": "blob", "path": "file.md", "url": "http://example.com/file.md"},
        ]
        py_blob = self._blob_response("print('hello')")
        md_blob = self._blob_response("# readme")

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),   # tree call
                py_blob,                      # file.py blob
                md_blob,                      # file.md blob
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".md"])

        assert "file.py" in result
        assert "file.md" in result
        assert "file.js" not in result
        assert result["file.py"] == "print('hello')"
        assert result["file.md"] == "# readme"

    def test_max_files_limit(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://x.com/{i}"}
            for i in range(10)
        ]
        blob = self._blob_response("content")

        with patch("requests.get") as mock_get:
            # first call = tree, subsequent = blobs
            mock_get.side_effect = [self._tree_response(tree)] + [blob] * 10
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "src/", "url": "http://x.com/src"},
            {"type": "blob", "path": "main.py", "url": "http://x.com/main.py"},
        ]
        blob = self._blob_response("code")

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree), blob]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "src/" not in result

    def test_invalid_base64_skips_file(self, shared):
        tree = [
            {"type": "blob", "path": "bad.py", "url": "http://x.com/bad.py"},
        ]
        bad_resp = MagicMock()
        bad_resp.json.return_value = {"content": "!!!not-valid-base64!!!"}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree), bad_resp]
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Exception is swallowed; result may be empty or contain garbage
        # The important thing is no exception propagates
        assert isinstance(result, dict)

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions_returns_empty(self, shared):
        tree = [
            {"type": "blob", "path": "index.js", "url": "http://x.com/index.js"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree)]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_uses_correct_tree_url(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            shared.get_repo_files("myowner", "myrepo", [".py"])
            called_url = mock_get.call_args_list[0][0][0]

        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    def test_returns_diff_text(self, shared):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/file.py b/file.py\n+new line"

        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result
        assert "+new line" in result

    def test_uses_diff_accept_header(self, shared):
        mock_resp = MagicMock()
        mock_resp.text = "diff content"

        with patch("requests.get", return_value=mock_resp) as mock_get:
            shared.get_pr_diff("owner", "repo", 1)
            _, kwargs = mock_get.call_args
            assert kwargs["headers"]["Accept"] == "application/vnd.github.diff"

    def test_truncates_to_30000_chars(self, shared):
        long_diff = "x" * 50000
        mock_resp = MagicMock()
        mock_resp.text = long_diff

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_correct_pr_url(self, shared):
        mock_resp = MagicMock()
        mock_resp.text = ""

        with patch("requests.get", return_value=mock_resp) as mock_get:
            shared.get_pr_diff("acme", "myrepo", 99)
            url = mock_get.call_args[0][0]

        assert "acme" in url
        assert "myrepo" in url
        assert "99" in url

    def test_short_diff_not_truncated(self, shared):
        short_diff = "small diff"
        mock_resp = MagicMock()
        mock_resp.text = short_diff

        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("o", "r", 1)

        assert result == short_diff


# ===========================================================================
# write_output_file
# ===========================================================================

class TestWriteOutputFile:
    def _make_get_response(self, sha=None):
        resp = MagicMock()
        resp.json.return_value = {"sha": sha} if sha else {}
        return resp

    def _make_put_response(self, html_url="https://github.com/test-owner/ai-delivery-outputs/blob/main/out.md"):
        resp = MagicMock()
        resp.json.return_value = {"content": {"html_url": html_url}}
        return resp

    def test_creates_new_file_without_sha(self, shared):
        get_resp = self._make_get_response(sha