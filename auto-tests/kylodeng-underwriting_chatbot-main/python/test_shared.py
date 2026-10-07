"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Stripping markdown code fences from JSON strings
- get_repo_files(): GitHub API tree fetching, extension filtering, base64 decoding, max_files limit
- get_pr_diff(): GitHub API PR diff fetching, truncation
- write_output_file(): GitHub API file create/update (with/without existing SHA)
- post_pr_comment(): GitHub API PR comment posting
- send_email(): SendGrid email sending, warning on failure
- email_html(): HTML email generation, status color, content
- write_audit_entry(): Audit log entry construction and file writing (stub)

Mocks used:
- unittest.mock.patch for os.environ (all required env vars)
- unittest.mock.patch / MagicMock for requests.get, requests.post, requests.put
- unittest.mock.patch for anthropic.Anthropic (Claude client)
- unittest.mock.patch for datetime.datetime (deterministic timestamps)

TODOs:
- write_audit_entry(): Full integration test requires inspecting the JSON/Markdown files
  written to the output repo — needs more context on the file format and appending logic
  (source code is truncated).
- call_claude(): Test streaming or extended thinking modes if ever used.
- send_email(): Test retry / back-off behaviour if added in future.
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------
FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


def _import_shared():
    """Import (or re-import) shared with a controlled environment."""
    with patch.dict("os.environ", FAKE_ENV, clear=False):
        # Remove cached module so env vars are re-read on import
        sys.modules.pop("shared", None)
        # Make sure the scripts directory is on the path
        import importlib.util, pathlib
        scripts_dir = str(pathlib.Path(__file__).parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        spec = importlib.util.spec_from_file_location(
            "shared",
            str(pathlib.Path(__file__).parent / "shared.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        # Provide a stub anthropic module so the import doesn't fail without the package
        if "anthropic" not in sys.modules:
            stub = types.ModuleType("anthropic")
            stub.Anthropic = MagicMock()
            sys.modules["anthropic"] = stub
        spec.loader.exec_module(mod)
        return mod


# We import once at module level; individual tests patch at a finer grain.
with patch.dict("os.environ", FAKE_ENV, clear=False):
    # Ensure anthropic stub exists before first import
    if "anthropic" not in sys.modules:
        _stub = types.ModuleType("anthropic")
        _stub.Anthropic = MagicMock()
        sys.modules["anthropic"] = _stub

    # Ensure requests is importable (it is a real dependency)
    import requests  # noqa: E402 – needed to allow patching below

    sys.modules.pop("shared", None)
    import pathlib as _pathlib
    import importlib.util as _ilu

    _spec = _ilu.spec_from_file_location(
        "shared",
        str(_pathlib.Path(__file__).parent / "shared.py"),
    )
    shared = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(shared)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_response(status_code=200, json_data=None, text=""):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# clean_json
# ---------------------------------------------------------------------------

class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self):
        raw = '   {"key": "value"}   '
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_whitespace_inside_fences(self):
        raw = '```json\n  {"a": 1}  \n```'
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_fences(self):
        raw = "```json\n```"
        result = shared.clean_json(raw)
        # Should not raise; content between fences is empty/whitespace
        assert isinstance(result, str)

    def test_multiline_json_in_fence(self):
        raw = '```json\n{\n  "model_name": "Underwriting Risk Classification",\n  "model_type": "CatBoostClassifier"\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_no_closing_fence_returns_partial(self):
        """If there is no closing fence, rsplit leaves content intact (no crash)."""
        raw = "```json\n{}"
        result = shared.clean_json(raw)
        assert isinstance(result, str)

    @pytest.mark.parametrize("raw,expected", [
        ('{"a":1}', '{"a":1}'),
        ('  \n{"b":2}\n  ', '{"b":2}'),
        ('```json\n[]\n```', '[]'),
        ('```\ntrue\n```', 'true'),
    ])
    def test_parametrized_cases(self, raw, expected):
        assert shared.clean_json(raw) == expected


# ---------------------------------------------------------------------------
# call_claude
# ---------------------------------------------------------------------------

class TestCallClaude:
    def _make_claude_client(self, text_response="Hello from Claude"):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=text_response)]
        mock_client.messages.create.return_value = mock_message
        return mock_client

    def test_returns_text_from_response(self):
        mock_client = self._make_claude_client("Test response")
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user message")
        assert result == "Test response"

    def test_passes_correct_model(self):
        mock_client = self._make_claude_client("ok")
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self):
        mock_client = self._make_claude_client("ok")
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=mock_client):
            shared.call_claude("my system", "my user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"][0]["content"] == "my user"
        assert kwargs["messages"][0]["role"] == "user"

    def test_default_max_tokens(self):
        mock_client = self._make_claude_client("ok")
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self):
        mock_client = self._make_claude_client("ok")
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self):
        mock_anthropic_cls = MagicMock()
        mock_anthropic_cls.return_value = self._make_claude_client("ok")
        with patch.object(sys.modules["anthropic"], "Anthropic", mock_anthropic_cls):
            shared.call_claude("sys", "usr")
        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_propagates_api_exception(self):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("sys", "usr")

    def test_large_response_text(self):
        large_text = "x" * 100_000
        mock_client = self._make_claude_client(large_text)
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=mock_client):
            result = shared.call_claude("sys", "usr")
        assert result == large_text


