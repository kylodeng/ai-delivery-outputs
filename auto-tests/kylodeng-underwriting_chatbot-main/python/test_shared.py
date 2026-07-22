"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API interaction, response parsing
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub API tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): File creation (no SHA), file update (with SHA), URL fallback
- post_pr_comment(): PR comment posting
- send_email(): SendGrid API call, success/failure status codes
- email_html(): HTML output structure, SUCCESS/FAILURE status colouring
- write_audit_entry(): Audit log entry construction and repo writes (partial — source truncated)

Mocks used:
- unittest.mock.patch for os.environ (prevent KeyError on import)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- base64 encoding/decoding verified inline

TODOs:
- write_audit_entry() full coverage blocked by truncated source — stubs provided
- MODEL constant value tested but actual model routing not exercisable without live API
"""

import base64
import datetime
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
_ENV_DEFAULTS = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-gh-owner",
}


@pytest.fixture(autouse=True, scope="session")
def _patch_env_for_import():
    """Patch environment before shared.py is first imported."""
    with mock.patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
        # Force (re)import under the patched environment
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Make sure the script directory is on sys.path
        import os
        script_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        # Also try current directory (when running from repo root)
        root_candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts"),
            ".github/scripts",
        ]
        for candidate in root_candidates:
            if os.path.isdir(candidate) and candidate not in sys.path:
                sys.path.insert(0, candidate)
        yield


@pytest.fixture(scope="session")
def shared_module(_patch_env_for_import):
    """Import shared module once per session under the patched environment."""
    with mock.patch.dict("os.environ", _ENV_DEFAULTS, clear=False):
        import importlib, sys
        if "shared" in sys.modules:
            del sys.modules["shared"]
        import shared  # noqa: PLC0415
        return shared


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def shared(shared_module):
    """Per-test alias so tests can just write `shared.xxx`."""
    return shared_module


def _make_response(status_code=200, json_data=None, text=""):
    """Build a minimal requests.Response mock."""
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

    def test_strips_generic_code_fence(self, shared):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = "   \n{\"x\": 2}\n   "
        result = shared.clean_json(raw)
        assert result == '{"x": 2}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_only_fences_no_content(self, shared):
        raw = "```json\n```"
        result = shared.clean_json(raw)
        # Should not raise; inner content is empty/whitespace
        assert isinstance(result, str)

    def test_nested_backticks_not_stripped_twice(self, shared):
        raw = "```json\n{\"code\": \"```inner```\"}\n```"
        result = shared.clean_json(raw)
        assert "inner" in result

    def test_valid_json_after_strip(self, shared):
        raw = "```json\n{\"model_name\": \"Underwriting Risk Classification\"}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_multiline_json_fence(self, shared):
        raw = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": 2}

    def test_no_fence_preserves_content(self, shared):
        raw = '{"customers": ["CUST00000001", "CUST00006151"]}'
        assert shared.clean_json(raw) == raw


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _make_client_mock(self, text_response="Hello from Claude"):
        client = MagicMock()
        message = MagicMock()
        message.content = [MagicMock(text=text_response)]
        client.messages.create.return_value = message
        return client

    def test_happy_path_returns_text(self, shared):
        client_mock = self._make_client_mock("test response")
        with patch("anthropic.Anthropic", return_value=client_mock):
            result = shared.call_claude("system prompt", "user prompt")
        assert result == "test response"

    def test_passes_correct_model(self, shared):
        client_mock = self._make_client_mock()
        with patch("anthropic.Anthropic", return_value=client_mock):
            shared.call_claude("sys", "usr")
        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self, shared):
        client_mock = self._make_client_mock()
        with patch("anthropic.Anthropic", return_value=client_mock):
            shared.call_claude("my system", "my user")
        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["system"] == "my system"
        assert kwargs["messages"][0]["content"] == "my user"

    def test_default_max_tokens(self, shared):
        client_mock = self._make_client_mock()
        with patch("anthropic.Anthropic", return_value=client_mock):
            shared.call_claude("s", "u")
        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, shared):
        client_mock = self._make_client_mock()
        with patch("anthropic.Anthropic", return_value=client_mock):
            shared.call_claude("s", "u", max_tokens=1024)
        _, kwargs = client_mock.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_api_key_passed_to_client(self, shared):
        client_mock = self._make_client_mock()
        with patch("anthropic.Anthropic", return_value=client_mock) as mock_cls:
            shared.call_claude("s", "u")
        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_returns_first_content_block(self, shared):
        client_mock = MagicMock()
        message = MagicMock()
        message.content = [
            MagicMock(text="first"),
            MagicMock(text="second"),
        ]
        client_mock.messages.create.return_value = message
        with patch("anthropic.Anthropic", return_value=client_mock):
            result = shared.call_claude("s", "u")
        assert result == "first"

    def test_api_error_propagates(self, shared):
        client_mock = MagicMock()
        client_mock.messages.create.side_effect = Exception("API error")
        with patch("anthropic.Anthropic", return_value=client_mock):
            with pytest.raises(Exception, match="API error"):
                shared.call_claude("s", "u")

    def test_unicode_response(self, shared):
        arabic_text = "\u0625\u0644\u063a\u0627\u0621"
        client_mock = self._make_client_mock(arabic_text)
        with patch("anthropic.Anthropic", return_value=client_mock):
            result = shared.call_claude("s", "u")
        assert result == arabic_text


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return _make_response(json_data={"content": encoded + "\n"})

    def test_happy_path_filters_by_extension(self, shared):
        tree = [
            {"type": "blob", "path": "main.py", "url": "http://blob/main.py"},
            {"type": "blob", "path": "README.md", "url": "http://blob/readme"},
            {"type": "blob", "path": "utils.py", "url": "http://blob/utils.py"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("print('main')"),
                self._blob_response("print('utils')"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "main.py" in result
        assert "utils.py" in result
        assert "README.md" not in result

    def test_max_files_limit(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_resp = self._blob_response("content")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [self._tree_response(tree)] + [blob_resp] * 10
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    def test_skips_non_blob_items(self, shared):
        tree = [
            {"type": "tree", "path": "src", "url": "http://tree/src"},
            {"type": "blob", "path": "main.py", "url": "http://blob/main"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("code"),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "main.py" in result

    def test_multiple_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "app.py", "url": "http://blob/app"},
            {"type": "blob", "path": "model_card.json", "url": "http://blob/mc"},
            {"type": "blob", "path": "notes.txt", "url": "http://blob/notes"},
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response("python code"),
                self._blob_response('{"model_name": "Underwriting Risk Classification"}'),
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".json"])
        assert "app.py" in result
        assert "model_card.json" in result
        assert "notes.txt" not in result

    def test_decodes_base64_content(self, shared):
        content = '{"model_name": "Underwriting Risk Classification", "model_type": "CatBoostClassifier"}'
        tree = [{"type": "blob", "path": "model_card.json", "url": "http://blob/mc"}]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                self._blob_response(content),
            ]
            result = shared.get_repo_files("owner", "repo", [".json"])
        assert result["model_card.json"] == content

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_bad_blob_content_skipped(self, shared):
        tree = [{"type": "blob", "path": "bad.py", "url": "http://blob/bad"}]
        bad_blob = _make_response(json_data={"content": "not-valid-base64!!!"})
        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                self._tree_response(tree),
                bad_blob,
            ]
            # Should not raise; bad file is silently skipped
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert isinstance(result, dict)

    def test_uses_correct_url_format(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = self._tree_response([])
            shared.get_repo_files("myowner", "myrepo", [".py"])
        first_call_url = mock_get.call_args_list[0][0][0]
        assert "myowner" in first_call_url
        assert "myrepo" in first_call_url
        assert "recursive=1" in first_call_url

    def test_missing_tree_key_returns_empty(self, shared):
        with patch("requests.get") as mock_get: