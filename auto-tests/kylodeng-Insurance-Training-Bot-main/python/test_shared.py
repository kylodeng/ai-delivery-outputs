"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases, boundary values
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, decode errors
- get_pr_diff(): PR diff fetching, truncation boundary
- write_output_file(): file create (no SHA) and update (with SHA) paths, URL fallback
- post_pr_comment(): comment posting
- send_email(): SendGrid payload construction, success/failure status codes
- email_html(): HTML output structure, SUCCESS/FAILURE colour logic
- write_audit_entry(): audit log JSON and Markdown construction (stub — needs full source)

Mocks used:
- unittest.mock.patch for os.environ (all required env vars)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- datetime.datetime.utcnow patched for deterministic timestamps

TODOs:
- write_audit_entry() source is truncated; stub tests added with pytest.mark.skip
- Full integration test for Claude API requires real ANTHROPIC_API_KEY
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen before shared.py is imported
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
def patch_env_session():
    """Patch environment variables for the entire test session before module import."""
    with patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        yield


# Import shared after env is patched.
# We use importlib so the module is loaded with the patched env.
@pytest.fixture(scope="session")
def shared(patch_env_session):
    with patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # The module lives at .github/scripts/shared.py — add path if needed
        import importlib.util, pathlib

        spec_path = pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py"
        spec = importlib.util.spec_from_file_location("shared", spec_path)
        mod = importlib.util.module_from_spec(spec)
        # Stub anthropic before exec so the top-level client creation doesn't fail
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _make_response(status_code: int = 200, json_data=None, text: str = ""):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data if json_data is not None else {}
    mock.text = text
    return mock


# ===========================================================================
# clean_json
# ===========================================================================