# ---------------------------------------------------------------------------
# get_repo_files
# ---------------------------------------------------------------------------

class TestGetRepoFiles:
    def _tree_item(self, path, item_type="blob", url="https://api.github.com/content/x"):
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return {"content": encoded + "\n", "encoding": "base64"}

    def test_returns_files_matching_extension(self):
        tree = [self._tree_item("README.md"), self._tree_item("app.py")]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._blob_response("print('hello')"))

        with patch("requests.get", side_effect=[tree_resp, blob_resp]) as mock_get:
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "README.md" not in result
        assert result["app.py"] == "print('hello')"

    def test_filters_multiple_extensions(self):
        tree = [
            self._tree_item("README.md"),
            self._tree_item("app.py"),
            self._tree_item("index.js"),
            self._tree_item("data.json"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_py = _make_response(json_data=self._blob_response("python"))
        blob_js = _make_response(json_data=self._blob_response("javascript"))

        with patch("requests.get", side_effect=[tree_resp, blob_py, blob_js]):
            result = shared.get_repo_files("owner", "repo", [".py", ".js"])

        assert "app.py" in result
        assert "index.js" in result
        assert "README.md" not in result
        assert "data.json" not in result

    def test_respects_max_files_limit(self):
        tree = [self._tree_item(f"file{i}.py") for i in range(10)]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._blob_response("content"))

        with patch("requests.get", side_effect=[tree_resp] + [blob_resp] * 3):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_skips_non_blob_items(self):
        tree = [
            {"type": "tree", "path": "src", "url": "https://x"},
            self._tree_item("app.py"),
        ]
        tree_resp = _make_response(json_data={"tree": tree})
        blob_resp = _make_response(json_data=self._blob_response("code"))

        with patch("requests.get", side_effect=[tree_resp, blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "app.py" in result
        assert "src" not in result

    def test_handles_decode_error_gracefully(self):
        tree = [self._tree_item("broken.py")]
        tree_resp = _make_response(json_data={"tree": tree})
        # Return a blob with no 'content' key to trigger exception
        bad_blob = _make_response(json_data={})

        with patch("requests.get", side_effect=[tree_resp, bad_blob]):
            result = shared.get_repo_files("owner", "repo", [".py"])

        # File should be skipped gracefully
        assert "broken.py" not in result

    def test_empty_repo_tree(self):
        tree_resp = _make_response(json_data={"tree": []})
        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_no_matching_extension(self):
        tree = [self._tree_item("README.md"), self._tree_item("Makefile")]
        tree_resp = _make_response(json_data={"tree": tree})
        with patch("requests.get", return_value=tree_resp):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_correct_api_url_constructed(self):
        tree_resp = _make_response(json_data={"tree": []})
        with patch("requests.get", return_value=tree_resp) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])
        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_max_files_zero_returns_empty(self):
        tree