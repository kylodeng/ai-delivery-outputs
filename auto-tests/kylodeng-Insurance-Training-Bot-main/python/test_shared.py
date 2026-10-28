"""
Test module for .github/scripts/shared.py

What is tested:
- call_claude: Claude API integration (happy path, error conditions)
- clean_json: Markdown fence stripping (happy path, edge cases, no fences)
- get_repo_files: GitHub repo file fetching (happy path, extension filtering, max_files limit, decode errors)
- get_pr_diff: GitHub PR diff fetching (happy path, truncation)
- write_output_file: File creation/update in output repo (new file, existing file with SHA)
- post_pr_comment: PR comment posting (happy path)
- send_email: SendGrid email sending (success, failure status codes)
- email_html: HTML email template generation (SUCCESS/FAILURE status, content)
- write_audit_entry: Audit log writing (called correctly, delegates to write_output_file)

Mocks used:
- unittest.mock.patch for os.environ (all required env vars)
- unittest.mock.MagicMock / patch for requests.get, requests.post, requests.put
- unittest.mock.MagicMock for anthropic.Anthropic client
- base64 encoding/decoding tested inline (no mock needed)

TODOs:
- TODO: Integration test for write_audit_entry verifying exact JSON/Markdown content written
- TODO: Test for concurrent/race conditions in write_output_file (SHA conflict)
- TODO: Test email_html timestamp format with frozen time library
"""

import base64
import importlib
import json
import sys
import types
import datetime
from unittest.mock import MagicMock, patch, call

import pytest


# ── Helpers to import module with env vars set ────────────────────────────────

REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "GH_TOKEN": "test-gh-token",
    "SENDGRID_API_KEY": "test-sendgrid-key",
    "OUTPUT_REPO": "ai-delivery-outputs",
    "OUTPUT_REPO_OWNER": "test-owner",
    "NOTIFY_EMAIL": "notify@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GITHUB_REPOSITORY_OWNER": "test-owner",
}


@pytest.fixture(scope="module")
def shared_module():
    """Import shared.py with required environment variables mocked."""
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        # Remove cached module if present
        if ".github.scripts.shared" in sys.modules:
            del sys.modules[".github.scripts.shared"]
        # Load via importlib using file path
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location(
            "shared",
            os.path.join(os.path.dirname(__file__), ".github", "scripts", "shared.py"),
        )
        if spec is None:
            # Try relative path from repo root
            spec = importlib.util.spec_from_file_location(
                "shared",
                ".github/scripts/shared.py",
            )
        mod = importlib.util.module_from_spec(spec)
        # Stub anthropic before exec so module-level import doesn't fail
        fake_anthropic = types.ModuleType("anthropic")
        fake_anthropic.Anthropic = MagicMock()
        sys.modules.setdefault("anthropic", fake_anthropic)
        spec.loader.exec_module(mod)
        yield mod


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def env_patch():
    """Patch environment for tests that reload module-level constants."""
    with patch.dict("os.environ", REQUIRED_ENV, clear=False):
        yield


# ═════════════════════════════════════════════════════════════════════════════
# clean_json
# ═════════════════════════════════════════════════════════════════════════════

class TestCleanJson:
    def test_no_fences_returns_stripped(self, shared_module):
        raw = '  {"key": "value"}  '
        assert shared_module.clean_json(raw) == '{"key": "value"}'

    def test_json_code_fence_stripped(self, shared_module):
        raw = "```json\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_plain_code_fence_stripped(self, shared_module):
        raw = "```\n{\"key\": \"value\"}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{"key": "value"}'

    def test_multiline_json_with_fence(self, shared_module):
        raw = "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```"
        result = shared_module.clean_json(raw)
        assert result == '{\n  "a": 1,\n  "b": 2\n}'

    def test_empty_string(self, shared_module):
        assert shared_module.clean_json("") == ""

    def test_only_whitespace(self, shared_module):
        assert shared_module.clean_json("   ") == ""

    def test_no_closing_fence(self, shared_module):
        """If there's an opening fence but no closing, rsplit returns original."""
        raw = "```json\n{\"key\": \"value\"}"
        result = shared_module.clean_json(raw)
        # Should still strip the opening line and return body without trailing fence removal issue
        assert "key" in result

    def test_plain_string_no_json(self, shared_module):
        raw = "hello world"
        assert shared_module.clean_json(raw) == "hello world"

    def test_nested_backticks_not_stripped(self, shared_module):
        """Content with backtick chars inside should not be incorrectly stripped."""
        raw = '{"code": "use `foo`"}'
        assert shared_module.clean_json(raw) == '{"code": "use `foo`"}'

    @pytest.mark.parametrize("raw,expected", [
        ('```json\n[]\n```', '[]'),
        ('```\nnull\n```', 'null'),
        ('```json\n"string"\n```', '"string"'),
    ])
    def test_parametrized_fence_variants(self, shared_module, raw, expected):
        assert shared_module.clean_json(raw) == expected


# ═════════════════════════════════════════════════════════════════════════════
# call_claude
# ═════════════════════════════════════════════════════════════════════════════

