"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree traversal, base64 decoding, extension filtering, max_files limit
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File creation (no SHA) and update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid API call, success/failure status codes
- email_html(): HTML template generation, SUCCESS/FAILURE colour logic
- write_audit_entry(): Audit log construction (partial - source truncated)

Mocks used:
- unittest.mock.patch / MagicMock for:
  - anthropic.Anthropic (Claude client)
  - requests.get, requests.post, requests.put
  - os.environ (via monkeypatch)

TODOs:
- write_audit_entry(): Source code is truncated; full behaviour of JSON/Markdown
  log appending cannot be verified without the complete implementation.
- Integration tests against real GitHub/Claude/SendGrid APIs are intentionally omitted.
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap – must happen BEFORE shared.py is imported because
# the module reads env-vars at import time.
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

for _k, _v in ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

# Make sure the scripts directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".github", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

import shared  # noqa: E402  (import after env setup)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def _reset_module_globals(monkeypatch):
    """Ensure module-level constants stay predictable across tests."""
    monkeypatch.setattr(shared, "OUTPUT_REPO", "ai-delivery-outputs")
    monkeypatch.setattr(shared, "OUTPUT_REPO_OWNER", "test-owner")
    monkeypatch.setattr(shared, "NOTIFY_EMAIL", "notify@example.com")
    monkeypatch.setattr(shared, "SENDER_EMAIL", "sender@example.com")
    monkeypatch.setattr(shared, "SENDGRID_API_KEY", "test-sendgrid-key")
    monkeypatch.setattr(shared, "GH_TOKEN", "test-gh-token")


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    """Tests for shared.call_claude()"""

    def _make_client_mock(self, text: str):
        """Return a mock anthropic.Anthropic client whose messages.create returns *text*."""
        content_block = MagicMock()
        content_block.text = text

        response = MagicMock()
        response.content = [content_block]

        client = MagicMock()
        client.messages.create.return_value = response
        return client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        expected = "Hello from Claude"
        mock_anthropic_cls.return_value = self._make_client_mock(expected)

        result = shared.call_claude("sys prompt", "user prompt")

        assert result == expected

    @patch("shared.anthropic.Anthropic")
    def test_passes_correct_model_and_tokens(self, mock_anthropic_cls):
        client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client

        shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user_messages(self, mock_anthropic_cls):
        client = self._make_client_mock("response")
        mock_anthropic_cls.return_value = client

        shared.call_claude("my system", "my user")

        _, kwargs = client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client

        shared.call_claude("s", "u")

        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_uses_api_key_from_module(self, mock_anthropic_cls):
        client = self._make_client_mock("ok")
        mock_anthropic_cls.return_value = client

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    @patch("shared.anthropic.Anthropic")
    def test_propagates_api_exception(self, mock_anthropic_cls):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API failure")
        mock_anthropic_cls.return_value = client

        with pytest.raises(RuntimeError, match="API failure"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_empty_response_text(self, mock_anthropic_cls):
        mock_anthropic_cls.return_value = self._make_client_mock("")
        result = shared.call_claude("s", "u")
        assert result == ""


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    """Tests for shared.clean_json()"""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == raw

    def test_strips_json_code_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = '  {"key": "value"}  '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_whitespace_padding(self):
        raw = '  ```json\n{"a":1}\n```  '
        result = shared.clean_json(raw)
        assert result == '{"a":1}'

    def test_multiline_json_inside_fence(self):
        inner = '{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_no_fence_array(self):
        raw = '[1, 2, 3]'
        assert shared.clean_json(raw) == raw

    def test_fence_with_extra_newlines(self):
        raw = "```json\n\n{}\n\n```"
        result = shared.clean_json(raw)
        # inner content after stripping outer fence lines
        assert "{}" in result

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   \n  ") == ""

    def test_returns_valid_parseable_json(self):
        raw = '```json\n{"Age": 34.57, "model_type": "CatBoostClassifier"}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_type"] == "CatBoostClassifier"

    def test_no_double_stripping(self):
        """A JSON string that contains backticks in a value should survive."""
        raw = '{"code": "some `backtick` value"}'
        result = shared.clean_json(raw)
        assert result == raw


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    """Tests for shared.get_repo_files()"""

    def _tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    @patch("shared.requests.get")
    def test_happy_path_fetches_matching_files(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "backend/model_card.json", "url": "http://blob/1"},
            {"type": "blob", "path": "README.md", "url": "http://blob/2"},
        ]
        blob_content = '{"model_name": "Underwriting Risk Classification"}'

        mock_get.side_effect = [
            self._tree_response(tree_items),
            self._blob_response(blob_content),
        ]

        result = shared.get_repo_files("owner", "repo", [".json"])

        assert "backend/model_card.json" in result
        assert "README.md" not in result
        assert json.loads(result["backend/model_card.json"])["model_name"] == "Underwriting Risk Classification"

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "file.py", "url": "http://blob/py"},
            {"type": "blob", "path": "file.json", "url": "http://blob/json"},
            {"type": "blob", "path": "file.md", "url": "http://blob/md"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree_items),
            self._blob_response("{}"),
            self._blob_response("# readme"),
        ]

        result = shared.get_repo_files("o", "r", [".json", ".md"])

        assert "file.py" not in result
        assert "file.json" in result
        assert "file.md" in result

    @patch("shared.requests.get")
    def test_respects_max_files_limit(self, mock_get):
        tree_items = [
            {"type": "blob", "path": f"file{i}.json", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_resp = self._blob_response("{}")

        # First call = tree, subsequent = blob fetches (only up to max_files=3)
        mock_get.side_effect = [self._tree_response(tree_items)] + [blob_resp] * 3

        result = shared.get_repo_files("o", "r", [".json"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_tree_items(self, mock_get):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "http://tree/1"},
            {"type": "blob", "path": "src/main.py", "url": "http://blob/1"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree_items),
            self._blob_response("print('hello')"),
        ]

        result = shared.get_repo_files("o", "r", [".py"])
        assert "src/" not in result
        assert "src/main.py" in result

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = self._tree_response([])
        result = shared.get_repo_files("o", "r", [".json"])
        assert result == {}

    @patch("shared.requests.get")
    def test_blob_decode_error_is_silenced(self, mock_get):
        """If decoding fails, the file is simply skipped."""
        tree_items = [
            {"type": "blob", "path": "bad.json", "url": "http://blob/bad"},
        ]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "NOT_VALID_BASE64!!!"}

        mock_get.side_effect = [self._tree_response(tree_items), bad_blob]

        result = shared.get_repo_files("o", "r", [".json"])
        # Should not raise; bad file skipped
        assert "bad.json" not in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree_items = [
            {"type": "blob", "path": "a.json", "url": "http://blob/a"},
            {"type": "blob", "path": "b.md", "url": "http://blob/b"},
            {"type": "blob", "path": "c.txt", "url": "http://blob/c"},
        ]
        mock_get.side_effect = [
            self._tree_response(tree_items),
            self._blob_response("{}"),
            self._blob_response("# markdown"),
        ]

        result = shared.get_repo_files("o", "r", [".json", ".md"])
        assert len(result) == 2
        assert "c.txt" not in result

    @patch("shared.requests.get")
    def test_constructs_correct_tree_url(self, mock_get):
        mock_get.return_value = self._tree_response([])
        shared.get_repo_files("myowner", "myrepo", [".py"])

        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner/myrepo/git/trees/HEAD" in called_url
        assert "recursive=1" in called_url


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    """Tests for shared.get_pr_diff()"""

    @patch("shared.requests.get")
    def test_returns_diff_text(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "diff --git a/file.py b/file.py\n+added line\n"
        mock_get.return_value = mock_resp

        result = shared.get_pr