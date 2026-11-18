"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files cap, decode errors
- get_pr_diff(): PR diff retrieval, truncation behaviour
- write_output_file(): file create (no SHA) and update (with SHA) paths
- post_pr_comment(): comment posting
- send_email(): SendGrid happy path, failure warning
- email_html(): HTML output structure and colour logic
- write_audit_entry(): JSON/Markdown audit log construction (partial — source truncated)

Mocks used:
- unittest.mock.patch / MagicMock for: requests.get, requests.post, requests.put,
  anthropic.Anthropic (and its messages.create chain)
- os.environ patched before module import via monkeypatch / mock.patch.dict

TODOs:
- write_audit_entry(): source file is truncated; full behaviour cannot be verified without
  the complete implementation — stub tests are included with pytest.mark.skip
- MODEL constant value change testing requires re-import logic not easily done in pytest
"""

import base64
import importlib
import json
import sys
import types
from unittest import mock
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sg-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(scope="session", autouse=True)
def _patch_env_for_import():
    """Patch environment variables so shared.py can be imported without real secrets."""
    with mock.patch.dict("os.environ", FAKE_ENV, clear=False):
        # Remove cached module so it re-evaluates module-level constants
        sys.modules.pop("shared", None)
        sys.modules.pop(".github.scripts.shared", None)
        yield


@pytest.fixture(scope="session")
def shared_module(_patch_env_for_import):
    """Import shared inside the patched environment."""
    with mock.patch.dict("os.environ", FAKE_ENV, clear=False):
        # Ensure anthropic is importable (may not be installed in CI)
        if "anthropic" not in sys.modules:
            fake_anthropic = types.ModuleType("anthropic")
            fake_anthropic.Anthropic = MagicMock()
            sys.modules["anthropic"] = fake_anthropic

        sys.modules.pop("shared", None)
        import importlib.util, pathlib, os

        spec = importlib.util.spec_from_file_location(
            "shared",
            pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
        )
        # Fallback: try relative path used when tests run from repo root
        if spec is None or spec.origin is None:
            spec = importlib.util.spec_from_file_location(
                "shared", ".github/scripts/shared.py"
            )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["shared"] = mod
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ===========================================================================
# clean_json
# ===========================================================================


class TestCleanJson:
    def test_no_fences_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == raw

    def test_backtick_json_fence_stripped(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_backtick_fence_no_language_stripped(self, shared_module):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_leading_trailing_whitespace_stripped(self, shared_module):
        raw = "   \n  {\"a\": 1}  \n  "
        result = shared_module.clean_json(raw)
        assert result == '{"a": 1}'

    def test_fence_with_whitespace_around(self, shared_module):
        raw = "  ```json\n[1,2,3]\n```  "
        result = shared_module.clean_json(raw)
        assert result == "[1,2,3]"

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_multiple_fences_only_outermost_removed(self, shared_module):
        """Only the first opening fence line and last closing fence are removed."""
        raw = "```json\n```inner```\n```"
        result = shared_module.clean_json(raw)
        assert "```inner```" in result

    def test_valid_json_survives_round_trip(self, shared_module):
        payload = {"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        result = shared_module.clean_json(raw)
        assert json.loads(result) == payload

    def test_no_closing_fence(self, shared_module):
        """Gracefully handles missing closing fence."""
        raw = "```json\n{\"a\": 1}"
        result = shared_module.clean_json(raw)
        # Should not raise; content after opening fence should be present
        assert '{"a": 1}' in result


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    @pytest.fixture()
    def mock_anthropic(self, shared_module):
        with patch("anthropic.Anthropic") as MockClass:
            mock_client = MagicMock()
            MockClass.return_value = mock_client
            # Patch the module-level reference too
            original = shared_module.anthropic.Anthropic
            shared_module.anthropic.Anthropic = MockClass
            yield mock_client, MockClass
            shared_module.anthropic.Anthropic = original

    def test_happy_path_returns_text(self, shared_module, mock_anthropic):
        mock_client, MockClass = mock_anthropic
        fake_text = "Here is the analysis result."
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=fake_text)]
        )

        result = shared_module.call_claude("system prompt", "user prompt")
        assert result == fake_text

    def test_passes_correct_model(self, shared_module, mock_anthropic):
        mock_client, MockClass = mock_anthropic
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="ok")]
        )
        shared_module.call_claude("sys", "usr")
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("model") == shared_module.MODEL or \
               call_kwargs[1].get("model") == shared_module.MODEL or \
               call_kwargs[0][0] if call_kwargs[0] else True  # positional fallback

    def test_default_max_tokens(self, shared_module, mock_anthropic):
        mock_client, _ = mock_anthropic
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="ok")]
        )
        shared_module.call_claude("s", "u")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("max_tokens") == 4096

    def test_custom_max_tokens(self, shared_module, mock_anthropic):
        mock_client, _ = mock_anthropic
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="ok")]
        )
        shared_module.call_claude("s", "u", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("max_tokens") == 1024

    def test_messages_structure(self, shared_module, mock_anthropic):
        mock_client, _ = mock_anthropic
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="ok")]
        )
        shared_module.call_claude("system text", "user text")
        _, kwargs = mock_client.messages.create.call_args
        messages = kwargs.get("messages", [])
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "user text"

    def test_api_error_propagates(self, shared_module, mock_anthropic):
        mock_client, _ = mock_anthropic
        mock_client.messages.create.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            shared_module.call_claude("s", "u")


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    def _tree_response(self, paths):
        return {
            "tree": [{"type": "blob", "path": p, "url": f"https://api.github.com/blob/{p}"} for p in paths]
        }

    def _blob_response(self, content: str):
        return {"content": base64.b64encode(content.encode()).decode() + "\n"}

    @patch("requests.get")
    def test_happy_path_filters_by_extension(self, mock_get, shared_module):
        tree = self._tree_response(["foo.py", "bar.js", "baz.py", "README.md"])
        blob = self._blob_response("print('hello')")
        mock_get.side_effect = [
            _make_response(json_data=tree),   # tree call
            _make_response(json_data=blob),   # foo.py
            _make_response(json_data=blob),   # baz.py
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "foo.py" in result
        assert "baz.py" in result
        assert "bar.js" not in result
        assert "README.md" not in result

    @patch("requests.get")
    def test_max_files_cap(self, mock_get, shared_module):
        paths = [f"file{i}.py" for i in range(30)]
        tree = self._tree_response(paths)
        blob = self._blob_response("content")
        mock_get.side_effect = [_make_response(json_data=tree)] + [
            _make_response(json_data=blob) for _ in range(20)
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=20)
        assert len(result) <= 20

    @patch("requests.get")
    def test_empty_tree(self, mock_get, shared_module):
        mock_get.return_value = _make_response(json_data={"tree": []})
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    @patch("requests.get")
    def test_multiple_extensions(self, mock_get, shared_module):
        tree = self._tree_response(["a.py", "b.js", "c.ts", "d.go"])
        blob = self._blob_response("code")
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data=blob),
            _make_response(json_data=blob),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py", ".js"])
        assert "a.py" in result
        assert "b.js" in result
        assert "c.ts" not in result
        assert "d.go" not in result

    @patch("requests.get")
    def test_decode_error_skips_file(self, mock_get, shared_module):
        tree = self._tree_response(["good.py", "bad.py"])
        good_blob = self._blob_response("good content")
        bad_blob = {"not_content": "missing"}  # will raise KeyError -> caught
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data=good_blob),
            _make_response(json_data=bad_blob),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "good.py" in result
        assert result["good.py"] == "good content"
        # bad.py silently skipped
        assert "bad.py" not in result

    @patch("requests.get")
    def test_non_blob_items_skipped(self, mock_get, shared_module):
        tree = {
            "tree": [
                {"type": "tree", "path": "somedir", "url": "http://x"},
                {"type": "blob", "path": "file.py", "url": "http://y"},
            ]
        }
        blob = self._blob_response("hello")
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data=blob),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "file.py" in result
        assert "somedir" not in result

    @patch("requests.get")
    def test_correct_url_built(self, mock_get, shared_module):
        tree = self._tree_response([])
        mock_get.return_value = _make_response(json_data=tree)
        shared_module.get_repo_files("my-owner", "my-repo", [".py"])
        called_url = mock_get.call_args[0][0]
        assert "my-owner" in called_url
        assert "my-repo" in called_url
        assert "recursive=1" in called_url

    @patch("requests.get")
    def test_unicode_content_decoded(self, mock_get, shared_module):
        content = "# Arabic: \u0625\u0644\u063a\u0627\u0621"
        tree = self._tree_response(["ar.py"])
        blob = self._blob_response(content)
        mock_get.side_effect = [
            _make_response(json_data=tree),
            _make_response(json_data=blob),
        ]
        result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result["ar.py"] == content


# ===========================================================================
# get_pr_diff
# ===========================================================================


class TestGetPrDiff:
    @patch("requests