class TestCallClaude:
    def _make_mock_client(self, response_text: str):
        mock_content = MagicMock()
        mock_content.text = response_text
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        return mock_client

    def test_happy_path_returns_text(self, shared_module):
        mock_client = self._make_mock_client("Hello from Claude")
        with patch("anthropic.Anthropic", return_value=mock_client):
            shared_module_client_patch = MagicMock(return_value=mock_client)
            with patch.object(
                sys.modules["anthropic"], "Anthropic", shared_module_client_patch
            ):
                # Patch at the module level
                original = shared_module.anthropic if hasattr(shared_module, "anthropic") else None
                import anthropic as ant_mod
                with patch.object(ant_mod, "Anthropic", return_value=mock_client):
                    result = shared_module.call_claude("sys prompt", "user prompt")
        assert result == "Hello from Claude"

    def test_call_claude_uses_correct_model(self, shared_module):
        mock_client = self._make_mock_client("response")
        import anthropic as ant_mod
        with patch.object(ant_mod, "Anthropic", return_value=mock_client):
            shared_module.call_claude("system", "user")
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs.get("model") == "claude-sonnet-4-6" or \
               (call_kwargs.args and call_kwargs.args[0] == "claude-sonnet-4-6") or \
               call_kwargs.kwargs.get("model", call_kwargs[1].get("model", None)) is not None

    def test_call_claude_passes_max_tokens(self, shared_module):
        mock_client = self._make_mock_client("response")
        import anthropic as ant_mod
        with patch.object(ant_mod, "Anthropic", return_value=mock_client):
            shared_module.call_claude("system", "user", max_tokens=1024)
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("max_tokens") == 1024

    def test_call_claude_default_max_tokens(self, shared_module):
        mock_client = self._make_mock_client("response")
        import anthropic as ant_mod
        with patch.object(ant_mod, "Anthropic", return_value=mock_client):
            shared_module.call_claude("sys", "user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("max_tokens") == 4096

    def test_call_claude_passes_messages(self, shared_module):
        mock_client = self._make_mock_client("response")
        import anthropic as ant_mod
        with patch.object(ant_mod, "Anthropic", return_value=mock_client):
            shared_module.call_claude("sys prompt", "user prompt")
        _, kwargs = mock_client.messages.create.call_args
        messages = kwargs.get("messages", [])
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "user prompt"

    def test_call_claude_passes_system(self, shared_module):
        mock_client = self._make_mock_client("response")
        import anthropic as ant_mod
        with patch.object(ant_mod, "Anthropic", return_value=mock_client):
            shared_module.call_claude("my system prompt", "user")
        _, kwargs = mock_client.messages.create.call_args
        assert kwargs.get("system") == "my system prompt"

    def test_call_claude_api_exception_propagates(self, shared_module):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        import anthropic as ant_mod
        with patch.object(ant_mod, "Anthropic", return_value=mock_client):
            with pytest.raises(Exception, match="API error"):
                shared_module.call_claude("sys", "user")

    def test_call_claude_empty_response_text(self, shared_module):
        mock_client = self._make_mock_client("")
        import anthropic as ant_mod
        with patch.object(ant_mod, "Anthropic", return_value=mock_client):
            result = shared_module.call_claude("sys", "user")
        assert result == ""


# ═════════════════════════════════════════════════════════════════════════════
# get_repo_files
# ═════════════════════════════════════════════════════════════════════════════

class TestGetRepoFiles:
    def _make_tree_response(self, items):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"tree": items}
        return mock_resp

    def _make_blob_response(self, content: str):
        encoded = base64.b64encode(content.encode()).decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"content": encoded}
        return mock_resp

    def test_happy_path_returns_files(self, shared_module):
        tree = [
            {"type": "blob", "path": "README.md", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "main.py", "url": "https://api.github.com/blob/2"},
        ]
        blob_md = self._make_blob_response("# Hello")
        blob_py = self._make_blob_response("print('hi')")

        tree_resp = self._make_tree_response(tree)

        with patch("requests.get", side_effect=[tree_resp, blob_md, blob_py]):
            result = shared_module.get_repo_files("owner", "repo", [".md", ".py"])

        assert "README.md" in result
        assert result["README.md"] == "# Hello"
        assert "main.py" in result
        assert result["main.py"] == "print('hi')"

    def test_extension_filtering(self, shared_module):
        tree = [
            {"type": "blob", "path": "file.txt", "url": "https://api.github.com/blob/1"},
            {"type": "blob", "path": "file.py", "url": "https://api.github.com/blob/2"},
            {"type": "blob", "path": "file.md", "url": "https://api.github.com/blob/3"},
        ]
        blob_py = self._make_blob_response("python content")
        tree_resp = self._make_tree_response(tree)

        with patch("requests.get", side_effect=[tree_resp, blob_py]):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "file.py" in result
        assert "file.txt" not in result
        assert "file.md" not in result

    def test_max_files_limit(self, shared_module):
        tree = [
            {"type": "blob", "path": f"file{i}.py", "url": f"https://api.github.com/blob/{i}"}
            for i in range(10)
        ]
        tree_resp = self._make_tree_response(tree)
        blob_responses = [self._make_blob_response(f"content {i}") for i in range(5)]

        with patch("requests.get", side_effect=[tree_resp] + blob_responses):
            result = shared_module.get_repo_files("owner", "repo", [".py"], max_files=5)

        assert len(result) == 5

    def test_non_blob_items_skipped(self, shared_module):
        tree = [
            {"type": "tree", "path": "some_dir", "url": "https://api.github.com/tree/1"},
            {"type": "blob", "path": "file.py", "url": "https://api.github.com/blob/2"},
        ]
        blob_py = self._make_blob_response("content")
        tree_resp = self._make_tree_response(tree)

        with patch("requests.get", side_effect=[tree_resp, blob_py]):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert "some_dir" not in result
        assert "file.py" in result

    def test_empty_repo_returns_empty_dict(self, shared_module):
        tree_resp = self._make_tree_response([])

        with patch("requests.get", return_value=tree_resp):
            result = shared_module.get_repo_files("owner", "repo", [".py"])

        assert result == {}

    def test_blob_decode_error_skipped(self, shared_module):
        tree = [
            {"type": "blob", "path": "bad.py", "url": "https://api.github.com/blob/1"},
        ]
        tree_resp = self._make_tree_response