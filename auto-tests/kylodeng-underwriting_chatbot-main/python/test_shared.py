"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree traversal, extension filtering, base64 decoding, max_files limit
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): file create (no SHA) and update (with SHA) paths
- post_pr_comment(): PR comment posting
- send_email(): SendGrid success and failure paths
- email_html(): HTML output structure and status colour logic
- write_audit_entry(): audit log creation (stub — requires further context)

Mocks used:
- unittest.mock.patch / MagicMock for: anthropic.Anthropic, requests.get, requests.post, requests.put
- Environment variables patched via os.environ before module import

TODOs:
- TODO: write_audit_entry full coverage requires the rest of the function body (source truncated)
- TODO: get_repo_files error path when base64 content is missing/malformed needs real fixture data
- TODO: Integration-level test for call_claude with actual model response shape
"""

import base64
import datetime
import importlib
import json
import os
import sys
import types
import unittest.mock as mock
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Environment setup – must happen BEFORE shared.py is imported
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


@pytest.fixture(autouse=True, scope="session")
def _patch_env():
    """Patch environment variables for the entire test session."""
    with patch.dict(os.environ, ENV_DEFAULTS, clear=False):
        yield


# ---------------------------------------------------------------------------
# Import shared after env is patched.  We re-import each time via importlib
# so that module-level constants pick up the patched env.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def shared():
    """Return the shared module, importing it once per session."""
    # Remove cached import if present so we get a fresh load with patched env
    sys.modules.pop("shared", None)
    # Add the script directory to path
    script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Also try relative path resolution for environments where the test runs
    # from the repo root
    alt_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
    if alt_dir not in sys.path:
        sys.path.insert(0, alt_dir)

    import shared as _shared
    return _shared


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_response_mock(status_code=200, json_data=None, text=""):
    """Build a mock requests.Response-like object."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data if json_data is not None else {}
    m.text = text
    return m


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ===========================================================================
# clean_json
# ===========================================================================
class TestCleanJson:
    def test_no_fences_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self, shared):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_whitespace(self, shared):
        raw = '   ```json\n{"a": 1}\n```   '
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_multiline_json_no_fence(self, shared):
        raw = '{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}'
        assert shared.clean_json(raw) == raw.strip()

    def test_nested_backtick_content_preserved(self, shared):
        # Backticks inside the JSON value should not be eaten
        raw = '```json\n{"code": "x = `y`"}\n```'
        result = shared.clean_json(raw)
        assert '"code"' in result

    def test_fence_without_closing(self, shared):
        # Only opening fence, no closing — should still strip the first line
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        # rsplit on ``` with no closing leaves the content intact
        assert '{"key": "value"}' in result

    @pytest.mark.parametrize("raw,expected", [
        ('```json\n[]\n```', '[]'),
        ('```\nnull\n```', 'null'),
        ('```json\n"string"\n```', '"string"'),
    ])
    def test_various_json_types(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================
class TestCallClaude:
    def test_happy_path_returns_text(self, shared):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user message")

        assert result == "Hello from Claude"

    def test_passes_correct_model(self, shared):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_default_max_tokens(self, shared):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_passes_system_and_user(self, shared):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_uses_api_key_from_env(self, shared):
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="ok")]
            mock_client.messages.create.return_value = mock_response
            mock_anthropic_cls.return_value = mock_client

            shared.call_claude("s", "u")

        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_raises_on_api_error(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("sys", "usr")

    def test_returns_first_content_block(self, shared):
        mock_client = MagicMock()
        mock_response = MagicMock()
        block1 = MagicMock(text="first block")
        block2 = MagicMock(text="second block")
        mock_response.content = [block1, block2]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u")

        assert result == "first block"


# ===========================================================================
# get_repo_files
# ===========================================================================
class TestGetRepoFiles:
    def _tree_response(self, items):
        return _make_response_mock(json_data={"tree": items})

    def _blob_response(self, content: str):
        return _make_response_mock(json_data={"content": _b64(content) + "\n"})

    def test_happy_path_single_file(self, shared):
        tree_item = {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/abc"}
        tree_resp = self._tree_response([tree_item])
        blob_resp = self._blob_response("# Hello World")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".md"])

        assert "README.md" in result
        assert result["README.md"] == "# Hello World"

    def test_filters_by_extension(self, shared):
        items = [
            {"type": "blob", "path": "main.py", "url": "url1"},
            {"type": "blob", "path": "data.json", "url": "url2"},
            {"type": "blob", "path": "README.md", "url": "url3"},
        ]
        tree_resp = self._tree_response(items)
        blob_py = self._blob_response("print('hello')")
        blob_json = self._blob_response('{"key": "value"}')

        with patch("requests.get", side_effect=[tree_resp, blob_py, blob_json]):
            result = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "main.py" in result
        assert "data.json" in result
        assert "README.md" not in result

    def test_max_files_limit(self, shared):
        items = [{"type": "blob", "path": f"file{i}.py", "url": f"url{i}"} for i in range(10)]
        tree_resp = self._tree_response(items)
        blob_resps = [self._blob_response(f"content{i}") for i in range(3)]

        with patch("requests.get", side_effect=[tree_resp] + blob_resps):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_tree_entries(self, shared):
        items = [
            {"type": "tree", "path": "src/", "url": "url_tree"},
            {"type": "blob", "path": "src/main.py", "url": "url_blob"},
        ]
        tree_resp = self._tree_response(items)
        blob_resp = self._blob_response("# code")

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "src/main.py" in result

    def test_empty_tree(self, shared):
        tree_resp = self._tree_response([])

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions(self, shared):
        items = [{"type": "blob", "path": "file.rb", "url": "url1"}]
        tree_resp = self._tree_response(items)

        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_handles_decode_error_gracefully(self, shared):
        items = [{"type": "blob", "path": "bad.py", "url": "url1"}]
        tree_resp = self._tree_response(items)
        # Return a blob with no "content" key to trigger the except clause
        bad_blob_resp = _make_response_mock(json_data={})

        with patch("requests.get", side_effect=[tree_resp, bad_blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Should silently skip the bad file
        assert "bad.py" not in result

    def test_correct_url_construction(self, shared):
        tree_resp = self._tree_response([])

        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("my-owner", "my-repo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "my-owner" in first_call_url
        assert "my-repo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_multiple_extensions_match(self, shared):
        items = [
            {"type": "blob", "path": "model_card.json", "url": "url1"},
            {"type": "blob", "path": "assessment_criterias.json", "url": "url2"},
        ]
        tree_resp = self._tree_response(items)
        blob1 = self._blob_response('{"model_name": "Underwriting Risk Classification"}')
        blob2