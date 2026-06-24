"""
Test suite for .github/scripts/shared.py

What is tested:
- call_claude(): Claude API interaction, response extraction
- clean_json(): Markdown fence stripping, edge cases
- get_repo_files(): GitHub tree traversal, base64 decoding, extension filtering, max_files limit
- get_pr_diff(): PR diff fetching, truncation
- write_output_file(): File create (no SHA) and update (with SHA) paths
- post_pr_comment(): PR comment posting
- send_email(): SendGrid integration, success/failure status codes
- email_html(): HTML email rendering, status color logic
- write_audit_entry(): Audit log construction (partial — source truncated)

Mocks used:
- unittest.mock.patch for os.environ (required before module import)
- unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client

TODOs:
- write_audit_entry(): Source code is truncated; full behaviour of JSON/Markdown log appending
  cannot be fully tested without seeing the complete function body.
- MODEL constant value may change; tests pin to "claude-sonnet-4-6" as observed in source.
"""

import base64
import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Environment bootstrap — must happen BEFORE shared.py is imported,
# because the module executes os.environ[] reads at import time.
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
    """Patch environment variables before shared.py is first imported."""
    with patch.dict("os.environ", ENV_DEFAULTS, clear=False):
        # Force (re-)import with the patched env
        if "shared" in sys.modules:
            del sys.modules["shared"]
        # Also remove from any cached path under .github.scripts
        for key in list(sys.modules.keys()):
            if "shared" in key:
                del sys.modules[key]

        import importlib.util, pathlib

        spec = importlib.util.spec_from_file_location(
            "shared", pathlib.Path(__file__).parent.parent / "scripts" / "shared.py"
        )
        module = importlib.util.module_from_spec(spec)
        # Stub anthropic before executing so we don't need a real install path
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)
        spec.loader.exec_module(module)
        sys.modules["shared"] = module
        yield module


@pytest.fixture()
def shared():
    return sys.modules["shared"]


# ===========================================================================
# clean_json
# ===========================================================================


class TestCleanJson:
    def test_no_fences_unchanged(self, shared):
        raw = '{"key": "value"}'
        assert shared.clean_json(raw) == '{"key": "value"}'

    def test_strips_json_fence(self, shared):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_strips_plain_fence(self, shared):
        raw = "```\n{\"a\": 1}\n```"
        result = shared.clean_json(raw)
        assert result == '{"a": 1}'

    def test_strips_leading_trailing_whitespace(self, shared):
        raw = '   {"x": 2}   '
        assert shared.clean_json(raw) == '{"x": 2}'

    def test_fence_with_extra_whitespace_inside(self, shared):
        raw = "```json\n\n  {\"k\": \"v\"}\n\n```"
        result = shared.clean_json(raw)
        assert result.strip() == '{"k": "v"}'

    def test_empty_string(self, shared):
        assert shared.clean_json("") == ""

    def test_empty_fence(self, shared):
        raw = "```\n```"
        result = shared.clean_json(raw)
        assert result == ""

    def test_only_backticks_no_closing(self, shared):
        """Fence without closing — should still strip the opening line."""
        raw = "```json\n{\"partial\": true}"
        result = shared.clean_json(raw)
        assert '{"partial": true}' in result

    def test_valid_json_after_clean(self, shared):
        raw = "```json\n{\"model_name\": \"Underwriting Risk Classification\"}\n```"
        result = shared.clean_json(raw)
        parsed = json.loads(result)
        assert parsed["model_name"] == "Underwriting Risk Classification"

    def test_does_not_strip_non_fence_backticks(self, shared):
        raw = 'Use `code` here: {"key": "val"}'
        result = shared.clean_json(raw)
        # Should be unchanged — does not start with ```
        assert result == raw


# ===========================================================================
# call_claude
# ===========================================================================


class TestCallClaude:
    @pytest.fixture(autouse=True)
    def _setup(self, shared):
        self.shared = shared

    def _make_mock_client(self, text="Hello from Claude"):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=text)]
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_returns_text_content(self, shared):
        mock_client = self._make_mock_client("Test response")
        with patch("anthropic.Anthropic", return_value=mock_client):
            # Re-bind the module-level client creation
            with patch.object(shared, "call_claude", wraps=shared.call_claude):
                import anthropic as anth
                original = anth.Anthropic
                anth.Anthropic = MagicMock(return_value=mock_client)
                try:
                    result = shared.call_claude("system prompt", "user prompt")
                    assert result == "Test response"
                finally:
                    anth.Anthropic = original

    def test_passes_correct_model(self, shared):
        mock_client = self._make_mock_client("ok")
        import anthropic as anth
        original = anth.Anthropic
        anth.Anthropic = MagicMock(return_value=mock_client)
        try:
            shared.call_claude("sys", "usr")
            call_kwargs = mock_client.messages.create.call_args
            assert call_kwargs.kwargs.get("model") == "claude-sonnet-4-6" or \
                   call_kwargs[1].get("model") == "claude-sonnet-4-6" or \
                   "claude-sonnet-4-6" in str(call_kwargs)
        finally:
            anth.Anthropic = original

    def test_default_max_tokens(self, shared):
        mock_client = self._make_mock_client("ok")
        import anthropic as anth
        original = anth.Anthropic
        anth.Anthropic = MagicMock(return_value=mock_client)
        try:
            shared.call_claude("sys", "usr")
            call_kwargs = mock_client.messages.create.call_args
            kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
            assert kwargs.get("max_tokens") == 4096
        finally:
            anth.Anthropic = original

    def test_custom_max_tokens(self, shared):
        mock_client = self._make_mock_client("ok")
        import anthropic as anth
        original = anth.Anthropic
        anth.Anthropic = MagicMock(return_value=mock_client)
        try:
            shared.call_claude("sys", "usr", max_tokens=1024)
            call_kwargs = mock_client.messages.create.call_args
            kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
            assert kwargs.get("max_tokens") == 1024
        finally:
            anth.Anthropic = original

    def test_passes_system_and_user(self, shared):
        mock_client = self._make_mock_client("ok")
        import anthropic as anth
        original = anth.Anthropic
        anth.Anthropic = MagicMock(return_value=mock_client)
        try:
            shared.call_claude("my system", "my user")
            call_kwargs = mock_client.messages.create.call_args
            kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
            assert kwargs.get("system") == "my system"
            messages = kwargs.get("messages", [])
            assert any(m.get("content") == "my user" for m in messages)
        finally:
            anth.Anthropic = original

    def test_empty_system_and_user(self, shared):
        mock_client = self._make_mock_client("")
        import anthropic as anth
        original = anth.Anthropic
        anth.Anthropic = MagicMock(return_value=mock_client)
        try:
            result = shared.call_claude("", "")
            assert result == ""
        finally:
            anth.Anthropic = original


# ===========================================================================
# get_repo_files
# ===========================================================================


class TestGetRepoFiles:
    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content_str):
        encoded = base64.b64encode(content_str.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_filters_by_extension(self, shared):
        tree = [
            {"type": "blob", "path": "file.py", "url": "http://blob/1"},
            {"type": "blob", "path": "file.md", "url": "http://blob/2"},
            {"type": "blob", "path": "README.txt", "url": "http://blob/3"},
        ]
        blob_py = self._make_blob_response("print('hello')")
        blob_txt = self._make_blob_response("readme text")

        def side_effect(url, headers):
            if url.endswith("?recursive=1"):
                return self._make_tree_response(tree)
            if "blob/1" in url:
                return blob_py
            if "blob/3" in url:
                return blob_txt
            return self._make_blob_response("")

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py", ".txt"])
        assert "file.py" in result
        assert "README.txt" in result
        assert "file.md" not in result

    def test_skips_tree_nodes(self, shared):
        tree = [
            {"type": "tree", "path": "src", "url": "http://tree/1"},
            {"type": "blob", "path": "main.py", "url": "http://blob/main"},
        ]
        blob = self._make_blob_response("# main")

        def side_effect(url, headers):
            if "recursive=1" in url:
                return self._make_tree_response(tree)
            return blob

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert "main.py" in result
        assert "src" not in result

    def test_respects_max_files(self, shared):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"http://blob/{i}"}
            for i in range(10)
        ]
        blob = self._make_blob_response("code")

        def side_effect(url, headers):
            if "recursive=1" in url:
                return self._make_tree_response(tree)
            return blob

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"], max_files=3)
        assert len(result) == 3

    def test_decodes_base64_content(self, shared):
        content = '{"model_name": "Underwriting Risk Classification"}'
        tree = [{"type": "blob", "path": "model_card.json", "url": "http://blob/mc"}]
        blob = self._make_blob_response(content)

        def side_effect(url, headers):
            if "recursive=1" in url:
                return self._make_tree_response(tree)
            return blob

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".json"])
        assert result["model_card.json"] == content

    def test_skips_file_on_decode_error(self, shared):
        tree = [{"type": "blob", "path": "bad.py", "url": "http://blob/bad"}]

        bad_blob = MagicMock()
        bad_blob.json.return_value = {}  # no "content" key → base64.b64decode raises

        def side_effect(url, headers):
            if "recursive=1" in url:
                return self._make_tree_response(tree)
            return bad_blob

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])
        # Should not crash; file simply omitted
        assert result == {}

    def test_empty_tree(self, shared):
        def side_effect(url, headers):
            mock = MagicMock()
            mock.json.return_value = {"tree": []}
            return mock

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_missing_tree_key(self, shared):
        """API returns unexpected shape — should default to empty list."""
        def side_effect(url, headers):
            mock = MagicMock()
            mock.json.return_value = {}
            return mock

        with patch("requests.get", side_effect=side_effect):
            result = shared.get_repo_files("owner", "repo", [".py"])
        assert result == {}

    def test_multiple_extensions(self, shared):
        tree = [
            {"type": "blob", "path": "app.js", "url": "http://blob/js"},
            {"type": "blob", "path": "style.css", "url": "http://blob/css"},
            {"type": "blob", "path": "data.json", "url": "http://blob/json"},
        ]

        def side_effect(url, headers):
            if "recursive=1" in url:
                return self._make_tree_response(tree)
            return self._make_blob_response("content")

        with patch("requests.get", side_effect=side_effect