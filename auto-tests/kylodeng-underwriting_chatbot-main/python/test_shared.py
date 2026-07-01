"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: Happy path, API response parsing, token passthrough
- clean_json: Stripping markdown fences, plain JSON passthrough, edge cases
- get_repo_files: File fetching, extension filtering, max_files limit, decode errors
- get_pr_diff: Successful diff fetch, truncation at 30000 chars
- write_output_file: Create new file (no SHA), update existing file (with SHA), fallback URL
- post_pr_comment: Successful comment posting
- send_email: Success (200/202), failure warning path
- email_html: SUCCESS/FAILURE status colour, content rendering
- write_audit_entry: (stub — source truncated, see TODO)

Mocks used:
- unittest.mock.patch / MagicMock for: anthropic.Anthropic, requests.get, requests.post, requests.put
- os.environ patched via monkeypatch / patch.dict to supply required env vars

TODOs:
- TODO: write_audit_entry body is truncated in source; full implementation needed to test audit log JSON/Markdown output
- TODO: Integration test for full Claude round-trip requires live ANTHROPIC_API_KEY
- TODO: Verify exact GH_HEADERS forwarded on diff request (Accept override)
"""

import base64
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
FAKE_ENV = {
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
    with patch.dict(os.environ, FAKE_ENV, clear=False):
        # Force (re)import inside the patched environment
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Make the scripts directory importable
        scripts_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", ".github", "scripts"
        )
        sys.path.insert(0, os.path.abspath(scripts_dir))
        import shared as _shared  # noqa: F401 – side-effectful import
        yield


@pytest.fixture()
def shared_module():
    """Return the already-imported shared module."""
    import shared  # noqa: WPS433
    return shared


# ===========================================================================
# Helpers
# ===========================================================================

def _make_requests_response(status_code=200, json_data=None, text=""):
    """Build a minimal mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


