"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response parsing
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree API fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File creation (no SHA), file update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid integration, success/failure status codes
- email_html(): HTML template generation, SUCCESS/FAILURE coloring
- write_audit_entry(): Audit log writing (stubbed — incomplete source)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude client)
  - requests.get, requests.post, requests.put
  - os.environ (environment variables)

TODOs:
- write_audit_entry(): Source code is truncated; full behavior cannot be tested without complete implementation
- call_claude(): Extended token/model validation tests require real API schema knowledge
"""

import base64
import json
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------
_ENV_PATCH = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

with patch.dict(os.environ, _ENV_PATCH, clear=False):
    import importlib
    import sys
    # Remove cached module if present so env vars are picked up fresh
    sys.modules.pop("shared", None)
    sys.modules.pop(".github.scripts.shared", None)

    import importlib.util, pathlib
    _spec = importlib.util.spec_from_file_location(
        "shared",
        pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
    )
    shared = importlib.util.module_from_spec(_spec)
    with patch.dict(os.environ, _ENV_PATCH, clear=False):
        _spec.loader.exec_module(shared)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text=""):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


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

    def test_strips_leading_trailing_whitespace(self):
        raw = "   {\"key\": \"value\"}   "
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_fence_with_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_multiline_json_in_fence(self):
        raw = '```json\n{\n  "tool": "underwriting",\n  "status": "ok"\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["tool"] == "underwriting"

    def test_fence_without_closing(self):
        # No closing fence — just drops the opening line
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        assert '{"key": "value"}' in result

    def test_already_clean_array(self):
        raw = '[1, 2, 3]'
        assert shared.clean_json(raw) == '[1, 2, 3]'

    def test_nested_backticks_in_content(self):
        # Content that has backticks but doesn't start with ```
        raw = 'use `code` here'
        assert shared.clean_json(raw) == 'use `code` here'


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    @patch("anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", mock_anthropic_cls):
            result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello from Claude"

    @patch("anthropic.Anthropic")
    def test_passes_correct_model(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", mock_anthropic_cls):
            shared.call_claude("sys", "user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    @patch("anthropic.Anthropic")
    def test_passes_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", mock_anthropic_cls):
            shared.call_claude("sys", "user", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    @patch("anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", mock_anthropic_cls):
            shared.call_claude("sys", "user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", mock_anthropic_cls):
            shared.call_claude("my system prompt", "my user prompt")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system prompt"
        assert kwargs["messages"] == [{"role": "user", "content": "my user prompt"}]

    @patch("anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_client.messages.create.side_effect = Exception("API error")

        with patch.object(shared.anthropic, "Anthropic", mock_anthropic_cls):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("sys", "user")

    @patch("anthropic.Anthropic")
    def test_uses_api_key_from_env(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_anthropic_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch.object(shared.anthropic, "Anthropic", mock_anthropic_cls):
            shared.call_claude("sys", "user")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _make_blob(self, path, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return {
            "type": "blob",
            "path": path,
            "url": f"https://api.github.com/repos/owner/repo/git/blobs/abc_{path}",
            "content": encoded,
        }

    @patch("requests.get")
    def test_happy_path_filters_by_extension(self, mock_get):
        tree = [
            self._make_blob("backend/model_card.json", '{"model_name": "Underwriting Risk Classification"}'),
            self._make_blob("README.md", "# readme"),
            self._make_blob("config.json", '{"key": "val"}'),
        ]
        blob_responses = {
            item["url"]: _make_response(json_data={"content": item["content"]})
            for item in tree
            if item["path"].endswith(".json")
        }

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return blob_responses.get(url, _make_response(json_data={}))

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".json"])

        assert "backend/model_card.json" in result
        assert "config.json" in result
        assert "README.md" not in result

    @patch("requests.get")
    def test_respects_max_files(self, mock_get):
        tree = [self._make_blob(f"file{i}.json", f'{{"i": {i}}}') for i in range(10)]
        blob_content = base64.b64encode(b'{"i": 0}').decode()

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data={"content": blob_content})

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".json"], max_files=3)
        assert len(result) == 3

    @patch("requests.get")
    def test_empty_repo_returns_empty_dict(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree"},
            self._make_blob("main.py", "print('hello')"),
        ]
        blob_content = base64.b64encode(b"print('hello')").decode()

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data={"content": blob_content})

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "main.py" in result
        assert "src" not in result

    @patch("requests.get")
    def test_multiple_extensions(self, mock_get):
        tree = [
            self._make_blob("app.py", "x=1"),
            self._make_blob("style.css", "body{}"),
            self._make_blob("notes.txt", "hello"),
        ]

        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            for item in tree:
                if item["url"] == url:
                    return _make_response(json_data={"content": item["content"]})
            return _make_response(json_data={})

        mock_get.side_effect = side_effect
        result = shared.get_repo_files("owner", "repo", [".py", ".css"])
        assert "app.py" in result
        assert "style.css" in result
        assert "notes.txt" not in result

    @patch("requests.get")
    def test_decoding_error_skipped_gracefully(self, mock_get):
        tree = [self._make_blob("bad.json", "data")]
        # Return invalid base64
        def side_effect(url, headers=None):
            if "git/trees" in url:
                return _make_response(json_data={"tree": tree})
            return _make_response(json_data={"content": "!!!not valid base64!!!"})

        mock_get.side_effect = side_effect
        # Should not raise; bad file is skipped
        result = shared.get_repo_files("owner", "repo", [".json"])
        assert "bad.json" not in result

    @patch("requests.get")
    def test_correct_url_constructed(self, mock_get):
        mock_get.return_value = _make_response(json_data={"tree": []})
        shared.get_repo_files("myowner", "myrepo", [".json"])
        call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in call_url
        assert "myrepo" in call_url
        assert "recursive=1" in call_url

    @patch("requests.get")
    def test_decoded_content_correct(self, mock_get):
        content = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBo