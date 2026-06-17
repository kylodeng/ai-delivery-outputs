"""
Tests for .github/scripts/shared.py

What is tested:
- call_claude: happy path, response extraction
- clean_json: markdown fence stripping (various formats), plain JSON passthrough, edge cases
- get_repo_files: happy path, extension filtering, max_files limit, base64 decode errors, empty tree
- get_pr_diff: happy path, truncation at 30000 chars
- write_output_file: create new file (no SHA), update existing file (with SHA), fallback URL
- post_pr_comment: happy path, correct URL/payload
- send_email: success (200, 202), failure warning
- email_html: SUCCESS status, FAILURE status, content checks
- write_audit_entry: tested via stub (source truncated in provided code)

Mocks used:
- unittest.mock.patch for: anthropic.Anthropic, requests.get, requests.post, requests.put
- os.environ patched via monkeypatch / patch.dict
- datetime.datetime patched for deterministic timestamps

TODOs:
- write_audit_entry: source code is truncated — full logic untestable without complete implementation
- call_claude: extended token/model parameter validation requires real API contract knowledge
"""

import base64
import json
import os
import sys
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen BEFORE importing shared.py
# ---------------------------------------------------------------------------
ENV_VARS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "fallback-owner",
}


@pytest.fixture(autouse=True, scope="session")
def patch_env():
    with patch.dict(os.environ, ENV_VARS, clear=False):
        # Ensure shared.py is (re)imported with patched env
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Insert the script directory into path
        script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        sys.path.insert(0, script_dir)
        # Also try relative path for CI
        alt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, alt_dir)
        yield


def import_shared():
    """Import (or re-use) the shared module after env is patched."""
    if "shared" in sys.modules:
        return sys.modules["shared"]
    # Try multiple likely locations
    for path in [
        os.path.join(os.path.dirname(__file__), ".github", "scripts"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts"),
    ]:
        if path not in sys.path:
            sys.path.insert(0, path)
    import importlib
    return importlib.import_module("shared")


# ---------------------------------------------------------------------------
# Module-level import with env already set
# ---------------------------------------------------------------------------
with patch.dict(os.environ, ENV_VARS, clear=False):
    # Remove cached module if any
    sys.modules.pop("shared", None)
    _script_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".github", "scripts"
    )
    if _script_path not in sys.path:
        sys.path.insert(0, _script_path)
    import shared  # noqa: E402  (must come after env patch)


# ===========================================================================
# clean_json
# ===========================================================================
class TestCleanJson:
    """Tests for clean_json — strips markdown code fences from Claude responses."""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = '   {"key": "value"}   '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_fence_with_whitespace_around_content(self):
        raw = "```json\n\n  {\"a\": 1}  \n\n```"
        result = shared.clean_json(raw)
        # Inner content should be preserved stripped of outer whitespace
        assert '{"a": 1}' in result

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_multiline_json_in_fence(self):
        raw = '```json\n{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"
        assert parsed["model_type"] == "CatBoostClassifier"

    def test_no_closing_fence_returns_partial(self):
        """If there's an opening fence but no closing, rsplit still works."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # Should at least drop the opening fence line
        assert "```json" not in result

    def test_nested_json_array(self):
        raw = '```json\n[1, 2, 3]\n```'
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"

    def test_arabic_content_preserved(self):
        """Verify unicode content (e.g. Arabic translations) is preserved."""
        arabic = '{"cancel": "\u0625\u0644\u063a\u0627\u0621"}'
        raw = f"```json\n{arabic}\n```"
        result = shared.clean_json(raw)
        assert "\u0625\u0644\u063a\u0627\u0621" in result


# ===========================================================================
# call_claude
# ===========================================================================
class TestCallClaude:
    """Tests for call_claude — wraps the Anthropic messages API."""

    def _make_mock_client(self, text: str):
        mock_content = MagicMock()
        mock_content.text = text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    @patch("shared.anthropic.Anthropic")
    def test_happy_path_returns_text(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("Hello from Claude")
        mock_anthropic_cls.return_value = mock_client

        result = shared.call_claude("system prompt", "user prompt")

        assert result == "Hello from Claude"

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user_messages(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("response")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("sys", "usr", max_tokens=1024)

        mock_client.messages.create.assert_called_once_with(
            model=shared.MODEL,
            max_tokens=1024,
            system="sys",
            messages=[{"role": "user", "content": "usr"}],
        )

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_api_key_passed_to_client(self, mock_anthropic_cls):
        mock_client = self._make_mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key="test-anthropic-key")

    @patch("shared.anthropic.Anthropic")
    def test_returns_first_content_block(self, mock_anthropic_cls):
        """Ensures only content[0].text is returned."""
        mock_content_0 = MagicMock()
        mock_content_0.text = "first block"
        mock_content_1 = MagicMock()
        mock_content_1.text = "second block"
        mock_response = MagicMock()
        mock_response.content = [mock_content_0, mock_content_1]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        result = shared.call_claude("s", "u")
        assert result == "first block"

    @patch("shared.anthropic.Anthropic")
    def test_api_exception_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(Exception, match="API error"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_json_response_round_trip(self, mock_anthropic_cls):
        """Simulate Claude returning JSON wrapped in fences."""
        json_content = '```json\n{"model_name": "Underwriting Risk Classification"}\n```'
        mock_client = self._make_mock_client(json_content)
        mock_anthropic_cls.return_value = mock_client

        raw = shared.call_claude("s", "u")
        cleaned = shared.clean_json(raw)
        parsed = json.loads(cleaned)
        assert parsed["model_name"] == "Underwriting Risk Classification"


# ===========================================================================
# get_repo_files
# ===========================================================================
class TestGetRepoFiles:
    """Tests for get_repo_files — fetches and decodes repo file contents."""

    def _encoded(self, text: str) -> str:
        return base64.b64encode(text.encode()).decode() + "\n"

    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, text: str):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": self._encoded(text)}
        return mock_resp

    @patch("shared.requests.get")
    def test_happy_path_returns_file_contents(self, mock_get):
        tree = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/1"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("# Hello World"),
        ]

        result = shared.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == "# Hello World"

    @patch("shared.requests.get")
    def test_filters_by_extension(self, mock_get):
        tree = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "config.json", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/3"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("print('hello')"),
            self._make_blob_response('{"key": "value"}'),
        ]

        result = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "main.py" in result
        assert "config.json" in result
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_respects_max_files_limit(self, mock_get):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        blob_resp = self._make_blob_response("content")
        # tree call + up to 3 blob calls
        mock_get.side_effect = [self._make_tree_response(tree)] + [
            self._make_blob_response(f"content{i}") for i in range(10)
        ]

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/1"},
        ]
        mock_get.side_effect = [
            self._make_tree_response(tree),
            self._make_blob_response("code"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "app.py" in result

    @patch("shared.requests.get")
    def test_empty_tree_returns_empty_dict(self, mock_get):
        mock_get.return_value = self._make_tree_response([])
        result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("shared.requests.get")
    def test_skips_file_on_decode_error(self, mock_get):
        tree = [
            {"type": "blob", "path": "binary.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "good.py", "url": "https://api.github.com/blob/2"},
        ]
        bad_blob = MagicMock()
        bad_blob.json.return_value = {"content": "!!!not-valid-base64!!!"}

        mock_get.side_effect = [
            self._make_tree_response(tree),
            bad_blob,
            self._make_blob_response("good content"),
        ]

        result = shared.get_repo_files("owner", "repo", [".py"])
        # bad file skipped, good file present
        assert "good.