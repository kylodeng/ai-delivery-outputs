"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude: happy path, API error propagation
- clean_json: markdown fence stripping (various formats), plain JSON pass-through, edge cases
- get_repo_files: happy path, extension filtering, max_files limit, base64 decode errors, empty tree
- get_pr_diff: happy path, truncation at 30000 chars
- write_output_file: create new file (no SHA), update existing file (with SHA), missing html_url fallback
- post_pr_comment: happy path, correct URL construction
- send_email: success (200/202), failure warning path
- email_html: SUCCESS status renders green, non-SUCCESS renders red, all placeholders present
- write_audit_entry: tested via mocks for the file-writing calls it triggers

Mocks used:
- unittest.mock.patch for: requests.get, requests.post, requests.put, anthropic.Anthropic
- os.environ patched via monkeypatch fixture before module import
- datetime.datetime patched to produce deterministic timestamps

TODOs:
- TODO: Integration test for write_audit_entry requires the full function body (source code truncated)
- TODO: Test Claude streaming/extended responses once streaming is enabled
- TODO: Test GH_HEADERS token interpolation with varied GH_TOKEN values
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Fixtures – environment setup before the module is imported
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(scope="session", autouse=True)
def patch_env_and_import():
    """Patch environment variables and import shared once for the whole session."""
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        # Remove cached module so it re-reads env vars
        sys.modules.pop("shared", None)
        # Ensure the scripts directory is on the path
        import importlib.util, pathlib
        scripts_dir = str(pathlib.Path(__file__).parent.parent / ".github" / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        yield


@pytest.fixture()
def shared_module():
    """Return the shared module, re-importing with clean env each time."""
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        sys.modules.pop("shared", None)
        import shared
        return shared


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text=""):
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
    """Tests for the clean_json helper."""

    def test_plain_json_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == raw

    def test_strips_json_code_fence(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self, shared_module):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared_module):
        raw = "   {\"key\": \"value\"}   "
        assert shared_module.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self, shared_module):
        raw = "  ```json\n{\"a\":1}\n```  "
        result = shared_module.clean_json(raw)
        assert result == '{"a":1}'

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_multiline_json_in_fence(self, shared_module):
        inner = '{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared_module.clean_json(raw)
        assert result == inner

    def test_no_closing_fence_preserved(self, shared_module):
        """If there's an opening fence but no closing, rsplit still works gracefully."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_module.clean_json(raw)
        # Should at minimum strip the opening fence line
        assert "```json" not in result

    def test_json_array(self, shared_module):
        raw = '```json\n[1, 2, 3]\n```'
        result = shared_module.clean_json(raw)
        assert result == '[1, 2, 3]'

    def test_nested_backticks_not_stripped(self, shared_module):
        """Only the outermost fences should be stripped."""
        raw = '```json\n{"code": "x = `hello`"}\n```'
        result = shared_module.clean_json(raw)
        assert '`hello`' in result


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for the call_claude wrapper."""

    def test_happy_path_returns_text(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Claude says hello")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared_module.call_claude("system prompt", "user prompt")

        assert result == "Claude says hello"

    def test_passes_correct_model_and_tokens(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module.call_claude("sys", "user", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1024
        assert call_kwargs.kwargs["model"] == shared_module.MODEL

    def test_passes_system_and_user(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module.call_claude("my system", "my user")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_default_max_tokens_is_4096(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module.call_claude("sys", "user")

        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 4096

    def test_api_exception_propagates(self, shared_module):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API failure")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API failure"):
                shared_module.call_claude("sys", "user")

    def test_uses_api_key_from_env(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
            shared_module.call_claude("sys", "user")

        mock_anthropic.assert_called_once_with(api_key="test-anthropic-key")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for GitHub repo file fetcher."""

    def _tree_item(self, path, item_type="blob", url="https://api.github.com/blob/abc"):
        return {"type": item_type, "path": path, "url": url}

    def test_happy_path_returns_files(self, shared_module):
        tree = [
            self._tree_item("src/main.py"),
            self._tree_item("src/utils.py"),
        ]
        content_response = {"content": _b64("print('hello')\n")}

        def side_effect(url, headers):
            if "recursive" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=content_response)

        with patch("requests.get", side_effect=side_effect):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert result["src/main.py"] == "print('hello')\n"

    def test_filters_by_extension(self, shared_module):
        tree = [
            self._tree_item("README.md"),
            self._tree_item("main.py"),
            self._tree_item("config.yaml"),
        ]
        content_response = {"content": _b64("content")}

        def side_effect(url, headers):
            if "recursive" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=content_response)

        with patch("requests.get", side_effect=side_effect):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "main.py" in result
        assert "README.md" not in result
        assert "config.yaml" not in result

    def test_max_files_limit(self, shared_module):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        content_response = {"content": _b64("x")}

        def side_effect(url, headers):
            if "recursive" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=content_response)

        with patch("requests.get", side_effect=side_effect):
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self, shared_module):
        with patch("requests.get", return_value=_make_response(json_data={"tree": []})):
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_non_blob_items_skipped(self, shared_module):
        tree = [
            self._tree_item("src", item_type="tree"),
            self._tree_item("main.py", item_type="blob"),
        ]
        content_response = {"content": _b64("code")}

        def side_effect(url, headers):
            if "recursive" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=content_response)

        with patch("requests.get", side_effect=side_effect):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_base64_decode_error_skipped(self, shared_module):
        tree = [self._tree_item("bad.py")]

        def side_effect(url, headers):
            if "recursive" in url:
                return _make_response(json_data={"tree": tree})
            # Missing 'content' key → KeyError triggers the except
            return _make_response(json_data={})

        with patch("requests.get", side_effect=side_effect):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self, shared_module):
        tree = [
            self._tree_item("main.py"),
            self._tree_item("README.md"),
            self._tree_item("app.js"),
        ]
        content_response = {"content": _b64("data")}

        def side_effect(url, headers):
            if "recursive" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data=content_response)

        with patch("requests.get", side_effect=side_effect):
            result = shared_module.get_repo_files("owner", "repo", [".py", ".md"])

        assert "main.py" in result
        assert "README.md" in result
        assert "app.js" not in result

    def test_invalid_base64_content_skipped(self, shared_module):
        tree = [self._tree_item("broken.py")]

        def side_effect(url, headers):
            if "recursive" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data={"content": "!!!not-valid-base64!!!"})

        with patch("requests.get", side_effect=side_effect):
            # Should not raise; the except block swallows it
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_correct_url_constructed(self, shared_module):
        with patch("requests.get", return_value=_make_response(json_data={"tree": []})) as mock_get:
            shared_module.get_repo_files("myowner", "myrepo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo" in first_call_url
        assert "recursive=1" in first_call_url


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetP