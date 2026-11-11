"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API integration
- clean_json(): Markdown fence stripping utility
- get_repo_files(): GitHub repo file fetching with extension filtering
- get_pr_diff(): GitHub PR diff fetching
- write_output_file(): GitHub file create/update operations
- post_pr_comment(): GitHub PR comment posting
- send_email(): SendGrid email delivery
- email_html(): HTML email template generation
- write_audit_entry(): Audit log writing (JSON + Markdown)

Mocks used:
- unittest.mock.patch for os.environ (all API keys/config)
- unittest.mock.MagicMock / patch for anthropic.Anthropic client
- unittest.mock.patch for requests.get, requests.post, requests.put
- base64 decoding is exercised with real encoded fixtures

TODOs:
- TODO: write_audit_entry full integration — needs actual file content round-trip
  verification; stub tests provided for JSON/Markdown format validation
- TODO: call_claude streaming variants if ever adopted
- TODO: email_html timestamp — currently non-deterministic (utcnow); consider
  injecting a clock for deterministic assertions
"""

import base64
import datetime
import json
import os
import sys
from types import SimpleNamespace
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
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}

with patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
    import importlib
    import types

    # Stub anthropic before import so the module-level client construction works
    anthropic_stub = types.ModuleType("anthropic")
    anthropic_stub.Anthropic = MagicMock()
    sys.modules.setdefault("anthropic", anthropic_stub)

    # Now import the module under test
    import importlib.util, pathlib

    _spec = importlib.util.spec_from_file_location(
        "shared",
        pathlib.Path(__file__).parent.parent / ".github" / "scripts" / "shared.py",
    )
    shared = importlib.util.module_from_spec(_spec)
    with patch.dict(os.environ, _ENV_DEFAULTS, clear=False):
        _spec.loader.exec_module(shared)

# Re-export for convenience
call_claude = shared.call_claude
clean_json = shared.clean_json
get_repo_files = shared.get_repo_files
get_pr_diff = shared.get_pr_diff
write_output_file = shared.write_output_file
post_pr_comment = shared.post_pr_comment
send_email = shared.send_email
email_html = shared.email_html
write_audit_entry = shared.write_audit_entry


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture()
def mock_requests(monkeypatch):
    """Return a namespace with pre-wired mock callables for requests.*."""
    get_mock = MagicMock()
    post_mock = MagicMock()
    put_mock = MagicMock()
    monkeypatch.setattr(shared.requests, "get", get_mock)
    monkeypatch.setattr(shared.requests, "post", post_mock)
    monkeypatch.setattr(shared.requests, "put", put_mock)
    return SimpleNamespace(get=get_mock, post=post_mock, put=put_mock)


def _make_response(json_data=None, text="", status_code=200):
    """Helper — build a mock requests.Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data or {}
    return resp


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


# ===========================================================================
# clean_json
# ===========================================================================


class TestCleanJson:
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_fence(self):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = clean_json(raw)
        assert result == '{"key": "value"}'

    def test_leading_trailing_whitespace(self):
        raw = "   \n```json\n{\"a\":1}\n```\n   "
        result = clean_json(raw)
        assert result == '{"a":1}'

    def test_empty_string(self):
        assert clean_json("") == ""

    def test_only_whitespace(self):
        assert clean_json("   ") == ""

    def test_multiline_json_in_fence(self):
        inner = '{\n  "product_name": "Generations II",\n  "doc_type": "product_brochure"\n}'
        raw = f"```json\n{inner}\n```"
        result = clean_json(raw)
        assert result == inner

    def test_no_closing_fence_keeps_content(self):
        # Only opening fence — rsplit on ``` finds nothing extra, keeps body
        raw = "```json\n{\"a\":1}"
        result = clean_json(raw)
        assert '{"a":1}' in result

    def test_fence_without_language_tag(self):
        raw = "```\nhello\n```"
        assert clean_json(raw) == "hello"

    @pytest.mark.parametrize("raw,expected", [
        ('{"x":1}', '{"x":1}'),
        ('  {"x":1}  ', '{"x":1}'),
        ("```json\n[1,2,3]\n```", "[1,2,3]"),
    ])
    def test_parametrized_cases(self, raw, expected):
        assert clean_json(raw) == expected


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    def _make_client(self, text="Hello from Claude"):
        client = MagicMock()
        msg = MagicMock()
        msg.content = [MagicMock(text=text)]
        client.messages.create.return_value = msg
        return client

    def test_happy_path_returns_text(self, monkeypatch):
        client = self._make_client("Test response")
        monkeypatch.setattr(shared.anthropic, "Anthropic", MagicMock(return_value=client))
        result = call_claude("system prompt", "user prompt")
        assert result == "Test response"

    def test_passes_correct_model(self, monkeypatch):
        client = self._make_client()
        mock_cls = MagicMock(return_value=client)
        monkeypatch.setattr(shared.anthropic, "Anthropic", mock_cls)
        call_claude("sys", "usr")
        _, kwargs = client.messages.create.call_args
        assert kwargs["model"] == shared.MODEL

    def test_passes_system_and_user(self, monkeypatch):
        client = self._make_client()
        monkeypatch.setattr(shared.anthropic, "Anthropic", MagicMock(return_value=client))
        call_claude("MY_SYSTEM", "MY_USER")
        _, kwargs = client.messages.create.call_args
        assert kwargs["system"] == "MY_SYSTEM"
        assert kwargs["messages"][0]["content"] == "MY_USER"

    def test_default_max_tokens(self, monkeypatch):
        client = self._make_client()
        monkeypatch.setattr(shared.anthropic, "Anthropic", MagicMock(return_value=client))
        call_claude("s", "u")
        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 4096

    def test_custom_max_tokens(self, monkeypatch):
        client = self._make_client()
        monkeypatch.setattr(shared.anthropic, "Anthropic", MagicMock(return_value=client))
        call_claude("s", "u", max_tokens=1024)
        _, kwargs = client.messages.create.call_args
        assert kwargs["max_tokens"] == 1024

    def test_uses_api_key_from_env(self, monkeypatch):
        client = self._make_client()
        mock_cls = MagicMock(return_value=client)
        monkeypatch.setattr(shared.anthropic, "Anthropic", mock_cls)
        call_claude("s", "u")
        mock_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    def test_propagates_api_exception(self, monkeypatch):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API error")
        monkeypatch.setattr(shared.anthropic, "Anthropic", MagicMock(return_value=client))
        with pytest.raises(RuntimeError, match="API error"):
            call_claude("s", "u")

    def test_empty_response_text(self, monkeypatch):
        client = self._make_client("")
        monkeypatch.setattr(shared.anthropic, "Anthropic", MagicMock(return_value=client))
        result = call_claude("s", "u")
        assert result == ""


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    def _tree_response(self, items):
        return _make_response(json_data={"tree": items})

    def _blob_response(self, content: str):
        return _make_response(json_data={"content": _b64(content)})

    def test_returns_files_matching_extension(self, mock_requests):
        tree = [
            {"type": "blob", "path": "foo.py", "url": "http://blob/foo"},
            {"type": "blob", "path": "bar.md", "url": "http://blob/bar"},
        ]
        mock_requests.get.side_effect = [
            self._tree_response(tree),
            self._blob_response("print('hello')"),
        ]
        result = get_repo_files("owner", "repo", [".py"])
        assert "foo.py" in result
        assert "bar.md" not in result
        assert result["foo.py"] == "print('hello')"

    def test_excludes_non_blob_items(self, mock_requests):
        tree = [
            {"type": "tree", "path": "src", "url": "http://tree/src"},
            {"type": "blob", "path": "main.py", "url": "http://blob/main"},
        ]
        mock_requests.get.side_effect = [
            self._tree_response(tree),
            self._blob_response("# main"),
        ]
        result = get_repo_files("owner", "repo", [".py"])
        assert "src" not in result
        assert "main.py" in result

    def test_respects_max_files_limit(self, mock_requests):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob_resp = self._blob_response("content")
        # First call = tree, then up to max_files blob calls
        mock_requests.get.side_effect = [self._tree_response(tree)] + [
            self._blob_response(f"content{i}") for i in range(5)
        ]
        result = get_repo_files("owner", "repo", [".py"], max_files=5)
        assert len(result) == 5

    def test_multiple_extensions(self, mock_requests):
        tree = [
            {"type": "blob", "path": "a.py", "url": "http://blob/a"},
            {"type": "blob", "path": "b.ts", "url": "http://blob/b"},
            {"type": "blob", "path": "c.go", "url": "http://blob/c"},
        ]
        mock_requests.get.side_effect = [
            self._tree_response(tree),
            self._blob_response("python"),
            self._blob_response("typescript"),
        ]
        result = get_repo_files("owner", "repo", [".py", ".ts"])
        assert "a.py" in result
        assert "b.ts" in result
        assert "c.go" not in result

    def test_handles_base64_decode_error_gracefully(self, mock_requests):
        tree = [{"type": "blob", "path": "bad.py", "url": "http://blob/bad"}]
        bad_blob = _make_response(json_data={"content": "not-valid-base64!!!"})
        mock_requests.get.side_effect = [self._tree_response(tree), bad_blob]
        # Should not raise — the except block swallows it
        result = get_repo_files("owner", "repo", [".py"])
        assert "bad.py" not in result

    def test_empty_tree_returns_empty_dict(self, mock_requests):
        mock_requests.get.return_value = self._tree_response([])
        result = get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_correct_url_constructed(self, mock_requests):
        mock_requests.get.return_value = self._tree_response([])
        get_repo_files("my-owner", "my-repo", [".py"])
        called_url = mock_requests.get.call_args_list[0][0][0]
        assert "my-owner" in called_url
        assert "my-repo" in called_url
        assert "recursive=1" in called_url

    def test_unicode_file_content(self, mock_requests):
        content = "# 中文注释\nprint('hello')"
        tree = [{"type": "blob", "path": "unicode.py", "url": "http://blob/u"}]
        mock_requests.get.side_effect = [
            self._tree_response(tree),
            self._blob_response(content),
        ]
        result = get_repo_files("o", "r", [".py"])
        assert result["unicode.py"] == content


# ===========================================================================
# get_pr_diff
# ===========================================================================


class TestGetPrDiff:
    def test_returns_diff_text(self, mock_requests):
        mock_requests.get.return_value = _make_response(text="diff --git a/foo b/foo\n+added line")
        result = get_pr_diff("owner", "repo", 42)
        assert "diff --git" in result
        assert "+added line" in result

    def test_uses_diff_accept_header(self, mock_requests):
        mock_requests.get.return_value = _make_response(text="")
        get_pr_diff("owner", "repo", 1)
        called_headers = mock_requests.get.call_args[1]["headers"]
        assert called_headers["Accept"] == "application/vnd.github.diff"

    def test_correct_pr_