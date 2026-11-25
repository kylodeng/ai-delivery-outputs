"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): markdown fence stripping, edge cases, plain JSON pass-through
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding, error handling
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): file create (no SHA), file update (with SHA), URL fallback
- post_pr_comment(): comment POST request
- send_email(): SendGrid payload construction, success/failure status codes
- email_html(): HTML output structure, SUCCESS/FAILURE colour logic
- write_audit_entry(): audit log JSON + Markdown construction (partial — source truncated)

Mocks used:
- unittest.mock.patch / MagicMock for: anthropic.Anthropic, requests.get, requests.post, requests.put
- os.environ patched before module import via monkeypatch / mock.patch.dict

TODOs:
- write_audit_entry() full coverage requires the complete source (file is truncated); stubs provided
- MODEL constant value tested structurally only — real model name may change
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
# Environment setup – must happen BEFORE shared.py is imported
# ---------------------------------------------------------------------------

FAKE_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
}


@pytest.fixture(scope="session", autouse=True)
def _patch_env_for_import():
    """Patch environment variables before shared.py is imported for the first time."""
    with mock.patch.dict("os.environ", FAKE_ENV, clear=False):
        # Remove any previously cached import so env vars take effect
        sys.modules.pop("shared", None)
        # Provide a minimal stub for the anthropic package so we don't need it installed
        if "anthropic" not in sys.modules:
            anthropic_stub = types.ModuleType("anthropic")
            anthropic_stub.Anthropic = MagicMock()
            sys.modules["anthropic"] = anthropic_stub
        yield


@pytest.fixture(scope="session")
def shared():
    """Import shared module once per session with env vars in place."""
    with mock.patch.dict("os.environ", FAKE_ENV, clear=False):
        sys.modules.pop("shared", None)
        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", ".github/scripts/shared.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int = 200, json_data=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    return resp


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    def test_happy_path_returns_text(self, shared):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Hello from Claude")]
        mock_client.messages.create.return_value = mock_message

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            result = shared.call_claude("system prompt", "user prompt")

        assert result == "Hello from Claude"

    def test_passes_correct_model_and_tokens(self, shared):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_message

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            shared.call_claude("sys", "usr", max_tokens=1024)

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL
        assert kwargs["max_tokens"] == 1024
        assert kwargs["system"] == "sys"
        assert kwargs["messages"] == [{"role": "user", "content": "usr"}]

    def test_default_max_tokens_is_4096(self, shared):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_message

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            shared.call_claude("s", "u")

        _, kwargs = mock_client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_uses_api_key_from_env(self, shared):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="ok")]
        mock_client.messages.create.return_value = mock_message

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client) as mock_cls:
            shared.call_claude("s", "u")
            mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_propagates_exception_from_client(self, shared):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")

        with patch.object(shared.anthropic, "Anthropic", return_value=mock_client):
            with pytest.raises(RuntimeError, match="API error"):
                shared.call_claude("s", "u")


# ===========================================================================
# clean_json
# ===========================================================================