def _b64(content: str) -> str:
    return base64.b64encode(content.encode()).decode()


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:

    def test_plain_json_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == raw

    def test_strips_json_fence(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_fence(self, shared_module):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self, shared_module):
        raw = "   \n{\"a\": 1}\n   "
        result = shared_module.clean_json(raw)
        assert result == '{"a": 1}'

    def test_fence_with_whitespace_around_content(self, shared_module):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = shared_module.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_empty_fence(self, shared_module):
        raw = "```json\n\n```"
        result = shared_module.clean_json(raw)
        assert result == ""

    def test_no_closing_fence(self, shared_module):
        """If there is no closing fence, rsplit returns the original after opening strip."""
        raw = "```json\n{\"a\": 1}"
        result = shared_module.clean_json(raw)
        # After split on first \n we get '{"a": 1}'; rsplit on ``` returns same
        assert '{"a": 1}' in result

    def test_nested_json_preserved(self, shared_module):
        data = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        raw = f"```json\n{data}\n```"
        assert shared_module.clean_json(raw) == data

    def test_arabic_content_preserved(self, shared_module):
        data = '{"cancel": "\\u0625\\u0644\\u063a\\u0627\\u0621"}'
        assert shared_module.clean_json(data) == data


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:

    @patch("anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        fake_content = MagicMock()
        fake_content.text = "Hello from Claude"
        mock_client.messages.create.return_value = MagicMock(content=[fake_content])

        result = shared_module.call_claude("system prompt", "user prompt")
        assert result == "Hello from Claude"

    @patch("anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        fake_content = MagicMock()
        fake_content.text = "ok"
        mock_client.messages.create.return_value = MagicMock(content=[fake_content])

        shared_module.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared_module.MODEL

    @patch("anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        fake_content = MagicMock()
        fake_content.text = "ok"
        mock_client.messages.create.return_value = MagicMock(content=[fake_content])

        shared_module.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("anthropic.Anthropic")
    def test_custom_max_tokens(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        fake_content = MagicMock()
        fake_content.text = "ok"
        mock_client.messages.create.return_value = MagicMock(content=[fake_content])

        shared_module.call_claude("sys", "usr", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        fake_content = MagicMock()
        fake_content.text = "ok"
        mock_client.messages.create.return_value = MagicMock(content=[fake_content])

        shared_module.call_claude("my-system", "my-user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my-system"
        assert kwargs["messages"] == [{"role": "user", "content": "my-user"}]

    @patch("anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            shared_module.call_claude("sys", "usr")

    @patch("anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        fake_content = MagicMock()
        fake_content.text = "ok"
        mock_client.messages.create.return_value = MagicMock(content=[fake_content])

        shared_module.call_claude("sys", "usr")
        mock_anthropic_cls.assert_called_once_with(api_key=shared_module.ANTHROPIC_API_KEY)


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:

    def _tree_item(self, path, item_type="blob", url="http://fake/blob"):
        return {"path": path, "type": item_type, "url": url}

    @patch("requests.get")
    def test_happy_path_returns_file_content(self, mock_get, shared_module):
        tree = [self._tree_item("src/main.py")]
        encoded = _b64("print('hello')")
        mock_get.side_effect = [
            _make_requests_response(json_data={"tree": tree}),
            _make_requests_response(json_data={"content": encoded + "\n"}),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "src/main.py" in result
        assert result["src/main.py"] == "print('hello')"

    @patch("requests.get")
    def test_filters_by_extension(self, mock_get, shared_module):
        tree = [
            self._tree_item("main.py"),
            self._tree_item("README.md"),
            self._tree_item("app.js"),
        ]
        encoded_py = _b64("python code")
        encoded_js = _b64("js code")

        def side_effect(url, headers=None):
            if "trees" in url:
                return _make_requests_response(json_data={"tree": tree})
            if "main.py" in url or "blob" in url:
                # Return based on call order via stateful counter – use a list instead
                return _make_requests_response(json_data={"content": encoded_py})

        mock_get.side_effect = [
            _make_requests_response(json_data={"tree": tree}),
            _make_requests_response(json_data={"content": encoded_py}),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "main.py" in result
        assert "README.md" not in result
        assert "app.js" not in result

    @patch("requests.get")
    def test_respects_max_files_limit(self, mock_get, shared_module):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        encoded = _b64("content")
        responses = [_make_requests_response(json_data={"tree": tree})] + [
            _make_requests_response(json_data={"content": encoded}) for _ in range(5)
        ]
        mock_get.side_effect = responses
        result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=5)
        assert len(result) == 5

    @patch("requests.get")
    def test_ignores_non_blob_tree_items(self, mock_get, shared_module):
        tree = [
            {"path": "src", "type": "tree", "url": "http://fake/tree"},
            self._tree_item("src/main.py"),
        ]
        encoded = _b64("python code")
        mock_get.side_effect = [
            _make_requests_response(json_data={"tree": tree}),
            _make_requests_response(json_data={"content": encoded}),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "src/main.py" in result

    @patch("requests.get")
    def test_skips_file_on_decode_error(self, mock_get, shared_module):
        tree = [self._tree_item("broken.py"), self._tree_item("good.py")]
        encoded_good = _b64("good content")
        mock_get.side_effect = [
            _make_requests_response(json_data={"tree": tree}),
            # broken.py: missing content key → KeyError swallowed
            _make_requests_response(json_data={}),
            _make_requests_response(json_data={"content": encoded_good}),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "broken.py" not in result
        assert "good.py" in result

    @patch("requests.get")
    def test_empty_repo_returns_empty_dict(self, mock_get, shared_module):
        mock_get.return_value = _make_requests_response(json_data={"tree": []})
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_multiple_extensions(self, mock_get, shared_module):
        tree = [self._tree_item("app.py"), self._tree_item("index.js")]
        enc_py = _b64("py")
        enc_js = _b64("js")
        mock_get.side_effect = [
            _make_requests_response(json_data={"tree": tree}),
            _make_requests_response(json_data={"content": enc_py}),
            _make_requests_response(json_data={"content": enc_js}),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py", ".js"])
        assert "app.py" in result
        assert "index.js" in result

    @patch("requests.get")
    def test_constructs_correct_tree_url(self, mock_get, shared_module):
        mock_get.return_value = _make_requests_response(json_data={"tree": []})
        