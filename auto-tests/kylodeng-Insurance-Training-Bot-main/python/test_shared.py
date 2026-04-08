"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, decode errors
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File creation (no SHA) and update (with SHA), fallback URL
- post_pr_comment(): PR comment posting
- send_email(): SendGrid payload construction, success/failure status codes
- email_html(): HTML template generation, SUCCESS/FAILURE colour logic
- write_audit_entry(): Audit log JSON + Markdown construction, output repo write calls

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude client)
  - requests.get / requests.post / requests.put
  - os.environ (via monkeypatch)
- No real network calls are made anywhere

TODOs:
- TODO: Integration test for full workflow round-trip (requires live credentials)
- TODO: Test write_audit_entry with real repo content once audit log format is confirmed complete
"""

import base64
import datetime
import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, Mock, call, patch

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
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(scope="session", autouse=True)
def _patch_env_for_import():
    """Patch env vars before shared.py is first imported."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        # Force (re)import with env in place
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Make the script directory importable
        scripts_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", ".github", "scripts"
        )
        scripts_dir = os.path.abspath(scripts_dir)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import shared as _shared  # noqa: F401

        yield


@pytest.fixture(autouse=True)
def shared_module():
    """Return a freshly-accessible shared module for each test."""
    import shared
    return shared


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data=None, text: str = ""):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self, shared_module):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_surrounding_whitespace(self, shared_module):
        raw = "   \n```json\n{\"a\": 1}\n```\n   "
        result = shared_module.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_multiline_json_in_fence(self, shared_module):
        raw = "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{\n  "a": 1,\n  "b": 2\n}'

    def test_no_closing_fence(self, shared_module):
        """If there's no closing fence the content after the opening line is returned."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_module.clean_json(raw)
        # rsplit on ``` with no match returns the whole string
        assert '{"key": "value"}' in result

    def test_valid_json_parses_after_clean(self, shared_module):
        raw = "```json\n{\"product_name\": \"Generations II\"}\n```"
        cleaned = shared_module.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["product_name"] == "Generations II"

    def test_nested_backticks_not_stripped(self, shared_module):
        """Content without opening ``` is returned as-is."""
        raw = '{"code": "a`b`c"}'
        assert shared_module.clean_json(raw) == '{"code": "a`b`c"}'


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    @patch("anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        content_block = MagicMock()
        content_block.text = "Claude response text"
        mock_client.messages.create.return_value.content = [content_block]

        # Reload to pick up patched Anthropic
        with patch.dict(os.environ, ENV_DEFAULTS):
            result = shared_module.call_claude("sys prompt", "user prompt")

        assert result == "Claude response text"

    @patch("anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        content_block = MagicMock()
        content_block.text = "ok"
        mock_client.messages.create.return_value.content = [content_block]

        shared_module.call_claude("sys", "user", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024
        assert kwargs["model"] == shared_module.MODEL
        assert kwargs["system"] == "sys"
        assert kwargs["messages"] == [{"role": "user", "content": "user"}]

    @patch("anthropic.Anthropic")
    def test_default_max_tokens_is_4096(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        content_block = MagicMock()
        content_block.text = "ok"
        mock_client.messages.create.return_value.content = [content_block]

        shared_module.call_claude("sys", "user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with pytest.raises(RuntimeError, match="API error"):
            shared_module.call_claude("sys", "user")

    @patch("anthropic.Anthropic")
    def test_empty_system_prompt(self, mock_anthropic_cls, shared_module):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client

        content_block = MagicMock()
        content_block.text = "response"
        mock_client.messages.create.return_value.content = [content_block]

        result = shared_module.call_claude("", "user message")
        assert result == "response"


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path: str, item_type: str = "blob", url: str = "http://blob-url"):
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return {"content": encoded + "\n"}

    @patch("requests.get")
    def test_filters_by_extension(self, mock_get, shared_module):
        tree = [
            self._tree_item("file.py"),
            self._tree_item("file.md"),
            self._tree_item("file.txt"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("python content")),
            _make_response(json_data=self._blob_response("md content")),
        ]

        result = shared_module.get_repo_files("owner", "repo", [".py", ".md"])

        assert "file.py" in result
        assert "file.md" in result
        assert "file.txt" not in result

    @patch("requests.get")
    def test_respects_max_files_limit(self, mock_get, shared_module):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        blob_resp = self._blob_response("content")

        responses = [_make_response(json_data={"tree": tree})]
        responses += [_make_response(json_data=blob_resp)] * 5  # only 5 fetched

        mock_get.side_effect = responses

        result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    @patch("requests.get")
    def test_skips_non_blob_items(self, mock_get, shared_module):
        tree = [
            self._tree_item("dir", item_type="tree"),
            self._tree_item("file.py", item_type="blob"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("content")),
        ]

        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "dir" not in result
        assert "file.py" in result

    @patch("requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get, shared_module):
        mock_get.return_value = _make_response(json_data={"tree": []})

        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_missing_tree_key_returns_empty_dict(self, mock_get, shared_module):
        mock_get.return_value = _make_response(json_data={})

        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_decode_error_skips_file(self, mock_get, shared_module):
        tree = [self._tree_item("bad.py")]
        # Return blob with broken/missing content key
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data={}),  # no "content" key → KeyError → except
        ]

        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_correct_url_constructed(self, mock_get, shared_module):
        mock_get.return_value = _make_response(json_data={"tree": []})

        shared_module.get_repo_files("myowner", "myrepo", [".py"])

        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url

    @patch("requests.get")
    def test_file_content_decoded_correctly(self, mock_get, shared_module):
        expected_content = "print('hello world')"
        tree = [self._tree_item("hello.py")]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response(expected_content)),
        ]

        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result["hello.py"] == expected_content

    @patch("requests.get")
    def test_multiple_extensions(self, mock_get, shared_module):
        tree = [
            self._tree_item("a.json"),
            self._tree_item("b.yaml"),
            self._tree_item("c.xml"),
        ]
        mock_get.side_effect = [
            _make_response(json_data={"tree": tree}),
            _make_response(json_data=self._blob_response("{}")),
            _make_response(json_data=self._blob_response("yaml: content")),
        ]

        result = shared_module.get_repo_files("owner", "repo", [".json", ".yaml"])
        assert "a.json" in result
        assert "b.yaml" in result
        assert "c.xml" not in result


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    @patch("requests.get")
    def test_happy_path_returns_diff_text(self, mock_get, shared_module):
        mock_get.return_value = _make_response(text="diff --git a/file.py b/file.py\n+new line")

        result = shared_module.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result
        assert "+new line" in result

    @patch("requests.get")
    def test_url_contains_pr_number(self, mock_get, shared_module):
        mock_get.return_value = _make_response(text="")

        shared_module.get_pr_diff("myowner", "myrepo", 99)

        called_url = mock_get.call_args_list[0][0][0]
        assert "99" in called_url
        assert "myowner" in called_url
        assert "myrepo" in called_url

    @patch("requests.get")
    def test_uses_diff_accept_header(self, mock_get, shared_module):
        mock_get.return_value = _make_response(text="")