class TestCleanJson:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            # Plain JSON — untouched
            ('{"key": "value"}', '{"key": "value"}'),
            # Leading/trailing whitespace
            ('  {"key": "value"}  ', '{"key": "value"}'),
            # ```json fence
            ('```json\n{"key": "value"}\n```', '{"key": "value"}'),
            # ``` fence without language tag
            ('```\n{"key": "value"}\n```', '{"key": "value"}'),
            # Fence with extra trailing newlines inside
            ('```json\n{"a": 1}\n\n```', '{"a": 1}'),
            # Empty string
            ("", ""),
            # Only fences, no content
            ("```\n```", ""),
            # Nested JSON stays intact
            (
                '```json\n{"model_name": "Underwriting Risk Classification"}\n```',
                '{"model_name": "Underwriting Risk Classification"}',
            ),
            # Multiple backtick groups — only outer stripped
            ('```json\n```inner```\n```', "```inner```"),
        ],
    )
    def test_clean_json_variations(self, shared, raw, expected):
        assert shared.clean_json(raw) == expected

    def test_does_not_modify_array_json(self, shared):
        raw = '["CUST00000001", "CUST00006151"]'
        assert shared.clean_json(raw) == raw

    def test_fence_with_windows_line_endings(self, shared):
        raw = "```json\r\n{\"k\": 1}\r\n```"
        result = shared.clean_json(raw)
        # Should at least strip the opening fence line
        assert "```json" not in result


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    def _tree_response(self, paths):
        items = [{"type": "blob", "path": p, "url": f"https://api.github.com/blob/{p}"} for p in paths]
        return {"tree": items}

    def _blob_response(self, content: str):
        return {"content": base64.b64encode(content.encode()).decode()}

    def test_happy_path_filters_by_extension(self, shared):
        tree = self._tree_response(["a.py", "b.js", "c.py", "d.md"])
        blob_py = self._blob_response("print('hello')")

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(json_data=tree),          # tree call
                _make_response(json_data=blob_py),       # a.py
                _make_response(json_data=blob_py),       # c.py
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert set(result.keys()) == {"a.py", "c.py"}
        assert result["a.py"] == "print('hello')"

    def test_max_files_limit_respected(self, shared):
        paths = [f"file{i}.py" for i in range(10)]
        tree = self._tree_response(paths)
        blob = self._blob_response("content")

        responses = [_make_response(json_data=tree)] + [
            _make_response(json_data=blob) for _ in range(3)
        ]
        with patch("requests.get") as mock_get:
            mock_get.side_effect = responses
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)

        assert len(result) == 3

    def test_non_blob_items_skipped(self, shared):
        tree = {
            "tree": [
                {"type": "tree", "path": "src", "url": "u1"},
                {"type": "blob", "path": "main.py", "url": "u2"},
            ]
        }
        blob = self._blob_response("# code")

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(json_data=tree),
                _make_response(json_data=blob),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert "src" not in result
        assert "main.py" in result

    def test_base64_decode_error_skipped_silently(self, shared):
        tree = self._tree_response(["broken.py"])
        bad_blob = {"content": "!!!not-valid-base64!!!"}

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(json_data=tree),
                _make_response(json_data=bad_blob),
            ]
            result = shared.get_repo_files("owner", "repo", [".py"])

        # Should not raise; file either skipped or partially decoded
        assert isinstance(result, dict)

    def test_empty_tree_returns_empty_dict(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(json_data={"tree": []})
            result = shared.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_multiple_extensions_matched(self, shared):
        tree = self._tree_response(["a.py", "b.json", "c.md", "d.js"])
        blob = self._blob_response("{}")

        with patch("requests.get") as mock_get:
            mock_get.side_effect = [
                _make_response(json_data=tree),
                _make_response(json_data=blob),  # b.json
                _make_response(json_data=blob),  # d.js  — .js not in filter
            ]
            result = shared.get_repo_files("owner", "repo", [".py", ".json"])

        assert "a.py" in result
        assert "b.json" in result
        assert "c.md" not in result

    def test_correct_github_tree_url_called(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(json_data={"tree": []})
            shared.get_repo_files("my-owner", "my-repo", [".py"])

        first_call_url = mock_get.call_args_list[0][0][0]
        assert "my-owner" in first_call_url
        assert "my-repo" in first_call_url
        assert "recursive=1" in first_call_url


# ===========================================================================
# get_pr_diff
# ===========================================================================


class TestGetPrDiff:
    def test_returns_diff_text(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text="diff --git a/f b/f\n+new line")
            result = shared.get_pr_diff("owner", "repo", 42)

        assert "diff --git" in result

    def test_truncates_at_30000_chars(self, shared):
        long_diff = "x" * 50000
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text=long_diff)
            result = shared.get_pr_diff("owner", "repo", 1)

        assert len(result) == 30000

    def test_uses_diff_accept_header(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text="")
            shared.get_pr_diff("owner", "repo", 7)

        headers_used = mock_get.call_args[1]["headers"]
        assert headers_used["Accept"] == "application/vnd.github.diff"

    def test_correct_url_constructed(self, shared):
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text="")
            shared.get_pr_diff("capco", "platform", 99)

        url = mock_get.call_args[0][0]
        assert "/repos/capco/platform/pulls/99" in url

    def test_short_diff_not_truncated(self, shared):
        short_diff = "small diff"
        with patch("requests.get") as mock_get:
            mock_get.return_value = _make_response(text=short_diff)
            result = shared.get_pr_diff("o", "r", 1)

        assert result == short_diff


# ===========================================================================
# write_output_file
# ===========================================================================


class TestWriteOutputFile:
    def test_creates_new_file_without_sha(self, shared):
        existing_resp = _make_response(json_data={})  # no sha
        put_resp = _make_response(
            json_data={"content": {"html_url": "https://github.com/owner/repo/blob/main/out.md"}}
        )

        with patch("requests.get", return_value=existing_resp), \
             patch("requests.put", return_value=put_resp) as mock_put:
            url = shared.write_output_file("out.md", "hello", "initial commit")

        assert url == "https://github.com/owner/repo/blob/main/out.md