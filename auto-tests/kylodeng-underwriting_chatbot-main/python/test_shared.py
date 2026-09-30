"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation and response extraction
- clean_json(): Markdown fence stripping for various input formats
- get_repo_files(): GitHub tree API fetching with extension filtering and max_files limit
- get_pr_diff(): PR diff fetching with truncation
- write_output_file(): File creation and update (with/without existing SHA)
- post_pr_comment(): PR comment posting
- send_email(): SendGrid email sending, success and failure paths
- email_html(): HTML email template generation
- write_audit_entry(): Audit log entry construction and write calls

Mocks used:
- unittest.mock.patch for os.environ (to satisfy module-level env var reads)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for base64 (where needed)

TODOs:
- TODO: Integration test for full audit log round-trip requires a real or emulated GitHub repo
- TODO: Test for concurrent calls to write_output_file (race condition on SHA)
- TODO: Validate exact HTML structure of email_html with an HTML parser
"""

import base64
import datetime
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Module-level env vars must exist before shared.py is imported
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


@pytest.fixture(autouse=True, scope="session")
def _patch_env_for_import():
    """Patch environment so the module-level os.environ[] reads succeed."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        # Force (re)import with the patched env
        if "shared" in sys.modules:
            del sys.modules["shared"]
        import importlib, importlib.util, pathlib
        spec = importlib.util.spec_from_file_location(
            "shared", pathlib.Path(".github/scripts/shared.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["shared"] = mod
        spec.loader.exec_module(mod)
        yield mod


@pytest.fixture()
def shared():
    return sys.modules["shared"]


# ===========================================================================
# Helpers
# ===========================================================================

def _make_response(status_code=200, json_data=None, text=""):
    """Build a minimal requests.Response-like mock."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


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

    def test_strips_plain_code_fence(self, shared):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_leading_trailing_whitespace(self, shared):
        raw = "   \n```json\n{\"x\": 2}\n```\n   "
        result = shared.clean_json(raw)
        assert result == '{"x": 2}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_no_closing_fence(self, shared):
        """If there is no closing fence, rsplit leaves it intact after strip."""
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        # opening fence line dropped, no closing fence to strip
        assert '{"a": 1}' in result

    def test_multiline_json_with_fence(self, shared):
        raw = "```json\n{\n  \"model_name\": \"Underwriting Risk Classification\",\n  \"model_type\": \"CatBoostClassifier\"\n}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_already_stripped_returns_same(self, shared):
        raw = '{"status": "ok"}'
        assert shared.clean_json(raw) == raw

    @pytest.mark.parametrize("fence", ["```json", "```python", "```"])
    def test_various_fence_languages(self, shared, fence):
        raw = f"{fence}\n[1, 2, 3]\n```"
        result = shared.clean_json(raw)
        assert result == "[1, 2, 3]"


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def test_happy_path_returns_text(self, shared):
        mock_text = "This is Claude's response"
        mock_content = MagicMock()
        mock_content.text = mock_text

        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == mock_text

    def test_passes_correct_model(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "user")

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == shared.MODEL

    def test_passes_max_tokens(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "user", max_tokens=1024)

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1024

    def test_default_max_tokens_is_4096(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("sys", "user")

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 4096

    def test_passes_system_and_user(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")

        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == "my system"
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "my user"}]

    def test_api_error_propagates(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")

        with patch("anthropic.Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("sys", "user")

    def test_uses_api_key_from_env(self, shared):
        mock_content = MagicMock()
        mock_content.text = "ok"
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_anthropic:
            shared.call_claude("sys", "user")

        mock_anthropic.assert_called_once_with(api_key="test-anthropic-key")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _make_tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return _make_response(json_data={"content": encoded})

    def test_returns_matching_files(self, shared):
        tree_items = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/1"},
            {"type": "blob", "path": "src/utils.py", "url": "http://blob/2"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("print('main')"),
                self._make_blob_response("print('utils')"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/main.py" in result
        assert "src/utils.py" in result
        assert result["src/main.py"] == "print('main')"

    def test_filters_by_extension(self, shared):
        tree_items = [
            {"type": "blob", "path": "README.md", "url": "http://blob/readme"},
            {"type": "blob", "path": "app.py", "url": "http://blob/app"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("# python file"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "README.md" not in result

    def test_respects_max_files(self, shared):
        tree_items = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_response = self._make_blob_response("content")

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
            ] + [self._make_blob_response(f"content{i}") for i in range(3)]
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        tree_items = [
            {"type": "tree", "path": "src/", "url": "http://tree/1"},
            {"type": "blob", "path": "main.py", "url": "http://blob/1"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src/" not in result
        assert "main.py" in result

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._make_tree_response([])
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions(self, shared):
        tree_items = [
            {"type": "blob", "path": "app.py", "url": "http://blob/1"},
            {"type": "blob", "path": "model_card.json", "url": "http://blob/2"},
            {"type": "blob", "path": "README.md", "url": "http://blob/3"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                self._make_blob_response("python"),
                self._make_blob_response('{"model_name": "Underwriting Risk Classification"}'),
                self._make_blob_response("# readme"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".json", ".md"])

        assert "app.py" in result
        assert "model_card.json" in result
        assert "README.md" in result

    def test_handles_decode_error_gracefully(self, shared):
        """A blob with bad base64 content should be skipped silently."""
        tree_items = [
            {"type": "blob", "path": "good.py", "url": "http://blob/good"},
            {"type": "blob", "path": "bad.py", "url": "http://blob/bad"},
        ]

        bad_blob = _make_response(json_data={"content": "!!!not-valid-base64!!!"})
        good_blob = self._make_blob_response("good content")

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_tree_response(tree_items),
                good_blob,
                bad_blob,
            ]
            # Should not raise
            result = shared.get_repo_files("owner", "repo", [".py"])

        # good file should be present; bad file may or may not be depending on error handling
        assert "good.py" in result

    def test_default_max_files_is_20(self, shared):
        tree_items = [
            {"type": "blob", "path": f"f{i}.py", "url": f"http://b/{i}"}
            for i in range(25)
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._make_