class TestCleanJson:
    def test_plain_json_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_backtick_fence(self, shared):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = "   {\"a\": 1}   "
        assert shared.clean_json(raw) == '{"a": 1}'

    def test_fence_with_whitespace_around_content(self, shared):
        raw = "```json\n  {\"a\": 1}  \n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self, shared):
        assert shared.clean_json("   ") == ""

    def test_no_closing_fence_returns_partial(self, shared):
        # When there is no closing ```, rsplit won't strip anything meaningful
        raw = "```json\n{\"a\": 1}"
        result = shared.clean_json(raw)
        # Should at least drop the opening line
        assert "```json" not in result

    def test_multiline_json(self, shared):
        inner = '{\n  "product_name": "Generations II",\n  "doc_type": "product_brochure"\n}'
        raw = f"```json\n{inner}\n```"
        assert shared.clean_json(raw) == inner

    def test_nested_backticks_in_content_not_stripped(self, shared):
        # Only the outermost fences should be removed
        inner = '{"code": "use `x`"}'
        raw = f"```json\n{inner}\n```"
        result = shared.clean_json(raw)
        assert "use `x`" in result

    def test_result_is_valid_json_after_clean(self, shared):
        raw = '```json\n{"product_name": "Generations II", "doc_type": "product_brochure"}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["product_name"] == "Generations II"


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_client_mock(self):
        content_block = MagicMock()
        content_block.text = "Hello from Claude"
        response = MagicMock()
        response.content = [content_block]
        client = MagicMock()
        client.messages.create.return_value = response
        return client

    def test_returns_text_from_first_content_block(self, shared):
        client = self._make_client_mock()
        with patch("anthropic.Anthropic", return_value=client):
            shared_client_patch = patch.object(
                sys.modules["anthropic"], "Anthropic", return_value=client
            )
            with shared_client_patch:
                result = shared.call_claude("sys prompt", "user prompt")
        assert result == "Hello from Claude"

    def test_passes_correct_model(self, shared):
        client = self._make_client_mock()
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=client):
            shared.call_claude("sys", "user", max_tokens=512)
            _, kwargs = client.messages.create.call_args
            assert kwargs["model"] == "claude-sonnet-4-6"

    def test_passes_max_tokens(self, shared):
        client = self._make_client_mock()
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=client):
            shared.call_claude("sys", "user", max_tokens=1024)
            _, kwargs = client.messages.create.call_args
            assert kwargs["max_tokens"] == 1024

    def test_default_max_tokens_is_4096(self, shared):
        client = self._make_client_mock()
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=client):
            shared.call_claude("sys", "user")
            _, kwargs = client.messages.create.call_args
            assert kwargs["max_tokens"] == 4096

    def test_user_message_role_and_content(self, shared):
        client = self._make_client_mock()
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=client):
            shared.call_claude("my system", "my user message")
            _, kwargs = client.messages.create.call_args
            assert kwargs["messages"] == [{"role": "user", "content": "my user message"}]
            assert kwargs["system"] == "my system"

    def test_api_exception_propagates(self, shared):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API error")
        with patch.object(sys.modules["anthropic"], "Anthropic", return_value=client):
            with pytest.raises(RuntimeError, match="API error"):
                shared.call_claude("sys", "user")


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _blob_response(self, content: str):
        return _make_response(json_data={"content": _b64(content)})

    def test_returns_files_matching_extension(self, shared):
        tree = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/2"},
        ]
        py_content = "print('hello')"
        md_content = "# README"

        responses = [
            self._tree_response(tree),
            self._blob_response(py_content),
        ]
        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "main.py" in result
        assert result["main.py"] == py_content
        assert "README.md" not in result

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "src", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "app.py", "url": "https://api.github.com/blob/2"},
        ]
        responses = [
            self._tree_response(tree),
            self._blob_response("code"),
        ]
        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "app.py" in result

    def test_max_files_limit(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        # First call: tree; remaining: blob responses
        responses = [self._tree_response(tree)] + [
            self._blob_response(f"content{i}") for i in range(10)
        ]
        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    def test_multiple_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "style.css", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "index.js", "url": "https://api.github.com/blob/3"},
        ]
        responses = [
            self._tree_response(tree),
            self._blob_response("python code"),
            self._blob_response("css code"),
        ]
        with patch("requests.get", side_effect=responses):
            result = shared.get_repo_files("owner", "repo", [".py", ".css"])
        assert "main.py" in result
        assert "style.css" in result
        assert "index.js" not in result

    def test_decode_error_is_silently_skipped(self, shared):
        tree = [
            {"type": "blob", "path": "bad.py", "url": "https://api.github.com/blob/1"},
        ]
        bad_blob = _make_response(json_data={"content": "not-valid-base64!!!"})
        responses = [self._tree_response(tree), bad_blob]
        with patch("requests.get", side_effect=responses):
            # Should not raise; file may or may not be included depending on decode behaviour
            result = shared.get_repo_files("owner", "repo", [".py"])
        # The important thing is no exception is raised
        assert isinstance(result, dict)

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get", return_value=self._tree_response([])):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_missing_tree_key_returns_empty_dict(self, shared):
        with patch("requests.get", return_value=_make_response(json_data={})):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_correct_url_constructed(self, shared):
        with patch("requests.get", return_value=self._tree_response([])) as mock_get:
            shared.get_repo_files("myowner", "myrepo", [".py"])
            called_url = mock_get.call_args[0][0]
            assert "myowner" in called_url
            assert "myrepo" in called_url
            assert "recursive=1" in called_url

    def test_utf8_decoding_with_replacement(self, shared):
        tree = [
            {"type": "blob", "path": "file.py", "url": "https://api.github.com/blob/1"},
        ]
        # Content with bytes that are valid base64 but decode with replacement chars
        raw_bytes = b"hello \xff world"
        encoded = base64.b64encode(raw_bytes).decode()
        blob_resp = _make_response(json_data={"content": encoded})
        with patch("requests.get", side_effect=[self._tree_response(tree), blob_resp]):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "file.py" in result
        assert "hello" in result["file.py"]


# ===========================================================================
# get_pr_diff
# ===========================================================================

class TestGetPrDiff:
    def test_returns_diff_text(self, shared):
        mock_resp = _make_response(text="--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new")
        with patch("requests.get", return_value=mock_resp):
            result = shared.get_pr_diff("owner", "repo", 42)
        assert "--- a/file.py" in result

    def test_truncates_to_30000_chars(self, shared):
        long_diff = "x" * 50000
        mock_resp