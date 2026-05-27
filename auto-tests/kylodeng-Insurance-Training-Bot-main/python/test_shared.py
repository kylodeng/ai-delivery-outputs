"""
Test module for .github/scripts/shared.py

What is tested:
    - call_claude(): Claude API invocation, response extraction
    - clean_json(): Markdown fence stripping, edge cases
    - get_repo_files(): GitHub tree fetch, extension filtering, max_files limit, base64 decode
    - get_pr_diff(): PR diff fetch, truncation at 30000 chars
    - write_output_file(): File create (no SHA) and update (with SHA) paths, URL fallback
    - post_pr_comment(): PR comment posting
    - send_email(): SendGrid payload structure, success/failure status codes
    - email_html(): HTML output shape, SUCCESS/FAILURE colour logic
    - write_audit_entry(): Audit log construction (partial — module-level constant dependency)

Mocks used:
    - unittest.mock.patch / MagicMock for:
        * anthropic.Anthropic (Claude client)
        * requests.get, requests.post, requests.put (all HTTP calls)
    - os.environ patched via monkeypatch/patch.dict to satisfy module-level env reads

TODOs:
    - write_audit_entry() full integration: requires the rest of the source (truncated in prompt)
    - MODEL constant value change: needs re-import after env patch
    - GH_HEADERS build-time value: tested indirectly; direct header mutation tests skipped
"""

import base64
import importlib
import json
import sys
import os
import datetime
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment setup — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "gh-owner",
}


@pytest.fixture(scope="session", autouse=True)
def patch_env_for_import():
    """Patch environment before shared.py module-level code runs."""
    with patch.dict(os.environ, FAKE_ENV, clear=False):
        # Remove stale cached module so the patched env is used
        sys.modules.pop("shared", None)
        # Ensure the scripts directory is on the path
        scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        yield


# Import after env is patched
with patch.dict(os.environ, FAKE_ENV, clear=False):
    sys.modules.pop("shared", None)
    _scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts")
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import shared  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_anthropic_response(text: str):
    """Build a minimal mock that looks like an anthropic Messages response."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    """Tests for shared.call_claude()"""

    def test_happy_path_returns_text(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response("Hello world")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("sys prompt", "user prompt")

        assert result == "Hello world"

    def test_passes_correct_model_and_tokens(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "user", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL
        assert kwargs["max_tokens"] == 1024

    def test_passes_system_and_user_messages(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("be helpful", "what is 2+2?")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "be helpful"
        assert kwargs["messages"] == [{"role": "user", "content": "what is 2+2?"}]

    def test_default_max_tokens_is_4096(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response("ok")

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_uses_env_api_key(self):
        captured = {}

        def fake_init(api_key):
            captured["api_key"] = api_key
            client = MagicMock()
            client.messages.create.return_value = _make_anthropic_response("ok")
            return client

        with patch("anthropic.Anthropic", side_effect=fake_init):
            shared.call_claude("s", "u")

        assert captured["api_key"] == "test-anthropic-key"

    def test_propagates_api_exception(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API down"):
                shared.call_claude("s", "u")

    def test_empty_system_prompt(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response("response")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("", "user message")

        assert result == "response"

    def test_large_max_tokens(self):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_anthropic_response("big")

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("s", "u", max_tokens=100000)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 100000
        assert result == "big"


# ===========================================================================
# clean_json
# ===========================================================================


class TestCleanJson:
    """Tests for shared.clean_json()"""

    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_code_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_and_trailing_whitespace(self):
        raw = '  \n  {"key": "value"}  \n  '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_fence_with_surrounding_whitespace(self):
        raw = "  ```json\n{\"a\": 1}\n```  "
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   \n  ") == ""

    def test_fence_with_multiline_json(self):
        raw = "```json\n{\n  \"key\": \"value\",\n  \"num\": 42\n}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["key"] == "value"
        assert parsed["num"] == 42

    def test_no_closing_fence_returns_partial(self):
        """If there is no closing fence, rsplit returns the full string after opening line."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared.clean_json(raw)
        assert '{"key": "value"}' in result

    def test_real_insurance_data_json(self):
        """Uses synthetic data pattern: JSON with doc wrapper."""
        payload = json.dumps({
            "doc": {
                "product_name": "Generations II",
                "doc_type": "product_brochure",
                "linked_product": "Generations II",
                "summary": "Generations II is a participating whole life insurance plan.",
            }
        })
        fenced = f"```json\n{payload}\n```"
        result = shared.clean_json(fenced)
        parsed = json.loads(result)
        assert parsed["doc"]["product_name"] == "Generations II"

    def test_multiple_code_fences_only_outer_stripped(self):
        """Only the outermost fence markers are removed."""
        inner = '{"nested": "```inner```"}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert result == inner

    def test_non_json_content_with_fence(self):
        raw = "```\nhello world\n```"
        assert shared.clean_json(raw) == "hello world"


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    """Tests for shared.get_repo_files()"""

    def _make_tree_response(self, items):
        resp = MagicMock()
        resp.json.return_value = {"tree": items}
        return resp

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        resp = MagicMock()
        resp.json.return_value = {"content": encoded + "\n"}  # GitHub adds newline
        return resp

    def test_happy_path_fetches_matching_files(self):
        tree_items = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/main"},
            {"type": "blob", "path": "README.md", "url": "http://blob/readme"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("print('hello')"),
                self._make_blob_response("# readme"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".md"])

        assert "src/main.py" in result
        assert "README.md" in result
        assert "print('hello')" in result["src/main.py"]

    def test_filters_by_extension(self):
        tree_items = [
            {"type": "blob", "path": "main.py", "url": "http://blob/py"},
            {"type": "blob", "path": "data.json", "url": "http://blob/json"},
            {"type": "blob", "path": "image.png", "url": "http://blob/png"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("python code"),
                self._make_blob_response('{"key": "val"}'),
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "main.py" in result
        assert "data.json" in result
        assert "image.png" not in result

    def test_skips_non_blob_items(self):
        tree_items = [
            {"type": "tree", "path": "src", "url": "http://tree/src"},
            {"type": "blob", "path": "file.py", "url": "http://blob/file"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "file.py" in result

    def test_respects_max_files_limit(self):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_resp = self._make_blob_response("content")

        with patch("requests.get") as mock_get:
            # First call = tree, then up to max_files blob calls
            mock_get.side_effect = [self._make_tree_response(tree_items)] + [
                self._make_blob_response(f"content{i}") for i in range(10)
            ]
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._make_tree_response([])
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_no_matching_extensions_returns_empty(self):
        tree_items = [
            {"type": "blob", "path": "file.go", "url": "http://blob/go"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._make_tree_response(tree_items)
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert result == {}

    def test_handles_blob_decode_error_gracefully(self):
        """If base64 content is missing/corrupt, the file is silently skipped."""
        tree_items = [
            {"type": "blob", "path": "bad.py", "url": "http://blob/bad"},
            {"type": "blob", "path": "good.py", "url": "http://blob/good"},
        ]
        bad_resp = MagicMock()
        bad_resp.json.return_value = {}  # no "content" key → KeyError caught

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                bad_resp,
                self._make_blob_response("good content"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "bad