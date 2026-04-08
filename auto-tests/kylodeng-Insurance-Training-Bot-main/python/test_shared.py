"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API wrapper returning text response
- clean_json(): Markdown code-fence stripping utility
- get_repo_files(): GitHub API file fetching with extension filtering
- get_pr_diff(): GitHub API PR diff fetching
- write_output_file(): GitHub API file create/update in output repo
- post_pr_comment(): GitHub API PR comment posting
- send_email(): SendGrid email dispatch
- email_html(): HTML email body generation
- write_audit_entry(): Audit log writing (JSON + Markdown) via write_output_file

Mocks used:
- unittest.mock.patch for os.environ (all API keys / config)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- unittest.mock.patch for shared.write_output_file (in audit entry tests)

TODOs:
- TODO: write_audit_entry full round-trip test requires inspecting actual JSON/Markdown content
  written; needs write_output_file to be mockable at a finer grain.
- TODO: call_claude streaming / multi-content-block responses not covered without richer fixtures.
- TODO: send_email retry / back-off behaviour not implemented yet in source — no test added.
"""

import base64
import importlib
import json
import sys
import types
import datetime
from unittest.mock import MagicMock, patch, call

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
    """Ensure required env vars exist before shared.py is first imported."""
    with patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        # Remove shared from sys.modules so it re-imports cleanly with env vars
        sys.modules.pop("shared", None)
        # Also stub the `anthropic` package so we don't need the real SDK
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules["anthropic"] = fake_anthropic

        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", ".github/scripts/shared.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules["shared"] = mod
        yield mod


@pytest.fixture()
def shared_module():
    return sys.modules["shared"]


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
    def test_plain_json_unchanged(self, shared_module):
        raw = '{"key": "value"}'
        assert shared_module.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self, shared_module):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_leading_trailing_whitespace(self, shared_module):
        raw = "   ```json\n{}\n```   "
        result = shared_module.clean_json(raw)
        assert result == "{}"

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_no_closing_fence_still_strips_opening(self, shared_module):
        """If there's no closing ```, rsplit returns the original tail."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_module.clean_json(raw)
        # rsplit on ``` with no closing fence returns the whole string unchanged
        assert '{"key": "value"}' in result

    def test_nested_json_array(self, shared_module):
        raw = "```json\n[1, 2, 3]\n```"
        assert shared_module.clean_json(raw) == "[1, 2, 3]"

    def test_multiline_json(self, shared_module):
        inner = '{\n  "a": 1,\n  "b": 2\n}'
        raw = f"```json\n{inner}\n```"
        assert shared_module.clean_json(raw) == inner

    def test_no_fence_with_spaces_inside(self, shared_module):
        raw = '  {"product": "Generations II"}  '
        assert shared_module.clean_json(raw) == '{"product": "Generations II"}'


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    @pytest.fixture(autouse=True)
    def _patch_anthropic(self, shared_module):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.Anthropic", return_value=mock_client) as mock_cls:
            shared_module.anthropic.Anthropic = mock_cls
            # Also patch directly on the module to be sure
            original = shared_module.anthropic.Anthropic
            shared_module._test_mock_client = mock_client
            yield mock_client
            shared_module.anthropic.Anthropic = original

    def test_returns_text_content(self, shared_module):
        mock_client = shared_module._test_mock_client
        result = shared_module.call_claude("system prompt", "user message")
        assert result == "Hello from Claude"

    def test_calls_create_with_correct_model(self, shared_module):
        mock_client = shared_module._test_mock_client
        shared_module.call_claude("sys", "usr")
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["model"] == shared_module.MODEL

    def test_calls_create_with_default_max_tokens(self, shared_module):
        mock_client = shared_module._test_mock_client
        shared_module.call_claude("sys", "usr")
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 4096

    def test_calls_create_with_custom_max_tokens(self, shared_module):
        mock_client = shared_module._test_mock_client
        shared_module.call_claude("sys", "usr", max_tokens=1000)
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 1000

    def test_passes_system_and_user_correctly(self, shared_module):
        mock_client = shared_module._test_mock_client
        shared_module.call_claude("SYSTEM", "USER")
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["system"] == "SYSTEM"
        assert call_kwargs.kwargs["messages"] == [{"role": "user", "content": "USER"}]

    def test_api_exception_propagates(self, shared_module):
        mock_client = shared_module._test_mock_client
        mock_client.messages.create.side_effect = RuntimeError("API error")
        with pytest.raises(RuntimeError, match="API error"):
            shared_module.call_claude("sys", "usr")
        # Reset side effect
        mock_client.messages.create.side_effect = None
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_response


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    def _tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        return _make_response(json_data={"content": encoded})

    def test_returns_matching_files(self, shared_module):
        tree = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/1"},
            {"type": "blob", "path": "README.md", "url": "http://blob/2"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("print('hello')"),
                self._blob_response("# readme"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py", ".md"])
        assert "src/main.py" in result
        assert "README.md" in result
        assert result["src/main.py"] == "print('hello')"

    def test_filters_by_extension(self, shared_module):
        tree = [
            {"type": "blob", "path": "src/main.py", "url": "http://blob/1"},
            {"type": "blob", "path": "data.json", "url": "http://blob/2"},
            {"type": "blob", "path": "image.png", "url": "http://blob/3"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("{}"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".json"])
        assert "data.json" in result
        assert "src/main.py" not in result
        assert "image.png" not in result

    def test_ignores_non_blob_tree_items(self, shared_module):
        tree = [
            {"type": "tree", "path": "src", "url": "http://tree/1"},
            {"type": "blob", "path": "src/main.py", "url": "http://blob/2"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("code"),
            ]
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "src/main.py" in result

    def test_respects_max_files_limit(self, shared_module):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_responses = [self._blob_response(f"content{i}") for i in range(3)]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree)] + blob_responses
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    def test_empty_tree_returns_empty_dict(self, shared_module):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_skips_file_on_decode_error(self, shared_module):
        tree = [
            {"type": "blob", "path": "bad.py", "url": "http://blob/bad"},
            {"type": "blob", "path": "good.py", "url": "http://blob/good"},
        ]
        bad_blob = _make_response(json_data={"content": "!!!not-valid-base64!!!"})
        good_blob = self._blob_response("good content")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree), bad_blob, good_blob]
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        # bad.py silently skipped; good.py still present
        assert "good.py" in result

    def test_no_matching_extensions(self, shared_module):
        tree = [{"type": "blob", "path": "file.js", "url": "http://blob/1"}]
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response(tree)
            result = shared_module.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_constructs_correct_tree_url(self, shared_module):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            shared_module.get_repo_files("myowner", "myrepo", [".py"])
        called_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in called_url
        assert "myrepo" in called_url
        assert "recursive=1" in called_url


# ===========================================================================
# get_pr_diff
# ===========================================================================


class TestGetPrDiff:
    def test_returns_diff_text(self, shared_module):
        diff_text = "diff --git a/file.py b/file.py\n+added line"
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text=diff_text)
            result = shared_module.get_pr_diff("owner", "repo", 42)
        assert result == diff_text

    def test_truncates_to_30000_chars(self, shared_module):
        long_diff = "x" * 50000
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text=long_diff)
            result = shared_module.get_pr_diff("owner", "repo", 1)
        assert len(result) == 30000

    def test_constructs_correct_url(self, shared_module):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text="")
            shared_module.get_pr_diff("testowner", "testrepo", 99)
        called_url = mock_get.call_args[0][0]
        assert "testowner/testrepo/pulls/99" in called_url

    def test_uses_diff_accept_header(self, shared_module):
        with patch("requests.get") as mock_get: