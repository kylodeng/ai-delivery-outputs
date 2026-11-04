"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API invocation, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree fetching, extension filtering, max_files limit, base64 decoding
- get_pr_diff(): PR diff fetching, truncation behaviour
- write_output_file(): File creation (no SHA) and update (with SHA) in output repo
- post_pr_comment(): PR comment posting
- send_email(): SendGrid email dispatch, success and failure status codes
- email_html(): HTML template rendering for SUCCESS and FAILURE statuses
- write_audit_entry(): Audit log writing (JSON + Markdown) in output repo

Mocks used:
- unittest.mock.patch / MagicMock for:
    - anthropic.Anthropic (Claude client)
    - requests.get, requests.post, requests.put
    - base64.b64decode (selectively)
- Environment variables patched via os.environ / monkeypatch

TODOs:
- TODO: Integration test for call_claude() with a real Anthropic sandbox key
- TODO: Test write_audit_entry() JSON/Markdown content correctness once full source is available
- TODO: Verify GH_HEADERS propagation for authenticated requests in a live GitHub env
"""

import base64
import datetime
import importlib
import json
import os
import sys
import types
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

for _k, _v in _ENV_DEFAULTS.items():
    os.environ.setdefault(_k, _v)

# Ensure the scripts directory is importable
_scripts_dir = os.path.join(os.path.dirname(__file__), ".github", "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Also try repo root / .github/scripts relative to CWD
for _candidate in [
    os.path.join(os.getcwd(), ".github", "scripts"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".github", "scripts"),
]:
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

import shared  # noqa: E402  (must come after path/env setup)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_response(status_code: int = 200, json_data: dict = None, text: str = ""):
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
    def test_plain_json_unchanged(self):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_code_fence(self):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_generic_code_fence(self):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self):
        raw = "   \n{\"x\": true}\n   "
        assert shared.clean_json(raw) == '{"x": true}'

    def test_strips_fence_with_whitespace(self):
        raw = "  ```json\n[1,2,3]\n```  "
        result = shared.clean_json(raw)
        assert result == "[1,2,3]"

    def test_empty_string(self):
        assert shared.clean_json("") == ""

    def test_only_whitespace(self):
        assert shared.clean_json("   ") == ""

    def test_nested_backticks_in_content(self):
        """Content that has backtick-looking chars but no fence."""
        raw = '{"code": "x = `y`"}'
        assert shared.clean_json(raw) == '{"code": "x = `y`"}'

    def test_fence_no_newline_after_opening(self):
        """Edge: fence followed immediately by content on same line."""
        raw = "```json\n{}"
        result = shared.clean_json(raw)
        # Should at minimum strip the opening fence line
        assert "{}" in result

    @pytest.mark.parametrize("fence_lang", ["json", "JSON", ""])
    def test_various_fence_languages(self, fence_lang):
        raw = f"```{fence_lang}\n{{\"ok\": true}}\n```"
        result = shared.clean_json(raw)
        assert '{"ok": true}' in result


# ===========================================================================
# email_html
# ===========================================================================

class TestEmailHtml:
    def test_success_status_green(self):
        html = shared.email_html(
            tool="TestTool",
            status="SUCCESS",
            output_url="https://example.com/output",
            summary="All good",
            run_url="https://github.com/actions/run/1",
        )
        assert "green" in html
        assert "SUCCESS" in html

    def test_failure_status_red(self):
        html = shared.email_html(
            tool="TestTool",
            status="FAILURE",
            output_url="https://example.com/output",
            summary="Something broke",
            run_url="https://github.com/actions/run/2",
        )
        assert "red" in html
        assert "FAILURE" in html

    def test_tool_name_in_output(self):
        html = shared.email_html("MyTool", "SUCCESS", "url1", "summary", "url2")
        assert "MyTool" in html

    def test_output_url_link(self):
        html = shared.email_html("T", "SUCCESS", "https://out.example.com", "s", "https://run.example.com")
        assert "https://out.example.com" in html

    def test_run_url_link(self):
        html = shared.email_html("T", "SUCCESS", "https://out.example.com", "s", "https://run.example.com")
        assert "https://run.example.com" in html

    def test_summary_in_output(self):
        html = shared.email_html("T", "SUCCESS", "u", "My important summary text", "r")
        assert "My important summary text" in html

    def test_html_structure(self):
        html = shared.email_html("T", "SUCCESS", "u", "s", "r")
        assert "<html>" in html
        assert "</html>" in html
        assert "<body" in html

    def test_timestamp_format_present(self):
        html = shared.email_html("T", "SUCCESS", "u", "s", "r")
        # Should contain UTC timestamp like "2024-01-15 10:30 UTC"
        assert "UTC" in html

    def test_xss_passthrough(self):
        """shared.py does not escape HTML — verify content renders (not a security fix test)."""
        html = shared.email_html("T", "SUCCESS", "u", "<b>bold</b>", "r")
        assert "<b>bold</b>" in html

    @pytest.mark.parametrize("status,expected_color", [
        ("SUCCESS", "green"),
        ("FAILURE", "red"),
        ("ERROR", "red"),
    ])
    def test_status_color_mapping(self, status, expected_color):
        html = shared.email_html("T", status, "u", "s", "r")
        assert expected_color in html


# ===========================================================================
# call_claude
# ===========================================================================

class TestCallClaude:
    def _mock_client(self, text_response: str):
        mock_content = MagicMock()
        mock_content.text = text_response

        mock_message = MagicMock()
        mock_message.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message
        return mock_client

    @patch("shared.anthropic.Anthropic")
    def test_returns_text_response(self, mock_anthropic_cls):
        mock_client = self._mock_client('{"result": "ok"}')
        mock_anthropic_cls.return_value = mock_client

        result = shared.call_claude("system prompt", "user message")
        assert result == '{"result": "ok"}'

    @patch("shared.anthropic.Anthropic")
    def test_passes_system_and_user(self, mock_anthropic_cls):
        mock_client = self._mock_client("response")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("my system", "my user", max_tokens=1024)

        create_call = mock_client.messages.create
        create_call.assert_called_once()
        kwargs = create_call.call_args.kwargs
        assert kwargs["system"] == "my system"
        assert kwargs["messages"][0]["content"] == "my user"
        assert kwargs["max_tokens"] == 1024

    @patch("shared.anthropic.Anthropic")
    def test_default_max_tokens(self, mock_anthropic_cls):
        mock_client = self._mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u")
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["max_tokens"] == 4096

    @patch("shared.anthropic.Anthropic")
    def test_uses_configured_model(self, mock_anthropic_cls):
        mock_client = self._mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u")
        kwargs = mock_client.messages.create.call_args.kwargs
        assert kwargs["model"] == shared.MODEL

    @patch("shared.anthropic.Anthropic")
    def test_api_error_propagates(self, mock_anthropic_cls):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API error")
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="API error"):
            shared.call_claude("s", "u")

    @patch("shared.anthropic.Anthropic")
    def test_client_created_with_api_key(self, mock_anthropic_cls):
        mock_client = self._mock_client("ok")
        mock_anthropic_cls.return_value = mock_client

        shared.call_claude("s", "u")
        mock_anthropic_cls.assert_called_once_with(api_key=shared.ANTHROPIC_API_KEY)

    @patch("shared.anthropic.Anthropic")
    def test_empty_response_text(self, mock_anthropic_cls):
        mock_client = self._mock_client("")
        mock_anthropic_cls.return_value = mock_client

        result = shared.call_claude("s", "u")
        assert result == ""


# ===========================================================================
# get_repo_files
# ===========================================================================

class TestGetRepoFiles:
    def _tree_item(self, path: str, item_type: str = "blob", url: str = "https://api.github.com/blob/abc"):
        return {"type": item_type, "path": path, "url": url}

    def _blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        return {"content": encoded + "\n"}  # GitHub adds newline

    @patch("shared.requests.get")
    def test_happy_path_single_extension(self, mock_get):
        tree_resp = _make_response(json_data={
            "tree": [
                self._tree_item("src/main.py"),
                self._tree_item("README.md"),
            ]
        })
        blob_resp = _make_response(json_data=self._blob_response("print('hello')"))

        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/main.py" in result
        assert result["src/main.py"] == "print('hello')"
        assert "README.md" not in result

    @patch("shared.requests.get")
    def test_multiple_extensions(self, mock_get):
        tree_resp = _make_response(json_data={
            "tree": [
                self._tree_item("a.py"),
                self._tree_item("b.ts"),
                self._tree_item("c.go"),
                self._tree_item("d.txt"),
            ]
        })
        blob_py = _make_response(json_data=self._blob_response("python"))
        blob_ts = _make_response(json_data=self._blob_response("typescript"))
        blob_go = _make_response(json_data=self._blob_response("golang"))

        mock_get.side_effect = [tree_resp, blob_py, blob_ts, blob_go]

        result = shared.get_repo_files("owner", "repo", [".py", ".ts", ".go"])
        assert set(result.keys()) == {"a.py", "b.ts", "c.go"}

    @patch("shared.requests.get")
    def test_max_files_limit(self, mock_get):
        items = [self._tree_item(f"file{i}.py") for i in range(10)]
        tree_resp = _make_response(json_data={"tree": items})
        blob_resp = _make_response(json_data=self._blob_response("content"))

        # Tree + 3 blobs (max_files=3)
        mock_get.side_effect = [tree_resp] + [blob_resp] * 3

        result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    @patch("shared.requests.get")
    def test_skips_non_blob_items(self, mock_get):
        tree_resp = _make_response(json_data={
            "tree": [
                {"type": "tree", "path": "src/", "url": "u"},
                self._tree_item("src/main.py"),
            ]
        })
        blob_resp = _make_response(json_data=self._blob_response("hello"))
        mock_get.side_effect = [tree_resp, blob_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        assert "src/" not in result
        assert "src/main.py" in result

    @patch("shared.requests.get")
    def test_empty_tree(self, mock_get):
        tree_resp = _make_response(json_data={"tree": []})
        mock_get.side_effect = [tree_resp]

        result = shared.get_repo_files("owner", "repo", [".py"])
        